---
name: autopilot
description: "Run the whole loop hands-off: settle the goal in one interview, confirm a run card with its stop condition, then refine, dispatch with autofix, review and open the PR without asking again; --pause takes control, --take-over hands it back."
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

Goal: questions only in align's one interview, one confirmed run card, then the rest of the loop with no
questions, ending in exactly one terminal state, with its evidence. Starts only when the user
types the command; a message that mentions it is not an invocation. Never widens native permissions.

## Start

- **`autopilot <goal>`:** run [align](../align/SKILL.md) on the goal, with its interview.
  Settle everything later phases would ask: goal, principles, acceptance criteria with
  Verify lines that tell success from failure, and what the user reserves.
- **`autopilot --take-over`:** start from the session's current state. It reuses accepted decisions, tickets and reports,
  and asks only the align questions still unsettled; a phase already finished is not redone. With an existing card, continue from its State.
  A confirmed card whose goal, criteria and grants are unchanged is not confirmed again;
  a card that ended `waiting-on-user` or `paused-allowance` resumes once its cause is gone. Take over a card with
  `python3 "<plugin_root>/scripts/autopilot_card.py" take-over <card> [--auto-merge] [--effort <level>]
  [--dispatch-effort <level>] [--review-effort <level>]`: it keeps the card's levels unless a level flag is given;
  it moves the terminal state to `history:`,
  clears `terminal:` and sets `auto-merge:` from the flag alone, so the grant of the run that ended lapses
  with its terminal state. `phase:` stays as recorded: a run that ends keeps the phase it stopped in.
  A card that ended `paused-by-user` resumes at once, with no cause to wait for: first record each decision the
  user made while in control in the ledger as theirs, with their words, and re-read the branch and the board, so
  the work the user did is the current state.
- **`autopilot --pause`:** the user takes control of a confirmed run. Record `paused-by-user` with the user's words
  and the phase it stopped in, and answer the user as an ordinary session.
- **`autopilot --take-over --timed`:** what a resume timer runs, as the user's own take-over, authorized on the card.
  It resumes only a card that still ended `paused-allowance` with `timer:` set; for any other state it reports the
  card's state and changes nothing. It keeps the card's levels unchanged.
- **`--auto-merge`:** also grants merging once the PR is open; see [Stop](#stop).

Then autopilot writes the [run card](run-card.md) to `.scratch/<slug>/autopilot.md`, records both effort levels on
it, and asks the user to confirm it once.
The card, ledger and reports are workflow state: a principle about the project's files never keeps them out of `.scratch/`.
No phase after align starts before that confirmation; silence or an unrelated reply is
not one. Record the confirmation on the card, with the user's words. Waiting for this confirmation is not a terminal state: `terminal:` stays empty.
The confirmed card authorizes every action it grants, and stands for the user's check of newly cut and split tickets.

## Run

After confirmation, ask the user nothing. Re-read the card and ledger before each phase and after every compaction or resume.
A message from the user during the run is an instruction, not a pause: follow it, record it in the ledger as the
user's decision with their words, and ask nothing. One that changes the goal, a criterion or a grant also updates
the card with the user's words.

1. [refine](../refine/SKILL.md) the brief into tickets;
2. [dispatch](../dispatch/SKILL.md) them with `--autofix` and the card's current levels, `--dispatch-effort <level>`
   and `--review-effort <level>` (review, repairs, applicable E2E);
3. [pr](../pr/SKILL.md): write the description, push the workstream branch and open the PR, then watch the required checks.
   In a repository with no PR host, publish nothing: pass the PR procedure's delivery gate, write the delivery report and end `delivered-local`.

At each phase boundary and before each worker launch, run `python3 "<plugin_root>/scripts/allowance.py" status`.
Routing already leaves out the candidates of a `critical` provider unless a configuration layer sets `include_critical_allowance`,
so a `critical` provider that still leaves every needed job an eligible candidate does not pause the run. Pause only when the
resolver refuses a needed job for want of an eligible candidate and names the allowance: finish the step in progress, commit
passing work and end `paused-allowance`, and have the report ask the user whether to include the critical provider.
Ending `paused-allowance`, schedule the resume instead of leaving it to the user: set one timer through the host's
native scheduler, named in its session part's Resume timer section, for just after the reset of the window that drove the level (the latest `resets_at` of the windows at 80%
used or projected, or five minutes from now when none is, since `critical` also holds for five minutes) that runs `autopilot --take-over --timed`, and record the time and the timer's handle on the card. The
confirmed run card is the authorization, and the timer stays on the card, not in the host's settings. A host whose
timer needs the user's approval keeps that prompt pending, and a host with no scheduler gets the resume command in
the report. If the allowance is still `critical` when the timer fires, set the next timer only when the new reset is
later than the one just waited for; otherwise end with the resume command. A take-over or a pause, by the timer or by the
user, cancels a timer left over.
Update the card's State as each phase ends, with the commit and report it reached.
Candidate ADRs the user has not approved are not written; the final report lists them among the user's actions; approved ones are written as dispatch says.

## Decide without asking

After confirmation, decide every question instead of asking: look up facts, apply the ledger's principles and
[engineering judgment](../../standards/engineering.md), prefer the reversible direction,
and record its basis and reversibility in the ledger. The run ends `blocked` when no
option fits the principles and criteria, or when its split tickets also fail twice
(dispatch splits a ticket after two failed attempts).

The pause list is never decided: a login or permission prompt, credentials, spending
money, deleting data, a production change, merging without `--auto-merge`, a missing
approval the repository's approval path requires, weakening an acceptance criterion's
Verify, or narrowing a decision the user made. Reaching one ends `waiting-on-user` instead of deciding it;
leave the native prompt pending and record on the card what is needed and the resume command.

## While paused

After `--pause`, start no worker. Workers already running finish, and their results wait unaccepted in the
workstream's reports: stopping them loses their work, and accepting them would race the user's own changes.
Integrate, push and publish nothing, and ask the user what the next decision needs. The card grants nothing
until `--take-over`.

## Stop

Record exactly one terminal state, with its evidence, in the card's State:

- `delivered`: PR open, required checks pass, review and E2E evidence current for its head.
  Never record `delivered` while any acceptance criterion lacks passing evidence.
- `delivered-local`: the same without a PR host, or for a workstream the user
  ran directly on `main`, where a PR is not applicable: branch and delivery report ready.
- `merged`: only with `--auto-merge`. It merges only through the repository's normal path, once required checks
  and the [approval path](../../runtime/board/approval-path.md) are satisfied, never by admin override or self-approval.
- `waiting-on-user`: a pause-list action is needed.
- `paused-allowance`: a needed job has no eligible candidate because its providers' allowance is `critical`.
- `paused-by-user`: the user took control with `--pause`.
- `blocked`: no option fits the principles, or split tickets failed again.

Report in the conversation. The final report leads with the open assumptions, then the terminal state, PR or branch, decisive evidence and the user's next action.
