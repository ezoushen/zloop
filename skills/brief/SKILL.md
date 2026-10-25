---
name: brief
description: "Brief the user on current progress, remaining work, verification and decisions for the active task or a named effort, branch or PR, or show the pipeline map of which command to use."
disable-model-invocation: true
argument-hint: "[map | <effort|branch|PR-number|PR-URL>] [<focus or question>] — defaults to the active task"
---

# brief

Contents:
- [Establish the current snapshot](#establish-the-current-snapshot)
- [Brief the human](#brief-the-human)
- [Present it](#present-it)
- [Preserve the workflow](#preserve-the-workflow)
- [Show the pipeline map](#show-the-pipeline-map)

First read: the runtime bootstrap (`../../bootstrap.md`), the host session part.

This is a manually requested, read-only briefing, not another workflow phase.
Use the active task and its accepted scope unless the user names a different
target. With `map` as the whole target, follow [show the pipeline map](#show-the-pipeline-map)
instead; an effort named `map` is named by its path, `.scratch/map`. No zloop board
or report is required. If target identity is ambiguous, state what can be established
and ask only for the missing selection; offer the map when no work is in progress.

## Establish the current snapshot

Read the accepted goal, latest user direction and relevant task state. Refresh
only the evidence needed for this briefing: scoped board/tracker entries, Git
branch/head and working changes, existing implementation/review/E2E reports, and
the assigned workers' current state through the active runtime's read-only tools.
Use canonical tracker records rather than inventing a local mirror. Worktree-local
changes and reports may be newer than the main checkout; check their identity.

Use the current conversation for intent and decisions, not as proof that a worker
finished or a check passed. Compare report revisions with the target. Label stale,
unavailable or conflicting evidence and give its last known time/revision when
available. A completed command, idle worker or old green report alone does not
establish completed work. Do not wait for workers to finish just to give a briefing.

## Brief the human

Tell it in layers, as [explain](../explain/SKILL.md) says: the
headline is the goal and the most consequential current fact. Then cover:

- What is complete, with its meaningful result.
- What is running now, what comes next and what remains outside this batch.
- Review and verification status, separating passed checks from gaps or failures.
- Blockers and decisions that actually need the user, with a recommendation and
  consequence. Say when no user decision is needed.

Use the project's domain language. A small status table or flow can connect completed/current/next
work; skip it when a sentence is clearer. Link the few artifacts that support the
important claims. Give counts only for a verified scope; no invented percentages,
ETAs or adoption claims. Separate local implementation, integration, verification
and publication. Keep unrelated follow-ups separate from current blockers.

If a previous briefing is available, emphasize the meaningful changes since it;
still make this briefing understandable on its own. Otherwise report current state
without inventing a baseline. Match an explicitly requested depth or focus.

## Present it

Read [explain](../explain/SKILL.md) and use it to present this snapshot in
the same response. Select the smallest view that clarifies current state or
dependencies; plain prose is sufficient for a simple status. No second command is
needed. Default to in-chat presentation; exports or file creation require the user's
request.

## Preserve the workflow

A briefing does not launch workers, repair defects, run verification, update a
tracker, alter scheduling or close panes. Do not invoke dispatch, review, PR or
housekeeping as a side effect. Inspect existing worker state within the selected
executor's access boundaries; unavailable monitoring stays an explicit limitation.

Present the briefing in the conversation. Do not append it to README, project
instructions, skills or a recurring status document. Save/export only when asked,
following the [documentation standard's history policy](../../standards/documentation.md).
Existing authorized work retains its scope and ownership: after an in-flight
briefing, resume it unless the user pauses or redirects it. A standalone briefing
does not resurrect an inactive task.

## Show the pipeline map

Read the [pipeline map](pipeline.md) and explain which command fits the user's
current task, what each command owns and what it hands on. Lead with the phase that
matches the conversation and the next command to run; give the whole map only when
asked. Present it the same way, in the conversation.

