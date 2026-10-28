---
name: merge-conflict
agent: builder
inputs: ["worktree", "branch", "head", "report", "conflict"]
returns: []
independent_of: []
acceptance: false
---

# merge-conflict

Resolve an integration conflict in the effort worktree. Dispatch assigns it when a
ticket branch does not merge cleanly; this assignment is its authorization.

## Inputs

The assignment names the effort `worktree`, its `branch` and starting `head`, the
integration scope, the conflicting files and their `conflict` details, and the
`report`. No other worker uses that worktree while this job runs.

## Finish line

Resolve only the assigned conflicting files, preserving both sides' accepted
behavior; a resolution that needs a design choice returns to the orchestrator. Run
the checks the assignment tiers for you on the resolved tree.

## Return

Leave the resolution staged or committed as the assignment says and return control
with the new head: the orchestrator completes the merge. Report the files resolved, how each conflict
was decided, the checks run with their results, and any unresolved boundary.
