# Record a test claim

Record receipts for registered managed local ticket attempts. Each code criterion
declares a distinct printable test ID on its `Test claim:` line. Keep verification
instructions on `Verify (code):`. Run both phases from the assigned worktree root
with the same worker state and attempt. Inline work follows ordinary test-first
evidence because it has no registered attempt.

By default, the project's check or adapter emits the exact line `FAIL: <id>`
for the claimed assertion failure and exits 1. It emits `PASS: <id>` for a
passing claim and exits 0. A wrapper must report the actual test outcome and
preserve its exit status. Errors, skips and mentions in diagnostic text do not
count as results. Declare the test's repo-relative path on a `Test file:` line
under that criterion. Use a directory named `test`, `tests`, `__tests__`,
`spec` or `specs`, or a filename with a `test_` prefix or a stem ending in
`_test`, `.test` or `.spec`. A fully qualified Python unittest ID can use
`--format unittest` with verbose output and omit the path when the ID
identifies the test module.

```sh
python3 "<plugin_root>/scripts/run_check.py" \
  --timeout 30 --log "<retained-evidence>/claimed-red.log" \
  --state "<workstream>/reports/workers.sqlite" --attempt "<attempt>" \
  --phase red --claim "value follows the specification" -- \
  sh tests/value-check.sh
```

Red fixes the checkout at the assigned base and compares the index and raw file
bytes there to the commit. Regular files in recognized test locations may change.
Other tracked and Git-visible untracked paths must match the base. Keep
the logs outside the worktree. A valid red preserves exit 1 and adds a `tdd`
receipt to the summary.

After committing the fix, use `--phase green` and a fresh log. Green requires the
same command arguments and output format, and a passing test on a worktree and
index matching the checked-out commit. Both formats bind the exact claimed
test file and the Git-visible files in recognized test locations at red. Put
independent additions in a new test file or record a fresh red with the final
test and fixture files on the assigned base. Review inputs outside the snapshot,
including ignored untracked fixtures and files outside recognized test locations.
Keep both logs and summaries. Acceptance requires the matching red to finish
before green starts and green to identify the exact accepted head. Record older
receipts again if they lack this file snapshot.
