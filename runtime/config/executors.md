# Agent executors

Contents:
- [Configuration examples](#configuration-examples)
- [Named skill executor types](#named-skill-executor-types)
- [Manual setup and inspection](#manual-setup-and-inspection)

## Configuration examples

Optional files: `~/.config/zloop/executors.json` and
`<canonical-main-checkout>/.zloop/executors.json`. Merge the requested agent
into an existing file; do not replace unrelated agent entries. The
[configuration schema](../../schemas/executors.schema.json) ships with the plugin.
An optional `$schema` path supports editors but does not change runtime validation.

This complete example selects the native reviewer and preserves its native
model/thinking settings:

```json
{
  "agents": {
    "reviewer": {
      "executor": {"type": "subagent"},
      "models": {"mode": "inherit"}
    }
  }
}
```

A job can have its own entry of the same shape under top-level `jobs`, for example
`{"jobs": {"reproduce": {"candidates": ["sonnet", "glm"]}}}`; it replaces the
reviewer's entry for reproduce only. The agent entry covers every other job.

To use another execution path, replace only that agent's `executor` object with
one of the following. Executor selection and model policy are separate choices.

**Named sub-agent**, for example another plugin's delegate agent in Claude:

```json
{"type":"subagent","config":{"agent":"<plugin>:<agent>"}}
```

**Skill** ([contract](skill-executor.md)), with the instruction supplied after selecting the installed skill:

```json
{
  "type": "skill",
  "config": {
    "skill": "example-bridge",
    "instruction": "Launch a Codex worker for the supplied read-only review assignment and return its complete report."
  }
}
```

Herdr needs no route or skill: it is the built-in provider in a Herdr session. The built-in `herdr` type is the
lowest layer of `executor_types`: a user, project or session type named `herdr` replaces it, the resolver's
`executor_definition_source` names the one in force, and `{"type": "herdr"}` in a higher layer restores it. A skill's prerequisites and the host's
invocation rules still apply; names are not assumed available across hosts.
For different delegation skills in Claude Code and Codex, configure
[caller-specific skill routes](catalog.md#caller-specific-skill-routes).

**Bash**, for a program implementing the assignment/result contract ([Bash executor](bash-executor.md)):

```json
{"type":"bash","config":{"argv":["bash","/absolute/path/to/worker.sh"]}}
```

Replace the script path with your actual program. This is not a generic wrapper
around any CLI: the program must consume the assignment and model policy on stdin
and return the required result on stdout. Relative argv paths use the worker's
assigned worktree. Use an absolute script path to avoid ambiguity.

To override model/thinking, replace the agent's `models` object:

```json
{"mode":"override","model":"your-runtime-model-id","thinking":"high","allow_thinking_override":false}
```

Replace the illustrative model ID with the one you explicitly choose. Either
model or thinking may be omitted; omitted fields retain destination defaults.
Use `{"mode":"inherit"}` to clear both overrides and preserve native settings.
There is no `destination` field or schema definition inside the configuration.

For multiple permitted workers per agent, bounded thinking, or temporary overrides,
use [models and runtime routes](catalog.md), assignment selection and [session scope](session.md). Define reusable
models under top-level `models` with descriptions and runtime bindings containing
registered IDs and thinking policy. Top-level `runtimes` owns shared launch routes.
Agents reference one definition or a pool of definition
names. The router selects within those pools; sparse profiles contain optional
agent/runtime overrides. Existing fixed instances and maps of complete
candidates remain valid. Models and thinking are chosen together before invocation.

## Named skill executor types

Define a skill and its usage instruction once under `executor_types`, then select
that name with `type`. The example skill name is illustrative; use a verified
installed skill for each caller:

```json
{
  "executor_types": {
    "pi-direct": {
      "type": "skill",
      "config": {
        "skill": "example-pi-bridge",
        "instruction": "Use the single-task delegation entrypoint."
      }
    }
  },
  "runtimes": {
    "pi": {
      "executors": {
        "claude": {"type": "pi-direct"},
        "pi": {"type": "subagent"}
      }
    }
  }
}
```

References also work in a complete agent instance, a map of candidates, or an
explicit task choice. Model policy remains on the agent or model binding. Caller
restrictions remain on runtime routes; naming a type does not supply a skill,
change the host, establish Herdr, or bypass compatibility checks.

Definitions use the existing skill executor schema and must contain a complete
`type: "skill"` / `config` instance. Names cannot replace `subagent`, `skill` or
`bash`. References contain only `type`: change the shared definition or define a
distinct name for another usage. Nested type references and parameter templates
are not supported.

Definitions merge by name across user, project and session scopes; the higher
scope replaces the whole definition. Routing profiles contain agent/runtime candidate and thinking overrides.
The resolver checks referenced names after loading these layers and reports an
unknown name as a configuration error. Inspect all affected agents before saving
or removing a shared definition. Structural schema validation alone cannot prove
that references resolve across files.

Resolved output retains `configured_executor_type` and `executor_definition_source`,
while `executor_type: "skill"` and `executor_config` give the adapter its expanded
instructions. Candidate inspection returns expanded instances with the same source
metadata in `candidate_routes`. No skill is invoked during configuration inspection.

## Manual setup and inspection

The configuration skill can perform setup. For manual setup, replace
`<plugin_root>` with the maintained zloop directory and prepare its isolated
Python environment once, or use an existing environment with requirements.txt:

```sh
python3 -m venv "<plugin_root>/.venv"
"<plugin_root>/.venv/bin/python" -m pip install -r "<plugin_root>/requirements.txt"
```

Reuse a working environment; do not recreate it on every configuration change.
Configured execution needs `jsonschema`; unconfigured native resolution remains
stdlib-only. Ordinary dispatch never installs dependencies automatically.

After saving the requested configuration, inspect one agent without starting work:

```sh
"<plugin_root>/.venv/bin/python" "<plugin_root>/scripts/agent_models.py" --repo "<project>" --harness claude --agent reviewer --route --list-candidates
```

Replace `claude` with the current host (`claude`, `codex`, `pi`, or `opencode`).
The command validates the applicable configuration layers and prints the effective
executor, settings and their source. It does not confirm availability or launch a job.
No reload is needed for JSON changes: new assignments resolve them afresh. Already
running or resumable workers keep their launch settings.
