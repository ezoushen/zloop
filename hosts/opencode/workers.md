# OpenCode workers

Resolve [executors](../../runtime/workers/launch.md) first and follow the selected
executor. OpenCode workers use the supported Herdr route. Use
[pool recovery](../../runtime/workers/recovery.md) for availability failures;
configuration does not authorize installing providers or changing the main session.

## Top-level workers

Launch through `hooks/herdr_launch.py start` with the saved route, following the
[Herdr guide](../herdr.md#start-each-worker-in-a-tab-of-its-own). The launcher owns
private-server setup, tool restrictions and OS sandbox checks. Follow its refusals;
do not substitute the user's running OpenCode service, whose configured MCP/plugin
tools can remain callable under session deny rules. Verify available providers and
models on the private server before productive work.

Use the route's exact settings and job scope. Native permissions do not authorize
work outside the agent's permissions. For the job contract, inspect `agent_defs.py
--job <job>` and pass its absolute paths and inputs.

The launch returns `opencode.server`, `opencode.session` and
`opencode.password_file`. Never print the password file. Register that session ID
as `native_session` under [worker attempts](../../runtime/workers/prepare.md).
Export its current native evidence from the same server at startup, submission and
collection:

```sh
OPENCODE_PASSWORD="$(cat <password-file>)" opencode session export <session-id> --server <url> > <native.json>
```

Use the absolute export path as `native`. The export does not identify its server;
the orchestrator must preserve that provenance. Run a server's `opencode run` with
stdin from `/dev/null` to avoid blocking on non-terminal input. Herdr observes the
full TUI; `mini` does not supply its lifecycle activity.

After final collection, close the owned pane; TUI exit alone does not establish
teardown. Preserve uncertain workers for reconciliation rather than starting a
second private server blindly. The launcher owns its keeper and credential cleanup.

The [executor guides](../executors/README.md) cover other selected executor types.
