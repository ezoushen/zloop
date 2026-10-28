---
name: align
description: "Settle the goal and constraints, then establish a scoped WHAT / WHY / HOW plan, reusing accepted decisions and asking only material unresolved questions."
disable-model-invocation: true
argument-hint: "<idea, plan, decision or workstream-slug> [--interactive]"
---

# align

Contents:
- [Settle the mandate](#settle-the-mandate)
- [Establish the design](#establish-the-design)
- [Save the current alignment](#save-the-current-alignment)

First read: the runtime bootstrap (`../../bootstrap.md`), the host session part, [grilling](../../runtime/interview/grilling.md), [question filter](../../runtime/interview/question-filter.md).

Goal: a settled mandate, then a plan from it sufficient to split and verify. Reuse
the conversation and existing alignment; a mandate the user already stated need
not be settled again. Interview through grilling, including a request to grill the design,
and record consequential decisions in the mandate's ledger, keeping accepted user
decisions apart from reversible agent choices.

- While a governing mandate constraint is unresolved, ask it first and wait for its
  answer before presenting WHAT / HOW alternatives that depend on it.
- Keep provisional mechanisms internal. A request to go through the mandate and the
  design does not combine their question rounds into an implementation menu.
- A request to settle only the goal and constraints ends with the mandate's exit
  block; a plan-only request ends with the plan.

## Settle the mandate

A mandate is the user's goal, principles, constraints and reserved decisions.
Inside it the agent decides; a material choice outside it needs the user.
Work its design tree through grilling: the user decides principles and material
forks; the agent decides implementation detail, showing material ones for veto, at
every depth. A missing source stays an investigation gap for [research](../research/SKILL.md);
ask the human only for material choices they can decide from their priorities.

Work in this order: [establish authority](../../runtime/interview/authority.md),
[filter questions](../../runtime/interview/question-filter.md), then
[record and continue](../../runtime/interview/record.md).

## Establish the design

Read the ADR [index](../../runtime/domain/adr-index.md) and the ADRs in the area first; a plan that
contradicts one proposes a superseding ADR. Then prepare one concise WHAT / WHY / HOW brief:

- **WHAT:** observable outcomes, affected consumers, acceptance evidence and scope
  limits, including compatibility promises and non-goals. Each non-goal cites the
  ledger row that decided it and whether the user or the agent decided. A non-goal
  that narrows an earlier user decision is a question for the user, not an agent choice. Where real
  consumers or their data exist, acceptance uses them as well as fixtures.
- **WHY:** the concrete problem and why existing behavior/mechanisms are inadequate.
- **HOW:** the smallest adequate approach, responsibilities, interfaces and material
  tradeoffs. Apply [module design](../../standards/design.md) when interface/ownership
  decisions need scrutiny.

Choose depth by uncertainty: context for system boundaries, modules for changing
responsibilities, behavior for flows, state and ordering; include only the views that
resolve a live risk. They are lenses on one brief, not three approval turns.
Reuse existing mechanisms; compare an alternative only when it is viable and material.
Use the project's [glossary](../../runtime/domain/glossary.md) and keep it current as terms settle.
Use [prototype](jobs/prototype.md) only for a question that evidence on paper cannot settle.
Leave ordinary types, helper files and library plumbing to implementation; a concrete
interface or snippet belongs in the plan only when needed to settle the contract.

Before declaring the plan ready for refinement, check that each acceptance method can
tell success from failure with the available inputs and environment. Name who can
observe each outcome: code, an agent, or only the user; plan the user-only
observations as one request, sent once an integrated build allows them. Known
unstable or unsuitable evidence is a readiness gap: investigate it now or record the
blocked criterion and next action, rather than discover it at delivery.
Point at the project's current runner contract and record only the task-specific
gates, inputs and ownership, not transport or snapshot internals; refresh affected
assumptions when that contract changes. For an accepted delivery goal, establish the
repository's [approval path](../../runtime/board/approval-path.md) early, so an
impossible self-review is not discovered only after implementation.

Check the brief, and any design ruling it adopts, against the user's recorded
principles quoted in the Mandate section; a ruling that judges only API coherence has
not checked them. Report each deviation as a material fork.

Before selecting an approach or the next investigation, run the configured
[`alignment` checkpoint](../../runtime/decisions/checkpoint-command.md)
with concrete alternatives and evidence. Preserve accepted choices; record an
evidenced skip when the approach is already settled. Check this phase record
before declaring alignment ready.

Show the brief, consequential assumptions and any material unresolved questions.
Once those are settled, continue within existing authorization. With `--interactive`,
wait for the requested check before dependent work, without repeated approval for
unchanged decisions. A vague reply or silence does not choose an unresolved alternative.

## Save the current alignment

For a workstream entering refinement, write `.scratch/<slug>/alignment.md` and keep it
current; decision history stays in the ledger, not in obsolete plans. It contains:

```markdown
# <slug> — alignment

## Mandate
Goal and principles in the user's words, quoted with their ledger IDs; direction
and limits with their decision sources.
Consumers and compatibility obligations, including lockstep dependencies.

## WHAT
Outcomes, scope/non-goals and acceptance evidence.

## WHY
Problem and relevant constraints.

## HOW
Approach, responsibilities, seams and only the diagrams/flows needed to explain them.
Consequential ledger IDs; open decisions and the work each blocks, if any.
```

When new evidence contradicts a decision, reopen it at once, not at the next phase, and tell the user when it was their decision. Then reopen its
dependent design, tickets and review coverage, preserving unaffected conclusions.
Dispatch returns here when implementation disproves a design assumption; refinement
re-cuts only affected work. Independent work may continue when its contracts, worktree
and resources do not depend on the open decision; overall status remains incomplete.

Ready for refinement when the outcomes, boundaries, approach and verification are
clear enough for executable tickets and no unresolved decision blocks that scope.
Close with the [mandate](../../runtime/interview/record.md)'s exit block: user
decisions, agent decisions open to veto, pending user actions, and any carried-over
findings with their disposition.
