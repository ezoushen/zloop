# dispatch — reference

Consult the relevant section when needed. Nothing here is an additional step.

Runtime lifecycle belongs in the host adapter or explicitly selected executor.

## Why the rules are what they are

- **Review the integrated effort.** One scoped review covers interactions between
  completed tickets without repeating a full review after every implementation.
- **Reports live in the main checkout.** Durable evidence must survive removal of
  disposable worktrees and their gitignored `.scratch/` directories.
- **A finding needs concrete evidence.** Use an executed repro or a traced failure
  path; the [diff-review](../review/jobs/diff-review.md) job defines evidence for scoped design and documentation issues.
- **Scope every review and verify the range first.** Supply the pinned base/head,
  assigned checkout and accepted scope to the reviewer;
  a tool's default range or working directory does not establish this target.
- **Waiting is not completion.** Follow the host's wait lifecycle while a build
  mutex or worker remains active; report success only after its result is verified.
  A missing user decision or resource grant follows
  [blocked continuation](../../runtime/workers/focus.md#blocked-continuation).
- **Required compile coverage.** Check the project's applicable compile gates;
  one task's success does not prove coverage of another module.
- **Check affected entry points together.** Trace a shared defect through its
  callers and contracts before consolidating findings, rather than repairing one
  symptom and rediscovering siblings in later rounds.
- **The report is the record of delivery.** Judge each criterion's evidence and
  verified commits, not checkbox counts alone.
- **Two efforts written `verifying` in one turn deadlock** on each other's devices.
  Check the selected board's device reservation before starting E2E.
- **Resume with surviving state.** Identify retained edits, reports, jobs and
  missing evidence so a worker can continue the actual assignment.

## Environment gotchas the tools do not confess

Repo-specific ones belong in the project loop file. Universal ones:

- Shell state does not survive between an agent's tool calls: export env in the
  same command as the build.
- `.scratch/` is gitignored, so a worktree has no local tickets, brief or reports.
  File inputs use absolute main-checkout paths; remote tickets use canonical
  IDs/URLs and current assignment context. Pass the canonical main checkout to
  helpers requiring `--repo`, including executor and review-target resolution;
  only local Markdown boards use the ticket scheduler.
- `git worktree remove` leaves the branch; a re-spawned ticket resumes it.
- `UP-TO-DATE` or `FROM-CACHE` is not a fresh execution. Reuse needs evidence for
  the relevant code, inputs and environment, as defined by the assigned job.
