# Wait for a worker

Goal: every live attempt has one independent wait that can wake the host, and every
wake is handled before yielding. [Prepare the attempt](prepare.md) first.

## Arm a wait

Use **one independent wait per attempt**: run `watch --state ... --attempt ... --input
/absolute/wait.json` through the host's supported background operation with `notify`,
and yield once no independent work remains. Foreground waits are only for a worker about
to finish, at most 60 seconds; never poll workers in the foreground. The input holds a literal `argv` array for the installed bridge's wait or prompt operation and a `timeout` in
seconds. The helper runs in the assigned worktree and keeps combined output under
`workers.sqlite.logs/`.

Arm `notify` in the same request for a background wait. Its literal `argv` holds one
whole `{receipt}` argument, which the helper replaces with a JSON message carrying the
stable event ID, assignment and attempt, input head, state path and receipt; no shell
expansion happens. Use the installed native channel to the real available parent.
Herdr's `herdr --session <session> agent prompt <parent> {receipt}`, with no `--wait`,
reports prompt acceptance, not an acknowledged or completed parent turn. The channel must
return once the parent accepts the message: waiting for the parent's turn outlasts the
callback timeout and records a failed delivery. Before the first wait on a channel, and
again after its latest delivery failed, `watch` sends a no-op `channel-check` message
through it (the parent sees it as a prompt) and refuses to arm when that fails. Before
submission, verify the parent's identity and that its permissions allow receipt
inspection. Callback timeouts are at most 45 seconds. Without `notify`, only a foreground
wait of at most 60 seconds is accepted and the host awaits its native return; longer
waits without a channel fail before the worker command runs.

The terminal receipt is committed before delivery. Accepted delivery records
`delivery.state: sent`; failures and timeouts stay `failed`; an interruption before the
receipt is updated leaves `sending` with its owner's process identity. None of these
states acknowledges a receipt or accepts a worker's result. Callback output is kept
separately and cannot replace the worker log. On resume, inspect `status.pending`, the
same native parent and the callback log, and consume an already received event once by
its ID. Recover an undelivered event with `deliver --event-id ... --input
/absolute/retry.json`:

```json
{"retry": true, "evidence": ["same parent resumed; prior delivery and ownership reconciled"]}
```

The saved channel is reused; a `notify` object may replace an unavailable parent's
channel after its new identity is verified. A live delivery owner prevents retry, and an
uncertain prior send needs inspection, never a guessed failure. Sent or acknowledged
events are idempotent; recovery redelivers the same event ID and never reruns productive
work. Parent downtime stays pending; without a native channel, resume inspection is
required.

Use the bridge's activity-gated prompt-and-wait operation when available and record the
accepted prompt evidence from its native result; a standalone wait may return stale idle
at once. Never join independent waits in one shell command, suppress exit statuses or
append a success message. The helper keeps the command's exit status and uses 124 for its
own deadline and 130 for an interrupted monitor. It stops its own local wait process
group, not a remote worker or its jobs.

## Wake and yield

At every host continuation and after a wait wakes, run `status`: `--attempt` for a
focused read, otherwise reconcile the run's full set of handles. It lists unacknowledged
receipts, attempts needing attention and unresolved design holds, including holds whose
blocked worker is closed.

Before yielding, run `gate --state ... --stage yield` (no `--attempt`). It exits 1 and
names each live worker without an armed wait that can wake you, each lost wait and each
undelivered receipt; fix those first. On Claude Code and Codex (Stop hooks), Pi
(extension) and OpenCode (plugin, once the turn has ended) the plugin also runs it when
you stop, over runs active in the last day, and sends you back once. `ZLOOP_YIELD_HOOK=off` disables the hooks. The stop check holds:

- for the gate's findings, only the Herdr pane that registered the worker or, outside
  Herdr, sessions that are not inside a worker's own worktree;
- every session, however old the run, for each Herdr launch it logged
  ([launch log](prepare.md)) into `.worktrees/` or a cwd Herdr did not report, and,
  unless its launch marked it a worker (`ZLOOP_WORKER`, as [prepare](prepare.md) says) and
  it is inside Herdr, for each other live agent of its workspace whose cwd is in
  `.worktrees/` (none when the checkout has no `.worktrees/`). The hold lasts while no
  attempt names that pane or agent (alone or in a `<session>/<pane>/<name>` handle) and
  none of the session's dismissals accounts for it. For a launch, the naming attempt is
  never one another host session registered when it and the launch's coordinator both
  know one; otherwise it is one registered from the launch's pane, or after the launch
  when the launch was made outside Herdr. A live agent nobody launched here is cleared by
  any registration;
- the session dispatch bound to coordinate an effort (`next_tickets.py --coordinate`),
  for each collected attempt neither retained nor retired whose recorded tab Herdr still
  lists (with no binding, the Herdr pane that registered it is held), and for an effort
  whose review report records `autofix: true` (before it does, the binding's `--autofix`)
  and no `blocker`, with every ticket done and `--delivery` failing, until it is finished:
  its branch deleted, its reviewed head in main (local or the remote's) and a passing
  `e2e_status` recorded at a head in main; and for a confirmed autopilot run card of the
  session with no terminal state or no evidence. Neither hold applies while a live worker of
  the effort holds a running wait that will wake the host.

A launch, like the dispatch binding (`--coordinate`), belongs to the host session that
ran it when both sides know one, whatever pane either reports (a Codex session's
`HERDR_PANE_ID` can come from another pane through its shared app-server, and a restarted
Herdr reuses pane IDs); only without a host session does the pane decide. A closed
workspace or stopped Herdr session means the tab is gone. A check that cannot run (Herdr
or git fails or times out) and an unreadable or malformed launch log line are named gaps,
never a pass.

Read every completed wait's retained log and inspect the native worker and report. When
the opt-in [wake-triage pilot](../decisions/shadow-pilots.md#wake-triage-shadow-pilot) is
enabled, add `capture`, a literal argv that prints the worker's current output, to each
notified wait of an attempt registered with a `journal`; after delivering the receipt the
helper runs it in the worktree (15-second limit) and journals a hidden label, and
`status.pending[].payload.triage` holds its `decision_id` for the assessment. Handle the
wake:

- Settled idle with this assignment's complete report may be completion; idle without
  accepted prompt activity or a current report stays unverified. Any wait that ended,
  settled or not, leaves its monitor `finished`: record `reconciled` before `collect`
  ([input shape](collect.md#collect-a-result)).
- An error, timeout, provider failure or lost monitor needs inspection of the same worker
  and its owned jobs; record `reconciled` with that evidence after the old wait stopped,
  and arm a new independent wait for work still live.
- A restart consumes the previous wait: rearm after reconciliation. An active wait cannot
  be replaced or declared reconciled. A killed monitor keeps its local wait PID; reconcile
  its teardown before starting a replacement. Missing process evidence needs inspection,
  not a guessed stopped state.
