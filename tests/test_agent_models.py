#!/usr/bin/env python3
"""Preference precedence and native profile loading, isolated from personal settings."""

import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import support


SCRIPT = support.script('agent_models.py')
models = support.load_script('agent_models.py', 'agent_models')


class Preferences(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop models ')
        self.addCleanup(tmp.cleanup)
        # Routing reads the machine's allowance; an empty home keeps this machine's usage out of the result.
        machine = tempfile.TemporaryDirectory(prefix='zloop machine ')
        self.addCleanup(machine.cleanup)
        environment = patch.dict(os.environ, support.isolated_env(machine.name, ZLOOP_PROFILE='', HERDR_ENV=''))
        environment.start()
        self.addCleanup(environment.stop)
        self.root = Path(tmp.name)
        self.user = self.root / 'user/models.json'
        self.project = self.root / '.zloop/models.json'

    def resolve(self, harness='codex', agent='reviewer', **task):
        return models.resolve(self.root, self.user, harness, agent, task)

    def write_layers(self):
        self.user.parent.mkdir()
        self.project.parent.mkdir()
        self.user.write_text(json.dumps({'codex': {
            'defaults': {'model': 'personal-model', 'effort': 'low'},
            'reviewer': {'effort': 'high'}}}))
        self.project.write_text(json.dumps({'codex': {
            'defaults': {'effort': 'medium'},
            'reviewer': {'model': 'project-model'}}}))

    def test_reads_without_configuration_return_nothing_and_write_nothing(self):
        for harness in models.HARNESSES:
            for agent in models.AGENTS:
                result = self.resolve(harness, agent)
                assert result['settings'] == result['sources'] == {}
                if harness in ('claude', 'opencode'):
                    assert models.native_profile(result) is None
            assert models.resolve_execution(self.root, self.user, self.root / 'executors.json', harness,
                                            'reviewer', {})['executor'] == 'native'
        assert list(self.root.iterdir()) == []  # Reads do not opt the user in.

    def test_task_project_and_user_layers_take_precedence(self):
        self.write_layers()
        before = (self.user.read_bytes(), self.project.read_bytes())
        result = self.resolve()
        assert result['settings'] == {'model': 'project-model', 'effort': 'medium'}
        assert result['sources']['effort'] == f'{self.project}:codex.defaults'
        assert self.resolve(model='task-model')['settings'] == {'model': 'task-model', 'effort': 'medium'}
        assert self.resolve(model=None)['settings'] == {'effort': 'medium'}
        assert self.resolve(model=None)['sources']['model'] == 'task'
        assert self.resolve('pi')['settings'] == {}
        assert self.resolve(agent='builder')['settings'] == {'model': 'personal-model', 'effort': 'medium'}
        assert before == (self.user.read_bytes(), self.project.read_bytes())

        self.project.write_text(json.dumps({'codex': {'reviewer': {'model': None, 'effort': None}}}))
        assert self.resolve()['settings'] == {}
        self.project.write_text('{}')  # Removing the override exposes the user layer.
        assert self.resolve()['settings'] == {'model': 'personal-model', 'effort': 'high'}

    def test_invalid_configuration_is_rejected_and_left_unchanged(self):
        self.project.parent.mkdir()
        for data in ('{broken', '[]', '{"claud": {}}', '{"codex":{"implementer":{}}}',
                     '{"pi":{"builder":{"thinking":"high"}}}',
                     '{"claude":{"builder":{"model":false}}}',
                     '{"codex":{"builder":{"effort":"magic"}}}',
                     '{"pi":{"builder":{"model":"bad\\nmodel"}}}'):
            self.project.write_text(data)
            with self.assertRaises(ValueError, msg=data):
                self.resolve()
            assert self.project.read_text() == data

    def test_claude_profiles_carry_agent_tools_principles_and_requested_settings(self):
        for agent in models.AGENTS:
            source = (models.ROOT / 'agents' / f'{agent}.md').read_text()
            _, header, body = source.split('---', 2)
            assert not any(line.startswith(('model:', 'effort:')) for line in header.splitlines())
            result = self.resolve('claude', agent, model='opus', effort='high')
            profile = models.native_profile(result)
            text = profile['content']
            assert 'model: "opus"' in text and 'effort: "high"' in text
            # The agent's own body is the system prompt; zloop's keys stay out of Claude's frontmatter.
            assert text.endswith(body) and '## Principles' in text
            assert '\nwrites:' not in text and '\ncore_skills:' not in text
            for line in source.splitlines():
                if line.startswith(('tools: ', 'disallowedTools: ')):
                    key, value = line.split(': ', 1)
                    assert f'{key}: {json.dumps(value)}' in text
            assert profile == models.native_profile(result)
            assert profile['name'] != models.native_profile(self.resolve('claude', agent, effort='low'))['name']
            model_only = models.native_profile(self.resolve('claude', agent, model='opus'))['content']
            assert 'model: "opus"' in model_only and '\neffort:' not in model_only
            effort_only = models.native_profile(self.resolve('claude', agent, effort='high'))['content']
            assert 'effort: "high"' in effort_only and '\nmodel:' not in effort_only
        with self.assertRaises(ValueError, msg='Claude accepted ultra'):
            models.native_profile(self.resolve('claude', effort='ultra'))

    def test_opencode_profile_maps_effort_to_variant(self):
        profile = models.native_profile(self.resolve('opencode', model='openai/gpt-5', effort='high'))
        assert 'mode: "subagent"' in profile['content'] and 'variant: "high"' in profile['content']
        assert 'model:' not in models.native_profile(self.resolve('opencode', effort='high'))['content']

    def test_cli_rejects_profile_without_route_and_malformed_arguments(self):
        self.write_layers()
        self.project.write_text('{}')
        args = [sys.executable, '-B', str(SCRIPT), '--repo', str(self.root), '--user-config', str(self.user),
                '--harness', 'codex', '--agent', 'reviewer']
        cli = subprocess.run(args + ['--model', 'native'], capture_output=True, text=True, check=True)
        assert json.loads(cli.stdout)['settings'] == {'effort': 'high'}
        assert subprocess.run(args + ['--profile'], capture_output=True).returncode != 0
        assignment = self.root / 'assignment.txt'
        assignment.write_text('Review the pinned range.\n')
        args = args[:-2] + ['--job', 'diff-review']
        for value in ('Assignment prose instead of a file path.', 'long assignment ' * 30, str(self.root)):
            failed = subprocess.run(args + ['--route', '--user-executors', str(self.root / 'missing.json'),
                                            '--assignment', value], capture_output=True, text=True)
            assert failed.returncode == 2, failed.stdout + failed.stderr
            assert '--assignment requires a readable UTF-8 text file path' in failed.stderr, failed.stderr
        for flag, values in (('--eligible-candidates', ('luna,sol', '"sol"', 'null')),
                             ('--eligible-thinking', ('opus=medium', '["medium"]')),
                             ('--route-choice', ('native', 'null'))):
            for value in values:
                failed = subprocess.run(args + ['--route', '--user-executors', str(self.root / 'missing.json'),
                                                '--assignment', str(assignment), flag, value],
                                        capture_output=True, text=True)
                assert failed.returncode == 2, failed.stdout + failed.stderr
                assert flag + ' expects inline JSON, for example' in failed.stderr, failed.stderr

    def test_a_launch_route_needs_a_job_which_selects_its_agent(self):
        base = [sys.executable, '-B', str(SCRIPT), '--repo', str(self.root), '--user-config', str(self.user),
                '--harness', 'codex', '--route', '--user-executors', str(self.root / 'missing.json')]
        failed = subprocess.run(base + ['--agent', 'reviewer'], capture_output=True, text=True)
        assert failed.returncode == 2 and 'a launch route requires --job' in failed.stderr, failed.stderr
        listed = subprocess.run(base + ['--agent', 'reviewer', '--list-candidates'], capture_output=True, text=True)
        assert listed.returncode == 0, listed.stderr
        launched = json.loads(subprocess.run(base + ['--job', 'diff-review'], capture_output=True, text=True,
                                             check=True).stdout)
        assert (launched['agent'], launched['job']) == ('reviewer', 'diff-review')
        with self.assertRaisesRegex(ValueError, "Job 'ticket' is done by builder, not reviewer"):
            models.resolve_execution(self.root, self.user, self.root / 'missing.json', 'codex', 'reviewer', {},
                                     job='ticket')

def check_opencode_profile(root, env, name):
    """Read the loaded profile from an isolated OpenCode 2 service (no model calls)."""
    def opencode(*args):
        return subprocess.run(['opencode', *args], cwd=root, env=env, capture_output=True, text=True, timeout=60)
    # The CLI talks to a background service on a fixed port the user's own service already holds.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    assert opencode('service', 'set', 'port', str(port)).returncode == 0
    try:
        # A directory's first request can answer before its agents load; built-in `build` marks loaded.
        for _ in range(20):
            result = opencode('api', 'GET', '/api/agent', '-H', f'x-opencode-directory:{root}')
            assert result.returncode == 0, result.stdout + result.stderr
            agents = {agent['id']: agent for agent in json.loads(result.stdout)['data']}
            if 'build' in agents:
                break
            time.sleep(0.5)
        assert name in agents, (name, sorted(agents))
        loaded = agents[name]
        assert loaded['model'] == {'providerID': 'openai', 'id': 'gpt-5', 'variant': 'high'}, loaded
        assert loaded['mode'] == 'subagent' and 'job_file' in loaded['system']
    finally:
        opencode('service', 'stop')


@unittest.skipUnless(os.environ.get('ZLOOP_NATIVE') == '1', 'set ZLOOP_NATIVE=1 to check installed CLIs')
class NativeProfiles(unittest.TestCase):
    def test_claude_validates_and_opencode_loads_generated_profiles(self):
        with tempfile.TemporaryDirectory(prefix='zloop native models ') as tmp:
            root = Path(tmp)
            env = dict(os.environ)
            for key, directory in [('CLAUDE_CONFIG_DIR', 'claude-config'), ('XDG_CONFIG_HOME', 'config'),
                                   ('XDG_CACHE_HOME', 'cache'), ('XDG_DATA_HOME', 'data'),
                                   ('XDG_STATE_HOME', 'state')]:
                env[key] = str(root / directory)
                Path(env[key]).mkdir()
            for harness in ('claude', 'opencode'):
                for agent in models.AGENTS:
                    settings = {'model': 'opus' if harness == 'claude' else 'openai/gpt-5', 'effort': 'high'}
                    profile = models.native_profile({'harness': harness, 'agent': agent, 'settings': settings})
                    path = root / profile['path']
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(profile['content'])
                    if harness == 'claude':
                        for partial in ({'model': 'opus'}, {'effort': 'high'}):
                            profile = models.native_profile({'harness': harness, 'agent': agent, 'settings': partial})
                            (root / profile['path']).write_text(profile['content'])
                    if harness == 'opencode':
                        check_opencode_profile(root, env, profile['name'])
            result = subprocess.run(['claude', 'plugin', 'validate', '--strict', str(root / '.claude/agents')],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    unittest.main()
