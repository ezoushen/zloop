# How a ticket runs

A step of the dispatch [iteration](iteration.md). The orchestrator chooses one of two
modes for each ticket before it starts:

- **Inline:** the orchestrator implements the ticket itself in the workstream worktree, on
  the workstream branch. No ticket branch, worktree or builder is created.
- **Delegated:** a builder implements it in its own worktree and branch, launched as the
  iteration describes.

When tickets have a worker route, launch workers through that route or record why it cannot be used
in the dispatch report, with the working next step. Without a worker route, default to inline for
sequential, well-understood tickets whose context the session already holds. Delegate tickets that can
run in parallel, need a clean context, carry high risk or would crowd the orchestrator's
own context. Model and thinking for a builder still come from routing. The user's
instruction on how a ticket runs wins. Record the mode and its reason in the dispatch report.

## Inline

The orchestrator follows the [ticket job](jobs/ticket.md) itself: its inputs, finish line
and return bind it as they bind a builder. Set `worker=inline` on a local ticket as it starts
(`--set <ticket> status=implementing worker=inline`), write
`base:` and `branch: <workstream branch>` as the first lines of the ticket report, then work
test-first and run the assigned checks with their evidence before appending
`## Implemented`. Commit on the workstream branch; there is nothing to land and no ticket
worktree to retire.

Accept it with the same structural checks as a delegated ticket (`--accept` on a local
board reads the recorded branch), then judge each criterion. The orchestrator wrote this
work, so its acceptance is a self-check: the integrated review is its independent check.
Commit inline work before merging a delegated ticket into the workstream worktree, so the two
never write there at once. A blocked inline ticket follows the same escalation and
two-attempt rules; delegating it is one of the recovery choices.

## Directly on main

Every workstream gets a dedicated branch by default. Only an explicit user instruction to
work directly on main makes `main` the workstream branch and the main checkout its worktree;
record that instruction and the starting commit in the dispatch report. A stale or diverged
local `main` is reported and the user asked before the first commit. Inline tickets commit
there, and delegated tickets land on `main` by rebase and fast-forward under that instruction, which is the integration
authorization. A conflict is resolved in the ticket worktree by the `merge-conflict` job; a builder never works on `main`. Never push. The integrated review covers the starting
commit to the current `main` (pass `--base <starting commit>` to the review target), its
report records `workstream_branch: main` so the delivery gate reads
`main`, and PR publication is reported as not applicable; its delivery report lists the candidate ADRs
for the user.
