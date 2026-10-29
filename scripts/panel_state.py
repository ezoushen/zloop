#!/usr/bin/env python3
"""The panel's fact source: which workstream is live, and the rows a host draws for it."""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

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


def front_matter(path):
    """The `key: value` lines of a ticket's front matter."""
    lines = path.read_text(errors='replace').split('---')
    fields = {}
    if len(lines) > 2:
        for line in lines[1].splitlines():
            key, _, value = line.partition(':')
            if key.strip():
                fields[key.strip()] = value.strip()
    return fields


def tickets(workstream):
    """The workstream's local tickets, in name order, with their front matter."""
    return [(path, front_matter(path)) for path in sorted((workstream / 'issues').glob('*.md'))]


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


def planned_counts(path):
    """The assignments an orchestrator plans per job, from `{"<job>": <count>}`; a missing or malformed file, and any
    entry that is not a whole count, plan nothing."""
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(saved, dict):
        return {}
    return {job: count for job, count in saved.items()
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0}


def done_row(workstream):
    """The assignments each job of the workstream's workers completed, jobs in name order; None without workers."""
    done = {}
    for data in attempt_rows(workstream):
        if not (isinstance(data, dict) and isinstance(data.get('job'), str)
                and isinstance(data.get('assignment'), str)):
            continue
        finished = done.setdefault(data['job'], set())
        if isinstance(data.get('result'), dict) and data['result'].get('status') == 'completed':
            finished.add(data['assignment'])
    if not done:
        return None
    planned = planned_counts(workstream / 'reports' / 'planned.json')
    return {'kind': 'counts', 'text': 'done: ' + ' · '.join(
        f'{job} {len(done[job])}' + (f'/{planned[job]}' if job in planned else '') for job in sorted(done))}


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


def live_attempts(workstream, now):
    """The workstream's registered attempts that have neither a result nor a closure, most recently active first."""
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
            db_pending = [(attempt, acknowledged) for attempt, acknowledged in
                          db.execute("SELECT attempt, acknowledged FROM events WHERE kind = 'result'")]
        finally:
            db.close()
    except (sqlite3.DatabaseError, OSError, ValueError) as error:
        print(f'panel_state: workers.sqlite unreadable ({error}): {state}', file=sys.stderr)
        return []
    pending = {attempt for attempt, acknowledged in db_pending if not acknowledged}
    shown = []
    for data in rows:
        if not isinstance(data, dict) or data.get('closed'):
            continue
        has_result = bool(data.get('result'))
        awaiting = has_result and data['attempt'] in pending
        if has_result and not awaiting:
            continue
        shown.append((seen.get(data['attempt']) or now, data, awaiting))
    shown.sort(key=lambda row: row[0], reverse=True)
    return [(now - when, data, awaiting) for when, data, awaiting in shown]


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
        attempts = live_attempts(workstream, now)
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
    for age, data, awaiting in attempts:
        if awaiting:
            waiting.append({'kind': 'wait', 'text': f"result ready · {data.get('assignment', '')} · quiet {fmt_age(age)}"})
        else:
            running.append({'kind': 'run', 'text': f"▶ {data.get('agent', 'worker')} · {data.get('assignment', '')} · "
                                                   f'{model_of(data)} · quiet {fmt_age(age)}'})
    rows += running[:RUN_CAP]
    if len(running) > RUN_CAP:
        rows.append({'kind': 'more', 'text': f'+{len(running) - RUN_CAP} more'})
    rows += waiting
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
            'more': running[RUN_CAP:], 'strip': strip or None}


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
