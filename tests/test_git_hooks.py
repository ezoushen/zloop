#!/usr/bin/env python3
"""pre-commit: fast checks on each commit."""
import subprocess
import unittest

import support
from hook_case import IDENTITY, HookRepo
from test_doc_checker import GUIDE, git


class PreCommit(HookRepo):
    """Doc check on changed files and syntax of staged files; no test suite."""

    def test_a_commit_runs_the_doc_check_but_not_the_suite(self):
        first = self.commit()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(self.suite_runs(), [])
        (self.root / 'shared/guide.md').write_text(GUIDE + '\nSee [the old guide](old.md).\n')
        rejected = self.commit()
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('shared/guide.md:7: error link', rejected.stdout + rejected.stderr)
        (self.root / 'shared/guide.md').write_text(GUIDE + '\nBefore August 2025, use the old API.\n')
        warned = self.commit()
        self.assertEqual(warned.returncode, 0, warned.stdout + warned.stderr)
        self.assertIn('warning dated', warned.stdout + warned.stderr)

    def test_the_first_commit_checks_the_whole_tree(self):
        (self.root / 'shared/lost.md').write_text('# Lost\n')
        rejected = self.commit()
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('shared/lost.md:1: error orphan', rejected.stdout + rejected.stderr)

    def test_a_commit_judges_only_changed_docs_and_blocks_on_invalid_checker_input(self):
        (self.root / 'shared/big.md').write_text('# Big\n\n' + 'x' * 2100 + '\n')
        (self.root / 'README.md').write_text('# Project\n\nSee [tool](skills/tool/SKILL.md), [big](shared/big.md).\n')
        self.assertEqual(self.commit('--no-verify').returncode, 0)
        (self.root / 'shared/guide.md').write_text(GUIDE + '\nMore text.\n')
        unrelated = self.commit()
        self.assertEqual(unrelated.returncode, 0, unrelated.stdout + unrelated.stderr)
        (self.root / '.agents/skills/writing-agent-docs/config.json').write_text('{not json')
        broken = self.commit()
        self.assertNotEqual(broken.returncode, 0)
        self.assertIn('check_docs.py:', broken.stderr)

    def test_a_commit_with_a_python_or_shell_syntax_error_is_rejected(self):
        self.assertEqual(self.commit().returncode, 0)
        cases = {'scripts/bad.py': 'def broken(:\n', 'scripts/bad.sh': 'if then fi\n',
                 'scripts/tool': '#!/usr/bin/env bash\nif then fi\n'}
        for name, text in cases.items():
            with self.subTest(name=name):
                (self.root / name).write_text(text)
                rejected = self.commit()
                self.assertNotEqual(rejected.returncode, 0, name)
                self.assertIn(name, rejected.stdout + rejected.stderr)
                (self.root / name).unlink()
                git(self.root, 'add', '-A')

    def test_a_large_bash_script_without_an_extension_is_still_syntax_checked(self):
        # Reading only its first line must not break the pipe on a long file and skip the check under pipefail.
        self.assertEqual(self.commit().returncode, 0)
        name = 'scripts/long-tool'
        (self.root / name).write_text('#!/usr/bin/env bash\nif then fi\n' + '# padding\n' * 40000)
        rejected = self.commit()
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn(f'pre-commit: {name}: shell syntax error', rejected.stdout + rejected.stderr)

    def test_the_syntax_check_judges_the_staged_content_and_accepts_any_valid_python(self):
        self.assertEqual(self.commit().returncode, 0)
        bad = self.root / 'scripts/bad.py'
        bad.write_text('def broken(:\n')
        git(self.root, 'add', 'scripts/bad.py')
        bad.write_text('x = 1\n')  # fixed on disk only
        staged_broken = subprocess.run(['git', '-C', str(self.root), *IDENTITY, 'commit', '-qm', 'c'],
                                       capture_output=True, text=True)
        self.assertNotEqual(staged_broken.returncode, 0)
        self.assertIn('scripts/bad.py', staged_broken.stdout + staged_broken.stderr)
        git(self.root, 'add', 'scripts/bad.py')
        bad.write_text('def broken(:\n')  # broken on disk only
        staged_fine = subprocess.run(['git', '-C', str(self.root), *IDENTITY, 'commit', '-qm', 'c'],
                                     capture_output=True, text=True)
        self.assertEqual(staged_fine.returncode, 0, staged_fine.stdout + staged_fine.stderr)
        bad.write_text('x = 1\n')
        (self.root / 'scripts/bom.py').write_bytes(b'\xef\xbb\xbfx = 1\n')
        (self.root / 'scripts/latin.py').write_bytes(b'# -*- coding: latin-1 -*-\nNAME = "caf\xe9"\n')
        encoded = self.commit()
        self.assertEqual(encoded.returncode, 0, encoded.stdout + encoded.stderr)


class CommitMsg(HookRepo):
    """Rule (git's summary-line convention): a commit header is at most 72 characters, has a Conventional Commits
    type and no full stop; git's own and autosquash headers pass."""

    def setUp(self):
        super().setUp()
        hook = self.root / '.githooks/commit-msg'
        hook.write_text((support.ROOT / '.githooks/commit-msg').read_text())
        hook.chmod(0o755)
        (self.root / 'scripts/title_rules.py').write_text((support.ROOT / 'scripts/title_rules.py').read_text())
        git(self.root, 'add', '-A')
        self.assertEqual(self.say('feat: start the fixture', '--no-verify').returncode, 0)

    def say(self, header, *flags, editor=None):
        env = dict(self.env, GIT_EDITOR=editor) if editor else self.env
        return subprocess.run(['git', '-C', str(self.root), *IDENTITY, 'commit', '-q', '--allow-empty', *flags,
                               *(['-e', '-m', header] if editor else ['-m', header])],
                              capture_output=True, text=True, env=env)

    def test_a_header_breaking_a_rule_is_refused_naming_it_and_the_standard(self):
        for header, rule in (('feat: ' + 'x' * 67, 'header is 73 characters; keep it at most 72'),
                             ('update things', 'header has no type'),
                             ('feat: add the thing.', 'header ends with a full stop')):
            with self.subTest(header=header):
                refused = self.say(header)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn(f'commit-msg: the {rule}', refused.stderr)
                self.assertIn("git's summary-line convention", refused.stderr)

    def test_a_valid_header_and_the_headers_git_writes_pass(self):
        for header in ('feat: ' + 'x' * 66, 'fix(hooks): refuse a header without a type', 'feat!: drop the old flag',
                       'Merge branch \'main\' into feat/x', 'Revert "feat: add a thing"', 'fixup! feat: add a thing',
                       'squash! feat: add a thing'):
            with self.subTest(header=header):
                accepted = self.say(header)
                self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)


if __name__ == '__main__':
    unittest.main()
