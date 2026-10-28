# Apply requested changes

For **set --route** or a natural-language setup request, identify the shared agent
or named model/runtime/executor definition being changed. For a model with multiple bindings,
establish the intended runtime from the request before changing its ID or thinking;
accept `runtime=<name>` to identify that binding and `id=<model-id>` for its registered
ID (`model=<id>` remains an input alias). Runtime launch routes are shared defaults;
routing profiles change only candidate/thinking fields, not runtime definitions.
Use project scope unless user or session scope is requested. With only model/thinking
choices and no existing instance, use `executor: {"type":"subagent"}`; omit empty config.
`executor=<type>` selects a built-in instance or a defined skill type. Built-ins
obtain their config from the request (agent name, skill/instruction, or argv);
named type references contain only `type`. Accept natural-language setup requests
and structured JSON. Derive the skill
instruction from the user's stated delegation goal or supported mode; keep it brief.
Tool syntax, settings mapping and lifecycle belong to the selected skill; shared
scope, review and recovery rules come from zloop. Ask only if the target or goal is missing.
Do not invent command paths, agent names or models. Optional model policy defaults
to inherit. Explicit model/thinking choices select override;
changing only the executor must not silently carry model overrides to a different
runtime. Show any such conflict for the user to resolve. Merely choosing a skill
is not permission to install it or run a validation job.

For catalog entries, pools, bounded thinking or **--scope session**, read
[models and runtime routes](../../runtime/config/catalog.md) and [session scope](../../runtime/config/session.md). These options select
the per-agent flow. Accept a pool as structured JSON or construct it from the
user's named candidates as catalog references or complete executor/model policies.
Catalog membership alone does not grant every agent access. For agent grading,
research the exact model/runtime, choose candidates by the agent's obligations, and
put conditional task limits in model descriptions. Keep dated comparisons in research
journals; use [assignment selection](../../runtime/routing/choose.md)
to filter each task. Pools have no model default; `--candidate <id>` identifies
the candidate to edit. Migrate old defaults explicitly using that guide.
For a per-candidate change, establish the target from the request before editing;
preserve the other candidates. Never silently collapse a pool to one
instance. Shared model/runtime/executor definition edits affect every referencing agent. For
agent-only model settings, copy the model definition under a distinct descriptive ID
and update that agent's reference. For an agent-only launch override, use a complete
executor instance; runtime names are fixed. Preserve other uses. Unknown references fail after layers are merged;
validate all affected references, including when removing a definition. Pools default
to automatic availability fallback; set `fallback:false` only when requested.
`thinking=<JSON>` accepts `{ "default": "medium", "allowed": ["medium", "high"] }`;
use this bounded form for new requests that permit automatic adjustment. Preserve
existing string/boolean settings unless their policy is being changed. A bounded
form replaces the legacy boolean; a single allowed value locks thinking.

Session edits use the stable root session ID from the assignment/environment or
existing workstream record. If unavailable, generate and retain one before the first
override. Use the resolver's `--session` for all affected inspections and descendants.
Copy the effective agent entry before a partial override, preserve unrelated session
entries, validate, and save the complete configuration through `--save-session`.
The saved file contains its repository/session identity plus `config`; the helper
input is only that `config` object. The coordinator owns writes. Report the ID and
absolute path so resume can recover them. Never write session preferences to user
or project defaults, or treat another host's parent profile as the child's host.
Construct edits from layers up to the requested scope: user only for a user edit,
user plus project for a project edit, and those layers plus the matching session
for a session edit. Copy the affected effective object at that scope before a
partial override; never copy a higher layer's values into a lower file. Inspect
durable settings without `--session` and with `ZLOOP_SESSION` unset; for user
edits, read the user file directly rather than treating project resolution as user
defaults. Retain an active session's identity and show whether it still masks the
saved change. Explicit profile selection changes the view, not the saved router.

For a **router** request, store `{"type":"skill","skill":"<name>"}` with
optional user-specific `instruction`, `{"type":"profile","profile":"<name>"}`,
or `{"type":"decision-model"}` with the backend configured under
[`decisions`](../../runtime/config/decision-backend.md). For a decisions request, preserve
the effective object, then edit backend, enabled families or calibration as requested.
Enable only explicitly requested families. Record the registered endpoint/model and
known revision; absolute suite/baseline paths belong to the chosen config scope.
Calibration commands belong to the [decision workflow](../../runtime/decisions/decision.md);
configuration edits and inspection remain offline. An old inline decision router
still applies to worker selection when shared decisions are absent.
Removing `decisions` reveals the lower object or legacy inline worker router; it
does not necessarily disable the service. To disable backend decisions at this
scope, preserve the required backend and set `enabled: []`. Disable only a requested
family by removing it from the effective enabled list. Resetting `router` reveals
the lower router, or host judgment when none remains. To select host judgment
over a lower router, save `{"type": "host"}`. Keep unrelated decision
families and calibration settings when changing only worker selection.
When no layer defines a router, choose by [model routing](../../runtime/routing/model-routing.md) in the host's current context.
External routing skills return selections; they do not execute assignments. Keep
other API protocols and credentials in that skill's integration. Decision-model
settings belong in `decisions.backend`, not the worker catalog. Preserve the existing router
when editing pools or routes. This command does not invoke a router or probe its
service; a requested integration test is separate from configuration inspection.

For a **profile** request, edit the named agent/runtime entry only. Omitted fields
leave selection unchanged. A candidate must be in the agent pool and bound to that
worker runtime. A thinking-only entry is checked against the final selected model.
Resetting a profile field removes that override; resetting an agent/runtime entry
removes only that entry. Preserve sibling agents/runtimes. Defining a profile does
not activate it unless requested. An explicit activation updates `router`.

Agent entries, `router`, `decisions` and named profile definitions replace lower layers
in full. Before a partial edit at a new scope, copy the effective object and
preserve unrelated entries. Report the edited profile and whether it is active.
Complete instances store `models.model` and `models.thinking` together; catalog
bindings store `id` and `thinking` under `models.<name>.runtimes.<runtime>`. Neither reads
models.json. `effort=<label>` remains an input alias for thinking, not a second
stored field. Check requested controls against the actual installed destination's
contract/catalog when available. Reject known incompatible combinations. If the
runtime cannot be inspected, save explicitly requested, schema-valid settings and
label their runtime compatibility unverified; launch still requires preflight.
Never replace the user's model choice with a guessed supported value.
For string thinking, store `allow_thinking_override` alongside that binding's
thinking, or in a complete instance's `models` override policy. Enable it only when requested; a model/thinking change alone does not grant
adaptation. Preserve it when changing one setting. Follow the
[automatic adjustment contract](../../runtime/workers/thinking.md)
for one-run agent decisions. Resetting all model settings to inherit also clears
this permission; resetting only thinking preserves it.

An explicit configuration change includes preparing the isolated validator
environment when necessary: reuse the plugin .venv or an existing compatible
Python, and install requirements.txt there if missing. Follow the executor guide;
do not change global Python or account-wide harness configuration. Read-only show
reports a missing dependency instead of installing it. Configuration changes do
not authorize live worker probes, third-party plugin installation or new credentials.
