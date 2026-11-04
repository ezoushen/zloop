#!/usr/bin/env python3
"""The panel's fact source: which workstream is live, and the rows a host draws for it."""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import front_matter  # noqa: E402

SCRATCH = '.scratch'
CARD = 'autopilot.md'
ORCHESTRATOR = 'orchestrator.json'
RUN_CAP = 3
# Terminal states that leave a resume step in the card's `resume:` line.
PAUSED = ('waiting-on-user', 'paused-allowance', 'paused-by-user', 'blocked')
BLOCKERS = {
    'paused-by-user': 'paused-by-user: you took control',
    'paused-allowance': 'paused-allowance: waiting for the window to reset',
    'waiting-on-user': 'waiting-on-user: one action on the pause list is needed',
    'blocked': 'blocked: no option fits the principles',
}


def card_state(path):
    """The field lines of a run card's `## State` section, as `key: value`; an absent card has none."""
    if not path.is_file():
        return {}
    sections = path.read_text(errors='replace').split('\n## State', 1)
    if len(sections) < 2:
        return {}
    fields = {}
    for line in sections[1].split('\n## ', 1)[0].splitlines():
        key, _, value = line.partition(':')
        if key.strip() and ' ' not in key.strip():
            fields[key.strip()] = value.strip()
    return fields


def claims(workstream):
    """The session id a run card records, or None when it records none."""
    recorded = card_state(workstream / CARD).get('session', '')
    return recorded if recorded and recorded != 'unknown' else None


def workstreams(repo):
    """Every workstream directory of the checkout: those with a run card, tickets, workers or a decision journal."""
    found = []
    for path in sorted((repo / SCRATCH).glob('*')):
        if path.is_dir() and ((path / CARD).is_file() or (path / 'issues').is_dir()
                              or (path / 'reports' / 'workers.sqlite').is_file()
                              or any(path.glob('decisions-*.jsonl'))
                              or any((path / 'reports').glob('decisions-*.jsonl'))):
            found.append(path)
    return found


def activity(workstream):
    """When the workstream last changed: the newest thing it holds that this panel reads."""
    newest = 0.0
    for path in [workstream / CARD, workstream / 'reports' / 'workers.sqlite', *workstream.glob('decisions-*.jsonl'),
                 *(workstream / 'reports').glob('decisions-*.jsonl'), *workstream.glob('issues/*.md')]:
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def tickets(workstream):
    """The workstream's local tickets, in name order, with their front matter."""
    return [(path, front_matter.parse(path.read_text(errors='replace')) or {})
            for path in sorted((workstream / 'issues').glob('*.md'))]


def blocker_row(workstream, state, found_tickets):
    """The one blocker worth showing, by precedence: the run's own stop first, then its tickets."""
    terminal = state.get('terminal', '')
    if terminal in BLOCKERS:
        text = BLOCKERS[terminal]
        timer = state.get('timer', '')
        if terminal == 'paused-allowance' and timer:
            text = f'{text} (resumes {timer.split()[0]})'
        return {'kind': 'blocker', 'text': text}
    for path, fields in found_tickets:
        if fields.get('status') == 'blocked':
            reason = fields.get('blocker') or 'blocked'
            return {'kind': 'blocker', 'text': f"blocked: {fields.get('ticket', path.stem)} · {reason}"}
    return None


def is_draft(state):
    """Whether the run card is unconfirmed: `run-card.md` writes `confirmed: no` or a date with the user's words."""
    confirmed = state.get('confirmed', '')
    return not confirmed or confirmed.lower().startswith('no')


def live_workstream(state, attempts, blocker, draft, found_tickets):
    """Whether the workstream is worth showing: work in flight, a blocker, a draft card, or work its board holds."""
    if draft or blocker or attempts:
        return True
    if not state:
        return any(fields.get('status') in ('ready', 'implementing', 'blocked') for _, fields in found_tickets)
    if state.get('phase') == 'ended':
        return False
    return not state.get('terminal')


def next_row(state, found_tickets, draft):
    """The one step the person or the run takes next, or None when nothing follows."""
    if draft:
        return {'kind': 'next', 'text': 'awaiting your confirmation'}
    if state.get('terminal') in PAUSED:
        resume = state.get('resume', '')
        return {'kind': 'next', 'text': f'resume: {resume}' if resume else 'resume the run'}
    ready = [fields.get('ticket') for _, fields in found_tickets if fields.get('status') == 'ready']
    if ready:
        return {'kind': 'next', 'text': f'next: {ready[0]}'}
    return None


def counts_row(found_tickets):
    """The workstream's ticket tally, or None when it records no tickets."""
    if not found_tickets:
        return None
    tally = {'done': 0, 'blocked': 0, 'ready': 0, 'implementing': 0}
    for _, fields in found_tickets:
        if fields.get('status') in tally:
            tally[fields['status']] += 1
    return {'kind': 'counts', 'text': f"{tally['done']} done · {tally['blocked']} blocked · "
                                     f"{tally['ready']} ready · {tally['implementing']} live"}


def attempt_rows(workstream):
    """The attempt records the workstream's worker state holds; none when it has no state or the state is unreadable."""
    state = workstream / 'reports' / 'workers.sqlite'
    if not state.is_file():
        return []
    try:
        db = sqlite3.connect(f'file:{state}?mode=ro', uri=True)
        try:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM attempts')]
        finally:
            db.close()
    except (sqlite3.DatabaseError, OSError, ValueError):
        return []


def plans(workstream):
    """The assignments each planned job of the workstream's worker state holds (`worker_lifecycle.py plan`); none when
    it has no state, no plan or the state is unreadable."""
    state = workstream / 'reports' / 'workers.sqlite'
    if not state.is_file():
        return {}
    try:
        db = sqlite3.connect(f'file:{state}?mode=ro', uri=True)
        try:
            return {job: set(json.loads(assignments))
                    for job, assignments in db.execute('SELECT job, assignments FROM plans')}
        finally:
            db.close()
    except (sqlite3.DatabaseError, OSError, ValueError, TypeError):
        return {}


def done_row(workstream):
    """The assignments each job of the workstream's workers completed, jobs in name order; a planned job counts only
    its planned assignments, over its plan's size. None without workers or plans."""
    done = {}
    for data in attempt_rows(workstream):
        if not (isinstance(data, dict) and isinstance(data.get('job'), str)
                and isinstance(data.get('assignment'), str)):
            continue
        finished = done.setdefault(data['job'], set())
        if isinstance(data.get('result'), dict) and data['result'].get('status') == 'completed':
            finished.add(data['assignment'])
    planned = plans(workstream)
    if not done and not planned:
        return None
    return {'kind': 'counts', 'text': 'done: ' + ' · '.join(
        f'{job} {len(done.get(job, set()) & planned[job])}/{len(planned[job])}' if job in planned
        else f'{job} {len(done[job])}' for job in sorted(set(done) | set(planned)))}


def fmt_age(seconds):
    """A quiet span as `45s`, `12m`, `3h` or `2d`."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f'{seconds}s'
    if seconds < 3600:
        return f'{seconds // 60}m'
    if seconds < 86400:
        return f'{seconds // 3600}h'
    return f'{seconds // 86400}d'


def live_attempts(workstream, now, sessions):
    """The workstream's attempts, registered by one of these host sessions, that have no closure, most recently active
    first, each with what it waits on: `result ready` for an unacknowledged result, `wait ended` for an unconsumed
    wait receipt, `retained` for a collected result kept open for its next use, no result and no wait running (nothing
    watches it), None while it runs."""
    state = workstream / 'reports' / 'workers.sqlite'
    if not state.is_file():
        return []
    try:
        db = sqlite3.connect(f'file:{state}?mode=ro', uri=True)
        try:
            rows = [json.loads(row[0]) for row in db.execute('SELECT data FROM attempts')]
            seen = {}
            for attempt, payload in db.execute('SELECT attempt, payload FROM events ORDER BY id'):
                seen[attempt] = json.loads(payload).get('recorded_at')
            unconsumed = db.execute("SELECT attempt, kind FROM events WHERE acknowledged = 0 "
                                    "AND kind IN ('result', 'wait-finished')").fetchall()
        finally:
            db.close()
    except (sqlite3.DatabaseError, OSError, ValueError) as error:
        print(f'panel_state: workers.sqlite unreadable ({error}): {state}', file=sys.stderr)
        return []
    pending = {(attempt, kind) for attempt, kind in unconsumed}
    # A closure of the conversation, through whichever of its attempts recorded it, closes them all.
    closed = {(data.get('runtime'), data.get('native_session')) for data in rows
              if isinstance(data, dict) and data.get('closed')}
    shown = []
    for data in rows:
        if (not isinstance(data, dict) or (data.get('runtime'), data.get('native_session')) in closed
                or not sessions.intersection(recorded_sessions(data))):
            continue
        if data.get('result'):
            if (data['attempt'], 'result') in pending:
                label = 'result ready'
            elif data.get('retained'):
                label = 'retained'
            else:
                continue
        else:
            ended = ((data['attempt'], 'wait-finished') in pending
                     and (data.get('monitor') or {}).get('state') != 'running')
            label = 'wait ended' if ended else None
        shown.append((seen.get(data['attempt']) or now, data, label))
    shown.sort(key=lambda row: row[0], reverse=True)
    return [(now - when, data, label) for when, data, label in shown]


def model_of(data):
    """The model the attempt was observed on, else the one requested, else the destination's own."""
    observed = data.get('observed') or {}
    requested = data.get('requested') or {}
    return observed.get('model') or requested.get('model') or 'native'


def bound_sessions(workstream):
    """The host sessions the workstream's orchestrator binding names, or None when it keeps no binding."""
    try:
        saved = json.loads((workstream / 'reports' / ORCHESTRATOR).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict):
        return None
    sessions = saved.get('sessions')
    return [str(session) for session in sessions] if isinstance(sessions, list) else []


def registered_by(workstream, session_id):
    """Whether a worker attempt in the workstream's state was registered by this host session."""
    return any(session_id in recorded_sessions(data) for data in attempt_rows(workstream))


def recorded_sessions(data):
    """The host sessions an attempt's registrar exported: its session list, else its one session, as the yield gate
    reads them; none for a row that is not such a record."""
    if not isinstance(data, dict):
        return []
    listed = data.get('sessions') if isinstance(data.get('sessions'), list) else []
    return [value for value in listed if isinstance(value, str)] or (
        [data['session']] if isinstance(data.get('session'), str) else [])


def tied(repo, session_id):
    """The workstreams a record ties to this session, each once, in binding order.

    A binding comes first: `next_tickets.py --orchestrate` records the sessions running a workstream, so two sessions in
    one checkout each read their own. Then the card naming this session, its journal, and last the workstreams whose
    workers it registered, in name order, since every worker event rewrites a state and two live runs would otherwise
    swap; their states are read only when the caller asks past the others. A workstream no record ties to this session
    is another session's, however recent, so a session just opened shows none.
    """
    found = workstreams(repo)
    recorded = dict.fromkeys([
        *sorted((workstream for workstream in found if session_id in (bound_sessions(workstream) or [])), key=activity,
                reverse=True),
        *(workstream for workstream in found if claims(workstream) == session_id),
        *(workstream for workstream in found if any(workstream.glob(f'decisions-{session_id}*.jsonl'))
          or any((workstream / 'reports').glob(f'decisions-{session_id}*.jsonl')))])
    yield from recorded
    yield from (workstream for workstream in found
                if workstream not in recorded and registered_by(workstream, session_id))


def main_checkout(repo):
    """The checkout holding the workstream state: a session working in a worktree still reads its main checkout."""
    repo = Path(repo)
    if (repo / SCRATCH).is_dir():
        return repo
    try:
        common = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                                capture_output=True, text=True, timeout=5)
        candidate = Path(common.stdout.strip()).parent if common.returncode == 0 else None
        if candidate and (candidate / SCRATCH).is_dir():
            return candidate
    except (OSError, subprocess.SubprocessError):
        pass
    return repo


def snapshot(repo, session_id, now):
    """What this session's workstream is doing, as the rows a host draws: the first live workstream tied to it, else
    the first tied one, quiet."""
    chosen = None
    for workstream in tied(main_checkout(repo), session_id):
        state = card_state(workstream / CARD)
        found_tickets = tickets(workstream)
        draft = is_draft(state) if state else False
        blocker = blocker_row(workstream, state, found_tickets)
        bound = bound_sessions(workstream)
        attempts = live_attempts(workstream, now, set(bound) if bound is not None else {session_id})
        live = live_workstream(state, attempts, blocker, draft, found_tickets)
        if chosen is None or live:
            chosen = workstream, state, found_tickets, draft, blocker, attempts, live
        if live:
            break
    if chosen is None:
        return {'active': False, 'workstream': None, 'phase': None, 'draft': False, 'rows': [], 'more': [],
                'strip': None}
    workstream, state, found_tickets, draft, blocker, attempts, live = chosen
    phase = state.get('phase') or None
    if not live:
        return {'active': False, 'workstream': workstream.name, 'phase': phase, 'draft': draft, 'rows': [], 'more': [],
                'strip': None}
    headline = f'{workstream.name} · {phase}' if phase else workstream.name
    rows = [{'kind': 'headline', 'text': headline + (' · draft' if draft else '')}]
    running, waiting = [], []
    for age, data, label in attempts:
        if label:
            waiting.append({'kind': 'wait', 'text': f"{label} · {data.get('assignment', '')} · quiet {fmt_age(age)}"})
        else:
            running.append({'kind': 'run', 'text': f"▶ {data.get('agent', 'worker')} · {data.get('assignment', '')} · "
                                                   f'{model_of(data)} · quiet {fmt_age(age)}'})
    rows += running[:RUN_CAP] + waiting[:RUN_CAP]
    hidden = running[RUN_CAP:] + waiting[RUN_CAP:]
    if hidden:
        rows.append({'kind': 'more', 'text': f'+{len(hidden)} more'})
    if blocker:
        rows.append(blocker)
    step = next_row(state, found_tickets, draft)
    if step:
        rows.append(step)
    counts = counts_row(found_tickets) if found_tickets else done_row(workstream)
    if counts:
        rows.append(counts)
    closer = (blocker or step or {}).get('text', '')
    strip = ' · '.join(part for part in (f'{workstream.name} · {phase}' if phase else workstream.name,
                                         f'{len(running)} running' if running else '', closer) if part)
    return {'active': True, 'workstream': workstream.name, 'phase': phase, 'draft': draft, 'rows': rows,
            'more': hidden, 'strip': strip or None}


def render_text(found):
    """The snapshot as the lines a host prints when it asks for text."""
    return '\n'.join(row['text'] for row in found['rows'])


def main():
    parser = argparse.ArgumentParser(description='Report the panel rows for a session\'s workstream.')
    parser.add_argument('--repo', required=True)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--now', type=float)
    args = parser.parse_args()
    found = snapshot(Path(args.repo), args.session_id, args.now if args.now is not None else time.time())
    sys.stdout.write(json.dumps(found) + '\n' if args.json else (render_text(found) + '\n' if found['rows'] else ''))


if __name__ == '__main__':
    main()
