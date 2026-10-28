# Split oversized work

A step of [dispatch](SKILL.md) setup and its [iteration](iteration.md). The board's
[overall scope](../../runtime/board/scope.md) rules own inheritance and parent completion.

Selected work too large for one worker is split by the board's
[slicing rules](../../runtime/board/slicing.md), within native permissions and
existing tracker-write authorization, in the project's ticket convention.

Each child meets the bar a refined ticket meets: a one-sentence story; observable
Given/When/Then criteria, each with a `Verify` line naming the command or test and who
observes it (`code`, `agent` or `user`), and a check that cannot pass while the
criterion is broken and that normal variation cannot swamp. An edge the parent's brief
does not settle is a ledger row with a hinge. Run the configured
[`refinement` checkpoint](../../runtime/decisions/checkpoint-command.md) on the cut.
A child is ready when a builder with an empty context can build it from these, the
parent's brief and the project instructions. On a local Markdown board each child
carries the front matter a refined ticket carries — `ticket`, `status`, `blocked_by`
(always present, `[]` when nothing gates it), `touches`, `devices`, `model` and
`worker` — and the split ends with
`python3 "<plugin_root>/scripts/next_tickets.py" --repo "<repo>" <slug> --check`.
Reuse existing child issues and dependencies; dispatch ready children rather than
refining an already executable breakdown again. New children wait for the user's check like any new cut.
