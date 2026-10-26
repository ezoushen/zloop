# Models and runtime routes

## Configure an agent

Define models by task-fit description and **runtime bindings**. Each binding holds
that runtime's registered `id` and optional thinking policy. Define launch routes
once under top-level `runtimes`, shared by every model using that runtime. Agents
reference a model with `{"model":"sol"}` or list allowed models in a pool.

Here `sol` is the catalog name, `codex` is the worker runtime, and `gpt-6.1-sol` is its
registered model ID. Replace illustrative local IDs with installed catalog values:

```json
{
  "models": {
    "sol": {
      "description": "Cost-conscious complex implementation, repair and scoped review.",
      "runtimes": {
        "codex": {
          "id": "gpt-6.1-sol",
          "thinking": {"default": "medium", "allowed": ["medium", "high"]}
        }
      }
    },
    "glm": {
      "description": "Heavier local implementation and review.",
      "runtimes": {"pi": {"id": "local/glm"}}
    }
  },
  "runtimes": {
    "codex": {"executors": {"codex": {"type": "subagent"}}},
    "pi": {"executors": {"pi": {"type": "subagent"}}}
  },
  "agents": {
    "builder": {"candidates": ["sol", "glm"]},
    "reviewer": {"model": "sol"}
  }
}
```

A model may also state `cost` (`subscription`, `free` or `local`) and `data`
(`local`, `no-retention`, `provider` or `trains`). A model whose `data` is `trains`
uses prompts to train the provider's models, so it is unavailable unless a
configuration layer sets top-level `"allow_training": true`: a pool lists it in
`unavailable_candidates` with that reason, and an agent pinned to it fails. The last
layer that sets `allow_training` wins; set it only in the `.zloop/executors.json`
of a repository whose code may be used that way.

A subscription candidate whose provider's allowance level is `critical` is likewise
unavailable, with the allowance as its reason, unless a configuration layer sets
top-level `"include_critical_allowance": true` (the last layer wins). A job left with
no eligible candidate by that exclusion is refused with a message naming the allowance
and the setting.

`models.<name>.runtimes` lists supported worker runtimes: `claude`, `codex`, `pi`,
`opencode`. Herdr is a launch mechanism, not a worker runtime or model provider.
One model can have multiple bindings with distinct IDs and thinking policies.
Prefer its current-host binding when configured and reachable, then other bindings
in declaration order. This preference never infers model support from its name.

`runtimes.<target>.executors` contains routes indexed by **caller context**. Try
the actual caller's host key first, then `herdr` only with `HERDR_ENV=1`; a Pi caller tries `herdr` first. Herdr is
built in, so with `HERDR_ENV=1` every runtime has a `herdr` route even when none is configured. For example,
`runtimes.pi.executors.pi` launches native Pi children outside Herdr. A caller-specific named subagent can bridge runtimes:
`runtimes.codex.executors.claude` may select an installed Codex delegate in Claude.
An unnamed subagent is valid only when caller and target are the same runtime.
Only the actual caller's key and an eligible `herdr` route participate; another
host's route is never borrowed, even when its skill file exists on disk or its
profile is selected. Caller keys already scope routes, so no separate
`supported_runtimes` field is needed.

Routing profiles override candidate/thinking by agent and worker runtime; they do not create runtime capabilities.
A model with no reachable binding is an inspectable exclusion. Unknown model or
runtime references are configuration errors. The selected route still needs runtime
preflight. Native availability, tools and supported settings must be established;
configuration does not prove a provider or worker can execute.

Model, runtime, profile and named executor definitions merge by name across user, project and session layers;
each definition replaces the lower definition in full. Agent entries, `router` and
`decisions` also replace in full. The resolver reports `target_runtime`, `executor_context`, model/binding
and executor sources; pool inspection includes each candidate's resolved route.
These fields describe the requested route, not observed runtime identity.

Existing complete executor instances and maps of complete candidates remain
supported, including inherit mode. Use these for destination defaults or a fixed
assignment-specific executor/model policy. For catalog migration, move the previous
`model` value to `models.<name>.runtimes.<target>.id`, thinking policy alongside it,
and launch routes to `runtimes.<target>.executors`. Confirm the destination from its
installed contract or existing known setup; never infer it from model names or prose
alone. Reconcile conflicting shared routes before saving. The old catalog layout
fails with a migration diagnostic; it is not silently treated as native defaults.

Use `thinking: {"default":"medium","allowed":["medium","high"]}` on a runtime
binding, or `models.thinking` on a complete instance. The default must belong to the
nonempty, unique allowed set. A single value locks thinking. Omit
`allow_thinking_override` with this form; strings and the legacy boolean remain
supported. Bounds apply to explicit task thinking too. Runtime-specific IDs and
thinking policy move together; one runtime never borrows another's labels.

Provider URLs, credentials, quantization and inference options stay in the owning
runtime's configuration. Register local models there and reference their exact IDs.
Omit thinking when no supported control is configured. Record serving versions with
evidence when they affect comparisons; zloop does not configure model servers.

### Caller-specific skill routes

For cross-runtime delegation through a skill, configure each caller's own interface.
For example, this runtime definition launches Pi through different skills in Claude
Code and Codex, while Pi uses native children:

```json
{
  "runtimes": {
    "pi": {
      "executors": {
        "claude": {
          "type": "skill",
          "config": {
            "skill": "example-claude-pi-bridge",
            "instruction": "Delegate the supplied assignment to Pi and return its complete report."
          }
        },
        "codex": {
          "type": "skill",
          "config": {
            "skill": "example-codex-pi-bridge",
            "instruction": "Delegate the supplied assignment to Pi and return its complete report."
          }
        },
        "pi": {"type": "subagent"}
      }
    }
  }
}
```

The skill names are illustrative. Replace them with verified interfaces in each
caller; one portable skill may use the same name in both entries. Preserve existing
sibling routes, including `herdr`, when editing a runtime definition. Model IDs and
thinking stay in the model's Pi binding. Apply the same pattern under
`runtimes.opencode` for skills that launch OpenCode.

A caller-specific route takes precedence over the shared Herdr route even inside
Herdr, except for a Pi caller. Configure one only when its [skill contract](skill-executor.md) can honor the
assignment and model policy. Neither an explicit caller key nor the shared `herdr`
key proves that the skill and its tools work in the current host.
To reuse the same skill usage across routes, define a
[named executor type](executors.md#named-skill-executor-types) and reference it with
`{"type":"<name>"}`. Different callers can select different definitions.
