# Autofix verification

The verification stage of the [shared review procedure](SKILL.md).

## Verify autofix through E2E

For `--autofix`, continue here in the same invocation. A clean review is an
intermediate result, not a reason to stop and ask the user to run pipeline or PR.
The orchestrator owns remaining gates after analytical and repair workers return;
a read-only reviewer's permissions do not transfer verification to the user.

Determine applicable gates from the accepted scope, project verification commands
and, when present, the project loop file and selected tickets' `touches` union.
Include required compile/suite checks and E2E, not every step listed for unrelated
areas. When the dispatch's [verification level](../../runtime/checks/accept.md#verification-levels)
is `xhigh` or `max`, the applicable gates widen to every acceptance criterion with an
observable path, even where CI covers it, and `max` runs each E2E check and each
flaky-prone suite three times. When the repository has CI workflows, the required gates include every job CI
requires on the PR. Read them from `.github/workflows/` (or the CI configuration in
use). Either run each locally, or record which CI jobs remain to be watched after
the push. Consumer-repository branches made for the workstream are review targets as
well. Review each against its own base. Record why operator-only pilots, production rollouts and post-merge checks
fall outside this scope. If such an action is an explicit prerequisite within the
accepted scope, retain it as a real blocker; never silently waive it or execute it
without its authorization. No board or zloop project file is required for
standalone review; use the repository's existing verification conventions.

For a verified zloop workstream, the orchestrator integrates accepted local
repairs into the workstream branch while no worker owns that worktree. Prefer
fast-forward; review any new merge result or conflict resolution before E2E.
Before expensive gates, reconcile the intended integration base and known required
dependency merges under existing authorization. Coordinate who owns the branch
update; another session or remote update may already have moved it. Integrate the
required changes before pinning the verification head, rather than repeatedly
chasing optional base updates while checks run. If the base moves later, inspect
the actual delta and invalidate only affected evidence; required up-to-date rules
still apply.
Confirm `review_head` equals the integrated head. For any other target, verify
the local repair branch without changing the author's branch or the user's
checkout. Finish intended tracked housekeeping changes before pinning that head.

Prepare the per-check reuse/execute plan using
[plan and accept checks](../../runtime/checks/accept.md), including
implementation, review and repair evidence. Pass that plan to the e2e-gate job;
preserve justified reuse in its assignment rather than replacing it with a demand
for fresh execution of every gate. Missing run preconditions require fresh evidence
from the affected checks; recomputing old results cannot establish them. Schedule
authorized verification under the project's resource rules, including when no code defect
remains. A runtime estimate alone is not a permission boundary; honor explicit
user budgets and resource limits. Ask for a decision only when a concrete boundary
blocks execution or the user proposes an exception, keeping missing proof visible.

For checks requiring execution, assign a verifier the [e2e-gate](jobs/e2e-gate.md) job, supplying absolute adapter,
agent, job, plugin, repo, worktree, project-instruction and report paths, the reviewed
head, applicable checks, and reusable evidence. Follow the same adapter, permission
and native capacity checks as review workers. With no worker tools, run
locally where permitted. Reserve required devices through the configured
[board operations](../../runtime/board/board.md), or existing project resource conventions
for standalone review. Do not create local tickets for remote boards or standalone
reviews. Reconcile interrupted reservations and release only after actual teardown.
Supply the expected duration and next milestone under
[worker return and checkpoints](../../runtime/workers/return.md).
Accept a bounded failed/incomplete return promptly and take the existing recovery
action; keep the workflow gated and reuse already completed valid segments.

Feed a failing check back into this review procedure with its exact evidence.
Confirmed in-scope defects enter the existing repair loop; review the repair delta,
integrate when applicable, then rerun the failed and affected checks. Keep the same
finding IDs and repair-attempt counts across E2E; do not restart the retry budget
or spawn another dispatch loop. Continue independent checks when safe. Missing
access, unresolved intent or a wider repair is a reported blocker, not a new gate
approval round for already authorized testing.

Write the check-by-check evidence, reuse reasons, exclusions and gaps under `## E2E`.
Before accepting a pass, verify the scenario's expected versus observed activity
and invalid-run conditions, not just its exit code or the worker's summary.
On resumption, apply [resume interrupted checks](../../runtime/checks/plan.md#resume-interrupted-checks).
Give each unfinished gate its next action and responsible job, or the concrete
blocker that prevents progress. "Awaiting stakeholder decision" needs an identified
decision beyond executing already authorized verification.
Set `e2e_status: passed` only when every applicable gate is satisfied or has an
explicit project-approved omission; a verified absence of applicable E2E checks
needs a reason. Set `e2e_head` to the validated head, `e2e_by` to the e2e-gate attempt (or the
run_check logs of the checks the orchestrator ran itself), and retain original execution
heads for reused checks. Any unresolved failed check makes the aggregate `failed`;
otherwise missing evidence/access or unresolved requirements make it `incomplete`.
Keep every per-check failure and gap visible. Neither status certifies verification.
Recheck the target/base and local repair head before finishing; any movement or
new tracked edits reopens affected review and verification. For a zloop
workstream, apply the board's delivery gate after teardown (`--delivery` only for local
tickets). Unimplemented required work or broader uncovered scope still blocks it.
On later user follow-ups, reconcile the selected issues and report before continuing.
New code or relevant environment changes invalidate affected passes immediately:
mark them incomplete, retain the old evidence, and review/verify the delta before
restoring passed status. A completed subset does not certify the whole workstream.

Autofix finishes when all applicable review/documentation/verification gates pass,
or with the concrete blockers and remaining checks. Do not leave E2E at `not-run`
merely because review ended. Code publication remains with the PR command;
review-comment publication follows the selected comment policy.
