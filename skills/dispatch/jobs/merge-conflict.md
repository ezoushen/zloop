---
name: merge-conflict
agent: builder
inputs: ["worktree", "branch", "head", "report", "conflict"]
returns: []
independent_of: []
acceptance: false
---

# merge-conflict

Resolve an integration conflict in the ticket worktree by rebasing the ticket branch onto the workstream tip. Dispatch
assigns it when the ticket branch does not rebase cleanly; this assignment is its authorization. Rebase only a
branch that is still local.

## Inputs

The assignment names the ticket `worktree`, its `branch` and starting `head`, the workstream branch to rebase
onto, the conflicting files and their `conflict` details, and the `report`: the ticket's own report. No other worker uses that
worktree while this job runs.

## Finish line

Resolve only the assigned conflicting files, preserving both sides' accepted
behavior; a resolution that needs a design choice returns to the orchestrator. Run
the checks the assignment tiers for you on the resolved tree.

## Return

Finish the rebase, point the report's `Commit:` lines at the rebased commits, and return the ticket's report at the
new head: the orchestrator accepts it again and fast-forwards the workstream branch. Report the files resolved, how each conflict
was decided, the checks run with their results, and any unresolved boundary.
