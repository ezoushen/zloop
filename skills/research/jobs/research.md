---
name: research
agent: researcher
inputs: ["worktree", "head", "question", "report"]
returns: ["## Findings"]
independent_of: []
acceptance: false
---

# research

## Inputs

The assignment names a bounded question, its consumers, the absolute worktree and
pinned head, source leads and the report path outside that worktree. Missing access
is a gap; it does not authorize changing the subject or its configuration.

## Finish line

Apply the [research rules](../SKILL.md#rules) directly; this assignment is already
the helper, so launch no further helper. Check the primary source that owns each
claim and every real consumer in scope. Keep source versions and observation times
with volatile facts. Distinguish observations, inferences and unresolved questions.
Stop when the question is answered or the remaining evidence is unavailable; name
what would settle each gap. Recommend no mutation as if it had already happened.

## Return

Return `## Findings` with the answer, source citations, per-consumer coverage and
open points. Write only the assigned report, or return its complete text when the
assignment names `report_transport: return`. Include assignment ID and pinned head.
