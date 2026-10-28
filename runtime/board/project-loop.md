# Project execution facts

Contents:
- [Check execution and resources](#check-execution-and-resources)
- [Hot files and areas](#hot-files-and-areas)
- [Standing measurements](#standing-measurements)
- [E2E steps](#e2e-steps)
- [External reviewer](#external-reviewer)
- [Environment gotchas the tools do not confess](#environment-gotchas-the-tools-do-not-confess)
- [Run evidence](#run-evidence)

Resolve these facts from existing project instructions, build configuration, CI,
and accepted alignment. An existing `docs/agents/implementation-loop.md` is one
possible source, not required setup. Pass applicable sources and verified gaps
in assignments; record effort-specific facts in the existing alignment/report.
Update maintained project documentation only for a reusable missing convention.
The workflow protocol stays in the plugin.

## Check execution and resources

Record required commands and their applicability. Run them directly unless the
project establishes an exclusive shared resource. Identify the actual resource,
conflicting commands and existing owner/coordination under
[check execution](../checks/plan.md#coordinate-the-actual-resource).
Machine load uses the shared concurrency budget, not a blanket build mutex.

For an optional local `resource_lock.py` convention, verify its `RESOURCE_LOCK` resource
path, participating commands and bounded `RESOURCE_LOCK_TIMEOUT`; carry that same
identity across sessions/retries. Do not replace it with a private lock to bypass
ownership. Resolve the installed plugin path; do not copy the wrapper.
For remote execution, name and load the runner's current skill/contract. Point to
it for snapshot transport, inputs, wait/cancel and resource scheduling rather than
copying internals into alignment. Do not wrap a remote queue in a local mutex.
Use the [check runner](../checks/run.md#run-a-bounded-check) only when the
existing runner lacks the required deadline and retained-output behavior.

## Hot files and areas

Which files two live tickets must never edit at once, and the `touches` area name
each maps to. The `blocked_by` graph orders specs; the area lock keeps concurrent
tickets apart. Name the gate tickets, so an orchestrator can say when concurrency
buys nothing.

## Standing measurements

A metric a ticket could narrow without a test noticing (a recall rate, a size, a latency)
is recorded in `.scratch/<slug>/measurements.json`, next to the alignment, as a JSON array.
Refine or the orchestrator writes it, never a builder, and measures each baseline by running
the command at the effort's branch base.

```json
[{"name": "recall tool", "command": "node tools/measure-recall.mjs", "metric": "recall_t4",
  "baseline": 100, "direction": "higher", "paths": ["tools/measure-recall.mjs", "tools/fixtures"]}]
```

`command` goes to `sh -c` and prints one `<metric> <number>` line; the metric holds no space or
comma. `direction` names the better way, `higher` or `lower`. `timeout_seconds` (optional,
default 600) bounds the run.
`paths` (optional) lists the tool and its fixtures as repository-relative paths, none absolute
or containing `..`; a directory covers what is under it. The command must leave the tree
clean, so declare its outputs in `.gitignore`.

`next_tickets.py --accept` runs every command itself, through the
[bounded runner](../checks/run.md#run-a-bounded-check), at the ticket head, in the worktree that
has the ticket branch checked out. Each log is kept under
`.scratch/<slug>/reports/evidence/<NN>/accept-<N>/<head>/`. It names what it refuses: a ticket whose
diff touches an entry's `paths`, a metric with no value or a value worse than its baseline.

A ticket may excuse metrics with front matter `allows:` holding one or more comma-separated
pairs, each `<metric> <bound>` (the worst value it tolerates) or `<metric> tool` (it may change
that entry's `paths`) and excusing only its own metric, such as `allows: recall 80, latency tool`. One
`allows_source: <row id>`, such as `U3`, cites a row of `.scratch/<slug>/ledger.md`. The row must
record the user's decision, and the orchestrator judging acceptance checks that it does. When a
ticket with an allowed drop is integrated, the orchestrator sets that entry's baseline to the
new value and cites the same row. An effort without the file is accepted without measuring.

## E2E steps

Identify the project's applicable E2E checks for the affected behaviors and areas:
what to boot, the commands and the artefacts that count as evidence (log tail,
screenshot, checksum). Use its existing device boot/wait/teardown scripts through
the runner's supported invocation or transfer. Name the corpus a
"real-data" gate must run against and what the fixture cannot catch.
Distinguish pre-PR verification from operator-only pilots, production rollouts and
post-merge checks, including which are actual prerequisites for the selected scope.
Identify the owner of each implementation/runtime check and valid reuse at later
handoffs under [worker return and checkpoints](../workers/return.md).
For live operations, name the authoritative target inventory, required execution
identity/configuration, reservations and permitted operator. State how to verify
post-state and restore the prior configuration. Use an idle representative target
before wider application when the change can disrupt shared workloads.
Boot procedures must tear down and wait for their owned processes on failure or
interruption too; a background descendant can retain the build lock after its launcher exits.

## External reviewer

If configured, record its actual invocation, model selection, applicability and
whether it is a required gate. Describe how its supported interface receives the
pinned revision and scope, and its access/input limits; do not assume particular
flags or prompt support. Otherwise record "none".

## Environment gotchas the tools do not confess

The toolchain path a non-interactive shell lacks, the gitignored file a fresh
worktree needs copied, the stale build artefact that fails under a different
toolchain version, the task whose own cache serves stale content while reporting
"executed". Each one line: symptom → cause → command.
Apply the same prerequisites to recreated worktrees. Read only required nonsecret
fields or value-presence flags from configuration; keep credentials out of output.

## Run evidence

Keep timings, findings and execution snapshots in existing run reports or journals.
Use the [retention contract](../checks/evidence.md) for files those
reports cite; a report in the main checkout does not preserve worktree-local logs.
Maintained project instructions contain only current commands, constraints and
reusable conventions; they are not a running retrospective.
