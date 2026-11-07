#!/usr/bin/env python3
"""Stop hook: keep the host working while a lifecycle worker could finish without waking it.

Reads every run's workers.sqlite under the main checkout's .scratch/*/reports/ and .scratch/reviews/*/ without
writing to it, except that a stop (never `check`) first has the lifecycle's `reap` close the attempts this session
answers for whose status is already decided, and names each closure the lifecycle refuses. Applies the lifecycle's
yield gate to the workers this session registered (by host session, else pane), whatever the age of the state, or, for one that recorded neither and whose state changed in the last day, to
the session bound to orchestrate its workstream (with none bound, to every session). Sessions share the checkout:
another session's workers and launches never hold this one, and a row, state or launch log line that cannot be read is named only to a session it can be attributed to (a
binding beside it, or its pane or host session in its text). Every session is also held for each Herdr launch it logged
(hooks/herdr_launch.py) into a ticket worktree that no attempt accounts for and it did not dismiss, and for each pane of a
launch it dismissed that is still open unless kept (`dismiss --keep`); one without
ZLOOP_WORKER, the marker every worker is launched with, also for each other such live
agent of its Herdr workspace that no other session logged. The session bound to orchestrate a workstream
(next_tickets.py --orchestrate; without one, the pane that registered the attempt) is held for each collected
attempt, neither retained nor retired, whose Herdr tab is still open, and reminded of a retained one of its own that
nothing in its run follows (nothing unfinished, no later attempt of its conversation, no design hold on its
assignment) and of one recorded closed whose own pane Herdr shows still holding its conversation; and for a workstream whose
review report (before it records one, the binding) records autofix: true and no blocker, with
every ticket done and delivery failing, until it is finished (branch deleted, reviewed head in
main or on the remote's main, E2E passed at a head in main). A session is also held while a
confirmed autopilot run card (.scratch/<slug>/autopilot.md) naming it lacks one terminal state or its
evidence, or records delivered, delivered-local or merged while its workstream is not delivered on its local board
(another workstream's problems hold nothing). A review binding beside a standalone or workstream report holds its session until the report names a
completed collected diff-review attempt at review_head, names local review, or records a blocker with a launched
diff-review attempt in its state at review_head settled failed, blocked or cancelled; local review and a blocker without
a reviewer count only where review_target.py found no reviewer route for the recorded host and the router, asked
again, still finds none. Launch evidence is a Herdr placement, verified startup or recorded submission.
The review, delivery and card holds do not apply while a live worker of that state holds a running
wait that will wake the host. A stop
is held while its gaps stand, up to three times in a row for an identical set (a count per session under
.scratch/holds/); then the stop passes and the gate says it gave up. A check that cannot run is a named gap, never a
pass, and holds one stop, as do reminders when nothing else stands. `claude` and `codex` speak their shared Stop
hook protocol; `check <cwd> [session]` prints the gaps and the hold's message as JSON, and `stop <cwd> [session]`, which
other hosts call at each stop, also counts the stop and says whether to hold it. Set
ZLOOP_YIELD_HOOK=off to disable the check.
"""
import calendar
import functools
import importlib.util
import json
import os
import re
import shlex
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
RECENT_SECONDS = 24 * 3600
# The name Herdr gives the server a shell reaches without `--session`.
DEFAULT_SERVER = 'default'
GUIDE = ROOT / 'runtime/workers/wait.md'
PREPARE = ROOT / 'runtime/workers/prepare.md'
COLLECT = ROOT / 'runtime/workers/collect.md'
RETIRE = ROOT / 'runtime/resources/retire.md'
# Reminders hold a stop once, as a failed check does: a retained worker nothing follows, a closure Herdr contradicts.
REMINDER = 'and nothing in its run follows it'
CLOSED_REMINDER = 'recorded closed, but'
HANDOFF = ROOT / 'skills/dispatch/handoff.md'
# hooks/herdr_launch.py appends each `herdr agent start` here, one JSON object per line.
LAUNCHES = '.scratch/herdr-launches.jsonl'
# A session's stop is held while its gaps stand, this many consecutive times for an identical set; its count of them
# is kept in a file per session here.
HOLDS = '.scratch/holds'
MAX_HOLDS = 3
# scripts/next_tickets.py --orchestrate records here, in a workstream's reports/, the session that orchestrates it.
BINDING = 'orchestrator.json'
# The ones that claim the work delivered: the card's workstream must be delivered on its local board.


def lifecycle():
    return script('worker_lifecycle')


@functools.cache
def script(name):
    """The plugin's script `name`, loaded once per stop, so what it learns in one check (the router's answer) serves the
    next."""
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
    """(worker gaps, the orchestrator's own gaps: stale tabs and undelivered workstreams, each saying how to clear
    it)."""
    root = main_checkout(cwd)
    if root is None:
        return [], []
    cwd, pane = Path(cwd).resolve(), os.environ.get('HERDR_PANE_ID')
    module, missing = None, []
    for state in states(root):
        # Sessions share the folder: a worker holds only the session that registered it, by host session before pane;
        # one registered with neither recorded holds the session bound to orchestrate its workstream, or, with none
        # bound, every session that is not a worker, since nothing tells its owner apart.
        bound, unbound = binding_state(state, pane, session)
        try:
            saved_attempts, malformed, _ = read_state(state)
        except Exception as error:
            missing += [f'{state}: cannot be read ({error}); fix or remove it'] if unreadable_holds(
                state, pane, session, bound) else []
            continue
        missing += [f'{state}: attempt row {key} is malformed; fix or remove it' for key, saved in malformed
                    if bound or (isinstance(saved, dict) and attributed(saved) and owns(saved, pane, session))]
        module = module or lifecycle()
        orchestrated = answering(state, saved_attempts, root, cwd, pane, session, bound, unbound)
        db = sqlite3.connect(f'{state.as_uri()}?mode=ro', uri=True, timeout=5)
        try:
            missing += [f'{state}: {gap}' for gap in module.yield_gaps(db, orchestrated, saved_attempts)]
        except Exception as error:  # Named to whoever it could hide a gap from: its orchestrator and its workers' own.
            if any(orchestrated(saved) for saved in saved_attempts) or unreadable_holds(state, pane, session, bound):
                missing.append(f'{state}: cannot be read ({error}); fix or remove it')
        finally:
            db.close()
    # Every session answers for its own launches; a worker (its launch set ZLOOP_WORKER) not for its siblings.
    return (missing + unregistered(root, pane, session, siblings=not os.environ.get('ZLOOP_WORKER')),
            stale_tabs(root, pane, session) + undelivered(root, pane, session) + unfinished_runs(root, session)
            + unreviewed(root, pane, session))


def binding_state(state, pane, session):
    """(whether the binding of a run state's workstream names this session, whether it keeps none)."""
    owner = state.with_name('review.json' if state.parent.parent.name == 'reviews' else BINDING)
    binding, bound = read_binding(owner, pane, session)
    return bound, binding is None and not owner.exists()


def answering(state, saved_attempts, root, cwd, pane, session, bound, unbound):
    """Whether this session answers for an attempt row of a run state: the one that registered it, or, for a row nobody
    can be attributed to, the one bound to orchestrate the workstream (with none bound, any session that is not a
    worker) while the state is recent. Returns that test as a function of the row."""
    recent = time.time() - state.stat().st_mtime <= RECENT_SECONDS
    # A session inside a worker's own worktree is that worker, not its orchestrator.
    worker = any(Path(saved['worktree']) != root and cwd.is_relative_to(saved['worktree'])
                 for saved in saved_attempts if 'worktree' in saved)

    def orchestrated(saved):
        # A session launched as a worker (ZLOOP_WORKER) answers for no worker it did not register.
        # A row nobody can be attributed to holds only while its state is recent.
        fallback = bound or unbound and not os.environ.get('ZLOOP_WORKER')
        return owns(saved, pane, session) if attributed(saved) else recent and fallback and not worker
    return orchestrated


def reaped(cwd, session=None):
    """Close, through the lifecycle's `reap`, each attempt this session answers for whose status is already decided; one
    gap for each closure the lifecycle refuses, and for a run state it could not reap. Never raises."""
    try:
        root = main_checkout(cwd)
    except Exception:  # checked() names a git that cannot tell.
        return []
    if root is None:
        return []
    cwd, pane = Path(cwd).resolve(), os.environ.get('HERDR_PANE_ID')
    missing, deadline = [], None
    for state in states(root):
        mine = None
        try:
            module = lifecycle()
            # One budget for the whole stop, whatever the number of run states: what it does not reach waits for the next.
            deadline = deadline or time.monotonic() + module.REAP_SECONDS
            if time.monotonic() >= deadline:
                break
            db = sqlite3.connect(state, timeout=max(0.1, deadline - time.monotonic()))
            try:
                # The attempts the lifecycle itself reads, not only those the gate's validation accepts.
                saved_attempts = module.attempts(db)
                bound, unbound = binding_state(state, pane, session)
                orchestrated = answering(state, saved_attempts, root, cwd, pane, session, bound, unbound)
                mine = {saved['attempt'] for saved in saved_attempts if orchestrated(saved)}
                if not mine:
                    continue
                found = module.reap(db, state, lambda saved: saved['attempt'] in mine,
                                    budget=deadline - time.monotonic())
            finally:
                db.close()
        except Exception as error:  # A check that cannot run is a named gap, never a pass; gaps() names an unreadable state.
            missing += [f'could not check settled workers in {state}: {error!r}'] if mine else []
            continue
        missing += [f"{state}: {row['attempt']}: not closed at stop: {row['reason']}; settle it, then run "
                    f'`python3 {ROOT}/scripts/worker_lifecycle.py reap --state {state}`' for row in found['refused']]
    return missing


def unreviewed(root, pane, session):
    """Review bindings naming this session whose reports lack collected reviewer evidence."""
    missing = []
    for path in sorted(root.glob('.scratch/reviews/*/review.json')) + sorted(root.glob('.scratch/*/reports/review.json')):
        binding, mine = read_binding(path, pane, session)
        if not mine:
            continue
        if binding is None:
            missing.append(f'{path}: review binding cannot be read; rerun review_target.py for this target')
            continue
        try:
            report, state = Path(binding['report']), Path(binding['state'])
            if awaited(state):
                continue
            board = script('next_tickets')
            board.ROOT, board.ISSUES = root, root / '.scratch'  # The checkout whose router judges a local review.
            fields = script('front_matter').parse(report.read_text()) or {}
            blocker = fields.get('blocker') not in (None, '', 'none')
            routed = board.local_review_problems(binding) if blocker else []
            if blocker and not routed:  # The router still finds no reviewer to wait on.
                continue
            attempts = read_state(state)[0] if state.is_file() and (blocker or fields.get('review_by') != 'local') else []
            if blocker and any(saved.get('job') == 'diff-review' and
                               saved.get('head') == (saved.get('result') or {}).get('head') == fields.get('review_head') and
                               (saved.get('herdr') or saved.get('verified') and saved.get('startup_evidence')
                                or saved.get('submitted')) and
                               (saved.get('result') or {}).get('status') in ('failed', 'blocked', 'cancelled')
                               for saved in attempts):
                continue
            problems = board.review_by_problems(fields, attempts, binding)
            if problems:
                problems = list(dict.fromkeys(routed + problems))  # Why the blocker does not release it, too.
                live = [saved for saved in attempts if not saved.get('result')]
                action = ('route' if blocker else
                          'collect' if any(saved.get('submitted') or saved.get('submission_requested') for saved in live)
                          else 'submit' if live else 'route')
                missing.append(f"{report}: {'; '.join(problems)}; next: `{binding['next_commands'][action]}`")
        except Exception as error:
            missing.append(f'{path}: could not check review: {error}; rerun review_target.py for this target')
    return missing


def states(root):
    """Lifecycle states of workstreams and standalone reviews in the main checkout."""
    return sorted(set(root.glob('.scratch/*/reports/workers.sqlite')) | set(root.glob('.scratch/reviews/*/workers.sqlite')))


def names(text, pane, session):
    """Whether raw text holds this pane or host session as a whole ID, bounded by characters no pane or session ID
    holds, so w1:p1 is not found in w1:p12."""
    return any(value and re.search(rf'(?<![\w:.-]){re.escape(value)}(?![\w:.-])', text) for value in (pane, session))


def unreadable_holds(state, pane, session, bound):
    """Whether a run state that cannot be read is named to this session: it hides its owners' gaps, so it is named to
    the session bound to its workstream and each pane or host session its bytes still record, and to no one else."""
    try:
        raw = state.read_bytes().decode(errors='replace')
    except OSError:
        raw = ''
    return bool(bound) or names(raw, pane, session)


def read_state(state):
    """(well-formed attempts, (row id, data) of each malformed row, each attempt's registration time) of a run state.
    Raises when the state cannot be read."""
    db = sqlite3.connect(f'{state.as_uri()}?mode=ro', uri=True, timeout=5)
    try:
        rows = db.execute('SELECT id, data FROM attempts ORDER BY id').fetchall()
        events = db.execute("SELECT attempt, payload FROM events WHERE kind = 'register' ORDER BY id").fetchall()
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
    if any(key in saved and saved[key] is not None and not text(saved[key]) for key in ('orchestrator', 'session')):
        return False
    if any(key in saved and not (isinstance(saved[key], list) and all(map(text, saved[key])))
           for key in ('sessions', 'launch_sessions')):
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
    return bool(saved.get('session') or saved.get('orchestrator'))


def owns(saved, pane, session):
    """Whether this session registered the attempt: by host session before pane. A host started inside another's shell
    exports the outer host's session too, so each exported session counts."""
    return any(same_orchestrator({'pane': saved.get('orchestrator'), 'session': known}, pane, session)
               for known in recorded_sessions(saved) or [None])


def recorded_sessions(saved):
    """The host sessions an attempt's registrar exported: its session list, else its one session."""
    listed = saved.get('sessions') if isinstance(saved.get('sessions'), list) else []
    return [value for value in listed if isinstance(value, str)] or (
        [saved['session']] if isinstance(saved.get('session'), str) else [])


def board_gaps(root, path, state):
    """What `autopilot_card.py phase` would refuse in a run card's State written by hand: a phase none of the four,
    or dispatch or pr with no board, or with no ticket on its local board."""
    cards = script('autopilot_card')
    phase, board = state.get('phase', ''), state.get('board', '')
    if phase not in cards.PHASES:
        setter = shlex.join(['python3', str(ROOT / 'scripts/autopilot_card.py'), 'phase', str(path), '<phase>'])
        return [f"{path}: this session's autopilot run has phase {phase!r}, none of {', '.join(cards.PHASES)}: set it with "
                f'`{setter}`']
    if phase not in ('dispatch', 'pr') or cards.tracker_url(board):
        return []
    if board != 'local':
        return [f"{path}: this session's autopilot run is in {phase} with no board: write `board: local` or the "
                "tracker's URL in the card's State"]
    if cards.ticket_files(path.parent / 'issues'):
        return []
    new = shlex.join(['python3', str(ROOT / 'scripts/next_tickets.py'), '--repo', str(root), path.parent.name, '--new',
                      '<title>'])
    return [f"{path}: this session's autopilot run is in {phase} with no tickets on its local board: write each with "
            f'`{new}`']


def delivery_gaps(root, path, state):
    """What holds a run card that records a delivered terminal state: a board neither `local` nor a tracker's URL, or
    what keeps its workstream from delivered, as `next_tickets.undelivered` names it for the stop check and the panel
    alike. Another workstream's problems are not this run's to fix."""
    terminal, slug, board = state['terminal'], path.parent.name, state.get('board', '')
    cards = script('autopilot_card')
    if cards.tracker_url(board):
        return []
    if board != 'local':
        return [f"{path}: this session's autopilot run records `terminal: {terminal}` with no board: write `board: local` "
                "or the tracker's URL in the card's State"]
    try:
        module = script('next_tickets')
        module.ROOT, module.ISSUES = root, root / '.scratch'
        problems = module.undelivered(slug)  # Exits on a ticket of this workstream it refuses.
    except (Exception, SystemExit) as error:  # A check that cannot run is a named gap, never a pass.
        problems = [f'it cannot be checked: {error}']
    if not problems:
        return []
    return [f"{path}: this session's autopilot run records `terminal: {terminal}` while its workstream is not delivered: "
            f"{'; '.join(problems)}; finish what it names, or record the terminal state that fits"]


def unfinished_runs(root, session):
    """Name each confirmed autopilot run card of this host session that lacks one terminal state or its evidence,
    unless a live worker of the card's workstream holds a running wait that will wake the host, and in such a run
    what `autopilot_card.py phase` would refuse; and each that records a delivered state its local board refuses."""
    missing = []
    for path in sorted(root.glob('.scratch/*/autopilot.md')):
        life = lifecycle()
        state = life.card_state(path)
        if not (session in life.card_sessions(state) and life.confirmed(state)):
            continue
        if state.get('terminal') in life.TERMINAL_STATES and state.get('evidence'):
            if state['terminal'] in life.ENDED:
                missing += delivery_gaps(root, path, state)
            continue
        missing += board_gaps(root, path, state)
        if not awaited(path.with_name('reports') / 'workers.sqlite'):
            missing.append(f"{path}: this session's autopilot run has no terminal state or no evidence; record "
                           f"`terminal:` as exactly one of {', '.join(f'`{name}`' for name in life.TERMINAL_STATES)} and "
                           "`evidence:` in the card's State")
    return missing


def stale_tabs(root, pane, session):
    """Name each collected attempt whose recorded Herdr tab its session still lists, for the session bound to
    orchestrate its workstream or, with none bound, the pane that registered it, however old the run: one neither
    retained nor closed; as reminders, a retained one that nothing follows (its run has nothing unfinished, it is its
    conversation's latest attempt and no design hold waits on the user for its assignment) and one recorded closed
    whose own pane still holds its conversation."""
    listed, failed, missing = {}, {}, []
    unanswered = False  # Once Herdr fails to answer, the reminders about closures ask it nothing more this stop.
    for state in states(root):
        # An unreadable binding decides nothing here (undelivered names it); the registering pane does.
        binding, mine = read_binding(state.with_name(BINDING), pane, session)
        bound = mine if binding else None
        try:
            saved_attempts, _, registered = read_state(state)
            held = open_holds(state)
        except Exception:  # gaps() names a state that cannot be read to its workstream's orchestrator.
            continue
        closed = {(saved['runtime'], saved['native_session']) for saved in saved_attempts if saved.get('closed')}
        unfinished = any(not saved.get('result') and (saved['runtime'], saved['native_session']) not in closed
                         for saved in saved_attempts)
        order, latest = {name: index for index, name in enumerate(registered)}, {}
        for saved in sorted(saved_attempts, key=lambda saved: order.get(saved['attempt'], -1)):
            latest[(saved['runtime'], saved['native_session'])] = saved['attempt']
        for saved in saved_attempts:
            herdr = saved.get('herdr') or {}
            mine = bound if bound is not None else attributed(saved) and owns(saved, pane, session)
            if not (herdr.get('tab_id') and saved.get('result') and mine):
                continue
            retained = saved.get('retained')
            if not saved.get('closed') and retained and (
                    not saved['owned'] or unfinished or saved.get('assignment') in held
                    or latest[(saved['runtime'], saved['native_session'])] != saved['attempt']):
                continue  # A user's worker, or one its run, a later attempt or the user's ruling may still use.
            where = (herdr['session'], herdr['workspace_id'])
            if saved.get('closed') and unanswered:
                continue
            if where not in listed:
                try:
                    listed[where] = herdr_tabs(*where)
                except Exception as error:
                    listed[where], failed[where], unanswered = set(), f'could not check worker tabs: {error}', True
            if saved.get('closed'):
                # A recorded closure is reminded of, never gated: a Herdr that cannot be asked names nothing here.
                if herdr['tab_id'] in listed[where] and herdr.get('pane_id'):
                    own = holds_own(saved, herdr)
                    unanswered = own is None
                    if own:
                        named = '' if herdr['session'] == socket_server() else f"--session {herdr['session']} "
                        missing.append(f"{state}: {saved['attempt']}: {CLOSED_REMINDER} Herdr pane {herdr['pane_id']} "
                                       f"in tab {herdr['tab_id']} still holds its conversation; end it with `herdr "
                                       f"{named}pane close {herdr['pane_id']}`")
            elif where in failed:  # A check that cannot run is a named gap, never a pass.
                missing.append(failed.pop(where))
            elif herdr['tab_id'] in listed[where] and retained:
                missing.append(f"{state}: {saved['attempt']}: retained for \"{retained['next_use']}\" in Herdr tab "
                               f"{herdr['tab_id']}, {REMINDER}: register that next attempt in this worker, raise a "
                               f'design hold for the decision it waits on, or retire the worker ({RETIRE})')
            elif herdr['tab_id'] in listed[where]:
                missing.append(f"{state}: {saved['attempt']}: Herdr tab {herdr['tab_id']} is still open after "
                               f'collection; retire the worker or record its retention ({COLLECT})')
    return missing


def holds_own(saved, herdr):
    """Whether Herdr shows the attempt's recorded pane holding its own native conversation: None when Herdr did not
    answer, False for any other pane or a row it cannot be compared with."""
    named = [] if herdr['session'] == socket_server() else ['--session', herdr['session']]
    try:
        result = subprocess.run(['herdr', *named, 'pane', 'get', herdr['pane_id']], capture_output=True, text=True,
                                timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        identity = json.loads(result.stdout)['result']['pane'].get('agent_session') if result.returncode == 0 else None
        return isinstance(identity, dict) and lifecycle().conversation_named(saved, identity) is True
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def open_holds(state):
    """The assignments of a run state with a design hold not yet ruled on; none in a state that never held one."""
    db = sqlite3.connect(f'{state.as_uri()}?mode=ro', uri=True, timeout=5)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'holds'").fetchone():
            return set()
        return {row[0] for row in db.execute('SELECT assignment FROM holds')}
    finally:
        db.close()


def herdr_tabs(session, workspace):
    """The IDs of the tabs a Herdr session lists in a workspace; none when the workspace is closed or the session
    stopped, which close its tabs."""
    named = [] if session == socket_server() else ['--session', session]
    result = subprocess.run(['herdr', *named, 'tab', 'list', '--workspace', workspace],
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
    """Name each workstream this session was bound to orchestrate (next_tickets.py --orchestrate) whose delivery fails."""
    board, missing = None, []
    for path in sorted(root.glob(f'.scratch/*/reports/{BINDING}')):
        slug = path.parent.parent.name
        binding, mine = read_binding(path, pane, session)
        if not mine:
            continue
        if binding is None:
            missing.append(f'{path}: the orchestrator binding cannot be read; rebind with '
                           f'`python3 {ROOT}/scripts/next_tickets.py --repo {root} {slug} --orchestrate`')
            continue
        try:
            if board is None:
                board = script('next_tickets')
                board.ROOT, board.ISSUES = root, root / '.scratch'
            tickets = board.load(spared=slug)  # load() exits on a ticket of this workstream it refuses.
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
    since only such a session could be its orchestrator."""
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
        return binding, orchestrates(binding, pane, session)
    return None, names(text, pane, session)


def orchestrates(binding, pane, session):
    """Whether a dispatch binding ({pane, sessions}) names this session: as for a launch, by host session alone when
    both sides know one."""
    return any(same_orchestrator({'pane': binding['pane'], 'session': known}, pane, session)
               for known in binding['sessions'] or [None])


def herdr_agents():
    """The live agents the session's Herdr server reports."""
    result = subprocess.run(['herdr', 'agent', 'list'], capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise RuntimeError(f'herdr agent list exited {result.returncode}: {(result.stderr or result.stdout).strip()}')
    return json.loads(result.stdout)['result']['agents']


def same_orchestrator(theirs, pane, session):
    """Whether a recorded orchestrator ({pane, session}) is this session: by host session alone when both sides know
    one, since a Codex session's HERDR_PANE_ID can come from another pane (its shared app-server's) and a restarted
    Herdr may reuse a pane ID; else the pane decides."""
    if theirs['session'] and session:
        return theirs['session'] == session
    return bool(pane) and theirs['pane'] == pane


def socket_server(environ=os.environ):
    """The Herdr server HERDR_SOCKET_PATH is the socket of by Herdr's layout, else None: a lookup of that server goes
    through the socket rather than by name (as worker_lifecycle.socket_server)."""
    socket = Path(environ.get('HERDR_SOCKET_PATH') or '')
    if socket.name != 'herdr.sock':
        return None
    if socket.parent.name == 'herdr':
        return DEFAULT_SERVER
    return socket.parent.name if socket.parent.parent.name == 'sessions' and socket.parent.parent.parent.name == 'herdr' \
        else None


def own_server(environ=os.environ):
    """The Herdr server session the process talks to: the name in its HERDR_SOCKET_PATH
    (`.../sessions/<name>/herdr.sock`), else the default server."""
    socket = Path(environ.get('HERDR_SOCKET_PATH') or '')
    return socket.parent.name if socket.parent.parent.name == 'sessions' else DEFAULT_SERVER


def server_of(saved):
    """The Herdr server session an attempt's worker runs in, when it recorded one: its handle's or its placement's."""
    parts = saved['worker'].rsplit('/', 2)
    return parts[0] if len(parts) == 3 else (saved.get('herdr') or {}).get('session')


def attempt_keys(saved):
    """Where an attempt is indexed: by the server it names (None: any), what its worker names, and, for a launch to
    find it without a scan, the host session that registered or cleared it, else its orchestrator pane. A launch with
    a host session and an attempt with sessions are matched by session alone (see clears), every other pair by pane."""
    server, name = server_of(saved), named(saved['worker'])
    sessions = recorded_sessions(saved) + saved.get('launch_sessions', [])
    keys = [(server, name, 'orchestrator', saved.get('orchestrator'), bool(recorded_sessions(saved)))]
    return keys + [(server, name, 'session', known) for known in sessions] if sessions else keys


def candidates(index, launch):
    """The attempts that could account for a launch: its pane or agent, in its server or one that names none, by its
    host session, or by its pane when either side has no session."""
    server = launch.get('herdr_session') or DEFAULT_SERVER
    session, pane = launch['orchestrator']['session'], launch['orchestrator']['pane']
    for name in (launch['agent'], launch['pane']):
        for where in (server, None):
            yield from index.get((where, name, 'orchestrator', pane, False), [])
            yield from index.get((where, name, 'session', session) if session else
                                 (where, name, 'orchestrator', pane, True), [])


def named(worker):
    """What a registered worker names: the pane of a `<server session>/<pane>/<name>` handle, whatever name it carries
    now; else the worker itself, a pane or an agent name."""
    parts = worker.rsplit('/', 2)
    return parts[1] if len(parts) == 3 else worker


def clears(saved, launch):
    """Whether an attempt accounts for a logged launch: it names the launch's pane (a handle only its pane) or agent,
    in its Herdr server (candidates() finds it there), and a host session
    recorded at registration, or one whose launches it was recorded to clear, is the one that launched it when both
    know one; else the orchestrator recorded at
    registration is the pane that launched it, and only a launch made outside Herdr can instead be cleared by an
    attempt with no recorded orchestrator, registered no earlier."""
    if named(saved['worker']) not in (launch['agent'], launch['pane']):
        return False
    if recorded_sessions(saved) and launch['orchestrator']['session']:
        return launch['orchestrator']['session'] in recorded_sessions(saved) + saved.get('launch_sessions', [])
    if saved.get('orchestrator') or launch['orchestrator']['pane']:
        return saved.get('orchestrator') == launch['orchestrator']['pane']
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
                and all(text(value, False) for value in target.values()) and identity(record.get('by'))
                and isinstance(record.get('keep', False), bool))
    try:
        time.strptime(record.get('time'), '%Y-%m-%dT%H:%M:%SZ')
    except (TypeError, ValueError):
        return False
    return ({'agent', 'pane'} <= set(record) and text(record['agent']) and text(record['pane'])
            and identity(record.get('orchestrator')) and text(record.get('cwd')) and text(record.get('herdr_session')))


def pane_open(server, pane):
    """Whether a Herdr server still lists the pane; one that is not running lists none."""
    named = ['--session', server] if server and server != socket_server() else []
    result = subprocess.run(['herdr', *named, 'pane', 'get', pane],
                            capture_output=True, text=True, timeout=5)
    if result.returncode and herdr_error(result) in ('pane_not_found', 'workspace_not_found', 'server_not_running'):
        return False
    if result.returncode:
        raise RuntimeError(f'herdr pane get exited {result.returncode}: {(result.stderr or result.stdout).strip()}')
    return True


def unregistered(root, pane, session, siblings=True):
    """Name each Herdr worker that no attempt in any run and no dismissal of this orchestrator accounts for: each
    launch it logged into a ticket worktree or an unknown cwd (an attempt of another host session never accounts for
    it when both know one), with `siblings` each other live agent in a ticket worktree of its Herdr workspace; and
    each unreadable log line it names; and each pane of a launch it dismissed that is still open, unless dismissed
    with `--keep`."""
    attempts = []
    for state in states(root):
        try:
            saved_attempts, _, registered = read_state(state)
        except Exception:  # gaps() names a state that cannot be read to its workstream's orchestrator.
            continue
        attempts += [dict(saved, registered_at=registered.get(saved['attempt'])) for saved in saved_attempts]
    index = {}  # The attempts by server, name and session or pane: a launch looks up its own, never scanning the rest.
    for saved in attempts:
        for key in attempt_keys(saved):
            if key[2] == 'orchestrator' and key[3] is None and key in index:
                # With no orchestrator pane, only registration time tells attempts apart: the latest one decides.
                index[key] = [max(index[key][0], saved, key=lambda other: other['registered_at'] or 0)]
            else:
                index.setdefault(key, []).append(saved)
    placed = {(server_of(saved), named(saved['worker'])) for saved in attempts}
    # What this session registered by name: in the Herdr server and tab it recorded, when it recorded them.
    mine = {(server_of(saved), saved['worker'], (saved.get('herdr') or {}).get('tab_id'))
            for saved in attempts if attributed(saved) and owns(saved, pane, session)}
    workers = set()
    log, worktrees = root / LAUNCHES, (root / '.worktrees').resolve()
    records, unreadable = [], []
    for number, line in enumerate(log.read_text().splitlines() if log.is_file() else [], 1):
        try:
            record = json.loads(line)
        except ValueError:
            record = None
        if readable(record) and 'dismissed' not in record and record['agent'] is None and record['pane'] is None:
            # An older hook's line for a start that ran no agent: nothing can clear it, so only its orchestrator is
            # told to remove it.
            unreadable += [f'{log}: launch log line {number} names no agent or pane, so no attempt or dismissal can '
                           'clear it; remove it'] if same_orchestrator(record['orchestrator'], pane, session) else []
        elif readable(record):
            records.append(record)
        elif names(line, pane, session):  # Named to a session its text names, and ignored for matching.
            unreadable.append(f'{log}: launch log line {number} is unreadable; fix or remove it')
    launches, dismissed = [], []
    for record in records:
        if 'dismissed' in record:  # Accounts for its orchestrator's launches of that pane or name logged before it.
            def covered(launch):
                return (all(launch.get(key) == value for key, value in record['dismissed'].items())
                        and same_orchestrator(launch['orchestrator'], record['by']['pane'], record['by']['session']))
            dismissed = [launch for launch in dismissed if not record.get('keep') or not covered(launch)]
            if not record.get('keep'):
                dismissed += [launch for launch in launches if covered(launch)]
            launches = [launch for launch in launches if not covered(launch)]
        else:
            launches.append(record)
    # Another orchestrator's logged launch, dismissed or not, is that session's worker, never a live sibling of this one.
    # By pane: a running Herdr server does not reuse a pane ID, while an agent name is reused freely.
    workers |= {record['pane'] for record in records if 'dismissed' not in record
                and not same_orchestrator(record['orchestrator'], pane, session)}
    launches = [launch for launch in launches if same_orchestrator(launch['orchestrator'], pane, session)
                and not any(clears(saved, launch) for saved in candidates(index, launch))
                and ('cwd' not in launch or Path(launch['cwd']).resolve().is_relative_to(worktrees))]
    workers |= {handle for launch in launches for handle in (launch['agent'], launch['pane'])}  # Named once.
    workers |= {value for record in records if 'dismissed' in record  # This orchestrator's dismissals cover live agents.
                and same_orchestrator(record['by'], pane, session) for value in record['dismissed'].values()}
    workspace, failed, still_open, asked = os.environ.get('HERDR_WORKSPACE_ID'), [], [], {}
    for launch in dismissed:  # Each pane a dismissal of this orchestrator left open, named once.
        where = (launch.get('herdr_session'), launch['pane'])
        if launch['pane'] and where not in asked and same_orchestrator(launch['orchestrator'], pane, session):
            try:
                asked[where] = pane_open(*where)
            except Exception as error:  # A check that cannot run is a named gap, never a pass.
                asked[where], gap = False, f'could not check Herdr workers: {error}'
                failed += [gap] if gap not in failed else []
            still_open += [f"{launch['pane']}: dismissed launch is still open; close it, or dismiss with --keep <reason>"
                           ] if asked[where] else []
    try:
        agents = herdr_agents() if siblings and workspace and worktrees.is_dir() else []  # No worktrees, no workers.
    except Exception as error:  # A check that cannot run is a named gap, never a pass.
        agents, failed = [], failed + [f'could not check Herdr workers: {error}']
    # A name is reused freely, so an attempt accounts for a live agent by its pane, or by its name only when this session
    # registered it (another session's launch is told by its pane, above).
    live = [agent for agent in agents
            if agent.get('workspace_id') == workspace and agent['pane_id'] != pane and agent.get('cwd')
            and Path(agent['cwd']).resolve().is_relative_to(worktrees)
            and not workers & {agent.get('name'), agent['pane_id']} and not placed & {(server, agent['pane_id']) for server in (None, own_server())}
            and not {(server, agent.get('name'), tab) for server in (None, own_server())
                     for tab in (None, agent.get('tab_id'))} & mine]
    return ([f"{log}: {launch['agent']} (pane {launch['pane']}): launched without a registered attempt"
             for launch in launches]
            + [f"Herdr agent {agent.get('name')} (pane {agent['pane_id']}) runs in {agent['cwd']} "
               'without a registered attempt' for agent in live] + still_open + unreadable + failed)


def checked(cwd, session=None):
    """The gaps as gaps() splits them, or a worker gap naming why the check could not run: a broken check never passes
    silently."""
    try:
        return gaps(cwd, session)
    except Exception as error:
        return [f'could not check workers: {error}'], []


def count_hold(cwd, session, missing):
    """Record one more stop of this session with these gaps: (whether to hold it, the notice that the gate gave up or
    None, said once). An identical set holds up to MAX_HOLDS consecutive stops, one when every gap is a check that could
    not run or a reminder; a changed or cleared set starts the count again."""
    try:
        root = main_checkout(cwd)
    except Exception:
        root = Path(cwd)  # Git cannot place the checkout: the count lives in the session's own directory.
    if root is None:
        return False, None
    pane = os.environ.get('HERDR_PANE_ID')
    key = f'session-{session}' if session else f'pane-{pane}' if pane else 'anonymous'
    path = root / HOLDS / f"{quote(key, safe='')}.json"
    if not missing and not path.parent.is_dir():
        return False, None
    path.parent.mkdir(parents=True, exist_ok=True)
    files = script('state_files')
    with files.locked(path):  # Concurrent stops of one session are counted one at a time.
        if not missing:
            path.unlink(missing_ok=True)
            return False, None
        missing = sorted(missing)
        try:
            saved = json.loads(path.read_text())
        except (OSError, ValueError):
            saved = {}
        held = saved.get('holds', 0) if saved.get('gaps') == missing else 0
        if held >= (1 if all(failed_check(gap) or REMINDER in gap or CLOSED_REMINDER in gap for gap in missing)
                      else MAX_HOLDS):
            if saved.get('told'):
                return False, None  # Said once; later stops of the same gaps pass quietly.
            files.write_atomic(path, json.dumps({'gaps': missing, 'holds': held, 'told': True}))
            return False, (f"zloop yield gate gave up holding the stop after {held} {'hold' if held == 1 else 'holds'} "
                           'of the same gaps; stopping with:\n' + ''.join(f'- {gap}\n' for gap in missing))
        files.write_atomic(path, json.dumps({'gaps': missing, 'holds': held + 1}))
        return True, None


def failed_check(gap):
    """Whether a gap says a check could not run, which registering or dismissing cannot clear."""
    return gap.startswith('could not check')


def reason(workers, own, session=None):
    """The hold's message: every gap, the worker advice only for a worker gap that is not a failed check, and the retry
    advice for a failed check, which registering or dismissing cannot clear."""
    message = 'Stopping now leaves these gaps:\n' + ''.join(f'- {gap}\n' for gap in workers + own)
    failed = [gap for gap in workers + own if failed_check(gap)]
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
    if sys.argv[1:2] in (['check'], ['stop']) and len(sys.argv) in (3, 4):
        closing = reaped(Path(sys.argv[2]), *sys.argv[3:]) if sys.argv[1] == 'stop' else []
        workers, own = checked(Path(sys.argv[2]), *sys.argv[3:])
        own += closing
        report = {'missing': workers + own, 'reason': reason(workers, own, *sys.argv[3:])}
        if sys.argv[1] == 'stop':  # A host's stop, counted and settling what it can; `check` only reports.
            hold, notice = count_hold(sys.argv[2], (sys.argv[3:] or [None])[0], workers + own)
            report.update(hold=hold, notice=notice)
        print(json.dumps(report))
        return 0
    if sys.argv[1:] not in (['claude'], ['codex']):
        sys.exit('usage: yield_gate.py claude|codex < stop-hook-input, or yield_gate.py check|stop <cwd> [session]')
    payload = json.load(sys.stdin)
    cwd, session = Path(payload.get('cwd') or os.getcwd()), payload.get('session_id')
    closing = reaped(cwd, session)
    workers, own = checked(cwd, session)
    own += closing
    hold, notice = count_hold(cwd, session, workers + own)
    if hold:
        print(json.dumps({'decision': 'block', 'reason': reason(workers, own, session)}))
    elif notice:
        print(json.dumps({'systemMessage': notice}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
