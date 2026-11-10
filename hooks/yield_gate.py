#!/usr/bin/env python3
"""Stop hook: keep the host working while a lifecycle worker could finish without waking it.

Reads every run's workers.sqlite under the main checkout's .scratch/*/reports/ and .scratch/reviews/*/ without
writing to it, except that a stop (never `check`) first has the lifecycle's `reap` close the attempts this session
answers for whose status is already decided, and names each closure the lifecycle refuses. Applies the lifecycle's
yield gate to the workers this session registered (by host session, else pane), whatever the age of the state. Sessions share the checkout:
another session's workers and launches never hold this one, and a row, state or launch log line that cannot be read is named only to a session it can be attributed to (a
binding beside it, or its pane or host session in its text). Every session is also held for each Herdr launch it logged
(hooks/herdr_launch.py) into a ticket worktree that no attempt accounts for and it did not dismiss, and for each pane of a
launch it dismissed that is still open unless kept (`dismiss --keep`); one without
ZLOOP_WORKER, the marker every worker is launched with, also for each other such live
agent of its Herdr workspace that no other session logged. The session bound to orchestrate a workstream
(next_tickets.py --orchestrate; without one, the pane that registered the attempt) is held for each collected
attempt, neither retained nor retired, whose Herdr tab is still open, and reminded of a retained one of its own that
nothing in its run follows (nothing unfinished, no later attempt of its conversation, no design hold on its
assignment); and for a workstream whose
review report (before it records one, the binding) records autofix: true and no blocker, with
every ticket done and delivery failing, until it is finished (branch deleted, reviewed head in
main or on the remote's main, E2E passed at a head in main). A session is also held while a
confirmed autopilot run card (.scratch/<slug>/autopilot.md) naming it lacks one terminal state or its
evidence, or records delivered, delivered-local or merged while its workstream is not delivered on its local board
(another workstream's problems hold nothing; a workstream the autofix hold also names is named only here). A review binding beside a standalone or workstream report holds its session until the report names a
completed collected diff-review attempt at review_head, names local review, or records a blocker while every launched
diff-review attempt in its state settled at review_head failed, blocked or cancelled; local review and a blocker without
a reviewer count only where review_target.py found no reviewer route for the recorded host and the router, asked
again, still finds none. Launch evidence is a Herdr placement, verified startup or recorded submission.
The review, delivery and card holds do not apply while a live worker of that state holds a running
wait that will wake the host (the lifecycle's `wake_gap`). A stop
is held while its gaps stand, up to three times in a row for an identical set (a count per session under
.scratch/holds/); then the stop passes and the gate says it gave up. A check that cannot run is a named gap (marked `Unchecked` where it is made), never a
pass, and holds one stop with the retry advice, as do reminders when nothing else stands. `claude` and `codex` speak their shared Stop
hook protocol; `check <cwd> [session]` prints the gaps and the hold's message as JSON, and `stop <cwd> [session]`, which
other hosts call at each stop, also counts the stop and says whether to hold it. Set
ZLOOP_YIELD_HOOK=off to disable the check.
"""
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
HERDR_SECONDS = 5  # What each Herdr call of a stop may wait.
GUIDE = ROOT / 'runtime/workers/wait.md'
PREPARE = ROOT / 'runtime/workers/prepare.md'
COLLECT = ROOT / 'runtime/workers/collect.md'
RETIRE = ROOT / 'runtime/resources/retire.md'
HANDOFF = ROOT / 'skills/dispatch/handoff.md'
# hooks/herdr_launch.py appends each `herdr agent start` here, one JSON object per line.
LAUNCHES = '.scratch/herdr-launches.jsonl'
# A session's stop is held while its gaps stand, this many consecutive times for an identical set; its count of them
# is kept in a file per session here.
HOLDS = '.scratch/holds'
MAX_HOLDS = 3


class Reminder(str):
    """A gap that holds one stop: a retained worker nothing follows."""


class Unchecked(Reminder):
    """A check that could not run, marked where it is made; registering or dismissing cannot clear it: retry advice."""


def lifecycle():
    return script('worker_lifecycle')


def pointed(root):
    """The board script, pointed at this checkout."""
    board = script('next_tickets')
    board.ROOT, board.ISSUES = root, root / '.scratch'
    return board


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
    pane = os.environ.get('HERDR_PANE_ID')
    module, missing, runs = None, [], {}
    for state in states(root):
        try:
            runs[state] = read_state(state)
        except Exception as error:
            runs[state] = error
    for state, run in runs.items():
        # Sessions share the folder: a worker holds only the session that registered it, by host session before pane.
        bound = binding_state(state, pane, session)
        if isinstance(run, Exception):
            missing += [Unchecked(f'{state}: cannot be read ({run}); fix or remove it')] if unreadable_holds(
                state, pane, session, bound) else []
            continue
        saved_attempts, malformed, _ = run
        missing += [f'{state}: attempt row {key} is malformed; fix or remove it' for key, saved in malformed
                    if bound or (isinstance(saved, dict) and owns(saved, pane, session))]
        module = module or lifecycle()
        orchestrated = lambda saved: owns(saved, pane, session)
        db = sqlite3.connect(f'{state.as_uri()}?mode=ro', uri=True, timeout=5)
        try:
            missing += [f'{state}: {gap}' for gap in module.yield_gaps(db, orchestrated, saved_attempts)]
        except Exception as error:
            if any(orchestrated(saved) for saved in saved_attempts) or unreadable_holds(state, pane, session, bound):
                missing.append(Unchecked(f'{state}: cannot be read ({error}); fix or remove it'))
        finally:
            db.close()
    # Every session answers for its own launches; a worker (its launch set ZLOOP_WORKER) not for its siblings.
    readable_runs = {state: run for state, run in runs.items() if not isinstance(run, Exception)}
    named = set()
    stale, cards = stale_tabs(pane, session, readable_runs), unfinished_runs(root, session, named)
    return (missing + unregistered(root, pane, session, readable_runs, siblings=not os.environ.get('ZLOOP_WORKER')),
            stale + undelivered(root, pane, session, named) + cards + unreviewed(root, pane, session))


def binding_state(state, pane, session):
    """Whether the binding of a run state's workstream names this session."""
    owner = state.with_name('review.json' if state.parent.parent.name == 'reviews' else script('state_files').BINDING)
    return read_binding(owner, pane, session)[1]


def reaped(cwd, session=None):
    """Close, through the lifecycle's `reap`, each attempt this session answers for whose status is already decided; one
    gap for each closure the lifecycle refuses, and for a run state it could not reap. Never raises."""
    try:
        root = main_checkout(cwd)
    except Exception:  # stop_gaps() names a git that cannot tell.
        return []
    if root is None:
        return []
    pane = os.environ.get('HERDR_PANE_ID')
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
                mine = {saved['attempt'] for saved in saved_attempts if owns(saved, pane, session)}
                if not mine:
                    continue
                found = module.reap(db, state, lambda saved: saved['attempt'] in mine,
                                    budget=deadline - time.monotonic())
            finally:
                db.close()
        except Exception as error:  # A check that cannot run is a named gap, never a pass; gaps() names an unreadable state.
            missing += [Unchecked(f'could not check settled workers in {state}: {error!r}')] if mine else []
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
            board = pointed(root)  # The checkout whose router judges a local review.
            fields = script('front_matter').parse(report.read_text()) or {}
            blocker = fields.get('blocker') not in (None, '', 'none')
            routed = board.local_review_problems(binding) if blocker else []
            if blocker and not routed:  # The router still finds no reviewer to wait on.
                continue
            attempts = read_state(state)[0] if state.is_file() and (blocker or fields.get('review_by') != 'local') else []
            settled = {(saved.get('result') or {}).get('status') for saved in attempts
                       if saved.get('job') == 'diff-review' and
                       saved.get('head') == (saved.get('result') or {}).get('head') == fields.get('review_head') and
                       (saved.get('herdr') or saved.get('verified') and saved.get('startup_evidence')
                        or saved.get('submitted'))}
            if blocker and settled and settled <= {'failed', 'blocked', 'cancelled'}:
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
            missing.append(Unchecked(f'{path}: could not check review: {error}; rerun review_target.py for this target'))
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
    if any(key in saved and saved[key] is not None and not text(saved[key]) for key in ('orchestrator',)):
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


def owns(saved, pane, session):
    """Whether this session registered the attempt: by host session before pane. A host started inside another's shell
    exports the outer host's session too, so each exported session counts."""
    return any(same_orchestrator({'pane': saved.get('orchestrator'), 'session': known}, pane, session)
               for known in lifecycle().recorded_sessions(saved) or [None])


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
    what keeps its workstream from delivered. Another workstream's problems are not this run's to fix."""
    terminal, slug, board = state['terminal'], path.parent.name, state.get('board', '')
    if script('autopilot_card').tracker_url(board):
        return []
    if board != 'local':
        return [f"{path}: this session's autopilot run records `terminal: {terminal}` with no board: write `board: local` "
                "or the tracker's URL in the card's State"]
    return delivery_gap(root, slug, f"{path}: this session's autopilot run records `terminal: {terminal}` while its "
                                    'workstream is not delivered: ', '; finish what it names, or record the terminal state '
                                                                      'that fits')


def delivery_gap(root, slug, lead, tail):
    """The gap (a list of one, or none) naming what keeps workstream `slug` from delivered, as
    `next_tickets.undelivered` names it for both delivery holds and the panel, worded `lead`, the problems, `tail`. A
    board that could not be checked is a failed check."""
    try:
        board = pointed(root)
        problems = board.undelivered(slug)
    except Exception as error:
        return [Unchecked(f"could not check delivery of {slug}: {error}")]
    return [f"{lead}{'; '.join(problems)}{tail}"] if problems else []


def unfinished_runs(root, session, named):
    """Name each confirmed autopilot run card of this host session that lacks one terminal state or its evidence,
    unless a live worker of the card's workstream holds a running wait that will wake the host, and in such a run
    what `autopilot_card.py phase` would refuse; and each that records a delivered state its local board refuses, whose
    workstream is added to `named`."""
    missing = []
    for path in sorted(root.glob('.scratch/*/autopilot.md')):
        life = lifecycle()
        state = life.card_state(path)
        if not (session in life.card_sessions(state) and life.confirmed(state)):
            continue
        if state.get('terminal') in life.TERMINAL_STATES and state.get('evidence'):
            if state['terminal'] in life.ENDED and (found := delivery_gaps(root, path, state)):
                missing += found
                named.add(path.parent.name)
            continue
        missing += board_gaps(root, path, state)
        if not awaited(path.with_name('reports') / 'workers.sqlite'):
            missing.append(f"{path}: this session's autopilot run has no terminal state or no evidence; record "
                           f"`terminal:` as exactly one of {', '.join(f'`{name}`' for name in life.TERMINAL_STATES)} and "
                           "`evidence:` in the card's State")
    return missing


def stale_tabs(pane, session, runs):
    """Name each collected attempt whose recorded Herdr tab its session still lists, for the session bound to
    orchestrate its workstream or, with none bound, the pane that registered it, however old the run: one neither
    retained nor closed; as a reminder, a retained one that nothing follows (its run has nothing unfinished, it is its
    conversation's latest attempt and no design hold waits on the user for its assignment)."""
    listed, missing = {}, []
    for state, (saved_attempts, _, registered) in runs.items():
        # An unreadable binding decides nothing here (undelivered names it); the registering pane does.
        binding, mine = read_binding(state.with_name(script('state_files').BINDING), pane, session)
        bound = mine if binding else None
        try:
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
            mine = bound if bound is not None else owns(saved, pane, session)
            if not (herdr.get('tab_id') and saved.get('result') and not saved.get('closed') and mine):
                continue
            retained = saved.get('retained')
            if retained and (
                    not saved['owned'] or unfinished or saved.get('assignment') in held
                    or latest[(saved['runtime'], saved['native_session'])] != saved['attempt']):
                continue  # A user's worker, or one its run, a later attempt or the user's ruling may still use.
            where = (herdr['session'], herdr['workspace_id'])
            if where not in listed:
                try:
                    # None: the workspace is closed or its session stopped, which close its tabs.
                    tabs = asked(*where[:1], 'tab', 'list', '--workspace', where[1],
                                 absent=('workspace_not_found', 'server_not_running'))
                    listed[where] = set() if tabs is None else {tab['tab_id'] for tab in tabs['result']['tabs']}
                except Exception as error:
                    listed[where] = set()
                    missing.append(Unchecked(f'could not check worker tabs: {error}'))
            if herdr['tab_id'] in listed[where] and retained:
                missing.append(Reminder(f"{state}: {saved['attempt']}: retained for \"{retained['next_use']}\" in Herdr tab "
                                        f"{herdr['tab_id']}, and nothing in its run follows it: register that next attempt "
                                        f'in this worker, raise a design hold for the decision it waits on, or retire the '
                                        f'worker ({RETIRE})'))
            elif herdr['tab_id'] in listed[where]:
                missing.append(f"{state}: {saved['attempt']}: Herdr tab {herdr['tab_id']} is still open after "
                               f'collection; retire the worker or record its retention ({COLLECT})')
    return missing


def open_holds(state):
    """The assignments of a run state with a design hold not yet ruled on; none in a state that never held one."""
    db = sqlite3.connect(f'{state.as_uri()}?mode=ro', uri=True, timeout=5)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'holds'").fetchone():
            return set()
        return {row[0] for row in db.execute('SELECT assignment FROM holds')}
    finally:
        db.close()


def asked(session, *args, absent=()):
    """Herdr's answer to a command on a server session (None: the caller's own); None when it fails with one of the
    `absent` error codes, an error naming the failure for any other."""
    code, response = lifecycle().herdr(session, *args, timeout=HERDR_SECONDS)
    if not code:
        return response
    if (response.get('error') or {}).get('code') in absent:
        return None
    raise RuntimeError(f'herdr {" ".join(args[:2])} exited {code}: {json.dumps(response)}')


def undelivered(root, pane, session, named):
    """Name each workstream this session was bound to orchestrate (next_tickets.py --orchestrate) whose delivery fails,
    except those in `named`: a run card already names them."""
    missing = []
    for path in sorted(root.glob(f'.scratch/*/reports/{script("state_files").BINDING}')):
        slug = path.parent.parent.name
        binding, mine = read_binding(path, pane, session)
        if not mine:
            continue
        if binding is None:
            missing.append(f'{path}: the orchestrator binding cannot be read; rebind with '
                           f'`python3 {ROOT}/scripts/next_tickets.py --repo {root} {slug} --orchestrate`')
            continue
        if slug in named:
            continue
        try:
            board = pointed(root)
            tickets = board.load()[0]
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
            missing += delivery_gap(root, slug, f'{slug}: delivery incomplete under autofix: ',
                               f'. Continue the autofix handoff ({HANDOFF}) until `python3 {ROOT}/scripts/next_tickets.py '
                               f'--repo {root} {slug} --delivery` passes, or record `blocker: <reason>` in '
                               f'{path.with_name("spec-review.md")}')
        except Exception as error:
            missing.append(Unchecked(f'could not check delivery of {slug}: {error}'))
    return missing


def awaited(state):
    """Whether a live worker of the run holds a running wait with a wake channel: the host is told when it settles, so
    its delivery is not stuck."""
    module = lifecycle()
    try:
        saved_attempts = read_state(state)[0]
    except Exception:  # A state that is missing or cannot be read shows no running wait.
        return False
    closed = {(saved['runtime'], saved['native_session']) for saved in saved_attempts if saved.get('closed')}
    return any(not saved.get('result') and (saved['runtime'], saved['native_session']) not in closed
               and module.watching(saved) and not module.wake_gap(saved) for saved in saved_attempts)


def read_binding(path, pane, session):
    """The binding and whether it names this session; malformed text holds only the IDs it names whole."""
    binding, raw = script('state_files').read_binding(path)
    return binding, (any(same_orchestrator({'pane': binding.get('pane'), 'session': known}, pane, session)
                         for known in binding['sessions'] or [None]) if binding is not None
                     else names(raw, pane, session))


def same_orchestrator(theirs, pane, session):
    """Whether a recorded orchestrator ({pane, session}) is this session: by host session alone when both sides know
    one, since a Codex session's HERDR_PANE_ID can come from another pane (its shared app-server's) and a restarted
    Herdr may reuse a pane ID; else the pane decides."""
    if theirs['session'] and session:
        return theirs['session'] == session
    return bool(pane) and theirs['pane'] == pane


def server_of(saved):
    """The Herdr server session an attempt's worker runs in, when its placement recorded one."""
    return (saved.get('herdr') or {}).get('session')


def clears(saved, launch):
    """Whether an attempt accounts for a logged launch: its worker is the launch's pane or agent name, in its Herdr
    server (an unknown launch server or an attempt naming no server matches any), and a host session
    recorded at registration, or one whose launches it was recorded to clear, is the one that launched it when both
    know one; else the orchestrator recorded at registration is the pane that launched it."""
    if saved['worker'] not in (launch['agent'], launch['pane']):
        return False
    if launch.get('herdr_session') and server_of(saved) not in (None, launch['herdr_session']):
        return False
    if lifecycle().recorded_sessions(saved) and launch['orchestrator']['session']:
        return launch['orchestrator']['session'] in lifecycle().recorded_sessions(saved) + saved.get('launch_sessions', [])
    return saved.get('orchestrator') == launch['orchestrator']['pane']


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
    return asked(server, 'pane', 'get', pane,
                 absent=('pane_not_found', 'workspace_not_found', 'server_not_running')) is not None


def unregistered(root, pane, session, runs, siblings=True):
    """Name each Herdr worker that no attempt in any run and no dismissal of this orchestrator accounts for: each
    launch it logged into a ticket worktree or an unknown cwd (an attempt of another host session never accounts for
    it when both know one), with `siblings` each other live agent in a ticket worktree of its Herdr workspace; and
    each unreadable log line it names; and each pane of a launch it dismissed that is still open, unless dismissed
    with `--keep`."""
    attempts = [saved for saved_attempts, _, _ in runs.values() for saved in saved_attempts]
    by_name = {}
    for saved in attempts:
        by_name.setdefault(saved['worker'], []).append(saved)
    placed = {(server_of(saved), saved['worker']) for saved in attempts}
    # What this session registered by name: in the Herdr server and tab it recorded, when it recorded them.
    mine = {(server_of(saved), saved['worker'], (saved.get('herdr') or {}).get('tab_id'))
            for saved in attempts if owns(saved, pane, session)}
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
                and not any(clears(saved, launch) for name in (launch['agent'], launch['pane'])
                            for saved in by_name.get(name, []))
                and ('cwd' not in launch or Path(launch['cwd']).resolve().is_relative_to(worktrees))]
    workers |= {handle for launch in launches for handle in (launch['agent'], launch['pane'])}  # Named once.
    workers |= {value for record in records if 'dismissed' in record  # This orchestrator's dismissals cover live agents.
                and same_orchestrator(record['by'], pane, session) for value in record['dismissed'].values()}
    workspace, failed, still_open, seen = os.environ.get('HERDR_WORKSPACE_ID'), [], [], set()
    for launch in dismissed:  # Each pane a dismissal of this orchestrator left open, named once.
        where = (launch.get('herdr_session'), launch['pane'])
        if launch['pane'] and where not in seen and same_orchestrator(launch['orchestrator'], pane, session):
            seen.add(where)
            try:
                still_open += [f"{launch['pane']}: dismissed launch is still open; close it, or dismiss with --keep "
                               '<reason>'] if pane_open(*where) else []
            except Exception as error:  # A check that cannot run is a named gap, never a pass.
                gap = Unchecked(f'could not check Herdr workers: {error}')
                failed += [gap] if gap not in failed else []
    try:
        agents = asked(None, 'agent', 'list')['result']['agents'] if siblings and workspace and worktrees.is_dir() else []  # No worktrees, no workers.
    except Exception as error:  # A check that cannot run is a named gap, never a pass.
        agents, failed = [], failed + [Unchecked(f'could not check Herdr workers: {error}')]
    # A name is reused freely, so an attempt accounts for a live agent by its pane, or by its name only when this session
    # registered it (another session's launch is told by its pane, above).
    live = [agent for agent in agents
            if agent.get('workspace_id') == workspace and agent['pane_id'] != pane and agent.get('cwd')
            and Path(agent['cwd']).resolve().is_relative_to(worktrees)
            and not workers & {agent.get('name'), agent['pane_id']} and not placed & {(server, agent['pane_id']) for server in (None, lifecycle().own_server())}
            and not {(server, agent.get('name'), tab) for server in (None, lifecycle().own_server())
                     for tab in (None, agent.get('tab_id'))} & mine]
    return ([f"{log}: {launch['agent']} (pane {launch['pane']}): launched without a registered attempt"
             for launch in launches]
            + [f"Herdr agent {agent.get('name')} (pane {agent['pane_id']}) runs in {agent['cwd']} "
               'without a registered attempt' for agent in live] + still_open + unreadable + failed)


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
        if held >= (1 if all(isinstance(gap, Reminder) for gap in missing) else MAX_HOLDS):
            if saved.get('told'):
                return False, None  # Said once; later stops of the same gaps pass quietly.
            files.write_atomic(path, json.dumps({'gaps': missing, 'holds': held, 'told': True}))
            return False, (f"zloop yield gate gave up holding the stop after {held} {'hold' if held == 1 else 'holds'} "
                           'of the same gaps; stopping with:\n' + ''.join(f'- {gap}\n' for gap in missing))
        files.write_atomic(path, json.dumps({'gaps': missing, 'holds': held + 1}))
        return True, None


def reason(workers, own, session=None):
    """The hold's message: every gap, the worker advice only for a worker gap that is not a failed check, and the retry
    advice for a failed check, which registering or dismissing cannot clear."""
    message = 'Stopping now leaves these gaps:\n' + ''.join(f'- {gap}\n' for gap in workers + own)
    failed = [gap for gap in workers + own if isinstance(gap, Unchecked)]
    advice = []
    if [gap for gap in workers if gap not in failed]:
        dismiss = f"python3 {ROOT}/hooks/herdr_launch.py dismiss --pane <pane>{f' --session {session}' if session else ''}"
        advice.append(f'Register each launched worker, or dismiss a launch that needs none with `{dismiss} --reason <why>` '
                      + f'({PREPARE}). '
                      + 'Arm a notified wait, recover the receipt or collect the '
                      + 'result (for a worker that is gone, a failed or cancelled result whose report says what was lost) '
                      + f'before stopping ({GUIDE}).')
    if failed:
        advice.append('A check that could not run clears when its named cause is fixed: fix it, then retry the stop. '
                      'Or run with `ZLOOP_YIELD_HOOK=off` for this stop.')
    return message + ' '.join(advice)


def stop_gaps(cwd, session, closing=True):
    """(worker gaps, own gaps) of a stop, with the closures `reaped` could not make; `check` (closing False) settles none."""
    closed = reaped(cwd, session) if closing else []
    try:
        workers, own = gaps(cwd, session)
    except Exception as error:
        workers, own = [Unchecked(f'could not check workers: {error}')], []
    return workers, own + closed


def main():
    if os.environ.get('ZLOOP_YIELD_HOOK') == 'off':
        return 0
    if sys.argv[1:2] in (['check'], ['stop']) and len(sys.argv) in (3, 4):
        stop, cwd, session = sys.argv[1] == 'stop', Path(sys.argv[2]), (sys.argv[3:] or [None])[0]
        workers, own = stop_gaps(cwd, session, stop)
        report = {'missing': workers + own, 'reason': reason(workers, own, session)}
        if stop:
            hold, notice = count_hold(cwd, session, workers + own)
            report.update(hold=hold, notice=notice)
        print(json.dumps(report))
        return 0
    if sys.argv[1:] not in (['claude'], ['codex']):
        sys.exit('usage: yield_gate.py claude|codex < stop-hook-input, or yield_gate.py check|stop <cwd> [session]')
    payload = json.load(sys.stdin)
    cwd, session = Path(payload.get('cwd') or os.getcwd()), payload.get('session_id')
    workers, own = stop_gaps(cwd, session)
    hold, notice = count_hold(cwd, session, workers + own)
    if hold:
        print(json.dumps({'decision': 'block', 'reason': reason(workers, own, session)}))
    elif notice:
        print(json.dumps({'systemMessage': notice}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
