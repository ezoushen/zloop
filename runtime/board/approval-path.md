# Repository approval path

Read the project's current branch requirements, PR author identity and established
approval convention before publication. Record the requirements and known path
in the existing delivery report: an eligible independent reviewer, an already configured bot/App
author, or existing explicit user authorization for an administrative merge when
the repository permits it. An author cannot satisfy their own independent-review
requirement. Do not invent a bot, change branch rules or treat an impossible
self-review as live work that more waiting will resolve.

PR publication does not authorize merging. A merge gate the repository declares for a human is never met by the agent. Carry forward a user's already explicit
merge/admin-merge authorization when applicable, without asking again; it never
waives required verification. Without an authorized path, name the exact approval
blocker for merging and its resume event. Pending approval does not block authorized
PR creation. Continue independent authorized work instead of polling an unchanged
requirement. Recheck the path if author or branch rules change.

## Automated merge

Before recording a user-authorized auto-merge grant, verify the open PR with
`python3 "<plugin_root>/scripts/pr_safety.py" grant --pr <number>`. Before the
PR exists, use `grant --base <planned-base>` with the explicit publication
target. Run from the target checkout. The script reads classic protection and
active rules for the exact GitHub base in the checkout's origin repository.

Keep the approval and required-check watch steps. After they pass, submit an
authorized merge with `python3 "<plugin_root>/scripts/pr_safety.py" merge --pr <number>
--head <reviewed-sha> --method <approved-method>`. It rechecks required settings
on the PR's actual base and submits through GitHub with the reviewed head pinned.
GitHub still enforces checks, approvals and queue rules. A successful command can
mean queue admission; verify the PR and its base and head before recording a merge.

A `waiting-on-user` exit identifies absent or unverifiable requirements. Preserve
the grant and delivery evidence and report the specific blocker. A repository
maintainer can make the base enforce a named check, then the user can retry.
