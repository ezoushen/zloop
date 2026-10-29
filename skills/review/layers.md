# Review layers and invalidation

The orchestrator completes layers in dependency order for each affected scope.
Record their coverage in the existing review report; no separate state store.

| Layer | Complete when |
|---|---|
| Direction: WHY / WHAT / HOW | The concrete problem, intended outcome and mechanism have been challenged against requirements, existing mechanisms and viable simpler alternatives. All discoverable concerns at this layer have a disposition. |
| Scope and blast radius | Changed behaviors, contracts, consumers, dependencies and configuration paths are mapped, including unchanged code affected by the change. Scope limits and verification obligations are explicit. |
| Implementation | The [implementation review method](method.md)'s Standards and Spec analysis covers that boundary, including code, tests and the mandatory documentation gate. Every candidate has deciding evidence or a concrete gap. |

For a foreign PR, establish direction from the issue, author discussion, code and
project standards; PR prose describes intent, not human approval. For our own
implementation, reuse accepted alignment and inspect for drift. Reopen a settled
decision only for new conflicting evidence or user direction.
Security, testability, integrity and maintainability are cross-cutting lenses on
each relevant layer, not additional whole-PR passes. Record inapplicability briefly
when needed to make coverage clear. A smaller design is an alternative with
tradeoffs, not a blocker without a violated requirement or applicable standard.

## Complete a batch before moving down

Collect all currently discoverable findings within a layer's assigned coverage
before handing off or repairing. Reconcile overlapping claims by evidence and
root cause; keep axis provenance. No top-N cutoff and no extra unchanged full
pass merely to search for one more issue. A progress update is not a final batch.
Include adjacent state transitions, ownership/lifetime cases and affected public
contracts in the same scoped batch. Batch independent checks within the assigned
reviewer rather than creating a worker or round for every case. Use an independent
oracle where shared implementations can agree on the same wrong result, following
[check acceptance](../../runtime/checks/accept.md).

If direction or scope is rejected or a material decision remains unresolved,
finish that layer's independent fact-finding and batch its concerns. Mark the
lower coverage that depends on that decision blocked; pause its detailed review,
repairs and verification. Continue other scopes only when their contracts,
worktrees and resources are independent. Different filenames alone do not establish independence.
Overall review stays incomplete until every required dependency and coverage gap
is resolved. A confirmed implementation defect does not stop unrelated checks in
that implementation batch.

## One owner of implementation review

Assign one implementation-review owner for the eligible batch. That owner applies
the [implementation review method](method.md) once, with the accepted boundary and
prior coverage, and completes both axes directly; serial execution keeps the same two
axes and both complete axis reports. Additional independent scope assignments belong
to the orchestrator, within available capacity and selected executor settings; a
worker does not create a nested review team.

## Reopen only affected coverage

Before another round, compare requirements/decisions, target base/head, code,
consumers, dependency pins, inputs and environment with the recorded evidence.
For each change, name the affected layer and scope and reopen its dependent
coverage, including unchanged contracts or callers. A diff alone is insufficient.
A direction change may invalidate the whole affected implementation; a local
repair may leave direction and unrelated behaviors settled. Reuse unaffected
conclusions and valid verification with their original provenance.

Keep stable finding IDs. Label a newly discovered finding's cause when known:
missed original coverage, introduced by repair, new evidence, or changed scope/base.
If unclear, say so. Review every repair delta and its affected behavior before
closing findings; no automatic new full-PR round. Completion requires all assigned
coverage and candidates accounted for, including explicit blocked gaps, not a
fixed round count or a promise that no unknown defect can exist.
