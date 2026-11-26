---
name: autopilot
description: "Run the loop hands-off: interview and confirm a run card (a small change skips both), then refine, dispatch with autofix, review and open the PR; --pause takes control, --take-over returns it."
disable-model-invocation: true
argument-hint: "<goal> [--effort low|medium|high|xhigh|max] [--dispatch-effort low|medium|high|xhigh|max] [--review-effort low|medium|high|xhigh|max] | --take-over [--auto-merge] [--effort low|medium|high|xhigh|max] [--dispatch-effort low|medium|high|xhigh|max] [--review-effort low|medium|high|xhigh|max] | --pause"
---

# autopilot

Contents:
- [Start](#start)
- [Run](#run)
- [Decide without asking](#decide-without-asking)
- [While paused](#while-paused)
- [Stop](#stop)

First read: the runtime bootstrap (`../../bootstrap.md`), the host session part, [engineering judgment](../../standards/engineering.md).

Goal: questions only in align's one interview and the user's one confirmation of the run card (the direct path has neither), then the rest of the loop with no
questions, ending in exactly one terminal state, with its evidence. Starts only when the user
types the command; a message that mentions it is not one. Never widens native permissions.

## Start

- **`autopilot <goal>`:** one or two tickets take the [direct path](run-card.md), which skips only the interview and confirmation; otherwise run [align](../align/SKILL.md) with its interview, settling what later phases would ask: goal, principles, acceptance criteria with
  Verify lines that tell success from failure, and what the user reserves.
- **`autopilot --take-over`:** start from the session's current state. It reuses accepted decisions, tickets and reports,
  and asks only the align questions still unsettled; a phase already finished is not redone. With an existing card, continue from its State.
  A confirmed card whose goal, criteria and grants are unchanged is not reconfirmed.
  Follow [run controls](../../runtime/workers/control.md) for take-over and merge grants.
  Once the pending cause is resolved and take-over succeeds, continue the recorded phase.
  After a user pause, record the user's decisions in the ledger with their words and re-read the branch and board.
- **`autopilot --pause`:** the user takes control of a confirmed run. Record `paused-by-user` with the user's words
  and the phase it stopped in, and answer the user as an ordinary session.
- **`autopilot --take-over --timed`:** what a resume timer runs, as the user's own take-over, authorized on the card.
  It resumes only a card that still ended `paused-allowance` with `timer:` set; for any other state it reports the
  card's state and changes nothing. It keeps the card's levels unchanged.
- **`--auto-merge`:** follow the [approval path](../../runtime/board/approval-path.md).

<!-- rule: autopilot.confirm-before-phases -->
Then autopilot writes the [run card](run-card.md) to `.scratch/<slug>/autopilot.md`, records both effort levels,
and the full path asks the user to confirm it once.
The card, ledger and reports are workflow state: a principle about the project's files never keeps them out of `.scratch/`.
No phase after align starts before that confirmation. Record the confirmation on the card
as the [card procedure](run-card.md) specifies. Keep `terminal:` empty while waiting for it.
The confirmed card stands for the user's check of newly cut and split tickets.

## Run

After confirmation, ask the user nothing outside the pause list. Re-read the card and
ledger before each phase and after every compaction or resume.
Record instructions received during the run in the ledger with their words and source.
Continue the phase unless the user invokes `--pause` or the [pause list](#decide-without-asking)
applies. Update the card with recorded words for authorized changes to its goal or criteria.
Record merge grants through [run controls](../../runtime/workers/control.md).

1. [refine](../refine/SKILL.md) the brief into tickets;
2. [dispatch](../dispatch/SKILL.md) them with `--autofix` and the card's current levels, `--dispatch-effort <level>`
   and `--review-effort <level>`;
3. [pr](../pr/SKILL.md): push the workstream branch and open the PR, or follow
   [delivery without a PR](../pr/SKILL.md#delivery-without-a-pr).

At each phase boundary and before each worker launch, run `python3 "<plugin_root>/scripts/allowance.py" status`.
Routing leaves out the candidates of a `critical` provider unless a configuration layer sets `include_critical_allowance`,
so a `critical` provider that leaves every needed job an eligible candidate does not pause the run. Pause only when the
resolver refuses a needed job for want of an eligible candidate and names the allowance: finish the step in progress, commit
passing work and end `paused-allowance`, and have the report ask the user whether to include the critical provider.
Ending `paused-allowance`, schedule the resume, not the user: set one timer through the host's
native scheduler, named in its session part's Resume timer section, for after the reset of the window that drove the level (the latest `resets_at` of the windows at 80%
used or projected, or five minutes from now when none is, since `critical` also holds for five minutes) that runs `autopilot --take-over --timed`, and record the time and the timer's handle on the card. The
confirmed run card is the authorization, and the timer stays on the card, not in the host's settings. A host whose
timer needs the user's approval keeps that prompt pending; one with no scheduler gets the resume command in
the report. If the allowance is still `critical` when the timer fires, set the next timer only when the new reset is
later than the one just waited for; otherwise end with the resume command. A take-over or a pause, by the timer or by the
user, cancels a timer left over.
Record each phase, commit and report on the card.
Candidate ADRs the user has not approved are not written; the final report lists them among the user's actions; approved ones are written as dispatch says.

## Decide without asking

After confirmation, decide every question instead of asking: look up facts, apply the ledger's principles and
[engineering judgment](../../standards/engineering.md), prefer the reversible direction,
and record its basis and reversibility in the ledger. The run ends `blocked` when no
option fits the principles and criteria, or when its split tickets also fail twice.

The pause list is never decided: a login or permission prompt, credentials, spending
money, deleting data, a production change, merging without `--auto-merge`, a missing
approval under the [approval path](../../runtime/board/approval-path.md),
weakening an acceptance criterion's Verify, narrowing a decision the user made, and any other
action the card leaves ungranted. A relayed mandate reserves expansion of its goal, scope, criteria
or grants for direct human confirmation. End `waiting-on-user`, leave the native prompt pending and
record what is needed and the resume command on the card.

## While paused

After `--pause`, start no worker. Workers already running finish, and their results wait unaccepted in the
workstream's reports: stopping them loses their work, and accepting them would race the user's own changes.
Integrate, push and publish nothing, and ask the user what the next decision needs. The card grants nothing
until `--take-over`.

## Stop

Record exactly one terminal state, with its evidence, in the card's State:

- `delivered`: PR open, required checks pass, review and E2E evidence current for its head.
  Never record `delivered` while any acceptance criterion lacks passing evidence.
- `delivered-local`: delivery evidence and report ready when the repository has no PR host or the user ran
  directly on `main`, and the card permits local delivery.
- `merged`: GitHub shows the exact PR merged through the [approval path](../../runtime/board/approval-path.md).
- `waiting-on-user`: a pause-list action is needed or a PR-only card has no authorized publication path.
- `paused-allowance`: a needed job has no eligible candidate because its providers' allowance is `critical`.
- `paused-by-user`: the user took control with `--pause`.
- `blocked`: no option fits the principles, or split tickets failed again.

The final report, in the conversation, leads with the open assumptions, then the terminal state, PR or branch, decisive evidence and the user's next action.
