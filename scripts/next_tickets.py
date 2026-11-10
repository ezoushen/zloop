#!/usr/bin/env python3
"""zloop scheduler. Run from any directory; state stays in the main checkout.

  next_tickets.py --repo /path/to/repo [slug] [--max-live N] [action]

No action: JSON candidates. No slug: all workstreams. Other workstreams still hold locks.
Actions: --board, --counts, --check, --accept TICKET,
         --can-verify TICKET, --delivery, --set TICKET status=VALUE (or worker=ID, start=SHA), --orchestrate.
--set TICKET status=blocked also needs blocker=REASON, which the ticket's front matter keeps. Entering implementing
records start: (the workstream branch head) when the ticket has none; start never changes once recorded. A passing
--accept records accepted: (the head it accepted); status=done needs it, landed on the workstream branch
(its commits there as they are or as patch-equivalent rebased copies).
--accept TICKET --delivered-by URL accepts it instead by the PRs gh reports merged and the CI runs gh reports
successful, recorded as delivered_by:; status=done asks gh again.
Only --set may be combined with --counts. --accept of a delegated ticket (its branch is not the workstream branch)
needs the report a collected worker attempt of the workstream returned completed, unchanged since. It also runs the
standing measurements (.scratch/<slug>/measurements.json) at the ticket head, in a clean checkout, and refuses a
missing or worse value.
--delivery names each failing field. --orchestrate [--autofix] records the calling session (its Herdr pane and host
session IDs) and the repair authorization in .scratch/<slug>/reports/orchestrator.json for the stop hooks; with no slug
only workstreams with active work (a live workstream branch, or a ticket not done), and it refuses a session it cannot
identify.

Tickets: .scratch/<slug>/issues/NN-title.md, with restricted YAML front matter
(key: scalar and key: [a, b]). Only the orchestrator changes machine state.
Cross-workstream prerequisites require the entire source workstream done and its retained
branch merged into main and the target workstream branch. --check detects missing
edges and cycles between tickets or workstreams. One orchestrator per checkout.
"""
import argparse
import hashlib
import json
import math
import os
import posixpath
import re
import shlex
import sys
import sqlite3
import subprocess
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capability_probe  # noqa: E402
import front_matter  # noqa: E402
import state_files  # noqa: E402
import title_rules  # noqa: E402
import worker_lifecycle  # noqa: E402

ROOT = Path.cwd()
ISSUES = ROOT / ".scratch"
LIVE = {"implementing", "reviewing", "fixing", "verifying"}
TEMPLATE = Path(__file__).resolve().parents[1] / "skills" / "refine" / "ticket-template.md"
# Seconds the router may take when a local review is judged: once per host in a process (ROUTED), and
# not again once it went unanswered, inside the hosts' 20 s stop hook and the panel's 8 s read.
ROUTE_TIMEOUT = 5
ROUTED = {}
PR_URL = re.compile(r"https://[^/\s]+/[^/\s]+/[^/\s]+/pull/[0-9]+")  # A pull request, by its own address.
RUN_URL = re.compile(r"https://([^/\s]+/[^/\s]+/[^/\s]+)/actions/runs/([0-9]+)")  # A CI run, by its own address.
TERMINAL = {"done"}
STATUSES = {"ready", "blocked"} | LIVE | TERMINAL
TICKET_KEYS = {"blocked_by", "touches", "devices", "worker"}  # A front matter holding one is a ticket's.
MAX_LIVE = 8  # Tickets live at once, unless --max-live or MAX_LIVE in the environment says otherwise (main checks it).
# Critical path first. TICKET_PRIORITY=spec-a,spec-b names the specs to drain first; default is name order.
PRIORITY = [s for s in os.environ.get("TICKET_PRIORITY", "").split(",") if s]
MEASURE_TIMEOUT = 600  # seconds a measurement command may run unless its entry sets timeout_seconds


# What makes the board invalid, and the workstreams it bears on; a refused file also names the tickets it may hold.
Problem = namedtuple("Problem", "text slugs unread", defaults=((),))


def load():
    """The board's tickets by name, and the ticket files it refuses as `Problem`s of their folder's workstream."""
    tickets, refused = {}, []
    for path in sorted(ISSUES.glob("*/issues/*.md")):
        refusal = None
        try:
            fm = front_matter.parse(path.read_text())
        except (OSError, ValueError) as error:  # Unreadable, or not text: refused like any other file.
            fm, refusal = None, f"{path}: cannot be read: {error}"
        if fm and "ticket" in fm:
            if not isinstance(fm["ticket"], str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*/[0-9]+", fm["ticket"]):
                refusal = f"{path}: invalid ticket id {fm['ticket']!r}"
            elif fm["ticket"] in tickets:
                refusal = f"{path}: duplicate ticket {fm['ticket']}"
            elif path.parent.parent.name != spec_of(fm["ticket"]):
                refusal = f"{path}: ticket workstream differs from its directory"
            else:
                refusal = next((f"{path}: {key} must be a bracketed list" for key in ("blocked_by", "touches", "devices")
                                if not isinstance(fm.get(key), list)), None)
        elif fm and set(fm) & TICKET_KEYS:
            refusal = f"{path}: missing ticket front matter"
        if refusal:
            slug = path.parent.parent.name
            named = [fm["ticket"]] if fm and isinstance(fm.get("ticket"), str) else []
            refused.append(Problem(refusal, (slug,), (f"{slug}/{path.name.split('-')[0]}", *named)))
        elif fm and "ticket" in fm:
            fm["path"] = str(path.relative_to(ROOT))
            tickets[fm["ticket"]] = fm
    return tickets, refused


def load_whole():
    """The board's tickets; exits naming the first file it refuses."""
    tickets, refused = load()
    if refused:
        sys.exit(refused[0][0])
    return tickets


def delivered(fm):
    """The pull requests and CI runs a ticket's `delivered_by` names: its list, or its one value."""
    value = fm.get("delivered_by")
    return value if isinstance(value, list) else [value] if value else []


def accepted(fm):
    """Whether a ticket carries what only an acceptance writes: an accepted commit with its report's digest, or the
    pull requests and CI runs that delivered it. The form only; `--set status=done` judged the evidence."""
    urls = delivered(fm)
    return bool(re.fullmatch("[0-9a-f]{40}", str(fm.get("accepted") or ""))
                and re.fullmatch("[0-9a-f]{64}", str(fm.get("accepted_report") or ""))) or bool(
        urls and all(PR_URL.fullmatch(str(url)) or RUN_URL.fullmatch(str(url)) for url in urls))


def check(tickets, accepting=False):
    """Print what makes the board invalid; whether it is valid."""
    bad = problems(tickets, accepting=accepting)
    for problem in bad:
        print(problem.text, file=sys.stderr)
    return not bad


def bearing(found, slug):
    """The text of the problems among `found` that bear on workstream `slug`."""
    return [problem.text for problem in found if slug in problem.slugs]


def mine(ticket, text):
    return Problem(f"{ticket}: {text}", (spec_of(ticket),))


def problems(tickets, refused=(), accepting=False):
    """What makes the board invalid, [] when nothing does: the `refused` files `load` returned, then each problem. A
    done ticket needs its acceptance, except while `--accept` runs (`accepting`): it gives those tickets their
    acceptance, one at a time. A blocker a refused file may hold is not called missing: the board cannot tell."""
    unread = {name for problem in refused for name in problem.unread}
    bad = list(refused) + [mine(t, f"status {fm.get('status')!r} is not one of {sorted(STATUSES)}")
                           for t, fm in tickets.items() if not isinstance(fm.get('status'), str) or fm['status'] not in STATUSES]
    bad += [mine(t, f"status done with no acceptance; accept it with --accept {t}, or --accept {t} --delivered-by <url> "
                    "for a merged PR or successful CI run")
            for t, fm in tickets.items() if fm.get('status') == 'done' and not accepting and not accepted(fm)]
    bad += [mine(t, f"blocked_by {dep} does not exist") for t, fm in tickets.items()
            for dep in fm.get("blocked_by", []) if dep not in tickets and dep not in unread]
    allowed = ", ".join(capability_probe.NEEDS)
    for t, fm in tickets.items():
        if "needs" in fm and not isinstance(fm["needs"], list):
            bad.append(mine(t, f"needs must be a bracketed list of {allowed}"))
        else:
            bad += [mine(t, f"needs {need!r} is not one of {allowed}") for need in fm.get("needs", [])
                    if need not in capability_probe.NEEDS]
    bad += [mine(t, "no blocked_by key (write blocked_by: [] when nothing gates it)") for t, fm in tickets.items()
            if "blocked_by" not in fm]
    bad += [mine(t, f"number differs from its filename {Path(fm['path']).name}") for t, fm in tickets.items()
            if not Path(fm["path"]).name.startswith(t.split("/")[-1] + "-")]
    graphs = {"ticket": {t: fm.get("blocked_by", []) for t, fm in tickets.items()},
              "workstream": {spec_of(t): set() for t in tickets}}
    for t, fm in tickets.items():
        graphs["workstream"][spec_of(t)].update(spec_of(d) for d in fm.get("blocked_by", [])
                                           if spec_of(d) != spec_of(t))
    for label, graph in graphs.items():
        bad += [Problem(f"{label} dependency cycle: {' -> '.join(cycle)}", tuple(sorted({spec_of(node) for node in cycle})))
                for cycle in cycles(graph)]
    return bad


def cycles(graph):
    """Cycles of `graph` (each node to the nodes it waits on) that together pass through every node on one, each a
    path of nodes, each blocking the next, back to its first: every ticket or workstream in a cycle is named."""
    after, left = {}, {node: set(blockers) for node, blockers in graph.items()}
    for node, blockers in graph.items():
        for blocker in blockers:
            after.setdefault(blocker, set()).add(node)
            left.setdefault(blocker, set())
    free = [node for node, blockers in left.items() if not blockers]
    while free:  # What can be ordered is on no cycle; what is left is on one or waits on one.
        node = free.pop()
        for waiter in after.get(node, set()):
            left[waiter].discard(node)
            if not left[waiter]:
                free.append(waiter)
        del left[node]
    found, named = [], set()
    for start in sorted(left):
        if start in named:
            continue
        came, queue = {start: None}, [start]
        for node in queue:  # Breadth first, the queue growing as it goes: the shortest way from start back to it.
            if start in after.get(node, set()):
                path = [node]
                while came[path[-1]] is not None:
                    path.append(came[path[-1]])
                found.append([*reversed(path), start])
                named.update(path)
                break
            for waiter in sorted(after.get(node, set()) & left.keys()):
                if waiter not in came:
                    came[waiter] = node
                    queue.append(waiter)
    return found


def undelivered(slug, board=None):
    """What keeps workstream `slug` from delivered, [] when nothing does: its own problems on the board's check, its
    tickets not done, and the evidence `--delivery` judges, unless it finished (its branch deleted after the merge, its
    reviewed heads in main). `board` is `judged()` when the caller has it."""
    tickets, refused, found = board or judged()
    if bearing(refused, slug):
        return [f"it cannot be checked: {text}" for text in bearing(refused, slug)]
    if not any(spec_of(name) == slug for name in tickets):
        new = shlex.join(["python3", str(Path(__file__).resolve()), "--repo", str(ROOT), slug, "--new", "<title>"])
        return [f"its local board has no tickets: write each with `{new}`"]
    problems = bearing(found, slug)
    undone = [f"{name} ({fm.get('status')})" for name, fm in tickets.items()
              if spec_of(name) == slug and fm.get('status') != 'done']
    problems += [f"tickets not done: {', '.join(undone)}"] if undone else []
    if not finished(slug):
        problems += delivery_problems(slug)
    return problems


def judged():
    """The board, its refused files and what its check refuses: (tickets, refused, problems)."""
    tickets, refused = load()
    return tickets, refused, problems(tickets, refused)


def spec_of(name):
    return name.split("/")[0]


def integrated(tickets, name, dependency):
    source = spec_of(dependency)
    if source == spec_of(name):
        return True
    if any(fm["status"] != "done" for t, fm in tickets.items() if spec_of(t) == source):
        return False
    if not delivery_evidence(source):
        return False
    prerequisite = branch_ref(workstream_branch(source))
    if prerequisite is None or not valid_branch_name(workstream_branch(spec_of(name))):
        return False
    target = branch_ref(workstream_branch(spec_of(name)))
    bases = ["refs/heads/main"] + ([target] if target and target != "refs/heads/main" else [])
    return all(subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", prerequisite, base],
                              capture_output=True).returncode == 0 for base in bases)


def review_front_matter(slug):
    report = ISSUES / slug / 'reports' / 'spec-review.md'
    return (front_matter.parse(report.read_text()) or {}) if report.is_file() else None


def valid_branch_name(name):
    return isinstance(name, str) and bool(name) and name != "HEAD" and not name.startswith("-") \
        and "\0" not in name and subprocess.run(
        ["git", "check-ref-format", f"refs/heads/{name}"]).returncode == 0


def branch_ref(name):
    """refs/heads/<name> when name is an existing local branch, else None."""
    if not valid_branch_name(name):
        return None
    ref = f"refs/heads/{name}"
    exists = subprocess.run(["git", "-C", str(ROOT), "show-ref", "--verify", "--quiet", ref])
    return ref if exists.returncode == 0 else None


def workstream_branch(slug):
    """feat/<slug>, or main when the user had the workstream work directly on main."""
    branch = (review_front_matter(slug) or {}).get('workstream_branch')
    return branch if branch not in (None, '', []) else f'feat/{slug}'


def delivery_evidence(slug):
    return not delivery_problems(slug)


def finished(slug):
    """Whether the board records a workstream as delivered and gone: its branch deleted, its reviewed head in main
    (local or the remote's) and a passing E2E recorded at a head in main."""
    fm = review_front_matter(slug) or {}
    return (branch_ref(workstream_branch(slug)) is None and fm.get('e2e_status') == 'passed'
            and all(merged(str(fm.get(key) or '')) for key in ('review_head', 'e2e_head')))


def in_main(sha, ref='refs/heads/main'):
    """Whether sha is a 40-hex commit that main (or ref) contains."""
    return bool(re.fullmatch('[0-9a-f]{40}', sha)) and subprocess.run(
        ['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', sha, ref], capture_output=True).returncode == 0


def merged(sha):
    """Whether local main, or the remote-tracking main as the repository has it, contains sha. It never fetches: the
    stop-hook checks that call it run under a time budget."""
    return in_main(sha) or in_main(sha, 'refs/remotes/origin/main')


def delivery_problems(slug):
    """Structural delivery gate: each recorded field that fails it, [] when none does. The orchestrator must also
    judge the recorded evidence.

    main keeps moving after a workstream delivered on it, so there the reviewed head only has to stay in main.
    """
    fm = review_front_matter(slug)
    if fm is None:
        return [f'no review report at {ISSUES / slug / "reports" / "spec-review.md"}']
    problems = [f'{key}: {fm.get(key) or "missing"} (needs {value})' for key, value in
                (('review_status', 'clean'), ('documentation_status', 'passed'), ('e2e_status', 'passed'))
                if fm.get(key) != value]
    # The worker runs that produced the statuses; a host-written status alone is not evidence.
    unnamed = [key for key in VERDICTS if str(fm.get(key) or '').strip().lower()
               in ('', 'none', 'n/a', 'unknown', 'orchestrator', 'host')]
    problems += [f'{key}: {fm.get(key) or "missing"} names no worker run' for key in unnamed]
    # A status the report already fails is named once, above; one it records as met must be the worker's.
    met = [key for key in VERDICTS if key not in unnamed
           and all(fm.get(field) == required for field, required in VERDICTS[key].items() if required)]
    problems += [problem for key in met for problem in verdict_problems(slug, key, fm)]
    branch = workstream_branch(slug)
    ref = branch_ref(branch)
    head = ref and subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--verify', f'{ref}^{{commit}}'],
                                  capture_output=True, text=True)
    if not head or head.returncode:
        gone = [key for key in ('review_head', 'e2e_head')
                if re.fullmatch('[0-9a-f]{40}', str(fm.get(key) or '')) and not merged(str(fm[key]))]
        update = (f", and its {' and '.join(gone)} {'is' if len(gone) == 1 else 'are'} in no main; update local main "
                  f'from the remote (`git -C {ROOT} fetch origin main`, then fast-forward main to origin/main)'
                  if gone else '')
        return problems + [f'workstream branch {branch} is not an existing branch{update}']
    base = fm.get('review_base', '')
    if not isinstance(base, str) or not re.fullmatch('[0-9a-f]{40}', base):
        return problems + [f'review_base: {base or "missing"} is not a 40-hex commit']
    sha = head.stdout.strip()
    reviewed = str(fm.get('review_head') or '')
    if ref == 'refs/heads/main' and re.fullmatch('[0-9a-f]{40}', reviewed) and subprocess.run(
            ['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', reviewed, sha]).returncode == 0:
        sha = reviewed
    problems += [f'{key}: {fm.get(key) or "missing"} is stale against {branch} head {sha}, so {step}'
                 for key, step in (('review_head', 'review the whole range at that head and name its reviewer attempts '
                                                   'in review_by'),
                                   ('e2e_head', 'name in e2e_by a passing E2E run at that head, rerunning only the '
                                                'checks the commits since the last run can affect'))
                 if fm.get(key) != sha]
    if sha == base:
        problems.append(f'review_head: {sha} is review_base, so nothing was reviewed')
    merge_base = subprocess.run(['git', '-C', str(ROOT), 'merge-base', '--all', base, sha],
                                capture_output=True, text=True)
    if merge_base.returncode or merge_base.stdout.splitlines() != [fm.get('review_diff_base')]:
        problems.append(f'review_diff_base: {fm.get("review_diff_base") or "missing"} is not the merge base of '
                        f'review_base and {branch} head {sha}; run a whole-range final review from review_base '
                        f'{base} at head {sha}, collect its reviewer attempts and set review_by to them')
    return problems


# The statuses each worker field vouches for: a required value, or None for the value the review report records.
VERDICTS = {"review_by": {"review_status": "clean", "documentation_status": "passed", "review_head": None,
                          "review_diff_base": None},
            "e2e_by": {"e2e_status": "passed", "e2e_head": None}}


def review_by_problems(fm, attempts, binding=None):
    """Why review_by lacks completed collected diff-review attempts at review_head, or allowed local review."""
    if fm.get('review_by') == 'local':
        return local_review_problems(binding or {})
    collected = {saved['attempt']: saved for saved in attempts if saved.get('result')}
    names = fm.get('review_by') or 'missing'
    problems = []
    for name in names if isinstance(names, list) else [str(names).strip()]:
        saved = collected.get(name)
        if saved is None:
            problems.append(f'review_by: {name} names no collected worker attempt')
        elif saved['result']['status'] != 'completed':
            problems.append(f"review_by: {name} returned {saved['result']['status']}")
        elif saved.get('job') != 'diff-review':
            problems.append(f'review_by: {name} is not a diff-review attempt')
        elif saved['result'].get('head') != fm.get('review_head'):
            problems.append(f"review_by: {name} head {saved['result'].get('head')} is not review_head {fm.get('review_head')}")
    return problems


def local_review_problems(binding):
    """Why a review the orchestrator did itself does not count. It counts only where `review_target.py` found no
    reviewer route for its host and the router, asked again now, still finds none: a reviewer it routes, on the same
    host where it can, is the one to launch."""
    host, report = binding.get('host'), binding.get('report')
    if ((binding.get('route') or {}).get('status') != 'no_route' or host not in worker_lifecycle.TOP_LEVEL
            or not isinstance(report, str)):
        return ['review_by: local counts only where review_target.py found no reviewer route for its host; launch the '
                'reviewer it routes and name its attempt in review_by']
    if host not in ROUTED:
        ROUTED[host] = rerouted(host, Path(report).parent)
    return ROUTED[host]


def rerouted(host, reports):
    """What the router, asked now for `host`'s diff-review as `review_target.py` asks it, says of a local review: []
    when it still finds no reviewer, else why the review does not count."""
    silent = [f'review_by: local cannot be judged, the router did not answer within {ROUTE_TIMEOUT} s']
    if silent in ROUTED.values():  # One wait for a router that does not answer, however many hosts ask.
        return silent
    import agent_models
    try:
        route = agent_models.ask_route(ROOT, host, reports, ROUTE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return silent
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return [f'review_by: local cannot be judged, the router failed: {error}']
    found = route.get('result')
    if route['status'] == 'no_route':
        return []
    if route['status'] == 'selection_required':
        return [f"review_by: local, but the router has reviewers for {host} to choose from "
                f"({', '.join(found.get('eligible_candidates') or [])}): choose one with --candidate on the route "
                'command, launch it and name its attempt in review_by']
    where = (f'a {host} native subagent' if found.get('native_dispatch') else
             f"{found.get('target_runtime', 'its runtime')} ({(found.get('settings') or {}).get('model', 'its model')}) "
             f"through {found.get('executor')}")
    return [f'review_by: local, but the router now routes diff-review from {host} to {where}: launch that reviewer '
            'and name its attempt in review_by']


def verdict_problems(slug, key, fm):
    """Each status the review report records must be the one every named worker attempt returned, as collected,
    with its report still saying so; e2e_by may name run_check logs of checks the orchestrator ran instead."""
    saved_attempts = workstream_attempts(slug)
    if key == 'review_by':
        binding_path = ISSUES / slug / 'reports' / 'review.json'
        binding = json.loads(binding_path.read_text()) if binding_path.is_file() else {}
        problems = review_by_problems(fm, saved_attempts, binding)
        if problems or fm.get('review_by') == 'local':
            return problems
    attempts = {saved["attempt"]: saved for saved in saved_attempts if saved.get("result")}
    problems = []
    for name in fm[key] if isinstance(fm[key], list) else [str(fm[key]).strip()]:
        saved = attempts.get(name)
        if saved is None and key == "e2e_by":
            problems += check_run_problems([name], str(fm.get("e2e_head") or ""))
        elif saved is None:
            problems.append(f"{key}: {name} names no collected worker attempt")
        elif saved["result"]["status"] != "completed":
            problems.append(f"{key}: {name} returned {saved['result']['status']}")
        else:
            problems += returned_verdict_problems(key, name, saved, fm)
    return problems


def returned_verdict_problems(key, name, saved, fm):
    returned = saved["result"].get("verdict", {})
    report = Path(saved["result"]["report"])
    now = worker_lifecycle.report_verdict(report) if report.is_file() else {}
    problems = []
    for field, required in VERDICTS[key].items():
        wanted = required or str(fm.get(field) or "")
        value = returned.get(field, "missing")
        if value != wanted:
            problems.append(f"{key}: {name} returned {field}: {value}, not {wanted}")
        elif now.get(field, "missing") != value:
            problems.append(f"{key}: {name} report now says {field}: {now.get(field, 'missing')}, not {value}")
    return problems


def check_run_problems(logs, head):
    """E2E the orchestrator ran itself: each named run_check log passed, at the recorded E2E head."""
    problems = []
    for log in logs:
        summary = Path(f"{log}.summary.json")
        try:
            ran = json.loads(summary.read_text()) if Path(log).is_absolute() else None
        except (OSError, ValueError):
            ran = None
        if not isinstance(ran, dict):
            problems.append(f"e2e_by: {log} names no collected worker attempt or check run")
        elif ran.get("exit_code") != 0:
            problems.append(f"e2e_by: {log} exited {ran.get('exit_code')}")
        elif ran.get("head") != head:
            problems.append(f"e2e_by: {log} ran at {ran.get('head') or 'no commit'}, not {head}")
    return problems


def unblocked(tickets, name, known):
    """Whether the ticket's blockers let it start: each done, and one of another workstream also integrated. `known`
    keeps each integration verdict for the caller's pass over the board: it depends only on the two workstreams."""
    blockers = tickets[name].get("blocked_by", [])
    if not all(tickets[d]["status"] in TERMINAL for d in blockers):
        return False
    for dependency in blockers:
        pair = (spec_of(dependency), spec_of(name))
        if pair not in known:
            known[pair] = integrated(tickets, name, dependency)
        if not known[pair]:
            return False
    return True


def live_cap():
    """The positive live cap from MAX_LIVE, or the default; refuse an invalid setting."""
    value = os.environ.get('MAX_LIVE', MAX_LIVE)
    try:
        cap = int(value)
        if cap >= 1:
            return cap
    except ValueError:
        pass
    raise ValueError(f'MAX_LIVE must be a positive integer, got {value!r}')


def ready(tickets, slug=None, max_live=MAX_LIVE):
    live = [fm for fm in tickets.values() if fm["status"] in LIVE]
    busy_areas = {a for fm in live for a in fm.get("touches", [])}
    out, known = [], {}
    order = sorted(tickets, key=lambda n: (PRIORITY.index(n.split("/")[0]) if n.split("/")[0] in PRIORITY else 99, n))
    for name in order:
        if len(live) + len(out) >= max_live:
            break
        fm = tickets[name]
        if fm["status"] != "ready" or (slug and spec_of(name) != slug):
            continue
        if not unblocked(tickets, name, known):
            continue
        if busy_areas & set(fm.get("touches", [])):
            continue
        branch = ticket_branch(spec_of(name), Path(fm["path"]))
        out.append({**fm, "branch": branch, "worktree": f".worktrees/{branch.replace('/', '-', 1)}"})
        busy_areas |= set(fm.get("touches", []))
    return out


def spec_devices(tickets, spec):
    """E2E runs once per spec, so a spec's run needs the union of its tickets' devices."""
    return {d for fm in tickets.values() if spec_of(fm["ticket"]) == spec for d in fm.get("devices", [])}


def can_verify(tickets, name):
    """A device is held only for the duration of a spec's `verifying` stage."""
    want = spec_devices(tickets, spec_of(name))
    held = {d: t for t, fm in tickets.items() if fm["status"] == "verifying" and spec_of(t) != spec_of(name)
            for d in spec_devices(tickets, spec_of(t))}
    clash = {d: held[d] for d in want & held.keys()}
    if clash:
        print(f"{name}: wait, {', '.join(f'{d} held by {t}' for d, t in sorted(clash.items()))}")
        return False
    print(f"{name}: devices free ({', '.join(sorted(want)) or 'none needed'})")
    return True


def report_problems(text):
    """The structural half of the Implementer exit criterion: what a report must carry before anyone reads it."""
    problems = []
    if not re.search(r"^base:\s*`?[0-9a-f]{40}`?", text, re.M):
        problems.append("no `base: <40-hex sha>` line")
    if "## Implemented" not in text:
        problems.append("no `## Implemented` section")
    if not re.search(r"- \[[ x]\]", text):
        problems.append("no ticket checkbox accounted for")
    return problems


def report_branch(slug, nn, text):
    """(branch, recorded): the branch a ticket report's header names under `branch:`, else <slug>/<its ticket file's
    stem without NN->."""
    header = text.split("\n## ", 1)[0]
    recorded = re.search(r"^branch:\s*`?([^\s`]+)`?\s*$", header, re.M)
    if recorded:
        return recorded.group(1), True
    return ticket_branch(slug, next((ISSUES / slug / "issues").glob(f"{nn}-*.md"), Path(f"{nn}-ticket.md"))), False


def ticket_branch(slug, path):
    """A delegated ticket's branch: <slug>/<its ticket file's stem without NN->; its worktree turns the / into -."""
    return f"{slug}/{path.stem.partition('-')[2] or path.stem}"


def accept(tickets, name):
    """Deterministic verify node: the report exists in the main checkout with its evidence, and the branch moved.
    Any failure withdraws an earlier acceptance."""
    if verify(tickets, name):
        return True
    fm = tickets[name]
    withdrawn = {"accepted": None, "accepted_report": None} if fm.get("accepted") else {}
    if not accepted({**fm, **withdrawn}):
        if fm.get("status") == "done":  # Done only while accepted.
            withdrawn.update(status="blocked", blocker="its acceptance failed again; restore the evidence, then --accept it")
    if withdrawn:
        write_fields(fm, withdrawn)
    return False


def verify(tickets, name):
    slug, nn = name.split("/")
    report = ISSUES / slug / "reports" / f"{nn}.md"
    if not report.exists():
        print(f"{name}: no report at {report}", file=sys.stderr)
        return False
    text = report.read_text()
    problems = report_problems(text)
    base = re.search(r"^base:\s*`?([0-9a-f]{40})", text, re.M)
    branch, recorded = report_branch(slug, nn, text)
    ref = branch_ref(branch)
    if base and ref is None:
        problems.append(f"branch {branch} is not an existing branch" if recorded else
                        f"branch {branch}, the board's name for this ticket, does not exist; name the ticket branch so, "
                        "or record its branch as `branch: <name>` in the report's first lines")
    elif base:
        count = subprocess.run(["git", "-C", str(ROOT), "rev-list", "--count", f"{base.group(1)}..{ref}"],
                               capture_output=True, text=True)
        if count.returncode != 0:
            problems.append(f"branch {branch} or base not found: {count.stderr.strip()}")
        elif subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", base.group(1), ref],
                            capture_output=True).returncode != 0:
            problems.append(f"base is not an ancestor of {branch}")
        elif int(count.stdout) == 0:
            problems.append(f"no commit past base on {branch}")
        elif recorded and not listed_commit_on(text, base.group(1), ref):
            problems.append(f"no listed commit under ## Implemented is past base on {branch}")
    notes = []
    if not problems:
        problems = returned_problems(slug, report, ref, branch, delegated=ref != branch_ref(workstream_branch(slug)))
    if not problems:
        problems, notes = measurement_results(tickets[name], ref, text)
    for p in problems:
        print(f"{name}: {p}", file=sys.stderr)
    if not problems:
        for note in notes:
            print(f"{name}: {note}")
        # The head and the report that passed: done needs both, the head landed on the workstream branch.
        write_fields(tickets[name], {"accepted": commit_of(ref), "accepted_report": report_digest(report)})
        print(f"{name}: report and branch carry the evidence; judge the content now")
    return not problems


def workstream_attempts(slug):
    """The worker attempts the workstream's lifecycle state holds, collected or not; none without a state."""
    state = ISSUES / slug / "reports" / "workers.sqlite"
    if not state.is_file():
        return []
    db = sqlite3.connect(f"{state.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    try:
        return worker_lifecycle.attempts(db)
    finally:
        db.close()


def report_digest(report):
    """The sha256 of a report's bytes, or None when it is gone."""
    return hashlib.sha256(report.read_bytes()).hexdigest() if report.is_file() else None


def returned_problems(slug, report, ref, branch, delegated):
    """A delegated ticket's report, and any report a worker returned, must be one a collected worker attempt of the
    workstream returned completed, unchanged since and at the ticket head: the orchestrator judges a worker's evidence,
    it never writes it."""
    collected = [saved for saved in workstream_attempts(slug) if saved.get("result")]
    # A worker's report may be collected as an immutable snapshot: its content, not its path, vouches for the report.
    digest = report_digest(report)
    returned = [saved for saved in collected if saved["result"].get("report_sha256") == digest]
    shown = report.relative_to(ROOT) if report.is_relative_to(ROOT) else report
    if not returned:
        here = [saved for saved in collected if Path(saved["result"].get("report", "")).resolve() == report.resolve()]
        if here:
            return [f"{shown} changed after {here[-1]['attempt']} returned it"]
        return [f"no collected worker attempt returned {shown}"] if delegated else []
    completed = [saved for saved in returned if saved["result"]["status"] == "completed"]
    if not completed:
        return [f"{returned[-1]['attempt']} returned {returned[-1]['result']['status']}, not a completed ticket"]
    head = commit_of(ref)
    if not any(re.fullmatch("[0-9a-f]{7,40}", saved["result"]["head"]) and commit_of(saved["result"]["head"]) == head
               for saved in completed):
        return [f"{completed[-1]['attempt']} returned its report at {completed[-1]['result']['head']}, not at "
                f"{branch} head {head}"]
    return []


def measurement_results(fm, ref, report):
    """Run the workstream's standing measurements at the ticket head; (problems, notes on what the ticket's `allows`
    excused).

    A ticket may not change what measures a metric (its entry's `paths`) nor make a metric worse than its baseline,
    unless its `allows` covers that metric.
    """
    slug = spec_of(fm["ticket"])
    path = ISSUES / slug / "measurements.json"
    if not path.is_file():
        return [], []
    try:
        entries = read_measurements(path)
    except ValueError as error:
        return [f"measurements.json is unusable: {error}"], []
    branch = ref.removeprefix("refs/heads/")
    checkout = checkout_of(ref)
    if checkout is None:
        return [f"branch {branch} is checked out in no worktree; its head cannot be measured"], []
    problem = not_the_head(branch, checkout)
    if problem:
        return [problem], []
    problem = allowance_problem(fm, slug)
    if problem:
        return [problem], []
    allowed = allowance_pairs(str(fm["allows"])) if "allows" in fm else {}
    problems, notes = tool_changes(fm, entries, allowed, ref, report)
    if problems:
        return problems, notes
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", f"{ref}^{{commit}}"],
                          capture_output=True, text=True).stdout.strip()
    try:
        evidence = evidence_dir(slug, fm["ticket"].split("/")[1], head)
    except OSError as error:
        return [f"cannot keep the measurement evidence: {error}"], []
    for number, entry in enumerate(entries, 1):
        value, text = measure(entry, checkout, evidence / f"{number}.log")
        problem = not_the_head(branch, checkout, after=True)
        if problem:
            problems.append(problem)
            break
        label = f"measurement {entry['name']!r}: {entry['metric']}"
        if value is None:
            problems.append(f"{label} {text}; baseline {entry['baseline']}")
            continue
        sign = 1 if entry["direction"] == "higher" else -1
        if sign * value < sign * entry["baseline"]:
            worse = f"{label} is {text}, worse than baseline {entry['baseline']}"
            bound = finite_number(allowed.get(entry["metric"], "tool"))
            if bound is None:
                problems.append(f"{worse} ({entry['direction']} is better)")
            elif sign * value < sign * bound:
                problems.append(f"{worse} and past the bound {allowed[entry['metric']]} that ledger row "
                                f"{fm['allows_source']} allows")
            else:
                notes.append(f"{worse}; allowed {'down' if sign > 0 else 'up'} to {allowed[entry['metric']]} "
                             f"by ledger row {fm['allows_source']}")
    return problems, notes


def tool_changes(fm, entries, allowed, ref, report):
    """(problems, notes) for tickets that changed files an entry lists in `paths`.

    A delegated ticket is compared with the merge-base with its workstream branch (main when the workstream has
    none): that excludes what it merged in from the workstream, and a builder cannot hide an edit by moving `base:`. A
    stacked ticket changed a file only when it also differs from each dependency head its head contains, so a
    dependency's own change waits for that ticket's own acceptance. A ticket on the workstream branch itself (inline) is
    compared from the `start:` recorded when it moved into implementing, which the workstream branch must contain, with
    a commit the report lists past it. Only `allows: <metric> tool` with its ledger citation excuses an entry; the
    file names go in the problem or note.
    """
    slug = spec_of(fm["ticket"])
    workstream = branch_ref(workstream_branch(slug)) or "refs/heads/main"
    start = str(fm.get("start") or "")
    fix = f"record the workstream branch commit it started from with --set {fm['ticket']} start=<sha>"
    if ref == workstream and not start:
        return [f"an inline ticket is compared from its start:, and it has none; {fix}"], []
    if ref == workstream and not (re.fullmatch("[0-9a-f]{40}", start) and subprocess.run(
            ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", start, ref], capture_output=True).returncode == 0):
        return [f"start: {start} is not an ancestor of {ref.removeprefix('refs/heads/')}; delete that line from the "
                f"ticket, then {fix}"], []
    if ref == workstream and not listed_commit_on(report, start, ref):
        return [f"no commit listed under ## Implemented is past start: {start} on {ref.removeprefix('refs/heads/')}; "
                "a start recorded after the ticket's commits would hide them"], []
    if ref != workstream:
        fork = subprocess.run(["git", "-C", str(ROOT), "merge-base", workstream, ref], capture_output=True, text=True)
        if fork.returncode:
            return [f"no workstream branch ({workstream_branch(slug)}) or main exists to compare "
                    f"{ref.removeprefix('refs/heads/')} with"], []
        start = fork.stdout.strip()
    changed = changed_files(start, ref)
    if ref != workstream:
        for head in dependency_heads(fm, ref, start):
            changed &= changed_files(head, ref)
    problems, notes = [], []
    for entry in entries:
        touched = sorted({f for f in changed for p in entry.get("paths", []) if f == p or f.startswith(p + "/")})
        label = f"measurement {entry['name']!r}"
        if touched and allowed.get(entry["metric"]) == "tool":
            notes.append(f"{label}: tool change ({', '.join(touched)}) allowed by ledger row {fm['allows_source']}")
        elif touched:
            problems.append(f"{label}: the ticket changed what measures {entry['metric']} ({', '.join(touched)}); "
                            f"that needs allows: {entry['metric']} tool citing a ledger row")
    return problems, notes


def changed_files(start, ref):
    """Paths whose content differs between start and ref; a rename counts as both of its paths."""
    return set(subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", "--no-renames", "-z", start, ref],
                              capture_output=True, text=True, check=True).stdout.split("\0"))


def dependency_heads(fm, ref, fork):
    """The branch heads of the ticket's same-workstream `blocked_by` tickets that ref contains and is not, and that its
    fork with the workstream branch does not contain: a stacked ticket forks from them. Each branch is the one its
    report names, else <slug>/<its ticket file's stem without NN->."""
    slug = spec_of(fm["ticket"])
    own = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", f"{ref}^{{commit}}"],
                         capture_output=True, text=True).stdout.strip()
    heads = []
    for dependency in fm.get("blocked_by", []):
        if spec_of(dependency) != slug:
            continue
        nn = dependency.split("/")[1]
        report = ISSUES / slug / "reports" / f"{nn}.md"
        head = branch_ref(report_branch(slug, nn, report.read_text() if report.is_file() else "")[0])
        commit = head and subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", f"{head}^{{commit}}"],
                                         capture_output=True, text=True).stdout.strip()
        if commit and commit != own and all(subprocess.run(
                ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", *pair], capture_output=True).returncode == want
                                            for pair, want in (((commit, ref), 0), ((commit, fork), 1))):
            heads.append(commit)
    return heads


def evidence_dir(slug, ticket_number, head):
    """A new `reports/evidence/<NN>/accept-<N>/<head>/` directory, as the evidence contract lays it out."""
    attempts = ISSUES / slug / "reports" / "evidence" / ticket_number
    path = attempts / f"accept-{len(list(attempts.glob('accept-*'))) + 1}" / head
    path.mkdir(parents=True)
    return path


def measure(entry, checkout, log):
    """Run one measurement command in checkout through the bounded runner, which keeps both streams in log.

    (number, text as printed), or (None, why it has no value).
    """
    timeout = entry.get("timeout_seconds", MEASURE_TIMEOUT)
    runner = Path(__file__).resolve().with_name("run_check.py")
    try:
        run = subprocess.run([sys.executable, "-B", str(runner), "--timeout", str(timeout), "--log", str(log),
                              "--", "sh", "-c", entry["command"]],
                             cwd=checkout, capture_output=True, text=True)
        summary = json.loads(run.stdout)
    except (OSError, ValueError) as error:
        return None, f"has no value (the check runner failed: {error}; log {log})"
    if summary["timed_out"]:
        return None, f"has no value (its command timed out after {timeout:g} s; log {log})"
    if summary["exit_code"]:
        return None, f"has no value (its command exited {summary['exit_code']}; log {log})"
    lines = [l.split() for l in log.read_bytes().decode(errors="replace").splitlines()
             if l.split()[:1] == [entry["metric"]]]
    if not lines:
        return None, f"has no value (no `{entry['metric']} <number>` line in its output; log {log})"
    if len(lines) > 1:
        return None, f"has no single value (printed on {len(lines)} lines; log {log})"
    value = finite_number(lines[0][1]) if len(lines[0]) == 2 else None
    return (value, lines[0][1]) if value is not None else (
        None, f"is {' '.join(lines[0][1:])!r} (not a number; log {log})")


def allowance_problem(fm, slug):
    """Why the ticket's `allows` cannot excuse a regression, else None; it needs a ledger row as its source."""
    if "allows" not in fm:
        return None
    ledger = f".scratch/{slug}/ledger.md"
    if not fm.get("allows_source"):
        return f"allows: {fm['allows']} needs allows_source naming a row of {ledger}"
    if not ledger_row(slug, str(fm["allows_source"])):
        return f"allows_source {fm['allows_source']} is not a row of {ledger}"
    try:
        allowance_pairs(str(fm["allows"]))
    except ValueError as error:
        return f"allows {fm['allows']!r} {error}"


def allowance_pairs(text):
    """{metric: bound or "tool"} from `allows: <metric> <bound or tool>[, ...]`; ValueError says what is wrong."""
    pairs = {}
    for pair in text.split(","):
        words = pair.split()
        if len(words) != 2 or (words[1] != "tool" and finite_number(words[1]) is None):
            raise ValueError("must read `<metric> <number>` or `<metric> tool`")
        if words[0] in pairs:
            raise ValueError(f"names {words[0]} more than once")
        pairs[words[0]] = words[1]
    return pairs


def ledger_row(slug, row):
    """Whether row, such as U3, opens a table row of the workstream's ledger: `| U3 (user) | ...`."""
    ledger = ISSUES / slug / "ledger.md"
    return bool(re.fullmatch(r"[A-Za-z]+[0-9]+", row) and ledger.is_file()
                and re.search(rf"^\|\s*{row}(?=[\s(|])", ledger.read_text(), re.M))


def read_measurements(path):
    """The entries of a measurements.json; ValueError says why the file cannot gate anything."""
    try:
        entries = json.loads(path.read_text())
    except json.JSONDecodeError:
        raise ValueError("not valid JSON") from None
    if not isinstance(entries, list):
        raise ValueError("not a JSON array of entries")
    for number, entry in enumerate(entries, 1):
        if not (isinstance(entry, dict)
                and all(isinstance(entry.get(key), str) and entry[key] for key in ("name", "command", "metric"))
                and entry["metric"].split() == [entry["metric"]]
                and isinstance(entry.get("baseline"), (int, float)) and not isinstance(entry["baseline"], bool)
                and math.isfinite(entry["baseline"]) and entry.get("direction") in ("higher", "lower")):
            raise ValueError(f"entry {number} needs a name, command, metric without spaces, "
                             "numeric baseline and a direction of higher or lower")
        if "," in entry["metric"]:
            raise ValueError(f"entry {number} needs a metric without a comma, which separates the pairs of allows:")
        timeout = entry.get("timeout_seconds", 1)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout < math.inf:
            raise ValueError(f"entry {number} needs timeout_seconds to be a positive number")
        paths = entry.get("paths", [])
        if not isinstance(paths, list) or not all(
                isinstance(p, str) and p and not p.startswith("/") and ".." not in p.split("/")
                and posixpath.normpath(p) != "." for p in paths):
            raise ValueError(f"entry {number} needs paths to be a list of relative paths inside the project")
        entry["paths"] = [posixpath.normpath(p) for p in paths]  # git's spelling, so a path can match
    return entries


def finite_number(text):
    """text as a float when it reads as a finite number, else None."""
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def not_the_head(branch, checkout, after=False):
    """Why the checkout is not exactly the branch head, else None; `after` is a check made once a measurement ran."""
    try:
        changed = uncommitted(checkout)
    except ValueError as error:
        return f"branch {branch} has a worktree whose git status cannot be read: {error}"
    if changed and after:
        return (f"a measurement left uncommitted changes in branch {branch} ({', '.join(changed)}); "
                "declare its outputs in .gitignore")
    if changed:
        return (f"branch {branch} has uncommitted changes ({', '.join(changed)}); "
                "a measurement of it would not be of the ticket head")


def uncommitted(checkout):
    """Sorted paths with tracked changes, staged or not, or untracked and not ignored, in a checkout.

    ValueError carries git's message when the status cannot be read.
    """
    status = subprocess.run(["git", "-C", checkout, "status", "--porcelain", "--untracked-files=all"],
                            capture_output=True, text=True)
    if status.returncode:
        raise ValueError(status.stderr.strip())
    return sorted(line[3:] for line in status.stdout.splitlines())


def commit_of(rev):
    """The 40-hex commit rev names, else None."""
    named = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet", "--end-of-options",
                            f"{rev}^{{commit}}"], capture_output=True, text=True)
    return named.stdout.strip() if named.returncode == 0 else None


def checkout_of(ref):
    """The worktree, the main checkout included, that has ref checked out, else None."""
    listing = subprocess.run(["git", "-C", str(ROOT), "worktree", "list", "--porcelain"],
                             capture_output=True, text=True).stdout
    for block in listing.split("\n\n"):
        fields = dict(line.partition(" ")[::2] for line in block.splitlines())
        if fields.get("branch") == ref:
            return fields["worktree"]


def listed_commit_on(text, base, ref):
    """A recorded branch can be shared, so a `Commit:` line of the report must name a commit on it past base."""
    implemented = text.split("## Implemented", 1)[-1].split("\n## ", 1)[0]
    lines = re.findall(r"^\s*(?:- )?Commits?:(.*)$", implemented, re.M)
    for sha in re.findall(r"\b[0-9a-f]{7,40}\b", " ".join(lines)):
        full = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
                              capture_output=True, text=True).stdout.strip()
        if full and not full.startswith(base) and all(
                subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", a, b]).returncode == 0
                for a, b in ((base, full), (full, ref))):
            return True
    return False


def unproven(urls):
    """Why each URL does not deliver a ticket: gh reports the PR not merged or the CI run not successful, or cannot
    read it; none when all hold."""
    problems = []
    for url in urls:
        run = RUN_URL.fullmatch(str(url))
        if not (run or PR_URL.fullmatch(str(url))):  # Never handed to gh: a number or a flag means something else there.
            problems.append(f"{url} is not a pull request's or a CI run's URL")
            continue
        command = (["gh", "run", "view", run[2], "-R", run[1], "--json", "status,conclusion"] if run
                   else ["gh", "pr", "view", url, "--json", "state,mergeCommit"])
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            found = json.loads(result.stdout) if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            result, found = None, str(error)
        if not isinstance(found, dict):
            reason = found if isinstance(found, str) else (result.stderr or result.stdout).strip()
            problems.append(f"gh cannot read {url}: {reason}")
        elif run and (found.get("status"), found.get("conclusion")) != ("completed", "success"):
            ended = f" ({found['conclusion']})" if found.get("conclusion") else ""
            problems.append(f"{url} is {found.get('status')}{ended}, not a successful run")
        elif not run and found.get("state") != "MERGED":
            problems.append(f"{url} is {found.get('state')}, not merged")
        elif not run and not (found.get("mergeCommit") or {}).get("oid"):
            problems.append(f"{url} names no merge commit")
    return problems


def accept_delivered(tickets, name, urls):
    """Accept a ticket by the PRs and CI runs that deliver it, when gh reports each PR merged and each run successful;
    record them as `delivered_by`."""
    problems = unproven(urls)
    for problem in problems:
        print(f"{name}: {problem}", file=sys.stderr)
    if problems:
        return False
    write_fields(tickets[name], {"delivered_by": f"[{', '.join(urls)}]"})
    print(f"{name}: delivered by merged or successful {', '.join(urls)}; judge the content now")
    return True


def new_ticket(slug, title, blocked_by, touches):
    """Write the workstream's next numbered ticket from the local ticket template, under the board's lock; its path.
    Exits, writing nothing, when the board with it would fail its check."""
    issues = ISSUES / slug / "issues"
    issues.mkdir(parents=True, exist_ok=True)
    with state_files.locked(ISSUES / ".new-ticket"):  # One lock for the whole board: its check spans workstreams.
        tickets = load_whole()  # Read under the lock: a parallel call wrote its ticket before letting go of it.
        nn = f"{max((int(t.split('/')[1]) for t in tickets if spec_of(t) == slug), default=0) + 1:02}"
        words = "-".join(re.findall(r"[a-z0-9]+", re.sub("['\u2019]", "", title.lower())))
        path = issues / f"{nn}-{words}.md"
        taken = next((p for p in issues.glob(f"*-{words}.md") if p != path and p.stem.split("-", 1)[1] == words), None)
        if taken:  # The title words name the ticket's branch and worktree: two tickets would share them.
            print(f"--new wrote nothing: ticket {slug}/{taken.stem.split('-', 1)[0]} already has the title words "
                  f"{words}, which name its branch {slug}/{words}; reword the title", file=sys.stderr)
            sys.exit(2)
        fields = {"ticket": f"{slug}/{nn}", "status": "ready", "blocked_by": blocked_by, "touches": touches,
                  "devices": [], "needs": [], "path": str(path.relative_to(ROOT))}
        if not check({**tickets, fields["ticket"]: fields}):
            print("--new wrote nothing: the board with this ticket fails its check; fix what it names, then run it "
                  "again", file=sys.stderr)
            sys.exit(2)
        block = TEMPLATE.read_text().split("```markdown\n", 1)[1].rsplit("```", 1)[0]
        body = block.split("\n---\n", 1)[1].replace("<slug>", slug)
        body = re.sub(r"^# NN: .*$", lambda _: f"# {nn}: {title}", body, count=1, flags=re.M)
        try:
            stream = path.open("x")
        except FileExistsError:
            print(f"--new wrote nothing: {path} is taken by a file that is no ticket on the board; rename or remove "
                  "it, then run it again", file=sys.stderr)
            sys.exit(2)
        with stream:
            stream.write("\n".join(["---", f"ticket: {slug}/{nn}", "status: ready",
                                    f"blocked_by: [{', '.join(blocked_by)}]", f"touches: [{', '.join(touches)}]",
                                    "devices: []", "needs: []", "model: standard", "worker:", "---", body]))
    return path


def set_field(tickets, name, assignments):
    """Write each `key=value` to the ticket's front matter; a blocked ticket keeps its reason as `blocker`.

    A ticket moving into implementing from a status that is not live records `start:`, the workstream head it starts
    from, unless it has one; `start` is written once and never changed.
    """
    fields = {}
    for assignment in assignments:
        key, equals, value = assignment.partition("=")
        # One line by every separator the front-matter reader splits on, so no value can add a field.
        if not equals or key not in {"status", "worker", "blocker", "start"} or len(f"{value}x".splitlines()) > 1:
            sys.exit(f"{name}: --set accepts one-line status=, worker=, blocker= or start= only")
        fields[key] = value.strip() if key == "blocker" else value
    if "status" in fields and fields["status"] not in STATUSES:
        sys.exit(f"{name}: status {fields['status']!r} is not one of {sorted(STATUSES)}")
    fm = tickets[name]
    if "start" in fields:
        if fm.get("start"):
            sys.exit(f"{name}: start is {fm['start']} already and never changes")
        if not re.fullmatch("[0-9a-f]{40}", fields["start"]) or commit_of(fields["start"]) != fields["start"]:
            sys.exit(f"{name}: start={fields['start']} is not a 40-hex commit of this repository")
    elif fields.get("status") == "implementing" and not fm.get("start") and fm.get("status") not in LIVE:
        workstream = workstream_branch(spec_of(name))
        fields["start"] = commit_of(branch_ref(workstream) or "refs/heads/main")
        if not fields["start"]:
            sys.exit(f"{name}: no workstream branch ({workstream}) or main to record the start of implementing from")
    if fields.get("status") == "done" and fm.get("status") != "done" and fm.get("delivered_by"):
        # Delivered by PRs and CI runs: gh says each is merged or successful now, whatever the front matter records.
        problems = unproven(delivered(fm))
        if problems:
            sys.exit("\n".join(f"{name}: {problem}" for problem in problems))
    elif fields.get("status") == "done" and fm.get("status") != "done":
        accepted = fm.get("accepted") or ""
        if not re.fullmatch("[0-9a-f]{40}", accepted) or not re.fullmatch("[0-9a-f]{64}", fm.get("accepted_report") or ""):
            sys.exit(f"{name}: status=done needs a passing --accept first")
        workstream = workstream_branch(spec_of(name))
        ref = branch_ref(workstream)
        if not ref or landed(accepted, ref) is not True:
            sys.exit(f"{name}: accepted {accepted} is not in {workstream}; rebase the ticket branch onto {workstream} "
                     f"and fast-forward {workstream} to it, or, when that rebase resolved a conflict, --accept its "
                     f"resolved head, before status=done")
        slug, nn = name.split("/")
        if report_digest(ISSUES / slug / "reports" / f"{nn}.md") != fm.get("accepted_report"):
            sys.exit(f"{name}: the report changed after its --accept; accept it again")
    if fields.get("status") == "blocked" and not fields.get("blocker"):
        stored = f", even with one stored ({fm['blocker']!r})" if fm.get("blocker") else ""
        sys.exit(f"{name}: status=blocked needs the reason too{stored}: "
                 f"--set {name} status=blocked blocker=<reason>")
    if fields.get("status", "blocked") != "blocked":
        # A ticket leaving `blocked` drops its reason.
        fields["blocker"] = None
    write_fields(fm, fields)
    for key, value in fields.items():
        if value is not None:
            print(f"{name}: {key} = {value}")


def landed(sha, ref):
    """Whether every change of `sha` not reachable from `ref` is in `ref` as a commit with the same change: a ticket
    branch rebased onto the workstream and fast-forwarded gets new commit ids, and a clean rebase can move the lines
    around a change. Changes are compared without context (`-U0` patch ids), so only the changed lines count; a commit
    that changes nothing carries nothing to land."""
    base = subprocess.run(["git", "-C", str(ROOT), "merge-base", sha, ref], capture_output=True, text=True)
    if base.returncode != 0:
        return False

    def changes(tip):
        """The patch ids of the commits with a change between the fork and tip."""
        log = subprocess.run(["git", "-C", str(ROOT), "log", "-p", "-U0", "--no-color", "--no-merges",
                              "--format=commit %H", f"{base.stdout.strip()}..{tip}"],
                             capture_output=True, text=True, check=True).stdout
        ids = subprocess.run(["git", "-C", str(ROOT), "patch-id", "--stable"], input=log, capture_output=True,
                             text=True, check=True).stdout
        return {line.split()[0] for line in ids.splitlines()}

    return changes(sha) <= changes(ref)


def write_fields(fm, fields):
    """Write each field to a ticket's front matter; None removes the field. Only status, blocker, start, accepted,
    accepted_report and delivered_by may be added; another key must already be in the template."""
    path = ROOT / fm["path"]
    text = path.read_text()
    head = re.match(r"---\n.*?(?=\n---(?:\n|$))", text, re.S)[0]  # What front_matter.parse reads as the block.
    rest = text[len(head):]
    for key, value in fields.items():
        # A key's value runs on through a block list's `- item` lines, and the blank and comment lines between them.
        items = r"(?:(?:\n[ \t]*(?:#[^\n]*)?)*\n[ \t]*-[^\n]*)*"
        pattern = re.compile(rf"^({re.escape(key)}:)[^\n]*{items}", re.M)
        if value is None:
            head = re.sub(rf"\n{re.escape(key)}:[^\n]*{items}", "", head)
        elif pattern.search(head):
            head = pattern.sub(lambda match: f"{match[1]} {value}", head, count=1)
        elif key in ('status', 'blocker', 'start', 'accepted', 'accepted_report', 'delivered_by'):
            head += f'\n{key}: {value}'
        else:
            sys.exit(f"{fm['ticket']}: no front-matter key {key!r}")
    path.write_text(head + rest)


def orchestrate(slug, autofix):
    """Record the session running this command as the workstream's orchestrator, which the stop hooks hold: its Herdr
    pane, the session IDs its hosts export (a host started inside another's shell inherits the outer one's), and
    whether repairs are authorized until the review report records its own `autofix`. A later bind takes the workstream
    over."""
    binding = {"pane": os.environ.get("HERDR_PANE_ID"),
               "sessions": worker_lifecycle.host_sessions(), "autofix": autofix}
    path = ISSUES / slug / "reports" / "orchestrator.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    state_files.write_atomic(path, json.dumps(binding) + "\n")
    print(f"{slug}: orchestrated by Herdr pane {binding['pane']}, host sessions {binding['sessions']}, "
          f"autofix {str(autofix).lower()}")


def counts(tickets):
    n = {s: sum(fm["status"] == s for fm in tickets.values()) for s in ("done", "blocked", "ready")}
    n["live"] = sum(fm["status"] in LIVE for fm in tickets.values())
    assert sum(n.values()) == len(tickets), "a ticket has a status outside the enum; see --board"
    return " · ".join(f"{v} {k}" for k, v in n.items())


def main(argv):
    global ROOT, ISSUES
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, required=True, help="absolute main checkout path")
    parser.add_argument("slug", nargs="?", help="only this workstream; locks still include the whole board")
    parser.add_argument("--max-live", type=int)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true")
    actions.add_argument("--board", action="store_true")
    actions.add_argument("--accept", metavar="TICKET")
    actions.add_argument("--can-verify", metavar="TICKET")
    actions.add_argument("--delivery", action="store_true", help="require done tickets and current review/E2E evidence")
    actions.add_argument("--set", nargs="+", metavar=("TICKET", "ASSIGNMENT"))
    actions.add_argument("--orchestrate", action="store_true", help="bind this session as the workstreams' orchestrator")
    actions.add_argument("--new", metavar="TITLE", help="write the workstream's next ticket from the template")
    parser.add_argument("--blocked-by", action="append", default=[], metavar="TICKET", help="with --new")
    parser.add_argument("--delivered-by", action="append", default=[], metavar="URL",
                        help="with --accept: a PR gh reports merged or a CI run gh reports successful")
    parser.add_argument("--touches", action="append", default=[], metavar="AREA", help="with --new")
    parser.add_argument("--counts", action="store_true")
    parser.add_argument("--autofix", action="store_true", help="with --orchestrate: repairs are authorized")
    args = parser.parse_args(argv)
    if args.counts and any((args.check, args.board, args.accept, args.can_verify, args.delivery, args.orchestrate)):
        parser.error("--counts may only be combined with --set")
    if args.delivered_by and not args.accept:
        parser.error("--delivered-by goes with --accept")
    if not all(PR_URL.fullmatch(url) or RUN_URL.fullmatch(url) for url in args.delivered_by):
        parser.error("--delivered-by takes a pull request's or a CI run's URL: "
                     "https://<host>/<owner>/<repo>/pull/<number> or "
                     "https://<host>/<owner>/<repo>/actions/runs/<number>")
    if args.max_live is None:
        try:
            args.max_live = live_cap()
        except ValueError as error:
            parser.error(str(error))
    if args.max_live < 1:
        parser.error("--max-live must be positive")
    if args.slug and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.slug):
        parser.error("slug must be kebab-case")
    ROOT = args.repo.resolve()
    ISSUES = ROOT / ".scratch"
    root = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    common = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--git-common-dir"], capture_output=True, text=True)
    if root.returncode or Path(root.stdout.strip()).resolve() != ROOT or (ROOT / common.stdout.strip()).resolve() != ROOT / ".git":
        parser.error("--repo must name the main Git checkout, not a subdirectory or linked worktree")
    if args.delivery:  # The stop hold's reader, so another workstream's refused file or problem does not fail this one.
        board = judged()
        slugs = [args.slug] if args.slug else sorted({spec_of(t) for t in board[0]}.union(*(p.slugs for p in board[1])))
        if not slugs:
            parser.error(f"no tickets in requested scope under {ISSUES}")
        bad = [f'{slug}: {problem}' for slug in slugs for problem in undelivered(slug, board)]
        if bad:
            sys.exit('\n'.join(bad))
        print('delivery evidence matches selected workstream heads; inspect the recorded artifacts')
        return
    tickets = load_whole()
    if args.new is not None:
        title = args.new.strip()
        if not args.slug:
            parser.error("--new needs the workstream slug")
        if not title or len(f"{title}x".splitlines()) > 1:
            parser.error("--new takes a one-line title")
        problems = title_rules.ticket_problems(title)
        for problem in problems:
            print(f"--new wrote nothing: the title {problem} ({title_rules.STANDARD})", file=sys.stderr)
        if problems:
            sys.exit(2)
        unknown = [name for name in args.blocked_by if name not in tickets]
        if unknown:
            parser.error(f"--blocked-by {', '.join(unknown)}: no such ticket on the board")
        if not all(re.fullmatch(r"[A-Za-z0-9._/-]+", area) for area in args.touches):
            parser.error("--touches takes area names of letters, digits, '.', '_', '/' and '-'")
        print(new_ticket(args.slug, title, args.blocked_by, args.touches))
        return
    selected = {t: fm for t, fm in tickets.items() if not args.slug or spec_of(t) == args.slug}
    if not selected:
        parser.error(f"no tickets in requested scope under {ISSUES}")
    for name in (args.accept, args.can_verify, args.set[0] if args.set else None):
        if name and name not in selected:
            parser.error(f"{name}: no such ticket in requested scope")
    if args.board:
        for name, fm in selected.items():
            deps = ",".join(fm.get("blocked_by", [])) or "-"
            print(f"{fm.get('status', '<missing>'):<12} {name:<52} touches={','.join(fm.get('touches', []))} blocked_by={deps}")
        return
    if args.orchestrate:
        if not os.environ.get("HERDR_PANE_ID") and not worker_lifecycle.host_sessions():
            names = ", ".join(worker_lifecycle.SESSION_VARIABLES)
            sys.exit(f"no Herdr pane (HERDR_PANE_ID) or host session ID ({names}) here: "
                     "the stop hold cannot bind this session")
        slugs = sorted({spec_of(t) for t in selected})
        if not args.slug:  # Only workstreams with active work: a live workstream branch, or a ticket not done.
            slugs = [slug for slug in slugs if branch_ref(workstream_branch(slug))
                     or any(fm['status'] != 'done' for t, fm in selected.items() if spec_of(t) == slug)]
            if not slugs:
                sys.exit('no selected workstream has work left to orchestrate')
        for slug in slugs:
            orchestrate(slug, args.autofix)
        return
    if args.set:
        if len(args.set) < 2:
            parser.error("--set needs a ticket and at least one assignment")
        set_field(tickets, args.set[0], args.set[1:])
        if args.counts:
            board = load_whole()
            if not check(board):
                sys.exit(f"{args.set[0]}: set as asked; no counts while the board fails its check")
            print(counts({t: fm for t, fm in board.items() if t in selected}))
        return
    if not check(tickets, bool(args.accept)):
        sys.exit(1)
    if args.check:
        return
    if args.accept and args.delivered_by:
        sys.exit(0 if accept_delivered(tickets, args.accept, args.delivered_by) else 1)
    for name, fn in ((args.accept, accept), (args.can_verify, can_verify)):
        if name:
            sys.exit(0 if fn(tickets, name) else 1)
    if args.counts:
        print(counts(selected))
        return
    print(json.dumps(ready(tickets, args.slug, args.max_live), indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
