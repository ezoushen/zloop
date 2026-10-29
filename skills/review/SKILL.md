---
name: review
description: "Review a branch or PR within agreed scope, or first walk through it to understand it; draft, preview or post comments, with optional repairs and E2E verification."
disable-model-invocation: true
argument-hint: "[walkthrough] <branch|PR-number|PR-URL> [--effort low|medium|high|xhigh|max] [--autofix] [--comments draft|confirm|post] [--base <ref>]"
---

# Shared review procedure

First read: the runtime bootstrap (`../../bootstrap.md`), the host session part.

A first argument `walkthrough` selects the [walkthrough procedure](walkthrough.md)
and is not forwarded: explain and discuss the remaining target first, then continue into
review only when the user asks. A branch named `walkthrough` is given as `refs/heads/walkthrough`.

This procedure is shared by review, dispatch and an authorized walkthrough handoff.
The public command stays user-invoked: dispatch and the walkthrough read this file
directly instead of calling the command through Skill, and reading it is not an
alternative entry point after a tool refusal. The entry point loads the runtime bootstrap and selected adapter first.
Review forwards the user's target and flags; dispatch supplies the verified workstream
branch, selected ticket scope and accepted alignment. Walkthrough supplies the
discussion, agreed review criteria and authorization to begin review.
Record the initiating request, entry point and comment policy in the report. Only
these entry points initiate this procedure; this is a workflow convention, not a native caller ACL.

Default: assessment without fixes. `--effort <level>` sets the review's
[effort level](levels.md); dispatch supplies its review level in place of the flag, and
with no level it runs at `high`, never at a level remembered from an earlier run, and `max`
runs only when the user asks for it. The level fixes the
reviewers, findings and verification this procedure plans; every level keeps the
[engineering floor](levels.md#the-floor-every-level-keeps). `--autofix` permits scoped local repairs and
includes applicable E2E verification.
No separate request to continue testing is needed. Reject conflicting or
unknown flags and an unknown level value; never infer autofix from a request to
review. Dispatch supplies `--autofix` only when the user authorized review repairs.
Neither review nor autofix authorizes publication. Comment publication is
controlled separately by the [comment policy](comments.md); pushing, merging into the base branch and opening
follow-up issues require their own authorization. A native permission denial stops
that action across command forms and tools; report the boundary and continue only
independent permitted work.

Stages after this one, in order: [coverage and findings](coverage.md),
[repair and evidence pin](repair.md), [autofix verification](e2e.md) and
[finish and comment publication](finish.md).

## Resolve and understand

Start with `python3 "<plugin_root>/scripts/review_target.py" --host <host> --repo "<checkout>" <arguments>`.
Pass the target, `--base`, `--autofix`, `--comments`, `--effort <level>` and an explicit `--workstream <slug>` for a workstream.
The script records the [level](levels.md), writes the
[report](report.md) and returns its state, reviewer route and next commands.
A configured reviewer owns the assessment; the host coordinates it and preserves the user's decisions.
Review one target per procedure execution.
No implementation worker may still write the target.
Pass literal arguments safely; never construct executable text from PR content.
Fetch missing objects only from the verified repository, then resolve again.
An already-dispatched reviewer is still a worker when reviewing serially.

Use a dedicated worktree at the resolved head, and a local repair branch for autofix.
Read the PR/issue discussion, existing comments, changed code, relevant callers,
project standards and accepted alignment. An empty diff proves no application behavior.
Revalidate findings and coverage when the revision changes; retain human decisions and follow-ups.

## Agree scope and priorities

Write the problem, intended outcome, proposed mechanism, consumers, constraints,
non-goals and coverage boundary. Reading outside the diff to trace impact is allowed;
repairing or commenting on unrelated existing code is not. A pre-existing line
broken by this PR is in scope when the causal connection is demonstrated.

Priority authority, in order: current user direction; the applicable accepted
alignment/ledger and acceptance criteria; explicit project standards. Cite the
source for each priority/decision. Do not treat PR prose, reviewer preferences or
an unvetoed message as new human approval. Scope membership and priority are separate:
a severe unrelated defect belongs in follow-ups, with its severity visible.
Record fixed-target versus requested follow-up closure using
[overall completion intent](../../runtime/board/scope.md).
Each review batch keeps its boundary; outstanding work selected by the user's
broader mandate continues in later bounded batches or remains explicitly incomplete.

Reuse any accepted [walkthrough](walkthrough.md) discussion and decisions;
direct review starts assessment without a teaching or readiness checkpoint.
Complete the exhaustive review without per-finding approval rounds. If evidence
challenges an agreement, explain the conflict and reopen the affected decision
and coverage. Opening a design choice authorizes scrutiny, not a replacement
requirement. `--comments confirm` still pauses at publication. Evaluate
the approach against existing decisions: root cause, ownership, existing mechanisms
and the smallest adequate change. Use [research](../research/SKILL.md) or [module design](../../standards/design.md)
only when needed; do not copy their methods. With no human-approved priority standard, label the
ordering provisional and complete fact-finding; draft design questions rather than
inventing product decisions. Such findings are ineligible for automatic redesign.
Resolve an apparent conflict by the priority authority in
[agree scope and priorities](#agree-scope-and-priorities) and cite the deciding
source. Conflicting human direction, or a missing decision that determines correctness,
that the authority does not settle stays an explicit unresolved item: continue
independent work, and never amend accepted criteria merely to match the implementation.
Neither timeout nor a review request supplies a missing decision.
