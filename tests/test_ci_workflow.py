#!/usr/bin/env python3
"""Check the stable CI job contract and its local verification entry points."""
import re
import unittest

import support


class CIWorkflow(unittest.TestCase):
    def test_a_read_only_ci_job_runs_clean_suite_and_whole_repo_doc_check(self):
        workflow = (support.ROOT / '.github/workflows/ci.yml').read_text()
        self.assertRegex(workflow, r'(?m)^on:\s*\n  push:\s*\n  pull_request:\s*$')
        permissions = re.search(r'(?ms)^permissions:\n(.*?)(?=^[A-Za-z_][\w.-]*:|\Z)', workflow)
        self.assertIsNotNone(permissions, 'CI needs a workflow permissions mapping')
        self.assertEqual([line for line in permissions[1].splitlines()
                          if line.strip() and not line.lstrip().startswith('#')], ['  contents: read'])
        verify = re.search(r'(?ms)^jobs:\n  verify:\n(.*?)(?=^(?:\S|  \S)|\Z)', workflow)
        self.assertIsNotNone(verify, 'CI needs a verify job')
        job, marker, steps = verify[1].partition('    steps:\n')
        self.assertEqual(marker, '    steps:\n')
        self.assertIn('    name: Hermetic tests and docs\n', job)
        self.assertNotRegex(job, r'(?m)^    (if|permissions):')

        entries = [step.strip('\n') for step in re.split(r'(?m)^      - ', steps)[1:]]
        for action in ('checkout', 'setup-python', 'setup-node'):
            setup = [step for step in entries if step.startswith(f'uses: actions/{action}@')]
            self.assertEqual(len(setup), 1, action)
            self.assertNotRegex(setup[0], r'(?m)^        if:')

        prepare = self.step(entries, 'Prepare test tools')
        self.assertNotRegex(prepare, r'(?m)^        if:')
        self.assertIn('python -m venv .venv', prepare)
        self.assertIn('.venv/bin/python -m pip install -r requirements.txt', prepare)
        self.assertIn('npm install --global @anthropic-ai/claude-code@', prepare)
        self.assertEqual(self.step(entries, 'Run the clean-shell suite').splitlines()[1:],
                         ['        run: .venv/bin/python tests/run_clean.py'])
        self.assertEqual(self.step(entries, "Check the whole repository's agent docs").splitlines()[1:],
                         ['        if: ${{ !cancelled() }}',
                          '        run: .venv/bin/python .agents/skills/writing-agent-docs/check_docs.py'])

    def step(self, entries, name):
        matches = [entry for entry in entries if entry.splitlines()[0] == f'name: {name}']
        self.assertEqual(len(matches), 1, name)
        return matches[0]


if __name__ == '__main__':
    unittest.main()
