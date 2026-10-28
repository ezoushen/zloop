# Runtime bootstrap

The registered Claude/Codex SessionStart hook or Pi bootstrap extension supplies
`zloop host: claude|codex|pi`, the absolute plugin root, this bootstrap and the
active host's session part (`hosts/<host>/session.md`). Read this file and **only that
host's session part** once per session before using a skill. Resolve shared files relative to the plugin
root (two directories above this skill's `SKILL.md`), never the target repository.

If the hint is absent, an explicit `host=claude`, `host=codex`, `host=pi`
or `host=opencode` in the user's
invocation selects `hosts/<host>/session.md`. Do not guess from model names
or the presence of compatibility environment variables. Resolve the session part and
plugin root before workers start. Consume this selection before forwarding phase
arguments to command-line helpers. The bootstrap supplies context (Pi's also applies the
worker lifecycle's yield gate when a run settles); explicit
host selection is also sufficient. Native permissions still apply.

Shared docs are grouped by reader, each folder with a README that lists its files:
[workers](runtime/workers/README.md) (launch, wait for, collect and retire workers),
[routing](runtime/routing/README.md) (choose and report worker models),
[decisions](runtime/decisions/README.md) (decision checkpoints and journals),
[checks](runtime/checks/README.md) (plan and run checks), [config](runtime/config/README.md)
(configuration format) and `hosts/executors/` (one guide per executor type).
A link names the file to read; read it whole. Each doc in these folders fits one read, and
a `#anchor` names a section inside it.

Notation such as `/zloop:refine` identifies a skill, not a shell command. Use the
active adapter's invocation mechanism. Shared agents and jobs own behavior; host
adapters and selected executors own runtime operations.

Orchestrators and workers follow shared [assignment focus](runtime/workers/focus.md)
for priority, scope and concise updates.

`/zloop:setup` explains configuration and handles direct settings requests, only for user-requested changes;
workflow execution reads configuration without invoking that explicit-only command.

## Triggers

Each rule below fires outside its owner's document: when the trigger occurs, read the owner, whatever skill is running.

| When | Read |
|---|---|
| Before creating a PR, editing its title or body, or pushing to a PR branch, invoked as `/zloop:pr` or not | [PR writing guide](standards/pr.md); each such push or edit is a `delivery` boundary: run the `delivery` checkpoint or record an evidenced skip, then the [required-check watch](standards/pr.md#watch-the-required-checks) |
| Before mandate/alignment questions, including a request to "grill" the plan | [grilling](runtime/interview/grilling.md) and its [question filter](runtime/interview/question-filter.md): they own question depth and continuation; even exhaustive depth asks only principles and material forks |
| Explicit phase composition, such as refinement after research | [refinement procedure](skills/refine/SKILL.md#explicit-phase-composition) |
| Before each worker launch, including helper workers | [launch](runtime/workers/launch.md), [before launch](runtime/workers/before-launch.md), then the host's worker part, which its session part names; register the worker before its first prompt ([prepare](runtime/workers/prepare.md)) and arm its [wait](runtime/workers/wait.md) |
| Worker blockers and review/check failures, before the next attempt | [escalation checkpoints](runtime/workers/escalation.md); ordinary resumes reuse the saved choice |
| Before the first phase checkpoint, when `decisions.enabled` has a family | [phase checkpoints](runtime/decisions/checkpoint.md#phase-checkpoints) |
| `HERDR_ENV=1`, before cross-runtime dispatch, even when no hook supplied it | `hosts/herdr.md` |
| Choosing or updating a tracker, board or destination; project workflow | [tracker and project conventions](runtime/board/conventions.md), [board operations](runtime/board/board.md) |
| An autopilot run card with no terminal state (`.scratch/<slug>/autopilot.md`) that this conversation created or took over, on resume, after compacting, per phase | re-read it, its ledger, [autopilot](skills/autopilot/SKILL.md): an unconfirmed card starts no phase; a confirmed one asks nothing it did not reserve |
| After a compaction or resume | re-read this bootstrap and the host session part before the next worker launch or phase checkpoint; [long sessions](runtime/workers/long-sessions.md) |
| Writing or changing a source comment or API doc, outside a builder or reviewer job | [code comments standard](standards/code-comments.md) |
| A user correction to how you work | record it under the ledger's `## Standing directives` ([record and continue](runtime/interview/record.md)) or the host's project memory; a compaction summary is not a durable store |
