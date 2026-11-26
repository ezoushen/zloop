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

<!-- rule: board.relayed-card-delivers-pr-only -->
Require an explicit user grant verified by the human intent gate on a managed
run. A card marked `relayed(...)` delivers PR-only and cannot grant or use auto-merge. Before recording a grant, verify that the PR's base in origin enforces
required checks. Before the PR exists, verify the explicit planned base. Submit an
automated merge through the normal GitHub path via the authenticated local T3
host after the check watch and approvals pass, with the reviewed head pinned and
without an admin override.

Use [managed run control](../workers/control.md) to record and submit the grant.
`pr_safety.py grant` can preview required checks in the target checkout. It
records no authority. Legacy and standalone merge requests report `waiting-on-user`.

On `waiting-on-user`, preserve delivery evidence and name the requirement. If the
grant was consumed, verify the submission outcome before requesting another grant. If necessary, the maintainer can configure a required
base check before the user retries.
