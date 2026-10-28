---
name: setup
description: "Explain the current zloop setup, or inspect, configure or reset worker models, routes, pools, routers, profiles, thinking bounds and decision opt-ins and calibration at user, project or session scope."
disable-model-invocation: true
argument-hint: "[<question> | show|set|reset [agent|router|decisions|profile|model-definition|runtime-definition|executor-definition] | calibrate | calibration status | check | retro <journal> | replay <journal> <cases>] [--scope user|project|session] [--route] [--config-profile <name>] [host=claude|codex|pi|opencode] [--candidate <id>] [thinking=<label|JSON>]"
---

# setup

Contents:
- [Direct changes](#direct-changes)
- [Select the configuration flow](#select-the-configuration-flow)
- [Inspect effective settings](#inspect-effective-settings)

First read: the runtime bootstrap (`../../bootstrap.md`), the host session part.

The configuration procedure for the user-invoked setup command. The entry point first
reads runtime bootstrap and the active host adapter. No arguments means read-only
inspection, presented as the [setup tour](tour.md); so does a question or a request to
understand the setup. A direct request handles inspection, edits, resets and
validation through [select the configuration flow](#select-the-configuration-flow),
and a one-run choice is not a saved default.
Respect native invocation restrictions; a refused operation stays refused.
This file selects the flow and inspects settings. For edits read
[apply requested changes](apply.md); for resets and the final check read
[reset and validate](reset-and-validate.md). Decision calibration, retrospectives and
replays (`calibrate`, `calibration status`, `retro`, `replay`, `check`) follow the
[decision workflow](../../runtime/decisions/decision.md).
A direct, fully specified change needs no tour or repeated approval. Apply existing
authorization and report its effective result.

## Direct changes

Use `/zloop:setup` in Claude Code, `$zloop:setup` in Codex, or
`/skill:setup` in Pi. These are chat invocations, not shell commands. A request that
names `show`, `set` or `reset` goes straight to
[select the configuration flow](#select-the-configuration-flow) without the tour.
For example, using Claude's spelling:

```text
/zloop:setup set reviewer --route executor=subagent mode=inherit
/zloop:setup set reviewer --route thinking=high
/zloop:setup show reviewer --route
/zloop:setup reset reviewer --route thinking
```

The second command explicitly overrides thinking; the worker's model remains at
its native default. The last removes only that thinking override. Use an actual
supported `model=<id>` when you want to select a model too. Labels are validated
against the destination before launch, not assumed portable across runtimes.

You can describe the same changes in natural language after the command. Name the
agent, desired executor and any model/thinking choices. Available agents are
`builder`, `reviewer` and `verifier`. The skill
uses the current host and project by default and asks only for missing choices
that cannot be established from the request or current setup.

Add `--scope user` for personal defaults or `--scope session` for a temporary
override; otherwise changes are project-local.
Explicit configuration requests authorize preparation of the plugin's isolated
validator environment if needed. Showing settings never installs dependencies or
starts a worker. After a change, the skill reports the saved path, effective
executor/model policy and any runtime checks still pending. Use the next ordinary
`dispatch` or `review` invocation to execute work; saving config does not run it.

## Select the configuration flow

For new setup and natural-language configuration requests, use model definitions and agent entries
in `executors.json`; **--route** is the explicit command spelling for that flow.
Read [configuration examples](../../runtime/config/executors.md) and only the selected
executor's schema/adapter. Preserve structured legacy commands without
--route, --config-profile, --scope session or pool options, and explicit models.json
requests, using
[legacy preferences](../../runtime/workers/legacy-preferences.md).
The optional `host=` argument means the host for per-agent setup and the target
preference host for legacy settings. If omitted, use the current host; never
infer it from a model name. It does not change the main session or supply tools.
Use [host selection](../../runtime/workers/launch.md#host-selection) to distinguish the
caller from the worker runtime. A named-profile request uses
`profiles.<name>.<agent>.<worker-runtime>.candidate/thinking`; there is no `agents` or
`choices` wrapper. Require the target runtime for a profile edit, establishing it
from the requested candidate's binding when unambiguous. Preserve omitted fields.
Profiles never replace pools or routes. `--config-profile` explicitly selects a
profile router; host/Herdr names do not automatically select profiles.
For reusable models, read [model definitions](../../runtime/config/catalog.md).
Store descriptions under `models.<name>`; its `runtimes.<runtime>` bindings contain
the registered `id` and thinking policy. Top-level `runtimes.<runtime>.executors`
owns reusable launch routes indexed by caller context. For skill bridges, use
[caller-specific routes](../../runtime/config/catalog.md#caller-specific-skill-routes).
Establish both the caller and worker runtime before editing; update the requested
caller's route and preserve sibling routes when saving the complete runtime definition.
Use configured native routes
for same-runtime work, including Pi, and a supported bridge for cross-runtime work.
Herdr routes require a real managed environment. Provider connections stay in the runtime.
Keep agent pools shared. A routing profile may cover multiple worker runtimes.
Preserve selection policy when changing only transport.
For reusable skill usage, read [named skill executor types](../../runtime/config/executors.md#named-skill-executor-types).
An executor-definition request selects this per-agent flow. Store the complete skill
instance under `executor_types.<name>` and reference it with `{"type":"<name>"}`.
Keep destination routes and model settings separate; a definition name is not a host.
Resetting a named definition removes that definition only after its references have
been removed or reassigned as requested; preserve unrelated types and routes.

## Inspect effective settings

For a full inspection (**show** with `--route`, or a request to see every setting), show the host, router and selected profile/source, then each
agent's pool or fixed instance, settings and source.
Include shared decision backend, enabled families and offline calibration status;
`decisions.py status` reports saved warnings without inference or state writes.
Include a pool's candidates and descriptions, thinking defaults/allowed
sets or legacy adjustment permission, and active session ID/path/source when present.
Show runtime bindings, model IDs, definition sources, launch routes and excluded candidates with reasons;
include named executor types and their effective skill/config and definition source.
`--list-candidates` permits inspection even when no candidate has a matching route.
Report `selection_required` as an undecided assignment, not a selected model.
Show sparse profile overrides separately from the resolved pool.
Include native preferences only where
no agent instance is configured. A named bridge's destination may be unresolved;
show that honestly. Read-only inspection never creates files, installs dependencies,
loads executor skills or starts workers. Requested settings are not runtime proof.
