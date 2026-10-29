# Review coverage and findings

The coverage stage of the [shared review procedure](SKILL.md).

## Review to coverage completion

Follow [review layers and invalidation](layers.md): direction, scope/blast radius,
then implementation. Complete each layer's findings batch; pause dependent lower
coverage when an upstream decision blocks it. Carry accepted alignment forward.
The orchestrator owns direction/scope; one reviewer owns the implementation batch
and its Standards and Spec axes. Share the layer decisions and permitted coverage before launch.
At each completed layer, run the configured
[`review-triage` checkpoint](../../runtime/decisions/checkpoint-command.md)
before choosing an investigation or finding grouping. Identify the layer and head
in its phase assignment and check that record before leaving the layer. This also
applies without autofix; a clean, fully covered layer records an evidenced skip.

Assign a reviewer the [diff-review](jobs/diff-review.md) job with absolute adapter,
agent, job, plugin, repo, worktree, report and scope-source paths; pinned base, diff_base,
head, the full changed-file list, allowed behavior boundary and priority sources.
Keep static reads pinned to Git objects or a stable checkout for the whole review.
Do not share a mutable checkout with E2E that switches base/candidate or changes
experimental inputs; a single startup HEAD check cannot protect subsequent reads.
Dynamic checks need their own reserved stable view and restoration evidence.
Assign one absolute evidence directory outside disposable worktrees and pass it
to every worker. Write retained evidence there
from the start and verify report links resolve before accepting a result. A
`report_transport: return` worker returns evidence for the orchestrator to persist
within its permissions. Experiments still execute in the assigned worktree.
Pass the actual brief/spec when one exists; do not invent a spec or require tickets
for someone else's PR. Apply the [implementation review method](method.md) once for
the eligible implementation batch; preserve both axes while zloop owns coverage,
evidence and disposition.
The diff-review job's [documentation gate](jobs/diff-review.md) is mandatory for every
review: record its coverage and `documentation_status` for the pinned head; a confirmed
in-scope documentation defect blocks approval/delivery whatever its priority.
Another reviewer's approval does not waive these gates or change the user's priority
standard. Choosing not to post a finding does not change its evidence or gate status.

If the project configures an external reviewer and its applicability condition
holds, run it once for this pinned revision with the exact project command/model
and scope. Keep its raw output and model in the report; apply the same scope and
evidence filter to every candidate. Its availability is not a requirement for
standalone review unless the project explicitly makes it a gate.

Continue until every coverage item is reviewed or explicitly blocked, every
candidate is confirmed/dismissed/unverified, and newly implicated paths have been
checked under the layer dependencies. Record blocked dependent coverage explicitly;
it cannot count as reviewed. Reopen only affected coverage when new evidence lands.
Short progress updates do not replace the consolidated result.

Assign a reviewer the [reproduce](jobs/reproduce.md) job for difficult/disputed findings.
No source fixes during validation. Tests and experiments use the isolated worktree;
retain evidence outside it and remove only the experiment edits you introduced.
Record the command, working directory, exit status and diagnostic. A missing file
or failed setup is an execution gap, not a reproduced defect or a disproved finding;
correct permitted setup and rerun before judging the result.
All review checks, including those run by the orchestrator, follow
check [planning](../../runtime/checks/plan.md) and [execution](../../runtime/checks/run.md) for runner inputs, execution proof and
project-required resource coordination. A remote queue needs no local build mutex.

Out-of-scope candidates go immediately to `## Follow-ups` with location, rationale,
impact, relation to the current goal and the decision needed to pursue them. Keep
stable IDs and deduplicate by root cause. Do not pursue, fix, publish or silently
promote them within this review. Return them to dispatch, whose
[overall selection](../../runtime/board/scope.md) may already
authorize triage and implementation in a later batch. Without that authorization,
retain them for a user scope decision. Show important follow-ups at the end without mixing them into
the PR's blockers or letting them consume the active review.
An in-scope question or test waiting on a design decision stays in Findings/Coverage
with that dependency; waiting or assignment to another reviewer does not make it
out of scope.

Some findings stay in scope and cannot be deferred by the agent alone:
- a finding introduced by this batch, including minor ones and comment-rule
  violations such as ticket or decision IDs in code;
- a finding that shares a root cause with selected work;
- a finding that narrows a user decision.

Repair these, or ask the user to defer each one as a numbered decision
[with its context](../../runtime/ask/ask.md). "Logged,
not scheduled" in a status message is not a decision.
