# Review finish and comment publication

The closing stages of the [shared review procedure](SKILL.md).

## Finish

Reconcile worker reports into the top-level Coverage and Findings before claiming
completion: replace pending placeholders with reviewed items or explicit gaps,
retain superseded evidence with its final disposition, and align front matter,
documentation status, drafts and summary. An unresolved required decision or
coverage gap keeps the review incomplete; confirmed defects alone mean findings.

Prepare one review summary and inline drafts: finding ID, priority with its source,
type, repository-relative path, LEFT/RIGHT, exact diff line, commit SHA, triggering
condition/evidence, consequence and requested change. Confirm anchors against the
pinned diff. Broad design questions or unavailable anchors belong in the summary.
Retain raw Standards/Spec reports; consolidate duplicate comment drafts with both
source axes attached. Exclude fixed/dismissed findings and unrelated follow-ups.
Use native inline previews where supported; otherwise leave comment-ready text in
the report. Apply the [comment policy](comments.md). End with scope, coverage/gaps, prioritized findings, repair results
and E2E/verification status when autofix ran, review report location, and the separate
follow-up list. Distinguish a clean review from incomplete verification.
Summaries must preserve the report's finding dispositions and decision sources;
do not summarize accepted exceptions or unverified claims as disproved defects.
When all findings are resolved but required evidence is still incomplete, report
both facts; reserve "review complete" and "verified" for their satisfied gates.
For operational workstreams, distinguish script readiness, measured benefit, an adoption
decision, per-target application and rollout verification. Implementation counts and
local integration do not establish those outcomes. Reconcile them and remaining
investigation with the selected issues and dispatch report before claiming completion.

## Publish authorized comments

User edits or selected IDs define the authorized batch.
A branch without a verified PR keeps local drafts; comment permission does not
authorize creating a PR. Local autofix commits must not be anchored to an unchanged
remote head or posted as defects already repaired on the remote PR.

Immediately before posting, refresh the PR head, base and discussion. If the range
changed, revalidate affected coverage, findings and anchors first. In confirm mode,
show an updated batch for approval when its reviewed revision or content changes.
Avoid duplicate comments by checking existing threads, including the user's own
previous submissions. Reply to an existing thread when the authorized action is a
follow-up; permission to post new findings alone does not authorize resolving or
dismissing other reviews. Evaluate explicit thread-resolution requests against the
verified fix and native permissions, not an invented prohibition on other authors.

Post only substantiated in-scope claims with valid anchors. Keep uncertain impact
or unresolved contract choices clearly labeled; include questions only when the
user authorized them, never as confirmed bugs. Otherwise keep those concerns local;
`--comments post` alone authorizes confirmed findings, not unverified questions.
Exclude unrelated follow-ups from
both inline comments and the remote summary unless separately requested. If review
coverage is incomplete, disclose its limits while posting any authorized confirmed
subset; publication never upgrades the review or documentation verdict.

Use a COMMENT review unless the user explicitly requested APPROVE or REQUEST_CHANGES.
Verify the returned review/comments against the PR and reviewed commit; record
IDs/URLs, posted finding IDs, revision and authorization source in the report.
On an uncertain or partial response, reconcile remote state before retrying so
already-posted comments are not duplicated. Report posted, withheld and pending
items separately. Do not claim a local draft or pending review was published.
