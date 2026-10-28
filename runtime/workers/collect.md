# Collect and accept a result

## Collect a result

Collect the actual assignment with `result`: `status` (`completed`, `blocked`,
`failed` or `cancelled`), checked `head`, an existing absolute retained `report`,
and verification `evidence`. A wait receipt is not an assignment result. The helper retains
the report digest and requires the checked revision at `gate --stage accept
--revision ...`; a missing or changed report fails that gate. Keep collected reports
immutable; snapshot a canonical report before its next authorized append. Later
attempts retain their own report snapshots. Other evidence links and live runtime
gates remain under the shared acceptance contract.
A `completed` result also owes its job's `returns` report sections (real headings,
outside code blocks) and, for a `writes: none` agent, the same HEAD, tracked changes
and untracked files (their content too) it was registered with, in the worktree and,
by the same rules, in each submodule and each nested repository (an untracked directory
that is a Git checkout of its own); a gitignored file is not a change. An unreadable
file is checked only for its presence. A directory Git cannot open (a checkout's top too,
named `./` in it), and a submodule with no checkout, is held to its own status
(permissions, entries added or removed directly in it), and `collect` names it in
`partly_checked` even when nothing changed. A nested checkout reached through a symlink
is held to its directory instead.
`collect` refuses a worktree that differs and names the change, and the submodule or
nested repository it is in. Restore a moved HEAD, a tracked change or a new untracked
file, then collect again. An edited or deleted untracked file, a changed status, or a
worktree whose state cannot be read, cannot be restored: record
the result with `record` (kind `result`), which keeps the gap on it, then collect
again; acceptance stays refused. Any other gap, and a difference recorded through
`result`, goes on the result; then `gate --stage accept` fails and recording
acceptance is refused.

After a wait settles (state `finished`, as after any `submit` or `watch` that ran to its end),
`collect` and the `result` record are refused with "Collect or reconcile the wait before the
assignment result" until a `record` of kind `reconciled` is written, once the wait's process is gone:

```json
{"kind": "reconciled", "attempt": "ticket-12/implementation-1", "evidence": ["wait log read; worker inspected"]}
```

Pass it with `record --state ... --input /absolute/reconciled.json`.

For a checked result, use `collect --state ... --attempt ... --input
/absolute/result.json` after native inspection and wait reconciliation. The input
contains `status`, checked `head`, existing immutable `report`, verification
`evidence`, and a required `disposition`:

```json
{
  "status": "completed", "head": "checked-revision",
  "report": "/absolute/retained-report.md", "evidence": ["native-result-and-checks"],
  "disposition": {
    "kind": "retire", "session": "explicit-owned-herdr-session", "pane": "returned-pane-id",
    "jobs_evidence": ["owned-jobs-and-waits-reconciled"],
    "acceptance": {"decision": "deferred", "reason": "acceptance checks not run yet"}
  }
}
```

For a completed attempt of a job with `acceptance: true` (`ticket`), `acceptance` goes
inside `disposition` (see acceptance below); other jobs omit it.

Collection persists the report and its digest, acknowledges only that attempt's
wait/result receipts, then performs the chosen disposition. A completed collection
straight after a settled, triaged wake records that wake's `done` label. To retain context,
use `disposition` with `kind: retain`, `reason`, `next_use` and `evidence`.
Retention may preserve a pending design-hold receipt; it does not acknowledge the
escalation, permit reuse, clear the hold or authorize dependent work. Mismatch and
hold receipts need explicit acknowledgement after the coordinator consumes them.
If retirement fails, the intact result stays collected. Resume with the same
status/head/report and valid disposition; do not overwrite the report or resubmit
productive work. Receipt acknowledgement survives restarts without deleting logs
or history. Other native transports use the recorded result/disposition protocol
and their supported native closure interface; record unsupported closure explicitly.

Acknowledge other consumed receipts with `ack --event-id ...`. Acknowledging
an error does not fix its settings mismatch or resolve its design hold. Continue the
board transition and review handoff once their prerequisites pass; monitoring alone
does not dispatch the next phase.

Record the skills the worker reports loading on demand, and listed skills it
did not use, in the [launch decision record](../routing/report.md); see
[record and learn](skills.md#record-and-learn).

## Accept and record outcomes

When the orchestrator accepts a completed result and integrates it, record
`accepted` on that attempt with the integrated `commit` and evidence. It is allowed
after the worker's session closed, once per attempt. Acceptance is the
orchestrator's judgement, not the worker's `completed` claim; the
[workstream spend report](workstream-spend.md) counts accepted tickets from it.

Closing or retiring a completed `acceptance: true` attempt that is neither accepted nor
declined settles it with an `acceptance` decision, whose shapes are in
[retire](../resources/retire.md). A deferred attempt asks for `record acceptance or decline`
in `status` until `accepted` or `declined` (with `reason` and evidence) is recorded; both
are allowed after the session closed, and each excludes the other.

Each collected attempt's outcome (result, acceptance or decline, reverts) also goes
to a machine-wide ledger, `$XDG_STATE_HOME/zloop/outcomes.sqlite`, with its
candidate, agent and job, whatever routed it. `scripts/outcomes.py summary [--agent <agent>]
[--job <job>]` counts successes, failures and pending attempts per agent, job and
candidate: an `acceptance: true` job succeeds when accepted, other jobs when completed; a decline, revert or failed
result fails, and a blocked result, which waits on something outside the worker,
counts neither way. `outcomes.py import <decisions.jsonl | workers.sqlite>...` adds
past decision journals and lifecycle state files once. A lifecycle attempt replaces
the journal copy of its routing decision, whichever is imported first, and a
journal's `succeeded` counts as accepted only when the host recorded it. A journal
cannot tell a blocked result from a failed one, so prefer the state file;
re-importing a state file refreshes its rows. Sources it cannot read are listed under `errors` with exit 1;
the others are still imported. A failed ledger write is reported as `ledger` and never blocks the record.

Later evidence about collected work goes back to its attempt:
`attribute --state ... --attempt <collected attempt> --kind review-defect|revert|owner-defect
[--count <n>] --evidence <reference>` for a confirmed review finding, a reverted
integration or a defect the user found. Registering a repair with `repairs`
attributes its `repair-round`. Each signal updates the routed outcome, so later
routing sees it.
