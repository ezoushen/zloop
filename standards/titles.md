# Titles

Every title zloop writes (a ticket title, a commit subject, a PR title, and so the branch
named after a ticket) follows git's summary-line convention. This file is the only place
that defines it; other docs link here and restate none of it. In what an agent writes, a project's own
documented title or commit convention takes precedence, as its branch convention does; the scripts that refuse titles
check these rules wherever they run.

- **Imperative mood.** The title completes "If applied, this change will …": "Show a
  running worker as running", not "Shows" or "Showing".
- **Short.** Aim for 50 characters; 72 is the hard limit for a commit or PR title. A ticket
  title is at most 50, since a ticket's branch and worktree are named after it; `--new` refuses a longer one.
- **No full stop** at the end.
- **No leading article** in a ticket title: lead with the verb.
- **English words** in a ticket title, since they name its branch.
- **Lowercase after the type.** A commit or PR title opens with a Conventional Commits
  `type[(scope)][!]: ` prefix and continues in lowercase. A ticket title carries no prefix
  and starts with a capital.

Scripts refuse part of it, each refusal naming the broken rule: `next_tickets.py --new` a ticket
title's length, full stop, leading article or missing English word; `lint_pr_body.py --title` and the
`commit-msg` hook a PR title's or commit header's length, missing type or full stop. Capitals, a prefix
on a ticket title and mood are left to review; a verb detector would flag valid verbs such as "embed" or "speed".

## Sources

- [git: SubmittingPatches](https://git-scm.com/docs/SubmittingPatches), the imperative
  mood and the short summary line.
- [How to write a git commit message](https://cbea.ms/git-commit/), the 50 and 72
  character limits and the "If applied" test; its capitalized subject is not adopted,
  because the prefix is lowercase in git's own `area: ` form and in Conventional Commits.
- [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/), the
  `type[(scope)]: ` prefix.

Why a named standard: a model already knows git's convention, so naming it works better
than a rule list of our own; zloop adopts the part that fits titles and drops the rest.
