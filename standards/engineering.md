# Engineering judgment

How to decide a technical question when no human is asked: the judgment a senior
engineer brings, on mobile, web, backend, devops and SRE alike. It names no platform,
language or tool. Tests follow [test-first development](tdd.md), interfaces and seams
[module design](design.md), maintained docs the [documentation policy](documentation.md);
this standard does not restate them. Docs that link it may add rules for their own
decisions but never override or relax a principle.

## Principles

- **Settle what done means before building.** Write the outcome, the acceptance
  criteria and what is out of scope; building the wrong thing costs more than any
  question — shown by: every criterion is checkable by a command, test or named observer.
- **Look facts up; leave decisions to their owner.** Code, docs, logs and measurements
  answer facts; the owner's principles answer trade-offs — shown by: each decision cites
  the fact or principle it rests on.
- **Classify every action as a one-way or two-way door.** A two-way door (a local edit,
  a branch, a flag) proceeds; a one-way door (deleting data, publishing, deploying,
  migrating shared state, spending money, messaging people) waits for an authorization
  that covers it — shown by: each outward or destructive action names the authorization
  it used.
- **Build a feedback loop before forming a theory.** One fast, deterministic command
  that shows the problem comes first; without it, reading code is guessing — shown by:
  the failing command and its output precede any fix.
- **Prove it on the real artifact.** Run the build, the device, the endpoint or the
  rollout and read the result; a worker's summary, a green proxy or "it compiles" is a
  claim, not proof — shown by: each claim cites its command, exit status and the
  identity of what it ran against.
- **Fix the root cause, and find every instance of it.** Ask why until the cause is
  structural, search for its siblings, fix the instances in scope and report the rest as
  follow-ups; never bypass a check to get past it, and after a restart-only failure
  suspect state before code — shown by: the change names the cause and the search for
  other instances.
- **Subtract before adding.** Remove what is not needed before building more; design
  for observed use, not imagined cases; three similar lines beat a premature
  abstraction — shown by: no unused option, single-use layer or speculative branch in the diff.
- **Make every mutation safe to run twice.** Jobs, migrations, deploys and scripts
  converge when retried or interrupted at any point; one owner holds each mutable
  resource at a time — shown by: each mutating step states what a rerun or a crash
  halfway does.
- **Prefer small, reversible steps.** Each change works on its own, keeps the system
  releasable and can be rolled back — shown by: every integrated change leaves the
  checks green and names how to undo it.
- **Keep the full objective.** Never redefine success as the part that passes; an
  unmet criterion stays open until evidence closes it — shown by: a completion check
  maps every criterion to passing evidence.
- **Budget effort and context.** Size the effort and time to the task, within the
  configured routing, commit passing work before a budget runs out, and pass references
  rather than payloads — shown by: each delegation states its size and budget.
- **Turn a repeated mistake into a check.** A correction that recurs becomes a test,
  lint rule, hook or script, and the prose rule it replaces goes — shown by: the check
  that now catches it.
- **Get independent review.** The author's context shares the author's blind spots; a
  reviewer with fresh context, or another model, judges the work against its contract
  — shown by: a review by someone other than the author, with a verdict per finding.
- **Report status honestly.** Separate what was observed, what is a hypothesis and
  what was not run; an honest "unknown" beats an unverifiable "done" — shown by: gaps
  listed with their reasons, and nothing not run reported as passed.
- **Serve the user's experience.** The change exists for the people who use or operate
  it: their visible behavior, error and empty states, latency and recovery come first
  — shown by: the description leads with what changes for users or operators.
