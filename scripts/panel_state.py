#!/usr/bin/env python3
"""The panel's fact source: which effort is live, and the rows a host draws for it."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time

SCRATCH = '.scratch'
CARD = 'autopilot.md'
RUN_CAP = 3
# A run that stopped on one of these still needs the person, so the panel stays up.
PAUSED = ('waiting-on-user', 'paused-allowance', 'paused-by-user', 'blocked')
BLOCKERS = {
    'paused-by-user': 'paused-by-user: you took control',
    'paused-allowance': 'paused-allowance: waiting for the window to reset',
    'waiting-on-user': 'waiting-on-user: one action on the pause list is needed',
    'blocked': 'blocked: no option fits the principles',
}


def card_state(path):
    """The field lines of a run card's `## State` section, as `key: value`."""
    sections = path.read_text(errors='replace').split('\n## State', 1)
    if len(sections) < 2:
        return {}
    fields = {}
    for line in sections[1].split('\n## ', 1)[0].splitlines():
        key, _, value = line.partition(':')
        if key.strip() and ' ' not in key.strip():
            fields[key.strip()] = value.strip()
    return fields


def claims(effort):
    """The session id a run card records, or None when it records none."""
    recorded = card_state(effort / CARD).get('session', '')
    return recorded if recorded and recorded != 'unknown' else None


def efforts(repo):
    """Every effort directory of the checkout that holds a run card."""
    return sorted(path for path in (repo / SCRATCH).glob('*') if (path / CARD).is_file())


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


def tickets(effort):
    """The effort's local tickets, in name order, with their front matter."""
    return [(path, front_matter(path)) for path in sorted((effort / 'issues').glob('*.md'))]


def blocker_row(effort, state, found_tickets):
    """The one blocker worth showing, by precedence: the run's own stop first, then its tickets."""
    terminal = state.get('terminal', '')
    if terminal in BLOCKERS:
        return {'kind': 'blocker', 'text': BLOCKERS[terminal]}
    for path, fields in found_tickets:
        if fields.get('status') == 'blocked':
            reason = fields.get('blocker') or 'blocked'
            return {'kind': 'blocker', 'text': f"blocked: {fields.get('ticket', path.stem)} · {reason}"}
    return None


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
    """The effort's ticket tally, or None when it records no tickets."""
    if not found_tickets:
        return None
    tally = {'done': 0, 'blocked': 0, 'ready': 0, 'implementing': 0}
    for _, fields in found_tickets:
        if fields.get('status') in tally:
            tally[fields['status']] += 1
    return {'kind': 'counts', 'text': f"{tally['done']} done · {tally['blocked']} blocked · "
                                     f"{tally['ready']} ready · {tally['implementing']} live"}


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


def live_attempts(effort, now):
    """The effort's registered attempts that have neither a result nor a closure, most recently active first."""
    state = effort / 'reports' / 'workers.sqlite'
    if not state.is_file():
        return []
    try:
        db = sqlite3.connect(f'file:{state}?mode=ro', uri=True)
        try:
            rows = [json.loads(row[0]) for row in db.execute('SELECT data FROM attempts')]
            seen = {}
            for attempt, payload in db.execute('SELECT attempt, payload FROM events ORDER BY id'):
                seen[attempt] = json.loads(payload).get('recorded_at')
        finally:
            db.close()
    except (sqlite3.DatabaseError, OSError, ValueError) as error:
        print(f'panel_state: workers.sqlite unreadable ({error}): {state}', file=sys.stderr)
        return []
    live = [(seen.get(data['attempt']) or now, data) for data in rows
            if not data.get('result') and not data.get('closed')]
    live.sort(reverse=True)
    return [(now - when, data) for when, data in live]


def model_of(data):
    """The model the attempt was observed on, else the one requested, else the destination's own."""
    observed = data.get('observed') or {}
    requested = data.get('requested') or {}
    return observed.get('model') or requested.get('model') or 'native'


def chosen(repo, session_id):
    """The effort this session runs: the one whose card names it."""
    return next((effort for effort in efforts(repo) if claims(effort) == session_id), None)


def snapshot(repo, session_id, now):
    """What this session's effort is doing, as the rows a host draws."""
    effort = chosen(Path(repo), session_id)
    if effort is None:
        return {'active': False, 'effort': None, 'phase': None, 'draft': False, 'rows': [], 'strip': None}
    state = card_state(effort / CARD)
    phase = state.get('phase') or None
    found_tickets = tickets(effort)
    draft = not state.get('confirmed', '').startswith('yes')
    blocker = blocker_row(effort, state, found_tickets)
    if phase == 'ended' or (state.get('terminal') and not blocker):
        return {'active': False, 'effort': effort.name, 'phase': phase, 'draft': False, 'rows': [], 'strip': None}
    headline = f'{effort.name} · {phase}' if phase else effort.name
    rows = [{'kind': 'headline', 'text': headline + (' · draft' if draft else '')}]
    running, waiting = [], []
    for age, data in live_attempts(effort, now):
        if (data.get('monitor') or {}).get('state') == 'running':
            waiting.append({'kind': 'wait', 'text': f"waiting · {data.get('assignment', '')} · quiet {fmt_age(age)}"})
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
    counts = counts_row(found_tickets)
    if counts:
        rows.append(counts)
    closer = (blocker or step or {}).get('text', '')
    strip = ' · '.join(part for part in (f'{effort.name} · {phase}' if phase else effort.name,
                                         f'{len(running)} running' if running else '', closer) if part)
    return {'active': True, 'effort': effort.name, 'phase': phase, 'draft': draft, 'rows': rows,
            'strip': strip or None}


def render_text(found):
    """The snapshot as the lines a host prints when it asks for text."""
    return '\n'.join(row['text'] for row in found['rows'])


def main():
    parser = argparse.ArgumentParser(description='Report the panel rows for a session\'s effort.')
    parser.add_argument('--repo', required=True)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--now', type=float)
    args = parser.parse_args()
    found = snapshot(Path(args.repo), args.session_id, args.now if args.now is not None else time.time())
    sys.stdout.write(json.dumps(found) + '\n' if args.json else (render_text(found) + '\n' if found['rows'] else ''))


if __name__ == '__main__':
    main()
