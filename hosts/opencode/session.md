# OpenCode settings adapter

Select with `harness=opencode`; there is no zloop OpenCode bootstrap extension.
Load shared skills through OpenCode's native skill discovery and preserve their
explicit invocation policy.

The plugin `plugin.js` runs the yield gate and logs each `herdr agent start`. OpenCode
cannot refuse a stop, so when a root session's turn succeeds with a gap, the plugin prompts
that session with the hold's message, once per stop. Child sessions, one-shot
`opencode run` sessions and workers on Herdr's private server are not held; a child
session's launches count as its root session's.

Before configuring, launching or acting as a worker, also read [workers.md](workers.md). In
worker messages, `adapter` is this file's absolute path.

## Resume timer

OpenCode has no built-in scheduler, so an autopilot allowance pause can set no timer here. Put the
resume command, as the user types it in OpenCode, and the time after which it can run in the report, and install no
operating-system timer unless the user asks.
