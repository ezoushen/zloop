# Run card

<!-- rule-ref: board.relayed-card-delivers-pr-only -->
The file autopilot writes to `.scratch/<slug>/autopilot.md` before it goes hands-off.
It holds what the run may do and when it ends, so the stop condition survives
compaction. Use `autopilot_card.py confirm <card> --words "<user words>"` from the
active session and `autopilot_card.py phase <card> <phase>` for transitions.
Record the original words and their source in the ledger. For full cards the
[human intent gate](../../runtime/workers/control.md#verify-human-intent) verifies
and records direct human confirmation. A named relay uses
`--confirmed-by 'relayed(Agent Name)'` and records [PR-only delivery](../../runtime/board/approval-path.md#automated-merge).

Size the work under [autopilot](SKILL.md). A direct run still writes `alignment.md`
(the brief with its Crew rows) and runs refine; it skips only the interview and the
confirmation round, keeping required checks and independent review.
Record the original goal with `confirm`; a material question or pause-list action
requires the full path.

Use the skill's [pause list](SKILL.md#decide-without-asking) and
[terminal-state rule](SKILL.md#stop). Record mandate-specific restrictions on the card.
Set `delivery: pr-only` when the mandate requires a PR. For network restrictions,
use the [routing policy](../../runtime/routing/choose.md#local-capacity).
Request any merge grant through managed control under the
[approval path](../../runtime/board/approval-path.md).

```markdown
# <slug> — autopilot run card

## Goal
The user's goal, in their words, with its ledger IDs.

## Acceptance criteria
- [ ] Given <state>, when <action>, then <observable outcome>.
      Verify (code|agent|user): <command, test or the user's check>

## Stop condition
The applicable autopilot terminal state and its evidence.

## Granted actions
The phases and external actions granted for this run, including any authorized resume timer or merge.

## Pause list
The applicable autopilot pause list and this mandate's reserved actions.

## Assumptions
Decisions made without the user are rows in `.scratch/<slug>/ledger.md`; open ones listed here.

## State
confirmed: no | <date> "<the user's words>"
session: <bound host session IDs, comma-separated>
auto-merge: no
mode: full | direct   (`direct`: one or two tickets, no interview; `confirm` records `by rule` with the goal the user typed)
board: local | <the tracker's URL>
dispatch-effort: medium
review-effort: high   (`low` on a direct card unless a level flag sets another)
phase: align | refine | dispatch | pr   (the phase the run is in; kept at the one it stopped in when it ends)
terminal: <state, once ended>
history: <earlier terminal states, each with its date, kept on take-over>
evidence: <commit, report, PR URL>
timer: <resume time and the timer's handle, while `paused-allowance` waits on one>
resume: `autopilot --take-over` after <what the user must do>
```

`confirmed-by:` and `delivery:` are optional State fields. Write each at most once.
<!-- rule-ref: pr.publish-probe-before-dispatch -->
`publish-probe:` is an optional State field that `publish_probe.py <card>` writes, as
`<ok|failed> <UTC time> <remote or reason>`; `autopilot_card.py phase` enforces the
[PR standard's rule](../../standards/pr.md#probe-the-publish-route-first) on it.

To request a fresh merge grant at resume, follow the
[managed take-over procedure](../../runtime/workers/control.md#verify-human-intent).
