# OpenCode workers

Resolve [executors](../../runtime/workers/launch.md) first. A configured external executor, such as
the Herdr skill, owns invocation and collection through its own adapter. Apply
[pool recovery](../../runtime/workers/recovery.md) for availability failures.
Herdr's [top-level workers](#top-level-workers) route is the only supported OpenCode
worker route. Native OpenCode dispatch is not supported; the resolver refuses an
OpenCode-runtime model on a subagent executor.

For model management, use only the settings returned by executor resolution.
Do not select models from workload labels or re-resolve legacy preferences.
Omitted settings retain native defaults. Pass absolute `plugin_root`, `adapter`,
`agent_file`, `job_file`, canonical `repo` and assigned worktree/branch plus the job's
inputs. Workers read the agent and job files; model settings do not authorize
additional work or change agent permissions.

## Top-level workers

A top-level OpenCode 2 worker, such as one launched through Herdr, runs the full TUI,
which has no `--agent` or model flag and ignores a profile's variant. Its agent, model,
variant and write scope come from the session it opens, created on a private server.
The user's running service does not fail closed: MCP and plugin tools from the user's
configuration stay callable under a session's deny rules.

Launch through `python3 <plugin_root>/hooks/herdr_launch.py start` with the saved route,
worktree, assignment, role and name, as the [Herdr guide](../herdr.md#start-each-worker-in-a-tab-of-its-own)
specifies. It refuses before opening a tab if any of the three installed Herdr files is missing,
naming `herdr integration install opencode`. The launcher copies `plugins/herdr-agent-state.js`,
`herdr-tui-session.js` and `herdr-opencode/tui.js` into private configuration; `tui.jsonc` loads the
v1 reporter and `cli.json` loads `./herdr-opencode` for v2. It copies no other user configuration.
Generated `opencode.json` allows edit, bash, webfetch and external_directory, leaving the session's
deny rules to set write scope without permission prompts, including reports outside the worktree.

The tab's environment carries `ZLOOP_WORKER`, private `XDG_CONFIG_HOME` and `XDG_STATE_HOME`, and
`OPENCODE_PASSWORD`. Herdr starts the OpenCode TUI itself with only `--server <url> --session <id>`.
A detached launcher keeper owns the loopback server outside the pane, with its streams and the
server's streams sent to `/dev/null`: `opencode serve` logs its password. The server receives the
worker's `HERDR_PANE_ID` and `HERDR_TAB_ID`, so its integration reports that worker. Configuration
overrides are cleared; `XDG_DATA_HOME` (or its default) stays, sharing logins without copying credentials.
The keeper creates the route's session with `location.directory` set to the worktree. The session denies
edit and subagent for `writes: none` and subagent for `worktree`; denying subagent also prevents writes
through `general`. Every few seconds the keeper checks the pane through Herdr; `pane_not_found`, a minute
of failed lookups, the server's exit, SIGHUP or SIGTERM stops its server and removes configuration, state
and password. A single failed lookup does not. Close the pane after
final collection; TUI exit alone does not remove a pane. Startup failures name their step and stop the
keeper/server while leaving the tab's launch logged for recovery.

The launcher's response preserves Herdr's agent-start result and adds `opencode.server`,
`opencode.session` and `opencode.password_file`. The last names a file readable only by its owner;
never print its contents. Register the printed session ID as `native_session` and use
[prepare](../../runtime/workers/prepare.md) for registration and collection. Export the native record
from that server, with `OPENCODE_PASSWORD` read from the printed password file:

```sh
OPENCODE_PASSWORD="$(cat <password-file>)" opencode session export <session-id> --server <url> > <native.json>
```

Use the absolute export path as `native` for `worker_lifecycle.py record` (`kind: startup`) and
`submit`; export again before `collect`. Parse the complete retained export. The export records no
server, so the lifecycle cannot distinguish a private-server export from one of the user's service:
keep `--server <url>` and never substitute another server. The password file remains until the keeper stops.

Run `opencode run` against the server with stdin from `/dev/null` (`< /dev/null`); it blocks when stdin is
not a terminal. A `writes: none` worker keeps the shell for checks and its report but no editing tool;
the read-only worktree check at collection covers what its shell writes.
`opencode mini --model <provider/model>#<variant>` applies settings at launch, but Herdr cannot observe
`mini`'s activity, so it does not suit lifecycle workers. The private server has no providers from the
user's configuration; the available providers and models must be checked on that server before work.

Configured external execution needs its own capability checks and acceptance evidence.
Configuration management does not install a provider, change the main agent,
or enable another host.

The [executor guides](../executors/README.md) cover each executor type a route can select.
