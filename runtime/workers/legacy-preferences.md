# Legacy native model preferences

For new per-agent setup, use the [executor configuration guide](../config/executors.md).
This document covers existing models.json files and native control mappings.

No file means no zloop override. Keep the destination's native model and
thinking defaults; bundled agents do not pin them. Never create preferences
during installation or infer them from task
complexity. These settings affect delegated workers only, not the main session.

Worker launches first use [executor resolution](launch.md). New per-agent
executor/model settings live together in `executors.json`; `thinking` is normalized
to `settings.effort`. Their inherit/override policy never reads this legacy file.
The preferences below remain supported only for unconfigured native workers.
Migrate them to agent instances only on explicit request.

To inspect model preferences directly, independently of routing, run:

```sh
python3 "<plugin_root>/scripts/agent_models.py" --repo "<repo>" --host <host> --agent <agent>
```

`repo` is the canonical project checkout supplied by the orchestrator. Carry it
through nested assignments; a worker worktree must not silently lose project
preferences. The preference resolver is read-only, validates both model files and returns only
requested overrides and per-field sources. An error blocks the affected launch;
do not treat malformed preferences as absent. An empty `settings` object means
omit override parameters and use normal native behavior.

## Files and precedence

Optional user file: `~/.config/zloop/models.json`.
Optional project file: `<repo>/.zloop/models.json`.
For each field, strongest first: explicit task choice → project agent → project
defaults → user agent → user defaults → native behavior.
Removing a key reveals the lower layer; JSON `null` masks lower layers and restores
native behavior for that field. Model and effort merge independently. Changing
only the model can retain a saved effort: validate the resulting pair, don't
silently clear the user's choice.

Example shape, **not an installed preset**:

```json
{
  "claude": {
    "defaults": {"effort": "medium"},
    "reviewer": {"model": "opus", "effort": "high"}
  }
}
```

Host keys: `claude`, `codex`, `pi`, `opencode`. Agent keys: `builder`,
`reviewer`, `verifier`, plus `defaults`.
Values are `model` and/or `effort` strings, or `null`. Model IDs/aliases come from
that runtime's catalog; Pi/OpenCode use qualified `provider/model` identifiers.
For Pi, put thinking in `effort`, not a suffix in the saved model ID.

Stored legacy preferences use the resolver's known effort labels; task overrides
may use runtime-specific labels. Adapters must validate the selected model/effort
combination using current native capabilities. OpenCode maps effort to a
**same-named model variant**; if no such variant exists, report it rather than inventing a token budget or provider option.
Explicit task overrides use `--model <id>` / `--effort <level>`; `native` masks a
saved choice for this launch. Do not persist task-only choices.

## Applying overrides

Use only supported native controls. A prose instruction to think harder is not a
reasoning setting. Keep explicit model choices pinned despite workload labels or
provider failures. If policy substitutes a model or a requested setting cannot be
applied, report the requested and actual values and stop that affected assignment;
continue independent work. Do not change account settings or bypass permissions.
These legacy fixed preferences define no alternative pool. Configured pools use
the separate [availability recovery policy](recovery.md).

Record agent, requested settings/sources, native agent/run ID and confirmed model
and effort with the existing worker evidence. Mark unavailable runtime metadata
unverified; never call resolver output the actual model. Resume retains its launch
settings unless the native runtime explicitly supports updating them. Changed
preferences apply to new workers, not silently to an existing child.

For native Claude/OpenCode profile-only controls, add `--profile` to the routed
resolution command. External executors own their standalone launch controls and
must not reuse child-agent profiles. This renders a thin native
snapshot with a content-derived name, without writing it. Write that exact content
at `native_profile.path` relative to the **active native project's directory**;
use an existing identical file, and do not overwrite differing contents. Confirm
native discovery before launch, reloading/restarting if required. Pass the normal
agent, job and adapter paths; a Claude snapshot carries the agent file's body, and no
snapshot copies job instructions.
No settings produces no profile. Ignore obsolete snapshots when resolving new
launches; clean up proven-unused generated files on an explicit reset/housekeeping
request. Do not commit personal snapshots or include them in product changes.

References: [Claude agent settings](https://code.claude.com/docs/en/sub-agents),
[Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[OpenCode agents](https://opencode.ai/docs/agents/). Pi mappings follow the installed
pi-subagents `guide` and model catalog; adapters check the actual runtime schema.
