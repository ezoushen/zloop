---
name: repair
agent: builder
inputs: ["worktree", "branch", "head", "report", "findings", "allowed changes", "test seams", "authorization"]
returns: ["## Fixed round"]
independent_of: []
acceptance: false
---

# repair

Fix confirmed in-scope findings on an assigned local branch and record regression
evidence. Review assigns it under `--autofix`; remediation assigns it after proof.

## Inputs

The assignment names the `worktree`, `branch` and starting `head`; the `report`;
the confirmed finding IDs (`findings`) in priority order; the `allowed changes`
(files and behaviors); any priority sources; the accepted `test seams`; and its
`authorization`: an explicit `--autofix` review, or a remediation scope whose
confirmation rule has been met. A remediation repair changes tests and
documentation only.

## Finish line

Read scope and priorities from the report. Fix only the assigned in-scope findings,
in that order. Follow-ups, unverified claims, provisional design choices and
personal preferences are not repair instructions. If the minimal fix needs a
materially wider change, report the boundary and wait for human alignment; don't
refactor around it. Preserve unrelated work.

Use the [TDD standard](../../../standards/tdd.md) at accepted public seams for behavioral repairs; reuse
already accepted project/brief decisions instead of asking again. If a seam/API
choice is genuinely new, obtain that decision before implementing it. Verify
documentation repairs using code-comments and the original finding's evidence. A
comment correction needs no artificial runtime test. For a compatible batch,
establish each behavioral defect's original repro red, apply the smallest repairs
and show those repros green. A remediation test repair follows its kind's Repair
section instead: an expected result from an independent source, run against the
real implementation; if it fails there, stop and report a suspected runtime bug.
Run the checks the assignment tiers for you once for the completed batch: affected
suites, and the full gate only when the assignment names you for it; repeat only
checks invalidated by subsequent changes. Follow check
[planning](../../../runtime/checks/plan.md) and [execution](../../../runtime/checks/run.md)
for snapshot proof, execution output and project-required resource coordination;
do not commit temporary probes just for identity. A repair that introduces or changes a
domain term updates the [glossary](../../../runtime/domain/glossary.md) in the same commit that introduces or changes it.

Done when every assigned finding is repaired with evidence, or its boundary is
reported.

## Return

Commit scoped repairs on the assigned local branch. Append finding IDs, commits,
commands/results and any unresolved boundaries under `## Fixed round N` in the
existing report, saying of each assigned finding whether it is repaired, open at a
reported boundary or a suspected runtime bug. Never replace the initial evidence or clear the follow-up list.
Return the new head and evidence; only a reviewer closes findings, after checking
the repair. No publication or remote comment submission.
