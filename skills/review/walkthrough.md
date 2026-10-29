# Walkthrough procedure

Run when the review command is invoked with `walkthrough` before its target, or when
dispatch's handoff was asked for a walkthrough before review. Only `--base` shapes the
walkthrough; `--autofix`, `--comments` or `--effort` given with it are authorization or settings for
the review it continues into, within their stated scope, not a reason to start that review.
Help the user understand the change and form a judgment. The outcome is shared
understanding and review criteria; an exhaustive review is an optional next step.
Keep the walkthrough read-only, with explanations in the conversation. It does not
require a board, review report, worker launch or verification run. Export only when
requested. Use the brief command for a requested progress snapshot.

## Establish the change

Resolve the user's target with
`python3 "<plugin_root>/scripts/review_target.py" --resolve --repo "<checkout>" <target> [--base <ref>]`.
Pass literal arguments safely. Verify the repository identity; fetch missing
objects only from that repository, then resolve again. Preserve the user's checkout.
Keep the returned repository, target, base/head and merge base with the discussion.
Read the PR/issue discussion, changed code, relevant callers, project standards and
existing accepted decisions. Research discoverable facts before explaining them.
If the target is unclear, establish what is known and ask for the missing selection.

Read [explain](../explain/SKILL.md) and choose the smallest in-chat view
that clarifies the change. The brief command needs no invocation here.

## Teach and discuss

Teach the change in layers, as [explain](../explain/SKILL.md) says,
WHY, WHAT, then HOW, and keep these walkthrough rules:

1. **Explain the change.** Explain material tradeoffs, alternatives and scope limits;
   distinguish observed behavior, documented author rationale, accepted requirements
   and hypotheses.
2. **Discuss and deepen.** Invite the user's questions and reactions. Connect each
   answer to what it means for the change and its review. Resolve confusion, by a
   [re-pitch](../../runtime/ask/ask.md#re-pitch-what-lost-the-reader) when the
   user is lost, before asking the user to accept the affected scope or behavior.
3. **Guide the next step.** Recommend the next topic or the transition to review.
   Adapt depth to the user; a fixed question count or an answered scope question does
   not establish readiness to review.
4. **Summarize understanding.** Apply the [question filter](../../runtime/interview/question-filter.md)
   to material unresolved choices. Summarize intended behavior, scope, constraints,
   accepted tradeoffs, open questions and recommended review priorities. When the
   user has enough context, recommend reviewing or exploring a remaining question.
   If they wanted understanding only, finish with this summary; no review is required
   to complete the walkthrough. Agreement is not proof of correctness.

## Continue into review when requested

When the user indicates readiness to review, run the
[shared review procedure](SKILL.md) in the same conversation. A new
task or second `/review` invocation is unnecessary. If they already authorized
review once a named question was settled, announce the transition and proceed
without another approval. Silence or scope agreement alone does not authorize it.
Preserve native invocation policy and tool refusals; this handoff loads the shared
procedure directly, not the review command again through a Skill call.

Carry the repository/target and comparison, explanation, consequential discussion,
accepted decisions with sources, constraints, unresolved questions, priorities and
continuation authorization into the review report. Refresh the target's base/head
and revalidate affected facts if it moved; retain applicable human decisions.
Use the same review standards and evidence gates as direct review. Reuse existing
repair/publication authorization only within its stated scope; otherwise review
starts without fixes and with local comment drafts. Keep dependent coverage blocked
where an unresolved decision matters, while independent review proceeds. If evidence
challenges an agreement, explain the conflict and reopen the affected decision.
