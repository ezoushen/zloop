# Dispatch iteration

The iteration stage of [dispatch](SKILL.md); the [review handoff](handoff.md) follows it.
Goal: every selected ticket accepted and integrated, or blocked with the attempt that failed.

## One iteration

1. **Refresh.** Re-read the scoped board and tracker issues (bootstrap project conventions).
   Apply the user's changes to scope, criteria and priority before assigning work; materially
   changed active work returns to alignment. Record a principle the user states mid-dispatch verbatim
   ([authority rules](../../runtime/interview/authority.md)). Read pending receipts and attention items
   ([worker waits](../../runtime/workers/wait.md#wake-and-yield)) first; a design hold pauses dependent work
   until its ruling and updated criteria are recorded. Keep local runtime state apart from canonical issue state, and apply
   authorized tracker transitions at the project's actual milestones. Reconcile live tickets with the
   executor's status and on-disk reports.
   An absent agent is interrupted work, not success: resume its worktree or start a replacement with
   what survived; never discard edits to retry.
2. **Cross-effort edges.** The prerequisite effort must be done, with current review/E2E evidence, and
   its `feat/<dependency>` tip an ancestor of local `main`. Merging into main needs the user's
   authorization; carry an existing one forward instead of asking again. Before selection, merge that
   verified `main` into an existing effort worktree while no worker uses it, and verify the dependency tip
   is there. Keep dependency branches until dependents start. Never infer equivalence after squash or
   cherry-pick: re-cut the edge with explicit evidence. Never merge an unfinished effort, and never take
   merge permission from a backlog request.
3. **Select** candidates through the board operations; repair a scoped graph error or report an
   out-of-scope one. Admit only verified executor and account capacity, refreshing reservations before
   each launch. An empty candidate list is not completion: inspect the scoped execution counts. With live
   work, arm a notified wait for each worker, pass the [yield gate](../../runtime/workers/wait.md#wake-and-yield),
   then yield. With ready but unstartable work, identify the wait (area, capacity, dependency, integration);
   only when none can be resolved within scope, record the attempted resolution and block those tickets.
4. **Launch.** From `repo`, ensure the effort branch and its worktree exist: `feat/<slug>` in its effort
   worktree, or `main` when the user said to work [directly on main](modes.md#directly-on-main). A new effort
   branches from `main` after fetching and comparing it with its remote; a stale or diverged base is reported and the
   verified remote tip used (on `main`, ask the user). Apart from working directly on main, never commit to the user's local `main`.
   Choose [how a ticket runs](modes.md). An inline ticket skips only the ticket branch and worktree, their preparation, the builder launch with its launch decision record, and the merge; everything else in steps 4 and 5 applies to both modes.
   A delegated ticket gets a ticket branch and worktree from the effort branch (project naming, else
   `feat/<slug>-<key>` in `.worktrees/<slug>-<key>`, where the key is the local `NN` or a stable safe remote
   issue key from board operations); reuse existing ones after checking their identity, and merge the effort
   branch into a resumed one, keeping its local edits. When the user allows stacking, a dependent ticket may
   branch from an accepted but unintegrated ticket branch: record the stack in the run report and merge the
   base's later fixes into it before accepting it. Prepare fresh and recreated worktrees with the project's
   gitignored build inputs and verify the required paths, never printing credential-bearing files.
   Plan checks with [plan and accept checks](../../runtime/checks/accept.md); verify the runner's current
   contract and the worker's checkpoint capability, and carry its supported wait/status/log/cancel operations
   ([check execution](../../runtime/checks/run.md#run-a-bounded-check)). Create the reports directory, claim
   the ticket and record implementation started, then start a `builder` with:

   ```text
   adapter: <absolute active adapter>  plugin_root: <absolute>  repo: <absolute main checkout>
   agent_file: <plugin_root>/agents/builder.md  job_file: <plugin_root>/skills/dispatch/jobs/ticket.md
   ticket: <canonical ID/URL or absolute local ticket path>
   needs: <the ticket's needs list, [] when none; its own line, before ticket context>
   ticket context: <current issue content, criteria, dependencies, priority and revision>
   focus: <highest-priority outcome, next acceptance condition and deferred optional work>
   checks: <each check's criterion or project requirement and tier: focused and affected for this worker; full gate and E2E with phase and owner; reusable proof>
   timing: <expected duration with uncertainty; next observable milestone>
   checkpoint: <supported parent recipient/handle or bounded-return fallback>
   return: <assigned deliverable/checks accounted for, or shared failure/incomplete boundary>
   brief: <absolute alignment.md>  report: <absolute reports/key.md>
   evidence: <absolute main-checkout reports/evidence/key/attempt/head directory>
   worktree: <absolute>  branch: <assigned branch>  project instructions: <paths and applicable facts>
   skills: <situational skill paths or names, each with its fact>
   comments: <previous failing command/output, if any>
   ```

   Register every worker, probe, checker or E2E run in the run report, with its assignment, reservation and
   result, including ad-hoc ones the user asks for; status answers read that record. Record the agent ID
   through board operations and complete the [launch decision record](../../runtime/routing/report.md). If
   spawning fails, check whether a child started before retrying.
5. **Accept.** Run the board's acceptance checks (report structure, ancestry and
   [standing measurements](../../runtime/board/project-loop.md#standing-measurements), which refuse a missing or worse
   value unless a ledger-cited `allows` excuses it). Judge every criterion in `## Implemented`; later-phase gates
   stay with their owners and block delivery, not integration. Accept the worker's recorded checks at their commit; do not rerun them, apart from those measurements.
   Collecting through the worker lifecycle records the decision outcome; otherwise record it against the
   acceptance evidence or mark it unknown, then run the [completion record check](../../runtime/decisions/annotate.md#check-a-handoff);
   keep a capture gap visible; bookkeeping failure never justifies repeating implementation or inventing
   evidence. A failed return goes to recovery ([worker return](../../runtime/workers/return.md)), not to the
   user. Missing assigned implementation evidence → blocked, with the failure.
   Accepted → keep the [check evidence](../../runtime/checks/evidence.md) outside the worktree, merge the branch in the
   effort worktree, record `accepted` with the commit and the tracker transition. Not accepted → `declined` with the reason.
   Retire a worker only after an [acceptance decision](../../runtime/workers/collect.md#accept-and-record-outcomes); keep
   ticket worktrees through review, repair and E2E.
   Once an integrated build allows checks only the user can observe (`Verify (user)`), send the user one request covering all of them, [with context](../../runtime/ask/ask.md).
   On a merge conflict, assign a builder the `merge-conflict` job in the effort worktree (not on
   [main](modes.md#directly-on-main)); no other worker uses it until the orchestrator completes the merge.
6. **Unblock.** A blocked ticket is the orchestrator's work: [research](../research/SKILL.md) missing facts,
   diagnose defects under [diagnosis](../diagnose/SKILL.md), decide engineering choices within the constraints, or
   return design errors to align/refine. Apply [escalation checkpoints](../../runtime/workers/escalation.md) before a
   retry and report the retain or replacement decision. Before asking the user for a resource, check what is available and name what you checked when you
   [ask with context](../../runtime/ask/ask.md), only for missing context, product choices or real
   permission boundaries. Record failed commands verbatim. Resolved → ready. Replacing a criterion's `Verify`
   method with a weaker one is the user's decision. Two failed attempts at the same ticket → split it around one deterministic Verify per criterion; never an unchanged third attempt; the new tickets wait for the user's check.
   An agent's permission limit is not a human-only task. Separate missing authorization, access, capability
   and unresolved user choices; within the user's authorization and native permissions, do it, but a genuine
   operator-only restriction still applies. Verify target identities from the project's inventory and probe
   the relevant capability, not an unrelated privileged command. Record the blocker and continue independent work.
7. **Report** fresh scoped counts, implementation separately from review and E2E, the effort branch, its
   worktree and blocked tickets. Answer a direct user question first, from current recorded state, naming any
   pending check, before processing queued notifications. For a gate expected to take over 15 minutes, report
   its milestones and ETA from the start and keep reporting them; never go silent. Persist authorization and scope changes with the board updates, and
   carry unresolved selected work through follow-ups. Once implementation is complete, continue into the handoff.
