---
name: pr
description: "Create a PR for a workstream with current review and verification evidence."
disable-model-invocation: true
argument-hint: "<workstream-slug> — review and applicable verification must already pass"
---

# pr

First read: the runtime bootstrap (`../../bootstrap.md`), the host session part.

The optional last phase of the pipeline. Everything the description needs already
exists in the workstream's artifacts and configured board: the mandate and design in
`alignment.md`, decisions in `ledger.md`, stories in canonical tracker issues or
local Markdown tickets, and evidence in `reports/`. The PR description is assembled from them,
never written from memory. Every claim traces to the artefact that proves it: cite
that artefact in the report or ledger, and inline the figure in the PR body.

## Repository approval path

Follow the [approval path](../../runtime/board/approval-path.md).

## Steps

Before checking delivery evidence, apply [housekeeping](../housekeeping/SKILL.md)
to the workstream worktree if it has not covered the current task state. If it changes
tracked content, return that delta to review and refresh required delivery evidence
before proceeding. Preserve artifacts used by the PR's claims.

A workstream the user ran directly on `main` has no branch to publish: report the PR as
not applicable, with the review and E2E evidence, and stop.

1. **Confirm delivery evidence**, from the main checkout, using the selected
   [board's delivery gate](../../runtime/board/board.md). Remote issues are checked
   directly alongside Git and reports; no local ticket mirror or scheduler call.
   For local Markdown only, run
   `python3 "<plugin_root>/scripts/next_tickets.py" --repo "<repo>" <slug> --delivery`.
   Require implemented/integrated work and a clean review plus passed E2E for the
   current workstream head and pinned base. Read coverage, decisions and artifacts too;
   structural markers alone are not proof. Follow-ups are separate future work, not PR
   blockers or implied permission to implement them. Missing/stale evidence means
   return to the corresponding phase, never call an implemented branch delivered.
   Run the configured [`delivery` checkpoint](../../runtime/decisions/checkpoint-command.md)
   for follow-up attention after checking the evidence, or record an evidenced skip
   when no discretionary choice remains. Check this phase record at delivery.
   Required gates and publication permission are facts the backend cannot waive.
2. **Read the whole range**, not the last commit: `git log --oneline main..feat/<slug>`
   and `git diff main...feat/<slug>`. Use the accepted brief to explain intent and
   the final diff to explain resulting behavior, and list the ADRs the workstream adds. Reuse the current review report;
   preparing the description does not start another review or testing loop.
3. **Write the description** following [PR writing standard](../../standards/pr.md) into
   `.scratch/<slug>/pr.md`, run its self-check, and show it to the user. Carry forward existing
   description/publication approval; obtain only a still-required decision.
   An unrelated reply or silence is not approval.
4. **Push and open.** Record the [approval path](../../runtime/board/approval-path.md). When publication is authorized, run
   `git push -u origin feat/<slug>` directly. Verify the remote branch matches the
   reviewed local tip, then run
   `gh pr create --base main --head feat/<slug> --title "<type>: <goal in one line>" --body-file .scratch/<slug>/pr.md`.
   The type follows the repo's commit format (`feat`, `fix`, `refactor`, …), chosen
   from the accepted goal. When that format uses `!`, add it for a breaking change
   established by the accepted compatibility obligations and final diff. Report the URL.
5. **Watch the required checks** for the pushed SHA, as [watch the required checks](../../standards/pr.md#watch-the-required-checks)
   describes.
6. **After the merge, on request:** apply the
   [worktree retirement checks](../../runtime/resources/retire-worktree.md), then
   delete `feat/<slug>` and its recorded ticket branches only after downstream
   dependencies no longer need them. Retain useful timing/retrospective evidence
   in the existing reports or journals.

## Description

[PR writing standard](../../standards/pr.md) owns the description: structure, title, numbers, what
to leave out, diagrams, length, refreshing an existing PR, and the self-check.
It also applies to every later edit of the PR, whether or not this skill was
invoked. Supply it the accepted brief or issue, the final change range, current
verification evidence, material decisions and known limitations. Numbers come
from recorded command output, not recollection. Reports in gitignored `.scratch/`
are local evidence, not reviewer links. Publish artifacts only with authorization.
