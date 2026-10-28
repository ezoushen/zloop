#!/usr/bin/env python3
"""The ADR number check names duplicated numbers and the next free one, in a tree or at a Git ref."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import support

SCRIPT = support.script('adr_numbers.py')


def git(repo, *args):
    subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True)


def check(repo, *args):
    result = subprocess.run(['python3', str(SCRIPT), '--repo', str(repo), *args], capture_output=True, text=True)
    return result.returncode, json.loads(result.stdout) if result.stdout.strip() else None, result.stderr


class AdrNumbers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        git(self.repo, 'init', '-q', '-b', 'main')
        git(self.repo, 'config', 'user.email', 't@example.com')
        git(self.repo, 'config', 'user.name', 't')

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, *names, folder='docs/adr'):
        for name in names:
            path = self.repo / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# decision\n')

    def test_distinct_numbers_pass_and_name_the_next_free_number(self):
        self.write('0001-event-sourced-orders.md', '0002-postgres-write-model.md', 'template.md')
        code, out, _ = check(self.repo)
        self.assertEqual(0, code)
        self.assertEqual({}, out['duplicates'])
        self.assertEqual('0003', out['next'])

    def test_a_duplicated_number_fails_and_lists_each_file(self):
        self.write('0001-orders.md', '0002-billing.md', '0002-ticket-modes.md')
        code, out, _ = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual({'0002': ['0002-billing.md', '0002-ticket-modes.md']}, out['duplicates'])
        self.assertEqual('0003', out['next'])

    def test_an_absent_folder_passes_with_the_first_number(self):
        code, out, _ = check(self.repo)
        self.assertEqual(0, code)
        self.assertEqual('0001', out['next'])

    def test_a_project_location_is_checked_instead_of_the_default(self):
        self.write('0004-a.md', '0004-b.md', folder='docs/decisions')
        self.assertEqual(0, check(self.repo)[0])
        code, out, _ = check(self.repo, '--dir', 'docs/decisions')
        self.assertEqual(1, code)
        self.assertEqual(['0004'], list(out['duplicates']))
        self.assertEqual('0005', out['next'])

    def test_a_ref_is_read_from_git_not_the_working_tree(self):
        self.write('0001-a.md')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'one')
        git(self.repo, 'checkout', '-q', '-b', 'feat/x')
        self.write('0001-b.md')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'two')
        git(self.repo, 'checkout', '-q', 'main')
        self.assertEqual(0, check(self.repo)[0])
        code, out, _ = check(self.repo, '--ref', 'feat/x')
        self.assertEqual(1, code)
        self.assertEqual({'0001': ['0001-a.md', '0001-b.md']}, out['duplicates'])

    def commit(self, message):
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', message)

    def test_a_non_ascii_slug_at_a_ref_still_counts(self):
        self.write('0001-a.md', '0001-決策.md')
        self.commit('two')
        code, out, _ = check(self.repo, '--ref', 'HEAD')
        self.assertEqual(1, code)
        self.assertEqual({'0001': ['0001-a.md', '0001-決策.md']}, out['duplicates'])

    def test_a_missing_repository_is_invalid_input(self):
        code, out, _ = check(self.repo / 'missing')
        self.assertEqual(2, code)
        self.assertIsNone(out)

    def test_against_main_finds_a_number_main_claimed_after_the_branch_left(self):
        self.write('0001-a.md')
        self.commit('one')
        git(self.repo, 'checkout', '-q', '-b', 'feat/x')
        self.write('0002-ticket-modes.md')
        self.commit('workstream')
        git(self.repo, 'checkout', '-q', 'main')
        self.write('0002-billing.md')
        self.commit('main moved')
        self.assertEqual(0, check(self.repo, '--ref', 'feat/x')[0])
        code, out, _ = check(self.repo, '--ref', 'feat/x', '--against', 'main')
        self.assertEqual(1, code)
        self.assertEqual({'0002': ['0002-billing.md', '0002-ticket-modes.md']}, out['duplicates'])
        self.assertEqual('0003', out['next'])

    def test_against_main_ignores_the_files_both_sides_share(self):
        self.write('0001-a.md')
        self.commit('one')
        git(self.repo, 'checkout', '-q', '-b', 'feat/x')
        self.write('0002-b.md')
        self.commit('workstream')
        code, out, _ = check(self.repo, '--ref', 'feat/x', '--against', 'main')
        self.assertEqual(0, code)
        self.assertEqual('0003', out['next'])

    def test_against_follows_renames_and_deletions_as_a_merge_would(self):
        self.write('0001-a.md', '0002-billing.md', '0003-old-name.md')
        self.commit('base')
        git(self.repo, 'checkout', '-q', '-b', 'feat/x')
        git(self.repo, 'mv', 'docs/adr/0003-old-name.md', 'docs/adr/0003-new-name.md')
        self.write('0004-ticket-modes.md')
        self.commit('workstream renames and adds')
        git(self.repo, 'checkout', '-q', 'main')
        git(self.repo, 'mv', 'docs/adr/0002-billing.md', 'docs/adr/0002-billing-limits.md')
        git(self.repo, 'rm', '-q', 'docs/adr/0001-a.md')
        self.commit('main renames and deletes')
        code, out, _ = check(self.repo, '--ref', 'feat/x', '--against', 'main')
        self.assertEqual(0, code, out)
        self.assertEqual('0005', out['next'])

    def test_against_handles_a_criss_cross_history(self):
        self.write('0009-root.md')
        self.commit('root')
        git(self.repo, 'checkout', '-q', '-b', 'a')
        self.write('0001-old.md')
        self.commit('a')
        git(self.repo, 'checkout', '-q', 'main')
        git(self.repo, 'checkout', '-q', '-b', 'b')
        self.write('0002-other.md')
        self.commit('b')
        for branch, other in (('feat/x', 'b'), ('main', 'a')):
            git(self.repo, 'checkout', '-q', '-B', branch, 'a' if branch == 'feat/x' else 'b')
            git(self.repo, 'merge', '-q', '--no-edit', other)
        git(self.repo, 'checkout', '-q', 'feat/x')
        git(self.repo, 'mv', 'docs/adr/0001-old.md', 'docs/adr/0001-new.md')
        self.commit('workstream renames')
        code, out, _ = check(self.repo, '--ref', 'feat/x', '--against', 'main')
        self.assertEqual(0, code, out)
        self.assertEqual('0010', out['next'])

    def test_a_missing_ancestor_object_is_invalid_input(self):
        self.write('0001-a.md')
        self.commit('base')
        base = subprocess.run(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True).stdout.strip()
        git(self.repo, 'checkout', '-q', '-b', 'feat/x')
        self.write('0002-b.md')
        self.commit('workstream')
        git(self.repo, 'checkout', '-q', 'main')
        self.write('0003-c.md')
        self.commit('main')
        (self.repo / '.git/objects' / base[:2] / base[2:]).unlink()
        code, out, _ = check(self.repo, '--ref', 'feat/x', '--against', 'main')
        self.assertEqual(2, code)
        self.assertIsNone(out)

    def test_against_still_checks_when_the_merge_has_a_conflict(self):
        self.write('0001-a.md')
        self.commit('base')
        git(self.repo, 'checkout', '-q', '-b', 'feat/x')
        (self.repo / 'docs/adr/0001-a.md').write_text('# workstream wording\n')
        self.write('0002-workstream.md')
        self.commit('workstream')
        git(self.repo, 'checkout', '-q', 'main')
        (self.repo / 'docs/adr/0001-a.md').write_text('# main wording\n')
        self.write('0002-main.md')
        self.commit('main')
        code, out, _ = check(self.repo, '--ref', 'feat/x', '--against', 'main')
        self.assertEqual(1, code)
        self.assertEqual({'0002': ['0002-main.md', '0002-workstream.md']}, out['duplicates'])

    def test_an_unknown_ref_is_invalid_input(self):
        code, out, err = check(self.repo, '--ref', 'no-such-branch')
        self.assertEqual(2, code)
        self.assertIsNone(out)
        self.assertIn('no-such-branch', err)


if __name__ == '__main__':
    unittest.main()
