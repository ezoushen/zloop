---
name: reproduce
agent: reviewer
inputs: ["worktree", "head", "report", "findings", "proof steps", "effort level"]
returns: []
independent_of: ["diff-review", "audit"]
acceptance: false
---

# reproduce

Decide difficult or disputed findings independently of the reviewer who raised
them. Review and remediation assign it.

## Inputs

The assignment names the `worktree`, pinned `head` and `report`; the `findings`:
one finding, or a batch that shares one kind and proof procedure; their scope and
priority evidence; the `proof steps` to apply; any counter-evidence; and the review's
`effort level` ([levels](../levels.md)). Read the
report's scope and do not expand it. The worker that raised a finding never decides
it.

## Finish line

Diagnosis only: never repair the finding. When a finding needs its cause
established, follow [diagnosis](../../diagnose/SKILL.md), one reproducer per finding.
A successful workaround alone does not establish the root cause. Run and minimize
the evidence in isolation under check [planning](../../../runtime/checks/plan.md)
and [execution](../../../runtime/checks/run.md), with stable input identity and only
necessary resource coordination. When the assigned proof steps are reading-only,
edit and run nothing.

Done when every assigned finding has a verdict. Inability to reproduce alone does
not disprove a defect. Unrelated discoveries go to the report's follow-ups; don't
investigate further or create issues.

## Return

Append, per finding, CONFIRMED with the repro or trace, FALSE-POSITIVE with the
disproof, or UNVERIFIED with the exact missing capability and attempted commands.
Return each finding ID, its verdict and deciding evidence. The orchestrator owns
priority and disposition. When the assignment verifies every finding — a review at
`xhigh` or `max` ([levels](../levels.md)) — return CONFIRMED, PLAUSIBLE or REFUTED
instead: CONFIRMED with its reproduction, PLAUSIBLE with evidence that supports the
finding without reproducing it, REFUTED with the disproof. The review keeps CONFIRMED
and PLAUSIBLE and drops REFUTED, recording its verdict.
