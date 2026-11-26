#!/usr/bin/env python3
"""Persist one due continuation and verify notification through target readback.

Run outside the agent host. No system service is installed and uncertain sends
are never repeated automatically.
"""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_control  # noqa: E402
import state_files  # noqa: E402
import worker_lifecycle  # noqa: E402


def read(queue):
    data = json.loads(Path(queue).read_text())
    if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('wakes'), dict):
        raise ValueError('Unreadable continuation queue')
    return data


def save(queue, data):
    state_files.write_atomic(Path(queue), json.dumps(data, indent=2) + '\n', mode=0o600)


def branch_head(card, branch):
    """Return the full SHA the local branch points at, or '' when it does not exist."""
    result = subprocess.run(['git', '-C', str(Path(card).parent), 'rev-parse', '--verify', '--quiet',
                             f'refs/heads/{branch}^{{commit}}'], capture_output=True, text=True)
    return result.stdout.strip()


def moved_branch(card, branch, expected):
    """Name the branch and both SHAs when it no longer points at the expected head."""
    actual = branch_head(card, branch)
    if actual != expected:
        return f'Branch {branch} points at {actual or "nothing"}, not the expected {expected}'


def schedule(queue, card, due, config, event_id, expect_head=None):
    """Arm an explicit existing resume grant, bound to the exact managed revision.

    With `expect_head` (a full SHA), the wake also lapses once the workstream branch no
    longer points at it, and scheduling is refused when the branch does not now."""
    queue, card = Path(queue).resolve(), Path(card).resolve()
    control = run_control.read(card)
    if not control or not control['allow_resume']:
        raise ValueError('A managed run with an explicit existing resume grant is required')
    if state_files.state_fields(control['card_text']).get('terminal') != 'paused-allowance':
        raise ValueError('Only an allowance pause can arm an unattended continuation')
    for name in ('preflight', 'notify', 'progress'):
        argv = config.get(name)
        if not isinstance(argv, list) or not all(isinstance(x, str) and x for x in argv) or argv.count('{receipt}') != 1:
            raise ValueError(f'{name} requires literal argv with exactly one {{receipt}} argument')
    timeout = config.get('timeout', 15)
    if (type(timeout) not in (int, float) or not 0 < timeout <= 45
            or not isinstance(event_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', event_id)
            or type(due) not in (int, float) or not math.isfinite(due)):
        raise ValueError('Require an event ID, due epoch and a timeout in (0, 45] seconds')
    request = dict(card=str(card), run_id=control['run_id'], control_revision=control['revision'],
                   due=due, config=config)
    if expect_head is not None:
        request.update(branch=run_control.workstream_branch(card.parent), expect_head=expect_head)
        if problem := moved_branch(card, request['branch'], expect_head):
            raise ValueError(problem)
    queue.parent.mkdir(parents=True, exist_ok=True)
    with state_files.locked(queue):
        data = read(queue) if queue.exists() else {'schema': 1, 'wakes': {}}
        if event_id in data['wakes']:
            entry = data['wakes'][event_id]
            if entry['request'] != request:
                raise ValueError('Wake request ID already names different input')
            return entry
        if any(e['state'] not in ('progressed', 'cancelled') for e in data['wakes'].values()):
            raise ValueError('An unfinished wake already owns this queue; reconcile it first')
        entry = dict(event_id=event_id, request=request, state='pending', created_at=time.time(), attempts=0)
        data['wakes'][event_id] = entry
        save(queue, data)
        return entry


def invoke(argv, receipt, timeout, log):
    """Run a bounded target driver, retaining stderr and its JSON observation."""
    args = [json.dumps(receipt) if x == '{receipt}' else x for x in argv]
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('w') as errors:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=errors, text=True, start_new_session=True)
        try:
            output, _ = process.communicate(timeout=timeout)
        finally:
            worker_lifecycle.stop(process)
    log.with_suffix('.json').write_text(output)
    if process.returncode:
        raise ValueError(f'Target driver exited {process.returncode}; inspect {log}')
    if not output.strip():
        return {}
    result = json.loads(output)
    if not isinstance(result, dict):
        raise ValueError(f'Target driver must return a JSON object: {log}')
    return result


def receipt(queue, entry):
    return dict(event_id=entry['event_id'], queue=str(Path(queue).resolve()),
                **{k: v for k, v in entry['request'].items() if k != 'config'})


def current(entry):
    control = run_control.read(entry['request']['card'])
    if not control or control['run_id'] != entry['request']['run_id'] or not control['allow_resume']:
        raise ValueError('Continuation grant or run identity changed')
    values = state_files.state_fields(control['card_text'])
    resumed = any(e.get('source', {}).get('key') == entry['event_id'] and e.get('terminal') == ''
                  for e in control['events'])
    if values.get('terminal') != 'paused-allowance' and not (resumed and not values.get('terminal')):
        raise ValueError('Run no longer waits on this allowance pause')
    if not resumed and control['revision'] != entry['request']['control_revision']:
        raise ValueError('Control revision changed')
    if not resumed and (head := entry['request'].get('expect_head')) is not None:
        if problem := moved_branch(entry['request']['card'], entry['request']['branch'], head):
            raise ValueError(problem)
    return control


def progressed(entry, observed):
    control = current(entry)
    return (not state_files.state_fields(control['card_text']).get('terminal')
            and any(e.get('source', {}).get('key') == entry['event_id'] and e.get('terminal') == ''
                    for e in control['events'])
            and observed.get('event_id') == entry['event_id'] and observed.get('progressed') is True)


def tick(queue, event_id=None):
    """Inspect one due wake; a sent wake only checks progress and is not resent."""
    queue = Path(queue).resolve()
    with state_files.locked(queue):
        data = read(queue)
        if not data['wakes']:
            return {'state': 'idle'}
        entry = data['wakes'][event_id] if event_id else next(
            (e for e in data['wakes'].values() if e['state'] not in ('progressed', 'cancelled')),
            list(data['wakes'].values())[-1])
        if entry['state'] in ('progressed', 'cancelled'):
            return entry
        try:
            current(entry)
        except (OSError, ValueError) as error:
            entry.update(state='cancelled', reason=str(error))
            save(queue, data)
            return entry
        if entry['state'] in ('sending', 'uncertain'):
            entry.update(state='uncertain', reason='Inspect the same target receipt before reconciliation; do not resend')
            save(queue, data)
            return entry
        if entry['request']['due'] > time.time():
            return entry
        config, payload = entry['request']['config'], receipt(queue, entry)
        logs = queue.with_name(queue.name + '.logs')
        entry['attempts'] += 1
        prefix = logs / f'{entry["event_id"]}-{entry["attempts"]}'
        try:
            if entry['state'] == 'pending':
                checked = invoke(config['preflight'], payload, config.get('timeout', 15), str(prefix) + '.preflight.log')
                entry['preflight'] = checked
                if checked.get('ready') is not True or checked.get('allowance') != 'eligible' or checked.get('approval') != 'clear':
                    entry['reason'] = 'Target preflight is not ready, allowance-eligible and approval-clear'
                    save(queue, data)
                    return entry
                current(entry)
                entry.update(state='sending', claim={'pid': os.getpid(), 'at': time.time()})
                save(queue, data)
                invoke(config['notify'], payload, config.get('timeout', 15), str(prefix) + '.notify.log')
                entry.update(state='sent', sent_at=time.time())
                save(queue, data)
            observed = invoke(config['progress'], payload, config.get('timeout', 15), str(prefix) + '.progress.log')
            entry['progress'] = observed
            if progressed(entry, observed):
                entry.update(state='progressed', progressed_at=time.time())
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            entry.update(state='uncertain' if entry['state'] == 'sending' else entry['state'], reason=str(error))
        save(queue, data)
        return entry


def reconcile(queue, event_id):
    """Resolve an uncertain send only by readback of the same target event; never resend it."""
    queue = Path(queue).resolve()
    with state_files.locked(queue):
        data = read(queue)
        entry = data['wakes'][event_id]
        if entry['state'] != 'uncertain':
            raise ValueError('Only an uncertain send needs explicit reconciliation')
        current(entry)
        config = entry['request']['config']
        observed = invoke(config['progress'], receipt(queue, entry), config.get('timeout', 15),
                          queue.with_name(queue.name + '.logs') / f'{event_id}.reconcile.log')
        if not progressed(entry, observed):
            raise ValueError('Target does not prove this event progressed; keep the uncertain state')
        entry.update(state='progressed', progress=observed, progressed_at=time.time())
        save(queue, data)
        return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['schedule', 'tick', 'run', 'reconcile'])
    parser.add_argument('queue', type=Path)
    parser.add_argument('--card', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--due', type=float)
    parser.add_argument('--event-id')
    parser.add_argument('--expect-head', help='full SHA the workstream branch must keep pointing at')
    parser.add_argument('--interval', type=float, default=5)
    args = parser.parse_args()
    try:
        if args.command == 'schedule':
            if not args.card or not args.config or args.due is None:
                raise ValueError('Schedule needs --card, --config and --due')
            result = schedule(args.queue, args.card, args.due, json.loads(args.config.read_text()),
                              args.event_id, args.expect_head)
        elif args.command == 'reconcile':
            result = reconcile(args.queue, args.event_id)
        else:
            if args.interval < 1:
                raise ValueError('Supervisor interval must be at least one second')
            while True:
                result = tick(args.queue, args.event_id)
                if args.command != 'run' or result['state'] in ('progressed', 'cancelled', 'uncertain', 'idle'):
                    break
                time.sleep(args.interval)
        print(json.dumps({key: result[key] for key in ('event_id', 'state', 'reason', 'attempts',
                          'created_at', 'sent_at', 'progressed_at') if key in result}, indent=2))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    main()
