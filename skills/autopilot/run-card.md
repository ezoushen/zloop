# Run card

The file autopilot writes to `.scratch/<slug>/autopilot.md` before it goes hands-off.
It holds what the run may do and when it ends, so the stop condition survives
compaction. Fill every section; the user confirms it once. The State always records both effort levels on their own lines, `dispatch-effort:` and `review-effort:`,
defaults included (medium and high), so the user confirms them with the card; the start or a take-over sets them
([autopilot](SKILL.md)).
`--effort` sets both steps, and a split flag overrides it for its step.
A start with no level writes these defaults, never a level typed earlier.
`max` runs only when the user asks; an unknown flag or level value is refused.

Record the user's confirmation from the shell of the session running the autopilot, with
`python3 "<plugin_root>/scripts/autopilot_card.py" confirm <card> --words "<the user's words>"`, and set `phase:` with
`autopilot_card.py phase <card> <align|refine|dispatch|pr>`; each refuses what it cannot record and says what to add.

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
narrowing a user decision, and anything not granted.

## Assumptions
Decisions made without the user are rows in `.scratch/<slug>/ledger.md`; open ones listed here.

## State
confirmed: no | <date> "<the user's words>"
session: <each host session ID this shell exports, comma-separated; `confirm` and `take-over` rewrite it>
auto-merge: no | yes
board: local | <the tracker's URL>
dispatch-effort: medium
review-effort: high
phase: align | refine | dispatch | pr   (the phase the run is in; kept at the one it stopped in when it ends)
terminal: <state, once ended>
history: <earlier terminal states, each with its date, kept on take-over>
evidence: <commit, report, PR URL>
timer: <resume time and the timer's handle, while `paused-allowance` waits on one>
resume: `autopilot --take-over` (plus `--auto-merge` when granted), after <what the user must do>
```
