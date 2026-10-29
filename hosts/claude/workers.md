# Claude Code workers

Before launching a worker, resolve [executors](../../runtime/workers/launch.md) with the plugin's
validator Python and `agent_models.py --route --repo <canonical-repo> --host claude
--job <job>`; include any explicit configuration profile. Record the full result,
without piping through head/tail or masking its exit status. Inspect the pool with
`--list-candidates`, establish the available model/effort pairs below, then select
with `--eligible-candidates` and `--eligible-thinking`. Render profiles only where
native controls require them.
Do this for review workers too, including each partition and later round; an
`zloop:<agent>` name is not a substitute for resolving user configuration. The operations
below apply to `native`; a selected external executor owns its launch/lifecycle
and model controls. The orchestrator retains this host adapter.

Start workers with the native `Agent` tool using the `zloop:<agent>` profile
(or its exact registered name) of the job's agent. Claude loads the agent file natively.
Supply absolute `adapter`, `agent_file`, `job_file`, `plugin_root` and `repo` alongside the
job's inputs, each as `name: value`. Do not request another automatic worktree: dispatch supplies an existing
one. Reuse an agent with `SendMessage`/native resume when supported; otherwise
start a replacement with the surviving report and worktree. Inspect the actual
tool schema; do not treat an ordinary message to an idle agent as a guaranteed
restart. Use native completion notifications or wait tools for background work.
Use an existing task's native wait/output mechanism or the selected executor's
wait operation. A background Bash task completing proves only that its command
ended: inspect the executor result before accepting worker completion. If the user
reports a finished worker while a wait remains pending, reconcile that worker and
repair the wait before launching the next batch. Do not make sleep-and-poll shell
jobs or idle timers to wait for Agent notifications. Stop owned monitors when their
work completes, and ignore late expiration notifications for finished assignments.

Bundled worker profiles allow `SendMessage`, `Monitor` and `TaskStop` for checkpoints
and owned background jobs. Before launch, confirm the current runtime exposes the
needed controls and supply the parent's actual recipient/handle in the assignment.
Workers send checkpoints to that parent; lifecycle actions cover only their assigned
jobs. A native task stop does not cancel a remote job: use the runner's own cancel
and confirm teardown. If messaging is unavailable, use the shared
[bounded return](../../runtime/workers/return.md) fallback.
Regenerate a configured profile after a shipped profile changes; its content-derived
name changes with the tool list. An older registered snapshot retains its old tools,
and the Agent-model hook denies launching one whose tools differ from its shipped agent.

Apply only the model/effort settings returned by executor resolution. Bundled
profiles do not pin either field, so launching `zloop:<agent>` always passes
`model`. When resolution leaves the model unspecified, name the session's model
explicitly; the Agent-model hook requires that. Omitted effort inherits the **parent session's
effort**, which need not be the selected model's default.
See Claude's [native subagent controls](https://code.claude.com/docs/en/sub-agents).
Do not select models from ticket workload labels or re-resolve legacy preferences.
Do not change the main session's model. A model-only override uses Agent's native
`model` argument. If the resolved effort equals a verified inherited session effort,
the bundled agent can use it without a new profile; still confirm the child's metadata.
Otherwise render with `--profile`, prepare/discover the returned native profile and invoke its exact
name instead of `zloop:<agent>`. Set only the resolved fields; an effort-only
override leaves the model unspecified. The generated profile
preserves the shipped agent's tools, restrictions and principles. Inspect existing
native/user profile customizations before using it; carry forward their restrictions
or report a conflict, never weaken them to apply model settings. Verify model/effort
in native task metadata; forced environment settings or allowlists can override a
request. Do not invent an Agent `effort` argument absent from the current schema.
Profile rendering preserves the original decision ID/journal and routing snapshot;
it is not a new model-selection call or a replacement for the decision record.
Use the current session's registered agent inventory as discovery evidence; file
validation or discovery in a fresh subprocess does not prove registration here.
Prepare needed profiles before routing or fan-out. On a failed lookup, record the
capability failure once and reselect from verified combinations. For example, a
verified inherited `medium` permits `--eligible-thinking '{"opus":["medium"]}'`
when medium is allowed by config. `[null]` means an unconfigured/inherited policy,
not "use whichever allowed level the session happens to have"; it cannot replace
a configured bound. Unknown inherited effort supplies no eligible explicit level.
An unavailable effort does not establish that another profile is registered.
Only a successful new resolution supplies launch settings. If none is adequate,
report the missing registration/control; a resolver error cannot be bypassed with
a model-only launch. Preserve explicit pins and the main session's settings.
At launch, follow the shared [decision record](../../runtime/routing/report.md):
record the host action through the worker lifecycle and supply the child's transcript
to `worker_lifecycle.py record` with `kind: startup` and `native`.
Apply the shared executor preflight and job-lifetime rules, including
native background Bash tasks still queued behind a build lock.

Every `Agent` call names its model, helpers included: Explore, general-purpose,
Plan, and other plugins' agents. Choose the cheapest model that is adequate:
- `haiku` for search, lookups and summaries;
- `sonnet` for research, review and bounded implementation;
- `opus` only for hard design, adversarial review or deep diagnosis.

Forks keep the parent's model, so fork only when the shared context is worth it.
The plugin's `PreToolUse` hook (`hooks/agent_model.py`) denies an Agent call with no
model whose definition doesn't pin one, and a zloop agent launch whose
prompt lacks `job_file:` or any input of that job, or names a job of another agent. `ZLOOP_AGENT_MODEL_HOOK=off` disables
it. A second `PreToolUse` hook (`hooks/pr_body.py`) denies `gh pr create` and
`gh pr edit` unless the description comes from a `--body-file` that passes
`scripts/lint_pr_body.py`. `ZLOOP_PR_HOOK=off` disables it. A third, `hooks/herdr_launch.py`,
logs each `herdr agent start` for the yield gate and never blocks. A `Stop` hook
(`hooks/yield_gate.py`) applies the worker lifecycle's yield gate when the session
stops, and names Herdr workers you launched but did not register;
`ZLOOP_YIELD_HOOK=off` disables it. A non-Anthropic model needs a configured cross-runtime route (Herdr, Pi or
Codex), not an Agent call. Offer those routes when an agent's configured pool
includes them and the assignment is long enough to justify the launch overhead.

Use at most the available child slots, with 8 as the scheduling ceiling rather
than a promise of capacity. Count review, reproduction and E2E workers too. A
reviewer completes its assigned axes directly. The orchestrator schedules separate
review assignments within available capacity.

Keep draining in the active session, using the runtime's background lifecycle.
If the user explicitly requests a persistent goal and this runtime supports it,
install it through the actual native goal operation using dispatch's scoped
completion condition. Do not assume that printing `/goal` executes it. Read the
current goal contract; do not depend on a particular evaluator, idle cadence or
turn limit. Follow shared [blocked continuation](../../runtime/workers/focus.md#blocked-continuation)
for unresolved user decisions and resource grants. Without a goal, keep waiting
for live workers in the current run.

## Top-level workers

From a Claude orchestrator, `agent_models.py --host claude --route` resolves a Claude-runtime
worker as a native subagent: `native_dispatch` is true and there is no `launch`. Start it with the
`Agent` tool; its restrictions are the agent profile's tools, which Claude enforces, plus
the read-only worktree check at collection. It is not launched through Herdr.
Register it with its `agentId` as `native_session`. Its transcript lives at
`~/.claude/projects/<project>/<session>/subagents/agent-<agentId>.jsonl`.
A `PreToolUse` hook
(`hooks/read_only_writes.py`) denies a `writes: none` subagent (its `agent_type`) Edit, Write,
NotebookEdit and a Bash `>`, `>>`, `&>` or `tee` to a literal path in the main checkout or its
worktrees, except `.scratch/<slug>/reports/` and the `worktree:` its assignment names (that
transcript's first user message, even before registration; collection checks it is restored);
writes elsewhere (`/tmp`) pass. Undetected: run-time paths (`$VAR`, `$(...)`),
`<>`, escaped spaces, `#` in a word, `sh -c`, `sudo` or `xargs tee`, a `cd` that never runs,
a symlink the command makes, programs that open files (`python -c`, `sed -i`, `cp`) and
MCP tools; against those hold the agent's absolute-path rule and, for a builder, `--accept`'s check of
its ticket branch.

A Claude worker started as its own process takes a route's `launch.argv` after `--`, and only a
non-Claude orchestrator (Codex, Pi or OpenCode, resolving with its own `--host` and
`--target-runtime claude`) gets one, to start through Herdr. Every restriction is in it, since the user's default mode
is `auto`. It sets `--permission-mode dontAsk` and the agent's tools as `--allowedTools`,
then `--model` and `--effort`. For `writes: none`, resolve with `--worktree <abs worktree>
--reports <abs reports dir>` (the resolver refuses without them); the launch then adds absolute
rules that deny edits under the worktree and allow them under the reports directory, shell-quoted:

```
--settings '{"permissions":{"deny":["Edit(//<worktree>/**)"],"allow":["Edit(//<reports>/**)"]}}'
```

The resolver refuses a reports directory inside the worktree, since the deny would beat the
allow; review from a separate worktree, such as `.worktrees/review-<slug>`. A deny of this kind was probed against the Edit tool, the Write tool and a shell redirect; it
does not stop a script that opens files itself, and MCP tools the user's settings allow stay
callable. Start the worker only in a worktree of a checkout the user already trusts: anywhere
else the trust dialog blocks the start (Herdr: `agent_not_ready`, agent `blocked`). Report that
dialog to the user; never answer it. Claude's write scope rests on these launch flags plus the
read-only worktree check at collection.

Startup of a Herdr-started Claude worker follows [the startup order](../../runtime/workers/prepare.md):
`herdr agent get` reports `agent_session` (kind `id`). The transcript is
`~/.claude/projects/<project>/<id>.jsonl`; pass it as `native` to `submit` and `collect`.

The [executor guides](../executors/README.md) cover each executor type a route can select.
