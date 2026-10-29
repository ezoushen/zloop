# Skill executor contract

Resolve `executor_config.skill` through the actual caller's installed skill catalog
or documented skill loader. A copy installed for another host does not establish
availability here. Check the selected skill's caller support and required tools,
including for a shared `herdr` route; a profile name never changes the caller.
Use the exact selected skill; do not guess a filesystem
path, install a substitute or invoke a similarly named sub-agent. Skill invocation
rules still apply: reading instructions is not a workaround for an explicit-only
invocation restriction. Report an unavailable or non-composable skill as a blocker.
The orchestrator may recover through another allowed candidate under the shared
recovery policy; this executor does not
choose replacement models or bypass invocation restrictions.

Provide `executor_config.instruction` followed by the current shared assignment
and resolved model policy/settings. Keep scope, worktree, pinned revision, agent
and job files, permissions and acceptance criteria in the assignment. The configured
instruction describes how to delegate; it does not replace the agent or job file.

Before the first launch, read the selected skill's applicable preflight, launch,
completion, recovery and cleanup instructions, including their referenced procedures.
An opening excerpt or CLI help alone does not establish the lifecycle contract.
Establish that it can launch the requested worker, honor its access limits and
collect its complete result. Read the destination's settings guidance as needed.
Use the skill's supported delegation entrypoint for the supplied assignment;
zloop retains planning, review and acceptance gates. If the skill requires an
incompatible orchestration loop, treat it as non-composable rather than bypassing
its rules or nesting a second workflow.
`inherit` must preserve that destination's model/thinking configuration; merely
omitting flags is insufficient if the bridge discards it. `override` must map
`settings.model` and `settings.effort` to real controls for the final worker.
Do not approximate a thinking label with a prompt or select a replacement model.
If the skill cannot fulfill the assignment or model policy, report the exact gap.
