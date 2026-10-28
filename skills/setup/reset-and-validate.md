# Reset and validate

## Reset only the requested settings

For **reset --scope session** without fields or a candidate, remove the named agent
entry to expose durable defaults, preserving unrelated entries. An explicit
whole-session reset uses `--reset-session`. Field/candidate resets use the instance
rules below. Within a pool, reset only the requested candidate's settings, preserving
its executor and siblings. Deleting a candidate requires an explicit request;
update any referencing profile entries in the same change.

For **reset --route** outside session scope or a reset of one candidate's model
settings, preserve the executor and set `models: {"mode":"inherit"}` when no fields
are selected.
Catalog runtime bindings require an ID. Removing only thinking is valid; inheritance
uses a complete executor instance instead of a model reference. Explain that
conversion and preserve sibling candidates and unrelated shared definitions.
Resetting a specific model/thinking field removes that override; it does not reveal legacy preferences. Removing the instance reveals a
lower scope; setting it to null masks lower instances and restores existing native
behavior. Report which reset was applied and its effective result. Do not silently
remove executor selection when the user asks only to reset models.

## Validate and report

Validate candidate executor JSON with `validate_execution_config` from
`scripts/agent_models.py` before replacing the original, then resolve every affected
agent with --route --list-candidates, including every affected profile. Resolve
profile overrides with the relevant --target-runtime and candidate to check thinking.
A pending selection is valid and never authorizes a worker probe. Preserve unrelated
entries. Finish with the saved absolute path,
agent, executor/selection, model policy, model/thinking (or inherited), and any
pending runtime checks. Show the focused diff without claiming a worker was tested.
New assignments read changes automatically; identify a reload only when native
profile discovery or plugin installation actually requires one.
The former per-model executor catalog needs the migration described in [models and runtime routes](../../runtime/config/catalog.md);
confirm destination runtimes from known configuration/contracts, preserve IDs and
thinking, and reconcile shared route conflicts. Back up the existing file before
migration. Legacy package/routes files use the executor
[migration](../../runtime/config/troubleshooting.md#migration). Never
reinterpret malformed/old configuration as an absent configuration.
Named executor types encapsulate skill usage under the bundled schema; they do not
load custom schemas, code or nested definitions. Resolve references after merging
user/project/session layers; reject unknown names without invoking a skill.

## Legacy preferences and native profiles

For structured legacy commands without **--route**, **--config-profile**, session
scope or pool options, show/set/reset models.json.
Accept thinking as an input alias but store legacy `effort`; `defaults` applies only
here. Preserve its per-field precedence and null masks. Validate with `read_config`
and resolve the affected harness/agent. No harness argument defaults to the current
host. Do not edit legacy settings expecting them to affect a configured instance.
A one-run request stays in the assignment, not a saved file.

For default native workers, prepare generated profiles only when requested controls
require them, preserving existing restrictions. Named sub-agents, skills and Bash
executors use their own supported controls; never replace a named delegate with a
zloop profile. Changes apply to new workers; do not restart live workers or
change the main model. Clean up only generated profiles proven unused by active or
resumable assignments. Report required reloads and unverified capabilities.
