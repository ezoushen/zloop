# Dispatch review handoff

The handoff stage of [dispatch](SKILL.md), after the [iteration](iteration.md).

## Review handoff

Stop after implementation only when the user explicitly requested that boundary;
state that review has not run and provide the exact standalone review command.
Otherwise continue in this invocation, without waiting for another user turn.

For each selected effort with integrated changes, finish or reconcile its writers,
then apply [housekeeping](../housekeeping/SKILL.md) to the effort worktree, list its
[candidate ADRs](../../runtime/domain/adr.md#deliver-an-efforts-decisions), each
[with its context](../../runtime/ask/ask.md), and commit the approved ones,
merge the verified `main` tip and run the ADR number check. Commit
intended tracked cleanup/documentation changes there before pinning the review
head; preserve unrelated local edits. Run the
[shared review procedure](../review/SKILL.md), read as a file (its typed-only setting governs
invocation, not reading), on the verified effort branch,
one target at a time. Pass the selected ticket scope, accepted alignment/ledger,
priority sources, project instructions and report path. Include `--autofix` only
when repairs are authorized. If the user explicitly requested a walkthrough before
review, follow [walkthrough](../review/walkthrough.md) first and honor its transition
policy. With no integrated changes, report that there is no review target.

Review the integrated result even if other selected tickets are blocked; record
missing implementation against the selected criteria and keep delivery blocked.
Apply the review's layer dependencies: continue independent scopes, pausing lower
coverage whose contracts or resources depend on a blocked decision.
Do not broaden the assignment to unselected tickets or unrelated branch changes.
Keep a review limited to selected tickets distinct from effort-wide delivery proof.
An active writer that cannot be reconciled safely or unavailable review capability
leaves the handoff blocked with its reason, never silently skipped or marked clean.

Do not call `Skill(zloop:review)` or ask the user to run `/review` to continue
an ordinary dispatch. The public review command remains user-invoked; its shared
procedure is already part of this dispatch. Native tool refusals
remain binding, including those from reviewer dependencies; stop the denied action
and record the gap. There is one review loop, maintained in the shared procedure.

Record its report, scope/coverage, findings and follow-ups alongside implementation
status. With `--autofix`, the shared procedure includes repair integration and
applicable E2E before returning; do not run a duplicate verification phase in
dispatch. Read and execute [Verify autofix through E2E](../review/e2e.md)
before returning from an autofix handoff, including when all code findings are closed.
Under autofix ([report fields](../review/report.md)), continue until `--delivery` passes,
or record the concrete `blocker`.
`pr` still requires current review/E2E evidence as well as done tickets.
Once the remaining gates are complete or explicitly excluded, retire an unused
ticket worktree through [housekeeping's retirement checks](../../runtime/resources/retire-worktree.md).
Keep the effort worktree and any checkout still needed by an active assignment.
Then reconcile the overall queue under the board's continuation rules. Return a
final result only when that scope is complete or cannot progress past documented
blockers; do not end a backlog-wide invocation at the first batch's delivery gate.
Before the result lists an item as unverified, run any check for it that fits the
remaining budget, such as a search, a read or a short command, and report what it
showed. List as unverified only items that need missing access, an owner decision
or a run beyond budget, and name which.
