# OpenCode settings adapter

Select with `harness=opencode`; there is no zloop OpenCode bootstrap extension.
Load shared skills through OpenCode's native skill discovery and preserve their
explicit invocation policy.

The plugin `plugin.js` applies the worker lifecycle's yield gate and logs each
`herdr agent start` for it. OpenCode cannot refuse a stop, so once a root session's turn
succeeds with a gap the plugin sends the hold's message as a prompt in that session, once
per stop. A child session, a one-shot `opencode run` (it exits before the new turn
finishes) and a worker on Herdr's private server (its isolated configuration loads no
plugin) are not held; a child session's launches are its root session's.

Before configuring, launching or acting as a worker, also read [workers.md](workers.md). In
worker messages, `adapter` is this file's absolute path.

## Resume timer

OpenCode has no built-in scheduler, so an autopilot allowance pause can set no timer here. Put the
resume command, as the user types it in OpenCode, and the time after which it can run in the report, and install no
operating-system timer unless the user asks.
