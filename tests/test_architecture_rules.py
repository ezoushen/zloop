#!/usr/bin/env python3
"""check_docs.py enforces the module architecture: each rule on a small fixture tree."""
import json
import shutil
import subprocess
import sys
import unittest

import support
from test_doc_checker import CHECKER, GUIDE, Fixture, SKILL

ARCHITECTURE = {
    'levels': [['skills'], ['flows'], ['runtime']],
    'modules': {
        'skills': {'docs': ['skills/*/SKILL.md'], 'isolated': True},
        'flows': {'docs': ['flows/**/*.md']},
        'runtime': {'docs': ['shared/**/*.md']},
    },
    'debt': {},
}


class ArchitectureRules(Fixture):
    def setUp(self):
        super().setUp()
        self.architecture = json.loads(json.dumps(ARCHITECTURE))
        self.config['docs'] = ['skills/**/*.md', 'shared/**/*.md', 'flows/**/*.md']
        self.config['linking'] = ['README.md'] + self.config['docs']
        self.config['code_doc_pattern'] = r'(?:shared|skills|flows)/[\w/-]+\.md'

    def messages(self, code):
        return [(f['path'], f['line'], f['message']) for f in self.check_full() if f['code'] == code]

    def test_the_clean_fixture_passes(self):
        self.assertEqual(self.check(), set())

    def test_a_lower_layer_linking_a_higher_one_is_reported_with_both_modules_but_same_module_links_are_not(self):
        self.write('flows/a/one.md', '# One\n\nSee [two](two.md).\n')
        self.write('flows/a/two.md', '# Two\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + 'And [one](../../flows/a/one.md).\n')
        self.assertEqual(self.check(), set())
        self.write('shared/guide.md', '# Guide\n\nUp: [one](../flows/a/one.md).\n')
        self.assertEqual(self.check(), {('shared/guide.md', 3, 'direction', 'error')})
        [(_, _, message)] = self.messages('direction')
        self.assertIn('runtime', message)
        self.assertIn('flows', message)

    def test_a_skill_linking_another_skill_is_reported_and_a_skill_may_link_what_it_owns_below(self):
        self.write('skills/other/SKILL.md', SKILL.format(name='other', description='Runs another tool.')
                   + 'Also [tool](../tool/SKILL.md).\n')
        self.assertEqual(self.check(), {('skills/other/SKILL.md', 9, 'direction', 'error')})
        [(_, _, message)] = self.messages('direction')
        self.assertIn('skills/other/SKILL.md', message)
        self.assertIn('skills/tool/SKILL.md', message)

    def test_modules_in_the_same_level_are_siblings_and_may_not_link_each_other(self):
        self.architecture['levels'] = [['skills'], ['flows', 'peer'], ['runtime']]
        self.architecture['modules']['peer'] = {'docs': ['peer/**/*.md']}
        self.config['docs'] += ['peer/**/*.md']
        self.config['linking'] += ['peer/**/*.md']
        self.write('peer/p.md', '# Peer\n')
        self.write('flows/a/one.md', '# One\n\nSee [peer](../../peer/p.md).\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + '[one](../../flows/a/one.md) [peer](../../peer/p.md)\n')
        self.assertEqual(self.check(), {('flows/a/one.md', 3, 'direction', 'error')})
        [(_, _, message)] = self.messages('direction')
        self.assertIn('sibling', message)

    def test_a_doc_path_named_in_text_is_a_dependency_like_a_link(self):
        self.write('flows/a/one.md', '# One\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + '[one](../../flows/a/one.md)\n')
        self.write('shared/guide.md', '# Guide\n\n## Steps\n\nThe watch is step 5 of `flows/a/one.md`.\n')
        self.assertEqual(self.check(), {('shared/guide.md', 5, 'direction', 'error')})
        [(_, _, message)] = self.messages('direction')
        self.assertIn('shared/guide.md -> flows/a/one.md', message)
        self.write('shared/guide.md', GUIDE)
        self.write('flows/a/one.md', '# One\n\nSee `shared/guide.md`, in a lower module.\n')
        self.assertEqual(self.check(), set())

    def test_a_relative_doc_path_named_in_text_is_a_dependency_too(self):
        self.config['code_doc_pattern'] = r'(?<![\w/.-])(?:shared|skills|flows)/[\w/-]+\.md'  # as the repository's
        self.write('flows/a/one.md', '# One\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + '[one](../../flows/a/one.md)\n')
        self.write('shared/guide.md', '# Guide\n\n## Steps\n\nSee `../flows/a/one.md`.\n')
        self.assertEqual(self.check(), {('shared/guide.md', 5, 'direction', 'error')})

    def test_a_doc_named_only_in_text_is_reached_not_an_orphan(self):
        self.write('flows/a/one.md', '# One\n\nSee `shared/extra.md`.\n')
        self.write('shared/extra.md', '# Extra\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + '[one](../../flows/a/one.md)\n')
        self.assertEqual(self.check(), set())

    def test_a_doc_may_name_a_module_injected_by_path_in_text(self):
        self.architecture['levels'].append(['hosts'])
        self.architecture['modules']['hosts'] = {'docs': ['hosts/**/*.md'], 'unlinked': True}
        self.config['docs'] = self.config['docs'] + ['hosts/**/*.md']
        self.config['entry_points'] = self.config['entry_points'] + ['hosts/*/session.md']
        self.config['code_doc_pattern'] = r'(?:shared|skills|flows|hosts)/[\w/-]+\.md'
        self.write('hosts/small/session.md', '# Session\n')
        self.write('shared/guide.md', GUIDE + '\nThe host part is `hosts/small/session.md`.\n')
        self.assertEqual(self.check(), set())

    def test_code_naming_a_doc_of_a_higher_module_is_a_direction_error(self):
        self.architecture['modules']['runtime']['code'] = ['scripts/run.py']
        self.write('flows/a/one.md', '# One\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + '[one](../../flows/a/one.md)\n')
        self.assertEqual(self.check(), set())
        self.write('scripts/run.py', "GUIDE = 'shared/guide.md'\nHELP = 'see flows/a/one.md'\n")
        self.assertEqual(self.check(), {('scripts/run.py', 2, 'direction', 'error')})

    def test_the_checker_reports_a_script_importing_a_higher_module(self):
        self.architecture['modules']['flows']['code'] = ['scripts/high.py']
        self.architecture['modules']['runtime']['code'] = ['scripts/run.py']
        self.write('scripts/high.py', 'X = 1\n')
        self.assertEqual(self.check(), set())
        self.write('scripts/run.py', "GUIDE = 'shared/guide.md'\n\ndef f():\n    import high\n")
        self.assertEqual(self.check(), {('scripts/run.py', 4, 'import-direction', 'error')})

    def test_a_module_injected_by_path_may_not_be_linked(self):
        self.first_read_tree()
        self.write('flows/a/one.md', '# One\n\nSee [host](../../hosts/small/session.md).\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + '[one](../../flows/a/one.md)\n')
        self.assertIn(('flows/a/one.md', 3, 'direction', 'error'), self.check())
        [(_, _, message)] = self.messages('direction')
        self.assertIn('never linked', message)

    def test_two_modules_linking_each_other_form_a_cycle_naming_both(self):
        self.write('flows/a/one.md', '# One\n\nDown: [guide](../../shared/guide.md).\n')
        self.write('shared/guide.md', '# Guide\n\nUp: [one](../flows/a/one.md).\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + 'And [one](../../flows/a/one.md).\n')
        found = {(f['code'], f['severity']) for f in self.check_full()}
        self.assertEqual(found, {('direction', 'error'), ('cycle', 'error')})
        [(_, _, message)] = self.messages('cycle')
        self.assertIn('flows', message)
        self.assertIn('runtime', message)

    def test_recorded_debt_is_tolerated_and_stale_debt_is_flagged(self):
        self.write('shared/guide.md', '# Guide\n\nUp: [one](../flows/a/one.md).\n')
        self.write('flows/a/one.md', '# One\n\nDown: [guide](../../shared/guide.md).\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + 'And [one](../../flows/a/one.md).\n')
        self.architecture['debt'] = {'links': ['shared/guide.md -> flows/a/one.md'], 'cycles': [['flows', 'runtime']]}
        self.assertEqual(self.check(), set())
        self.architecture['debt'] = {'links': ['shared/guide.md -> flows/a/one.md', 'skills/tool/SKILL.md -> shared/guide.md'],
                                     'cycles': [['flows', 'runtime', 'skills']]}
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'stale-debt', 'warning'),
                                        ('flows/a/one.md', 3, 'debt', 'warning')})
        self.write('shared/guide.md', GUIDE)
        self.assertEqual(self.check(), {('shared/guide.md', 1, 'stale-debt', 'warning'),
                                        ('skills/tool/SKILL.md', 1, 'stale-debt', 'warning'),
                                        ('architecture.json', 1, 'stale-debt', 'warning')})

    def test_a_doc_no_module_owns_is_an_error(self):
        self.write('shared/guide.md', GUIDE)
        self.config['docs'] = self.config['docs'] + ['extra/*.md']
        self.write('extra/loose.md', '# Loose\n')
        self.write('README.md', '# Project\n\nSee [tool](skills/tool/SKILL.md) and [loose](extra/loose.md).\n')
        self.assertEqual(self.check(), {('extra/loose.md', 1, 'unmapped', 'error')})

    def test_an_anchored_link_to_a_doc_no_module_owns_is_not_a_crash(self):
        self.write('shared/guide.md', GUIDE)
        self.write('flows/a/one.md', '# One\n\nSee [readme](../../README.md#project).\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + '[one](../../flows/a/one.md)\n')
        self.assertEqual(self.check(), {('flows/a/one.md', 3, 'unowned', 'error')})

    def test_an_agent_doc_linking_or_naming_a_doc_no_module_owns_is_an_error_but_a_peoples_doc_may_link_it(self):
        # Rule: a doc an agent doc sends agents to is an agent doc, owned by a module and checked as one.
        self.config['linking'] += ['notes/**/*.md']
        self.write('notes/helper.md', '# Helper\n\nRun the helper.\n')
        self.write('README.md', '# Project\n\nSee [tool](skills/tool/SKILL.md) and [helper](notes/helper.md).\n')
        self.assertEqual(self.check(), set())
        self.write('shared/guide.md', GUIDE + 'Use [the helper](../notes/helper.md).\nOr read ../notes/helper.md.\n')
        self.assertEqual(self.check(), {('shared/guide.md', 6, 'unowned', 'error'),
                                        ('shared/guide.md', 7, 'unowned', 'error')})
        [message, _] = self.messages('unowned')
        self.assertIn('notes/helper.md', message[2])

    def first_read_tree(self):
        self.architecture['levels'] = [['skills'], ['bootstrap'], ['flows'], ['runtime'], ['hosts']]
        self.architecture['modules']['bootstrap'] = {'docs': ['bootstrap.md']}
        self.architecture['modules']['hosts'] = {'docs': ['hosts/**/*.md'], 'unlinked': True}
        self.architecture['first_read'] = {'budget': 100000, 'bootstrap': ['bootstrap.md'],
                                           'host_session': ['hosts/*/session.md'], 'skills': ['skills/*/SKILL.md']}
        self.config['max_bytes'] = 10000
        self.config['docs'] += ['bootstrap.md', 'hosts/**/*.md']
        self.config['entry_points'] += ['bootstrap.md', 'hosts/*/session.md']
        self.config['code_doc_pattern'] = r'(?:shared|skills|flows|hosts)/[\w/-]+\.md'
        bootstrap, small, large = '# Bootstrap\n' + 'b' * 300 + '\n', '# S\n' + 'h' * 200 + '\n', '# L\n' + 'h' * 500 + '\n'
        skill = SKILL.format(name='tool', description='Runs the tool.') + '\n## Later\n\nSee [flow](../../flows/a/late.md).\n'
        skill = skill.replace('Read [the guide](../../shared/guide.md).', 'First read: [bootstrap](../../bootstrap.md), [the guide](../../shared/guide.md).')
        self.write('bootstrap.md', bootstrap)
        self.write('hosts/small/session.md', small)
        self.write('hosts/large/session.md', large)
        self.write('flows/a/late.md', '# Late\n' + 'x' * 5000 + '\n')
        self.write('skills/tool/SKILL.md', skill)
        encoded = lambda text: len(text.encode())
        return encoded(bootstrap) + encoded(large) + encoded(skill) + encoded(GUIDE)

    def test_a_declared_first_read_under_a_heading_counts_toward_the_budget(self):
        total = self.first_read_tree()
        path = self.root / 'skills/tool/SKILL.md'
        heading = '## Required reads\n\n'
        path.write_text(path.read_text().replace('First read:', heading + 'First read:'))
        total += len(heading.encode())
        self.architecture['first_read']['budget'] = total - 1
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'first-read', 'error')})
        [(_, _, message)] = self.messages('first-read')
        self.assertIn(f'first reads total {total} bytes', message)

    def test_a_skill_without_a_first_read_line_is_an_error_and_only_that_line_counts(self):
        total = self.first_read_tree()
        path = self.root / 'skills/tool/SKILL.md'
        text = path.read_text()
        self.architecture['first_read']['budget'] = total
        path.write_text(text.replace('First read:', 'Start with:'))
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'first-read-line', 'error')})
        extra = '\nSee [late](../../flows/a/late.md).\n'
        self.architecture['first_read']['budget'] = total + len(extra.encode())
        path.write_text(text.replace('# Tool\n', '# Tool\n' + extra, 1))
        self.assertEqual(self.check(), set())

    def test_a_wrapped_first_read_declaration_counts_every_line_of_its_paragraph(self):
        total = self.first_read_tree()
        path = self.root / 'skills/tool/SKILL.md'
        path.write_text(path.read_text().replace('(../../bootstrap.md), [the guide]', '(../../bootstrap.md),\n[the guide]'))
        self.architecture['first_read']['budget'] = total
        self.assertEqual(self.check(), set())
        self.architecture['first_read']['budget'] = total - 1
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'first-read', 'error')})

    def test_a_skill_whose_first_reads_exceed_the_budget_is_reported_with_its_total_bytes(self):
        total = self.first_read_tree()
        self.architecture['first_read']['budget'] = total
        self.assertEqual(self.check(), set())
        self.architecture['first_read']['budget'] = total - 1
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'first-read', 'error')})
        [(_, _, message)] = self.messages('first-read')
        self.assertIn(str(total), message)
        self.architecture['debt'] = {'first_read': {'skills/tool/SKILL.md': total}}
        self.assertEqual(self.check(), set())
        self.architecture['debt'] = {'first_read': {'skills/tool/SKILL.md': total - 1}}
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'first-read', 'error')})
        self.architecture['debt'] = {'first_read': {'skills/tool/SKILL.md': total + 7}}
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'debt', 'warning')})
        self.architecture['first_read']['budget'] = total + 1
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'stale-debt', 'warning')})

    def entry_tree(self, target, own_module=False, command=False):
        """The first-read fixture with the entry-doc rule on and the skill's declaration naming target.

        own_module maps skills/tool/ as the skill's own module, so its SKILL.md holds a procedure;
        otherwise the skill is a thin wrapper mapped into the shared `skills` module."""
        self.first_read_tree()
        self.architecture['entry_doc'] = True
        self.architecture['modules']['skills']['docs'] = ['skills/other/SKILL.md']
        if own_module:
            self.architecture['levels'].insert(1, ['tool'])
            self.architecture['modules']['tool'] = {'docs': ['skills/tool/**/*.md']}
        else:
            self.architecture['modules']['skills']['docs'].append('skills/tool/SKILL.md')
        path = self.root / 'skills/tool/SKILL.md'
        text = path.read_text()
        if command:
            text = text.replace('---\n\n', 'disable-model-invocation: true\n---\n\n', 1)
        if target:
            self.write(target, '# Tool procedure\n')
            text = text.replace('[the guide](../../shared/guide.md).',
                                f'[the guide](../../shared/guide.md), [procedure](../../{target}).')
        path.write_text(text)

    def test_a_skill_wrapping_a_lower_doc_named_after_it_is_an_error_naming_its_own_folder(self):
        for command in (False, True):
            with self.subTest(command=command):
                self.entry_tree('shared/tool.md', command=command)
                self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'entry-doc', 'error')})
                [(_, _, message)] = self.messages('entry-doc')
                self.assertIn('skills/tool/', message)

    def test_a_command_holding_its_procedure_in_its_own_module_passes(self):
        self.entry_tree(None, own_module=True, command=True)
        self.assertEqual(self.check(), set())

    def test_a_command_mapped_into_another_commands_module_is_an_error(self):
        self.entry_tree(None, own_module=True, command=True)
        self.architecture['modules']['other'] = self.architecture['modules'].pop('tool')
        self.architecture['levels'][1] = ['other']
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'entry-doc', 'error')})
        [(_, _, message)] = self.messages('entry-doc')
        self.assertIn('module named tool', message)

    def test_a_skill_in_its_own_module_that_first_reads_a_doc_named_after_it_is_an_error(self):
        self.entry_tree('skills/tool/tool.md', own_module=True, command=True)
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'entry-doc', 'error')})
        [(_, _, message)] = self.messages('entry-doc')
        self.assertIn('SKILL.md', message)

    def test_a_skill_in_its_own_module_that_first_reads_a_same_named_lower_doc_is_an_error(self):
        # The thin-wrapper shape: a skill whose first read is a standard or runtime doc named like it.
        self.entry_tree('shared/tool.md', own_module=True, command=True)
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'entry-doc', 'error')})

    def host_tree(self, **extra):
        self.first_read_tree()
        self.architecture['host_shape'] = {'root': 'hosts', 'files': ['session.md', 'workers.md'], 'exempt': ['tools']}
        for name in ('session.md', 'workers.md'):
            self.write(f'hosts/pair/{name}', '# Part\n')
        self.write('hosts/tools/bash.md', '# Tool\n')
        self.write('hosts/small/workers.md', '# Workers\n')
        for path, text in extra.items():
            self.write(path, text)
        self.write('README.md', '# Project\n\nSee [tool](skills/tool/SKILL.md).\n')

    def host_findings(self):
        return {(f['path'], f['code'], f['severity']) for f in self.check_full() if f['code'] == 'host-shape'}

    def test_a_host_folder_holds_exactly_a_session_part_and_a_worker_part(self):
        self.host_tree()
        self.write('hosts/large/workers.md', '# Workers\n')
        self.assertEqual(self.host_findings(), set())
        self.write('hosts/pair/extra.md', '# Extra\n')
        self.assertEqual(self.host_findings(), {('hosts/pair', 'host-shape', 'error')})
        (self.root / 'hosts/pair/extra.md').unlink()
        (self.root / 'hosts/pair/workers.md').unlink()
        self.assertEqual(self.host_findings(), {('hosts/pair', 'host-shape', 'error')})

    def test_a_section_linked_from_three_files_in_other_modules_should_be_its_own_file(self):
        self.write('shared/guide.md', '# Guide\n\n## Steps\n\nRun.\n\n## Later\n\nStop.\n')
        for name in ('a', 'b', 'c'):
            self.write(f'flows/{name}/f.md', f'# F\n\nSee [steps](../../shared/guide.md#steps).\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + ''.join(f'[{n}](../../flows/{n}/f.md) ' for n in 'abc') + '\n')
        self.assertEqual(self.check(), {('shared/guide.md', 1, 'shared-section', 'error')})
        self.write('flows/c/f.md', '# F\n\nSee [guide](../../shared/guide.md).\n')
        self.assertEqual(self.check(), set())
        self.write('shared/other.md', '# Other\n\nSee [steps](guide.md#steps).\n')
        self.write('flows/c/f.md', '# F\n\nSee [guide](../../shared/guide.md) and [other](../../shared/other.md).\n')
        self.assertEqual(self.check(), set())

    def test_links_to_a_files_title_or_its_only_section_are_not_shared_sections(self):
        self.write('shared/guide.md', '# Guide\n\n## Steps\n\nRun.\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + ''.join(f'[{n}](../../flows/{n}/f.md) ' for n in 'abc') + '\n')
        for target in ('guide', 'steps'):
            with self.subTest(anchor=target):
                for name in ('a', 'b', 'c'):
                    self.write(f'flows/{name}/f.md', f'# F\n\nSee [it](../../shared/guide.md#{target}).\n')
                self.assertEqual(self.check(), set())

    def test_skills_count_as_other_modules_to_each_other_for_shared_sections(self):
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.') + '\n## Filter\n\nAsk less.\n\n## Later\n\nStop.\n')
        for name in ('a', 'b', 'c'):
            self.write(f'skills/{name}/SKILL.md', SKILL.format(name=name, description='Runs a tool.')
                       + 'See [filter](../tool/SKILL.md#filter).\n')
        self.architecture['debt'] = {'links': [f'skills/{n}/SKILL.md -> skills/tool/SKILL.md' for n in 'abc']}
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'shared-section', 'error')})


TARGET_DOCS = {  # doc under a target folder: is it an entry point (exempt from the orphan rule)?
    'bootstrap.md': True,
    'skills/dispatch/handoff.md': False,
    'skills/dispatch/jobs/job.md': True,
    'runtime/board/board.md': False,
    'standards/style.md': False,
    'runtime/workers/launch.md': False,
    'hosts/claude/session.md': True,
    'hosts/claude/workers.md': False,
    'hosts/herdr.md': False,
    'hosts/executors/bash.md': False,
}


class TargetFolders(Fixture):
    """The repository's own config applies size, link, orphan and code-path rules to every target folder."""

    def run_repository_config(self):
        self.write('GLOSSARY.md', '# Fixture\n')  # the repository's config names a glossary, which must exist
        result = subprocess.run([sys.executable, str(CHECKER), '--root', str(self.root), '--json'],
                                capture_output=True, text=True)
        assert result.returncode in (0, 1), result.stderr
        # the fixture script is unmapped under the repository's architecture; only its doc paths matter here
        return {f['code'] for f in json.loads(result.stdout)
                if f['path'] == self.doc or (f['path'] == 'scripts/run.py' and f['code'] == 'code-path')}

    def test_each_target_folder_gets_the_size_link_orphan_and_code_path_rules(self):
        for doc, entry in TARGET_DOCS.items():
            with self.subTest(doc=doc):
                self.doc = doc
                (self.root / 'scripts').mkdir(exist_ok=True)
                for name in self.root.iterdir():
                    if name.name != 'scripts':
                        shutil.rmtree(name) if name.is_dir() else name.unlink()
                self.write(doc, '# Doc\n\nSee [gone](gone.md).\n\n' + 'x' * 13000 + '\n')
                sibling = doc.rsplit('/', 1)[0] + '/missing.md' if '/' in doc else None
                self.write('scripts/run.py', f"MISSING = '{sibling}'\n" if sibling else "OK = 1\n")
                expected = {'size', 'link'} | (set() if entry else {'orphan'}) | ({'code-path'} if sibling else set())
                self.assertEqual(self.run_repository_config() & {'size', 'link', 'orphan', 'code-path', 'unmapped'}, expected)

    def test_a_missing_bootstrap_named_in_code_is_a_code_path_error(self):
        self.write('scripts/run.py', "BOOT = 'bootstrap.md'\n")
        self.doc = 'bootstrap.md'
        self.assertEqual(self.run_repository_config(), {'code-path'})


RUNTIME_MODULES = ('workers', 'checks', 'routing', 'resources', 'decisions', 'config')


class RepositoryDebt(unittest.TestCase):
    def test_the_runtime_modules_record_no_debt(self):
        data = json.loads((support.ROOT / '.agents/skills/writing-agent-docs/architecture.json').read_text())
        runtime_docs = [glob for name in RUNTIME_MODULES for glob in data['modules'][name]['docs']]
        self.assertTrue(all(glob.startswith('runtime/') for glob in runtime_docs), runtime_docs)
        debt = data['debt']
        self.assertEqual([link for link in debt['links'] if link.startswith('runtime/')], [])
        self.assertEqual([cycle for cycle in debt['cycles'] if set(cycle) & set(RUNTIME_MODULES)], [])


if __name__ == "__main__":
    unittest.main()
