---
name: refine
description: "Split an accepted alignment brief, or a plan agreed in chat, into scoped tickets in the project's tracker, or local Markdown."
disable-model-invocation: true
argument-hint: "<effort-slug> | <the plan agreed in this conversation>"
---

# Shared refinement procedure

Contents:
- [Process](#process)
- [Definition of Ready](#definition-of-ready)
- [Then](#then)
- [Explicit phase composition](#explicit-phase-composition)
- [Local Markdown ticket template](ticket-template.md)

First read: the runtime bootstrap (`../../bootstrap.md`), the host session part.

Goal: tickets a builder with an empty context can build, each with a user story,
acceptance criteria and blocking edges. `/zloop:refine` cuts work into tickets by the board's [slicing rules](../../runtime/board/slicing.md),
each sized for one fresh context window. Input: `.scratch/<slug>/alignment.md` or
[a plan agreed in chat](chat-plan.md). Output: canonical issue IDs/URLs in the configured
tracker, or, for local Markdown only, `.scratch/<slug>/issues/NN-<title>.md` in dependency order,
with the front matter the local scheduler reads. Remote tickets get no local files and need no local
front matter ([board operations](../../runtime/board/board.md)). Dispatch's split step cuts already-selected work by the same slicing rules,
preserving the parent's scope and outstanding outcomes.
Runs only on explicit invocation or an explicitly requested phase; record the initiating request. A denied
operation stops with its input kept (see [Explicit phase composition](#explicit-phase-composition)).

## Process

1. **Read the brief and project instructions.** Resolve areas, hot files and verification
   from [project execution facts](../../runtime/board/project-loop.md); name missing areas from
   modules or shared resources and record them in the alignment. Use the project's [glossary](../../runtime/domain/glossary.md) for the words.
   Read the tracker's related issues and keep their destination, identity, scope and priority.
   Before committing the plan or calling it independently deliverable, record the branch base and any
   unmerged prerequisites; reuse the project's branch convention and preserve an existing branch's work.
   Record standing measurements in `.scratch/<slug>/measurements.json`
   ([format](../../runtime/board/project-loop.md#standing-measurements)): each baseline run at that base,
   each entry's tool and fixtures listed in `paths`.
2. **Slice** by the [slicing rules](../../runtime/board/slicing.md), keeping existing issue identities.
   Before adopting the cut, run the [`refinement` checkpoint](../../runtime/decisions/checkpoint-command.md) with the viable
   cuts and their evidence, or an evidenced skip when the cuts are settled, and check its record before
   calling the breakdown ready.
3. **Draw the edges.** Remote: native blocking links. Local: `blocked_by` IDs. An unresolved
   external prerequisite stays blocked, recorded by the board's conventions. An edge the brief settles is a
   fact; one it does not is a ledger row with its hinge, appended to `.scratch/<slug>/ledger.md`.
4. **Write each ticket** with the project's template, or the local [template](ticket-template.md); reuse a matching issue.
   - Story: one sentence from the actor's side.
   - Criteria: observable Given/When/Then outcomes, each with a `Verify` line naming the
     command or test and who observes it (`code`, `agent` or `user`). Name behaviours and commands, never files or code.
   - **Seams:** the public interfaces its tests reach, existing and higher ones first, as few as will do; the ticket check approves them.
   - Each Verify must fail when its criterion is broken and survive normal variation: start from existing
     baseline observations, and replace an unstable measurement with a discriminating invariant or a justified
     tolerance within the accepted contract. Keep partial evidence apart from the remaining verification; an unavailable required check stays
     explicit and never turns an out-of-scope production operation into a prerequisite.
     Replacing a `Verify` method with a weaker one is the user's decision.
   - Checks only the user can observe go to the user in one request, once an integrated build allows them.
   - Confirm each named command and its configured inputs work in the target environment. Migrations and
     conversions separate metadata compatibility, representative target-value checks and real target
     execution; reference-only checks prove no target behaviour; label sampled evidence.
   - A readiness handoff names the next consumer's real inputs and one bounded path through them in the authorized
     environment; without
     a real-input check that readiness claim stays open.
   - Before a long run, state input counts, workload units, batch/drop policy, estimated resources and duration, and any agreed checkpoint or stop condition. Derive workload conversions (steps, batches,
     epochs) from the actual inputs and runner. A
     runner ticket says whether it delivers a scaffold or the full procedure and lists the promised stages
     ([runner contract checks](../../runtime/resources/resources.md)).
5. **Execution inputs.** Take areas, devices and workload from the project conventions and pass them to
   dispatch with the issue; require no new tracker fields or labels. Local front matter: `touches` from the
   areas; `devices` only when its E2E step boots one; `model: standard`, or `model: deep` when it edits a
   hot file (a workload label; executor configuration owns model and thinking); `blocked_by: []` when nothing
   gates it, never omitted.
6. **Present the breakdown:** each ticket's outcome, dependency and decisive verification, plus
   material trade-offs and ledger rows; explain a rejected cut only when it was viable with a different
   outcome, risk or verification cost. Apply the [question filter](../../runtime/interview/question-filter.md).
   Newly cut tickets wait for the user's check before any implementation; silence or an unrelated reply is not approval.
   Write authorized changes to the board, never report a failed remote write as done, and validate the
   resulting issue graph. Local Markdown: write the files and run `python3 "<plugin_root>/scripts/next_tickets.py" --repo "<repo>" <slug> --check`.

## Definition of Ready

A builder with an empty context can build it from its story, criteria with Verify lines,
edges, the brief, project instructions and the glossary, within one context window. Before reporting
readiness, reconcile every criterion with its discriminating signal, preconditions and known variation, and
list all remaining gaps in one summary. Report spec readiness separately from scheduling readiness, in the
tracker's conventions. A ticket with an open material question is not spec-ready.

## Then

`/zloop:dispatch` schedules the tickets on `blocked_by` and `touches`; builders follow the ticket job, with `alignment.md` as the spec.

## Explicit phase composition

When the user explicitly asks for refinement after another task, such as research, run this
procedure once its input is ready, if the runtime allows loading it directly. Never infer refinement
from research alone or call an explicit-only skill through a forbidden tool. If the runtime needs a
separate user invocation or denies the operation, keep the input and say so.
