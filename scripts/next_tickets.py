#!/usr/bin/env python3
"""zloop scheduler. Run from any directory; state stays in the main checkout.

  next_tickets.py --repo /path/to/repo [slug] [--max-live N] [action]

No action: JSON candidates. No slug: all efforts. Other efforts still hold locks.
Actions: --board, --counts, --check, --accept TICKET,
         --can-verify TICKET, --delivery, --set TICKET status=VALUE (or worker=ID, start=SHA), --coordinate.
--set TICKET status=blocked also needs blocker=REASON, which the ticket's front matter keeps. Entering implementing
records start: (the effort branch head) when the ticket has none; start never changes once recorded. A passing
--accept records accepted: (the head it accepted); status=done needs it, merged into the effort branch.
Only --set may be combined with --counts. --accept also runs the effort's standing measurements
(.scratch/<slug>/measurements.json) at the ticket head, in a clean checkout, and refuses a missing or worse value.
--delivery names each failing field. --coordinate [--autofix] records the calling session (its Herdr pane and host
session IDs) and the repair authorization in .scratch/<slug>/reports/coordinator.json for the stop hooks; with no slug
only efforts with active work (a live effort branch, or a ticket not done), and it refuses a session it cannot identify.

Tickets: .scratch/<slug>/issues/NN-title.md, with restricted YAML front matter
(key: scalar and key: [a, b]). Only the orchestrator changes machine state.
Cross-effort prerequisites require the entire source effort done and its retained
branch merged into main and the target effort branch. --check detects missing
edges and cycles between tickets or efforts. One orchestrator per checkout.
"""
import argparse
from graphlib import CycleError, TopologicalSorter
import json
import math
import os
import posixpath
import re
import sys
import subprocess
from pathlib import Path

ROOT = Path.cwd()
ISSUES = ROOT / ".scratch"
LIVE = {"implementing", "reviewing", "fixing", "verifying"}
TERMINAL = {"done"}
STATUSES = {"ready", "blocked"} | LIVE | TERMINAL
try:
    MAX_LIVE = int(os.environ.get("MAX_LIVE", "8"))
    assert MAX_LIVE >= 1
except (ValueError, AssertionError):
    sys.exit(f"MAX_LIVE must be a positive integer, got {os.environ.get('MAX_LIVE')!r}")
# Critical path first. TICKET_PRIORITY=spec-a,spec-b names the specs to drain first; default is name order.
PRIORITY = [s for s in os.environ.get("TICKET_PRIORITY", "").split(",") if s]
MEASURE_TIMEOUT = 600  # seconds a measurement command may run unless its entry sets timeout_seconds
# The session ID Claude Code, Codex and Pi export to the orchestrator's shell; each equals the session_id their Stop
# hook, or Pi's settle check, reports.
SESSION_VARIABLES = ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "PI_SESSION_ID")


def parse_front_matter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return None
    fm = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            fm[key.strip()] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        else:
            fm[key.strip()] = value
    return fm


def load():
    tickets = {}
    for path in sorted(ISSUES.glob("*/issues/*.md")):
        fm = parse_front_matter(path.read_text())
        if fm and "ticket" in fm:
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*/[0-9]+", fm["ticket"]):
                sys.exit(f"{path}: invalid ticket id {fm['ticket']!r}")
            if fm["ticket"] in tickets:
                sys.exit(f"{path}: duplicate ticket {fm['ticket']}")
            if path.parent.parent.name != spec_of(fm["ticket"]):
                sys.exit(f"{path}: ticket effort differs from its directory")
            for key in ("blocked_by", "touches", "devices"):
                if not isinstance(fm.get(key), list):
                    sys.exit(f"{path}: {key} must be a bracketed list")
            fm["path"] = str(path.relative_to(ROOT))
            tickets[fm["ticket"]] = fm
        elif fm and set(fm) & {"blocked_by", "touches", "devices", "worker"}:
            sys.exit(f"{path}: missing ticket front matter")
    return tickets


def check(tickets):
    bad = [f"{t}: status {fm.get('status')!r} is not one of {sorted(STATUSES)}"
           for t, fm in tickets.items() if fm.get('status') not in STATUSES]
    bad += [f"{t}: blocked_by {dep} does not exist" for t, fm in tickets.items()
           for dep in fm.get("blocked_by", []) if dep not in tickets]
    bad += [f"{t}: no blocked_by key (write blocked_by: [] when nothing gates it)" for t, fm in tickets.items()
            if "blocked_by" not in fm]
    bad += [f"{t}: number differs from its filename {Path(fm['path']).name}" for t, fm in tickets.items()
            if not Path(fm["path"]).name.startswith(t.split("/")[-1] + "-")]
    graphs = {"ticket": {t: fm.get("blocked_by", []) for t, fm in tickets.items()},
              "effort": {spec_of(t): set() for t in tickets}}
    for t, fm in tickets.items():
        graphs["effort"][spec_of(t)].update(spec_of(d) for d in fm.get("blocked_by", [])
                                           if spec_of(d) != spec_of(t))
    for label, graph in graphs.items():
        try:
            tuple(TopologicalSorter(graph).static_order())
        except CycleError as error:
            bad.append(f"{label} dependency cycle: {' -> '.join(error.args[1])}")
    for line in bad:
        print(line, file=sys.stderr)
    return not bad


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
    prerequisite = branch_ref(effort_branch(source))
    if prerequisite is None or not valid_branch_name(effort_branch(spec_of(name))):
        return False
    target = branch_ref(effort_branch(spec_of(name)))
    bases = ["refs/heads/main"] + ([target] if target and target != "refs/heads/main" else [])
    return all(subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", prerequisite, base],
                              capture_output=True).returncode == 0 for base in bases)


def review_front_matter(slug):
    report = ISSUES / slug / 'reports' / 'spec-review.md'
    return (parse_front_matter(report.read_text()) or {}) if report.is_file() else None


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


def effort_branch(slug):
    """feat/<slug>, or main when the user had the effort work directly on main."""
    branch = (review_front_matter(slug) or {}).get('effort_branch')
    return branch if branch not in (None, '', []) else f'feat/{slug}'


def delivery_evidence(slug):
    return not delivery_problems(slug)


def finished(slug):
    """Whether the board records an effort as delivered and gone: its branch deleted, its reviewed head in main and a
    passing E2E recorded at a head in main."""
    fm = review_front_matter(slug) or {}
    return (branch_ref(effort_branch(slug)) is None and fm.get('e2e_status') == 'passed'
            and all(in_main(str(fm.get(key) or '')) for key in ('review_head', 'e2e_head')))


def in_main(sha):
    """Whether sha is a 40-hex commit that main contains."""
    return bool(re.fullmatch('[0-9a-f]{40}', sha)) and subprocess.run(
        ['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', sha, 'refs/heads/main'],
        capture_output=True).returncode == 0

def delivery_problems(slug):
    """Structural delivery gate: each recorded field that fails it, [] when none does. The orchestrator must also
    judge the recorded evidence.

    main keeps moving after an effort delivered on it, so there the reviewed head only has to stay in main.
    """
    fm = review_front_matter(slug)
    if fm is None:
        return [f'no review report at {ISSUES / slug / "reports" / "spec-review.md"}']
    problems = [f'{key}: {fm.get(key) or "missing"} (needs {value})' for key, value in
                (('review_status', 'clean'), ('documentation_status', 'passed'), ('e2e_status', 'passed'))
                if fm.get(key) != value]
    # The worker runs that produced the statuses; a host-written status alone is not evidence.
    problems += [f'{key}: {fm.get(key) or "missing"} names no worker run' for key in ('review_by', 'e2e_by')
                 if str(fm.get(key) or '').strip().lower() in ('', 'none', 'n/a', 'unknown', 'orchestrator', 'host')]
    branch = effort_branch(slug)
    ref = branch_ref(branch)
    head = ref and subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--verify', f'{ref}^{{commit}}'],
                                  capture_output=True, text=True)
    if not head or head.returncode:
        return problems + [f'effort branch {branch} is not an existing branch']
    base = fm.get('review_base', '')
    if not isinstance(base, str) or not re.fullmatch('[0-9a-f]{40}', base):
        return problems + [f'review_base: {base or "missing"} is not a 40-hex commit']
    sha = head.stdout.strip()
    reviewed = str(fm.get('review_head') or '')
    if ref == 'refs/heads/main' and re.fullmatch('[0-9a-f]{40}', reviewed) and subprocess.run(
            ['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', reviewed, sha]).returncode == 0:
        sha = reviewed
    problems += [f'{key}: {fm.get(key) or "missing"} is stale against {branch} head {sha}'
                 for key in ('review_head', 'e2e_head') if fm.get(key) != sha]
    if sha == base:
        problems.append(f'review_head: {sha} is review_base, so nothing was reviewed')
    merge_base = subprocess.run(['git', '-C', str(ROOT), 'merge-base', '--all', base, sha],
                                capture_output=True, text=True)
    if merge_base.returncode or merge_base.stdout.splitlines() != [fm.get('review_diff_base')]:
        problems.append(f'review_diff_base: {fm.get("review_diff_base") or "missing"} is not the merge base of '
                        f'review_base and {branch} head {sha}')
    return problems


def ready(tickets, slug=None, max_live=MAX_LIVE):
    live = [fm for fm in tickets.values() if fm["status"] in LIVE]
    busy_areas = {a for fm in live for a in fm.get("touches", [])}
    out = []
    order = sorted(tickets, key=lambda n: (PRIORITY.index(n.split("/")[0]) if n.split("/")[0] in PRIORITY else 99, n))
    for name in order:
        if len(live) + len(out) >= max_live:
            break
        fm = tickets[name]
        if fm["status"] != "ready" or (slug and spec_of(name) != slug):
            continue
        if any(tickets[d]["status"] not in TERMINAL for d in fm.get("blocked_by", [])):
            continue
        if any(not integrated(tickets, name, d) for d in fm.get("blocked_by", [])):
            continue
        if busy_areas & set(fm.get("touches", [])):
            continue
        out.append(fm)
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
    """(branch, recorded): the branch a ticket report's header names under `branch:`, else feat/<slug>-<NN>."""
    header = text.split("\n## ", 1)[0]
    recorded = re.search(r"^branch:\s*`?([^\s`]+)`?\s*$", header, re.M)
    return (recorded.group(1), True) if recorded else (f"feat/{slug}-{nn}", False)


def accept(tickets, name):
    """Deterministic verify node: the report exists in the main checkout with its evidence, and the branch moved."""
    slug, nn = name.split("/")
    report = ISSUES / slug / "reports" / f"{nn}.md"
    if not report.exists():
        print(f"{name}: no report at {report}", file=sys.stderr)
        return False
    text = report.read_text()
    problems = report_problems(text)
    base = re.search(r"^base:\s*`?([0-9a-f]{40})", text, re.M)
    branch, recorded = report_branch(slug, nn, text)
    ref = branch_ref(branch) if recorded else f"refs/heads/{branch}"
    if base and ref is None:
        problems.append(f"branch {branch} is not an existing branch")
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
        problems, notes = measurement_results(tickets[name], ref, text)
    for p in problems:
        print(f"{name}: {p}", file=sys.stderr)
    if not problems:
        for note in notes:
            print(f"{name}: {note}")
        # The head that passed: done needs it recorded and merged into the effort branch.
        write_fields(tickets[name], {"accepted": commit_of(ref)})
        print(f"{name}: report and branch carry the evidence; judge the content now")
    return not problems


def measurement_results(fm, ref, report):
    """Run the effort's standing measurements at the ticket head; (problems, notes on what the ticket's `allows` excused).

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

    A delegated ticket is compared with the merge-base with its effort branch (main when the effort has none): that
    excludes what it merged in from the effort, and a builder cannot hide an edit by moving `base:`. A stacked ticket
    changed a file only when it also differs from each dependency head its head contains, so a dependency's own change
    waits for that ticket's own acceptance. A ticket on the effort branch itself (inline) is compared from the `start:`
    recorded when it moved into implementing, which the effort branch must contain, with a commit the report lists
    past it. Only `allows: <metric> tool` with its ledger citation excuses an entry; the file names go in the problem
    or note.
    """
    slug = spec_of(fm["ticket"])
    effort = branch_ref(effort_branch(slug)) or "refs/heads/main"
    start = str(fm.get("start") or "")
    fix = f"record the effort branch commit it started from with --set {fm['ticket']} start=<sha>"
    if ref == effort and not start:
        return [f"an inline ticket is compared from its start:, and it has none; {fix}"], []
    if ref == effort and not (re.fullmatch("[0-9a-f]{40}", start) and subprocess.run(
            ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", start, ref], capture_output=True).returncode == 0):
        return [f"start: {start} is not an ancestor of {ref.removeprefix('refs/heads/')}; delete that line from the "
                f"ticket, then {fix}"], []
    if ref == effort and not listed_commit_on(report, start, ref):
        return [f"no commit listed under ## Implemented is past start: {start} on {ref.removeprefix('refs/heads/')}; "
                "a start recorded after the ticket's commits would hide them"], []
    if ref != effort:
        fork = subprocess.run(["git", "-C", str(ROOT), "merge-base", effort, ref], capture_output=True, text=True)
        if fork.returncode:
            return [f"no effort branch ({effort_branch(slug)}) or main exists to compare "
                    f"{ref.removeprefix('refs/heads/')} with"], []
        start = fork.stdout.strip()
    changed = changed_files(start, ref)
    if ref != effort:
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
    """The branch heads of the ticket's same-effort `blocked_by` tickets that ref contains and is not, and that its fork
    with the effort branch does not contain: a stacked ticket forks from them. Each branch is the one its report names,
    else feat/<slug>-<NN>."""
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
    """Whether row, such as U3, opens a table row of the effort's ledger: `| U3 (user) | ...`."""
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


def set_field(tickets, name, assignments):
    """Write each `key=value` to the ticket's front matter; a blocked ticket keeps its reason as `blocker`.

    A ticket moving into implementing from a status that is not live records `start:`, the effort head it starts
    from, unless it has one; `start` is written once and never changed.
    """
    fields = {}
    for assignment in assignments:
        key, equals, value = assignment.partition("=")
        if not equals or key not in {"status", "worker", "blocker", "start"} or any(c in value for c in "\r\n"):
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
        effort = effort_branch(spec_of(name))
        fields["start"] = commit_of(branch_ref(effort) or "refs/heads/main")
        if not fields["start"]:
            sys.exit(f"{name}: no effort branch ({effort}) or main to record the start of implementing from")
    if fields.get("status") == "done" and fm.get("status") != "done":
        accepted = fm.get("accepted") or ""
        if not re.fullmatch("[0-9a-f]{40}", accepted):
            sys.exit(f"{name}: status=done needs a passing --accept first")
        effort = effort_branch(spec_of(name))
        ref = branch_ref(effort)
        if not ref or subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", accepted, ref],
                                     capture_output=True).returncode != 0:
            sys.exit(f"{name}: accepted {accepted} is not in {effort}; merge it before status=done")
    if fields.get("status") == "blocked" and not fields.get("blocker"):
        stored = f", even with one stored ({fm['blocker']!r})" if fm.get("blocker") else ""
        sys.exit(f"{name}: status=blocked needs the reason too{stored}: "
                 f"--set {name} status=blocked blocker=<reason>")
    if fields.get("status", "blocked") != "blocked":
        # A ticket leaving `blocked` drops its reason.
        fields.pop("blocker", None)
        fields["blocker"] = None
    write_fields(fm, fields)
    for key, value in fields.items():
        if value is not None:
            print(f"{name}: {key} = {value}")


def write_fields(fm, fields):
    """Write each field to a ticket's front matter; None removes the field. Only status, blocker, start and accepted
    may be added; another key must already be in the template."""
    path = ROOT / fm["path"]
    head, sep, body = path.read_text().partition("\n---\n")
    for key, value in fields.items():
        pattern = re.compile(rf"^({re.escape(key)}:).*$", re.M)
        if value is None:
            head = re.sub(rf"\n{re.escape(key)}:.*$", "", head, flags=re.M)
        elif pattern.search(head):
            head = pattern.sub(lambda match: f"{match[1]} {value}", head, count=1)
        elif key in ('status', 'blocker', 'start', 'accepted'):
            head += f'\n{key}: {value}'
        else:
            sys.exit(f"{fm['ticket']}: no front-matter key {key!r}")
    path.write_text(head + sep + body)


def coordinate(slug, autofix):
    """Record the session running this command as the effort's coordinator, which the stop hooks hold: its Herdr pane,
    the session IDs its hosts export (a host started inside another's shell inherits the outer one's), and whether
    repairs are authorized until the review report records its own `autofix`."""
    binding = {"pane": os.environ.get("HERDR_PANE_ID"),
               "sessions": [os.environ[name] for name in SESSION_VARIABLES if os.environ.get(name)], "autofix": autofix}
    path = ISSUES / slug / "reports" / "coordinator.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(binding) + "\n")
    print(f"{slug}: coordinated by Herdr pane {binding['pane']}, host sessions {binding['sessions']}, "
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
    parser.add_argument("slug", nargs="?", help="only this effort; locks still include the whole board")
    parser.add_argument("--max-live", type=int, default=MAX_LIVE)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true")
    actions.add_argument("--board", action="store_true")
    actions.add_argument("--accept", metavar="TICKET")
    actions.add_argument("--can-verify", metavar="TICKET")
    actions.add_argument("--delivery", action="store_true", help="require done tickets and current review/E2E evidence")
    actions.add_argument("--set", nargs="+", metavar=("TICKET", "ASSIGNMENT"))
    actions.add_argument("--coordinate", action="store_true", help="bind this session as the efforts' coordinator")
    parser.add_argument("--counts", action="store_true")
    parser.add_argument("--autofix", action="store_true", help="with --coordinate: repairs are authorized")
    args = parser.parse_args(argv)
    if args.counts and any((args.check, args.board, args.accept, args.can_verify, args.delivery, args.coordinate)):
        parser.error("--counts may only be combined with --set")
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
    tickets = load()
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
    if args.coordinate:
        if not os.environ.get("HERDR_PANE_ID") and not any(os.environ.get(name) for name in SESSION_VARIABLES):
            sys.exit(f"no Herdr pane (HERDR_PANE_ID) or host session ID ({', '.join(SESSION_VARIABLES)}) here: "
                     "the stop hold cannot bind this session")
        slugs = sorted({spec_of(t) for t in selected})
        if not args.slug:  # Only efforts with active work: a live effort branch, or a ticket not done.
            slugs = [slug for slug in slugs if branch_ref(effort_branch(slug))
                     or any(fm['status'] != 'done' for t, fm in selected.items() if spec_of(t) == slug)]
            if not slugs:
                sys.exit('no selected effort has work left to coordinate')
        for slug in slugs:
            coordinate(slug, args.autofix)
        return
    if args.set:
        if len(args.set) < 2:
            parser.error("--set needs a ticket and at least one assignment")
        set_field(tickets, args.set[0], args.set[1:])
        if args.counts:
            print(counts({t: fm for t, fm in load().items() if t in selected}))
        return
    if not check(tickets):
        sys.exit(1)
    if args.check:
        return
    if args.delivery:
        bad = []
        for slug in sorted({spec_of(t) for t in selected}):
            undone = [f"{t} ({fm['status']})" for t, fm in selected.items()
                      if spec_of(t) == slug and fm['status'] != 'done']
            bad += [f'{slug}: {problem}' for problem in
                    ([f"tickets not done: {', '.join(undone)}"] if undone else []) + delivery_problems(slug)]
        if bad:
            sys.exit('\n'.join(bad))
        print('delivery evidence matches selected effort heads; inspect the recorded artifacts')
        return
    for name, fn in ((args.accept, accept), (args.can_verify, can_verify)):
        if name:
            sys.exit(0 if fn(tickets, name) else 1)
    if args.counts:
        print(counts(selected))
        return
    print(json.dumps(ready(tickets, args.slug, args.max_live), indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
