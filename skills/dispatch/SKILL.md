---
name: dispatch
description: "Implement scoped tickets and review the integrated changes; --autofix adds repairs and applicable E2E verification."
disable-model-invocation: true
argument-hint: "[<workstream|project|milestone|issue-ID|issue-URL|the plan agreed in this conversation>] [--autofix]"
---

# dispatch

Contents:
- [Setup](#setup)
- [State and continuation](#state-and-continuation)
- [One iteration](iteration.md)
- [Review handoff](handoff.md)
- [Reference](reference.md)

First read: the runtime bootstrap (`../../bootstrap.md`), the host session part.

One orchestrator owns scheduling, worktree integration and board updates.
Workers receive an agent file, a job file and only that job's inputs. A workstream
has a local slug for its branch, alignment and reports; tickets live in the
configured tracker or, only for local Markdown, `.scratch/<slug>/issues/`.
Read [board operations](../../runtime/board/board.md) and use the selected board throughout this skill.
Use [reference.md](reference.md) when a rule's rationale or a known failure mode
needs explanation.

Dispatch includes a read-only assessment after implementation, unless the
user explicitly requests implementation only. `--autofix` authorizes scoped review
repairs and applicable E2E verification; separate it from the board selector.
Carry forward existing explicit repair authorization, including an applicable
full-delivery request. Without that authorization, dispatch stops after read-only
review. Publication requires its own request.

## Setup

1. Resolve existing project instructions using [project loop](../../runtime/board/project-loop.md).
   Reuse established sources; no new setup document is required. Pass the relevant
   sources and uncovered facts in assignments. The project defines required
   compile, suite and E2E gates; do not invent extra suites for every ticket.
   Verify unfamiliar or changed check commands resolve the intended tool/version
   in the worker environment before distributing them. Reuse valid preflight evidence.
2. Resolve absolute `plugin_root` and `repo` (the main Git checkout). Call the
   maintained scripts in the plugin directly. Never copy them into the repo.
   Add `.worktrees/` and `.scratch/` to the project's ignore rules if absent.
3. Check the active adapter, selected executor tools and permissions. Verify assigned
   repository/worktree identities before starting workers. zloop does not
   require a Git denial probe or impose a Git-command blocklist.
   Reconcile the [shared capacity budget](../../runtime/resources/capacity.md)
   across known sessions/accounts before admitting the batch; local scheduler
   capacity is not an account-wide allocation.
4. Resolve and validate the selected board and invocation scope using board
   operations. Remote projects use native issue queries and dependencies directly;
   do not create local ticket files or call the local scheduler for them.
   With [a plan agreed in chat](../refine/chat-plan.md) and no tickets yet, cut them
   first; the iteration starts once the user has checked them. Tickets the user
   selected for dispatch are already checked.
5. On a local board, bind this session as the orchestrator before the first launch, and
   again when another session resumes the dispatch:
   `python3 "<plugin_root>/scripts/next_tickets.py" --repo "<repo>" [slug] --orchestrate`,
   adding `--autofix` when repairs are authorized. Run it through the host's shell: it
   records the Herdr pane and the host session ID there, and the stop hooks hold that
   session alone ([handoff](handoff.md)). With no slug it binds only workstreams with active
   work: a live workstream branch, or a ticket not done. Name the slug to bind any other, such
   as a workstream whose branch was deleted before review. It refuses, binding nothing, where
   it finds no pane or session.

A supplied scope is never widened, even to unblock it. Use the board's
[overall scope](../../runtime/board/scope.md) rules to preserve
backlog-wide requests across narrower batches, including selected parent/child work.
Selected work too large for one worker follows [split oversized work](split-work.md).
Consider other workstreams' live work for capacity, area and device conflicts without modifying them through
a scoped call. One orchestrator per main checkout; two independent
sessions must not schedule/mutate the same board concurrently.

## State and continuation

```
per ticket: ready → implementing → done                 any stage → blocked
```

These are execution milestones, not required tracker status names. Only the
orchestrator records them through the selected board operations. Here `done`
means implementation accepted and integrated. Review evidence belongs
to the review report, not the last implementation ticket. Reports always live in the main
checkout's `.scratch/<slug>/reports/`; worktrees do not contain that ignored state.

Implementation completion for the current batch: every selected ticket is done,
or blocked with the orchestrator's actual unblock attempt and failure recorded
in the issue or dispatch report; execution counts show `0 ready` and `0 live`. Name blocked tickets and
their reasons; do not call that full delivery. The adapter owns waiting and any
explicitly authorized goal. Quoting `/goal` is never a lifecycle operation.
This is the handoff point, not the end of dispatch: complete the [review handoff](handoff.md)
or record why it is excluded/blocked before reporting the invocation finished.

