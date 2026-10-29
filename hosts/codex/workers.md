# Codex dispatch runtime

Contents:
- [Assignment and capacity](#assignment-and-capacity)
- [Peer messages](#peer-messages)
- [Continuation](#continuation)
- [Top-level workers](#top-level-workers)
- [Resume timer](#resume-timer)

Resolve [executors](../../runtime/workers/launch.md) first. Default to native children, not sidebar
tasks. An explicitly selected external executor owns its launch/lifecycle and
settings; the native operations below apply only to `executor: native`. Complete
the shared dispatch preflight: active adapter, native tools, permissions and
verified repository/worktree assignments.

For runtimes exposing `collaboration`:

| Operation | Tool |
|---|---|
| Start | `spawn_agent`, self-contained prompt, `fork_turns="none"` |
| Inspect | `list_agents` |
| Send to an active agent | `send_message` |
| Assign more work to an idle child | `followup_task` |
| Interrupt the current turn | `interrupt_agent`; the child remains available |
| Wait for updates | `wait_agent`, then consume delivered messages |

Follow current schemas and returned IDs, including runtimes exposing `wait`,
`send_input`, `resume_agent` or `close_agent` instead. No child tools → dispatch
unavailable. No peer messaging → relay evidence through the parent.

## Assignment and capacity

When acceptance requires native closure or capacity release, preflight the actual
close operation before spawning. A runtime exposing only `interrupt_agent` cannot
satisfy that requirement. Use an explicitly selected, preflighted external route
with confirmed cleanup when authorized; otherwise keep the assignment queued.
Do not launch a child first and discover this limitation during collection.

Supply absolute `adapter`, `agent_file`, `job_file`, `plugin_root`, `repo`, the job's inputs, relevant
user decisions and exact failure evidence. Children read the adapter, agent file and job
file, then verify worktree and branch. Pass paths instead of copied conversations.
Record a child's startup with `worker_lifecycle.py record`, `kind: startup` and `native`:
its rollout under `$CODEX_HOME/sessions/` (default `~/.codex/sessions/`), named
`rollout-*-<id>.jsonl`; register the child's `agent_path` as `native_session`.
A child's task message is stored encrypted, so before each
`spawn_agent` record its assignment, from inside the checkout the child works in, with `python3
<plugin_root>/hooks/read_only_writes.py codex-assign --task <task_name> --agent-file <agent_file> --worktree
<worktree>`; only when it succeeds, spawn it with that `task_name`, new for each child. The record survives a
child's redirects, `tee` and `apply_patch`, not `rm`, `mv` or an interpreter, and codex-assign's refusal of a
child rests on `CODEX_THREAD_ID`, which a child can set. In a session with records, each path a child writes is
judged by the record in its checkout: a child of a `writes: none` agent, or one with no record there, writes
inside the checkouts only to its reports and its recorded worktree (no `.git` path), by absolute path; the hook
sees redirects, `tee` and `apply_patch`, not files an interpreter or tool opens itself. A builder still relies on
`--accept`'s check of its ticket branch.
After session loss, inspect saved IDs; reconstruct absent agents from surviving
files/worktrees and record replacement IDs.

Use only the route's resolved settings; ticket workload labels do not select models.
Map `model` to the native
spawn model and `effort` to `reasoning_effort` (or the current schema's equivalent).
`inherit` supplies no model/effort fields; do not reapply saved zloop preferences.
Omit absent fields; use `fork_turns="none"` with this host's explicit overrides.
Check supported model/effort combinations. Native custom-agent configuration can
supersede spawn settings: inspect it and report conflicts rather than assuming the
spawn request proves the effective model. Do not rewrite global Codex configuration.

Derive the child ceiling from the current runtime's capacity, reserving the parent
slot when its limit includes the parent. For the local Markdown board, pass that
ceiling as `--max-live` on every scheduler call. Apply it to native tracker dispatch
too; admit only free slots, counting reviewer and verifier jobs. Idle or interrupted
agents may still occupy slots. Interruption ends a turn; reconcile owned jobs under
[job lifetime](../../runtime/resources/lifetime.md). Release capacity only after
their teardown and a supported close operation with confirmed status. When closing
is unavailable, `followup_task` may continue only a collected, explicitly retained
same-assignment repair/recheck with matching agent, settings and permissions.
A new ticket, another agent, or a job whose `independent_of` lists one the worker
already did needs a fresh native conversation under
[worker attempts](../../runtime/workers/prepare.md). If the runtime cannot
provide that context or release capacity, queue work and report the limitation.
Workers complete their assigned scope directly; additional decomposition returns
to the orchestrator.

## Peer messages

Supply relevant peer IDs; refresh via `list_agents`. Children can `send_message`
directly to active siblings; use full canonical task paths across parents. Send a
contract, finding or blocker with file/commit evidence and the requested action.
Copy scope/integration decisions to the orchestrator.

Messages cannot transfer file ownership, approve changes or complete tickets.
Scheduling, assignments, board updates and integration remain the orchestrator's.
It uses `followup_task` to wake idle children; `send_message` does not. Continue
independent work or report the dependency to the orchestrator; avoid mutual waits.
Reviewers form initial findings from code/criteria before consulting authors.

## Continuation

Wait in the active turn while children work; report meaningful progress. A spawn
or empty candidate list is not completion. Persist before interruption and resume
from disk. Goals require explicit user requests and follow runtime status rules;
a blocked board alone cannot justify changing goal status. Scheduled work requires
an explicit user request; an automation does not replace waiting for live children.

Codex runs a plugin's hooks, the yield gate and launch log included, only after the user
trusts them, and records trust per hooks-file path and per hook: a moved hooks file or a new
hook runs only once the user trusts it again.

## Top-level workers

A Codex worker started as its own process, such as one launched through Herdr, takes the
route's `launch.argv` after `--`; every restriction is in it, since the user's default is
`danger-full-access` with approval `never`. Resolve with `--reports <abs reports dir>`, and for
`writes: worktree` also `--worktree <abs worktree>`; the resolver refuses without them. The launch
is a permissions profile (`-c default_permissions=...` and its `filesystem` table) that reads
everything and writes `$TMPDIR` and the reports directory; `worktree` also writes the worktree
and the shared git directory (`git rev-parse --git-common-dir` of `--repo`), where a linked
worktree's commits land, and the worktree's own git directory (`git rev-parse --git-dir` of
`--worktree`, `.git/worktrees/<name>` for a linked one), which Codex otherwise keeps read-only, so
`git commit` cannot take its `index.lock`; `--worktree` must already be a checkout. Then `-a never`, `-m`
and `-c model_reasoning_effort="<effort>"`, each value one argument. In logged-out `codex exec`
probes a profile given with `-c` applied over a config `sandbox_mode`, and the rollout recorded
exactly these entries; under `codex sandbox` with the profile, `git commit` succeeded in a linked
worktree. Start it in a
worktree of a checkout the user already trusts: anywhere else "Trust this folder?" blocks the
start (Herdr: `agent_not_ready`, agent `blocked`). Report that dialog to the user; never answer
it.
`codex exec` has no `-a` and always runs with approval `never`.

Startup follows [the startup order](../../runtime/workers/prepare.md). Herdr's `agent_session`
names the rollout id; pass the rollout as `native` to `submit` and `collect`.

The [executor guides](../executors/README.md) cover each executor type a route can select.

## Resume timer

Codex's scheduled tasks are recurring and belong to the app, not the CLI, and `codex queue` sends a message at once,
so an autopilot allowance pause can set no timer here. Put the resume command `$zloop:autopilot --take-over` and
the time after which it can run in the report, and install no operating-system timer unless the user asks.
