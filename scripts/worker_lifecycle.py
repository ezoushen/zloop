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
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

# Find the sibling decision modules even under an isolated interpreter (python -I).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_defs  # noqa: E402
import capacity  # noqa: E402
import decisions  # noqa: E402
import journal_store  # noqa: E402
import native_settings  # noqa: E402
import outcomes  # noqa: E402

DEFINITIONS = agent_defs.load()
TAB_RECIPE = ('start the worker in a tab of its own: `herdr tab create --workspace <coordinator workspace> '
              '--cwd <worktree> --label "<slug>/<assignment> <role>" --env ZLOOP_WORKER=<slug>/<assignment> '
              '--no-focus`, then `herdr agent start <name> --kind <kind> --pane <returned root pane> -- <launch args>`, '
              'then register it')
LOOKUP_SECONDS = 5  # Registration's wait on each Herdr call.
# Native write-scope controls: a launch requests them (its `verify`) and startup observes them.
SCOPE = ('permission_mode', 'sandbox', 'approval', 'filesystem', 'tools', 'permissions')
TOP_LEVEL = ('claude', 'codex', 'opencode', 'pi')  # Runtimes whose own process the resolver gives launch controls.
# What Claude Code, Codex and Pi export to the coordinator's shell as its host session ID.
SESSION_VARIABLES = ('CLAUDE_CODE_SESSION_ID', 'CODEX_SESSION_ID', 'PI_SESSION_ID')


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
    path.touch(mode=0o600, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS holds (assignment TEXT PRIMARY KEY, data TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS criteria (assignment TEXT PRIMARY KEY, data TEXT NOT NULL)')
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


def final_retirement(db, saved, declining=False):
    """Every attempt of the conversation is collected with its report intact, or declined (a changed report can
    never be accepted, and its attempt still closes), and nothing of it is still waiting."""
    siblings = {row['attempt']: row for row in attempts(db)
                if (row['runtime'], row['native_session']) == (saved['runtime'], saved['native_session'])}
    latest = next(row[0] for row in db.execute("SELECT attempt FROM events WHERE kind = 'register' ORDER BY id DESC")
                  if row[0] in siblings)
    require(latest == saved['attempt'], 'Retire the final registered attempt of the native conversation')
    require(all(row.get('result')
                and (report_intact(row) or row.get('declined') or declining and row['attempt'] == saved['attempt'])
                and not watching(row)
                and not pending(db, row['attempt']) and not wait_running(row) for row in siblings.values()),
            'Collect all conversation attempts, each with an intact report or declined, and reconcile their '
            'receipts and waits before retirement')


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


def yield_gaps(db, include=lambda saved: True):
    """Name each way a host that yields now would miss an event of an included worker."""
    missing = []
    closed = closed_sessions(db)
    included = [saved for saved in attempts(db) if include(saved)]
    names = {saved['attempt'] for saved in included}
    receipts = [row for row in pending(db) if row['kind'] in ('wait-finished', 'hold') and row['attempt'] in names]
    for saved in included:
        monitor = saved.get('monitor', {})
        live = ((saved.get('submitted') or saved.get('submission_requested')) and not saved.get('result')
                and (saved['runtime'], saved['native_session']) not in closed)
        if not live:
            continue
        if watching(saved):
            if process_stamp(monitor['pid']) != monitor['stamp']:
                missing.append(f"{saved['attempt']}: wait process lost")
            elif not monitor.get('notify'):
                missing.append(f"{saved['attempt']}: wait has no wake channel")
        elif not (monitor.get('state') == 'finished' and any(
                row['attempt'] == saved['attempt'] and row['kind'] == 'wait-finished'
                and row['payload'].get('token') == monitor.get('token') for row in receipts)):
            # Only the current wait's unconsumed receipt can still wake the host.
            missing.append(f"{saved['attempt']}: no armed wait")
    missing += [f"{row['attempt']}: receipt {row['id']} was not delivered" for row in receipts
                if row['payload'].get('delivery', {}).get('state') != 'sent']
    return missing


# A review or E2E report's own verdict; collection keeps the values the worker returned.
VERDICT_FIELDS = ('review_status', 'documentation_status', 'review_head', 'review_base', 'review_diff_base',
                  'e2e_status', 'e2e_head')


def report_verdict(report):
    """The verdict fields a report's front matter gives."""
    match = re.match(r'---\n(.*?)\n---(?:\n|$)', Path(report).read_text(errors='replace'), re.S)
    fields = {}
    for line in match.group(1).splitlines() if match else []:
        key, colon, value = line.partition(':')
        if colon and key.strip() in VERDICT_FIELDS:
            fields[key.strip()] = value.strip().strip('`"\'')
    return fields


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


def state_path(db):
    return str(Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve())


def ledger(db, saved):
    """Write a collected attempt's outcome to the machine-wide ledger; a failed write is reported, never fatal."""
    if not saved.get('result'):
        return None
    state = state_path(db)
    try:
        outcomes.put(f"{state}#{saved['attempt']}", outcomes.attempt_row(db, saved, state))
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


def record(db, data, refuse_changes=False):
    kind, attempt = data.get('kind'), data.get('attempt')
    require(text(attempt), 'Event requires attempt')
    # A read-only worktree is read before the write lock is taken; the result check and any refusal use that one read.
    now = observed_state(get(db, attempt)) if kind == 'result' and data.get('status') == 'completed' else None
    located = elsewhere = None
    if kind == 'startup' and text(data.get('native')):
        # A native record, and a read-only agent's other checkout, are read before the write lock is taken too.
        target = get(db, attempt)
        located = native_settings.working_directory(data['native'], target['runtime'], target['native_session'])
        # Only a Pi record names the child's own directory; Claude and Codex ones name their coordinator's.
        checkout = (located and target['runtime'] == 'pi' and 'worktree_baseline' in target
                    and checkout_root(located))
        if checkout and checkout != checkout_root(target['worktree']) \
                and checkout not in target.get('checkout_baselines', {}):
            elsewhere = checkout, worktree_state(checkout)
    # Herdr is asked before the write lock is taken too, so a Herdr that does not answer holds no lock. The
    # execution is read once, here, so the attempt keeps the route that decided whether to ask.
    placed = execution = None
    if kind == 'register':
        execution = read_object(data['execution']) if text(data.get('execution')) else None
        route = execution or {}
        # The resolver marks a Herdr route by its context; a skill route may name the Herdr skill with a plugin prefix.
        skill = str(route.get('executor_config', {}).get('skill', ''))
        if route.get('executor_context') == 'herdr' or skill.rpartition(':')[2] == 'herdr' or 'herdr' in data:
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
            for key in ('repo', 'worktree'):
                require(Path(data[key]).is_absolute() and Path(data[key]).is_dir(),
                        f'{key} must be an existing absolute directory')
                data[key] = str(Path(data[key]).resolve())
            require(type(data.get('owned')) is bool, 'Registration requires explicit owned boolean')
            require(execution, 'Registration requires execution, the resolved execution file')
            settings = execution.get('settings')
            require(isinstance(settings, dict) and not set(settings) - {'model', 'effort'}
                    and all(text(value) for value in settings.values()), 'Invalid resolved settings')
            require(text(execution.get('agent')) and execution.get('job') in DEFINITIONS['jobs'],
                    'Resolved execution requires its agent and a known job (resolve with --job)')
            job = DEFINITIONS['jobs'][execution['job']]
            require(job['agent'] == execution['agent'], f'Job {execution["job"]} is done by {job["agent"]}')
            packet = Path(data.get('packet') or '')
            require(packet.is_absolute() and packet.is_file(),
                    'Registration requires packet: the absolute path of the assignment text sent to the worker')
            missing = agent_defs.missing_inputs(job, packet.read_text(errors='replace'))
            require(not missing, f'The {execution["job"]} assignment lacks {", ".join(missing)}; write each as '
                                 f'`name: value` (see {job["path"]})')
            runtime = execution.get('target_runtime') or execution.get('harness')
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
            if placed:
                saved['herdr'] = placed
            if DEFINITIONS['agents'][execution['agent']]['writes'] == 'none':
                saved['worktree_baseline'] = worktree_state(data['worktree'])  # Not in the event: it can be large.
            decision = linked_decision(execution)
            if decision:
                saved['decision'] = decision
            if os.environ.get('HERDR_PANE_ID'):
                # The registering host's pane; the yield hooks hold only that host for this worker.
                saved['coordinator'] = os.environ['HERDR_PANE_ID']
            session = next((os.environ[name] for name in SESSION_VARIABLES if os.environ.get(name)), None)
            if session:
                saved['session'] = session  # The registering host session, which decides before the pane.
            if 'journal' in data:
                require(text(data['journal']) and Path(data['journal']).is_absolute(),
                        'journal must be an absolute decisions journal path')
            if 'repairs' in data:
                require(text(data['repairs']), 'repairs names the attempt whose work this one repairs')
                require(get(db, data['repairs']).get('result'), 'A repaired attempt must be collected first')
            if execution.get('backend'):
                # A local backend's slot is shared by every effort on this machine.
                capacity.acquire(execution['backend'], execution.get('capacity') or {},
                                 state_path(db), attempt)
        else:
            saved = get(db, attempt)
            require((saved['runtime'], saved['native_session']) not in closed_sessions(db)
                    or kind in ('ruling', 'accepted', 'declined'), 'Native conversation already closed')
            if kind == 'startup':
                observed = data.get('observed')
                require(isinstance(observed, dict) and not set(observed) - {'runtime', 'model', 'effort', *SCOPE}
                        and all(text(value) or isinstance(value, list) and value for value in observed.values()),
                        'Invalid native settings')
                require(isinstance(data.get('evidence'), list) and data['evidence']
                        and all(text(x) for x in data['evidence']),
                        'Startup requires native evidence')
                aliases = data.get('model_alias_evidence', [])
                require(isinstance(aliases, list) and all(text(x) for x in aliases), 'Invalid alias evidence')
                require(not aliases or text(observed.get('model')) and text(saved['requested'].get('model')),
                        'Alias evidence requires requested and observed model IDs')
                saved['observed'], saved['startup_evidence'] = observed, data
                if located:
                    # A Claude or Codex native child records its coordinator's directory, so this only warns.
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
                    require(not gaps(db, saved, 'work') and saved.get('submitted'),
                            'Completion requires verified settings, accepted prompt and resolved design hold')
                require(not saved.get('result'), 'Result already collected; use a new attempt for further work')
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
                require_returned(saved)
                saved['accepted'] = data
            elif kind == 'declined':
                require(saved.get('result', {}).get('status') == 'completed',
                        'Declining requires a collected completed result')
                require(not saved.get('accepted'), 'Attempt already accepted')
                require(not saved.get('declined'), 'Attempt already declined')
                require(text(data.get('reason')), 'Declining requires a reason')
                saved['declined'] = data
            elif kind in ('retain', 'closed'):
                declining = kind == 'closed' and (data.get('acceptance') or {}).get('decision') == 'declined'
                if kind == 'closed':
                    final_retirement(db, saved, declining)
                receipts = pending(db, attempt)
                require(saved.get('result') and not watching(saved)
                        and (not receipts or kind == 'retain' and all(r['kind'] == 'hold' for r in receipts)),
                        'Collect result and acknowledge receipts before retaining or closing')
                require(report_intact(saved) or saved.get('declined') or declining,
                        'Retain an intact report before retaining or closing, or decline the attempt')
                if kind == 'closed':
                    require(saved['owned'], 'Existing user worker is not a cleanup target')
                    settle(saved, data.get('acceptance'))
                    saved['closed'] = data
                else:
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
    unrecorded = ledger(db, saved) if kind in ('result', 'accepted', 'declined', 'closed') else None
    if kind in ('result', 'closed') and saved.get('execution_snapshot', {}).get('backend'):
        try:
            capacity.release(state_path(db), attempt)
        except (OSError, ValueError, sqlite3.Error):
            pass  # A lease of a finished attempt is reclaimed by the next acquisition.
    # A directory held only to what can be read of it is named even when nothing changed.
    partly = now and [*partly_checked(now['worktree'] or {}),
                      *(os.path.join(checkout, name) for checkout, state in now['checkouts'].items()
                        for name in partly_checked(state or {}))]
    return {'event_id': event_id, 'attempt': attempt, 'verified': saved['verified'],
            **({'partly_checked': partly} if partly else {}),
            **({'journal': journal} if journal else {}), **({'repairs': repaired} if repaired else {}),
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


def herdr_prompt_flags(argv):
    """The options after the target and text of a Herdr `agent prompt` argv, or None for any other command."""
    if (not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv)
            or Path(argv[0]).name != 'herdr'):
        return None  # Not literal text: the wait's own validation reports it.
    # The command follows any global options (--session, --machine, --remote ...) and their values.
    index = next((index for index in range(1, len(argv) - 1) if argv[index:index + 2] == ['agent', 'prompt']), None)
    return None if index is None else argv[index + 4:]


def submit(db, state, attempt, request):
    """Verify native settings and arm a durable wait before invoking productive submission once."""
    flags = herdr_prompt_flags(request.get('argv'))
    # Plain --wait returns when the worker's turn settles, which would hold the coordinator for the whole job.
    require(flags is None or '--wait' in flags and ('--until=working' in flags or any(
        pair == ('--until', 'working') for pair in zip(flags, flags[1:]))),
            'A Herdr submit returns once the worker is working: use `herdr agent prompt <worker> <assignment> '
            '--wait --until working --until blocked --timeout 45000`; once its receipt is reconciled, arm '
            '`watch` with `herdr agent wait <worker>` and notify for its completion')
    saved = get(db, attempt)
    observation = native_settings.observe(request['native'], saved['runtime'],
                                         saved['native_session'], saved['worktree'])
    directory = state.parent / (state.name + '.native')
    directory.mkdir(mode=0o700, exist_ok=True)
    snapshot = directory / (uuid.uuid4().hex + '.json')
    with os.fdopen(os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump(observation, stream, indent=2)
        stream.write('\n')
    record(db, dict(kind='startup', attempt=attempt, observed=observation['observed'],
                    evidence=[str(snapshot)], native_observation=observation,
                    model_alias_evidence=request.get('model_alias_evidence', [])))
    return watch(db, state, attempt, request, productive=True)


def herdr(session, *args, timeout=15):
    try:
        process = subprocess.run(['herdr', '--session', session, *args], capture_output=True,
                                 text=True, timeout=timeout)
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
    # HERDR_TAB_ID can be stale; Herdr places the coordinator's pane now, and a coordinator outside this session
    # has no tab here to share.
    caller = caller_pane(session, 'registration', LOOKUP_SECONDS) or {}
    require(caller.get('tab_id') != placed['tab_id'],
            f"One worker per tab: {placed['tab_id']} is the coordinator's own tab; {TAB_RECIPE}")
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
    require(report_intact(saved), 'Retain an intact report before retirement')
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
    final_retirement(db, saved)
    settle(dict(saved), request.get('acceptance'))  # Refuse before the pane closes, not after.
    previous = saved.get('retirement_requested')
    if previous:
        require(previous['session'] == session and previous['pane'] == pane,
                'Reconcile the original retirement target')
        require(process_stamp(previous['pid']) != previous['stamp'], 'Retirement already owned by a live process')
    code, response = herdr(session, 'agent', 'get', pane)
    if code == 0:
        agent = response['result']['agent']
        require(agent.get('pane_id') == pane and agent.get('agent') == saved['runtime'],
                'Native pane no longer holds the registered runtime')
        require(saved['worker'] in (pane, agent.get('name'), f"{session}/{pane}/{agent.get('name')}"),
                'Native target does not match the registered worker handle')
        identity = agent.get('agent_session', {})
        require(isinstance(identity, dict), 'Invalid native conversation identity')
        source = saved.get('startup_evidence', {}).get('native_observation', {}).get('source')
        require(identity.get('value') == saved['native_session'] or identity.get('kind') == 'path'
                and source and identity.get('value') == source, 'Native conversation no longer matches the attempt')
        require(agent.get('agent_status') in ('idle', 'done'), 'Native worker is active or blocked; retain it for inspection')
        db.execute('BEGIN IMMEDIATE')
        with db:
            saved = get(db, attempt)
            final_retirement(db, saved)
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
    else:
        require(previous, 'Inspect the missing native worker before recording closure')
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


def collect(db, state, attempt, request):
    """Persist a checked report and collect its receipts with a required final disposition."""
    disposition = request.get('disposition', {})
    require(isinstance(disposition, dict), 'Collection disposition must be an object')
    require(disposition.get('kind') in ('retire', 'retain'), 'Collection requires retirement or explicit retention')
    saved = get(db, attempt)
    if disposition['kind'] == 'retire':
        require(saved['owned'], 'Existing user worker is not a cleanup target')
    else:
        require(text(disposition.get('reason')) and text(disposition.get('next_use'))
                and isinstance(disposition.get('evidence'), list) and disposition['evidence']
                and all(text(x) for x in disposition['evidence']), 'Retention requires reason, next_use and evidence')
    previous = saved.get('result')
    if previous:
        require(all(previous.get(key) == request.get(key) for key in ('status', 'head', 'report'))
                and report_intact(saved), 'Resume collection with the same intact report and checked result')
        result = {'attempt': attempt, 'already_collected': True}
    else:
        result = record(db, dict(request, kind='result', attempt=attempt), refuse_changes=True)
    with db:
        db.execute("UPDATE events SET acknowledged = 1 WHERE attempt = ? AND kind IN ('wait-finished', 'result')", (attempt,))
    if disposition['kind'] == 'retire':
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
                   'retirement_requested') if key in saved}
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
                                           'status', 'ack', 'gate', 'attribute'))
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
