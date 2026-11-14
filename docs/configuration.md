# Configuring workers

No configuration is required for the existing native workflow. To choose how a
worker runs, its model, or its thinking level, use the **setup** command in chat
(`/zloop:setup` in Claude, `$zloop:setup` in Codex, `/skill:setup` in Pi).
With no arguments or a question it takes the [tour](../skills/setup/tour.md):
available workers, launch routes, selection policy, decision opt-ins and which scope
supplies each effective value. A tour is read-only and never starts a router or
worker. Requested changes continue in the same conversation, at user, project or
session scope, with a focused diff and effective result. When you already know the
setting, name it directly (`show`, `set`, `reset`); the same command skips the tour.

```text
/zloop:setup
/zloop:setup Why does this project use a different router from my user default?
/zloop:setup Disable recovery decisions for this session only.
/zloop:setup set reviewer --route executor=subagent mode=inherit
/zloop:setup show reviewer --route
/zloop:setup Configure this project's reviewer to use the subagent <plugin>:<agent> and inherit its destination settings.
/zloop:setup Reset only the reviewer's thinking override; keep its executor and model.
```

`--route` selects the per-agent configuration. The first `set` above selects the
native reviewer and preserves its native model/thinking settings, bypassing older
zloop model overrides for that agent; other agents stay unchanged. The command
prepares the isolated validator environment when needed, validates the configuration
and reports where it was saved. The named delegate requires the official Codex plugin
in Claude Code. Names and model controls must exist in the host where execution takes
place; validation does not prove that a live delegate can run.

## Scope, models and pools

Project scope is the default. Add `--scope user` for personal defaults shared across
projects, or `--scope session` for temporary overrides that survive resume. To select
a model, supply its actual supported ID and optionally `thinking=high`; explicit
choices enable override mode. Changing models never changes the main session. Reset
with `--route` and no field selection restores inheritance while keeping the selected
executor. For bounded thinking, use
`thinking={"default":"medium","allowed":["medium","high"]}`.

Define each model once under `models` with a task-fit description and runtime
bindings. Each binding holds its registered `id` and thinking policy; top-level
`runtimes` holds shared launch routes. Agents reference model names individually or
in pools; complete executor instances remain supported. A Pi model can use a native
Pi subagent or a Herdr-launched Pi worker from Codex, using the same binding. When
credits or a provider are unavailable, a suitable remaining pool candidate is chosen
automatically, preserving user pins, scope and permissions; exhausted pools report
the blocker. Pools can opt out with `fallback:false`. All hosts apply the shared
resolver's model/thinking settings and keep native defaults for omitted fields;
workload labels do not select models.

Configuration lives in `.zloop/executors.json` or
`~/.config/zloop/executors.json`; its schemas ship with this plugin. See
[host selection](../runtime/workers/launch.md#host-selection) for precedence, the
[configuration guide](../runtime/config/executors.md) for executor examples and
manual setup, and [reset and troubleshooting](../runtime/config/troubleshooting.md).

## Executors

- **Sub-agent:** the host's native worker or an installed named delegate (the default;
  Pi uses pi-subagents).
- **Skill:** an installed skill plus a follow-up instruction, such as Herdr.
- **Bash:** a program implementing the documented assignment/result contract.

Encapsulate reusable skill usage under `executor_types.<name>`, then select
`{"type":"<name>"}` in a route or agent; see
[named skill executor types](../runtime/config/executors.md#named-skill-executor-types).
Inside Herdr, [caller-specific routes](../runtime/config/catalog.md#caller-specific-skill-routes)
let Claude/Codex use native workers for same-runtime work and Herdr for other
destinations; the [Herdr guidance](../hosts/herdr.md) covers waits and cleanup.
The resolver refuses native OpenCode dispatch; OpenCode workers use the
[Herdr top-level route](../hosts/opencode/workers.md#top-level-workers), which needs runtime verification.

## Routers and decisions

An agent can define a pool of allowed candidates. The `router` chooses through host
judgment (the default), a skill, a built-in decision-model client, or a named profile. Profiles use
`profiles.<name>.<agent>.<worker-runtime>.candidate/thinking`; omitted fields leave
selection unchanged ([routers and sparse profiles](../runtime/config/routers.md#routers-and-sparse-profiles)).
Select one with `router: {"type":"profile","profile":"balanced"}`,
`--config-profile <name>` or `ZLOOP_PROFILE`. With no router configured, the
host chooses by [model routing](../runtime/routing/model-routing.md) in its
existing context (`router: {"type":"host"}`), without a separate model call.

For a local Jev/OpenJev service, configure its endpoint and model ID under
`decisions.backend`, enable `worker-selection`, and select
`router: {"type":"decision-model"}`. The
[decision-model router](../runtime/routing/decision-model.md) selects a permitted
model/effort pair and falls back to host judgment if unavailable or undecided. The
[shared decision loop](../runtime/decisions/phases.md) uses the same backend for
recovery and other explicitly enabled phase checkpoints; enable only the families
you want. The [decision journal](../runtime/decisions/journal.md) retains prompts,
responses, actions, worker settings and outcomes. `/zloop:setup calibrate`
runs a labeled calibration, `retro <journal>` an offline retrospective and
`replay <journal> <cases>` a bounded wording/order experiment; replay does not train
a model. Synthetic examples are integration checks, not evidence of production
accuracy. Checkpoints accept [bounded image evidence](../runtime/decisions/images.md)
for visual choices.

## Older preferences

Existing `models.json` preferences still apply to unconfigured native workers. Older
structured `show`/`set`/`reset` commands without `--route`, `--config-profile`,
session scope or pool options keep that behavior; see
[legacy model preferences](../runtime/workers/legacy-preferences.md). Old
executor-package/`routes` files need the explicit
[migration](../runtime/config/troubleshooting.md#migration).
