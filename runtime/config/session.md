# Session scope

`--session <root-session-id>` or `ZLOOP_SESSION` enables the session layer.
Use the real root session ID when available; otherwise create one stable ID when
the first session override is requested and retain it in existing workstream state.
Pass that ID and canonical repository to descendants and resume messages. Workers
use their actual host adapter; carrying the session ID does not pin the parent's host.
Only the orchestrator changes the shared override record.

State lives under `${XDG_STATE_HOME:-~/.local/state}/zloop/sessions/`, keyed by
canonical repository and root session ID. The record includes both identities and
`config`, using the same schema (including `executor_types`) as executors.json. Inspection does
not create it. New sessions use durable defaults; resume with the same ID restores
the session layer. Store policy only; keep transcripts and credentials in their
owning systems.

Use the setup command with `--scope session` to edit it. Its helper operations are:

```text
--session <id> --save-session <configuration.json>
--session <id> --reset-session
```

These are explicit writes: save validates and atomically replaces the session's
complete configuration, so preserve unrelated entries from its existing `config`.
Reset removes only this session record and exposes project/user defaults. Both
require the normal repo/host/agent/route arguments. For an agent-only reset, remove
its override from the saved configuration instead of resetting the whole session.
The resolver returns the absolute state path in `session.path`. Root identity is
checked when reading, replacing or deleting an existing record.
