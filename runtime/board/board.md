# Board operations

Contents:
- [Local Markdown board](#local-markdown-board)
- [Remote board](#remote-board)
- [Delivery gate for either board](#delivery-gate-for-either-board)
- [Overall scope and batches](scope.md)
- [Slicing tickets](slicing.md)

Choose the project's configured tracker under the runtime bootstrap's conventions.
Use one board for a workstream. A remote board never requires `.scratch/*/issues`,
local ticket front matter, or `next_tickets.py`. Local reports, alignment and
worker recovery notes are evidence, not a ticket mirror.

## Local Markdown board

Only for projects using local tickets: run
`python3 "<plugin_root>/scripts/next_tickets.py" --repo "<repo>" [slug]`
with `--max-live <runtime ceiling>` on scheduling calls. This board uses
`.scratch/<slug>/issues/NN-<title>.md`. An omitted slug selects all local workstreams;
a supplied slug never widens. `TICKET_PRIORITY=a,b` orders workstreams when requested.
Also apply [shared capacity](../resources/capacity.md) across
known account users; `--max-live` sees this board's workers, not every service client.

| Flag | Use |
|---|---|
| no action flag | candidates (scheduling calls still take `--max-live`) |
| `--check` | validation |
| `--counts` | counts |
| `--set <ticket> status=<state>` or `worker=<id>` | runtime state; `status=blocked` also takes a new `blocker=<reason>`, which the ticket's front matter keeps until it leaves `blocked` |
| `--accept <ticket>` | structural evidence checks and standing measurements |
| `--delivery` | the structural delivery gate, which names each failing field |
| `--coordinate` | bind the calling session as the workstream's coordinator |

Read and judge the evidence after those checks.
Ordinary issue history without machine ticket metadata is not enrolled in this
board. Malformed machine tickets remain errors; `--board` exposes invalid statuses
and `--set` repairs them. Scheduling still validates all enrolled workstreams so a slug
cannot bypass dependencies, area locks, device reservations or live capacity.
For E2E, check `--can-verify <ticket>` before temporarily setting a done sentinel
to `verifying`; restore `done` after completion/failure and actual device release.

## Remote board

Use the existing CLI or connector directly. Resolve the supplied project, milestone,
parent issue, issue ID or URL within the configured destination. Without an argument,
use the project's established dispatch queue; do not select every issue in a
workspace. An ambiguous queue needs alignment. Fetch all pages and blocking links
needed for that scope; a partial query or access failure is not an empty board.

For an unfamiliar CLI or changed version, check its supported query fields and
operations before composing the board query. Cache that capability result until
the tool changes. If an optional field is unsupported, use a supported query or
equivalent native operation. If required ownership, status or dependency facts
cannot be fetched, keep that uncertainty explicit and block affected selection;
an API/field error never means there are no issues or dependencies. Do not upgrade
or replace project tooling just to obtain an optional field.

Read scope, acceptance criteria, priorities, ownership, dependencies and statuses
from canonical issues. Preserve native IDs and relationships. Map workflow states
by the project's meanings, not guessed label names; don't add zloop fields or
labels. Reject cycles and unresolved dependencies. An external blocker may stay
external: verify its delivery/integration evidence or keep the dependent blocked.
A closed/cancelled issue alone is not proof its prerequisite code was delivered.

Select ready, unassigned work in the accepted priority order after dependencies,
cross-workstream integration, capacity and area conflicts are checked. Read ongoing
work outside the selected scope only to detect relevant conflicts; do not schedule
or modify it. Unknown ownership/resource use blocks the affected candidate.
Re-read before claiming an issue; use the project's assignment/claim convention
and verify success before starting a worker. On a concurrent human change,
reconcile rather than overwrite. Only the orchestrator updates the board, within
existing authorization. Report a failed update and reconcile before retrying.
Before every state-changing update, refresh the issue's current accepted scope,
owner and status. Dependency cleanup owns its dependency evidence; it does not own
a dependent workstream's readiness. Use that workstream's current owner and convention,
and verify mutually exclusive states after updating. Never replay an old readiness
instruction over a newer alignment or leave contradictory readiness labels.

Record native issue ID/URL, agent ID, branch/worktree, starting/accepted commit,
execution phase, attempts, report path and active area/device reservations in
`.scratch/<slug>/reports/dispatch.md` (or the project's existing run log).
Update this record at launch, acceptance, integration and failure, before waiting
or yielding. Reconcile conflicting log, board, worker and Git evidence before the
next selection; a stale live row must not strand integrated work or cause a duplicate
assignment. Keep unresolved discrepancies explicit rather than overwriting them.
Do not copy issue bodies into local tickets. On resume, reconstruct execution
from that log, Git and native worker status; re-read requirements from the tracker.
Use the project branch convention; otherwise a stable filesystem-safe issue key
(for example `eng-123` or `gh-123`) replaces local `NN` in branch/report names.

Acceptance requires the worker report's original base, each criterion's evidence,
required checks, and reported commits verified on the branch the report records:
the workstream branch for a ticket the orchestrator ran inline, otherwise the assigned one. Confirm
integration into the workstream branch before recording implementation complete.
Apply remote status changes at the project's milestones: implementation complete
can remain In Review until merge. Compute execution counts from those verified
milestones plus the current scoped queue, so an implemented issue awaiting review
is neither redispatched nor falsely reported as remotely closed.

Before E2E, reconcile active workers and device reservations across this checkout's
run logs; reserve the required devices there, release after actual teardown, and
retain interrupted reservations until reconciled. Never change a remote issue's
status just to reserve a device. One orchestrator owns this checkout; use any
existing project resource reservation mechanism as well.

## Delivery gate for either board

Verify all selected work is implemented and integrated, with no unresolved required
work or reservation, and that the
[ADR number check](../domain/adr.md#deliver-a-workstreams-decisions) passes on the workstream branch. Require `review_status: clean`, `documentation_status: passed`,
`e2e_status: passed`, and
`review_head`/`e2e_head` equal to the current workstream branch commit, and
`review_by`/`e2e_by` naming the worker runs that produced them. With `workstream_branch: main`
the reviewed head must instead be an ancestor of the current `main`, which keeps moving;
later commits on `main` are not covered by that evidence. Verify
`review_base` resolves and its unique merge base with that head equals
`review_diff_base`; inspect coverage, accepted decisions and actual check/artifact
evidence. Missing or stale evidence fails the gate. Remote dispatch performs these
Git/report checks directly; it never creates local tickets to call `--delivery`.
Snapshot-based check evidence can validate that final head only after the host
records [relevant-input equivalence](../checks/plan.md#identify-executed-inputs),
retaining the original execution snapshot and artifacts. Setting `e2e_head` alone
cannot establish that equivalence.
Follow-ups remain outside this batch's delivery scope. Three kinds of finding are
never follow-ups without a user decision: those introduced by the batch, those
sharing a root cause with selected work, and those narrowing a user decision (see
review workflow's review-to-coverage-completion section). The [overall-scope rules](scope.md)
decide whether dispatch queues them for a later batch under existing authorization.
