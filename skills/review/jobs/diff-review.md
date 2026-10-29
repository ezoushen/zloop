---
name: diff-review
agent: reviewer
inputs: ["worktree", "report", "base", "diff_base", "head", "changed files", "scope sources", "priority sources", "effort level", "angle"]
returns: ["## Coverage", "## Review round"]
independent_of: []
acceptance: false
---

# diff-review

Review a pinned change range: the implementation layer of the
[shared review procedure](../SKILL.md).

Contents:
- [Inputs](#inputs)
- [Finish line](#finish-line)
- [Return](#return)

## Inputs

The assignment names the `worktree` and `report`; the pinned `base`, `diff_base` and
`head`; the full `changed files` list; the `scope sources` and `priority sources`;
settled direction and blast radius; eligible implementation coverage; the
brief/spec if any; and the review's `effort level` ([levels](../levels.md)) and your
`angle`. The `angle` is `all` at `low`, `medium` and `high`; at `xhigh` one of the six
[angles](../levels.md) or `sweep`; at `max` an angle in its module; a `sweep` assignment
also carries the verified findings. Read `## Scope and priorities` in the report before acting.

## Finish line

Follow [review layers](../layers.md), including blocked dependencies
and incremental invalidation. As implementation-review owner, apply the
[implementation review method](../method.md) in this worker to the exact
pinned range, spec, eligible coverage and standards supplied. If assigned only an
axis/partition, execute that part of the method and return evidence without starting
another full review. Preserve Standards and Spec. Explain the zloop assignment: the changed
range and demonstrated effects on its consumers are the review boundary, not all
improvements the repository could use. Preserve complete axis reports even when
the return summary is short. Missing spec → explicit limitation, never invented
requirements. A PR description is evidence of intent, not an approved standard.

The [level](../levels.md) narrows this job. At `low`, report only findings you proved
yourself, with an executed reproduction or a traced failure path, at most 4, surest
first, and state how many candidates you left out. At `medium`, report every finding
with your own verdict, CONFIRMED with its evidence else UNVERIFIED. At `low` and
`medium`, request no reproduce job, not even for a disputed flag. At `xhigh` and `max`
you review one `angle`. As an angle reviewer, apply the [method](../method.md) through
your angle, within your module at `max`; the levels name the gates the Standards angle
owns. At `xhigh` and `max` set `review_status` and `documentation_status` for your own assignment
as the levels say. As the `sweep`, look for what the angles missed, given the verified findings, and
re-check the documentation gate and the test and comment checks with them. Every level
returns a verdict per finding.

Complete the assigned axes directly. Request any independent partition from the
orchestrator, and a [reproduce](reproduce.md) job for hard or disputed findings.
Don't run unrelated setup/install flows. If a required capability is absent,
record that coverage gap.

Account for each changed file and affected behavior in `## Coverage`. Follow
callers and entry points to understand impact; do not turn context-reading into
an audit of unrelated code. Findings that expose another affected path reopen that
coverage item. Finish all applicable coverage, not a quota of findings or rounds.

Use [code-comments](../../../standards/code-comments.md) for writing-quality criteria;
this job owns the gate and verdict, not a separate writing standard.
Code comments and API documentation (JSDoc, KDoc, docstrings and equivalents) are
part of the source contract and a mandatory review gate. Cover added/edited/deleted
comments and existing documentation whose meaning is affected by the change,
including documentation-only diffs. Check claims against the accepted contract,
implementation and callers using code-comments, including missing documentation
needed for the changed contract or required by project conventions.

Check every added or modified test with
[tautological-tests](../../../standards/kinds/tautological-tests.md): write its
checklist, route it, trace masked rules and re-read flags, dismissing false alarms
with cited evidence. Report a finding only with that kind's cited proof.
A flag you would dismiss is disputed: send it to a [reproduce](reproduce.md) job, given
your contrary evidence and the flagged shape.

For added comments, list the added lines of the pinned reviewed diff with
`git diff -U0 <diff_base> <head>` in the worktree, identify the comments among them
in each file's language, reading each added line in its file so a line inside an
existing block comment counts, and retain that list as evidence. Its entries are
candidates, not verdicts. Apply code-comments and the
project's documented convention: function/interface docs are allowed; other
comments require a constraint that cannot be encoded as a type, assertion, test
or lint rule after trying to encode it. For each violation, cite the exact added
comment, applicable rule and proof (for example, the statements it merely narrates
or an executable encoding of its constraint). A listed comment alone is not proof.

Report only proven violations of these test/comment rules as findings. Drop
unproven candidates from findings, recording the dropped count and reason;
retain UNVERIFIED evidence gaps separately without treating them as confirmed
violations or a passed check. Request unavailable proof through the orchestrator.

A stale, false, contradictory, materially incomplete or broken documented contract
is a hard blocker, never a cosmetic nit or deferred follow-up when caused by this
change. Cite the exact claim in context, its assumptions and the conflicting
code/requirement; a fabricated runtime repro is unnecessary. Check what the contract
actually promises: reproducing behavior does not establish that it violates the
contract. A stronger guarantee suggested by the reviewer remains a design question
until supported by an applicable requirement. If intent is unclear, record an
unresolved contract decision and keep the gate incomplete.
Unrelated pre-existing documentation problems retain the normal follow-up boundary.
Stylistic preferences alone are not correctness defects.
For maintained README, project instructions, skills and references, also check
the [documentation standard](../../../standards/documentation.md):
the changed guidance gives the current contract, with retained history in its
designated record. An added journal or changelog inside maintained guidance is a
policy violation; stale/conflicting instructions keep the documentation gate blocked.
Existing required review reports and ledgers retain their history by design.

Record documentation coverage and evidence in the report, including an explicit
reason if no documentation obligations are affected. Set `documentation_status`
to `passed`, `findings` for confirmed unresolved defects, or `incomplete` for gaps
or unresolved contract decisions. It applies to the report's pinned review head;
recheck affected documentation after repairs before marking it passed.

For each candidate, first establish its relation to the agreed goal/range. An
existing line this change breaks is in scope with a causal trace. An independent
old defect or desirable cleanup goes to `## Follow-ups`: location, evidence,
impact, why unrelated and the decision needed. Preserve/deduplicate its ID and
continue the assigned work. Severity does not authorize scope expansion.
An old pattern repeated in newly added code does not make the new defect
pre-existing. Keep the new instance in scope and the independent old ones in follow-ups.

Within scope, judge design against actual requirements and existing mechanisms.
Name a concrete simpler alternative and its tradeoffs. Simplicity findings are prompts
to investigate, not mandates to extract abstractions, add dependencies or rename
working code. Retain necessary boundary validation, accessibility and meaningful
tests. Personal taste is not a blocking defect.
For each blocking claim, identify the violated contract or project standard and its
source. Before recommending a change, trace or test it against the PR's intended
successful scenario and affected callers, including relevant event ordering.
Record any lost behavior as a tradeoff requiring alignment, not a safe fix merely
because it is small or restores pre-PR behavior.

Challenge verification sufficiency as well as test results: could the supplied
check pass while the accepted outcome is broken? Trace the promised entry point
through its relevant configuration and consumers. A bypass, syntax check or local
replay proves only the path it exercises. Record uncovered accepted behavior as a
verification gap; do not invent wider acceptance criteria or require a production
rollout outside the agreed scope.

Validate correctness with an executed repro or a concrete input/execution trace
showing the failure. Builds follow check [planning](../../../runtime/checks/plan.md) and [execution](../../../runtime/checks/run.md), including
runner input identity and only project-required resource coordination.
Experiments must not alter reviewed production code; record exact commands/results.
Design/simplicity claims need requirement/caller evidence and a behavior-preserving
alternative, not a fabricated runtime failure. Apply the documentation gate above
even when all executable tests pass.

## Return

Write CONFIRMED, FALSE-POSITIVE with disproof, or UNVERIFIED with the missing evidence.
Keep evidence status, scope, priority and disposition separate. A confirmed
regression remains confirmed even if the current deployment is unaffected; only
an applicable accepted scope decision can exclude that configuration. An explicitly
accepted exception retains its finding ID, evidence and decision source, never a
FALSE-POSITIVE label. Low priority does not waive the documentation gate.
Assign priority only from the scope brief's sources; label absent/conflicting
standards provisional and request alignment through the orchestrator. Preserve
both axes and finding provenance. Match the report template, with your verdict in its
front matter (`review_status`, `documentation_status`, `review_head`, `review_diff_base`):
the delivery gate takes them from your report. Don't suppress valid in-scope findings to fit
the summary. Return coverage completion/gaps, prioritized
finding IDs, unresolved decisions and a separate follow-up count. No repairs.
