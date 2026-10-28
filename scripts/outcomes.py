#!/usr/bin/env python3
"""Keep each worker attempt's outcome across workstreams and summarise it per agent, job and candidate.

The worker lifecycle writes an attempt's result, acceptance or decline, and later reverts.
`import` adds past decision journals and lifecycle state files once. A job that requires acceptance
succeeds when accepted; other jobs when their work completed. A revert, a decline or a failed result is a failure; a blocked
result waits on something outside the worker and counts neither way.
"""

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_defs  # noqa: E402
import journal_store  # noqa: E402

JOBS = agent_defs.load()['jobs']


# A collected lifecycle status as an outcome, and later quality signals as metric names.
STATUSES = {'completed': 'succeeded', 'failed': 'failed', 'blocked': 'failed', 'cancelled': 'unknown'}
METRICS = {'review-defect': 'review_defects', 'repair-round': 'repair_rounds',
           'revert': 'reverts', 'owner-defect': 'owner_defects'}


def ledger_path():
    state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state').expanduser()
    if not state.is_absolute():
        raise ValueError('XDG_STATE_HOME must be an absolute directory')
    return state / 'zloop/outcomes.sqlite'


def connect():
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30, isolation_level=None)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS outcomes (key TEXT PRIMARY KEY, data TEXT NOT NULL)')
    return db


def put(key, row):
    """Write one attempt's current outcome; the latest write for a key wins. A lifecycle attempt replaces
    the copy of its routing decision imported from a journal."""
    db = connect()
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if row.get('decision'):
                db.execute('DELETE FROM outcomes WHERE key = ?', (row['decision'],))
            db.execute('INSERT OR REPLACE INTO outcomes VALUES (?, ?)',
                       (key, json.dumps(dict(row, recorded_at=time.time()))))
    finally:
        db.close()


def needs_acceptance(job):
    return bool(JOBS.get(job, {}).get('acceptance'))


def verdict(row):
    """'success', 'failure' or 'pending' for one attempt's outcome."""
    if row.get('reverts') or row.get('declined'):
        return 'failure'
    if row.get('result') == 'blocked':
        return 'pending'
    if row.get('status') == 'failed':
        return 'failure'
    if row.get('status') != 'succeeded':
        return 'pending'
    if needs_acceptance(row.get('job')) and not row.get('accepted'):
        return 'pending'
    return 'success'


def summary(agent=None, job=None):
    """Read-only: a missing ledger has no outcomes yet."""
    path = ledger_path()
    if not path.is_file():
        return {}
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=30)
    try:
        rows = [json.loads(data) for (data,) in db.execute('SELECT data FROM outcomes ORDER BY key')]
    finally:
        db.close()
    agents = {}
    for row in rows:
        # Rows recorded before agents and jobs measured different work; they are not compared.
        if 'agent' not in row or agent is not None and row['agent'] != agent or job is not None and row['job'] != job:
            continue
        counts = agents.setdefault(row['agent'], {}).setdefault(row['job'], {}).setdefault(
            row['candidate'], {'successes': 0, 'failures': 0, 'pending': 0})
        counts[{'success': 'successes', 'failure': 'failures', 'pending': 'pending'}[verdict(row)]] += 1
    return agents


def attempt_row(db, saved, source):
    """A collected lifecycle attempt's outcome, with the quality signals attributed to it."""
    counts = {}
    for (payload,) in db.execute("SELECT payload FROM events WHERE attempt = ? AND kind = 'attribution'",
                                 (saved['attempt'],)):
        signal = json.loads(payload)
        counts[METRICS[signal['kind']]] = counts.get(METRICS[signal['kind']], 0) + signal['count']
    execution = saved.get('execution_snapshot', {})
    actual = saved.get('observed') or saved['requested']
    # States from before the lifecycle kept the link still hold it in the resolved route.
    routed = execution.get('decision_model') or {}
    link = saved.get('decision') or (routed if routed.get('decision_id') and routed.get('journal') else {})
    return dict(decision=f"{Path(link['journal']).resolve()}#{link['decision_id']}" if link else None,
                source=source, attempt=saved['attempt'], assignment=saved['assignment'], repo=saved['repo'],
                agent=saved['agent'], job=saved['job'], candidate=execution.get('candidate') or actual.get('model'),
                model=actual.get('model'), effort=actual.get('effort'), runtime=saved['runtime'],
                selection_source=execution.get('selection_source'),
                shadow_choice=(execution.get('shadow_selection') or {}).get('choice'),
                result=saved['result']['status'], status=STATUSES[saved['result']['status']],
                accepted=bool(saved.get('accepted')),
                declined=bool(saved.get('declined')), **counts)


def state_rows(path):
    """Collected attempts from a lifecycle state file, keyed as the lifecycle keys them."""
    source = str(Path(path).resolve())
    db = sqlite3.connect(f'file:{source}?mode=ro', uri=True, timeout=30)
    try:
        for (data,) in db.execute('SELECT data FROM attempts ORDER BY id'):
            saved = json.loads(data)
            if saved.get('result'):
                yield f"{source}#{saved['attempt']}", attempt_row(db, saved, source)
    finally:
        db.close()


def journal_rows(path):
    """Worker-selection outcomes from a decision journal, keyed by journal and decision."""
    rows = journal_store.read(path)
    latest = {}
    for row in rows:
        if row['event'] in ('action', 'outcome'):
            latest.setdefault(row['decision_id'], {})[row['event']] = row
    for row in rows:
        request = row.get('request') if row['event'] == 'decision' else None
        if not isinstance(request, dict) or request.get('family') != 'worker-selection':
            continue
        state = request.get('state', '')
        if not state.startswith('Agent: '):
            continue
        events = latest.get(row['decision_id'], {})
        choice = events.get('action', {}).get('choice') or (row.get('result') or {}).get('choice')
        option = request.get('options', {}).get(choice)
        if not option:
            continue
        outcome = events.get('outcome', {})
        agent = state[len('Agent: '):].split('\n', 1)[0]
        job = state.split('\nJob: ', 1)[1].split('\n', 1)[0] if '\nJob: ' in state else None
        status = outcome.get('status', 'unknown')
        yield f"{Path(path).resolve()}#{row['decision_id']}", {
            'source': str(Path(path).resolve()), 'attempt': row.get('assignment'), 'agent': agent, 'job': job,
            'candidate': json.loads(option)['candidate'], 'status': status,
            # A host records success when it accepted the work; the lifecycle records it at completion.
            'accepted': (needs_acceptance(job) and status == 'succeeded'
                         and events.get('action', {}).get('actor') != 'worker-lifecycle') or None,
            'reverts': outcome.get('metrics', {}).get('reverts', 0)}


def import_sources(paths):
    db = connect()
    try:
        rows = list(db.execute('SELECT key, data FROM outcomes'))
    finally:
        db.close()
    # Journal decisions a lifecycle attempt already carries count as known.
    known = {key for key, _ in rows} | {json.loads(data).get('decision') for _, data in rows} - {None}
    imported = already = 0
    errors = {}
    for path in paths:
        state = Path(path).suffix == '.sqlite'
        try:
            rows = list(state_rows(path) if state else journal_rows(path))
        except (OSError, ValueError, KeyError, sqlite3.Error) as error:
            errors[str(path)] = str(error)
            continue
        for key, row in rows:
            seen = key in known
            already += seen
            imported += not seen
            if seen and not state:
                continue
            put(key, row)  # A state file is the attempt's record, so it refreshes an earlier row.
            known |= {key, row.get('decision')} - {None}
    return {'imported': imported, 'already': already, **({'errors': errors} if errors else {})}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    report = commands.add_parser('summary', help='successes, failures and pending attempts per agent, job and candidate')
    report.add_argument('--agent')
    report.add_argument('--job')
    load = commands.add_parser('import', help='add past decision journals and lifecycle state files once')
    load.add_argument('sources', nargs='+', type=Path, help='decisions .jsonl or lifecycle workers .sqlite files')
    args = parser.parse_args()
    try:
        if args.command == 'summary':
            result = {'ledger': str(ledger_path()), 'agents': summary(args.agent, args.job)}
        else:
            result = import_sources(args.sources)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(2, f'{error}\n')
    print(json.dumps(result, indent=2))
    sys.exit(1 if result.get('errors') else 0)


if __name__ == '__main__':
    main()
