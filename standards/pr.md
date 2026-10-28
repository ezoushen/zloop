# Writing and refreshing a PR description

Contents:
- [Structure](#structure)
- [Title](#title)
- [Numbers](#numbers)
- [Leave out](#leave-out)
- [Diagrams](#diagrams)
- [Length](#length)
- [Refreshing an existing PR](#refreshing-an-existing-pr)
- [Self-check before every publish](#self-check-before-every-publish)
- [Watch the required checks](#watch-the-required-checks)

Read this before writing a PR description and before every later edit or push to a
PR branch, whether or not `/zloop:pr` was invoked. It applies to free-text
requests such as "update the PR description".

A good description lets a reviewer who has never seen the workstream answer four
questions from the first screen:
- what changed for users and consumers;
- why;
- how much (the headline numbers);
- what could break.

Everything else supports those answers.

## Structure

Follow the repository's PR template and any PR style the user or project has
stated (sections, tables, formatting, length). Otherwise:

```markdown
## Summary
<2–4 sentences. Lead with the resulting behavior, in bold. Name who is affected and the headline number.>

## Why
<The problem and intended outcome from the accepted brief or issue. Don't infer a prior bug from the final behavior.>

## Results            ← only when the change makes a quantitative claim
<one comparison table, a method line, and footnotes>

## How it works
<Approach and material tradeoffs. A diagram for each mechanism a reviewer can't infer. Where to start reading.>

## Merge risk
<Consumers, migration, lockstep repins and rollback, or "unknown: <what>".>

## Scope
<In and Out. Each Out item traces to a decision; an agent's own choice is labelled as such.>
```

A small change needs only Summary, Why and one line of evidence. For
non-quantitative changes, evidence is the behavior that was red before and green
after, named concretely. If no pre-change baseline was run, say so; never invent
one.

## Title

Use `<type>[(scope)]: <resulting behavior>`, at most 72 characters, in the
repository's commit format, with `!` for a breaking change. It describes the
current merged scope and stands alone in `git log`. Never use `WIP` or internal
IDs. On every refresh, compare the title with the Summary and rename it in the same
step when the scope moved. A title edit is covered by authorization to edit the
description.

## Numbers

1. **Compare against the PR's base.** The baseline is the target branch (usually
   `main`) at the merge base, not an intermediate commit, a ticket step or a sibling
   PR. Add an intermediate column only when the reader needs it, and never in place
   of the base column.
2. **Show each metric's base, head and change together,** so a reader can see the
   size of each change without computing it. Keep the absolute values.
3. **Measure every row at one head, against one base, with one method.** Name the
   short head SHA. Reuse a recorded number only when its revision is the final head.
   Otherwise re-measure it or label it `at <sha>`.
4. **Add a method line:** units, run count and aggregation, device or environment,
   and what differs between the two arms. State run-to-run noise, and don't present
   a delta inside the noise as a result.
5. **Explain any delta that moves against the goal or would surprise a reader,** in
   a footnote on that row; for example, one component grows while the total
   shrinks.
6. **Cover every platform and dimension the evidence has,** or say why one is
   omitted.

## Leave out

- Verification output the PR already shows elsewhere. State what proves the change.
- Process history: repair rounds, review rounds, which worker or model did what.
- Internal identifiers a reviewer can't resolve: ledger rows (`D12`), ticket
  numbers, finding codes (`R-02`, `F-07`, `FU-…`). Public issue and PR numbers,
  committed ADR numbers and commit SHAs are fine.
- Private or local links and paths: `.scratch/…`, `/Users/…`, claude.ai artifacts.
  Inline the essential numbers instead.
- A limitations paragraph that waives required verification. That returns to
  `review --autofix`.

## Diagrams

Add a diagram when the change introduces or alters a mechanism that the table
can't convey: a data flow, a precedence chain, a protocol sequence or a layout.
Use about one per mechanism, each followed by two or three bullets on what is
non-obvious. Confirm that each diagram renders where it is published before
publishing.

## Length

The first screen answers the four questions. Scale the rest to the change: put
details in reports and inline only the essentials.

## Refreshing an existing PR

1. `.scratch/<slug>/pr.md` is the source of truth. Back up the live body
   (`gh pr view <n> --json body`) once before the first overwrite.
2. Rewrite it as one coherent document from the final diff and current evidence.
   Never append a parallel section, a second baseline or an "Update:" block, and
   never leave a stale head SHA. Keep the existing structure unless the user asks
   for a change.
3. Re-measure or relabel every number for the current head (Numbers rule 3).
   Re-validate every diagram.
4. Check the title (Title).
5. Lint, then publish with `gh pr edit <n> --body-file .scratch/<slug>/pr.md`, then
   verify the live body equals the file (`--published` below).
6. A push or an edit is a delivery boundary. Run the `delivery` checkpoint, or
   record an evidenced skip, and [watch the required checks](#watch-the-required-checks).

## Self-check before every publish

Run the mechanical checks:

```bash
python3 "<plugin_root>/scripts/lint_pr_body.py" .scratch/<slug>/pr.md --title "<title>" [--published <n>]
```

The linter checks:
- private paths and links;
- internal identifiers;
- the title;
- that the live body equals the file.

Fix every error. Fix each warning, or say why it stays. Then check the judgment
rules the script can't:
- the diagrams render;
- the baseline is the PR base;
- surprising deltas are explained;
- every platform is covered;
- Out items trace to decisions;
- merge risk and rollback are stated;
- the Summary answers the four questions.

## Watch the required checks

Watch the required checks for the pushed SHA. Confirm the PR head equals that
SHA, then run `gh pr checks <n> --required --watch` as a background job. When the
repository reports no required checks, the watch returns at once: watch every
check with `gh pr checks <n> --watch` instead, and treat each as required. A
failing required check is not "delivered". Read the failed job's log and return through `recovery` to
`review --autofix`. The repair reproduces the full failing CI job locally before
pushing, and the watch restarts on the new SHA. Report "ready to merge" only when
every required check is green and the approval path is known. The same watch
follows every later push to the PR branch.
