# Pi adapter

The bootstrap extension supplies this adapter's absolute path. Pi discovers the
shared skills recursively through the development symlink. The extension registers
`/zloop:<skill>` for each skill the user types, completing the flags and modes in its
`argument-hint`; it sends `/skill:<skill> <args>`, which also works typed directly.
Preserve `disable-model-invocation`.
Claude profiles and Codex metadata do not register Pi workers.

Panel: [the panel doc](../../runtime/workers/panel.md).

The extension also guards against loops: a tool call that returned the same result
three times in a row is blocked when repeated, with a reason telling the agent to
change approach or report that it is blocked, and a second blocked repeat stops the
turn. Calls whose output changes, such as polling, are not affected.
`ZLOOP_LOOP_GUARD=off` disables it.

Pi's `bash` has no default timeout, so the extension gives a call that sets none a
120 s timeout, as Claude Code and OpenCode do natively; Codex returns from a long
command after a few seconds and leaves it running in a session. An explicit `timeout`
is kept, and background tools such as `bg_run` are not bounded.
`ZLOOP_BASH_TIMEOUT=off` disables it.

Before delegating, resuming or acting as a worker, also read [workers.md](workers.md). In worker
messages, `adapter` is this file's absolute path.

## Resume timer

An autopilot allowance pause schedules its resume with the extension's `zloop_wake` tool: `at` is the reset time
as an ISO 8601 time with its offset, a few minutes after it, and `command` is `/skill:autopilot --take-over --timed`, which
Pi expands as a skill command and queues if the agent is busy. Record the timer id it returns on the card; `cancel`
with that id drops a leftover. The timer is lost if Pi exits or replaces the extension (`/reload`, a new session) before it fires, and a pending timer never keeps a
headless run alive. Pi drops a wake that arrives while it compacts, so the card keeps the resume command. The user's request for the resume (the run card) is the explicit request for scheduled work
that [workers.md](workers.md) asks for.
