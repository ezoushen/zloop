---
name: acceptance-rerun
agent: verifier
inputs: ["worktree", "head", "report", "ticket", "checks"]
returns: ["## Acceptance rerun"]
independent_of: []
acceptance: false
---

# acceptance-rerun

Re-run one integrated ticket's acceptance on the workstream's integrated head. Dispatch
assigns it, one attempt per ticket, at `high` and higher
([verification levels](../../../runtime/checks/accept.md#verification-levels)).

## Inputs

The assignment names the workstream `worktree`, the integrated `head` to verify, the
`ticket` whose acceptance it re-runs, its `checks`: that ticket's `Verify (code)` commands
with their criteria, and the `report`. Its `report` is a file of its own, never the review
report: it holds no `e2e_status` or `e2e_head`, and the review report does not list it in
`e2e_by`. The orchestrator creates it before the launch. Verify the assigned head.

## Finish line

Run each of the ticket's `Verify (code)` commands on the integrated head, under check
[planning](../../../runtime/checks/plan.md) and [execution](../../../runtime/checks/run.md),
and map every criterion to its command, exit status and the head it ran at. A criterion
with no `Verify (code)` command, or whose command cannot run, stays unverified in the
report; it is never passed. Fix nothing and run no E2E: a failing command stops the job
at that step.

## Return

Write each criterion's command, exit status, output tail and head under
`## Acceptance rerun` in `report`, and return control with the verdict per criterion. The
orchestrator records a failure against the ticket and takes the usual recovery action; the
job never reopens or repairs it.
