#!/usr/bin/env python3
"""lint_pr_body.py checks the mechanical rules of a PR description before it is published."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import support

SCRIPT = support.script('lint_pr_body.py')

GOOD = """## Summary
**Each build now reads one packaged value store.** Android APK shrinks by 1.5%.

## Results

| Metric | main | this PR | Δ |
|---|---:|---:|---:|
| APK bytes | 1,000 | 985 | −15 (−1.5%) |
| Heap KB | 0 | 0 | n/a |

## How it works

```text
lookup -> native store
```

## Merge risk
Consumers repin in lockstep. Rollback: revert this PR.
"""


class LintPrBody(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        self.env = dict(os.environ, PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')

    def lint(self, body, *args, code=0, env=None):
        path = self.root / 'pr.md'
        path.write_text(body)
        result = subprocess.run([sys.executable, str(SCRIPT), str(path), *args], capture_output=True, text=True,
                                env=env or self.env, timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_a_well_formed_body_passes(self):
        report = self.lint(GOOD, '--title', 'feat: each build reads one packaged value store')
        self.assertEqual((report['errors'], report['warnings']), ([], []))

    def test_fences_with_attributes_do_not_hide_later_prose(self):
        body = GOOD + '\n```python title="a.py"\nprint(1)\n```\n\nSee .scratch/workstream/reports/1.md.\n'
        self.assertTrue(self.lint(body, code=1)['errors'])

    def test_formatting_and_length_are_left_to_project_style(self):
        body = GOOD.replace('−15 (−1.5%)', '−15') + '\n## Test results\n' + '- suite passed\n' * 12 + 'word ' * 2000
        self.assertEqual(self.lint(body), {'errors': [], 'warnings': []})

    def test_code_samples_are_not_scanned_for_private_paths(self):
        body = GOOD + '\n```dockerfile\nWORKDIR /home/app\n```\n'
        self.assertEqual(self.lint(body)['errors'], [])

    def test_private_paths_and_links_are_errors(self):
        for leak in ('.scratch/workstream/reports/12.md', 'https://claude.ai/artifact/abc', '/Users/me/repo/file'):
            with self.subTest(leak=leak):
                report = self.lint(GOOD + f'\nSee {leak}.\n', code=1)
                self.assertTrue(any(leak.split('/')[0] in e or 'private' in e for e in report['errors']))

    def test_internal_identifiers_are_warnings(self):
        report = self.lint(GOOD + '\nPer D42 and FU-iOS-1, ticket 15 and F-07 stay open; review R-02.\n')
        joined = ' '.join(report['warnings'])
        for ident in ('D42', 'FU-iOS-1', 'ticket 15', 'F-07', 'R-02'):
            self.assertIn(ident, joined)

    def test_title_rules(self):
        for title in ('WIP: stuff', 'update things', 'feat: ' + 'x' * 80):
            with self.subTest(title=title):
                self.assertTrue(self.lint(GOOD, '--title', title, code=1)['errors'])
        self.assertEqual(self.lint(GOOD, '--title', 'fix(ios): keep plural forms at the active locale')['errors'], [])

    def test_published_body_must_equal_the_file(self):
        gh = self.root / 'bin/gh'
        gh.write_text(f"#!{sys.executable}\nimport json\nprint(json.dumps({{'body': 'older body'}}))\n")
        gh.chmod(0o755)
        report = self.lint(GOOD, '--published', '13', code=1)
        self.assertTrue(any('published' in e for e in report['errors']))


if __name__ == '__main__':
    unittest.main()
