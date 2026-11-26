#!/usr/bin/env python3
"""Check the stable CI job contract and its local verification entry points."""
import unittest

import support


class CIWorkflow(unittest.TestCase):
    def test_a_read_only_ci_job_runs_clean_suite_and_whole_repo_doc_check(self):
        workflow = (support.ROOT / '.github/workflows/ci.yml').read_text()
        self.assertRegex(workflow, r'(?m)^on:\s*\n  push:\s*\n  pull_request:\s*$')
        self.assertRegex(workflow, r'(?m)^permissions:\n  contents: read$')
        self.assertRegex(workflow, r'(?m)^  verify:\n    name: Hermetic tests and docs$')
        self.assertIn('actions/setup-python@', workflow)
        self.assertIn('actions/setup-node@', workflow)
        self.assertIn('npm install --global @anthropic-ai/claude-code@', workflow)
        self.assertIn('python -m venv .venv', workflow)
        self.assertIn('.venv/bin/python -m pip install -r requirements.txt', workflow)
        self.assertIn('run: .venv/bin/python tests/run_clean.py\n', workflow)
        self.assertRegex(workflow, r'(?m)^        run: \.venv/bin/python '
                                  r'\.agents/skills/writing-agent-docs/check_docs\.py$')


if __name__ == '__main__':
    unittest.main()
