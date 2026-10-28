#!/usr/bin/env python3
"""The ADR check holds the current-state index to the files and every supersede link to both ends."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import support

SCRIPT = support.script('adr_numbers.py')

INDEX = """# Decisions

## Accepted
{accepted}

## Superseded
{superseded}

## Deprecated
{deprecated}
"""


def check(repo, *args, index=True):
    flag = ['--index'] if index else []
    result = subprocess.run(['python3', str(SCRIPT), '--repo', str(repo), *flag, *args], capture_output=True, text=True)
    return result.returncode, json.loads(result.stdout) if result.stdout.strip() else None


class AdrIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        subprocess.run(['git', '-C', str(self.repo), 'init', '-q'], check=True)
        self.folder = self.repo / 'docs/adr'
        self.folder.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def adr(self, name, status=None, supersedes=None):
        head = ''
        if status or supersedes:
            head = '---\n' + (f'status: {status}\n' if status else '') + \
                   (f'supersedes: {supersedes}\n' if supersedes else '') + '---\n'
        (self.folder / name).write_text(head + '# A decision\n\nWe decided it because of a reason.\n')

    def index(self, accepted=(), superseded=(), deprecated=()):
        lines = lambda names: '\n'.join(f'- [{n[:4]}]({n}) A decision: the rule in one line' for n in names)
        (self.folder / 'README.md').write_text(INDEX.format(accepted=lines(accepted), superseded=lines(superseded),
                                                            deprecated=lines(deprecated)))

    def consistent(self):
        self.adr('0001-sync-api.md', status='superseded by ADR-0003')
        self.adr('0002-cents.md')
        self.adr('0003-async-api.md', status='accepted', supersedes='ADR-0001')
        self.adr('0004-old-cache.md', status='deprecated')
        self.index(accepted=['0002-cents.md', '0003-async-api.md'], superseded=['0001-sync-api.md'],
                   deprecated=['0004-old-cache.md'])

    def test_a_consistent_index_passes(self):
        self.consistent()
        code, out = check(self.repo)
        self.assertEqual(0, code, out)
        self.assertEqual([], out['index_problems'])

    def test_an_absent_folder_passes_even_when_an_index_is_required(self):
        code, out = check(self.repo, '--dir', 'docs/none')
        self.assertEqual(0, code, out)

    def test_an_accepted_adr_missing_from_the_index_fails(self):
        self.consistent()
        self.adr('0005-new-rule.md')
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['0005-new-rule.md: accepted but not listed under Accepted'], out['index_problems'])

    def test_an_index_entry_without_its_file_fails(self):
        self.consistent()
        (self.folder / '0002-cents.md').unlink()
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['0002-cents.md: listed in the index but no such file'], out['index_problems'])

    def test_a_superseded_adr_still_listed_as_accepted_fails(self):
        self.consistent()
        self.index(accepted=['0001-sync-api.md', '0002-cents.md', '0003-async-api.md'],
                   superseded=['0001-sync-api.md'], deprecated=['0004-old-cache.md'])
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['0001-sync-api.md: superseded by ADR-0003 but also listed under Accepted'],
                         out['index_problems'])

    def test_a_supersede_link_without_its_counterpart_fails_in_both_directions(self):
        self.consistent()
        self.adr('0003-async-api.md', status='accepted')
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['0001-sync-api.md: superseded by ADR-0003, which does not name it in supersedes'],
                         out['index_problems'])
        self.consistent()
        self.adr('0001-sync-api.md')
        self.index(accepted=['0001-sync-api.md', '0002-cents.md', '0003-async-api.md'],
                   deprecated=['0004-old-cache.md'])
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['0003-async-api.md: supersedes ADR-0001, which is not superseded by ADR-0003'],
                         out['index_problems'])

    def test_an_unknown_status_fails(self):
        self.consistent()
        self.adr('0002-cents.md', status='proposed')
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertIn('0002-cents.md: unknown status proposed', out['index_problems'])

    def test_a_missing_index_fails_with_the_index_check(self):
        self.adr('0001-a.md')
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['docs/adr/README.md: no index'], out['index_problems'])

    def test_without_the_index_check_a_projects_own_convention_passes(self):
        self.adr('0001-a.md', status='Proposed')
        self.adr('0002-b.md', status='superseded by ADR-0007')
        (self.folder / 'README.md').write_text('| ADR | Title |\n|---|---|\n| [1](0001-a.md) | A |\n')
        code, out = check(self.repo, index=False)
        self.assertEqual(0, code, out)
        self.assertEqual([], out['index_problems'])

    def test_relative_and_anchored_links_name_their_file(self):
        self.consistent()
        text = (self.folder / 'README.md').read_text()
        text = text.replace('](0002-cents.md)', '](./0002-cents.md)').replace('](0003-async-api.md)', '](0003-async-api.md#decision)')
        (self.folder / 'README.md').write_text(text + '- [9999](./9999-missing.md) gone\n')
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['9999-missing.md: listed in the index but no such file'], out['index_problems'])

    def test_fenced_examples_in_the_index_do_not_count(self):
        self.consistent()
        text = (self.folder / 'README.md').read_text().replace('- [0002](0002-cents.md) A decision: the rule in one line\n', '')
        (self.folder / 'README.md').write_text('```md\n## Accepted\n- [0002](0002-cents.md) example\n```\n' + text)
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['0002-cents.md: accepted but not listed under Accepted'], out['index_problems'])

    def test_nested_and_indented_fences_follow_markdown(self):
        self.consistent()
        text = (self.folder / 'README.md').read_text().replace('- [0002](0002-cents.md) A decision: the rule in one line\n', '')
        example = ('````md\n```\n## Accepted\n- [0002](0002-cents.md) example\n```\n````\n'
                   '```\n~~~\n## Accepted\n- [0002](0002-cents.md) example\n```\n')
        (self.folder / 'README.md').write_text(example + text)
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['0002-cents.md: accepted but not listed under Accepted'], out['index_problems'])
        self.consistent()
        text = (self.folder / 'README.md').read_text()
        (self.folder / 'README.md').write_text('    ``` an indented code line, not a fence\n' + text)
        self.assertEqual(0, check(self.repo)[0])

    def test_links_with_a_title_name_their_file(self):
        self.consistent()
        text = (self.folder / 'README.md').read_text().replace('](0002-cents.md)', '](0002-cents.md "Cents")')
        (self.folder / 'README.md').write_text(text + '- [9999](9999-missing.md "Missing") gone\n')
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['9999-missing.md: listed in the index but no such file'], out['index_problems'])

    def test_an_inline_code_span_does_not_open_a_fence(self):
        self.consistent()
        text = (self.folder / 'README.md').read_text()
        (self.folder / 'README.md').write_text('```example``` is inline code, not a fence.\n\n' + text)
        self.assertEqual(0, check(self.repo)[0])

    def test_an_entry_with_an_escaped_title_is_read(self):
        self.consistent()
        text = (self.folder / 'README.md').read_text()
        (self.folder / 'README.md').write_text(text + '- [9999](9999-ghost.md "say \\"hello\\"")\n')
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['9999-ghost.md: listed in the index but no such file'], out['index_problems'])

    def test_a_list_line_naming_an_adr_without_a_link_is_reported(self):
        self.consistent()
        text = (self.folder / 'README.md').read_text()
        (self.folder / 'README.md').write_text(text + '- 0005-unlinked.md is pending\n')
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['README.md: cannot read an index entry in: - 0005-unlinked.md is pending'],
                         out['index_problems'])

    def test_prose_headings_and_annotations_are_not_entries(self):
        self.consistent()
        text = (self.folder / 'README.md').read_text()
        text = text.replace('# Decisions', '# Decisions about [0002](0002-cents.md)')
        text = text.replace('- [0001](0001-sync-api.md) A decision: the rule in one line',
                            '- [0001](0001-sync-api.md) A decision: superseded by [0003](0003-async-api.md)')
        (self.folder / 'README.md').write_text(text + '\nSee `0002-cents.md` for the rationale. Superseded by 0003-async-api.md.\n')
        code, out = check(self.repo)
        self.assertEqual(0, code, out)

    def test_an_annotation_naming_a_missing_adr_is_reported(self):
        self.consistent()
        text = (self.folder / 'README.md').read_text().replace(
            '- [0002](0002-cents.md) A decision: the rule in one line',
            '- [0002](0002-cents.md) A decision: see [9999](<9999-ghost.md>)')
        (self.folder / 'README.md').write_text(text)
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['9999-ghost.md: listed in the index but no such file'], out['index_problems'])

    def test_a_target_that_only_starts_like_the_file_is_not_an_entry(self):
        for broken in ('- [0002](0002-cents.md.bak) A decision', '- [0002](0002-cents.md A decision'):
            with self.subTest(line=broken):
                self.consistent()
                text = (self.folder / 'README.md').read_text().replace(
                    '- [0002](0002-cents.md) A decision: the rule in one line', broken)
                (self.folder / 'README.md').write_text(text)
                code, out = check(self.repo)
                self.assertEqual(1, code)
                self.assertIn('0002-cents.md: accepted but not listed under Accepted', out['index_problems'])

    def test_a_file_name_label_and_an_escaped_bracket_are_read(self):
        self.consistent()
        text = (self.folder / 'README.md').read_text()
        text = text.replace('- [0002](0002-cents.md)', '- [0002-cents.md](0002-cents.md)')
        text = text.replace('- [0003](0003-async-api.md)', '- [Async \\] API](0003-async-api.md)')
        (self.folder / 'README.md').write_text(text)
        code, out = check(self.repo)
        self.assertEqual(0, code, out)

    def test_an_entry_naming_a_missing_bracketed_file_is_reported(self):
        self.consistent()
        text = (self.folder / 'README.md').read_text()
        (self.folder / 'README.md').write_text(text + '- [ghost](9999-cache[v2].md)\n')
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['9999-cache[v2].md: listed in the index but no such file'], out['index_problems'])

    def test_a_link_outside_any_section_is_reported_not_a_crash(self):
        self.consistent()
        text = (self.folder / 'README.md').read_text()
        (self.folder / 'README.md').write_text('- [0002](0002-cents.md) intro\n' + text + '\n## Other\n- [0002](0002-cents.md)\n')
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['0002-cents.md: accepted but also listed outside a status section',
                          '0002-cents.md: accepted but also listed under Other'], out['index_problems'])

    def test_quoted_status_values_are_read(self):
        self.consistent()
        self.adr('0004-old-cache.md', status='"deprecated"')
        self.assertEqual(0, check(self.repo)[0])

    def test_a_supersede_link_to_a_missing_adr_says_so(self):
        self.consistent()
        self.adr('0002-cents.md', status='superseded by ADR-0007')
        self.index(accepted=['0003-async-api.md'], superseded=['0001-sync-api.md', '0002-cents.md'],
                   deprecated=['0004-old-cache.md'])
        code, out = check(self.repo)
        self.assertEqual(1, code)
        self.assertEqual(['0002-cents.md: superseded by ADR-0007, which does not exist'], out['index_problems'])

    def test_the_gate_reads_the_index_from_a_merge_of_two_branches(self):
        git = lambda *a: subprocess.run(['git', '-C', str(self.repo), *a], check=True, capture_output=True)
        git('config', 'user.email', 't@e'); git('config', 'user.name', 't')
        self.consistent()
        git('add', '-A'); git('commit', '-qm', 'base'); git('branch', '-M', 'main')
        git('checkout', '-qb', 'feat/x')
        self.adr('0005-new.md')
        self.index(accepted=['0002-cents.md', '0003-async-api.md', '0005-new.md'], superseded=['0001-sync-api.md'],
                   deprecated=['0004-old-cache.md'])
        git('add', '-A'); git('commit', '-qm', 'workstream')
        git('checkout', '-q', 'main')
        code, out = check(self.repo, '--ref', 'feat/x', '--against', 'main')
        self.assertEqual(0, code, out)
        self.assertEqual('0006', out['next'])
        self.adr('0006-unlisted.md')
        git('add', '-A'); git('commit', '-qm', 'main adds an unlisted ADR')
        code, out = check(self.repo, '--ref', 'feat/x', '--against', 'main')
        self.assertEqual(1, code)
        self.assertEqual(['0006-unlisted.md: accepted but not listed under Accepted'], out['index_problems'])


if __name__ == '__main__':
    unittest.main()
