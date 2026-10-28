# Decisions

The binding decisions of this repository, newest last. Format, statuses and supersession:
[ADRs](../../runtime/domain/adr.md); this index: [ADR index](../../runtime/domain/adr-index.md).

## Accepted
- [0001](0001-adr-numbers-are-checked-on-main.md) ADR numbers are re-checked on main when an effort lands: number on the branch, re-check against the fetched `main` before review, renumber on a collision.
- [0002](0002-grilling-asks-principles-only.md) Grilling never asks the user about implementation detail: principles and material forks only, at every depth; the agent shows material choices for veto.
- [0003](0003-adrs-record-what-others-follow.md) An ADR records a decision others must follow, or one that passes the three tests: bare rules go to the rules file.
- [0004](0004-adrs-are-superseded-never-rewritten.md) ADRs are superseded, never rewritten: the old ADR's status line is its only edit; a partial overrule supersedes the whole.
- [0005](0005-the-adr-index-is-the-current-state.md) The ADR index is the current-state view: updated in the same commit, checked with `--index`, read first.
- [0006](0006-adr-statuses-have-no-proposed.md) ADR statuses have no proposed: accepted, superseded by ADR-NNNN or deprecated; the user's yes accepts.
- [0007](0007-builders-work-test-first-from-our-own-standard.md) Builders work test-first from our own standard, and tests protect business rules: one rule at a time, each test names its business rule and fails when that rule breaks; coverage means rules, not lines.
- [0008](0008-builders-refactor-after-green-within-the-ticket.md) Builders refactor after green, within the ticket: the ticket's own code, within its scope; a wider refactor goes to review.
- [0009](0009-the-framework-depends-on-no-external-skill-package.md) The framework depends on no external skill package: no manifest dependency, its own procedures under its own names, guarded by a repository-wide test.
- [0010](0010-autopilot-merges-only-through-the-normal-path.md) Autopilot merges only through the repository's normal path: only with `--auto-merge`, once required checks and the approval path are satisfied, never by admin override or self-approval.
- [0011](0011-the-engineering-standard-is-extended-never-overridden.md) The engineering standard is extended, never overridden: docs that link it add rules for their own decisions and never relax a principle.
- [0012](0012-an-autopilot-resume-timer-is-the-users-own-take-over.md) An autopilot resume timer is the user's own take-over: authorized on the run card, it runs `--take-over --timed` and resumes only a card still waiting on it; a pause or take-over cancels it.

## Superseded

## Deprecated
