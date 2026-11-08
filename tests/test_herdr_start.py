#!/usr/bin/env python3
"""Saved routes reach Herdr as argument lists, with no shell serialization."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

import support


class HerdrStart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='zloop start ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / 'project with spaces'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        self.worktree = self.repo / '.worktrees/worker with spaces'
        self.worktree.mkdir(parents=True)
        self.route = self.root / 'saved route.json'
        self.argv = ['-c', 'default_permissions="zloop_worktree"', '-c',
                     'permissions.zloop_worktree.filesystem={":root"="read",":tmpdir"="write",'
                     + json.dumps(str(self.worktree)) + '="write"}', '-a', 'never', '-m', 'fixture-model']
        self.route.write_text(json.dumps({'host': 'opencode', 'target_runtime': 'codex',
                                         'launch': {'argv': self.argv}}))
        self.calls = self.root / 'calls.jsonl'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        stub = self.bin / 'herdr'
        stub.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ['CALLS']).open('a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args[:2] == ['tab', 'create']:
    print(json.dumps({'result': {'root_pane': {'pane_id': 'root-pane'}, 'tab_id': 'worker-tab'}}))
elif args[:2] == ['agent', 'start']:
    print(json.dumps({'result': {'agent': {'name': args[2], 'pane_id': 'root-pane'}}}))
else:
    sys.exit(9)
''')
        stub.chmod(0o755)
        self.env = support.isolated_env(self.root, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                                        CALLS=str(self.calls), HERDR_WORKSPACE_ID='orchestrator-workspace',
                                        HERDR_PANE_ID='orchestrator-pane')
        self.env = support.without_host_sessions(self.env)
        self.env['OPENCODE_SESSION_ID'] = 'opencode-host'
        self.env.pop('HERDR_SOCKET_PATH', None)

    def start(self):
        return subprocess.run([sys.executable, str(support.HOOKS / 'herdr_launch.py'), 'start',
                               '--route', str(self.route), '--worktree', str(self.worktree),
                               '--assignment', 'workstream/05', '--role', 'builder', '--name', 'builder-05',
                               '--session', 'opencode-host'],
                              cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=10)

    def test_saved_route_preserves_spaces_and_toml_through_tab_and_agent_start(self):
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.calls.exists(), 'start must call Herdr')
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(calls, [
            ['tab', 'create', '--workspace', 'orchestrator-workspace', '--cwd', str(self.worktree),
             '--label', 'workstream/05 builder', '--env', 'ZLOOP_WORKER=workstream/05', '--no-focus'],
            ['agent', 'start', 'builder-05', '--kind', 'codex', '--pane', 'root-pane', '--', *self.argv]])
        filesystem = tomllib.loads(calls[1][calls[1].index('--') + 4])['permissions']['zloop_worktree']['filesystem']
        self.assertEqual(filesystem, {':root': 'read', ':tmpdir': 'write', str(self.worktree): 'write'})
        self.assertEqual(json.loads(result.stdout)['result']['agent']['pane_id'], 'root-pane')

    def test_start_logs_the_launch_before_herdr_can_fail_for_the_stop_check(self):
        stub = self.bin / 'herdr'
        stub.write_text(stub.read_text().replace(
            "elif args[:2] == ['agent', 'start']:",
            "elif args[:2] == ['agent', 'start']:\n"
            "    log = Path(os.environ['LAUNCH_LOG'])\n"
            "    Path(os.environ['OBSERVED']).write_text(log.read_text() if log.exists() else '')\n"
            "    print(json.dumps({'error': {'code': 'agent_not_ready'}}))\n"
            "    sys.exit(7)"))
        log = self.repo / '.scratch/herdr-launches.jsonl'
        self.env['LAUNCH_LOG'] = str(log)
        observed = self.root / 'log-before-start.jsonl'
        self.env['OBSERVED'] = str(observed)
        result = self.start()
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'error': {'code': 'agent_not_ready'}})
        records = [json.loads(line) for line in observed.read_text().splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual(log.read_text(), observed.read_text())
        self.assertTrue(records[0].pop('time'))
        self.assertEqual(records, [{'agent': 'builder-05', 'pane': 'root-pane', 'cwd': str(self.worktree),
                                    'herdr_session': 'default',
                                    'orchestrator': {'pane': 'orchestrator-pane', 'session': 'opencode-host'}}])


if __name__ == '__main__':
    unittest.main()
