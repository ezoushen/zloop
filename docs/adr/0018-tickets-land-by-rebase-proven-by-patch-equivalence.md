# Tickets land by rebase and fast-forward, proven by patch equivalence

While a ticket branch is local, an accepted ticket lands on its workstream branch by rebasing onto the workstream tip and fast-forwarding, so no merge commit comes from a ticket branch; once pushed it is never rewritten, and `main` still merges into a workstream. Because a rebase gives the commits new ids, the done check proves a ticket landed by comparing what each commit changes (`-U0` patch ids, read as bytes with the repository's diff attributes, converters and `GIT_DIFF_OPTS` set aside), not by ancestry; a commit whose change it cannot read refuses rather than passes.

## Considered Options

- Squash-merge one commit per ticket: rejected by the user in favour of keeping each ticket's commits.
- `merge-tree` "merging the ticket changes nothing": refuses a clean rebase past an adjacent-line edit.
- Patch ids with context: a clean rebase past a nearby edit changes them.

## Consequences

An identical line added elsewhere in the same file can read as landed; a rebased landing needs git 2.40 or newer
(`--attr-source`); attributes in `.git/info/attributes` still apply and can make a clean rebase refuse.
