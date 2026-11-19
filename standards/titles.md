# Titles

Use git's summary-line convention for ticket, commit and PR titles. A project's
own documented convention takes precedence in what the agent writes.

Choose an imperative verb and a useful description of the change. A title should
complete “If applied, this change will …”. Keep ticket titles capitalized without
a type prefix; use lowercase after the Conventional Commits type for commits and
PRs. Review meaning and capitalization; a verb detector cannot judge them reliably.

Inspect deterministic constraints before publishing:

```sh
python3 <plugin_root>/scripts/title_rules.py --ticket '<ticket title>'
python3 <plugin_root>/scripts/title_rules.py --commit '<commit or PR title>'
```

Ticket creation, PR linting and the commit-msg hook use the same checks. Their
refusals name the broken constraint; do not copy that rule list into prompts.

## Sources

- [git: SubmittingPatches](https://git-scm.com/docs/SubmittingPatches)
- [How to write a git commit message](https://cbea.ms/git-commit/)
- [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)
