# External continuation supervisor

Run the optional supervisor outside the agent host when a confirmed allowance
pause must survive that host's exit. It persists due work; it installs no operating
system service. A process running inside the host's lifetime does not prove this
property. Machine reboot or supervisor loss needs an existing external service or
a restart of the same saved queue.

First adopt the confirmed card under [managed controls](control.md), retaining its
existing resume grant. No legacy, explicit-user-pause or waiting-on-user card arms
an automatic wake.

The target supplies three bounded driver commands in a JSON configuration:

```json
{"preflight": ["/absolute/check-parent", "{receipt}"],
 "notify": ["/absolute/wake-parent", "{receipt}"],
 "progress": ["/absolute/read-progress", "{receipt}"], "timeout": 15}
```

Each literal argv has exactly one whole `{receipt}` argument, replaced with JSON:
event ID, queue path, card, run ID, expected control revision and due epoch.
No shell expansion happens. Successful drivers print one JSON object; `notify`
may instead print nothing. Put diagnostics on stderr. Supply commands for the real, identified host:

- `preflight` reads the current parent, required-job routing/allowance and pending
  approvals. It returns `{"ready":true,"allowance":"eligible","approval":"clear"}`
  only when this continuation is permitted. Unknown access, a pending prompt or no
  eligible job route cannot return ready. It performs no productive work.
- `notify` queues that exact receipt through the host's supported interface and
  returns after acceptance, not after the host finishes. The parent rechecks the
  control revision and resumes with `run_control.py resume <card> --wake <queue>
  --event-id <id> --expect <revision> --request-id <stable-resume-id> --reason <reason>`.
  It then uses the ordinary work/acceptance gates. A changed pause cancels the wake;
  receipt arrival cannot override it or grant merging.
- `progress` independently reads actual post-state. It returns the same `event_id`
  and `progressed:true` only when this wake led to meaningful authorized work.
  A saved command, notification acknowledgement or heartbeat is insufficient.

Driver stderr and JSON observations are retained under `<queue>.logs/`. Keep
credentials out of argv, outputs and evidence. All commands have a deadline of at
most 45 seconds and owned local descendants are stopped on exit.

```sh
python3 <plugin_root>/scripts/supervisor.py schedule <queue.json> --card <card> \
  --due <UTC-epoch-seconds> --config <drivers.json> --event-id <unique-safe-id>
python3 <plugin_root>/scripts/supervisor.py run <queue.json> --interval 5
```

Start `run` through an already authorized external terminal/service; do not claim
it survives host loss without exercising that arrangement. `tick` processes one
due wake for testing or an existing scheduler. One unfinished wake owns a queue;
identical scheduling requests converge.

The sender records intent before delivery. Sent events only poll their progress
driver, never resend. A lost or timed-out sender becomes `uncertain`; the loop
stops rather than guess. Inspect the same target, then use `reconcile <queue>
--event-id <id>`: it requires readback of actual authorized progress for that event
and does not replay the command. If that evidence is unavailable, keep the gap.
