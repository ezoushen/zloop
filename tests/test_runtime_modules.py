#!/usr/bin/env python3
"""The runtime holds ten modules in a fixed order, and the skills over them stay thin."""
import unittest

import support

ROOT = support.ROOT
RUNTIME = ROOT / 'runtime'
ORDER = ['interview', 'board', 'workers', 'checks', 'routing', 'resources', 'decisions', 'config', 'ask', 'domain']


class RuntimeModules(unittest.TestCase):
    def test_the_runtime_folder_holds_exactly_the_ten_modules(self):
        self.assertEqual(sorted(path.name for path in RUNTIME.iterdir()), sorted(ORDER))

    def test_recovery_joins_the_workers_and_the_configuration_format_joins_config(self):
        for name in ('workers/recovery.md', 'config/catalog.md', 'config/routers.md', 'config/session.md',
                     'config/executors.md', 'resources/resources.md', 'resources/retire-worktree.md'):
            self.assertTrue((RUNTIME / name).is_file(), name)
        self.assertTrue((RUNTIME / 'config/troubleshooting.md').is_file())
        self.assertTrue((RUNTIME / 'workers/legacy-preferences.md').is_file())
        for name in ('SKILL.md', 'tour.md', 'apply.md', 'reset-and-validate.md'):
            self.assertTrue((ROOT / 'skills/setup' / name).is_file(), name)

    def test_each_runtime_readme_lists_every_doc_in_its_folder(self):
        for readme in sorted(RUNTIME.glob('*/README.md')):
            text = readme.read_text()
            for doc in sorted(readme.parent.glob('*.md')):
                if doc.name != 'README.md':
                    with self.subTest(doc=str(doc.relative_to(RUNTIME))):
                        self.assertIn(f']({doc.name})', text)

    def test_every_agent_reads_resource_lifetime_with_the_runner_contract_checks(self):
        for agent in sorted((ROOT / 'agents').glob('*.md')):
            text = agent.read_text()
            with self.subTest(agent=agent.name):
                self.assertIn('](../runtime/resources/lifetime.md)', text)
                self.assertIn('](../runtime/resources/resources.md)', text)

    def test_the_verifier_starts_with_every_check_doc(self):
        import agent_defs
        core = agent_defs.load(ROOT)['agents']['verifier']['core_skills']
        docs = {f'runtime/checks/{path.name}' for path in (RUNTIME / 'checks').glob('*.md') if path.name != 'README.md'}
        self.assertEqual(set(core), docs)

    def test_a_host_router_result_leads_to_the_model_routing_procedure(self):
        text = ' '.join((RUNTIME / 'routing/choose.md').read_text().split())
        self.assertIn('For the host router (`{"type": "host"}`, the default and every fallback), choose by '
                      '[model routing](model-routing.md)', text)
        for path in sorted(ROOT.glob('**/*.md')):
            if {'.worktrees', 'journals', '.scratch'} & set(path.relative_to(ROOT).parts):
                continue
            with self.subTest(doc=str(path.relative_to(ROOT))):
                self.assertNotRegex(' '.join(path.read_text().split()), r'built-in (?:host |routing )?skill|this skill returns')

    def test_decision_and_routing_are_runtime_docs_not_skills(self):
        for skill, target in (('decision', 'runtime/decisions/decision.md'),
                              ('model-routing', 'runtime/routing/model-routing.md')):
            self.assertFalse((ROOT / 'skills' / skill).exists(), skill)
            self.assertTrue((ROOT / target).is_file(), target)

    def test_housekeeping_keeps_a_pointer_to_worktree_retirement(self):
        text = (ROOT / 'skills/housekeeping/SKILL.md').read_text()
        self.assertIn('## Retire a worktree\n\nFollow [worktree retirement](../../runtime/resources/retire-worktree.md).', text)
        self.assertNotIn('git -C <checkout> status --porcelain --ignored', text)
        self.assertIn('git -C <checkout> status --porcelain --ignored', (RUNTIME / 'resources/retire-worktree.md').read_text())


if __name__ == '__main__':
    unittest.main()
