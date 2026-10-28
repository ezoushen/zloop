# Report routing decisions

Before the initial dispatch and each evidence-driven retain, escalation or fallback,
the orchestrator gives a concise user-visible update and records the same decision
in the existing assignment/review report. Batch independent announcements when useful.
Ordinary resumes refer to the saved decision; repeat neither the announcement nor
the router call when no decision changed.

Include assignment/attempt, previous → requested model and thinking, target runtime
and executor, **who proposed**, **who accepted or overrode**, **how it was selected**,
and the concrete evidence/reason. Use actual identities where available; label an
unavailable host model ID as unknown. Distinguish requested from runtime-confirmed
settings, and preserve the policy source/session and resolved JSON. Record the
situational skills with their sources.

For a recorded backend decision, record the `action`, `execution` and `outcome`
as [annotate.md](../decisions/annotate.md) describes. An override explained only in
prose is missing the actual host action. Reconcile a capture gap from retained
evidence, preserving unknowns. Retain verified alias evidence and report
errors/host recovery separately from eventual delivery success under the journal contract.
A launch failure or settings mismatch belongs to host/execution evidence, not a
label that the decision model chose badly. Report a new calibration warning with
its next step; preserve it without rerunning calibration automatically.

Attribution follows the actual path: an explicit user pin, named profile, named
routing skill, host judgment or the decision service's returned model/protocol.
For OpenJev, preserve its option/probability record and separately state the
orchestrator's reason for invoking or accepting it. The score is not a generated
rationale or proof of correctness. If the service fails and the host chooses,
attribute the final choice to the host and name the failed service separately.

Example: "Ticket 12, repair attempt 2: Luna/xhigh → Sol/high via native Codex.
Proposed by OpenJev through System One; accepted by the Codex orchestrator. Trigger:
R1-SPEC-02 still reproduces after the focused correction. Settings requested;
runtime confirmation pending. Existing retry budget unchanged."

Keep acceptance results, classified failures, elapsed time and available usage
evidence with those decisions; mark missing metrics unavailable. Histories belong
in the existing run reports/journals, not maintained skills or configuration.
For Claude, [recorded usage accounting](../resources/claude-usage.md) includes the native
family and separately associated worktree sessions. Keep requested settings beside
runtime-confirmed settings; tokens do not establish subscription credits. The
workstream spend report (`workstream_spend.py`) gives tokens per accepted ticket
across Claude, Codex and Pi workers and the host. Evaluate
a cheaper routing choice on a bounded assignment through accepted review/repair
closure before claiming a completion-cost improvement from synthetic scores.
