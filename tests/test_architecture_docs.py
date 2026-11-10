#!/usr/bin/env python3
"""The README and the agent-docs skill describe the layers and how to extend them."""
import json
import re
import unittest

import support

ROOT = support.ROOT
README = (ROOT / 'README.md').read_text()
SKILL = (ROOT / '.agents/skills/writing-agent-docs/SKILL.md').read_text()
BOOTSTRAP = (ROOT / 'bootstrap.md').read_text()
TOP = ('skills', 'runtime', 'hosts', 'standards', 'agents', 'hooks', 'scripts', 'schemas',
       'benchmarks', 'docs', 'bootstrap.md')
# Codex runtime method names, not repository paths.
CODEX_RPC = ('skills/list', 'hooks/list')
EXTENSIONS = ('host', 'executor', 'agent', 'job', 'kind', 'standard', 'runtime module', 'command')


class ArchitectureDocs(unittest.TestCase):
    def test_every_repository_path_the_readme_names_exists(self):
        for name in sorted(set(re.findall(r'`((?:%s)(?:/[\w./-]*)?)`' % '|'.join(TOP), README))):
            if name in CODEX_RPC:
                continue
            with self.subTest(path=name):
                self.assertTrue((ROOT / name).exists(), name)

    def test_the_readme_layout_has_a_row_for_every_top_level_folder(self):
        rows = [line.split('|')[1] for line in README.split('## Layout', 1)[1].split('\n## ', 1)[0].splitlines()
                if line.startswith('| `')]
        # docs/ has no row in the README today; it is the one top-level path left out.
        for name in (name for name in TOP if name != 'docs'):
            self.assertTrue(any(f'`{name}' in row for row in rows), name)
        self.assertIn('writing-agent-docs/SKILL.md', README)

    def test_the_readme_architecture_lists_the_layers_in_the_order_architecture_json_records(self):
        levels = json.loads((ROOT / '.agents/skills/writing-agent-docs/architecture.json').read_text())['levels']
        rank = {name: number for number, names in enumerate(levels) for name in names}
        section = README.split('## Architecture', 1)[1].split('\n## ', 1)[0]
        layers = {'skills/<command>/': 'brief', 'bootstrap.md': 'bootstrap', 'agents/': 'agents', 'standards/': 'standards',
                  'hosts/': 'hosts', 'runtime/': 'interview'}
        self.assertEqual(sorted(layers, key=section.index), sorted(layers, key=lambda token: rank[layers[token]]))
        runtime = ('interview', 'board', 'workers', 'checks', 'routing', 'resources', 'decisions', 'config', 'ask')
        listed = section.split('(interview', 1)[1].split(')', 1)[0]
        self.assertEqual(sorted(runtime, key=lambda name: (f'interview{listed}').index(name)),
                         sorted(runtime, key=rank.get))

    def test_the_readme_says_only_skills_face_the_user_and_nothing_below_them_links_one(self):
        section = ' '.join(README.split('## Architecture', 1)[1].split('\n## ', 1)[0].split())
        self.assertIn('diagnose, research, prototype, explain and tdd', section)
        self.assertIn('nothing below the skills links one', section)

    def test_the_readme_command_table_lists_every_skill_the_user_types(self):
        typed = {path.parent.name for path in (ROOT / 'skills').glob('*/SKILL.md')
                 if 'user-invocable: false' not in path.read_text()}
        table = README.split('## Commands', 1)[1].split('\n## ', 1)[0]
        self.assertEqual(set(re.findall(r'^\| `([a-z-]+)` \|', table, re.M)), typed)
        self.assertIn('the agent also loads them itself', ' '.join(table.split()))

    def test_the_readme_links_the_skill_instead_of_repeating_the_rules(self):
        self.assertIn('](.agents/skills/writing-agent-docs/SKILL.md#extend-the-architecture)', README)
        self.assertIsNone(re.search(r'\]\([^)]*journals/', README + SKILL))

    def test_the_skill_says_how_to_add_each_kind_of_module(self):
        section = SKILL.split('## Extend the architecture', 1)[1].split('\n## ', 1)[0]
        for kind in EXTENSIONS:
            self.assertRegex(section, r'(?i)\badd an? %s\b' % kind)
        for word in ('strict', 'room', 'architecture.json'):
            self.assertIn(word, section.lower())
        flat = ' '.join(section.split())
        self.assertIn('A skill both the user types and the agent loads by its trigger', flat)
        self.assertIn('nothing below the skills links one', flat)
        self.assertIn('A skill that launches nothing (`huh`, `explain`) skips the bootstrap', flat)

    def test_the_skill_contents_list_the_new_section(self):
        self.assertIn('- [Extend the architecture](#extend-the-architecture)', SKILL)

    def test_the_bootstrap_keeps_the_rules_every_session_needs_before_any_skill_or_launch(self):
        flat = ' '.join(BOOTSTRAP.split())
        self.assertIn('even exhaustive depth asks only principles and material forks', flat)

    def test_no_doc_points_at_a_retired_location_or_a_moved_bootstrap(self):
        self.assertNotIn('skills/<owner>/jobs', SKILL)
        self.assertNotIn('this bootstrap', (ROOT / 'runtime/workers/long-sessions.md').read_text())

    def test_the_code_path_pattern_skips_a_hidden_folder_prefix_and_the_scanned_tests_are_not_excluded(self):
        config = json.loads((ROOT / '.agents/skills/writing-agent-docs/config.json').read_text())
        pattern = re.compile(config['code_doc_pattern'])
        self.assertEqual(pattern.findall('.agents/skills/writing-agent-docs/SKILL.md'), [])
        self.assertEqual(pattern.findall("'skills/brief/SKILL.md' and bootstrap.md"),
                         ['skills/brief/SKILL.md', 'bootstrap.md'])
        self.assertFalse({'tests/test_architecture_docs.py', 'tests/test_import_direction.py'} & set(config['exclude']))

    def test_a_runtime_doc_names_no_path_in_a_higher_layer_and_the_bash_contract_lives_in_runtime_config(self):
        higher = re.compile(r'`(?:hosts|skills|standards|agents)/[^`]*`')
        for path in sorted((ROOT / 'runtime').rglob('*.md')):
            self.assertEqual(higher.findall(path.read_text()), [], path)
        contract = ROOT / 'runtime/config/bash-executor.md'
        self.assertIn('assignment/result contract', contract.read_text())
        self.assertFalse((ROOT / 'hosts/executors' / 'bash.md').exists())
        self.assertIn('](../../runtime/config/bash-executor.md)', (ROOT / 'hosts/executors/README.md').read_text())

    def test_the_skill_executor_contract_lives_in_runtime_config_and_config_docs_link_it(self):
        contract = (ROOT / 'runtime/config/skill-executor.md').read_text()
        self.assertIn('Resolve `executor_config.skill`', contract)
        self.assertIn('treat it as non-composable', ' '.join(contract.split()))
        self.assertNotIn('Resolve `executor_config.skill`', (ROOT / 'hosts/executors/skill.md').read_text())
        self.assertIn('](../../runtime/config/skill-executor.md)', (ROOT / 'hosts/executors/skill.md').read_text())
        for name in ('catalog.md', 'executors.md'):
            self.assertIn('](skill-executor.md)', (ROOT / 'runtime/config' / name).read_text(), name)

    def test_the_dispatch_reference_sends_no_reader_up_to_a_skill(self):
        self.assertNotIn('pipeline skill', (ROOT / 'skills/dispatch/reference.md').read_text())

    def test_no_recorded_debt_remains(self):
        base = ROOT / '.agents/skills/writing-agent-docs'
        self.assertEqual(json.loads((base / 'config.json').read_text())['debt'], {})
        debt = json.loads((base / 'architecture.json').read_text())['debt']
        self.assertEqual(set(debt), {'links', 'cycles', 'first_read', 'imports', 'import_cycles'})
        self.assertTrue(all(not value for value in debt.values()))


if __name__ == '__main__':
    unittest.main()
