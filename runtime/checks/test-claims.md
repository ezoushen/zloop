# Record a test claim

Each code criterion in a managed local ticket declares a distinct test ID on its
`Test claim:` line. Keep the verification instructions on `Verify (code):`.
Use a single printable line for the ID. Run both phases from the assigned
worktree root with the same worker state and attempt.

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
bytes there to the commit. The declared test file or the matching unittest
module may differ. Other tracked and Git-visible untracked paths must match
the base. Keep the logs outside the worktree. A valid red preserves exit 1 and
adds a `tdd` receipt to the summary.

After committing the fix, use `--phase green` and a fresh log. Green requires the
same command arguments and output format, and a passing test on a worktree and
index matching the checked-out commit. The named unittest method must be
defined directly in the matching test file. The pair binds the module's syntax
tree, including the claimed method and same-file fixtures, constants and
helpers. Other methods starting with `test` are recorded by class and method name.
Green may add these methods and plain test-only classes with a simple named base.
Earlier methods and class declarations must retain their definitions and positions
relative to each other and to the fixed module and fixture code.
A marker claim must retain the exact
declared test file. Keep both logs and summaries. Acceptance requires the
matching red to finish before green starts and green to identify the exact
accepted head. Record older receipts again if they lack these identities or
identify only a unittest method.
