#!/usr/bin/env python3
"""Stop hook: keep the host working while a lifecycle worker could finish without waking it.

Reads every run's workers.sqlite under the main checkout's .scratch/*/reports/ that
changed in the last day, without writing to it, and applies the lifecycle's yield
gate. Every session is also held for each Herdr launch it logged (hooks/herdr_launch.py)
into a ticket worktree that no attempt accounts for and it did not dismiss; one without
ZLOOP_WORKER, the marker every worker is launched with, also for each other such live
agent of its Herdr workspace. The session bound to coordinate an effort (next_tickets.py
--coordinate; without one, the pane that registered the attempt) is held for each collected
attempt, neither retained nor retired, whose Herdr tab is still open, and for an effort whose
review report (before it records one, the binding) records autofix: true and no blocker, with
every ticket done and delivery failing, until it is finished (branch deleted, reviewed head in
main, E2E passed at a head in main), unless a live worker of the effort holds a running wait that will wake the
host. A
check that cannot run, and a launch log line that is unreadable or malformed, are named gaps,
never a pass. `claude` and `codex` speak their shared Stop
hook protocol; `check <cwd> [session]` prints the gaps and the hold's message as JSON for other hosts. Set
ZLOOP_YIELD_HOOK=off to disable the check.
"""
import calendar
import importlib.util
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
RECENT_SECONDS = 24 * 3600
GUIDE = ROOT / 'runtime/workers/wait.md'
PREPARE = ROOT / 'runtime/workers/prepare.md'
COLLECT = ROOT / 'runtime/workers/collect.md'
HANDOFF = ROOT / 'skills/dispatch/handoff.md'
# hooks/herdr_launch.py appends each `herdr agent start` here, one JSON object per line.
LAUNCHES = '.scratch/herdr-launches.jsonl'
# scripts/next_tickets.py --coordinate records here, in an effort's reports/, the session that coordinates it.
BINDING = 'coordinator.json'


def lifecycle():
    return script('worker_lifecycle')


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f'scripts/{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main_checkout(cwd):
    """The main checkout holding cwd, None outside any repository; raises when git cannot tell."""
    result = subprocess.run(['git', '-C', str(cwd), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                            capture_output=True, text=True, timeout=5, env=dict(os.environ, LC_ALL='C'))
    if result.returncode == 0:
        return Path(result.stdout.strip()).parent
    if 'not a git repository' in result.stderr:
        return None
    raise RuntimeError(f'git rev-parse exited {result.returncode}: {result.stderr.strip()}')


def gaps(cwd, session=None):
    """(worker gaps, the coordinator's own gaps: stale tabs and undelivered efforts, each saying how to clear it)."""
    root = main_checkout(cwd)
    if root is None:
        return [], []
    cwd, pane = Path(cwd).resolve(), os.environ.get('HERDR_PANE_ID')
    module, missing = None, []
    for state in sorted(root.glob('.scratch/*/reports/workers.sqlite')):
        if time.time() - state.stat().st_mtime > RECENT_SECONDS:
            continue
        module = module or lifecycle()
        db = sqlite3.connect(f'{state.as_uri()}?mode=ro', uri=True, timeout=5)
        try:
            # A session inside a worker's own worktree is that worker, not its coordinator.
            worker = any(Path(saved['worktree']) != root and cwd.is_relative_to(saved['worktree'])
                         for saved in module.attempts(db))

            def coordinated(saved):
                if pane and saved.get('coordinator'):
                    return saved['coordinator'] == pane
                return not worker
            missing += [f'{state}: {gap}' for gap in module.yield_gaps(db, coordinated)]
        finally:
            db.close()
    # Every session answers for its own launches; a worker (its launch set ZLOOP_WORKER) not for its siblings.
    return (missing + unregistered(root, pane, session, siblings=not os.environ.get('ZLOOP_WORKER')),
            stale_tabs(root, pane, session) + undelivered(root, pane, session))


def stale_tabs(root, pane, session):
    """Name each collected attempt, neither retained nor retired, whose recorded Herdr tab its session still lists, for
    the session bound to coordinate its effort or, with none bound, the pane that registered it, however old the run."""
    module, listed, missing = None, {}, []
    for state in sorted(root.glob('.scratch/*/reports/workers.sqlite')):
        module = module or lifecycle()
        # An unreadable binding decides nothing here (undelivered names it); the registering pane does.
        binding, mine = read_binding(state.with_name(BINDING), pane, session)
        bound = mine if binding else None
        db = sqlite3.connect(f'{state.as_uri()}?mode=ro', uri=True, timeout=5)
        try:
            saved_attempts = module.attempts(db)
        finally:
            db.close()
        for saved in saved_attempts:
            herdr = saved.get('herdr') or {}
            mine = bound if bound is not None else bool(pane) and saved.get('coordinator') == pane
            # A retired attempt's tab was confirmed gone before its closure was recorded.
            if not (herdr.get('tab_id') and saved.get('result') and not saved.get('retained')
                    and not saved.get('closed') and mine):
                continue
            where = (herdr['session'], herdr['workspace_id'])
            if where not in listed:
                try:
                    listed[where] = herdr_tabs(*where)
                except Exception as error:  # A check that cannot run is a named gap, never a pass.
                    listed[where] = set()
                    missing.append(f'could not check worker tabs: {error}')
            if herdr['tab_id'] in listed[where]:
                missing.append(f"{state}: {saved['attempt']}: Herdr tab {herdr['tab_id']} is still open after "
                               f'collection; retire the worker or record its retention ({COLLECT})')
    return missing


def herdr_tabs(session, workspace):
    """The IDs of the tabs a Herdr session lists in a workspace; none when the workspace is closed or the session
    stopped, which close its tabs."""
    result = subprocess.run(['herdr', '--session', session, 'tab', 'list', '--workspace', workspace],
                            capture_output=True, text=True, timeout=5)
    if result.returncode and herdr_error(result) in ('workspace_not_found', 'server_not_running'):
        return set()
    if result.returncode:
        raise RuntimeError(f'herdr tab list exited {result.returncode}: {(result.stderr or result.stdout).strip()}')
    return {tab['tab_id'] for tab in json.loads(result.stdout)['result']['tabs']}


def herdr_error(result):
    """The error code a failed Herdr command reported, else None."""
    try:
        return json.loads(result.stderr or result.stdout)['error']['code']
    except (ValueError, TypeError, KeyError):
        return None


def undelivered(root, pane, session):
    """Name each effort this session was bound to coordinate (next_tickets.py --coordinate) whose delivery fails."""
    board, tickets, missing = None, None, []
    for path in sorted(root.glob(f'.scratch/*/reports/{BINDING}')):
        slug = path.parent.parent.name
        binding, mine = read_binding(path, pane, session)
        if not mine:
            continue
        if binding is None:
            missing.append(f'{path}: the coordinator binding cannot be read; rebind with '
                           f'`python3 {ROOT}/scripts/next_tickets.py --repo {root} {slug} --coordinate`')
            continue
        try:
            if board is None:
                module = script('next_tickets')
                module.ROOT, module.ISSUES = root, root / '.scratch'
                board, tickets = module, module.load()  # load() exits on a board it refuses.
            review = board.review_front_matter(slug) or {}
            # Only authorized repairs owe delivery, once every ticket is done; a recorded blocker is why it stops here.
            # Dispatch start records the authorization until the review report records its own (not a placeholder).
            autofix = review.get('autofix')
            if autofix not in ('true', 'false'):
                autofix = 'true' if binding.get('autofix') else 'false'
            if (autofix != 'true' or str(review.get('blocker') or 'none') != 'none'
                    or any(fm['status'] != 'done' for name, fm in tickets.items() if board.spec_of(name) == slug)
                    or board.finished(slug)):
                continue
            if awaited(path.with_name('workers.sqlite')):
                continue
            problems = board.delivery_problems(slug)
        except (Exception, SystemExit) as error:  # A check that cannot run is a named gap, never a pass.
            missing.append(f'could not check delivery of {slug}: {error}')
            continue
        if problems:
            missing.append(f"{slug}: delivery incomplete under autofix: {'; '.join(problems)}. Continue the autofix "
                           f'handoff ({HANDOFF}) until `python3 {ROOT}/scripts/next_tickets.py --repo {root} {slug} '
                           f'--delivery` passes, or record `blocker: <reason>` in {path.with_name("spec-review.md")}')
    return missing


def awaited(state):
    """Whether a live worker of the run holds a running wait with a wake channel: the host is told when it settles, so
    its delivery is not stuck."""
    if not state.is_file():
        return False
    module = lifecycle()
    db = sqlite3.connect(f'{state.as_uri()}?mode=ro', uri=True, timeout=5)
    try:
        closed, saved_attempts = module.closed_sessions(db), module.attempts(db)
    finally:
        db.close()
    return any((saved.get('submitted') or saved.get('submission_requested')) and not saved.get('result')
               and (saved['runtime'], saved['native_session']) not in closed and module.watching(saved)
               and module.process_stamp(saved['monitor']['pid']) == saved['monitor']['stamp']
               and saved['monitor'].get('notify') for saved in saved_attempts)


def read_binding(path, pane, session):
    """(binding, mine) for a dispatch binding file: the binding, or None when it is missing or cannot be read, and
    whether it names this session. An unreadable one names it only when its raw text holds this pane or host session,
    since only such a session could be its coordinator."""
    try:
        text = path.read_text(errors='replace')
    except OSError:  # Missing, or not a file.
        return None, False
    try:
        binding = json.loads(text)
    except ValueError:
        binding = None
    if (isinstance(binding, dict) and (binding.get('pane') is None or isinstance(binding['pane'], str))
            and isinstance(binding.get('sessions'), list)
            and all(isinstance(known, str) for known in binding['sessions'])):
        return binding, coordinates(binding, pane, session)
    # A whole ID only, bounded by characters no pane or session ID holds, so w1:p1 is not found in w1:p12.
    return None, any(value and re.search(rf'(?<![\w:.-]){re.escape(value)}(?![\w:.-])', text)
                     for value in (pane, session))


def coordinates(binding, pane, session):
    """Whether a dispatch binding ({pane, sessions}) names this session: as for a launch, by host session alone when
    both sides know one."""
    return any(same_coordinator({'pane': binding['pane'], 'session': known}, pane, session)
               for known in binding['sessions'] or [None])


def herdr_agents():
    """The live agents the session's Herdr server reports."""
    result = subprocess.run(['herdr', 'agent', 'list'], capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise RuntimeError(f'herdr agent list exited {result.returncode}: {(result.stderr or result.stdout).strip()}')
    return json.loads(result.stdout)['result']['agents']


def same_coordinator(theirs, pane, session):
    """Whether a recorded coordinator ({pane, session}) is this session: by host session alone when both sides know
    one, since a Codex session's HERDR_PANE_ID can come from another pane (its shared app-server's) and a restarted
    Herdr may reuse a pane ID; else the pane decides."""
    if theirs['session'] and session:
        return theirs['session'] == session
    return bool(pane) and theirs['pane'] == pane


def handles(worker):
    """What a registered worker matches: itself, and the pane and name of a `<session>/<pane>/<name>` handle."""
    parts = worker.rsplit('/', 2)
    return {worker, *parts[1:]} if len(parts) == 3 else {worker}


def clears(saved, launch):
    """Whether an attempt accounts for a logged launch: it names the launch's pane or agent, and the host session
    recorded at registration is the one that launched it when both know one; else the coordinator recorded at
    registration is the pane that launched it, and only a launch made outside Herdr can instead be cleared by an
    attempt with no recorded coordinator, registered no earlier."""
    if not handles(saved['worker']) & {launch['agent'], launch['pane']}:
        return False
    if saved.get('session') and launch['coordinator']['session']:
        return saved['session'] == launch['coordinator']['session']
    if saved.get('coordinator') or launch['coordinator']['pane']:
        return saved.get('coordinator') == launch['coordinator']['pane']
    return (saved['registered_at'] or 0) >= calendar.timegm(time.strptime(launch['time'], '%Y-%m-%dT%H:%M:%SZ'))


def readable(record):
    """Whether a launch log line is a well-formed launch or dismissal; any other line is named, never matched."""
    def text(value, optional=True):
        return isinstance(value, str) or optional and value is None

    def identity(value):
        return isinstance(value, dict) and set(value) == {'pane', 'session'} and all(map(text, value.values()))
    if not isinstance(record, dict):
        return False
    if 'dismissed' in record:
        target = record['dismissed']
        return (isinstance(target, dict) and len(target) == 1 and set(target) <= {'pane', 'agent'}
                and all(text(value, False) for value in target.values()) and identity(record.get('by')))
    try:
        time.strptime(record.get('time'), '%Y-%m-%dT%H:%M:%SZ')
    except (TypeError, ValueError):
        return False
    return ({'agent', 'pane'} <= set(record) and text(record['agent']) and text(record['pane'])
            and identity(record.get('coordinator')) and text(record.get('cwd')))


def unregistered(root, pane, session, siblings=True):
    """Name each Herdr worker that no attempt in any run and no dismissal of this coordinator accounts for: each
    launch it logged into a ticket worktree or an unknown cwd (an attempt of another host session never accounts for
    it when both know one), with `siblings` each other live agent in a ticket worktree of its Herdr workspace; and
    each unreadable log line."""
    module, attempts = None, []
    for state in root.glob('.scratch/*/reports/workers.sqlite'):
        module = module or lifecycle()
        db = sqlite3.connect(f'{state.as_uri()}?mode=ro', uri=True, timeout=5)
        try:
            registered = {row[0]: json.loads(row[1])['recorded_at']
                          for row in db.execute("SELECT attempt, payload FROM events WHERE kind = 'register'")}
            attempts += [dict(saved, registered_at=registered.get(saved['attempt'])) for saved in module.attempts(db)]
        finally:
            db.close()
    workers = {handle for saved in attempts for handle in handles(saved['worker'])}
    log, worktrees = root / LAUNCHES, (root / '.worktrees').resolve()
    records, unreadable = [], []
    for number, line in enumerate(log.read_text().splitlines() if log.is_file() else [], 1):
        try:
            record = json.loads(line)
        except ValueError:
            record = None
        if readable(record):
            records.append(record)
        else:  # Named, and ignored for matching, so one bad line hides no other gap.
            unreadable.append(f'{log}: launch log line {number} is unreadable; fix or remove it')
    launches = []
    for record in records:
        if 'dismissed' in record:  # Accounts for its coordinator's launches of that pane or name logged before it.
            launches = [launch for launch in launches
                        if any(launch.get(key) != value for key, value in record['dismissed'].items())
                        or not same_coordinator(launch['coordinator'], record['by']['pane'], record['by']['session'])]
        else:
            launches.append(record)
    launches = [launch for launch in launches if same_coordinator(launch['coordinator'], pane, session)
                and not any(clears(saved, launch) for saved in attempts)
                and ('cwd' not in launch or Path(launch['cwd']).resolve().is_relative_to(worktrees))]
    workers |= {handle for launch in launches for handle in (launch['agent'], launch['pane'])}  # Named once.
    workers |= {value for record in records if 'dismissed' in record  # This coordinator's dismissals cover live agents.
                and same_coordinator(record['by'], pane, session) for value in record['dismissed'].values()}
    workspace, failed = os.environ.get('HERDR_WORKSPACE_ID'), []
    try:
        agents = herdr_agents() if siblings and workspace and worktrees.is_dir() else []  # No worktrees, no workers.
    except Exception as error:  # A check that cannot run is a named gap, never a pass.
        agents, failed = [], [f'could not check Herdr workers: {error}']
    live = [agent for agent in agents
            if agent.get('workspace_id') == workspace and agent['pane_id'] != pane and agent.get('cwd')
            and Path(agent['cwd']).resolve().is_relative_to(worktrees)
            and not workers & {agent.get('name'), agent['pane_id']}]
    return ([f"{log}: {launch['agent']} (pane {launch['pane']}): launched without a registered attempt"
             for launch in launches]
            + [f"Herdr agent {agent.get('name')} (pane {agent['pane_id']}) runs in {agent['cwd']} "
               'without a registered attempt' for agent in live] + unreadable + failed)


def checked(cwd, session=None):
    """The gaps as gaps() splits them, or a worker gap naming why the check could not run: a broken check never passes
    silently."""
    try:
        return gaps(cwd, session)
    except Exception as error:
        return [f'could not check workers: {error}'], []


def reason(workers, own, session=None):
    """The hold's message: every gap, the worker advice only for a worker gap that is not a failed check, and the retry
    advice for a failed check, which registering or dismissing cannot clear."""
    message = 'Stopping now leaves these gaps:\n' + ''.join(f'- {gap}\n' for gap in workers + own)
    failed = [gap for gap in workers + own if gap.startswith('could not check')]
    advice = []
    if [gap for gap in workers if gap not in failed]:
        dismiss = f"python3 {ROOT}/hooks/herdr_launch.py dismiss --pane <pane>{f' --session {session}' if session else ''}"
        advice.append(f'Register each launched worker, or dismiss a launch that needs none with `{dismiss} --reason <why>` '
                      + f'({PREPARE}). '
                      + 'Arm a notified wait, recover the receipt or collect the '
                      + 'result (for a worker that is gone, a failed or cancelled result whose report says what was lost) '
                      + f'before stopping ({GUIDE}).')
    if failed:
        advice.append('A check that could not run clears when it runs again: retry the stop once Herdr (or git) is '
                      'reachable, or run with `ZLOOP_YIELD_HOOK=off` for this stop.')
    return message + ' '.join(advice)


def main():
    if os.environ.get('ZLOOP_YIELD_HOOK') == 'off':
        return 0
    if sys.argv[1:2] == ['check'] and len(sys.argv) in (3, 4):
        workers, own = checked(Path(sys.argv[2]), *sys.argv[3:])
        print(json.dumps({'missing': workers + own, 'reason': reason(workers, own, *sys.argv[3:])}))
        return 0
    if sys.argv[1:] not in (['claude'], ['codex']):
        sys.exit('usage: yield_gate.py claude|codex < stop-hook-input, or yield_gate.py check <cwd> [session]')
    payload = json.load(sys.stdin)
    if payload.get('stop_hook_active'):
        return 0
    workers, own = checked(Path(payload.get('cwd') or os.getcwd()), payload.get('session_id'))
    if workers or own:
        print(json.dumps({'decision': 'block', 'reason': reason(workers, own, payload.get('session_id'))}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
