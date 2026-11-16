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

First read: the runtime bootstrap (`../../bootstrap.md`), the host session part.

Goal: a settled mandate, then a plan from it sufficient to split and verify. Reuse
the conversation and existing alignment; a mandate the user already stated need
not be settled again. Interview through grilling and record consequential decisions in the mandate's ledger, keeping accepted user
decisions apart from reversible agent choices.

- While a governing mandate constraint is unresolved, ask it first and wait for its
  answer before presenting WHAT / HOW alternatives that depend on it.
- Keep provisional mechanisms internal; going through the mandate and the design does
  not merge their question rounds into an implementation menu.
- A request to settle only the goal and constraints ends with the mandate's exit
  block; a plan-only request ends with the plan.

## Settle the mandate

A mandate is the user's goal, principles, constraints and reserved decisions.
Inside it the agent decides; a material choice outside it needs the user.
Before an interview, read [grilling](../../runtime/interview/grilling.md) and the
[question filter](../../runtime/interview/question-filter.md). Work the design tree
through them: the user decides principles and material
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
  For comparative outcomes, name the observable result, comparison inputs and baseline,
  acceptance rule, preserved behavior and observer or command; qualitative outcomes use
  an agreed scenario and rubric. Derive targets from accepted requirements, never invent
  them. An observational audit may deliver measurements and ranked options without an
  improvement target; a material unresolved target blocks dependent implementation.
- **WHY:** the concrete problem and why existing behavior/mechanisms are inadequate.
- **HOW:** the smallest adequate approach, responsibilities, interfaces and material
  tradeoffs. Apply [module design](../../standards/design.md) when interface/ownership
  decisions need scrutiny.

Choose depth by uncertainty: context for system boundaries, modules for changing
responsibilities, behavior for flows, state and ordering; include only the views that
resolve a live risk. They are lenses on one brief, not three approval turns.
When correctness depends on state or ordering, record the affected entity and owner,
reachable states, and event plus guard → next state plus side effects. State invariants
and applicable retry, interruption, timeout, cancellation and resume behavior. Cite
implementation or check evidence and mark unknowns. Cover the changed lifecycle and
affected callers; this evidence decides whether a structural change is needed.

Inspect the repository, standard library and current dependencies for the required
capability first. If a capability or design question remains unresolved, investigate
relevant external implementations through [research](../research/SKILL.md). Compare only
viable, material alternatives with the smallest local approach on required behavior,
supported versions, interface fit, license constraints, dependency cost and migration
or removal cost. Record exact versions and decisive evidence. Stop when an option meets
the accepted constraints and identified facts that could change the choice are resolved;
otherwise name the remaining fact and next probe. Adoption stays within the mandate.
Use the project's [glossary](../../runtime/domain/glossary.md) and keep it current as terms settle.
Use [prototype](jobs/prototype.md) only for a question that evidence on paper cannot settle.
Leave ordinary types, helpers and plumbing to implementation; put an interface or
snippet in the plan only to settle a contract.

Before declaring the plan ready for refinement, check that each acceptance method can
tell success from failure with the available inputs and environment. Name who can
observe each outcome: code, an agent, or only the user; plan the user-only
observations as one request, sent once an integrated build allows them. Known
unstable or unsuitable evidence is a readiness gap: investigate it now or record the
blocked criterion and next action.
Point at the project's current runner contract and record only the task-specific
gates, inputs and ownership, not transport or snapshot internals; refresh affected
assumptions when that contract changes. For an accepted delivery goal, establish the
repository's [approval path](../../runtime/board/approval-path.md) early, so an
impossible self-review shows sooner.

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
Outcomes, scope/non-goals and acceptance evidence, including comparative baselines and criteria.

## WHY
Problem and relevant constraints.

## HOW
Approach, responsibilities, seams and only the diagrams/flows needed to explain them.
Consequential ledger IDs; open decisions and the work each blocks, if any.

## Crew
From `.scratch/crew.md`: the areas whose rows are taken from it, or none; omit while it is absent.
One row per expert: agent; skills, each with the fact that selects it; area; whether it
starts before a diff exists.
```

When new evidence contradicts a decision, reopen it at once and tell the user if it was theirs. Then reopen its
dependent design, tickets and review coverage, preserving unaffected conclusions.
Dispatch returns here when implementation disproves a design assumption; refinement
re-cuts only affected work. Independent work may continue when its contracts, worktree
and resources do not depend on the open decision; overall status remains incomplete.

Ready for refinement when the outcomes, boundaries, approach and verification are
clear enough for executable tickets and no unresolved decision blocks that scope.
Close with the [mandate](../../runtime/interview/record.md)'s exit block: user
decisions, agent decisions open to veto, pending user actions, and any carried-over
findings with their disposition.
