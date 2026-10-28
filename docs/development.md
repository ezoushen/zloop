# Development

Changes to the plugin follow its own architecture rules
([writing-agent-docs](../.agents/skills/writing-agent-docs/SKILL.md)); the checks
below enforce them. Read the [decision index](adr/README.md) before an architectural change:
only accepted ADRs bind, and a change that conflicts with one is raised, not worked around.

- A new ADR's number is re-checked against the fetched `main` before review, and renumbered on a collision
  ([ADR 0001](adr/0001-adr-numbers-are-checked-on-main.md)).
- Grilling asks the user about principles and material forks only, never implementation detail
  ([ADR 0002](adr/0002-grilling-asks-principles-only.md)).
- An ADR records a decision others must follow, or one that passes the three tests; a bare rule goes to the rules
  file ([ADR 0003](adr/0003-adrs-record-what-others-follow.md)).
- An accepted ADR is superseded, never rewritten
  ([ADR 0004](adr/0004-adrs-are-superseded-never-rewritten.md)).
- The ADR index is updated in the same commit as the ADRs it lists
  ([ADR 0005](adr/0005-the-adr-index-is-the-current-state.md)).
- An ADR is accepted, superseded or deprecated, never proposed
  ([ADR 0006](adr/0006-adr-statuses-have-no-proposed.md)).
- Builders work test-first from `standards/tdd.md` and each test protects a named business rule
  ([ADR 0007](adr/0007-builders-work-test-first-from-our-own-standard.md)).
- Builders refactor the ticket's own code after green, within the ticket's scope
  ([ADR 0008](adr/0008-builders-refactor-after-green-within-the-ticket.md)).
- The plugin declares no external skill package as a dependency
  ([ADR 0009](adr/0009-the-framework-depends-on-no-external-skill-package.md)).
- Autopilot merges only with `--auto-merge`, through the repository's normal path
  ([ADR 0010](adr/0010-autopilot-merges-only-through-the-normal-path.md)).
- Docs that link the engineering standard add rules and never relax one of its principles
  ([ADR 0011](adr/0011-the-engineering-standard-is-extended-never-overridden.md)).
- An autopilot resume timer is the user's own take-over, authorized on the run card and run as `--take-over --timed`
  ([ADR 0012](adr/0012-an-autopilot-resume-timer-is-the-users-own-take-over.md)).

## Suite and hooks

`python3 tests/run_all.py` runs every `tests/test_*.py` file as per-class units with the plugin environment's `.venv`
and exits 1 on any failure; concurrent runs share one budget of CPU slots (`ZLOOP_TEST_SLOTS`,
`ZLOOP_TEST_SLOT_DIR`), so a run takes the cores it is given. Named scripts
(`python3 tests/run_all.py tests/test_a.py tests/test_b.py`) run alone the same way, for the tests a change touches;
a path that is no `tests/test_*.py` script is refused. Tests are
`unittest` cases, so one file, class or test runs on its own. The default suite needs
an installed `claude` for `test_claude_effort.py`, which runs it against a local fake
API. `tests/support.py` locates and loads the scripts and builds an environment whose
zloop state, configuration and Codex home are temporary; use it for a new test
that runs a script which writes machine state.

Enable the Git hooks once per clone with `git config core.hooksPath .githooks`:
- `pre-commit` (about a second) runs the doc checker on changed files and checks the
  syntax of staged Python and shell files. The doc check reads files as they are on
  disk, so unstaged edits and untracked docs count, not only what is staged.
- `pre-push` checks out each pushed commit into a temporary worktree and runs the
  whole-repo doc check and the suite there, so the result is the pushed code's.
  Commits the remote already has are not rerun; an older commit without the checker
  or suite is pushed with a warning. The checks run in a session of their own, without
  a terminal: Ctrl-C or any signal to the hook reaches them once, as the same signal,
  followed by TERM after 2 s and KILL after 5 s, and the temporary worktree is
  removed. Not covered: a check that starts its own session (`setsid`), and `kill -9`
  of the hook, which leaves the checks running; the next push removes their worktree
  once they have finished.

In Claude Code, `.claude/settings.json` also runs the architecture part of the doc
checker after every Edit or Write. `--no-verify` skips either Git hook.

## Native checks

The package and profile readers of the installed CLIs are opt-in and make no model
calls:

```sh
.venv/bin/python -B tests/test_router_profiles.py
.venv/bin/python -B -m unittest discover -s tests -t tests
# Installed CLI discovery, OpenCode 2 and Claude profile readers:
ZLOOP_NATIVE=1 .venv/bin/python -B tests/test_cross_harness.py
ZLOOP_NATIVE=1 .venv/bin/python -B tests/test_agent_models.py
# Pi SDK discovery and bootstrap, isolated from user extensions:
ZLOOP_NATIVE_PI=1 .venv/bin/python -B tests/test_cross_harness.py
```

They validate discovery, invocation metadata and registration. When this source is
installed in Claude, the check also verifies that the development plugin is enabled
and has no loading errors, including missing dependencies. Pi's isolated SDK check
also exercises bootstrap startup/reload. They do not launch workers or establish that
native hooks are trusted in a live session. Use native readers for shared skill
metadata: the generic skill-creator validator does not accept the Claude-specific
frontmatter this package uses.

**Still requires a live installed session in each host:** worker spawning and context
loading, review/fix/E2E, and interruption/resume. These are not claimed as passing.
Use the [review behavior rehearsal](rehearsals/review.md) for walkthrough discussion,
review handoff and finding adjudication, the
[dispatch recovery rehearsal](rehearsals/dispatch.md) for executor completion, failed
waits, state reconciliation and automatic review continuation, and the
[planning rehearsal](rehearsals/planning.md) for the alignment question filter.

## Usage and spend tools

- The read-only [usage helper](../runtime/resources/claude-usage.md) ranks local Claude session families,
  includes native workers and attributes related worktree sessions over an explicit
  time window. It reports recorded tokens without estimating account credits.
- The read-only [workstream spend report](../runtime/workers/effort-spend.md) divides a workstream's worker and
  coordinator tokens by its accepted tickets and shows what the coordinator's tokens
  went to. It reads Claude, Codex and Pi transcripts.
- `scripts/allowance.py status` reports the Claude Code and Codex allowance windows and
  their pressure levels; see [selection](../runtime/routing/choose.md#allowance).
- `scripts/outcomes.py summary [--agent <agent>] [--job <job>]` counts each
  candidate's accepted, failed and pending attempts across workstreams;
  `scripts/outcomes.py import <decisions.jsonl | workers.sqlite>...` adds past decision
  journals and lifecycle state files once.

## Local Markdown board

The scheduler runs from any directory and is not used for remote trackers:

```sh
python3 /path/to/zloop/scripts/next_tickets.py --repo /path/to/project feature-name --check
python3 /path/to/zloop/scripts/next_tickets.py --repo /path/to/project feature-name --max-live 3
python3 /path/to/zloop/scripts/next_tickets.py --repo /path/to/project feature-name --set feature-name/01 status=implementing --counts
```

Omit the slug for all workstreams. An unknown slug or argument fails. Scope controls
selection, board, counts and mutation; locks and validation still see other workstreams.
Use one orchestrator per checkout. Invoke the plugin's resource lock
(`scripts/resource_lock.py`) directly when a project needs it; never copy it into the
project.
