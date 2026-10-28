# Run card

The file autopilot writes to `.scratch/<slug>/autopilot.md` before it goes hands-off.
It holds what the run may do and when it ends, so the stop condition survives
compaction. Fill every section; the user confirms it once.

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
session: <the host's session ID where it exposes one in the environment, else unknown; never a path>
auto-merge: no | yes
phase: align | refine | dispatch | pr   (the phase the run is in; kept at the one it stopped in when it ends)
terminal: <state, once ended>
history: <earlier terminal states, each with its date, kept on take-over>
evidence: <commit, report, PR URL>
timer: <resume time and the timer's handle, while `paused-allowance` waits on one>
resume: `autopilot --take-over` (plus `--auto-merge` when granted), after <what the user must do>
```
