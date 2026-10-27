#!/usr/bin/env python3
"""Stop hook: keep the host working while a lifecycle worker could finish without waking it.

Reads every run's workers.sqlite under the main checkout's .scratch/*/reports/ that
changed in the last day, without writing to it, and applies the lifecycle's yield
gate to the workers this session registered (by host session, else pane), or, for one that
recorded neither, to the session bound to coordinate its effort (with none bound, to every
session). Sessions share the checkout: another session's workers and launches never hold this
one, and a row or state that cannot be read is named to the sessions it could hide a gap
from. Every session is also held for each Herdr launch it logged (hooks/herdr_launch.py)
into a ticket worktree that no attempt accounts for and it did not dismiss; one without
ZLOOP_WORKER, the marker every worker is launched with, also for each other such live
agent of its Herdr workspace that no other session logged. The session bound to coordinate an effort (next_tickets.py
--coordinate; without one, the pane that registered the attempt) is held for each collected
attempt, neither retained nor retired, whose Herdr tab is still open, and for an effort whose
review report (before it records one, the binding) records autofix: true and no blocker, with
every ticket done and delivery failing, until it is finished (branch deleted, reviewed head in
main or on the remote's main, E2E passed at a head in main). A session is also held while a
confirmed autopilot run card (.scratch/<slug>/autopilot.md) naming it lacks one terminal state or its
evidence. Neither the delivery hold nor the card hold applies while a live worker of the effort holds a running wait that will wake the
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
# The states skills/autopilot/run-card.md lets a run end in.
TERMINAL_STATES = ('delivered', 'delivered-local', 'merged', 'waiting-on-user', 'paused-allowance', 'paused-by-user',
                   'blocked')


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
        # Sessions share the folder: a worker holds only the session that registered it, by host session before pane;
        # one registered with neither recorded holds the session bound to coordinate its effort, or, with none bound,
        # every session that is not a worker, since nothing tells its owner apart.
        binding, bound = read_binding(state.with_name(BINDING), pane, session)
        unbound = binding is None and not state.with_name(BINDING).exists()
        recent = time.time() - state.stat().st_mtime <= RECENT_SECONDS
        try:
            saved_attempts, malformed, _ = read_state(state)
        except Exception as error:
            missing += [f'{state}: cannot be read ({error}); fix or remove it'] if unreadable_holds(
                state, pane, session, bound, recent) else []
            continue
        missing += [f'{state}: attempt row {key} is malformed; fix or remove it' for key, saved in malformed
                    if bound or (isinstance(saved, dict) and attributed(saved) and owns(saved, pane, session))]
        if not recent:
            continue
        module = module or lifecycle()
        # A session inside a worker's own worktree is that worker, not its coordinator.
        worker = any(Path(saved['worktree']) != root and cwd.is_relative_to(saved['worktree'])
                     for saved in saved_attempts if 'worktree' in saved)

        def coordinated(saved):
            # A session launched as a worker (ZLOOP_WORKER) answers for no worker it did not register.
            fallback = bound or unbound and not os.environ.get('ZLOOP_WORKER')
            return owns(saved, pane, session) if attributed(saved) else fallback and not worker
        db = sqlite3.connect(f'{state.as_uri()}?mode=ro', uri=True, timeout=5)
        try:
            missing += [f'{state}: {gap}' for gap in module.yield_gaps(db, coordinated, saved_attempts)]
        except Exception as error:  # Named to whoever it could hide a gap from: its coordinator and its workers' own.
            if any(coordinated(saved) for saved in saved_attempts) or unreadable_holds(state, pane, session, bound,
                                                                                       recent):
                missing.append(f'{state}: cannot be read ({error}); fix or remove it')
        finally:
            db.close()
    # Every session answers for its own launches; a worker (its launch set ZLOOP_WORKER) not for its siblings.
    return (missing + unregistered(root, pane, session, siblings=not os.environ.get('ZLOOP_WORKER')),
            stale_tabs(root, pane, session) + undelivered(root, pane, session) + unfinished_runs(root, session))


def unreadable_holds(state, pane, session, bound, recent):
    """Whether a run state that cannot be read is named to this session: it hides its owners' gaps, and a worker that
    recorded no owner may be among them, so it is named to every session that is not a worker while it is recent, and
    always to the session bound to its effort and each pane or host session its bytes still record."""
    try:
        raw = state.read_bytes().decode(errors='replace')
    except OSError:
        raw = ''
    if bound or any(value and re.search(rf'(?<![\w:.-]){re.escape(value)}(?![\w:.-])', raw) for value in (pane, session)):
        return True
    return recent and not os.environ.get('ZLOOP_WORKER')


def read_state(state):
    """(well-formed attempts, (row id, data) of each malformed row, each attempt's registration time) of a run state.
    Raises when the state cannot be read."""
    db = sqlite3.connect(f'{state.as_uri()}?mode=ro', uri=True, timeout=5)
    try:
        rows = db.execute('SELECT id, data FROM attempts ORDER BY id').fetchall()
        events = db.execute("SELECT attempt, payload FROM events WHERE kind = 'register'").fetchall()
    finally:
        db.close()
    saved_attempts, malformed, registered = [], [], {}
    for key, data in rows:
        try:
            saved = json.loads(data)
        except ValueError:
            saved = None
        if well_formed(saved):
            saved_attempts.append(saved)
        else:
            malformed.append((key, saved))
    for attempt, payload in events:
        try:
            registered[attempt] = json.loads(payload)['recorded_at']
        except (ValueError, TypeError, KeyError):
            pass
    return saved_attempts, malformed, registered


def well_formed(saved):
    """Whether an attempt row has the shape the lifecycle writes, as far as the stop checks read it."""
    text = lambda value: isinstance(value, str) and value != ''
    if not (isinstance(saved, dict) and all(text(saved.get(key)) for key in ('attempt', 'worker', 'runtime',
                                                                              'native_session'))):
        return False
    if 'worktree' in saved and not (text(saved['worktree']) and Path(saved['worktree']).is_absolute()):
        return False
    if any(key in saved and saved[key] is not None and not text(saved[key]) for key in ('coordinator', 'session')):
        return False
    if 'sessions' in saved and not (isinstance(saved['sessions'], list) and all(map(text, saved['sessions']))):
        return False
    herdr, monitor = saved.get('herdr'), saved.get('monitor')
    if herdr is not None and not (isinstance(herdr, dict)
                                  and all(text(herdr.get(key)) for key in ('session', 'workspace_id', 'tab_id'))):
        return False
    if monitor is not None and not (isinstance(monitor, dict) and (
            monitor.get('state') != 'running' or (isinstance(monitor.get('pid'), int) and 'stamp' in monitor))):
        return False
    return True


def attributed(saved):
    """Whether an attempt recorded the session or pane that registered it."""
    return bool(saved.get('session') or saved.get('coordinator'))


def owns(saved, pane, session):
    """Whether this session registered the attempt: by host session before pane. A host started inside another's shell
    exports the outer host's session too, so each exported session counts."""
    return any(same_coordinator({'pane': saved.get('coordinator'), 'session': known}, pane, session)
               for known in recorded_sessions(saved) or [None])


def recorded_sessions(saved):
    """The host sessions an attempt's registrar exported: its session list, else its one session."""
    listed = saved.get('sessions') if isinstance(saved.get('sessions'), list) else []
    return [value for value in listed if isinstance(value, str)] or (
        [saved['session']] if isinstance(saved.get('session'), str) else [])


def card_state(path):
    """The `key: value` lines of a run card's State section, '' for a blank or placeholder (`<...>`) value; {} when the
    card cannot be read or has no State."""
    try:
        text = path.read_text(errors='replace')
    except OSError:
        return {}
    state = {}
    for line in text.partition('\n## State\n')[2].splitlines():
        key, colon, value = line.partition(':')
        if colon and re.fullmatch(r'[a-z-]+', key):
            value = value.strip()
            state[key] = '' if value.startswith('<') else value
    return state


def unfinished_runs(root, session):
    """Name each confirmed autopilot run card of this host session that lacks one terminal state or its evidence,
    unless a live worker of the card's effort holds a running wait that will wake the host."""
    missing = []
    for path in sorted(root.glob('.scratch/*/autopilot.md')):
        state = card_state(path)
        if (session and state.get('session') == session and state.get('confirmed', 'no') not in ('', 'no')
                and (state.get('terminal') not in TERMINAL_STATES or not state.get('evidence'))
                and not awaited(path.with_name('reports') / 'workers.sqlite')):
            missing.append(f"{path}: this session's autopilot run has no terminal state or no evidence; record "
                           f"`terminal:` as exactly one of {', '.join(f'`{name}`' for name in TERMINAL_STATES)} and "
                           "`evidence:` in the card's State")
    return missing


def stale_tabs(root, pane, session):
    """Name each collected attempt, neither retained nor retired, whose recorded Herdr tab its session still lists, for
    the session bound to coordinate its effort or, with none bound, the pane that registered it, however old the run."""
    listed, missing = {}, []
    for state in sorted(root.glob('.scratch/*/reports/workers.sqlite')):
        # An unreadable binding decides nothing here (undelivered names it); the registering pane does.
        binding, mine = read_binding(state.with_name(BINDING), pane, session)
        bound = mine if binding else None
        try:
            saved_attempts = read_state(state)[0]
        except Exception:  # gaps() names a state that cannot be read to its effort's coordinator.
            continue
        for saved in saved_attempts:
            herdr = saved.get('herdr') or {}
            mine = bound if bound is not None else attributed(saved) and owns(saved, pane, session)
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
    try:
        saved_attempts = read_state(state)[0]
    except Exception:  # A state that cannot be read shows no running wait.
        return False
    closed = {(saved['runtime'], saved['native_session']) for saved in saved_attempts if saved.get('closed')}
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
    if recorded_sessions(saved) and launch['coordinator']['session']:
        return launch['coordinator']['session'] in recorded_sessions(saved)
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
    attempts = []
    for state in root.glob('.scratch/*/reports/workers.sqlite'):
        try:
            saved_attempts, _, registered = read_state(state)
        except Exception:  # gaps() names a state that cannot be read to its effort's coordinator.
            continue
        attempts += [dict(saved, registered_at=registered.get(saved['attempt'])) for saved in saved_attempts]
    workers = {handle for saved in attempts for handle in handles(saved['worker'])}
    log, worktrees = root / LAUNCHES, (root / '.worktrees').resolve()
    records, unreadable = [], []
    for number, line in enumerate(log.read_text().splitlines() if log.is_file() else [], 1):
        try:
            record = json.loads(line)
        except ValueError:
            record = None
        if readable(record) and 'dismissed' not in record and record['agent'] is None and record['pane'] is None:
            # An older hook's line for a start that ran no agent: nothing can clear it, so only its coordinator is
            # told to remove it.
            unreadable += [f'{log}: launch log line {number} names no agent or pane, so no attempt or dismissal can '
                           'clear it; remove it'] if same_coordinator(record['coordinator'], pane, session) else []
        elif readable(record):
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
    # Another coordinator's logged launch, dismissed or not, is that session's worker, never a live sibling of this one.
    # By pane: a running Herdr server does not reuse a pane ID, while an agent name is reused freely.
    workers |= {record['pane'] for record in records if 'dismissed' not in record
                and not same_coordinator(record['coordinator'], pane, session)}
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
