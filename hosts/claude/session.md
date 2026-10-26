# Claude Code adapter

Registration: `.claude-plugin/plugin.json`, native `agents/*.md` profiles, and
`hooks/hooks.json`. SessionStart selects this adapter explicitly. Invoke phase
skills with `/zloop:<phase>`.

Read shared docs with the Read tool, not `cat`, `sed` or `head`: Bash output over 30 KB is saved to a file, and a
guessed range can cut a section short. Each doc under `skills/`, `runtime/`, `standards/` and `hosts/` fits one read.

Panel: [the panel doc](../../runtime/workers/panel.md).

zloop does not block Git commands. The orchestrator can perform authorized
publication and cleanup directly; workers retain their agent's permissions.
Follow native permissions. On permission denial, report the actual boundary and
stop that action; do not route around it.
Carry the denied operation into worker and resume assignments so another path, copy or
tool does not repeat it. Keep startup update/auth/permission prompts pending; a review
assignment does not authorize upgrades or disabling approvals to make a delegate run.

Before launching, resuming or acting as a worker, also read [workers.md](workers.md): it holds
launch, profile and effort handling, the Claude hooks, child slots and goals. In worker
messages, `adapter` is this file's absolute path.

## Resume timer

An autopilot allowance pause schedules its resume with `CronCreate` with `recurring: false`, the cron pinned to the
reset's minute, hour, day and month, a few minutes after it; record the job id on the card. A scheduled prompt cannot
run a skill marked `disable-model-invocation`, so the prompt is plain text that names the card and the plugin's
autopilot skill file, by absolute path, and asks for its `--take-over --timed` procedure. The job fires only while the
session is idle. Claude restores it when the same session is resumed with `claude --resume` or `claude --continue`
before its time; it stops firing when the session exits, and a time that passed while it was closed is not caught up.
Cancel a leftover with `CronDelete`. The run card is the explicit request for scheduled work that
[workers.md](workers.md) asks for.
