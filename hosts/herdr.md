# Herdr environment guidance

Contents:
- [Route before launching](#route-before-launching)
- [Start each worker in a tab of its own](#start-each-worker-in-a-tab-of-its-own)
- [Wait and recover](#wait-and-recover)
- [Close owned workers after their final use](#close-owned-workers-after-their-final-use)
- [Hand off to another machine](#hand-off-to-another-machine)

Load alongside the current host adapter when `HERDR_ENV=1`. Herdr provides a
transport; it does not replace the Claude, Codex, Pi or OpenCode host identity.
Read the installed Herdr skill when using that transport. It owns CLI syntax,
session prerequisites and identity checks; this file owns zloop coordination.

## Route before launching

Herdr is zloop's built-in worker provider. A Pi caller launches its workers here; Pi's own delegation would come
from a third-party extension. Other callers prefer their native children for same-runtime assignments and use Herdr
for cross-runtime ones or an explicit Herdr route.
Preserve an explicit user-selected executor and model policy;
do not infer the destination from a model name or silently replace a saved route.
Use [runtime definitions](../runtime/config/catalog.md) to share launch routes
across models; model bindings declare each runtime's registered ID and thinking
policy. Agent pools stay shared; routing profiles override candidate/thinking by
worker runtime only when selected explicitly. If a saved route sends a non-Pi caller's same-runtime
work through Herdr, show the mismatch and
use the setup command for an authorized correction. Environment detection alone
does not rewrite configuration. Native capacity or permission limits do not
authorize a Herdr fallback; queue work or report the boundary.
Quota/provider failures instead follow [pool recovery](../runtime/workers/recovery.md):
exclude the failed account/capability, choose a suitable allowed alternative and
state the switch. Do not try another model on the same exhausted account.

Pass the destination's host adapter to a cross-framework worker, plus this guide
if it runs inside Herdr. It completes the assigned scope directly. Additional
assignments return to the orchestrator, which resolves each worker from its actual
runtime bindings and the active router.

Use absolute paths for the adapter, agent and job files and required method sources, and verify
their reads succeeded before productive work. A long Claude prompt can arrive as
pasted content: naming a slash command inside it does not prove phase invocation.
Use a short native slash command for an authorized phase reserved for explicit
user invocation; keep its longer scenario in a supplied file. Use the Skill tool
only where the skill permits model invocation. Retain the actual source/result.
Session-local settings isolation must keep the plugins the assignment invokes,
such as zloop for its slash commands, and Herdr's native identity hook;
verify both before accepting the launch.
Launch an OpenCode 2 worker as the full TUI, on a private server, as the
[OpenCode adapter](opencode/workers.md#top-level-workers) describes; Herdr's
integration reports the TUI's activity and session, not `mini`'s. Its isolated
configuration keeps the installed Herdr server plugin and, for the full TUI, its native TUI
session reporter, and nothing else of the user's: MCP tools from the user's configuration
stay callable under the session's deny rules. Reproduce the installer's current TUI
configuration in the isolated config directory, including its referenced module; a
server-plugin entry alone does not load the TUI reporter. Use the supported configuration filename (for
example, `tui.jsonc`) rather than assuming another file is read.
Check `integration status` and the actual `agent_session` before productive work;
a current server alone does not establish that its integration is loaded.
If startup returns before the native input UI finishes loading, wait for its
actual input marker before the first prompt. After a stalled prompt, inspect the
same conversation and its native output before deciding whether to retry.
Accept startup only after all required source reads and the final assistant turn
have completed; an early READY sentence or transient settled status is insufficient.
Parse the complete retained native export before collection, redirected to a file as
the adapter says; exit zero alone does not establish integrity.

## Start each worker in a tab of its own

Start every worker in a tab of its own in your workspace, never as a split of your
pane, whatever placement the installed skill defaults to:

1. `herdr tab create --workspace <orchestrator workspace> --cwd <worktree> --label "<slug>/<assignment> <role>" --env ZLOOP_WORKER=<slug>/<assignment> --no-focus`,
   with your `HERDR_WORKSPACE_ID` as the workspace. `ZLOOP_WORKER` is how
   zloop's stop check tells a worker from its orchestrator.
2. `herdr agent start <name> --kind <kind> --pane <returned root pane> -- <launch args>`,
   with the `.result.root_pane.pane_id` that step 1 returned. The launch args are the
   route's `launch.argv`; a Claude coordinator's Claude-runtime worker is a native subagent with no
   `launch`, so the Claude recipe applies only to a Claude worker of another host's coordinator. The argv holds the worker's write scope, model and effort; each
   runtime's recipe is in its adapter: [Claude](claude/workers.md#top-level-workers),
   [Codex](codex/workers.md#top-level-workers), [Pi](pi/workers.md#top-level-workers) and
   [OpenCode](opencode/workers.md#top-level-workers), whose args name a private server and session.
3. Then register it, adding `"herdr": {"session": "<session>"}`; `herdr session list`
   names the session. Registration reads the worker's pane, tab, workspace and name
   from `herdr agent get` and refuses a tab that holds another pane or is your own.

Retirement closes the pane, which closes the tab; closure is recorded only once
`herdr tab get` answers `tab_not_found`.

## Wait and recover

Bind each prompt, independent wait and collected result to its registered attempt
under [worker attempts](../runtime/workers/prepare.md) and [waits](../runtime/workers/wait.md). Resume from its pending
receipts and native worker state; rearm consumed waits after reconciliation.
Register each pane right after its startup probe, before the assignment: the plugin logs every `herdr agent start`,
and the stop check names a launch into `.worktrees/`, or a live agent there, that no
attempt registers, even after its pane closed. Dismiss a launch that failed, with its
reason, as the worker attempts doc describes.

Submit the assignment with `herdr agent prompt <worker> <assignment> --wait --until working
--until blocked --timeout 45000`, which returns once the worker starts its turn; plain
`--wait` holds until the turn settles, and `submit` refuses it. Acknowledge its receipt and
record `reconciled` ([collect](../runtime/workers/collect.md)), then arm `watch` with
`herdr --session <session> agent wait <worker> --timeout <ms>` and `notify` for the
completion; it returns at once when the turn has already settled.

Read the installed skill's completion section and current command-group help
before the first wait. Use its default settled-state operation, with a bounded
timeout; `idle` can be a completed turn. Reserve state filters for a specific
state-dependent task. For the lifecycle's `notify` channel, use
`herdr --session <session> agent prompt <parent> {receipt}` with no `--wait` or
`--timeout`; for wake-triage `capture`, use
`herdr --session <session> agent read <worker> --source visible --lines 200`. Acceptance still requires this assignment's full report
and revision, including evidence that the submitted work actually ran.

Preserve the exit status and error body. A syntax error needs corrected syntax
from the installed CLI, not another wait using the same command. After a timeout,
stalled prompt or uncertain submission, inspect that same worker before waiting
again or resubmitting; neither a shell success message nor timeout is completion.
Workers raise holds through the supplied escalation channel, or by returning
bounded. If a worker opens its native question UI instead (for example Codex
`request_user_input`), the orchestrator answers only within its own mandate. Record
the answer as orchestrator-originated, not the user's, and leave choices reserved to
the user with the user.
Inspect native approval dialogs against existing explicit user authorization;
when it covers the exact fixture action, apply that scoped approval without asking
again. Otherwise keep the dialog for the user. Stop obsolete owned monitors once the
assignment settles and its result is verified.

## Close owned workers after their final use

Record created pane/worker IDs with the assignment. After accepting its final
result and verifying durable reports/evidence, close its owned pane automatically
through the installed skill, then confirm closure and clear the live handle. Use
the [collection](../runtime/workers/collect.md) and [retirement](../runtime/resources/retire.md) integration
so native closure also retires its registered handle.
An idle reviewer needed for a repair recheck stays available until that phase ends;
retiring it early requires the explicit handoff in the [skill executor](executors/skill.md).
Retain active, approval-blocked or deliberately reusable workers with a stated
reason. For a reusable worker, the reason names the assignment it will take next.
When that assignment goes to another worker or finishes, close the pane. Existing user panes and the caller's pane are not cleanup targets.
Before returning, account for every owned worker as closed or retained with its
next use/blocker; no separate cleanup request is needed for completed owned work.

## Hand off to another machine

Before sending work to a pane on another machine or session, verify that the
target can do it:
- its OS and toolchain;
- the repository, branches and credentials;
- the required devices;
- every named pin, such as a machine profile, actually exists there (`herdr machine list`).

If any of these is missing, report it rather than sending the work anyway. Write
the scenario to a file and send a short slash command. A synced copy of the board is
a second orchestrator. Add a `Handed off to <machine/pane> at <time>` line to the local dispatch report before
the remote starts. If the handoff fails, add `Returned at <time>`.
