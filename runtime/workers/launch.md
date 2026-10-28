# Launch a worker

Read before every worker launch. Goal: each worker starts on a verified executor with
resolved settings and a complete assignment.

Contents:
- [Host selection](#host-selection)
- [Resolution and models](#resolution-and-models)
- [Assignment and outcome](#assignment-and-outcome)
- [Fan-out](#fan-out)

Core owns assignments, scope, capacity, worktrees and acceptance gates. A worker provider owns invocation: it
starts the worker of the target runtime in the assigned worktree with the resolved launch controls, leaves a
native record the worker lifecycle reads, ends in the lifecycle's wait and closes when retired; the lifecycle
checks each step. Herdr is the built-in provider (`herdr`; its procedure is the Herdr guide the bootstrap names):
in a Herdr session every runtime is reachable through it with no configuration. The adapters `subagent`, `skill`
and `bash` cover the others (JSON Schemas in `schemas/executors/`; instances in runtime launch routes or directly
in `agents`).
User-defined `executor_types` wrap skill usage and expand to the skill adapter.
Definitions are configuration data: resolution never loads user-supplied code or schemas.

## Host selection

Claude and Codex startup hooks and Pi's bootstrap extension name the current host.
Without that hint, select the host explicitly under the runtime bootstrap; OpenCode
always needs it.

Top-level `agents` defines shared pools for `builder`, `reviewer` and `verifier`, or
fixed instances. `router` selects host judgment (default), a routing skill, a System One
decision model or a sparse named profile
([routers and sparse profiles](../config/routers.md#routers-and-sparse-profiles)). A
profile key such as `builder.codex` addresses the worker runtime, even on a Claude host.
Shared [decisions and calibration](../config/decision-backend.md) configure a backend
and explicit phase opt-ins; a decision-model router may use it, skill and profile
routers keep their own policy.

The routing profile is `--config-profile <name>`, else `ZLOOP_PROFILE`, else the
configured router; no router means host judgment. A profile name neither establishes
Herdr nor selects the host. Caller-specific native routes win over a Herdr bridge when available,
except for a Pi caller, which tries Herdr first; Herdr routes need `HERDR_ENV=1` and runtime preflight. One profile can hold preferences
for several worker runtimes without duplicating models, pools or routes. The separate
`--profile` flag renders a native worker settings file, only after selection.

## Resolution and models

Before every worker launch, the orchestrator runs:

```sh
"<python>" "<plugin_root>/scripts/agent_models.py" --repo "<canonical-main-checkout>" --host <host> --job <job> --route
```

The job selects its agent; a route without `--job` is refused, and `--agent <agent>` is
only for inspecting or editing settings (`--list-candidates`). The assignment text
carries `adapter`, `agent_file`, `job_file`, `plugin_root`, `repo` and every input in the
job's frontmatter, each as `name: value`; registration refuses a `packet` missing one,
and on Claude the launch hook checks the prompt the same way. Use the plugin's
`.venv/bin/python` when present, else a Python with the required dependency if
configuration is present.

A `selection_required` result has no launch settings: finish
[selection](../routing/choose.md) first. Then read the returned `executor_guide` and the
active host adapter. Resolution never launches a worker. Inspection is offline and
read-only; only an explicit `--assignment` (compact routing text) may call the decision
backend and update calibration monitoring, and only explicit session save or reset
changes the session configuration.

Agent precedence: **one-run choice → session → project → user → existing native**.
Agent entries replace in full. A top-level `jobs.<job>` entry has the same shape and,
within one layer, replaces its agent's entry for that job only; a higher layer's agent
entry still beats a lower layer's job entry. Model, runtime, routing profile and
executor-type definitions merge by name user → project → session, each replacing the
lower definition whole; `router` and `decisions` each replace the lower object whole.
References expand after the merge. Profiles override only the candidate and thinking
fields they name, after worker runtime selection. The result records sources, target
runtime and caller context. A shared agent set to `null` masks lower entries and
restores native behavior, saved native model preferences included; deleting it reveals
the lower layer. A native executor with inherit bypasses legacy preferences. The session
layer needs a root session ID ([session scope](../config/session.md)).
`--route-choice '<JSON agent instance>'` picks a complete one-run instance without
saving it. Relative argv paths refer to the assigned worktree, not the config file.

With `candidate_pool`, follow [selection](../routing/choose.md) before launch: the router
recommends model and thinking together and the resolver enforces bounds. Adapters map
the returned settings to native controls and never choose models or add agent defaults;
with no override the destination's native settings stay. Ticket `model: standard|deep`
and legacy `sonnet|opus` describe workload only and never select or promote a model.

Omitted `models` on a configured agent means `{"mode":"inherit"}`:

- **inherit** keeps the destination's model and reasoning: no overrides sent, no zloop
  model preferences read, no promotion by workload. A bridge that discards destination
  settings cannot claim it. Override fields and task flags are rejected.
- **override** uses only `models.model`/`models.thinking`, then explicit task
  `--model`/`--thinking`; omitted fields keep destination defaults and task value
  `native` drops the override for this launch. New instances never merge the older
  models.json files.

## Assignment and outcome

For every delegated assignment, and after an interruption or compaction, use worker
attempts to [register native session identity and verify settings](prepare.md),
[wait](wait.md), [collect](collect.md) and [account for retirement](../resources/retire.md);
the same protocol owns [early design holds](design-hold.md) and their ruling and criteria gate.

Before fan-out, inspect each assignment's configuration, preflight its executor and profile discovery and supported
settings, and select among those combinations. Successful capability evidence is reusable only for the same host session, executor, settings and environment; a change or failed launch
voids it. With no read-only probe, the first real assignment proves availability. A
missing profile or quota failure excludes every candidate sharing that capability or
account; [automatic recovery](recovery.md) picks another in the allowed pool without
another approval. Retry a failed route only after a relevant state change; record the
failure once and apply it to every candidate on that route until registration or access
changes. Fallback never bypasses a permission denial. When availability differs by
effort or profile, pass the exact supported pairs with
[`--eligible-thinking`](../routing/choose.md); a rendered profile is not discovery
evidence. Verify each new attempt's settings against the resolved snapshot and
[record the decision](../routing/report.md). A failed resolution never permits launching
with settings omitted.

The assignment holds: unique assignment ID, canonical repo, worktree, branch and head,
pinned review base, absolute agent and job paths, scope and priorities, job inputs,
[situational skills](skills.md), permissions, model policy and settings, expected
evidence, known source and test entry points, and relevant prior evidence with its revision and applicability limits. It also carries the user's current allowance priority, required deliverable and deferred
optional work, in the compact routing input too; acceptance gates stay part of the
deliverable, and a quota concern does not imply a global model change: choose this assignment by
the accepted priority.
Start a fresh worker context and send the registered `packet` text whole as the opening
message. Workers read the named adapter, agent and job files first, verify the supplied
anchors against their checkout, then fill gaps with bounded reads, widening only when
evidence requires it.

Adapters need direct access to the supplied paths (no remote filesystem translation).
Verify before starting that the executor can fulfil the assignment; configuration does
not prove capability: missing skills, tools, permissions or settings
support block the launch, and recovery may pick another permitted candidate. Use the
executor's own lifecycle; record its run handle and requested and confirmed settings;
count it in core capacity and locks. Collect a complete report attributable to this
assignment and revision; the orchestrator persists read-only workers' reports. A finished
process, settled pane or empty output is not acceptance; apply the shared review and verification gates after collection. On timeout or session loss, reconcile the same run before any
retry and never duplicate uncertain live work. Leave pending approvals to the user and
clean up only this assignment's resources.

## Fan-out

Size a fan-out by how many parts can proceed without each other's results, not by idle
capacity: a question one search or read answers gets no worker, one coherent change gets
one worker. Launch independent siblings together within
[shared capacity](../resources/capacity.md); each assignment states its objective, its
boundary and what the siblings cover, so nothing is repeated or unowned. A worker that
writes its own report returns status, decisive evidence and the report path; a read-only
worker returns its complete report sections. Overlapping or shallow results mean sharper
assignments, not more workers.
