#!/usr/bin/env python3
"""Claude routes carry an effort control independent of the caller's session."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import support


models = support.load_script('agent_models.py', 'agent_models')


class ClaudeEffortRoutes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop claude routes ')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        environment = patch.dict(os.environ, support.isolated_env(
            self.root, HERDR_ENV='1', ZLOOP_PROFILE='', ZLOOP_SESSION='', CLAUDE_CODE_EFFORT_LEVEL='xhigh'))
        environment.start()
        self.addCleanup(environment.stop)
        self.config = self.root / 'executors.json'
        self.config.write_text(json.dumps({
            'runtimes': {'claude': {'executors': {'claude': {'type': 'subagent'}}}},
            'models': {'sonnet': {'description': 'Sonnet worker', 'runtimes': {'claude': {
                'id': 'sonnet', 'thinking': {'default': 'medium', 'allowed': ['medium', 'high']}}}}},
            'agents': {'builder': {'model': 'sonnet'}}}))

    def route(self):
        return models.resolve_execution(self.root, self.root / 'models.json', self.config,
                                        'claude', 'builder', {}, job='ticket')

    def test_sonnet_launch_controls_do_not_inherit_an_effort_outside_the_allowed_set(self):
        result = self.route()
        self.assertEqual(result['executor'], 'herdr')
        self.assertEqual(result['executor_type'], 'herdr')
        self.assertFalse(result['native_dispatch'])
        self.assertEqual(result['executor_context'], 'herdr')
        self.assertEqual(result['target_runtime'], 'claude')
        self.assertEqual(result['settings'], {'model': 'sonnet', 'effort': 'medium'})
        self.assertEqual(result['thinking_allowed'], ['medium', 'high'])
        self.assertEqual(result['launch'], {
            'writes': 'worktree',
            'argv': ['--permission-mode', 'dontAsk', '--allowedTools',
                     'Read,Edit,Write,Bash,Grep,Glob,Skill,SendMessage,Monitor,TaskStop',
                     '--model', 'sonnet', '--effort', 'medium'],
            'verify': {'permission_mode': 'dontAsk'}})
        self.assertEqual(result['executor_guide'], str(models.ROOT / 'hosts/herdr.md'))

    def test_explicit_claude_effort_without_herdr_names_a_working_next_step(self):
        with patch.dict(os.environ, {'HERDR_ENV': ''}):
            with self.assertRaisesRegex(ValueError, 'start the orchestrator in Herdr and resolve again.*'
                                        'or remove the effort that .*thinking sets to run natively at the session effort'):
                self.route()


if __name__ == '__main__':
    unittest.main()
