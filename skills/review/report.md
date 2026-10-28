# Review report

Create one Markdown report outside the disposable worktree. For worker attribution,
follow the [routing decision contract](../../runtime/routing/report.md).
Front matter uses literal scalars, never quoted placeholders in a finished report.
`review_by` and `e2e_by` name the collected worker attempts (one, or a list) whose own
reports gave those statuses at those heads; for checks the orchestrator ran itself, `e2e_by`
names their run_check logs. The delivery gate takes each status only from them. An effort the
user ran directly on main adds `effort_branch: main`; the gate reads `feat/<slug>` otherwise.
`autofix` is `true` only when repairs are authorized; while it is unset, dispatch's
`--autofix` counts. `blocker` names the concrete blocker
that stops delivery, and is `none` once nothing does. The stop hooks read both fields:

```markdown
---
autofix: <true|false>
blocker: none
review_status: incomplete
documentation_status: incomplete
review_head: <40-hex commit>
review_base: <40-hex base tip>
review_diff_base: <40-hex merge base>
e2e_status: not-run
e2e_head: none
review_by: none
e2e_by: none
---

## Scope and priorities
Repository, target/PR URL, worktree, local repair branch if any, entry point, initial head.
Problem, current/proposed behavior, approach and tradeoffs, allowed changes and non-goals.
For a walkthrough handoff: the explanation shown, consequential discussion and user
direction, remaining questions/next step, and the user's readiness to start review
or prior authorization to continue once a named question was settled.
User decisions and priority rules, each with its source; unresolved decisions.
Which priorities are accepted versus provisional. Do not invent approval.
Comment policy: draft/confirm/post, initiating request/source, and whether posting
permission covers this invocation, ongoing review rounds or only specified IDs.
Resolved worker profile/source, executor/settings, handles and completion/blockers.
For configured pools/session overrides: the routing decision,
including proposer, accepting/overriding orchestrator, method, evidence and
previous → requested settings. Include root session ID, policy source,
requested/confirmed settings and runtime identity. Record
accepted outcomes, classified failures, switch reasons and available time/usage
evidence with the relevant assignment; preserve prior attempts across switches.
Absolute evidence directory outside disposable worktrees; all retained links resolve.
For phase checkpoints, record the family, phase assignment, checkpoint ID and
decision ID or skip reason. Check each reached boundary before
its handoff; a worker-selection record does not account for triage or recovery.
For consulted boundaries include the host action, outcome or pending state, and
the journal completeness result or explicit capture gap.

## Coverage
| Layer | File / behavior / consumer | Revision / decision source | Status | Evidence or blocking dependency |
|---|---|---|---|---|
Direction, scope/blast radius and implementation have separate coverage. Record
why preserved evidence still applies and what invalidated reopened items.
Security/testability/integrity/maintainability apply within relevant layer coverage.

### Documentation gate
For the pinned review head: affected comments/API docs, their contract claims and
evidence against requirements/code/callers; include examples, references and any
required documentation checks. Record unresolved defects or coverage gaps.
If nothing is affected, state the inspected scope and why no documentation
obligation applies. `passed` requires that evidence and no unresolved obligations;
otherwise use `findings` or `incomplete`. Confirmed in-scope defects are hard blockers.

## Review round 1
### Direction and scope
Consolidated findings/decisions and the lower coverage they unblock or block.
### Standards
Complete findings from the Standards axis.
### Spec
Complete findings from the Spec axis, or the missing-spec limitation.
### Findings
| ID | Axis/type | Scope evidence | Priority/source | Verdict | Repro/trace | Disposition |
|---|---|---|---|---|---|---|
Verdict: CONFIRMED, FALSE-POSITIVE (disproved), or UNVERIFIED.
Disposition: comment, question, fixed, follow-up, or blocked; no evidence by vote.
An accepted exception names its deciding source and retains a CONFIRMED verdict.
Identify mandatory blockers separately from repair priority.
For blockers: exact violated contract/standard and source. For proposed changes:
effect on the intended successful scenario and affected callers, evidence or gap.
Keep in-scope design dependencies here and in Coverage until resolved.
For later findings, record cause when known: missed coverage, repair-introduced,
new evidence, or scope/base change; otherwise unknown.

## Follow-ups
| ID | Location | Evidence, concern and impact | Why outside scope | Decision needed | Status |
|---|---|---|---|---|---|
Keep across rounds; promotion requires recorded user direction.

## Fixed round 1
Only when repairs ran: finding IDs, commits, original repros and required checks.

## Comment drafts
Review summary, then one entry per actionable in-scope root cause:
ID, priority/source, type, path, side, line, commit, evidence, impact, requested action.
Broad design questions stay in the summary. No remote submission implied.

## Publication
Local-only, awaiting confirmation, posted, partial or blocked. Record previewed
revision/batch, user approval or direct-post request, posted IDs/URLs and commit,
withheld items and uncertain responses needing reconciliation. Publication status
is independent of review completeness. Preserve records across subsequent rounds.

## E2E
Required for autofix: validated head, applicable checks and their scope sources,
commands/exits, artifacts and timestamps. For each check record reuse/execute and
why, expected versus observed activity, and any invalid-run condition. Reused
evidence retains its execution head/source and the code/input/environment comparison.
Record exclusions, explicit project omissions and unavailable or failed checks.
On pause/resume retain completed iterations and the remaining work; explain any
required restart. Distinguish execution, resource queueing and user-paused time.
For each unfinished gate: next action and responsible job, or concrete blocker.
Read-only review may leave this not-run; autofix completes verification or records
failed/incomplete status with concrete blockers. Never invent a passing run.
```

Add numbered rounds as needed; don't overwrite previous evidence. The report is
the durable record, not the conversational word limit. A complete review with open
findings is `findings`; a gap in coverage or required evidence is `incomplete`.
