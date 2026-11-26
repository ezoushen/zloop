# Record a test claim

Each code criterion in a managed local ticket declares a distinct fully qualified
unittest test ID on its `Test claim:` line. Keep the verification instructions on
`Verify (code):`. Run the claimed test with verbose unittest output from the
assigned worktree root. Use the same worker state and attempt for both phases.

```sh
python3 "<plugin_root>/scripts/run_check.py" \
  --timeout 30 --log "<retained-evidence>/claimed-red.log" \
  --state "<workstream>/reports/workers.sqlite" --attempt "<attempt>" \
  --phase red --claim test_value.ValueTests.test_value -- \
  python3 -B -m unittest -v test_value.ValueTests.test_value
```

Red requires the assigned base commit and matching tracked bytes and index, with
no Git-visible untracked files. Only the claimed module may differ. Its filename
starts with `test_` or ends with `_test.py`. Keep the logs outside the worktree.
The named test must produce an assertion failure. A valid red preserves exit 1
and adds a `tdd` receipt to the summary. Other outcomes lack that receipt.

After committing the fix, run with `--phase green` and a fresh log. Green requires
the same test to pass on a worktree and index matching the checked-out commit.
Keep both logs and both summaries. Acceptance requires red to finish before green
starts and green to identify the exact accepted head.
