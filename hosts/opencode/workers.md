# OpenCode workers

Resolve [executors](../../runtime/workers/launch.md) first. A configured external executor, such as
the Herdr skill, owns invocation and collection through its own adapter. Apply
[pool recovery](../../runtime/workers/recovery.md) for availability failures.
The native settings procedure below does not implement that external lifecycle.

For model management, use only the settings returned by executor resolution.
Do not select models from workload labels or re-resolve legacy preferences.
Omitted settings retain native defaults. For a default native worker override, render
with `--profile` and prepare the returned `.opencode/agents/` snapshot. It uses
`mode: subagent`, optional `model: provider/model` and optional `variant: <effort>`.
OpenCode applies both a profile's model and its variant only to a Task subagent; for
a top-level worker, see [top-level workers](#top-level-workers). Check the model's variants with
`opencode api GET /api/model -H x-opencode-directory:<dir>`; a model without the
same-named variant cannot fulfill the effort request. For effort-only settings,
establish the otherwise inherited model first. Do not translate arbitrary effort
labels into provider-specific token budgets.

Confirm the loaded profile with `opencode api GET /api/agent -H
x-opencode-directory:<dir>` (its `model.providerID`, `model.id` and `model.variant`);
retry while the list lacks the built-in `build` agent, which a directory's first
request can omit. Then use its registered name when the native Task tool supports it.
Preserve project/native permissions; inspect any existing customized worker profile
and retain its restrictions or report a conflict before using a generated profile.
Pass absolute `plugin_root`, `adapter`, `agent_file`, `job_file`, canonical `repo` and
assigned worktree/branch plus the job's inputs. Workers read the agent and job files;
model settings do not authorize additional work or change agent permissions. Native profile inspection
proves configuration loading, not successful provider execution.

## Top-level workers

A top-level OpenCode 2 worker, such as one launched through Herdr, runs the full TUI,
which has no `--agent` or model flag and ignores a profile's variant. Its agent, model,
variant and write scope come from the session it opens, created on a private server.
The user's running service does not fail closed: MCP and plugin tools from the user's
configuration stay callable under a session's deny rules.

1. Start `opencode serve --hostname 127.0.0.1 --port <free port>` with `XDG_CONFIG_HOME`,
   `XDG_DATA_HOME` and `XDG_STATE_HOME` in a fresh directory and a known `OPENCODE_PASSWORD`,
   and with `HERDR_PANE_ID` and `HERDR_TAB_ID` unset (`env -u HERDR_PANE_ID -u HERDR_TAB_ID`): the
   server's Herdr plugin reports state for the pane in that environment, which from the orchestrator's
   shell is the orchestrator's pane.
2. Create the session from the route's `launch.api`: `opencode api POST /api/session
   --server <url> -d '<body>'`, with `"location":{"directory":"<worktree>"}` added to
   `launch.api.body`; without it the session gets another directory. The body denies edit
   and subagent for `writes: none` and subagent for `worktree`; a denied agent still
   writes through the `general` subagent unless subagent is denied.
3. In the worker's tab, export `OPENCODE_PASSWORD` and the same isolated `XDG_CONFIG_HOME`,
   `XDG_DATA_HOME` and `XDG_STATE_HOME`; without them the TUI loads the user's own `tui.json`, not the
   Herdr reporter. Then start the TUI with `--server <url> --session <id>`.
4. Follow [the startup order](../../runtime/workers/prepare.md), then redirect
   `opencode session export <id> --server <url>` (`opencode export` in version 1) to a file.
   Pass that file as `native` to `submit`, or `worker_lifecycle.py record` with `kind: startup`.
   Export again for `collect`.
   The export records no server, so `submit` cannot tell an export of the private server's
   session from one of the user's service: export with `--server <url>` and never substitute.

Run `opencode run` against the server with stdin from `/dev/null` (`< /dev/null`); it blocks when stdin is
not a terminal.

A `writes: none` worker keeps the shell for checks and its report but no editing tool; the
read-only worktree check at collection covers what its shell writes.
`opencode mini --model <provider/model>#<variant>` applies settings at launch, but Herdr
cannot observe `mini`'s activity, so it does not suit lifecycle workers.

**Models on the private server.** The isolated server has none of the user's providers or
credentials. The built-in `opencode` provider's free models ran there without credentials.
For another provider, add it under `providers` in the server's configuration
(`$XDG_CONFIG_HOME/opencode/opencode.json`, or `OPENCODE_CONFIG_CONTENT` in the server's
environment, which a server started this way reads), with its key as `{env:<NAME>}` and that
variable set for `opencode serve` ([config](https://opencode.ai/v2/docs/config),
[providers](https://opencode.ai/v2/docs/providers)). List what the server offers with
`opencode models --server <url>`. Not verified: such a provider end to end, a variant set at
session creation (submit checks it), and whether `opencode auth` with the isolated
`XDG_DATA_HOME` gives the server credentials.

This adapter covers model configuration and profile selection. Full zloop
native dispatch lifecycle/worktree integration in OpenCode is not implemented or verified;
do not claim native end-to-end support or invent a substitute CLI. Configured
external execution still needs its own capability checks and acceptance evidence.
Configuration management does not install a provider, change the main agent,
or enable another host.

The [executor guides](../executors/README.md) cover each executor type a route can select.
