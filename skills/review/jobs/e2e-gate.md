---
name: e2e-gate
agent: verifier
inputs: ["worktree", "head", "report", "checks"]
returns: ["## E2E"]
independent_of: []
acceptance: false
---

# e2e-gate

Verify the project's applicable E2E checks at a reviewed head or snapshot, for the
review's autofix loop or a delivery gate.

## Inputs

Your assignment names `worktree`, branch and reviewed head, `report`, the applicable
project checks, source instructions and reusable evidence. A zloop effort
also supplies its selected tickets' `touches` union. Verify the assigned head.
For a pre-delivery snapshot check, verify the supplied immutable input identity;
retain that identity rather than attributing dirty execution to HEAD alone.
Use [worker return and checkpoints](../../../runtime/workers/return.md)
for estimates, milestones and bounded failed/incomplete returns. Before a full run,
verify interaction/readback, localized permissions, recording and fixture/event
alignment through the project's actual driver. Repeated invalid scenario runs
without new evidence need host recovery, not another full measurement batch.

## Finish line

Before executing or accepting checks, apply [plan and accept checks](../../../runtime/checks/accept.md)
to the supplied evidence. Record each reuse/execute decision and its reason in the
existing report. Reconcile a blanket freshness instruction with the actual project
requirements; a phase change alone does not justify rerunning every gate.
Execute applicable project gates using existing boot/wait/teardown scripts. Respect
documented scope exclusions and operator-only boundaries; an unavailable required
check remains incomplete. Follow check [planning](../../../runtime/checks/plan.md) and [execution](../../../runtime/checks/run.md) for local
resource coordination, remote runner discovery, snapshot identity and execution
proof. Verify transported scripts/artifacts; missing-input pipelines and zero tests
cannot pass. Use required-output checks when the runner supplies authoritative markers.
Cached build outputs alone do not prove an E2E check executed.
Reuse valid check evidence; otherwise execute the missing check, without forcing
a rebuild of unchanged inputs merely to avoid a cache hit.

For each check, return expected versus observed activity, invalid-run conditions,
and what the evidence proves. Report an uncovered required outcome to the
orchestrator rather than passing the check or silently widening scope.
For live operations, identify each verified target, execution user, relevant
configuration and deployed script/artifact revision or checksum. Read back the
intended post-state; successful exit, flag availability and service health prove
different things. Bind remote checks to their exact run ID and revision, not the
latest run by name. An old deployed artifact cannot verify an unreleased change.
For optimization, compare the same metric under comparable workload conditions;
record noise and limitations before claiming benefit or increased capacity.

## Return

Write every command, exit code, artefact path and timestamp under `## E2E` in
`report`. On red, stop after that step and report which step, its tail and its
artefacts. Return control for the review's existing repair loop; do not fix code or
ask the user to restart testing. The orchestrator continues independent safe checks.
Tear down owned processes/devices and report anything still reserved.
On a user pause, retain completed checks/iterations and use
[resume interrupted checks](../../../runtime/checks/plan.md#resume-interrupted-checks).

Done when you finish the assigned checks or return at the verified failure,
unavailable required resource, or repeated invalid attempt described above. Missing
evidence remains incomplete; a return is not a pass. Acceptance requires verified
passing evidence and artifacts for every applicable step, or an authorized documented
omission. Record your verdict as `e2e_status` and `e2e_head` in the report's front matter
before returning: the delivery gate takes the status from it. Return per-check outcome/reuse,
validated head or snapshot, remaining gaps and job state; the orchestrator owns the next recovery action.
