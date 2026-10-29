# Pi workers

## Delegation

Resolve [executors](../../runtime/workers/launch.md) first. In a Herdr session Pi workers run through Herdr, the
built-in provider. The rest of this section applies to `native`, outside Herdr: Pi has no delegation of its own, so it
needs an extension offering a `subagent` capability that meets the provider contract (pi-subagents is one).
Inspect its current schema and `subagent({ action: "list", capabilities: true })`
before assigning work. Use `action: "guide", topic: "tool-reference"` for unfamiliar
operations. A missing delegation capability blocks that native launch. Return the
gap to [pool recovery](../../runtime/workers/recovery.md); use only an already
configured suitable alternative. Do not install a provider to force a launch.

Use the registered generic `worker` with the assigned `agent_file` (builder, reviewer
or verifier) and `job_file`. Verify its actual tools
meet the assignment; project profiles can override defaults. Agent permissions
still apply even when its tool list permits writes. Do not request nonexistent
zloop-named Pi profiles or copy the Claude profiles into Pi.

Start a child with `agent: "worker", task: <assignment>, cwd: <assigned worktree>,
context: "fresh", async: true, worktree: false`. zloop already owns the
worktree; explicitly disable extra isolation even if Pi's configured default
enables it. Include absolute `adapter`, `agent_file`, `job_file`,
`plugin_root`, `repo`, report and input paths, branch/head, accepted scope and
decisions. The child reads these files and verifies its checkout before acting.
Register it with `native`: the child's `sessionFile` from `subagent` status. Record startup
with `worker_lifecycle.py record`, `kind: startup` and that `native` path.
Fresh context keeps prior hypotheses and unrelated parent history out of reviews.

Use only the route's resolved settings by the **shared agent**, not the generic
native `worker` name. Ticket workload labels do not select models. Validate an
explicit override against the native provider/model catalog (`pi --list-models <search>`;
a bare `pi models` starts a full session); report unsupported
combinations for pool recovery without approximating them. Map a model override to the dispatch `model` field;
map effort to its `provider/model:<effort>` suffix. The installed pi-subagents
dispatch does not accept a separate thinking parameter. For effort-only overrides,
resolve the model the worker would otherwise use from native settings/catalog
(including provider and agent defaults); do not assume it is the parent's model or
pass `inherit:<effort>`. If that model cannot be established, report the missing
capability. Validate suffix support and inspect returned effective metadata.
`inherit` passes no model/effort override; do not reapply zloop preferences.

Use the smaller of available runtime capacity and the shared scheduling ceiling
of 8; an unlimited runtime does not remove that ceiling. Count all live workers.
Pass this ceiling as `--max-live` to local-board scheduling calls, and apply the
same admission limit to remote trackers. Workers complete their assigned scope
directly; the orchestrator handles any additional worker assignments.

## Continuation and coordination

After an async launch, persist the returned run ID and wait through Pi's native
completion notifications: yield the turn when no independent work remains, then
resume the same dispatch on notification. Report it as running, not finished.
Use a bounded `action: "status", id: <run-id>` for stale/blocked work; do not poll
or sleep to keep the parent alive. Follow runtime wait instructions for work that
lacks native notifications. For workers registered in the worker lifecycle (such as
Herdr panes), Pi's background wait is `bg_run`: run `worker_lifecycle.py watch` through it with `notify`
(`submit` returns once the worker starts, so `bash` will do), and pass `gate --stage yield` before you yield. The extension logs
each `herdr agent start` run through `bash` or `bg_run`, and its settle check also names
Herdr workers you launched but did not register, or says why it could not check. A
pi-subagents child in the background runner (`PI_SUBAGENT_CHILD=1`) skips that check; a
foreground child carries no such marker, so do not list this extension in a child agent's
`extensions`.

Use `action: "steer"` for a running child. For completed work, inspect
`action: "children.list"` and use `action: "resume", id: <resumable-id>, message: ...`.
Resume may return a new run ID; record it and consume its completion. Queuing a
message alone does not restart a completed child. If resume is unavailable, verify
the old writer stopped, preserve its worktree/report and assign a replacement.

Use `contact_supervisor` when available for decisions or blockers. Relay concrete
peer evidence through the orchestrator unless peer messaging is actually exposed;
messaging never transfers file ownership or approves scope changes. Permission
failures are boundaries; availability failures use the shared recovery policy.
Reconcile partially started children before retrying or replacing them.

The orchestrator retains board updates, integration and authorized publication.
zloop adds no Git blocklist. Native permissions and agent permissions
remain binding. Scheduling a recurring run or enabling a persistent goal requires
an explicit user request; ordinary async dispatch uses completion notifications.

## Top-level workers

A Pi worker started as its own process, such as one launched through Herdr, takes the
route's `launch.argv` after `--`; every restriction is in it, since the user's permission
extension runs in `yoloMode`. It sets `--tools` as an allowlist, `read,grep,find,ls,bash` for
`writes: none` and the seven built-ins for `worktree`, `--no-mcp` (the allowlist keeps MCP
tools; the flag is listed by `pi --help`, its effect unobserved here), then `--provider`,
`--model` and `--thinking`. Never narrow with `--exclude-tools`: extension shells such as `bg_run` stay.
A `writes: none` worker keeps `bash` for checks and its report but has no write or edit tool;
the read-only worktree check at collection covers what its shell writes.
Startup follows [the startup order](../../runtime/workers/prepare.md). Pi records live under
`~/.pi/agent/sessions/`; Herdr's `agent_session` is the session JSONL path.
Pass it as `native` to `submit` and `collect`.

The [executor guides](../executors/README.md) cover each executor type a route can select.
