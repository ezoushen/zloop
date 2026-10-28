---
name: prototype
agent: builder
inputs: ["question", "worktree", "branch", "report"]
returns: ["## Verdict"]
independent_of: []
acceptance: false
---

# prototype

Align assigns it: answer one question with throwaway code by [prototype](../../prototype/SKILL.md).

## Inputs

The assignment names the `question`, a throwaway `branch` in its own `worktree`
created from the effort's base, and the `report`. An assignment the
[procedure's question rules](../../prototype/SKILL.md#the-question) refuse goes back to align.

## Finish line

Build it as the prototype procedure says, committing only to the assigned throwaway
branch; its done-when applies.

## Return

Append the procedure's `## Verdict` to `report`. Align records
it: the verdict and the question it settled go to the ledger and, when it qualifies,
an ADR offered under the [ADR rules](../../../runtime/domain/adr.md#when-to-offer-one).
