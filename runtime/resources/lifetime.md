# Resource and job lifetime

Each bounded assignment has one worker owner. The worker completes it directly
and returns its evidence to the orchestrator; it does not start a second planning,
implementation or review team. The orchestrator owns any rescoping, independent
assignments and replacement handoffs. Provide only the requirements, source anchors
and evidence needed for that assignment. Review is a separate scoped assignment.
Workers surface blocking failures or repeated unproductive approaches promptly
through the executor's existing progress/report channel, with attempted actions,
evidence and live-job state. The orchestrator applies the
escalation checkpoints when receiving that
evidence; it need not wait for a final completion report.
Workers surface an early design decision
when unsure how to proceed within the agreement or when findings might break it,
including public contracts, platform exceptions and governing criteria. Stop only
dependent edits and supply the requirement, evidence and decision-brief path.
Use the supplied attempt/state with `worker_lifecycle.py escalate` and its armed
parent channel before dependent work. A read-only or unsupported progress channel
returns the structured blocker immediately; the orchestrator records the hold.
Within-agreement uncertainty uses the phase's optional decision consultation or
host investigation. A possible agreement breach always reaches the human; neither
a worker nor a backend may authorize it. Resume only after a sourced ruling and
current canonical criteria. Carry unaffected accepted decisions forward.
A forwarding executor may transport the request to its one worker, without becoming
an additional reasoning or delegation layer.

Use check execution for bounded failure controls, complete attempt logs
and evidence that survives checkout retirement.

The orchestrator reserves verified exclusive resources before launching dependent
experiments: mutable build outputs, device, fixture, shared service or worktree.
Follow resource coordination: a local
mutex is optional, and a remote scheduler owns its execution capacity. Launch one
experiment at a time only when they need the same exclusive resource. Other
analysis can proceed. Record ownership and waiting work in the existing
assignment/report, not a second queue.
Include other active sessions using the same device or build lane, even when they
use a different workflow. When a lock is needed, verify its common path and carry
the reservation into the project's existing instructions for standalone checks;
a worktree isolates files, not devices. Unconfirmed availability stays blocked
for dependent checks.
Reserve a complete measurement unit with its owner, expected duration and release
event (for example, baseline plus candidate plus restoration). A grant with insufficient
known availability stays a resource blocker. If revoked, checkpoint where supported,
reconcile owned jobs and resume from retained evidence after a stable grant; record
any measurements invalidated by interruption. Do not start optional later phases
just to fill a wait when their value depends on an unfinished result or the user's
allowance priority calls for deferring them.
Carry the same shared reservation and any required lock identity through retries
and recovery.
A new private lock path cannot make a busy shared lane available; an independent
lane needs verified resource separation. Same-file concurrency requires explicit
non-overlapping ownership and a semantic integration check, not just separate worktrees.

Before queuing a measurement, record its question, required suites, optional
extensions and stopping condition in the existing assignment/report. Distinguish
setup, capture and whole-experiment budgets. Report the suites actually queued and
completed so the host's estimate includes resource waits, execution and analysis.
Notify the host at meaningful milestones or material plan changes through the
existing channel; unchanged polling adds no progress. Defer optional exploration
only under the accepted stopping condition; required work retains its completion intent.

Every worker owns its spawned test jobs as well as its agent handle. Record job
handles and the worktree/resource they use, including commands still queued on a
lock or remote queue. Use the runner's current supported wait/status/log/cancel
operations. A native wait expiring leaves the job live until its status proves
otherwise; reconnect to the same ID instead of resubmitting. If a monitor tool is
unavailable, use supported bounded waits and status reads. Follow the host's
communication requirements and report meaningful milestones. Distinguish queue
time from execution and report an estimate overrun with the next observable
milestone; unchanged polls need no
repeated narration. Collect completion and verify owned descendants have stopped
before restoring experimental files, reporting finished, releasing a reservation or retiring a
worktree/worker. A launcher timeout or cancellation request is not proof of teardown.
On interruption, reconcile the same jobs; stop only owned jobs through supported
lifecycle tools or verified owned process handles and verify exit. Never use a
shared-host command-pattern kill that can affect another session. A local client
exit does not prove a remote job or device lease ended. If teardown cannot be
proved, report incomplete cleanup and retain the reservation. Never delete a
required shared lock to unblock it.
For checks resumed after interruption, follow check recovery
to preserve completed evidence and restart only what the measurement requires.

For work owned by another session, hand over the required dependency or result,
confirm its owner and retain this session's next integration condition. End routine
status relays after that handoff; re-engage when that condition changes or the user
requests coordination. Ownership transfer does not authorize duplicate work or
publication on the other session's behalf.

Before accepting a worker result, the orchestrator reconciles its jobs and evidence.
Retain a reviewer needed for an upcoming repair check, recording that next use.
Once no assignment remains, preserve its report and retire the owned worker handle
through the executor's supported close operation. If capacity requires earlier
retirement, preserve evidence, clear the handle and resolve a replacement normally.
With no close operation, record the completed handle; release scheduler capacity
only after actual work has ended. Leave the parent session and unrelated user agents
alone. Herdr-specific operations live in Herdr guidance.
