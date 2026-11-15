# OpenCode workers run through Herdr on a private server

An OpenCode-runtime worker launches only through Herdr, on its own `opencode serve` whose configuration, state and cache are private but whose data folder is the user's, so it uses the user's logins in place and no credential is copied; a keeper stops the server once the worker's pane is gone. The resolver refuses an OpenCode native-subagent route by name: native dispatch is not built, and hosts handed such a route improvised or blocked.

## Considered Options

- Copying the user's login into each worker's own data folder: a token refresh inside a copy can invalidate the user's own login.
- Free models only: no login is needed, but they are rate-limited and their allowance runs out.
- Building native dispatch through OpenCode's own subagents: a separate, larger design with its own live checks.

## Consequences

Worker sessions appear in the user's OpenCode history, and a token refresh by a worker updates the user's own login.
