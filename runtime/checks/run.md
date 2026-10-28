# Run checks

Read with [plan checks](plan.md) when running a required check.

To keep a check's evidence, read [retain evidence](evidence.md).

Contents:
- [Run a bounded check](#run-a-bounded-check)
- [Prove the failure path](#prove-the-failure-path)

## Run a bounded check

Prefer an existing project runner that already preserves full output, propagates
failure and tears down owned jobs. Otherwise, on POSIX hosts:

```sh
python3 "<plugin_root>/scripts/run_check.py" \
  --timeout <seconds> --log "<retained-evidence>/attempt-01.log" -- \
  <project-check-command> <args>
```

Create the evidence directory first. Apply required environment and any project-required
resource coordination to the command. Choose the deadline from the check's
expected duration, including any authorized lock wait. Give the host tool enough
time for that deadline plus teardown; a tool that backgrounds on timeout does not
bound the check itself.
Bound setup/submission and synchronous driver operations too. Check required
script paths and syntax before execution, supply their inputs explicitly, and
use noninteractive commands. A timeout increase needs evidence of productive work
and a revised milestone; repeated invalid setup returns for host recovery.

The runner closes stdin, creates a private log exclusively, combines stdout/stderr there, and
prints a JSON summary with `exit_code`, `timed_out` and the absolute `log` path.
When the check has an authoritative execution marker, add repeatable
`--require-output <literal>` arguments before `--`; each must be nonblank single-line
text from the actual check. Missing output turns a zero exit into 1 and is listed
in `missing_output`. Existing command failures retain their exit code. These markers
are execution preconditions, not a substitute for the scenario's acceptance evidence.
`exit_code` is authoritative: nonzero, including 124 (deadline), 125 (setup or cleanup error)
and 128 plus a signal, means the gate did not pass; `command_exit_code`, `exit_source`,
`timed_out` and `interrupted_signal` say why.
`cleanup_required: true` records that the command left descendants;
an otherwise zero exit becomes 125 with `exit_source: cleanup`. An original nonzero
exit is preserved. Fix the command's joins/teardown before claiming the gate passed.
Commands needing stdin must read an explicit file or redirect input inside the
command; interactive operations belong outside this helper.
When the opt-in [failure-cause pilot](../decisions/shadow-pilots.md#failure-cause-shadow-pilot) is
enabled, run required gates with `--classify-failure <absolute-session-decisions.jsonl>
--assignment-id <assignment/attempt/check> --repo <canonical-project>` before `--`,
with a new ID for each retry. A failed check then journals a hidden label; the
summary gains `failure_cause` with its `status` and `decision_id`, never the label.
Use a new attempt path on retry; inspect the saved log
instead of rerunning a build merely to expose output hidden by a filter.
Keep commands and their completion waits owned through the actual result; a
background launch is not a completed check. In progress reports, distinguish
execution from build/resource queueing and user-paused time, and name the next
observable milestone. Base estimates on measured runs when available; label
uncertainty rather than attributing the entire elapsed time to model reasoning.
Use one authoritative completion wait per remote job, with native status/log reads
for progress or recovery. Reconnect or replace a failed waiter; do not nest a
polling job around its output or resubmit the check merely to resume waiting.
Prefer the runner's native deadline/cancel operations over another watchdog.
A tool wait expiring does not prove that the remote job timed
out or stopped; reconnect to the same job and inspect its authoritative state.
Code 124 alone cannot distinguish the runner's timeout from a child's timeout.
Do not resubmit until ownership and the previous job's state are reconciled.

The runner cannot tear down processes detached into another session, simulator or
remote service. Use the project's owned job IDs and teardown procedure for those.
Hard-killing the runner bypasses cleanup. A normal command exit also does not prove
background jobs stopped: reconcile owned jobs under
[resource and job lifetime](../resources/lifetime.md) before releasing
the reservation. Cleanup uncertainty leaves the gate incomplete.

## Prove the failure path

Before a long stress run or intentional deadlock, use the smallest negative control
to prove both the expected assertion and bounded cleanup. Include cancellation,
worker/thread joins and finally blocks in that bound. Preserve its failing evidence,
then restore the temporary mutation only after owned jobs have stopped. Verify the
restoration before running the passing case or releasing the checkout/device.
An outer timeout alone proves neither the intended assertion nor safe teardown.
