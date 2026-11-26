# Run card

The file autopilot writes to `.scratch/<slug>/autopilot.md` before it goes hands-off.
It holds what the run may do and when it ends, so the stop condition survives
compaction. Use `autopilot_card.py confirm <card> --words "<user words>"` from the
active session and `autopilot_card.py phase <card> <phase>` for transitions.
Record the original words and their source in the ledger. Use `--confirmed-by human` for
direct human confirmation and `--confirmed-by 'relayed(Agent Name)'` for a named relay.
Omitting the flag preserves a recorded source. The label records declared provenance.
Structural validation and session binding do not attest who wrote a message. Silence
and unrelated replies carry no confirmation.

Size the work under [autopilot](SKILL.md). A direct run still writes `alignment.md`
(the brief with its Crew rows) and runs refine; it skips only the interview and the
confirmation round, keeping required checks and independent review.
Record the original goal with `confirm`; a material question or pause-list action
requires the full path.

Use the skill's [pause list](SKILL.md#decide-without-asking) and
[terminal-state rule](SKILL.md#stop). Record mandate-specific restrictions on the card.
Set `delivery: pr-only` when the mandate requires a PR. For network restrictions,
use the [routing policy](../../runtime/routing/choose.md#local-capacity).
Card commands use `--merge-pr <number>` or `--merge-base <planned-base>` for a target
authorized under the [approval path](../../runtime/board/approval-path.md).

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
session: <each host session ID this shell exports, comma-separated; `confirm` and `take-over` rewrite it>
auto-merge: no | yes
mode: full | direct   (`direct`: one or two tickets, no interview; `confirm` records `by rule` with the goal the user typed)
board: local | <the tracker's URL>
dispatch-effort: medium
review-effort: high   (`low` on a direct card unless a level flag sets another)
phase: align | refine | dispatch | pr   (the phase the run is in; kept at the one it stopped in when it ends)
terminal: <state, once ended>
history: <earlier terminal states, each with its date, kept on take-over>
evidence: <commit, report, PR URL>
timer: <resume time and the timer's handle, while `paused-allowance` waits on one>
resume: `autopilot --take-over` (plus `--auto-merge` when granted), after <what the user must do>
```

`confirmed-by:` and `delivery:` are optional State fields for compatibility. Write each
at most once.
