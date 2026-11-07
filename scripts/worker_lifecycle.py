#!/usr/bin/env python3
"""Retain worker attempts, wait receipts and design holds until the host collects them."""

import argparse
import errno
import hashlib
import json
import math
import os
import re
from pathlib import Path
import shlex
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

# Find the sibling decision modules even under an isolated interpreter (python -I).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_defs  # noqa: E402
import capability_probe  # noqa: E402
import capacity  # noqa: E402
import decisions  # noqa: E402
import front_matter  # noqa: E402
import journal_store  # noqa: E402
import native_settings  # noqa: E402
import outcomes  # noqa: E402

DEFINITIONS = agent_defs.load()
TAB_RECIPE = ('start the worker in a tab of its own: `herdr tab create --workspace <orchestrator workspace> '
              '--cwd <worktree> --label "<slug>/<assignment> <role>" --env ZLOOP_WORKER=<slug>/<assignment> '
              '--no-focus`, then `herdr agent start <name> --kind <kind> --pane <returned root pane> -- <launch args>`, '
              'then register it')
LOOKUP_SECONDS = 5  # Registration's wait on each Herdr call.
REAP_SECONDS = 5  # All a stop's reap may spend, inside the hosts' 20 s hook timeout, which the gate's other checks share.
# The terminal states a run card may record: ENDED after which nothing of the run is used again,
# PAUSED leaving a resume step on the card's `resume:` line.
ENDED = ('delivered', 'delivered-local', 'merged')
PAUSED = ('waiting-on-user', 'paused-allowance', 'paused-by-user', 'blocked')
TERMINAL_STATES = ENDED + PAUSED
# Native write-scope controls: a launch requests them (its `verify`) and startup observes them.
SCOPE = ('permission_mode', 'sandbox', 'approval', 'filesystem', 'tools', 'permissions')
TOP_LEVEL = ('claude', 'codex', 'opencode', 'pi')  # Runtimes whose own process the resolver gives launch controls.
# What Claude Code, Codex, Pi and OpenCode export to the orchestrator's shell as its host session ID.
SESSION_VARIABLES = ('CLAUDE_CODE_SESSION_ID', 'CODEX_SESSION_ID', 'PI_SESSION_ID', 'OPENCODE_SESSION_ID')


def host_sessions():
    """The host sessions that answer at stop for what this process does: each one exported, and for a pi-subagents
    runner child's own Pi session, which is never held, its parent session too, under which Pi's extension logs the
    child's launches. A Claude or Codex session the child started answers for itself."""
    names = [name for name in SESSION_VARIABLES if os.environ.get(name)]
    sessions = [os.environ[name] for name in names]
    parent = os.environ.get('PI_SUBAGENT_PARENT_SESSION', '').strip()
    if names[:1] == ['PI_SESSION_ID'] and os.environ.get('PI_SUBAGENT_CHILD') == '1' and parent:
        sessions.append(parent)
    return sessions


def require(value, message):
    if not value:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def read_object(path):
    require(Path(path).is_absolute(), 'Input paths must be absolute')
    data = json.loads(Path(path).read_text())
    require(isinstance(data, dict), 'Input must be a JSON object')
    return data


def connect(path):
    require(path.is_absolute(), '--state must be absolute')
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():  # An existing state is not touched: its age is a fact the stop gate reads.
        path.touch(mode=0o600)
    db = sqlite3.connect(path, timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS holds (assignment TEXT PRIMARY KEY, data TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS criteria (assignment TEXT PRIMARY KEY, data TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS plans (job TEXT PRIMARY KEY, assignments TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS findings (finding TEXT PRIMARY KEY)')  # The repair plan's, in plan order.
    db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, attempt TEXT NOT NULL, '
               'kind TEXT NOT NULL, payload TEXT NOT NULL, acknowledged INTEGER NOT NULL)')
    return db


def attempts(db):
    return [json.loads(row[0]) for row in db.execute('SELECT data FROM attempts ORDER BY id')]


def get(db, attempt):
    row = db.execute('SELECT data FROM attempts WHERE id = ?', (attempt,)).fetchone()
    require(row is not None, 'Unknown attempt')
    return json.loads(row[0])


def closed_sessions(db):
    return {(row['runtime'], row['native_session']): row for row in attempts(db) if row.get('closed')}


def report_owed(saved, declined=False):
    """Whether the attempt's report is settled: only a completed result owes an intact report, or its decline (a
    changed report can never be accepted, and its attempt still closes); an accepted attempt owes none, since
    acceptance consumed its report."""
    return (saved['result']['status'] != 'completed' or bool(saved.get('accepted')) or bool(declined)
            or report_intact(saved))


def conversation(db, saved):
    """(the attempts of the saved attempt's native conversation by id, in registration order; the id of the final
    registered one)."""
    siblings = {row['attempt']: row for row in attempts(db)
                if (row['runtime'], row['native_session']) == (saved['runtime'], saved['native_session'])}
    order = [row[0] for row in db.execute("SELECT attempt FROM events WHERE kind = 'register' ORDER BY id")
             if row[0] in siblings]
    return {name: siblings[name] for name in order}, order[-1]


def final_retirement(db, saved, declined=False):
    """Every attempt of the conversation is collected with its report settled (see report_owed), and nothing of it
    is still waiting. A completed attempt whose report path a later attempt of the conversation also recorded owes no
    intact report: the later one is the conversation's."""
    siblings, latest = conversation(db, saved)
    require(latest == saved['attempt'], 'Retire the final registered attempt of the native conversation')
    state, script = shlex.quote(state_path(db)), shlex.quote(str(Path(__file__).resolve()))
    reports = [(row.get('result') or {}).get('report') for row in siblings.values()]
    problems = []
    for number, row in enumerate(siblings.values()):
        name, lacks = row['attempt'], []
        if not row.get('result'):
            lacks.append(f'a collected result (`python3 {script} collect --state {state} --attempt {name} '
                         '--input <request>`)')
        elif not (reports[number] in reports[number + 1:]
                  or report_owed(row, row.get('declined') or declined and name == saved['attempt'])):
            lacks.append(f'an intact report (record it declined: `python3 {script} record --state {state} --input '
                         f'<JSON {{"kind": "declined", "attempt": "{name}", "reason", "evidence"}}>`)')
        lacks += [f'receipt {receipt["id"]} acknowledged (`python3 {script} ack --state {state} --event-id '
                  f'{receipt["id"]}`)' for receipt in pending(db, name)]
        if watching(row) or wait_running(row):
            lacks.append(f'its wait stopped (`python3 {script} status --state {state} --attempt {name}`, then '
                         'reconcile it)')
        if lacks:
            problems.append(f'{name} lacks ' + ', and '.join(lacks))
    require(not problems, 'Settle each attempt of the conversation before closing it: ' + '; '.join(problems))


def plan(db, job, assignments, replace=False):
    """Add assignments to a job's plan, or with replace make them the whole plan (partitions cut again)."""
    require(text(job) and assignments and all(text(x) for x in assignments),
            'Plan requires --job and at least one --assignment')
    require(job in DEFINITIONS['jobs'], f'Plan requires a known job, not {job}')
    db.execute('BEGIN IMMEDIATE')  # The plan extended is read under the lock, so concurrent additions add up.
    with db:
        planned = list(dict.fromkeys([*([] if replace else planned_of(db, job) or []), *assignments]))
        db.execute('INSERT OR REPLACE INTO plans VALUES (?, ?)', (job, json.dumps(planned)))
    return {'job': job, 'assignments': planned}


def plan_findings(db, job, findings, replace=False):
    """Add findings to the repair plan, or with replace make them its whole set: the confirmed findings to repair, each
    of which one repair assignment holds."""
    require(job == 'repair', 'Only the repair job takes --finding')
    for finding in findings:
        require(text(finding) and not re.search(r'[\s,]', finding),
                f'A finding is one ID with no comma or space, not {finding!r}')
    db.execute('BEGIN IMMEDIATE')
    with db:
        if replace:
            db.execute('DELETE FROM findings')
        db.executemany('INSERT OR IGNORE INTO findings VALUES (?)', [(finding,) for finding in findings])
    return {'job': job, 'findings': planned_findings(db)}


def planned_findings(db):
    """The repair plan's findings, in the order first planned."""
    return [row[0] for row in db.execute('SELECT finding FROM findings ORDER BY rowid')]


def assigned_findings(db, assignment, value):
    """The planned findings a repair packet's findings: line names, none of which another assignment holds."""
    named = list(dict.fromkeys(finding for finding in re.split(r'[\s,]+', value) if finding))
    require(named, "The repair packet's findings: names no finding; list the IDs of the planned findings this "
                   'assignment repairs, separated by commas or spaces')
    planned = set(planned_findings(db))
    unknown = [finding for finding in named if finding not in planned]
    command = shlex.join(['python3', str(Path(__file__).resolve()), 'plan', '--state', state_path(db), '--job',
                          'repair']) + ' --finding <ID>'
    require(not unknown, "The repair packet's findings: names what the repair plan does not hold: "
                         + ', '.join(unknown[:5]) + (f' and {len(unknown) - 5} more' if len(unknown) > 5 else '')
                         + '; list the IDs of the planned findings this assignment repairs, separated by commas or '
                         f'spaces, after planning a confirmed one with `{command}`')
    holders = {finding: row['assignment'] for row in attempts(db) if row['assignment'] != assignment
               for finding in row.get('findings', [])}
    taken = [f'{finding} ({holders[finding]})' for finding in named if finding in holders]
    require(not taken, f'Another repair assignment already holds {", ".join(taken)}; each planned finding is repaired '
                       'under one assignment: leave it out of this packet, or repair it in a new attempt of the '
                       'assignment that holds it')
    return named


# What the lifecycle records on an attempt itself, from its packet, its host and its later events: never a register
# event's to supply.
RECORDED = {'findings', 'result', 'closed', 'retained', 'accepted', 'declined', 'acceptance_deferred', 'submitted',
            'submission_requested', 'monitor', 'startup_evidence', 'observed', 'observed_cwd', 'verified',
            'retirement_requested', 'checkout_baselines', 'worktree_baseline', 'orchestrator', 'sessions',
            'launch_sessions', 'decision', 'needs', 'ticket', 'requested', 'agent', 'job', 'execution_snapshot'}
SETTLED = ('repaired', 'open', 'runtime-bug')  # What a repair attempt's result says of each finding it holds.


def require_settled(held, settled, started):
    """Refuse a result that does not give each finding its attempt holds, and only those, one of SETTLED: only open
    when nothing shows its worker `started`."""
    settled = settled if isinstance(settled, dict) else {}
    missing = [finding for finding in held if finding not in settled]
    problems = [f'missing {", ".join(missing)}'] if missing else []
    extra = [finding for finding in settled if finding not in held]
    problems += [f'{", ".join(extra)} not held by this attempt'] if extra else []
    problems += [f'{finding}: {value!r} is not repaired, open or runtime-bug' for finding, value in settled.items()
                 if finding in held and value not in SETTLED]
    require(not problems, f"This attempt's result settles each finding it holds ({', '.join(held)}) as repaired, open "
                          'or runtime-bug in findings: ' + '; '.join(problems))
    claimed = [f'{finding} as {value}' for finding, value in settled.items() if value != 'open']
    require(started or not claimed, 'This attempt has no recorded startup, so nothing shows a worker had the findings '
            f'it holds: settle each as open, not {", ".join(claimed)}; or, if its worker started, record `startup` '
            'with native: its session record first')


def finding_totals(db):
    """Each planned finding in one bucket: as the latest collected result naming it settles it, pending while only
    uncollected attempts hold it, or unassigned when none does; each bucket's count, and its IDs but the repaired."""
    rows = {row['attempt']: row for row in attempts(db)}
    found = {}
    for (name,) in db.execute("SELECT attempt FROM events WHERE kind = 'register' ORDER BY id"):
        for finding in rows.get(name, {}).get('findings', []):
            settled = ((rows[name].get('result') or {}).get('findings') or {}).get(finding)
            found[finding] = settled or found.get(finding, 'pending')
    buckets = {bucket: [] for bucket in (*SETTLED, 'pending', 'unassigned')}
    planned = planned_findings(db)
    for finding in planned:
        buckets[found.get(finding, 'unassigned')].append(finding)
    return {'planned': len(planned), 'counts': {bucket: len(ids) for bucket, ids in buckets.items()},
            **{bucket: ids for bucket, ids in buckets.items() if bucket != 'repaired'}}


def planned_of(db, job):
    """The assignments a job's plan holds, or None when it has no plan."""
    row = db.execute('SELECT assignments FROM plans WHERE job = ?', (job,)).fetchone()
    return json.loads(row[0]) if row else None


def save(db, data):
    db.execute('INSERT OR REPLACE INTO attempts VALUES (?, ?)',
               (data['attempt'], json.dumps(data)))


def event(db, attempt, kind, payload, *, pending=False):
    cursor = db.execute('INSERT INTO events (attempt, kind, payload, acknowledged) VALUES (?, ?, ?, ?)',
                        (attempt, kind, json.dumps(dict(payload, recorded_at=time.time())), int(not pending)))
    return cursor.lastrowid


def process_stamp(pid):
    process = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='], capture_output=True,
                             text=True, timeout=3, env=dict(os.environ, LC_ALL='C', TZ='UTC'))
    return process.stdout.strip() if process.returncode == 0 else None


def watching(data):
    monitor = data.get('monitor', {})
    return monitor.get('state') == 'running'


def wait_running(data):
    pgid = data.get('monitor', {}).get('wait_pid')
    if pgid is None:
        return False
    process = subprocess.run(['ps', '-ax', '-o', 'pgid=,stat='], capture_output=True, text=True, timeout=3)
    require(process.returncode == 0, 'Cannot verify owned wait teardown')
    return any(int(fields[0]) == pgid and not fields[1].startswith('Z')
               for line in process.stdout.splitlines() if len(fields := line.split()) == 2)


def hold(db, assignment):
    row = db.execute('SELECT data FROM holds WHERE assignment = ?', (assignment,)).fetchone()
    return json.loads(row[0]) if row else None


def pending(db, attempt=None):
    rows = db.execute('SELECT id, attempt, kind, payload FROM events WHERE acknowledged = 0 ORDER BY id')
    return [dict(id=row[0], attempt=row[1], kind=row[2], payload=json.loads(row[3]))
            for row in rows if attempt is None or row[1] == attempt]


def gaps(db, data, stage, revision=None):
    missing = []
    if not data.get('verified'):
        missing.append('runtime settings verification')
    if hold(db, data['assignment']):
        missing.append('design ruling and updated criteria')
    row = db.execute('SELECT data FROM criteria WHERE assignment = ?', (data['assignment'],)).fetchone()
    if row:
        approval = json.loads(row[0])
        if any(not Path(approval[key]).is_file()
               or hashlib.sha256(Path(approval[key]).read_bytes()).hexdigest() != approval[key + '_sha256']
               for key in ('criteria', 'ruling_source')):
            missing.append('approved criteria integrity')
    if stage == 'work' and ((data['runtime'], data['native_session']) in closed_sessions(db) or data.get('result')):
        missing.append('unfinished worker attempt')
    if stage == 'accept':
        result = data.get('result', {})
        if result.get('status') != 'completed':
            missing.append('completed assignment report')
        if not text(revision) or result.get('head') != revision:
            missing.append('report for the checked revision')
        if not report_intact(data):
            missing.append('retained report integrity')
        if result.get('missing_returns'):
            missing.append('job report sections ' + ', '.join(result['missing_returns']))
        if result.get('read_only_changes'):
            missing.append(f'unchanged worktree for a read-only agent ({result["read_only_changes"]})')
    return missing


def yield_gaps(db, include=None, rows=None):
    """Name each way a host that yields now would miss an event of an included worker (`include` None: every one, and
    the run's own gaps with them); `rows`, when given, are the attempts to judge instead of every row of db."""
    missing = []
    rows = attempts(db) if rows is None else rows
    closed = {(row['runtime'], row['native_session']) for row in rows if row.get('closed')}
    included = [saved for saved in rows if include is None or include(saved)]
    names = {saved['attempt'] for saved in included}
    unconsumed = [row for row in pending(db) if row['attempt'] in names]
    receipts = [row for row in unconsumed if row['kind'] in ('wait-finished', 'hold')]
    for saved in included:
        monitor = saved.get('monitor', {})
        submitted = saved.get('submitted') or saved.get('submission_requested')
        if saved.get('result') or (saved['runtime'], saved['native_session']) in closed:
            continue
        # A worker started as its own process wakes the host only through a wait, submitted or not; a native child
        # wakes it natively until submitted, once its recorded startup shows it exists, and an assignment under a
        # design hold waits for the user.
        if not submitted and hold(db, saved.get('assignment')):
            continue
        if not submitted and (saved.get('execution_snapshot') or {}).get('native_dispatch'):
            if not saved.get('startup_evidence'):
                missing.append(f"{saved['attempt']}: native child with no recorded startup; record `startup` with "
                               'native: its session record, or `collect` a cancelled result if it never started')
            continue
        if watching(saved):
            if process_stamp(monitor['pid']) != monitor['stamp']:
                missing.append(f"{saved['attempt']}: wait process lost")
            elif not monitor.get('notify'):
                missing.append(f"{saved['attempt']}: wait has no wake channel")
        else:
            # Only the current wait's unconsumed receipt can still wake the host.
            current = [row for row in receipts if monitor.get('state') == 'finished'
                       and row['attempt'] == saved['attempt'] and row['kind'] == 'wait-finished'
                       and row['payload'].get('token') == monitor.get('token')]
            if not current:
                missing.append(f"{saved['attempt']}: no armed wait" if submitted else
                               f"{saved['attempt']}: never submitted and no armed wait; `submit` its assignment, or "
                               'record `submitted` with evidence the worker already has it, then arm `watch`')
            # A delivered wake the host has not consumed: stopping now, it would never be told again.
            missing += [f"{row['attempt']}: receipt {row['id']} was delivered and not consumed; collect the result "
                        'or ack it and arm the next wait' for row in current
                        if row['payload'].get('delivery', {}).get('state') == 'sent']
    missing += [f"{row['attempt']}: result receipt {row['id']} was not collected; collect it or ack it"
                for row in unconsumed if row['kind'] == 'result']
    missing += [f"{row['attempt']}: receipt {row['id']} was not delivered" for row in receipts
                if row['payload'].get('delivery', {}).get('state') != 'sent']
    return missing + unassigned_findings(db, None if include is None else included, rows, closed)


def unassigned_findings(db, included, rows, closed):
    """The repair plan's findings no attempt holds, named once no repair attempt is unfinished: to the run's own
    check (`included` None), or to a host that registered a repair holding findings of that plan."""
    try:
        planned = planned_findings(db)
    except sqlite3.OperationalError:  # A state no lifecycle command has opened since findings were planned has none.
        return []
    held = {finding for row in rows for finding in row.get('findings', [])}
    unassigned = [finding for finding in planned if finding not in held]
    if not unassigned or included is not None and not any(set(saved.get('findings', [])) & set(planned)
                                                          for saved in included) or any(
            row.get('job') == 'repair' and not row.get('result')
            and (row['runtime'], row['native_session']) not in closed for row in rows):
        return []
    command = shlex.join(['python3', str(Path(__file__).resolve()), 'plan', '--state', state_path(db), '--job',
                          'repair', '--replace']) + ' [--finding <ID>...]'
    return [f'No repair assignment holds {len(unassigned)} of the {len(planned)} planned repair findings '
            f'({", ".join(unassigned[:5])}'
            + (f' and {len(unassigned) - 5} more' if len(unassigned) > 5 else '')
            + "): register a repair attempt whose packet's findings: names them, or, on the user's decision, narrow "
            f'the plan with `{command}`']


# A review or E2E report's own verdict; collection keeps the values the worker returned.
VERDICT_FIELDS = ('review_status', 'documentation_status', 'review_head', 'review_base', 'review_diff_base',
                  'e2e_status', 'e2e_head')


def report_verdict(report):
    """The verdict fields a report's front matter gives."""
    fields = front_matter.parse(Path(report).read_text(errors='replace')) or {}
    return {key: front_matter.unquoted(value) for key, value in fields.items()
            if key in VERDICT_FIELDS and isinstance(value, str)}


def report_intact(data):
    result = data.get('result', {})
    path = Path(result.get('report', ''))
    return (path.is_absolute() and path.is_file()
            and hashlib.sha256(path.read_bytes()).hexdigest() == result.get('report_sha256'))


# A collected assignment status as the routing decision's outcome.
OUTCOMES = outcomes.STATUSES


def linked_decision(execution):
    """The worker-selection decision that this resolved route applied, when the resolver journaled one."""
    decision = execution.get('decision_model') or {}
    if (execution.get('selection_source') == 'decision-model'
            and text(decision.get('decision_id')) and text(decision.get('journal'))):
        return {key: decision[key] for key in ('journal', 'decision_id', 'choice')}
    return None


def annotate(saved, kind, **fields):
    """Append to the linked decision's journal; a capture failure is a warning, never a lifecycle error."""
    link = saved.get('decision')
    if not link:
        return None
    try:
        _, latest = journal_store._find(journal_store.read(link['journal']), link['decision_id'])
        previous = latest.get(kind, {})
        if kind == 'action' and previous or kind == 'execution' and all(
                previous.get(key) == fields[key] for key in ('requested', 'observed', 'revision')):
            return {kind: 'already recorded'}
        journal_store.record(link['journal'], dict(fields, event=kind, decision_id=link['decision_id']))
        return {kind: 'recorded'}
    except (OSError, ValueError, KeyError) as error:
        return {kind: f'not recorded: {error}'}


# Later quality signals about an attempt's work, as outcome metric names.
ATTRIBUTIONS = outcomes.METRICS


def attribute(db, attempt, kind, count, evidence):
    """Attribute a later quality signal to the collected attempt whose work it concerns."""
    require(kind in ATTRIBUTIONS, 'Attribution kind must be ' + ', '.join(ATTRIBUTIONS))
    require(type(count) is int and count > 0, 'Attribution count must be a positive integer')
    require(isinstance(evidence, list) and evidence and all(text(x) for x in evidence),
            'Attribution requires evidence references')
    db.execute('BEGIN IMMEDIATE')
    with db:
        saved = get(db, attempt)
        require(saved.get('result'), 'Attribute quality signals to a collected attempt')
        event_id = event(db, attempt, 'attribution', dict(kind=kind, count=count, evidence=evidence))
        signals = [json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM events WHERE attempt = ? AND kind = 'attribution' ORDER BY id", (attempt,))]
    counts = {}
    for row in signals:
        counts[ATTRIBUTIONS[row['kind']]] = counts.get(ATTRIBUTIONS[row['kind']], 0) + row['count']
    result = saved['result']
    unrecorded = ledger(db, saved)
    journal = annotate(saved, 'outcome', status='failed' if counts.get('reverts') else OUTCOMES[result['status']],
                       revision=result['head'], metrics=dict(result.get('metrics', {}), **counts),
                       evidence=[result['report'], *result['evidence'], *(x for row in signals for x in row['evidence'])])
    return {'event_id': event_id, 'attempt': attempt, 'signals': counts, **({'journal': journal} if journal else {}),
            **({'ledger': unrecorded} if unrecorded else {})}


def fixes_in_chain(db, attempt):
    """How many repairs the `repairs` chain holds from this attempt back to the first, the attempt included."""
    count, saved = 0, get(db, attempt)
    while saved.get('repairs'):
        count, saved = count + 1, get(db, saved['repairs'])
    return count


def census(db):
    """Per route (runtime/model), the collected attempts of this state that wrote work and the review defects and
    repair rounds attributed to them."""
    routes = {}
    for saved in attempts(db):
        if saved.get('result') and DEFINITIONS['agents'].get(saved.get('agent'), {}).get('writes', 'none') != 'none':
            row = outcomes.attempt_row(db, saved, '')
            counts = routes.setdefault(f"{row['runtime']}/{row['model']}",
                                       dict(attempts=0, review_defects=0, repair_rounds=0))
            counts['attempts'] += 1
            for key in ('review_defects', 'repair_rounds'):
                counts[key] += row.get(key, 0)
    return routes


def state_path(db):
    return str(Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve())


def ledger(db, saved, waiting=30):
    """Write a collected attempt's outcome to the machine-wide ledger, waiting up to `waiting` seconds for it; a failed
    write is reported, never fatal."""
    if not saved.get('result'):
        return None
    state = state_path(db)
    try:
        outcomes.put(f"{state}#{saved['attempt']}", outcomes.attempt_row(db, saved, state), waiting)
    except (OSError, ValueError, sqlite3.Error) as error:
        return f'not recorded: {error}'
    return None


def decisions_journal(saved):
    return saved.get('journal') or saved.get('decision', {}).get('journal')


def triage(db, state, saved, event_id, capture):
    """Journal a hidden wake-triage label for this wake; nothing the host does depends on it."""
    policy = saved['execution_snapshot'].get('decision_policy') or {}
    journal = decisions_journal(saved)
    if 'wake-triage' not in policy.get('enabled', []) or not journal:
        return
    path = state.parent / (state.name + '.logs') / (uuid.uuid4().hex + '.capture.log')
    try:
        path.write_bytes(subprocess.run(capture, cwd=saved['worktree'], stdin=subprocess.DEVNULL,
                                        capture_output=True, timeout=15).stdout)
        trace = journal_store.trace_config(journal, saved['repo'], f"{saved['attempt']}/wake-{event_id}")
        result = decisions.shadow(policy, decisions.wake_request(path), trace=trace)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        result = {'status': 'unavailable', 'error': str(error)}
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            payload = json.loads(db.execute('SELECT payload FROM events WHERE id = ?', (event_id,)).fetchone()[0])
            payload['triage'] = dict({key: result[key] for key in ('status', 'decision_id', 'error') if key in result},
                                     journal=journal, capture=str(path))
            db.execute('UPDATE events SET payload = ? WHERE id = ?', (json.dumps(payload), event_id))
    except sqlite3.Error:
        pass  # The receipt is already delivered; a lost shadow label must not fail the wait.


def assess_wake(db, saved, report):
    """A completed report that existed at the last, settled wake shows the worker was done then."""
    rows = [(kind, json.loads(payload)) for kind, payload in db.execute(
        "SELECT kind, payload FROM events WHERE attempt = ? AND kind IN ('wait-started', 'wait-finished') "
        'ORDER BY id', (saved['attempt'],))]
    if not rows or rows[-1][0] != 'wait-finished':
        return None
    receipt = rows[-1][1]
    label = receipt.get('triage', {})
    if (receipt.get('reason') != 'settled' or label.get('status') != 'recorded'
            or Path(report).stat().st_mtime > receipt['recorded_at'] + 1):
        return None
    try:
        journal_store.record(label['journal'], {
            'event': 'assessment', 'decision_id': label['decision_id'], 'actor': 'worker-lifecycle',
            'expected': ['done'], 'category': 'unknown', 'source': 'objective', 'evidence': [report],
            'reason': 'The completed report the host collected was written before this settled wake.'})
        return {'assessment': 'recorded'}
    except (OSError, ValueError, KeyError) as error:
        return {'assessment': f'not recorded: {error}'}


def declining(saved, acceptance):
    """Whether the attempt is declined, or this closure's acceptance decision will decline it (an attempt already
    accepted is never declined by it)."""
    return bool(saved.get('declined')) or (unsettled(saved) and isinstance(acceptance, dict)
                                           and acceptance.get('decision') == 'declined')


def unsettled(saved):
    """A completed attempt whose job requires acceptance, with neither an acceptance nor a decline on record."""
    return (outcomes.needs_acceptance(saved.get('job')) and saved.get('result', {}).get('status') == 'completed'
            and not saved.get('accepted') and not saved.get('declined'))


def return_check(saved, report, now):
    """What a completed result owes its job and agent: the job's report sections, and an untouched
    worktree for an agent that writes nothing (`now` is its observed_state)."""
    if saved.get('job') not in DEFINITIONS['jobs']:
        return [], None  # Registered before agents and jobs existed.
    lines, fenced = [], None
    for line in report.read_text(errors='replace').splitlines():
        fence = re.match(r' {0,3}(`{3,}|~{3,})(.*)$', line)
        if fenced is None and fence and not (fence.group(1)[0] == '`' and '`' in fence.group(2)):
            fenced = fence.group(1)
        elif fenced is not None:
            # Only a bare run of the same character, at least as long, closes a fence.
            if fence and fence.group(1)[0] == fenced[0] and len(fence.group(1)) >= len(fenced) \
                    and not fence.group(2).strip():
                fenced = None
        else:
            lines.append(line.rstrip())
    # A heading names the section exactly, or continues it after a space, as in `## Fixed round 2`.
    missing = [heading for heading in DEFINITIONS['jobs'][saved['job']]['returns']
               if not any(line == heading or line.startswith(heading + ' ') for line in lines)]
    return missing, read_only_change(saved, now)


def observed_state(saved):
    """The checkout states a read-only attempt is checked against at collection, read once: its worktree's and each
    other checkout's its native record put it in; None for any other attempt."""
    if not saved.get('worktree_baseline'):
        return None
    return {'worktree': worktree_state(saved['worktree']),
            'checkouts': {checkout: worktree_state(checkout) for checkout in saved.get('checkout_baselines', {})}}


def checkout_root(path):
    """The top of the Git checkout holding path, resolved, or None outside one."""
    found = subprocess.run(['git', '-C', str(path), 'rev-parse', '--show-toplevel'], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
    return str(Path(found.stdout.strip()).resolve()) if found.returncode == 0 else None


def read_only_change(saved, now):
    """How a read-only agent's worktree, or another checkout its native record put it in, differs from when it was
    recorded, as (reason, restorable), or None when each is as it was."""
    change = checkout_change(saved.get('worktree_baseline', False), now and now['worktree'])
    if change or not now:
        return change
    for checkout, baseline in saved.get('checkout_baselines', {}).items():
        found = checkout_change(baseline, now['checkouts'].get(checkout))
        if found:
            return f'in {checkout}, where its native record put it: {found[0]}', found[1]
    return None


def checkout_change(baseline, now):
    """How a checkout differs from its recorded state, as (reason, restorable), or None when it is as it was. An
    edited or deleted untracked file, or a checkout that cannot be verified, cannot be restored."""
    if baseline is None:
        return 'not a readable Git checkout at registration; read-only work cannot be verified', False
    if not baseline:
        return None
    if now is None:
        return 'worktree state unreadable at collection', False
    # A registration from before untracked files were recorded is held to HEAD and tracked changes only.
    legacy = 'untracked' not in baseline
    note = '; untracked files were not recorded at registration' if legacy else ''
    if now['head'] != baseline['head']:
        return f'HEAD moved from {baseline["head"]} to {now["head"]}{note}', True
    # A submodule is compared before the tracked changes its own changes also show in, so the reason names it.
    for link in sorted(set(baseline.get('submodules', {})) | set(now['submodules'])
                       if 'submodules' in baseline else ()):
        old, new = baseline['submodules'].get(link), now['submodules'].get(link)
        if old == new:
            continue
        found = is_checkout(old) and is_checkout(new) and checkout_change(old, new)
        return ((f'in submodule {link}: {found[0]}', found[1]) if found
                else (f'submodule {link} is not the checkout it was at registration', False))
    if now['changes'] != baseline['changes']:
        return f'tracked files differ from their state at registration{note}', True
    if legacy:
        return None
    before, after = baseline['untracked'], now['untracked']
    if 'submodules' not in baseline:
        # Recorded before nested checkouts and directories Git cannot open were: a nested checkout was a marker and
        # such a directory was left out, so they are compared that way.
        after = {name: 'unreadable:EISDIR' if is_checkout(entry) else entry for name, entry in after.items()
                 if not isinstance(entry, dict) or is_checkout(entry)}
    if appeared := sorted(set(after) - set(before)):
        return f'an untracked file appeared: {appeared[0]}', True
    if deleted := sorted(set(before) - set(after)):
        return f'an untracked file was deleted: {deleted[0]}', False
    for name in sorted(name for name in before if after[name] != before[name]):
        found = is_checkout(before[name]) and is_checkout(after[name]) and checkout_change(before[name], after[name])
        if found:
            return f'in nested repository {name}: {found[0]}', found[1]
        return f'an {"unreadable directory" if name.endswith("/") else "untracked file"} changed: {name}', False


def is_checkout(entry):
    """Whether a snapshot entry is a nested checkout's state, not a file's digest or a directory's record."""
    return isinstance(entry, dict) and 'head' in entry


def partly_checked(state, prefix=''):
    """The directories, by path, a snapshot holds only to what can be read of them."""
    entries = [*state.get('untracked', {}).items(), *((link + '/', entry) for link, entry
                                                      in state.get('submodules', {}).items())]
    return sorted(found for name, entry in entries if isinstance(entry, dict)
                  for found in (partly_checked(entry, prefix + name) if is_checkout(entry)
                                else [prefix + name if name != './' else prefix or name]))


def refusal(change):
    reason, restorable = change
    way_out = ('restore it and collect again' if restorable else
               'record the result with its gap (`record`, kind `result`), then collect again; acceptance stays refused')
    return f'Collection requires an unchanged worktree for a read-only agent ({reason}); {way_out}'


def worktree_state(worktree):
    """HEAD, a digest of tracked changes against it, a digest of each untracked file the ignore rules leave
    visible (the state of a nested repository, the record of a directory Git cannot open) and the state of each
    submodule, or None outside a readable Git checkout. A snapshot check may start from a dirty tree, so a
    read-only agent is held to the state it was given."""
    # Git's warnings are parsed, so they are read untranslated.
    run = lambda *args: subprocess.run(['git', '-C', worktree, *args], capture_output=True, stdin=subprocess.DEVNULL,
                                       env=dict(os.environ, LC_ALL='C'))
    found = run('rev-parse', '--show-prefix', '--verify', '-q', 'HEAD')
    prefix, _, head = found.stdout.partition(b'\n')
    if found.returncode == 0:
        base = 'HEAD'
    elif run('symbolic-ref', '-q', 'HEAD').returncode == 0:
        # A repository with no commit yet: its tracked changes are those against the empty tree.
        head, base = b'unborn', run('hash-object', '-t', 'tree', '--stdin').stdout.decode().strip()
    else:
        return None
    diff = run('diff', '--no-ext-diff', '--no-textconv', '--no-color', '--binary', base)
    untracked, index = run('ls-files', '--others', '--exclude-standard', '-z'), run('ls-files', '--stage', '-z')
    if diff.returncode or untracked.returncode or index.returncode:
        return None
    names = [os.fsdecode(name) for name in untracked.stdout.split(b'\0') if name]
    # Git lists a nested repository as its directory; its own files are snapshotted by the same rules.
    digests = {name: checkout_or_directory(worktree, name) if name.endswith('/')
               else content_digest(Path(worktree, name)) for name in names}
    # Git warns of an untracked directory it cannot open, named from the top of the checkout, and leaves it out.
    for name in re.findall(rb"^warning: could not open directory '(.*/)': [^'\n]*$", untracked.stderr, re.M):
        if name.startswith(prefix):
            name = os.fsdecode(name[len(prefix):])
            digests[name] = directory_record(Path(worktree, name))
    # A top Git can search but not list hides every untracked file; it is held to its status, as `./`.
    try:
        os.scandir(worktree).close()
    except OSError:
        digests['./'] = directory_record(Path(worktree))
    # A submodule is a gitlink (mode 160000) in the index; its own files are snapshotted by the same rules.
    links = {os.fsdecode(entry.split(b'\t', 1)[1]) for entry in index.stdout.split(b'\0')
             if entry.startswith(b'160000 ')}
    return {'head': head.decode().strip(), 'changes': hashlib.sha256(diff.stdout).hexdigest(),
            'untracked': {name: digest for name, digest in digests.items() if digest is not None},
            'submodules': {link: checkout_or_directory(worktree, link) for link in sorted(links)}}


def checkout_or_directory(worktree, name):
    """The state of a checkout nested at name below worktree, or, where Git cannot read one there (an
    uninitialized submodule) or it is reached through a symlink, the record of its directory."""
    path = Path(worktree, name)
    return own_checkout(worktree, name) and worktree_state(str(path)) or directory_record(path)


def directory_record(path):
    """What can still be read of a directory without listing it: its status, a symlink's own, or why not.
    Adding, removing or renaming an entry in it, or changing its permissions, changes the record."""
    try:
        found = os.lstat(path)
    except OSError as error:
        return {'directory': f'unreadable:{errno.errorcode.get(error.errno, error.errno)}'}
    return {'directory': [found.st_mode, found.st_ino, found.st_nlink, found.st_uid, found.st_gid, found.st_size,
                          found.st_mtime_ns, found.st_ctime_ns]}


def own_checkout(worktree, name):
    """Whether name below worktree is the top of a Git checkout of its own, reached through no symlink in any
    component (so each nested snapshot is a real subdirectory, and the recursion ends): an uninitialized
    submodule or a broken repository is not, and Git would read the checkout around it instead."""
    path = Path(worktree, name)
    found = subprocess.run(['git', '-C', str(path), 'rev-parse', '--show-toplevel'], capture_output=True,
                           stdin=subprocess.DEVNULL)
    return (found.returncode == 0 and path.resolve() == Path(worktree).resolve() / name
            and Path(os.fsdecode(found.stdout.strip())).resolve() == path.resolve())


def content_digest(path):
    """A digest of a file's bytes, or of a symlink's target. What cannot be read, such as a file without
    permission, is held to a marker naming why; a file that vanished since it was listed is None."""
    try:
        data = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        return f'unreadable:{errno.errorcode.get(error.errno, error.errno)}'
    return hashlib.sha256(data).hexdigest()


def require_returned(saved):
    """Acceptance needs what the job owes: its report sections, and no edits by a read-only agent."""
    result = saved.get('result', {})
    require(not result.get('missing_returns'),
            f'Acceptance requires the job report sections {", ".join(result.get("missing_returns", []))}; '
            'decline it or collect a new attempt')
    require(not result.get('read_only_changes'),
            'Acceptance requires an unchanged worktree for a read-only agent; decline it')


def settle(saved, acceptance):
    """Apply the acceptance decision a closure carries for a completed attempt whose job requires one."""
    if not unsettled(saved):
        return
    message = (f'Settle acceptance of the completed {saved["job"]} attempt before closing it: record `accepted`, or pass '
               'acceptance {"decision": "accepted", "commit", "evidence"} or {"decision": "deferred" or '
               '"declined", "reason"}')
    require(isinstance(acceptance, dict), message)
    decision = acceptance.get('decision')
    if decision == 'accepted':
        require(text(acceptance.get('commit')) and isinstance(acceptance.get('evidence'), list)
                and acceptance['evidence'] and all(text(x) for x in acceptance['evidence']), message)
        require_returned(saved)
        saved['accepted'] = {'commit': acceptance['commit'], 'evidence': acceptance['evidence']}
    else:
        require(decision in ('deferred', 'declined') and text(acceptance.get('reason')), message)
        saved['declined' if decision == 'declined' else 'acceptance_deferred'] = {'reason': acceptance['reason']}


def submitted_at(db, attempt):
    times = [json.loads(row[0])['recorded_at'] for row in db.execute(
        "SELECT payload FROM events WHERE attempt = ? AND kind IN ('submitted', 'wait-started')", (attempt,))]
    return min(times) if times else None


def assigned_needs(assignment):
    """The capabilities an assignment's `needs` input names, None unless it is exactly one line of its own before
    `ticket context:` holding a one-line bracketed list. The ticket context may quote an issue, and nothing in it
    is the assignment's needs."""
    head = re.split(r'^[ \t]*ticket context:', assignment, maxsplit=1, flags=re.M)[0]
    lines = re.findall(r'^[ \t]*needs:[ \t]*(.*?)[ \t]*$', head, re.M)
    listed = re.fullmatch(r'\[([^\]]*)\]', lines[0]) if len(lines) == 1 else None
    return list(dict.fromkeys(need.strip() for need in listed.group(1).split(',') if need.strip())) if listed else None


def needs_problems(assignment):
    """Why an assignment's `needs` input is not a bracketed list of probe capabilities, else ''."""
    allowed = ', '.join(capability_probe.NEEDS)
    needs = assigned_needs(assignment)
    if needs is None:
        return f'needs must be one line of its own, before ticket context, holding a bracketed list of {allowed}'
    return '; '.join(f'needs {need!r} is not one of {allowed}' for need in needs if need not in capability_probe.NEEDS)


def unmet_needs(saved, evidence):
    """Why a completed ticket attempt's evidence does not show its ticket's needs available, else None.

    The needs are those the assignment names together with those a local ticket's front matter declares.

    The worker's own probe is a JSON file named in the result evidence; the orchestrator's environment says
    nothing about a sandboxed worker's."""
    packet = Path(saved.get('packet') or '')
    job = DEFINITIONS['jobs'][saved['job']]
    if 'ticket' not in job['inputs']:
        return None
    # The needs and ticket registration validated, whatever the assignment file says now; an attempt registered
    # before they were saved falls back to the file.
    declared, ticket = set(saved.get('needs') or []), saved.get('ticket')
    if ticket is None and packet.is_file():
        ticket = agent_defs.input_value(job, packet.read_text(errors='replace'), 'ticket')
    declared |= set(capability_probe.ticket_needs(ticket))
    known = [need for need in capability_probe.NEEDS if need in declared]
    needs = known + sorted(declared - set(known))  # An unknown need is never met.
    reports = []
    for entry in evidence if isinstance(evidence, list) else []:
        try:
            reports.append(json.loads(Path(entry).read_text()))
        except (OSError, ValueError):
            continue
    return capability_probe.unmet(needs, reports, saved['worktree'])


def record(db, data, refuse_changes=False, submitting=False, waiting=30):
    kind, attempt = data.get('kind'), data.get('attempt')
    require(text(attempt), 'Event requires attempt')
    # A read-only worktree is read before the write lock is taken; the result check and any refusal use that one read.
    now = observed_state(get(db, attempt)) if kind == 'result' and data.get('status') == 'completed' else None
    located = elsewhere = observation = None
    if kind == 'startup':
        require('observed' not in data and 'evidence' not in data,
                'record startup takes native: the absolute worker session record, not observed or evidence')
        require(text(data.get('native')),
                'record startup requires native: the absolute path of the worker session record')
        # A native record, and a read-only agent's other checkout, are read before the write lock is taken too.
        target = get(db, attempt)
        try:
            if target['runtime'] != 'opencode':
                located = native_settings.working_directory(data['native'], target['runtime'], target['native_session'])
            observation = native_settings.observe(data['native'], target['runtime'], target['native_session'],
                located if not submitting and target['execution_snapshot'].get('native_dispatch') and located
                else target['worktree'])
        except (OSError, ValueError) as error:
            raise ValueError(f'{error}; record startup with native: the session record of registered worker '
                             f'{target["native_session"]} (OpenCode: opencode session export)') from None
        # Only a Pi record names the child's own directory; Claude and Codex ones name their orchestrator's.
        checkout = (located and target['runtime'] == 'pi' and 'worktree_baseline' in target
                    and checkout_root(located))
        if checkout and checkout != checkout_root(target['worktree']) \
                and checkout not in target.get('checkout_baselines', {}):
            elsewhere = checkout, worktree_state(checkout)
    if kind == 'result' and data.get('status') == 'completed':
        target = get(db, attempt)
        require(target.get('verified'), 'Completion requires verified settings; record startup with native: '
                'the registered worker session record before collecting')
        native = target['startup_evidence']['native']
        require(text(data.get('native')), f'Completion requires native: {native}; collect with that session record '
                '(OpenCode: a fresh opencode session export)')
        require(target['runtime'] == 'opencode' or Path(data['native']).resolve() == Path(native).resolve(),
                f'Completion requires the startup session record; set native: {native}')
        try:
            native_settings.observe(data['native'], target['runtime'], target['native_session'],
                target.get('observed_cwd', target['worktree']) if target['execution_snapshot'].get('native_dispatch')
                else target['worktree'])
        except (OSError, ValueError) as error:
            raise ValueError(f'{error}; collect with native: {native}, the registered worker session record '
                             '(OpenCode: a fresh opencode session export)') from None
        child = target['runtime'] == 'codex' and target['execution_snapshot'].get('native_dispatch')
        packet, reply = native_settings.packet_reply(data['native'], target['runtime'], target['native_session'], attempt,
            parents=target.get('sessions', []) if child else None)
        require(not child or packet, 'Completion requires the Codex child rollout to name a registering host session '
                'as parent_thread_id; start a native child from that host and collect with its native record')
        require(packet, 'Completion requires the assignment packet; send the worker its packet, which starts with '
                f'`assignment: {target["assignment"]}  attempt: {attempt}`, then collect with native: {native}')
        require(reply, f'Completion requires the packet reply; let the worker answer it, then collect with native: {native}')
    # Herdr is asked before the write lock is taken too, so a Herdr that does not answer holds no lock. The
    # execution is read once, here, so the attempt keeps the route that decided whether to ask.
    placed = execution = None
    if kind == 'register':
        session = data.get('native_session')
        if (data.get('runtime') in ('claude', 'codex', 'pi') and text(session) and Path(session).is_absolute()
                and (Path(session).is_file() or not (data['runtime'] == 'codex' and session.startswith('/root/')))):
            # An existing file is a session record, and stands for the session it names; a Codex child's agent_path
            # (/root/<task>) is no file. Herdr is asked about the session, so this comes first.
            data['native_session'] = native_settings.session_id(session, data['runtime'])
        execution = read_object(data['execution']) if text(data.get('execution')) else None
        route = execution or {}
        # The resolver marks a Herdr route by its context or, under a caller key, by the built-in herdr type; a skill
        # route may name the Herdr skill with a plugin prefix.
        skill = str(route.get('executor_config', {}).get('skill', ''))
        if 'herdr' in (route.get('executor_context'), route.get('executor_type')) \
                or skill.rpartition(':')[2] == 'herdr' or 'herdr' in data:
            placed = herdr_placement(data)
    db.execute('BEGIN IMMEDIATE')
    with db:
        if kind == 'register':
            require(not any(row['attempt'] == attempt for row in attempts(db)), 'Attempt already registered')
            if data.get('runtime') == 'pi' and text(data.get('native')):
                # A Pi child's session record names its session; a pi-subagents run id is not it.
                named = native_settings.pi_session_id(data['native'])
                require(data.setdefault('native_session', named) == named,
                        f"native_session {data['native_session']} is not the session {named} the native record names")
            for key in ('assignment', 'repo', 'worktree', 'head', 'worker', 'native_session', 'runtime'):
                require(text(data.get(key)), f'Registration requires {key}')
            require(data['native_session'] not in host_sessions(),
                    'A host session cannot be its own worker; start a fresh worker and register its native_session')
            for key in ('repo', 'worktree'):
                require(Path(data[key]).is_absolute() and Path(data[key]).is_dir(),
                        f'{key} must be an existing absolute directory')
                data[key] = str(Path(data[key]).resolve())
            require(type(data.get('owned')) is bool, 'Registration requires explicit owned boolean')
            require(execution, 'Registration requires execution, the resolved execution file')
            if data['runtime'] == 'codex' and execution.get('native_dispatch'):
                task = native_settings.codex_task_name(attempt)
                # The read-only hook resolves a child only by /root/<task>, so no nested path is registered.
                require(data['native_session'] == f'/root/{task}',
                        f'Codex native attempts need a fresh child; spawn with task_name: {task} '
                        f'and register /root/{task} as native_session')
            settings = execution.get('settings')
            require(isinstance(settings, dict) and not set(settings) - {'model', 'effort'}
                    and all(text(value) for value in settings.values()), 'Invalid resolved settings')
            require(text(execution.get('agent')) and execution.get('job') in DEFINITIONS['jobs'],
                    'Resolved execution requires its agent and a known job (resolve with --job)')
            job = DEFINITIONS['jobs'][execution['job']]
            require(job['agent'] == execution['agent'], f'Job {execution["job"]} is done by {job["agent"]}')
            plan_of = planned_of(db, execution['job'])
            command = shlex.join(['python3', str(Path(__file__).resolve()), 'plan', '--state', state_path(db),
                                  '--job', execution['job'], '--assignment', data['assignment']])
            require(plan_of is None or data['assignment'] in plan_of,
                    f'{data["assignment"]} is not in the {execution["job"]} plan; add it: `{command}`')
            packet = Path(data.get('packet') or '')
            require(packet.is_absolute() and packet.is_file(),
                    'Registration requires packet: the absolute path of the assignment text sent to the worker')
            require(native_settings.has_attempt(packet.read_text(errors='replace'), attempt),
                    f'Registration packet lacks its attempt line; add `attempt: {attempt}` to the packet')
            missing = agent_defs.missing_inputs(job, packet.read_text(errors='replace'))
            require(not missing, f'The {execution["job"]} assignment lacks {", ".join(missing)}; write each as '
                                 f'`name: value` (see {job["path"]})')
            supplied = sorted(RECORDED & set(data))
            require(not supplied, f'Registration takes no {", ".join(supplied)}: the lifecycle records them itself, '
                                  'findings from the packet and the rest as the attempt goes on')
            if execution['job'] == 'repair' and planned_findings(db):
                data['findings'] = assigned_findings(db, data['assignment'], agent_defs.input_value(
                    job, packet.read_text(errors='replace'), 'findings'))
            problem = 'needs' in job['inputs'] and needs_problems(packet.read_text(errors='replace'))
            require(not problem, f'The {execution["job"]} assignment {problem}')
            needs = assigned_needs(packet.read_text(errors='replace')) if 'needs' in job['inputs'] else None
            ticket = (agent_defs.input_value(job, packet.read_text(errors='replace'), 'ticket')
                      if 'ticket' in job['inputs'] else None)
            runtime = execution.get('target_runtime') or (execution.get('host') if execution.get('native_dispatch') else None)
            require(runtime is None or runtime == data['runtime'], 'Runtime conflicts with resolved execution')
            closed = closed_sessions(db)
            require((data['runtime'], data['native_session']) not in closed,
                    'Retired conversation requires a fresh native session')
            for previous in attempts(db):
                same_session = (previous['runtime'] == data['runtime']
                                and previous['native_session'] == data['native_session'])
                same_worker = (previous['worker'] == data['worker']
                               and (previous['runtime'], previous['native_session']) not in closed)
                if same_session or same_worker:
                    require(previous['assignment'] == data['assignment']
                            and previous['repo'] == data['repo']
                            and previous['agent'] == execution['agent'],
                            'A new assignment or agent requires a fresh worker and native session')
                    require(previous['job'] not in job['independent_of'],
                            f'A {execution["job"]} job must be independent of {previous["job"]}: '
                            'this worker already did that job, so start a fresh worker')
                    require(not previous.get('retirement_requested'), 'Native retirement is in progress')
                    require(previous.get('result') and previous.get('retained')
                            and not watching(previous) and not pending(db, previous['attempt']),
                            'Same-assignment reuse requires collected result, explicit retention and no live wait')
            # The launch's write scope is requested like model and effort: a record without it stays unverified.
            scope = (execution.get('launch') or {}).get('verify', {})
            # A route that is not a native child starts its own process: one that names no target runtime (a
            # complete executor instance) is judged by the runtime it registers as.
            own_process = execution.get('native_dispatch') is False or (
                execution.get('target_runtime') in TOP_LEVEL and not execution.get('native_dispatch'))
            require(scope or not own_process or data['runtime'] not in TOP_LEVEL,
                    f'A route to a {data["runtime"]} worker started as its own process requires its launch controls '
                    '(launch.verify); resolve it again with agent_models.py --route')
            saved = dict(data, agent=execution['agent'], job=execution['job'], execution_snapshot=execution,
                         requested=dict(scope, **settings, runtime=data['runtime']), verified=False)
            if needs is not None:
                saved['needs'] = needs
            if ticket is not None:
                saved['ticket'] = ticket
            if placed:
                saved['herdr'] = placed
            if DEFINITIONS['agents'][execution['agent']]['writes'] == 'none':
                saved['worktree_baseline'] = worktree_state(data['worktree'])  # Not in the event: it can be large.
            decision = linked_decision(execution)
            if decision:
                saved['decision'] = decision
            if os.environ.get('HERDR_PANE_ID'):
                # The registering host's pane; the yield hooks hold only that host for this worker.
                saved['orchestrator'] = os.environ['HERDR_PANE_ID']
            sessions = host_sessions()
            if sessions:
                # The registering host session, which decides before the pane; a host started inside another's shell
                # also exports the outer one's, so every exported session is kept.
                saved['sessions'] = sessions
            parent = os.environ.get('PI_SUBAGENT_PARENT_SESSION', '').strip()
            if os.environ.get('PI_SUBAGENT_CHILD') == '1' and parent:
                # Inside a pi-subagents child, whatever host registers, Pi logged the child's launches under the
                # parent; this clears them without holding the parent for a host that answers for itself.
                saved['launch_sessions'] = [parent]
            if 'journal' in data:
                require(text(data['journal']) and Path(data['journal']).is_absolute(),
                        'journal must be an absolute decisions journal path')
            require('census' not in data, 'Registration computes the census; an event cannot supply it')
            require('premise' not in data or text(data['premise']), 'premise must be a sentence')
            if 'repairs' in data:
                require(text(data['repairs']), 'repairs names the attempt whose work this one repairs')
                require(get(db, data['repairs']).get('result'), 'A repaired attempt must be collected first')
                if fixes_in_chain(db, data['repairs']) >= 2:
                    found = census(db)
                    require(text(data.get('premise')),
                            'This repair fixes a fix of a fix; it needs premise: the one assumption the earlier '
                            'fixes shared. Weigh it against this census of collected work per route: '
                            + json.dumps(found))
                    saved['census'] = found
            if execution.get('backend'):
                # A local backend's slot is shared by every workstream on this machine.
                capacity.acquire(execution['backend'], execution.get('capacity') or {},
                                 state_path(db), attempt)
        else:
            saved = get(db, attempt)
            require((saved['runtime'], saved['native_session']) not in closed_sessions(db)
                    or kind in ('ruling', 'accepted', 'declined'), 'Native conversation already closed')
            if kind == 'startup':
                observed = observation['observed']
                directory = Path(state_path(db) + '.native')
                directory.mkdir(mode=0o700, exist_ok=True)
                snapshot = directory / (uuid.uuid4().hex + '.json')
                with os.fdopen(os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
                    json.dump(observation, stream, indent=2)
                    stream.write('\n')
                data = dict(data, observed=observed, evidence=[str(snapshot)], native_observation=observation)
                aliases = data.get('model_alias_evidence', [])
                require(isinstance(aliases, list) and all(text(x) for x in aliases), 'Invalid alias evidence')
                require(not aliases or text(observed.get('model')) and text(saved['requested'].get('model')),
                        'Alias evidence requires requested and observed model IDs')
                saved['observed'], saved['startup_evidence'] = observed, data
                if located:
                    # A Claude or Codex native child records its orchestrator's directory, so this only warns.
                    saved['observed_cwd'] = str(Path(located).resolve())
                if elsewhere:
                    # A read-only Pi child working in another checkout is held to that one too.
                    saved.setdefault('checkout_baselines', {})[elsewhere[0]] = elsewhere[1]
                saved['verified'] = not mismatched(saved)
            elif kind == 'submitted':
                require(not gaps(db, saved, 'work'), 'Verify runtime settings and resolve design hold before work')
                require(not saved.get('submitted') and not saved.get('result'),
                        'Prompt already submitted; reconcile rather than duplicate it')
                saved['submitted'] = data
            elif kind == 'reconciled':
                monitor = saved.get('monitor', {})
                require(monitor.get('state') == 'finished' or watching(saved)
                        and process_stamp(monitor['pid']) != monitor['stamp'],
                        'Reconcile only a finished or lost monitor; retire a live wait through its owner')
                require(not wait_running(saved), 'Owned wait is still running; confirm teardown before rearming')
                saved['monitor']['state'] = 'reconciled'
            elif kind == 'result':
                require(data.get('status') in ('completed', 'blocked', 'failed', 'cancelled'), 'Invalid result status')
                require(text(data.get('head')) and text(data.get('report')), 'Result requires head and durable report')
                report = Path(data['report'])
                require(report.is_absolute() and report.is_file(), 'Result report must be an existing absolute file')
                require(not watching(saved) and saved.get('monitor', {}).get('state') != 'finished',
                        'Collect or reconcile the wait before the assignment result')
                if data['status'] == 'completed':
                    require(not gaps(db, saved, 'work') and (saved.get('submitted')
                            or saved['execution_snapshot'].get('native_dispatch')),
                            'Completion requires verified settings, accepted prompt and resolved design hold')
                    require(not unmet_needs(saved, data.get('evidence')), unmet_needs(saved, data.get('evidence')))
                require(not saved.get('result'), 'Result already collected; use a new attempt for further work')
                if saved.get('findings'):
                    require_settled(saved['findings'], data.get('findings'), saved.get('startup_evidence'))
                saved['result'] = dict(data, report_sha256=hashlib.sha256(report.read_bytes()).hexdigest())
                verdict = report_verdict(report)
                if verdict:
                    saved['result']['verdict'] = verdict
                if data['status'] == 'completed':
                    missing, change = return_check(saved, report, now)
                    if refuse_changes and change:
                        raise ValueError(refusal(change))
                    saved['result'].update({'missing_returns': missing} if missing else {},
                                           **({'read_only_changes': change[0]} if change else {}))
                started = submitted_at(db, attempt)
                if started:
                    saved['result']['metrics'] = {'worker_seconds': round(time.time() - started, 3)}
            elif kind == 'accepted':
                require(saved.get('result', {}).get('status') == 'completed',
                        'Acceptance requires a collected completed result')
                require(not saved.get('accepted'), 'Attempt already accepted')
                require(not saved.get('declined'), 'Attempt was declined; accept a new attempt instead')
                require(text(data.get('commit')), 'Acceptance requires the integrated commit')
                require(report_intact(saved),
                        'The report changed after collection, so the attempt cannot be accepted; record declined, '
                        'or collect a new attempt')
                require_returned(saved)
                saved['accepted'] = data
            elif kind == 'declined':
                require(saved.get('result', {}).get('status') == 'completed',
                        'Declining requires a collected completed result')
                require(not saved.get('accepted'), 'Attempt already accepted')
                require(not saved.get('declined'), 'Attempt already declined')
                require(text(data.get('reason')), 'Declining requires a reason')
                saved['declined'] = data
            elif kind == 'closed':
                require_closable(db, saved, data.get('acceptance'))
                settle(saved, data.get('acceptance'))
                saved['closed'] = data
            elif kind == 'retain':
                require(saved.get('result') and not watching(saved)
                        and all(r['kind'] == 'hold' for r in pending(db, attempt)),
                        'Collect result and acknowledge receipts before retaining or closing')
                require(report_owed(saved, declining(saved, None)),
                        'Retain an intact report before retaining or closing, or decline the attempt')
                require(text(data.get('next_use')) and text(data.get('reason')),
                        'Retention requires reason and next_use for this assignment')
                saved['retained'] = data
            elif kind == 'hold':
                require(text(data.get('reason')) and text(data.get('brief')), 'Hold requires reason and decision brief')
                require(Path(data['brief']).is_absolute(), 'Decision brief path must be absolute')
                risk = data.get('risk', 'possible-breach')
                require(risk in ('within-agreement', 'possible-breach'), 'Invalid agreement risk')
                existing = hold(db, saved['assignment'])
                require(not existing or existing.get('risk') == 'within-agreement' and risk == 'possible-breach',
                        'Design hold already active; possible breaches cannot be downgraded')
                data['risk'] = risk
                db.execute('INSERT OR REPLACE INTO holds VALUES (?, ?)', (saved['assignment'], json.dumps(data)))
            elif kind == 'ruling':
                held = hold(db, saved['assignment'])
                require(held, 'No design hold to resolve')
                require(Path(held['brief']).is_file(), 'Complete the decision brief before recording the ruling')
                for key in ('authority', 'decision', 'criteria'):
                    require(text(data.get(key)), f'Ruling requires {key}')
                human = data['authority'] in ('human', 'user')
                require(human or held.get('risk') == 'within-agreement' and data['authority'] == 'host',
                        'A possible agreement breach requires a human ruling')
                if held.get('canonical_criteria'):
                    require(data.get('source_kind') == 'user-message' if human else
                            data.get('source_kind') in ('host-investigation', 'phase-decision'),
                            'Ruling source must match the actual authority; backend advice is not a human ruling')
                    criteria = Path(data['criteria'])
                    require(criteria.is_absolute() and criteria.is_file()
                            and str(criteria.resolve()) == held['canonical_criteria'],
                            'Update the held canonical criteria before resuming')
                    source = Path(data.get('ruling_source', ''))
                    require(source.is_absolute() and source.is_file() and source.read_text().strip(),
                            'Ruling requires an existing immutable source file')
                    approval = dict(criteria=str(criteria.resolve()), ruling_source=str(source.resolve()),
                                    criteria_sha256=hashlib.sha256(criteria.read_bytes()).hexdigest(),
                                    ruling_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
                    data['approval'] = approval
                    db.execute('INSERT OR REPLACE INTO criteria VALUES (?, ?)',
                               (saved['assignment'], json.dumps(approval)))
                db.execute('DELETE FROM holds WHERE assignment = ?', (saved['assignment'],))
            else:
                raise ValueError('Unknown event kind')
            if kind != 'startup':
                require(isinstance(data.get('evidence'), list) and data['evidence']
                        and all(text(x) for x in data['evidence']), 'Event requires evidence references')
        save(db, saved)
        event_id = event(db, attempt, kind, data,
                         pending=kind in ('result', 'hold') or kind == 'startup' and not saved['verified'])
    journal = None
    if kind == 'register':
        journal = annotate(saved, 'action', actor='worker-lifecycle', disposition='accepted',
                           choice=saved.get('decision', {}).get('choice'),
                           reason=f'Registered attempt {attempt} with the recommended route.')
    elif kind == 'startup' and saved['verified']:
        # The decision journal records model settings; the write scope stays with the attempt.
        requested, observed = ({key: value for key, value in found.items() if key not in SCOPE}
                               for found in (saved['requested'], saved['observed']))
        journal = annotate(saved, 'execution', requested=requested, observed=observed,
                           evidence=data['evidence'], revision=saved['head'],
                           **({'model_alias_evidence': data['model_alias_evidence']}
                              if data.get('model_alias_evidence') else {}))
    elif kind == 'result':
        journal = annotate(saved, 'outcome', status=OUTCOMES[data['status']], revision=data['head'],
                           evidence=[data['report'], *data['evidence']], metrics=saved['result'].get('metrics', {}))
        assessed = assess_wake(db, saved, data['report']) if data['status'] == 'completed' else None
        if assessed:
            journal = dict(journal or {}, **assessed)
    repaired = (attribute(db, data['repairs'], 'repair-round', 1, [f'repair attempt {attempt}'])
                if kind == 'register' and 'repairs' in data else None)
    unrecorded = ledger(db, saved, waiting) if kind in ('result', 'accepted', 'declined', 'closed') else None
    if kind in ('result', 'closed') and saved.get('execution_snapshot', {}).get('backend'):
        try:
            capacity.release(state_path(db), attempt, waiting)
        except (OSError, ValueError, sqlite3.Error):
            pass  # A lease of a finished attempt is reclaimed by the next acquisition.
    # A directory held only to what can be read of it is named even when nothing changed.
    partly = now and [*partly_checked(now['worktree'] or {}),
                      *(os.path.join(checkout, name) for checkout, state in now['checkouts'].items()
                        for name in partly_checked(state or {}))]
    return {'event_id': event_id, 'attempt': attempt, 'verified': saved['verified'],
            **({'partly_checked': partly} if partly else {}),
            **({'journal': journal} if journal else {}), **({'repairs': repaired} if repaired else {}),
            **({'census': saved['census']} if kind == 'register' and 'census' in saved else {}),
            **({'ledger': unrecorded} if unrecorded else {})}


def stop(process):
    # The new process group belongs to this wait command, not the remote worker.
    for signum in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
            if signum == signal.SIGKILL:
                break
        except subprocess.TimeoutExpired:
            continue


def interrupted(signum, frame):
    raise KeyboardInterrupt


def notification(value):
    require(isinstance(value, dict), 'Notification requires an object')
    argv, timeout = value.get('argv'), value.get('timeout')
    require(isinstance(argv, list) and argv and all(text(x) and '\0' not in x for x in argv)
            and argv.count('{receipt}') == 1, 'Notification requires literal argv with one {receipt} argument')
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 45,
            'Notification timeout must be in (0, 45] seconds')
    return dict(argv=argv, timeout=timeout)


def escalate(db, state, attempt, request):
    """Pause dependent work before delivering uncertainty to its decision owner."""
    saved = get(db, attempt)
    risk = request.get('risk')
    require(risk in ('within-agreement', 'possible-breach'), 'Escalation requires explicit agreement risk')
    criteria = Path(request['criteria'])
    require(criteria.is_absolute() and criteria.is_file() and criteria.read_text().strip(),
            'Escalation requires existing canonical criteria')
    phase = request.get('phase', 'alignment')
    require(phase in ('alignment', 'recovery'), 'Escalation phase must be alignment or recovery')
    enabled = phase in saved['execution_snapshot'].get('decision_policy', {}).get('enabled', [])
    notify = request.get('notify', saved.get('monitor', {}).get('notify'))
    result = record(db, dict(request, kind='hold', attempt=attempt, phase=phase,
                            canonical_criteria=str(criteria.resolve()), consultation_enabled=enabled,
                            **({'notify': notify} if notify else {})))
    if notify:
        result['delivery'] = deliver(db, state, result['event_id'], {})
    return dict(result, risk=risk, phase=phase, consultation_enabled=enabled)


def deliver(db, state, event_id, request):
    """Deliver a durable event; transport acceptance never acknowledges its collection."""
    db.execute('BEGIN IMMEDIATE')
    with db:
        row = db.execute('SELECT attempt, kind, payload, acknowledged FROM events WHERE id = ?', (event_id,)).fetchone()
        require(row and row[1] in ('wait-finished', 'hold'), 'Delivery requires a terminal or escalation receipt')
        attempt, kind, payload, acknowledged = row[0], row[1], json.loads(row[2]), row[3]
        previous = payload.get('delivery', {})
        if acknowledged or previous.get('state') == 'sent':
            return previous or {'state': 'acknowledged'}
        if previous:
            require(request.get('retry') is True and isinstance(request.get('evidence'), list)
                    and request['evidence'] and all(text(x) for x in request['evidence']),
                    'Inspect the same parent and supply retry evidence before redelivery')
            require(previous.get('state') != 'sending' or process_stamp(previous['pid']) != previous['stamp'],
                    'Notification delivery is still owned by a live process')
            require(not wait_running({'monitor': {'wait_pid': previous.get('wait_pid')}}),
                    'Owned notification process is still running; reconcile teardown before retry')
        config = notification(request.get('notify', payload.get('notify')))
        saved = get(db, attempt)
        directory = state.parent / (state.name + '.logs')
        directory.mkdir(mode=0o700, exist_ok=True)
        log = directory / (uuid.uuid4().hex + '.notification.log')
        stamp = process_stamp(os.getpid())
        require(stamp, 'Cannot establish notification process identity')
        intent = dict(state='sending', pid=os.getpid(), stamp=stamp, started_at=time.time(), log=str(log),
                      evidence=request.get('evidence', []))
        payload.update(notify=config, delivery=intent)
        db.execute('UPDATE events SET payload = ? WHERE id = ?', (json.dumps(payload), event_id))
    message = dict(event_id=event_id, kind=kind, attempt=attempt, assignment=saved['assignment'],
                   head=saved['head'], state=str(state), recorded_at=payload['recorded_at'],
                   receipt={key: value for key, value in payload.items() if key not in ('notify', 'delivery')},
                   instruction='Inspect this receipt and the native worker; acknowledge after consuming it. A wait is not an accepted assignment result.')
    argv = [json.dumps(message) if arg == '{receipt}' else arg for arg in config['argv']]
    process, code = None, 2
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        with log.open('x') as output:
            process = subprocess.Popen(argv, cwd=saved['worktree'], stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            with db:
                # Re-read so a concurrent update to this receipt is kept.
                db.execute('BEGIN IMMEDIATE')
                payload = json.loads(db.execute('SELECT payload FROM events WHERE id = ?', (event_id,)).fetchone()[0])
                payload['delivery']['wait_pid'] = process.pid
                intent['wait_pid'] = process.pid
                db.execute('UPDATE events SET payload = ? WHERE id = ?', (json.dumps(payload), event_id))
            code = process.wait(timeout=config['timeout'])
            code = code if code >= 0 else 128 - code
    except subprocess.TimeoutExpired:
        code = 124
    except KeyboardInterrupt:
        code = 130
    except OSError as error:
        with log.open('a') as output:
            output.write(str(error) + '\n')
    finally:
        if process is not None:
            stop(process)
        signal.signal(signal.SIGTERM, previous_handler)
    with db:
        # Preserve an acknowledgement received while the callback was running.
        payload = json.loads(db.execute('SELECT payload FROM events WHERE id = ?', (event_id,)).fetchone()[0])
        payload['delivery'] = dict(intent, state='sent' if code == 0 else 'failed',
                                   exit_code=code, finished_at=time.time())
        db.execute('UPDATE events SET payload = ? WHERE id = ?', (json.dumps(payload), event_id))
    return payload['delivery']


def channel_proven(db, argv):
    """Whether the latest finished delivery through this exact channel reached the parent."""
    deliveries = []
    for (payload,) in db.execute("SELECT payload FROM events WHERE kind IN ('wait-finished', 'hold', 'channel-check')"):
        payload = json.loads(payload)
        delivery = payload.get('delivery', {})
        if payload.get('notify', {}).get('argv') == argv and delivery.get('state') in ('sent', 'failed'):
            deliveries.append((delivery.get('finished_at', 0), delivery['state']))
    return bool(deliveries) and max(deliveries)[1] == 'sent'


def check_channel(db, state, saved, notify):
    """Send one no-op message through a new wake channel; a failing channel is never armed."""
    directory = state.parent / (state.name + '.logs')
    directory.mkdir(mode=0o700, exist_ok=True)
    log = directory / (uuid.uuid4().hex + '.channel.log')
    message = dict(kind='channel-check', attempt=saved['attempt'], assignment=saved['assignment'], state=str(state),
                   instruction='No action needed: this checks the wake channel before a worker wait starts.')
    argv = [json.dumps(message) if arg == '{receipt}' else arg for arg in notify['argv']]
    process, code = None, 2
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        with log.open('x') as output:
            process = subprocess.Popen(argv, cwd=saved['worktree'], stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            code = process.wait(timeout=notify['timeout'])
            code = code if code >= 0 else 128 - code
    except subprocess.TimeoutExpired:
        code = 124
    except KeyboardInterrupt:
        code = 130
    except OSError as error:
        with log.open('a') as output:
            output.write(str(error) + '\n')
    finally:
        if process is not None:
            stop(process)
        signal.signal(signal.SIGTERM, previous_handler)
    with db:
        event(db, saved['attempt'], 'channel-check',
              dict(notify=notify, delivery=dict(state='sent' if code == 0 else 'failed', exit_code=code, log=str(log),
                                                finished_at=time.time())))
    require(code == 0, f'Wake channel failed its check (exit {code}; log {log}). It must return once the parent '
                       "accepts the message, without waiting for the parent's turn. Fix it before arming this wait.")


def mismatched(saved):
    """The requested controls that the observed native record contradicts, each with both values."""
    observed = saved.get('observed', {})
    aliases = saved.get('startup_evidence', {}).get('model_alias_evidence')
    return [(key, value, observed.get(key)) for key, value in saved['requested'].items()
            if observed.get(key) != value and not (key == 'model' and aliases)]


def wait_preconditions(db, saved, productive):
    differs = '; '.join(f'{key} requested {value}, observed {"none" if seen is None else seen}'
                        for key, value, seen in mismatched(saved)) if 'observed' in saved else ''
    require(saved.get('verified') and not saved.get('result')
            and (saved['runtime'], saved['native_session']) not in closed_sessions(db),
            'Wait requires a verified, unfinished worker'
            + (f': the native record differs from the launch: {differs}' if differs else ''))
    require(saved.get('monitor', {}).get('state') in (None, 'reconciled'),
            'Wait already owned or awaits reconciliation')
    if productive:
        require(not gaps(db, saved, 'work'), 'Resolve runtime settings and design holds before submission')
        require(not saved.get('submission_requested') and not saved.get('submitted'),
                'Submission already requested; reconcile rather than resubmit')
    else:
        # A native child got its assignment with its spawn; a worker started as its own process gets it by submission.
        require(saved.get('submitted') or saved['execution_snapshot'].get('native_dispatch'),
                'Its submission was not confirmed: record `submitted` with evidence the worker accepted it, or record '
                'its result and register a new attempt' if saved.get('submission_requested') else
                'Wait for submitted work: `submit` its assignment, or record `submitted` with evidence the worker '
                'already has it')


def watch(db, state, attempt, request, *, productive=False):
    argv, timeout = request.get('argv'), request.get('timeout')
    require(isinstance(argv, list) and argv and all(text(x) and '\0' not in x for x in argv), 'Wait requires literal argv')
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 3600,
            'Wait timeout must be in (0, 3600] seconds')
    notify = notification(request['notify']) if 'notify' in request else None
    require(notify or timeout <= 60, 'Waits longer than 60 seconds require an automatic parent notification')
    capture = request.get('capture')
    require(capture is None or isinstance(capture, list) and capture
            and all(text(x) and '\0' not in x for x in capture), 'Capture requires literal argv')
    if notify and not channel_proven(db, notify['argv']):
        # Refuse an invalid wait before sending its parent anything.
        saved = get(db, attempt)
        wait_preconditions(db, saved, productive)
        check_channel(db, state, saved, notify)
    token = uuid.uuid4().hex
    directory = state.parent / (state.name + '.logs')
    directory.mkdir(mode=0o700, exist_ok=True)
    log = directory / (token + '.log')
    db.execute('BEGIN IMMEDIATE')
    with db:
        saved = get(db, attempt)
        wait_preconditions(db, saved, productive)
        if productive:
            saved['submission_requested'] = dict(argv=argv, requested_at=time.time())
        stamp = process_stamp(os.getpid())
        require(stamp, 'Cannot establish monitor process identity')
        saved['monitor'] = dict(state='running', token=token, pid=os.getpid(), stamp=stamp, log=str(log))
        if notify:
            saved['monitor']['notify'] = notify
        save(db, saved)
        event(db, attempt, 'wait-started', dict(token=token, argv=argv, timeout=timeout))
    process, code, reason = None, 2, 'launch-error'
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        with log.open('x') as output:
            # Take the write lock before spawning, so recording the wait's pid never waits behind another writer.
            with db:
                db.execute('BEGIN IMMEDIATE')
                process = subprocess.Popen(argv, cwd=saved['worktree'], stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                saved = get(db, attempt)
                saved['monitor']['wait_pid'] = process.pid
                save(db, saved)
            code = process.wait(timeout=timeout)
            code = code if code >= 0 else 128 - code
            reason = 'settled' if code == 0 else 'wait-error'
    except subprocess.TimeoutExpired:
        stop(process)
        code, reason = 124, 'timeout'
    except KeyboardInterrupt:
        if process is not None:
            stop(process)
        code, reason = 130, 'interrupted'
    except OSError as error:
        with log.open('a') as output:
            output.write(str(error) + '\n')
    finally:
        if process is not None:
            stop(process)
        signal.signal(signal.SIGTERM, previous_handler)
    with db:
        db.execute('BEGIN IMMEDIATE')
        saved = get(db, attempt)
        saved['monitor']['state'] = 'finished'
        if productive and code == 0:
            # The submit argv is activity-gated (e.g. Herdr `agent prompt --wait`), so success proves acceptance.
            saved['submitted'] = dict(argv=argv, evidence=[str(log)], source='submit')
            event(db, attempt, 'submitted', saved['submitted'])
        save(db, saved)
        receipt = dict(token=token, exit_code=code, reason=reason, log=str(log),
                       **({'notify': notify} if notify else {}))
        event_id = event(db, attempt, 'wait-finished', receipt, pending=True)
    delivery = deliver(db, state, event_id, {}) if notify else None
    if notify and capture:
        # After delivery, so classification never delays the host's wake.
        triage(db, state, saved, event_id, capture)
    return dict(receipt, event_id=event_id, **({'delivery': delivery} if delivery else {})), code


def herdr_prompt(argv):
    """(The text, the options after it) of a Herdr `agent prompt` argv, or None for any other command."""
    if (not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv)
            or Path(argv[0]).name != 'herdr'):
        return None  # Not literal text: the wait's own validation reports it.
    # The command follows any global options (--session, --machine, --remote ...) and their values.
    index = next((index for index in range(1, len(argv) - 1) if argv[index:index + 2] == ['agent', 'prompt']), None)
    return None if index is None else ((argv[index + 3:index + 4] or [''])[0], argv[index + 4:])


def submit(db, state, attempt, request):
    """Verify native settings and arm a durable wait before invoking productive submission once."""
    text, flags = herdr_prompt(request.get('argv')) or (None, None)
    # Plain --wait returns when the worker's turn settles, which would hold the orchestrator for the whole job.
    require(flags is None or '--wait' in flags and ('--until=working' in flags or any(
        pair == ('--until', 'working') for pair in zip(flags, flags[1:]))),
            'A Herdr submit returns once the worker is working: use `herdr agent prompt <worker> <assignment> '
            '--wait --until working --until blocked --timeout 45000`; once its receipt is reconciled, arm '
            '`watch` with `herdr agent wait <worker>` and notify for its completion')
    if text is not None:
        # The acceptance a submit records is the packet's: any other text would let a later prompt stand for it.
        packet = Path(get(db, attempt).get('packet') or '')
        require(packet.is_file() and text.strip() == packet.read_text(errors='replace').strip(),
                f'A Herdr submit sends the registered packet: prompt the text of {packet}; a worker that already has '
                'it is recorded with `record` of `submitted` and the evidence that it does')
    require('observed' not in request and 'evidence' not in request,
            'submit takes native: the absolute worker session record, not observed or evidence')
    record(db, dict(kind='startup', attempt=attempt, native=request.get('native'),
                    model_alias_evidence=request.get('model_alias_evidence', [])), submitting=True)
    return watch(db, state, attempt, request, productive=True)


DEFAULT_SERVER = 'default'  # What Herdr names the server a shell reaches without `--session`.


def socket_server(environ=os.environ):
    """The Herdr server session HERDR_SOCKET_PATH is the socket of, by Herdr's own layout (`<config>/herdr/herdr.sock`
    for the default server, `<config>/herdr/sessions/<name>/herdr.sock` for a named one), or None for any other path."""
    socket = Path(environ.get('HERDR_SOCKET_PATH') or '')
    if socket.name != 'herdr.sock':
        return None
    if socket.parent.name == 'herdr':
        return DEFAULT_SERVER
    return socket.parent.name if socket.parent.parent.name == 'sessions' and socket.parent.parent.parent.name == 'herdr' \
        else None


def own_server(environ=os.environ):
    """The Herdr server a process of this environment talks to without `--session`: its socket's, else the default."""
    return socket_server(environ) or DEFAULT_SERVER


def server_option(session):
    """Herdr's global option naming a server session, empty for none and for the server the caller's own socket is
    the socket of: `--session` looks the name up under the caller's config directory, which an isolated one (OpenCode's
    private server) does not hold, so that server is asked through the socket."""
    return ['--session', session] if session and session != socket_server() else []


def herdr(session, *args, timeout=15):
    """(exit code, the JSON object Herdr answered with, from stdout else stderr) for `herdr [--session S] <args>` on a
    Herdr server session (None: the caller's own); ValueError when it does not answer within `timeout` seconds or not
    with a JSON object."""
    try:
        process = subprocess.run(['herdr', *server_option(session), *args], capture_output=True, text=True,
                                 timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ValueError(f'Herdr did not answer {" ".join(args)} within {timeout} s') from None
    response = json.loads(process.stdout if process.stdout.strip() else process.stderr)
    require(isinstance(response, dict), 'Invalid native lifecycle response')
    return process.returncode, response


def herdr_placement(data):
    """Where Herdr itself places a registration's worker, which must be a tab of its own."""
    worker = data.get('worker')
    require(text(worker), 'Registration requires worker')
    claimed = dict(data['herdr']) if isinstance(data.get('herdr'), dict) else {}
    claimed.update({key: data[key] for key in ('runtime', 'native_session') if text(data.get(key))})
    if worker.count('/') == 2:
        # A cross-session handle names its session, pane and agent: ask that session for the pane, confirm the rest.
        for key, value in zip(('session', 'pane_id', 'name'), worker.split('/')):
            require(claimed.setdefault(key, value) == value,
                    f'Registration names {key} {claimed[key]}, but its worker handle {worker} names {value}')
        worker = claimed['pane_id']
    require(text(claimed.get('session')), 'A Herdr worker registers with herdr.session, the Herdr session it runs in')
    session = claimed['session']
    code, response = herdr(session, 'agent', 'get', worker, timeout=LOOKUP_SECONDS)
    require(code == 0, f'Herdr cannot confirm worker {worker} in session {session}: '
                       f'{response.get("error", {}).get("code")}')
    agent = response['result']['agent']
    placed = dict(session=session, **{key: agent.get(key) for key in ('workspace_id', 'tab_id', 'pane_id', 'name')})
    # Herdr's agent kind is the runtime; only a session it reports as an ID is a native conversation ID.
    identity = agent.get('agent_session') or {}
    reported = dict(placed, runtime=agent.get('agent'),
                    native_session=identity.get('value') if identity.get('kind') == 'id' else None)
    for key in ('tab_id', 'pane_id', 'name', 'runtime', 'native_session'):
        require(key not in claimed or reported[key] is None or claimed[key] == reported[key],
                f'Registration names {key} {claimed.get(key)}, but Herdr reports {reported[key]} for {worker}')
    code, response = herdr(session, 'tab', 'get', placed['tab_id'], timeout=LOOKUP_SECONDS)
    require(code == 0, f'Herdr cannot confirm tab {placed["tab_id"]}: {response.get("error", {}).get("code")}')
    panes = response['result']['tab']['pane_count']
    require(panes <= 1, f'One worker per tab: {placed["tab_id"]} holds {panes} panes; {TAB_RECIPE}')
    # HERDR_TAB_ID can be stale; Herdr places the orchestrator's pane now, and an orchestrator outside this session
    # has no tab here to share.
    caller = caller_pane(session, 'registration', LOOKUP_SECONDS) or {}
    require(caller.get('tab_id') != placed['tab_id'],
            f"One worker per tab: {placed['tab_id']} is the orchestrator's own tab; {TAB_RECIPE}")
    return placed


def caller_pane(session, purpose, timeout=15):
    """The calling pane where Herdr has it now, which follows a move; None outside Herdr or that session."""
    if not os.environ.get('HERDR_PANE_ID'):
        return None
    code, response = herdr(session, 'pane', 'current', '--current', timeout=timeout)
    if code == 0:
        return response['result']['pane']
    error = response.get('error')
    require(isinstance(error, dict) and error.get('code') == 'pane_not_found',
            f'Cannot resolve caller identity before {purpose}')
    return None


def retire(db, state, attempt, request):
    """Close the bound owned Herdr pane only after collection, then confirm it and its tab are absent."""
    saved = get(db, attempt)
    require(saved['owned'], 'Existing user worker is not a cleanup target')
    require(saved.get('result') and not watching(saved) and not pending(db, attempt)
            and not wait_running(saved), 'Collect result, acknowledge receipts and reconcile owned waits before retirement')
    declined = declining(saved, request.get('acceptance'))
    require(report_owed(saved, declined), 'Retain an intact report before retirement, or decline the attempt')
    session, pane = request.get('session'), request.get('pane')
    require(text(session) and text(pane), 'Retirement requires explicit Herdr session and pane')
    require(pane != os.environ.get('HERDR_PANE_ID'), 'Caller pane is not a cleanup target')
    require((caller_pane(session, 'retirement') or {}).get('pane_id') != pane, 'Caller pane is not a cleanup target')
    evidence = request.get('jobs_evidence')
    require(isinstance(evidence, list) and evidence and all(text(x) for x in evidence),
            'Retirement requires reconciled owned-job evidence')
    closure = closed_sessions(db).get((saved['runtime'], saved['native_session']))
    if closure:
        return {'attempt': attempt, 'closed': True, 'native_closed_by': closure['attempt'],
                'evidence': closure['closed']['evidence']}
    final_retirement(db, saved, declined)
    settle(dict(saved), request.get('acceptance'))  # Refuse before the pane closes, not after.
    previous = saved.get('retirement_requested')
    if previous:
        require(previous['session'] == session and previous['pane'] == pane,
                'Reconcile the original retirement target')
        require(process_stamp(previous['pid']) != previous['stamp'], 'Retirement already owned by a live process')
    code, response = herdr(session, 'agent', 'get', pane)
    present, exited = code == 0, False
    if present:
        agent = response['result']['agent']
        identity = agent.get('agent_session', {})
        require(isinstance(identity, dict), 'Invalid native conversation identity')
        # Another worker started in the recorded pane: closing the pane would close that one, and the reap closes this.
        require(pane != (saved.get('herdr') or {}).get('pane_id') or conversation_named(saved, identity) is not False,
                f"Pane {pane} now holds another conversation ({identity.get('value')}), so this attempt's worker is "
                f"gone: close it with `python3 {shlex.quote(str(Path(__file__).resolve()))} reap --state "
                f"{shlex.quote(state_path(db))}`")
        require(agent.get('pane_id') == pane and agent.get('agent') == saved['runtime'],
                'Native pane no longer holds the registered runtime')
        require(saved['worker'] in (pane, agent.get('name'), f"{session}/{pane}/{agent.get('name')}"),
                'Native target does not match the registered worker handle')
        require(conversation_named(saved, identity), 'Native conversation no longer matches the attempt')
        require(agent.get('agent_status') in ('idle', 'done'), 'Native worker is active or blocked; retain it for inspection')
    else:
        # The worker exited in its own pane: Herdr lists the pane with no agent in it, so closing it ends no worker. A
        # retirement already under way that finds the pane gone has nothing left to close.
        identity = None
        code, response = herdr(session, 'pane', 'get', pane)
        listed = (response.get('result') or {}).get('pane') if code == 0 else None
        placed = saved.get('herdr') or {}
        exited = ((session, pane) == (placed.get('session'), placed.get('pane_id')) and isinstance(listed, dict)
                  and not listed.get('agent'))
        require(previous or exited, 'Inspect the missing native worker before recording closure')
    if present or exited:
        db.execute('BEGIN IMMEDIATE')
        with db:
            saved = get(db, attempt)
            # Another command may have accepted the attempt, or changed its report, while Herdr was asked.
            declined = declining(saved, request.get('acceptance'))
            require(report_owed(saved, declined),
                    'Retain an intact report before retirement, or decline the attempt')
            final_retirement(db, saved, declined)
            current = saved.get('retirement_requested')
            require(not current or process_stamp(current['pid']) != current['stamp'],
                    'Retirement already owned by a live process')
            stamp = process_stamp(os.getpid())
            require(stamp, 'Cannot establish retirement process identity')
            saved['retirement_requested'] = dict(session=session, pane=pane, pid=os.getpid(),
                                                  stamp=stamp, native_identity=identity)
            save(db, saved)
            event(db, attempt, 'retirement-started', saved['retirement_requested'])
        code, response = herdr(session, 'pane', 'close', pane)
        require(code == 0 and response.get('result', {}).get('type') == 'ok',
                'Native close failed; reconcile the original pane before retrying')
    code, response = herdr(session, 'pane', 'get', pane)
    require(code != 0 and response.get('error', {}).get('code') == 'pane_not_found',
            'Native closure is not confirmed; preserve the original retirement target')
    # Closing a tab's only pane closes the tab; a tab that survives still holds another pane. An attempt
    # registered without a Herdr identity has no recorded tab and keeps the pane rule alone.
    tab, tab_response = saved.get('herdr', {}).get('tab_id'), None
    if tab:
        code, tab_response = herdr(session, 'tab', 'get', tab)
        require(code != 0 and tab_response.get('error', {}).get('code') == 'tab_not_found',
                f'Tab {tab} is not confirmed closed after its worker pane closed: {json.dumps(tab_response)}; '
                'closure is not recorded: close the remaining panes or the tab, then retire again')
    directory = state.parent / (state.name + '.native')
    directory.mkdir(mode=0o700, exist_ok=True)
    proof = directory / (uuid.uuid4().hex + '.json')
    proof.write_text(json.dumps(dict(session=session, pane=pane, confirmation=response, tab=tab,
                                     tab_confirmation=tab_response, jobs_evidence=evidence), indent=2) + '\n')
    record(db, dict(kind='closed', attempt=attempt, evidence=[str(proof), *evidence],
                    **({'acceptance': request['acceptance']} if 'acceptance' in request else {})))
    return {'attempt': attempt, 'closed': True, 'evidence': str(proof)}


def herdr_answer(placed, timeout=LOOKUP_SECONDS):
    """(Herdr's answer for the worker's tab, else pane, as one line, whether that confirms it gone, None when the server
    gave no answer): only a not-found answer for it, or a server that is not running, confirms it; a listing or any
    other error does not."""
    kind, target = ('tab', placed['tab_id']) if placed.get('tab_id') else ('pane', placed['pane_id'])
    command = f"herdr --session {placed['session']} {kind} get {target}"
    try:
        code, response = herdr(placed['session'], kind, 'get', target, timeout=timeout)
    except (OSError, ValueError) as error:
        return f'{command}: no answer ({error})', None
    error = response.get('error')
    answer = (error.get('code') if isinstance(error, dict) else None) if code else 'listed'
    return f'{command}: {answer}', bool(code) and answer in (f'{kind}_not_found', 'server_not_running')


def conversation_named(saved, identity):
    """Whether Herdr's session identity names the attempt's native conversation (True), another one (False), or none it
    can tell apart (None): an ID is compared with the attempt's, a record path with the file its startup observed,
    however either path names it."""
    value, kind = identity.get('value'), identity.get('kind')
    source = saved.get('startup_evidence', {}).get('native_observation', {}).get('source')
    if not value:
        return None
    try:
        same_file = kind == 'path' and source and os.path.samefile(value, source)
    except OSError:  # A path that names no file is compared by its resolved spelling.
        same_file = os.path.realpath(value) == os.path.realpath(source)
    if value == saved['native_session'] or same_file:
        return True
    return False if kind == 'id' or kind == 'path' and source else None


def pane_answer(saved, timeout=LOOKUP_SECONDS):
    """(Herdr's answer for the worker's pane, as one line, whether it shows the pane holding another conversation, which
    confirms the worker gone though its tab lives on; None when the server gave no answer)."""
    placed = saved['herdr']
    command = f"herdr --session {placed['session']} pane get {placed['pane_id']}"
    try:
        code, response = herdr(placed['session'], 'pane', 'get', placed['pane_id'], timeout=timeout)
    except (OSError, ValueError) as error:
        return f'{command}: no answer ({error})', None
    identity = ((response.get('result') or {}).get('pane') or {}).get('agent_session') if code == 0 else None
    identity = identity if isinstance(identity, dict) else {}
    if conversation_named(saved, identity) is False:
        return f'{command}: holds another conversation ({identity["value"]})', True
    return command, False


def require_closable(db, saved, acceptance):
    """Refuse a closure the lifecycle would not record: each attempt of the conversation settled, this one collected
    with no receipt outstanding, its report intact or declined, owned, and its acceptance settled."""
    declined = declining(saved, acceptance)
    final_retirement(db, saved, declined)
    require(saved.get('result') and not watching(saved) and not pending(db, saved['attempt']),
            'Collect result and acknowledge receipts before retaining or closing')
    require(report_owed(saved, declined), 'Retain an intact report before retaining or closing, or decline the attempt')
    require(saved['owned'], 'Existing user worker is not a cleanup target')
    settle(dict(saved), acceptance)


def require_ended(db, attempt, acceptance):
    """Refuse a closure recorded by hand for a Herdr worker whose own pane Herdr shows still holding its conversation:
    retire ends it, by closing that pane."""
    saved = get(db, attempt)
    placed = saved.get('herdr')
    if not placed:
        return
    # Herdr is asked only about a closure the lifecycle would record: it refuses the others itself, naming their command.
    require((saved['runtime'], saved['native_session']) not in closed_sessions(db), 'Native conversation already closed')
    require_closable(db, saved, acceptance)
    pane, script, state = placed['pane_id'], shlex.quote(str(Path(__file__).resolve())), shlex.quote(state_path(db))
    retire = f'`python3 {script} retire --state {state} --attempt {attempt} --input <request>`'
    try:
        code, response = herdr(placed['session'], 'pane', 'get', pane, timeout=LOOKUP_SECONDS)
        error = (response.get('error') or {}).get('code') if code else None
        unknown = None if not code or error in ('pane_not_found', 'server_not_running') else \
            f'pane get answered {error}'
    except (OSError, ValueError) as failure:
        unknown = str(failure)
    require(not unknown, f"Herdr cannot confirm whether pane {pane} still holds this attempt's conversation "
                         f'({unknown}); record the closure once it does, or retire the worker with {retire}')
    listed = ((response.get('result') or {}).get('pane') or {}) if code == 0 else {}
    identity = listed.get('agent_session') if isinstance(listed.get('agent_session'), dict) else {}
    named = ''.join(f'{word} ' for word in server_option(placed['session']))
    if listed.get('agent') and conversation_named(saved, identity) is None:
        raise ValueError(f"Herdr pane {pane} in tab {placed['tab_id']} holds a conversation "
                         f"({identity.get('value') or 'Herdr names none'}) "
                         "this attempt cannot tell from its own: once you have checked it is this attempt's worker, "
                         f'close it with `herdr {named}pane close {pane}`, then record the closure')
    require(not (listed.get('agent') and conversation_named(saved, identity)),
            f"Herdr pane {pane} in tab {placed['tab_id']} still holds this attempt's conversation, so its worker has "
            f'not ended: retire it with {retire}, which closes that pane and records the closure')


def state_fields(text):
    """The `key: value` lines of the State section (heading `## State` exactly) of a run card's text, '' for a blank or
    placeholder (`<...>`) value; {} when it has none."""
    heading = re.search(r'^## State[ \t]*$', text, re.M)
    fields = {}
    for line in re.split(r'^## ', text[heading.end():], maxsplit=1, flags=re.M)[0].splitlines() if heading else []:
        key, colon, value = line.partition(':')
        if colon and re.fullmatch(r'[a-z-]+', key):
            value = value.strip()
            fields[key] = '' if value.startswith('<') else value
    return fields


def card_state(path):
    """The State of the run card at `path`; {} when it cannot be read or has none."""
    try:
        return state_fields(path.read_text(errors='replace'))
    except OSError:
        return {}


def confirmed(state):
    """Whether the card's user confirmed it: `confirmed:` holds a date and the user's words, not blank or `no` (as the
    template's `no | <date> "<the user's words>"` begins)."""
    return not re.match(r'(no\b|$)', state.get('confirmed', ''), re.I)


def card_sessions(state):
    """The host sessions the card names on `session:`."""
    return [part.strip() for part in state.get('session', '').split(',') if part.strip() not in ('', 'unknown')]


def recorded_sessions(data):
    """The host sessions an attempt's registrar exported; none for a row that is not such a record."""
    if not isinstance(data, dict):
        return []
    listed = data.get('sessions') if isinstance(data.get('sessions'), list) else []
    return [value for value in listed if isinstance(value, str)]


def reap(db, state, include=lambda saved: True, budget=REAP_SECONDS):
    """Close, through `record`, each included attempt whose status is already decided: a collected worker whose Herdr
    tab its server confirms gone, or whose pane it shows holding another conversation, and a retained native subagent
    (no Herdr placement) of a run whose card ended delivered. Returns what it closed, what it left open for want of
    Herdr's confirmation, and what the lifecycle refused to close, with its refusal. It spends `budget` seconds in all,
    on Herdr's answers and on any lock, and leaves the attempts it did not reach for the next stop."""
    deadline = time.monotonic() + budget
    card = state.parent.parent / 'autopilot.md'
    terminal = card_state(card).get('terminal')
    closed, left, refused, done, silent = [], [], [], set(closed_sessions(db)), set()
    for saved in attempts(db):
        key = (saved['runtime'], saved['native_session'])
        # Only the conversation's final attempt closes it, and only once collected: its receipts acknowledged and no
        # wait running, which the host settles first. A user's own worker is no cleanup target.
        if not (include(saved) and saved['owned'] and saved.get('result')) or key in done \
                or conversation(db, saved)[1] != saved['attempt']:
            continue
        if watching(saved) or pending(db, saved['attempt']) or wait_running(saved):
            continue
        if time.monotonic() >= deadline:
            left.append({'attempt': saved['attempt'], 'reason': 'out of time for this stop; left for the next'})
            continue
        if saved.get('herdr'):
            server = saved['herdr']['session']
            if server in silent:  # Asked once: a server that does not answer is not asked for each of its workers.
                left.append({'attempt': saved['attempt'], 'reason': f'herdr --session {server}: no answer earlier'})
                continue
            evidence, gone = herdr_answer(saved['herdr'], min(LOOKUP_SECONDS, deadline - time.monotonic()))
            why = 'its Herdr tab is gone'
            if gone is False and time.monotonic() < deadline:
                # The tab lives on, perhaps with another worker started in the pane: that one's conversation there
                # confirms this worker gone.
                answer, replaced = pane_answer(saved, min(LOOKUP_SECONDS, deadline - time.monotonic()))
                if replaced:
                    evidence, gone, why = answer, True, 'its Herdr pane holds another conversation'
                elif replaced is None:
                    evidence, gone = answer, None
            if gone is None:
                silent.add(server)
        elif saved.get('retained') and terminal in ENDED:
            evidence, gone, why = f'{card}: terminal: {terminal}', True, f'its run card ended {terminal}'
        else:
            continue
        if not gone:
            left.append({'attempt': saved['attempt'], 'reason': evidence})
            continue
        # A completed attempt of a job that needs acceptance is closed with it deferred, not decided here.
        acceptance = ({'acceptance': {'decision': 'deferred', 'reason': f'closed by reap: {why}'}}
                      if unsettled(saved) else {})
        # SQLite's own wait for a lock sleeps in whole seconds and overshoots the budget, so no closure waits on one: a
        # locked state is polled until the deadline, and the machine-wide ledgers are left for the next write.
        db.execute('PRAGMA busy_timeout = 0')
        done_now = None
        while done_now is None:
            try:
                done_now = record(db, dict(kind='closed', attempt=saved['attempt'], evidence=[evidence], **acceptance),
                                  waiting=0)
            except ValueError as error:  # The lifecycle's own refusal, for the host to settle.
                refused.append({'attempt': saved['attempt'], 'reason': str(error)})
                break
            except sqlite3.OperationalError as error:
                if time.monotonic() >= deadline:  # Still locked for the whole budget: the next stop.
                    left.append({'attempt': saved['attempt'], 'reason': str(error)})
                    break
                time.sleep(0.1)
        if done_now is None:
            continue
        closed.append({'attempt': saved['attempt'], 'evidence': [evidence]})
        done.add(key)
    return {'closed': closed, 'open': left, 'refused': refused}


def collect(db, state, attempt, request):
    """Persist a checked report and collect its receipts with a required final disposition."""
    disposition = request.get('disposition', {})
    require(isinstance(disposition, dict), 'Collection disposition must be an object')
    require(disposition.get('kind') in ('retire', 'retain'), 'Collection requires retirement or explicit retention')
    saved = get(db, attempt)
    paneless = (disposition['kind'] == 'retire' and not saved.get('herdr')
                and not any(disposition.get(key) for key in ('session', 'pane')))
    if disposition['kind'] == 'retire':
        require(saved['owned'], 'Existing user worker is not a cleanup target')
        require(not paneless or isinstance(disposition.get('evidence'), list) and disposition['evidence']
                and all(text(x) for x in disposition['evidence']),
                'A worker with no Herdr pane (a native subagent) retires on disposition evidence: pass evidence '
                'naming how its closure was confirmed')
    else:
        require(text(disposition.get('reason')) and text(disposition.get('next_use'))
                and isinstance(disposition.get('evidence'), list) and disposition['evidence']
                and all(text(x) for x in disposition['evidence']), 'Retention requires reason, next_use and evidence')
    previous = saved.get('result')
    if previous:
        require(all(previous.get(key) == request.get(key) for key in ('status', 'head', 'report'))
                and report_owed(saved), 'Resume collection with the same checked result and, for a completed one, '
                'its intact report')
        result = {'attempt': attempt, 'already_collected': True}
    else:
        result = record(db, dict(request, kind='result', attempt=attempt), refuse_changes=True)
    with db:
        db.execute("UPDATE events SET acknowledged = 1 WHERE attempt = ? AND kind IN ('wait-finished', 'result')", (attempt,))
    if paneless:
        # No pane to close: the closure rests on the evidence the request gives.
        closure = closed_sessions(db).get((saved['runtime'], saved['native_session']))
        if not closure:
            record(db, dict(kind='closed', attempt=attempt, evidence=disposition['evidence'],
                            **({'acceptance': disposition['acceptance']} if 'acceptance' in disposition else {})))
        final = {'attempt': attempt, 'closed': True, 'evidence': disposition['evidence'],
                 **({'native_closed_by': closure['attempt']} if closure else {})}
    elif disposition['kind'] == 'retire':
        final = retire(db, state, attempt, disposition)
    else:
        final = record(db, dict(disposition, attempt=attempt))
    return {'result': result, 'disposition': final}


def status(db, attempt=None):
    if attempt is not None:
        get(db, attempt)
    result = []
    closed = closed_sessions(db)
    for saved in attempts(db):
        if attempt is not None and saved['attempt'] != attempt:
            continue
        attention = []
        closure = closed.get((saved['runtime'], saved['native_session']))
        if not closure:
            if hold(db, saved['assignment']):
                attention.append('design ruling required')
            if not saved['verified']:
                attention.append('verify runtime settings')
            monitor = saved.get('monitor', {})
            if (monitor.get('state') == 'finished' or watching(saved)
                    and process_stamp(monitor['pid']) != monitor['stamp']):
                attention.append('reconcile wait with native worker and report')
            elif saved.get('submitted') and not saved.get('result') and not watching(saved):
                attention.append('arm independent wait or collect native result')
            if saved.get('result') and not saved.get('retained'):
                attention.append('close owned worker or record same-assignment retention')
            if saved.get('retirement_requested'):
                attention.append('reconcile native retirement')
        if saved.get('acceptance_deferred') and unsettled(saved):
            attention.append('record acceptance or decline')
        if any(row['payload'].get('notify') and row['payload'].get('delivery', {}).get('state') != 'sent'
               for row in pending(db, saved['attempt'])):
            attention.append('inspect parent and recover pending notification')
        summary = {key: saved[key] for key in ('attempt', 'assignment', 'agent', 'job', 'worker', 'native_session',
                   'herdr', 'head', 'requested', 'observed', 'observed_cwd', 'verified', 'monitor', 'result',
                   'accepted', 'declined', 'acceptance_deferred', 'retained', 'closed',
                   'retirement_requested', 'premise', 'census', 'findings') if key in saved}
        if saved.get('observed_cwd') and native_settings.outside(saved['observed_cwd'], saved['worktree'],
                                                                 saved['runtime']):
            summary['warnings'] = [f"the native record puts the worker in {saved['observed_cwd']}, outside its "
                                   f"registered worktree {saved['worktree']}"]
        result.append(dict(summary, attention=attention,
                           **({'native_closed_by': closure['attempt']} if closure else {})))
    active_holds = [dict(json.loads(row[1]), assignment=row[0])
                    for row in db.execute('SELECT assignment, data FROM holds ORDER BY assignment')
                    if attempt is None or row[0] == get(db, attempt)['assignment']]
    missing = yield_gaps(db)
    return {'attempts': result, 'pending': pending(db, attempt), 'holds': active_holds,
            'yield': {'complete': not missing, 'missing': missing}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('record', 'watch', 'submit', 'collect', 'retire', 'deliver', 'escalate',
                                           'status', 'ack', 'gate', 'attribute', 'plan', 'reap', 'findings'))
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--input', type=Path, help='absolute lifecycle request JSON')
    parser.add_argument('--attempt')
    parser.add_argument('--event-id', type=int)
    parser.add_argument('--stage', choices=('work', 'accept', 'yield'))
    parser.add_argument('--revision')
    parser.add_argument('--risk', choices=('within-agreement', 'possible-breach'))
    parser.add_argument('--criteria')
    parser.add_argument('--brief')
    parser.add_argument('--reason')
    parser.add_argument('--evidence', action='append', default=[])
    parser.add_argument('--kind', choices=tuple(ATTRIBUTIONS), help='attribute: the quality signal')
    parser.add_argument('--count', type=int, default=1, help='attribute: how many of that signal')
    parser.add_argument('--job', help='plan: the job whose assignments are planned')
    parser.add_argument('--assignment', action='append', default=[], help='plan: one planned assignment, repeated')
    parser.add_argument('--finding', action='append', default=[],
                        help='plan: one confirmed finding the repair job must repair, repeated')
    parser.add_argument('--replace', action='store_true', help='plan: the assignments or findings are the whole plan')
    parser.add_argument('--session', help='reap: only the attempts this host session registered')
    args = parser.parse_args()
    try:
        with connect(args.state) as db:
            code = 0
            if args.action in ('record', 'watch', 'submit', 'collect', 'retire', 'deliver', 'escalate'):
                if args.action == 'escalate' and not args.input:
                    data = dict(risk=args.risk, criteria=args.criteria, brief=args.brief,
                                reason=args.reason, evidence=args.evidence)
                else:
                    require(args.input, 'This operation requires --input')
                    data = read_object(args.input)
                if args.action == 'record':
                    if data.get('kind') == 'closed' and text(data.get('attempt')):
                        require_ended(db, data['attempt'], data.get('acceptance'))
                    result = record(db, data)
                elif args.action == 'deliver':
                    require(args.event_id is not None, 'Delivery requires --event-id')
                    result = deliver(db, args.state, args.event_id, data)
                    code = int(result.get('state') == 'failed')
                elif args.action == 'escalate':
                    require(text(args.attempt), 'Escalation requires --attempt')
                    result = escalate(db, args.state, args.attempt, data)
                elif args.action in ('collect', 'retire'):
                    require(text(args.attempt), 'Collection/retirement requires --attempt')
                    operation = collect if args.action == 'collect' else retire
                    result = operation(db, args.state, args.attempt, data)
                else:
                    require(text(args.attempt), 'Watch/submit requires --attempt')
                    operation = submit if args.action == 'submit' else watch
                    result, code = operation(db, args.state, args.attempt, data)
            elif args.action == 'status':
                result = status(db, args.attempt)
            elif args.action == 'findings':
                result = finding_totals(db)
            elif args.action == 'reap':
                result = reap(db, args.state, lambda saved: not args.session
                              or args.session in recorded_sessions(saved))
            elif args.action == 'plan':
                require(not (args.assignment and args.finding), 'Plan takes --assignment or --finding, not both')
                # A repair plan replaced with nothing is emptied: repairs the user called off.
                emptied = args.job == 'repair' and args.replace and not args.assignment
                result = (plan_findings(db, args.job, args.finding, args.replace) if args.finding or emptied
                          else plan(db, args.job, args.assignment, args.replace))
            elif args.action == 'attribute':
                require(text(args.attempt) and args.kind, 'Attribution requires --attempt and --kind')
                result = attribute(db, args.attempt, args.kind, args.count, args.evidence)
            elif args.action == 'ack':
                require(args.event_id is not None, 'Ack requires --event-id')
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT payload FROM events WHERE id = ?', (args.event_id,)).fetchone()
                require(row, 'Unknown receipt')
                payload = json.loads(row[0])
                payload.setdefault('acknowledged_at', time.time())
                db.execute('UPDATE events SET acknowledged = 1, payload = ? WHERE id = ?',
                           (json.dumps(payload), args.event_id))
                result = {'acknowledged': args.event_id}
            else:
                require(args.stage == 'yield' or text(args.attempt) and args.stage,
                        'Gate requires --stage, and --attempt for work or accept')
                missing = (yield_gaps(db) if args.stage == 'yield'
                           else gaps(db, get(db, args.attempt), args.stage, args.revision))
                result = {'complete': not missing, 'missing': missing}
                code = int(bool(missing))
            print(json.dumps(result, indent=2))
        return code
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
