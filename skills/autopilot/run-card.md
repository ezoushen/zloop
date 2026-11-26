# Run card

The file autopilot writes to `.scratch/<slug>/autopilot.md` before it goes hands-off.
It holds what the run may do and when it ends, so the stop condition survives
compaction. Use `autopilot_card.py confirm <card> --words "<user words>"` from the
active session and `autopilot_card.py phase <card> <phase>` for transitions.
Pass `--confirmed-by human` for direct human confirmation, or
`--confirmed-by 'relayed(Agent Name)'` when a named agent conveyed it. Record the
original words and who conveyed them in the ledger. Omitting the flag preserves
any provenance already on the card. Silence or an unrelated reply cannot confirm it.
The script owns flag/default handling, session binding and structural checks.

Size the work under [autopilot](SKILL.md). A direct run still writes `alignment.md`
(the brief with its Crew rows) and runs refine; it skips only the interview and the
confirmation round, keeping required checks and independent review.
Record the original goal with `confirm`; a material question or pause-list
action requires the full path. Never treat structural validity as user consent.

```markdown
# <slug> — autopilot run card

## Goal
The user's goal, in their words, with its ledger IDs.

## Acceptance criteria
- [ ] Given <state>, when <action>, then <observable outcome>.
      Verify (code|agent|user): <command, test or the user's check>

## Stop condition
Ends in exactly one of: `delivered` (PR open, required checks pass, every criterion
has passing evidence), `delivered-local` (no PR host: branch and delivery report
ready), `merged` (only with `--auto-merge`), `waiting-on-user`, `paused-allowance`, `paused-by-user` (the user's `--pause`), `blocked`.

## Granted actions
refine; dispatch with autofix (review, repairs, applicable E2E); push the workstream branch;
open the PR and watch its checks; resume itself on `paused-allowance` with one timer; merging through the normal path only when `auto-merge: yes`.

## Pause list
Login or permission prompts, credentials, spending money, deleting data, production
changes, merging without `--auto-merge`, a missing approval, weakening a Verify,
narrowing a user decision, and anything not granted. A relayed mandate also pauses
for direct human confirmation before expanding the goal, scope, criteria or grants.

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

In State, `confirmed-by:` accepts `human` or `relayed(agent)`. Set
`delivery: pr-only` when the mandate requires a PR. Both fields are optional
for older cards. A present value must be valid and each field may appear once.
A PR-only card cannot end `delivered-local` through managed control.

## Guarded merge

To confirm an explicit `auto-merge: yes` grant or take over with `--auto-merge`,
pass `--merge-pr <number>` to the card command, or `--merge-base <planned-base>`
before the PR exists. The required checks
must be verified before the grant changes. Relayed confirmation cannot grant or
use auto-merge. A `waiting-on-user` exit leaves the
current grant and delivery state intact. Report the blocker and resume after the
base's required checks can be verified.

For merge submission, use the [approval path](../../runtime/board/approval-path.md)
for a legacy card or the [managed merge command](../../runtime/workers/control.md)
for a managed card. Both verify the PR's actual base again before submitting.
