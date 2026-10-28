# Feedback and recovery

Before retrying a blocked or failed worker, apply the [escalation checkpoints](escalation.md).

## Feedback and recovery

Classify the observed response before recovering. HTTP 429 alone does not establish
exhausted credit: inspect the service's error details and retry metadata. For a
temporary rate limit, retain the supported retry time, reduce admission against
the [shared account budget](../resources/capacity.md), and use a bounded retry
only when availability can change. If an eligible alternative is available, the
ordinary fallback can continue immediately. Proven exhausted quota or an outage
excludes candidates on the affected service/account; a new model name on that
same account is not recovery. Record the classification, affected account and
retry/fallback condition, without exposing credentials. Never create parallel
retry bursts or unbounded backoff that strands the assignment.

Availability failures trigger automatic recovery within the allowed pool: exhausted
credits/quota, unavailable model/provider/host, missing executor capability or
an unsupported requested model/effort pair. Preserve the failure evidence, exclude
the failed candidate, and re-resolve with the remaining `--eligible-candidates`.
Exclude other candidates sharing a proven failed service/account or capability;
switching from one Codex model to another does not cure an exhausted Codex account.
Do not ask for another approval when a suitable configured alternative exists.
If resolution requests selection, follow its `selection_router` through the
[assignment selection flow](../routing/choose.md). A profile preference excluded by availability does not get retried;
its candidate-specific thinking is also released for the replacement. State the
switch and continue the same assignment, scope and gates. Never lower
an effort bound or approximate a setting to force a launch.

Retain exclusions in existing session/assignment state; do not keep retrying a known
failure until availability actually changes. A fixed instance has no alternatives.
If all permitted candidates are exhausted, report the blocker and ask only for the
missing decision. Explicit user pins and `fallback:false` remain constraints.
Authentication unavailability can use an already authorized alternative account;
never acquire credentials, change permissions or bypass a sandbox/tool denial.

A failed worker's partial report cannot close its obligation. Reconcile and stop
any owned live writer/jobs before replacement; an ambiguous timeout requires
inspection, not immediate resubmission. Give the replacement the same assignment,
retained evidence and revision. Its complete verified report may close the original
obligation. Availability recovery does not authorize parent self-approval or waive
independent review.

Keep the existing review/repair attempt limits across model switches. A switch
does not reset the counter or bypass a permission or resource ownership boundary. Record its
reason and previous configuration; revisit a failed combination only after relevant
new evidence or a state change. Reuse unaffected review coverage and existing gates
instead of adding a separate model-quality review. Compare accepted assignments
and total recovery cost; a few outcomes do not establish a universal model ranking.
