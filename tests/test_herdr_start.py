#!/usr/bin/env python3
"""Saved routes reach Herdr as argument lists, with no shell serialization."""
import json
import os
import signal
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import time
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

    def start(self, worktree=None, name='builder-05'):
        return subprocess.run([sys.executable, str(support.HOOKS / 'herdr_launch.py'), 'start',
                               '--route', str(self.route), '--worktree', worktree or str(self.worktree),
                               '--assignment', 'workstream/05', '--role', 'builder', '--name', name],
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

    def test_a_name_herdr_would_refuse_is_refused_before_any_tab_opens(self):
        # Herdr refuses such a name only at agent start, after the tab exists; start checks it first.
        for name in ('bsrevB', '1worker', 'a' * 33, 'has space'):
            with self.subTest(name=name):
                result = self.start(name=name)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('lowercase letter', result.stderr)
                self.assertFalse(self.calls.exists(), 'no Herdr call before the name is valid')
        self.assertEqual(self.start(name='bs-rev_11').returncode, 0)

    def test_a_relative_worktree_opens_the_tab_in_that_folder_of_the_caller_s_checkout(self):
        result = self.start(worktree='.worktrees/worker with spaces')
        self.assertEqual(result.returncode, 0, result.stderr)
        create = json.loads(self.calls.read_text().splitlines()[0])
        self.assertEqual(create[create.index('--cwd') + 1], str(self.worktree))

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


class OpenCodeStart(unittest.TestCase):
    start = HerdrStart.start
    def setUp(self):
        HerdrStart.setUp(self)
        self.body = {'agent': 'build', 'model': {'providerID': 'openai', 'id': 'fixture', 'variant': 'high'},
                     'permissions': [{'action': 'subagent', 'resource': '*', 'effect': 'deny'}]}
        self.route.write_text(json.dumps({'target_runtime': 'opencode', 'launch': {
            'api': {'method': 'POST', 'path': '/api/session', 'body': self.body}}}))
        self.config = self.root / 'user config'
        self.integration = self.config / 'opencode'
        (self.integration / 'plugins').mkdir(parents=True)
        (self.integration / 'plugins/herdr-agent-state.js').write_text('server reporter')
        (self.integration / 'herdr-tui-session.js').write_text('tui reporter')
        (self.integration / 'herdr-opencode').mkdir()
        (self.integration / 'herdr-opencode/tui.js').write_text('v2 entrypoint')
        (self.integration / 'opencode.json').write_text('private user configuration')
        self.events = self.root / 'opencode.jsonl'
        self.owned_events = {}
        self.release = self.root / 'release-tui'
        self.env.update(XDG_CONFIG_HOME=str(self.config), XDG_DATA_HOME=str(self.root / 'user data'),
                        EVENTS=str(self.events), RELEASE=str(self.release))
        self.tab_env = self.root / 'tab-env.json'
        self.env['TAB_ENV'] = str(self.tab_env)
        stub = self.bin / 'herdr'
        stub.write_text(f'#!{sys.executable}\n' + '''import json, os, sys, subprocess
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ['CALLS']).open('a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args[:2] == ['tab', 'create']:
    env = dict(item.split('=', 1) for at, item in enumerate(args) if at and args[at-1] == '--env')
    Path(os.environ['TAB_ENV']).write_text(json.dumps(env))
    print(json.dumps({'result': {'root_pane': {'pane_id': 'root-pane'}, 'tab_id': 'worker-tab'}}))
elif args[:2] == ['agent', 'start']:
    if os.environ.get('HERDR_FAIL'):
        print('invalid Herdr response' if os.environ.get('HERDR_FAIL_JSON') else
              json.dumps({'error': {'code': 'agent_not_ready'}}))
        sys.exit(7)
    if args[args.index('--') + 1:][::2] != ['--server', '--session']:
        print('invalid attachment arguments'); sys.exit(7)
    subprocess.Popen(['opencode', *args[args.index('--') + 1:]], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        env=dict(os.environ, **json.loads(Path(os.environ['TAB_ENV']).read_text())))
    print(json.dumps({'result': {'agent': {'name': args[2], 'pane_id': 'root-pane'}}}))
elif args[:2] == ['pane', 'get']:
    if Path(os.environ['RELEASE']).exists():
        print(json.dumps({'error': {'code': 'pane_not_found'}})); sys.exit(1)
    print(json.dumps({'result': {'pane': {'pane_id': 'root-pane'}}}))
else:
    sys.exit(9)
''')
        stub = self.bin / 'opencode'
        stub.write_text(f'#!{sys.executable}\n' + '''import json, os, sys, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
import signal
args = sys.argv[1:]
config = Path(os.environ['XDG_CONFIG_HOME'])
record = {'args': args, 'pid': os.getpid(), 'config': str(config),
          'state': os.environ.get('XDG_STATE_HOME'), 'data': os.environ.get('XDG_DATA_HOME'),
          'password': bool(os.environ.get('OPENCODE_PASSWORD')), 'pane': os.environ.get('HERDR_PANE_ID'), 'tab': os.environ.get('HERDR_TAB_ID'),
          'parent': os.getppid(), 'sid': os.getsid(0),
          'quiet': all(os.fstat(fd).st_rdev == os.stat(os.devnull).st_rdev for fd in (0, 1, 2)),
          'overrides': {key: os.environ[key] for key in ('OPENCODE_CONFIG', 'OPENCODE_CONFIG_CONTENT',
                        'OPENCODE_CONFIG_DIR', 'OPENCODE_TUI_CONFIG') if key in os.environ},
          'files': {str(p.relative_to(config)): p.read_text() for p in config.rglob('*') if p.is_file()}}
with Path(os.environ['EVENTS']).open('a') as stream:
    stream.write(json.dumps(record) + '\\n')
if args[0] == 'serve':
    if os.environ.get('FAIL_STEP') == 'serve': sys.exit(9)
    if os.environ.get('HERDR_FAIL'):
        def stop(*args): time.sleep(.3); sys.exit(0)
        signal.signal(signal.SIGTERM, stop)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
        def log_message(self, *args): pass
    HTTPServer(('127.0.0.1', int(args[args.index('--port') + 1])), Handler).serve_forever()
elif args[0] == 'api':
    if os.environ.get('FAIL_STEP') == 'session POST': sys.exit(9)
    print(json.dumps({'data': {'id': 'private-session'}}))
else:
    while config.exists() and not Path(os.environ['RELEASE']).exists(): time.sleep(.02)
''')
        stub.chmod(0o755)
        self.addCleanup(self.stop_keeper)

    def stop_keeper(self):
        self.release.touch()
        if self.events.exists():
            self.owned_events.update({event['pid']: event for event in
                (json.loads(line) for line in self.events.read_text().splitlines())})
        private_folders = {Path(event['config']).parent for event in self.owned_events.values()}
        for private in private_folders:
            if (private / 'pid').exists():
                try:
                    os.kill(int((private / 'pid').read_text()), signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for event in self.owned_events.values():
            command = subprocess.run(['ps', '-p', str(event['pid']), '-o', 'command='],
                                     capture_output=True, text=True).stdout
            if str(self.bin / 'opencode') in command:
                try:
                    os.kill(event['pid'], signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 5
        while any(private.exists() for private in private_folders) and time.monotonic() < deadline:
            time.sleep(.02)
        for private in private_folders:
            shutil.rmtree(private, ignore_errors=True)

    def wait_events(self, count):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            events = [json.loads(line) for line in self.events.read_text().splitlines()] if self.events.exists() else []
            self.owned_events.update({event['pid']: event for event in events})
            if len(events) >= count:
                return events
            time.sleep(.02)
        self.fail(f'expected {count} OpenCode operations; got {events}')

    def test_api_route_runs_private_server_session_and_tui_in_worker_pane(self):
        result = self.start()
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        agent = next(call for call in calls if call[:2] == ['agent', 'start'])
        self.assertEqual(agent[agent.index('--') + 1:][::2], ['--server', '--session'],
                         'Herdr starts opencode itself; only attachment flags may follow --')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        response = json.loads(result.stdout)
        self.assertEqual(response['result']['agent']['pane_id'], 'root-pane')
        connection = response['opencode']
        self.assertEqual(connection['session'], 'private-session')
        self.assertTrue(connection['server'].startswith('http://127.0.0.1:'))
        password = Path(connection['password_file'])
        self.assertEqual(password.stat().st_mode & 0o777, 0o600)
        self.assertGreaterEqual(len(password.read_text()), 32)
        self.assertNotIn(password.read_text(), result.stdout)
        serve, api, tui = self.wait_events(3)
        self.assertEqual(serve['args'][:3], ['serve', '--hostname', '127.0.0.1'])
        self.assertEqual(serve['data'], str(self.root / 'user data'))
        self.assertNotEqual(serve['config'], str(self.config))
        self.assertNotEqual(serve['state'], self.env.get('XDG_STATE_HOME'))
        self.assertTrue(serve['password'])
        self.assertEqual(serve['pane'], 'root-pane')
        self.assertEqual(serve['tab'], 'worker-tab')
        self.assertEqual(serve['sid'], serve['parent'], 'server belongs to its detached keeper session')
        self.assertTrue(serve['quiet'], 'server streams must all use /dev/null')
        self.assertEqual(set(serve['files']), {'opencode/plugins/herdr-agent-state.js',
                                             'opencode/herdr-tui-session.js', 'opencode/tui.jsonc',
                                             'opencode/herdr-opencode/tui.js', 'opencode/cli.json',
                                             'opencode/opencode.json'})
        self.assertEqual(serve['files']['opencode/plugins/herdr-agent-state.js'], 'server reporter')
        self.assertEqual(serve['files']['opencode/herdr-tui-session.js'], 'tui reporter')
        self.assertIn('herdr-tui-session.js', serve['files']['opencode/tui.jsonc'])
        self.assertEqual(serve['files']['opencode/herdr-opencode/tui.js'], 'v2 entrypoint')
        self.assertEqual(json.loads(serve['files']['opencode/cli.json']), {'plugins': ['./herdr-opencode']})
        self.assertEqual(json.loads(serve['files']['opencode/opencode.json']), {'permission': {
            'edit': 'allow', 'bash': 'allow', 'webfetch': 'allow', 'external_directory': 'allow'}})
        tab_env = json.loads(self.tab_env.read_text())
        self.assertEqual(set(tab_env), {'ZLOOP_WORKER', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'OPENCODE_PASSWORD'})
        self.assertEqual(tab_env['ZLOOP_WORKER'], 'workstream/05')
        self.assertEqual(tab_env['XDG_CONFIG_HOME'], serve['config'])
        self.assertEqual(tab_env['XDG_STATE_HOME'], serve['state'])
        self.assertTrue(tab_env['OPENCODE_PASSWORD'] == password.read_text())
        self.assertEqual(password.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(api['args'][:3], ['api', 'POST', '/api/session'])
        self.assertEqual(json.loads(api['args'][api['args'].index('-d') + 1]),
                         dict(self.body, location={'directory': str(self.worktree)}))
        self.assertEqual(tui['args'], ['--server', connection['server'], '--session', 'private-session'])
        self.assertEqual(tui['config'], serve['config'])

    def test_inherited_config_overrides_cannot_load_the_users_configuration(self):
        for key in ('OPENCODE_CONFIG', 'OPENCODE_CONFIG_CONTENT', 'OPENCODE_CONFIG_DIR', 'OPENCODE_TUI_CONFIG'):
            self.env[key] = 'user configuration override'
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for event in self.wait_events(3):
            self.assertEqual(event['overrides'], {}, 'private server and TUI must ignore inherited config overrides')

    def test_pane_disappearance_and_keeper_signals_stop_server_and_remove_private_folder(self):
        for ending in ('pane gone', signal.SIGHUP, signal.SIGTERM):
            with self.subTest(ending=ending):
                self.release.unlink(missing_ok=True)
                self.events.unlink(missing_ok=True)
                result = self.start()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                private = Path(json.loads(result.stdout)['opencode']['password_file']).parent
                serve, _, _ = self.wait_events(3)
                if ending == 'pane gone':
                    self.release.touch()
                else:
                    os.kill(int((private / 'pid').read_text()), ending)
                deadline = time.monotonic() + 6
                while private.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertFalse(private.exists(), 'pane closure and keeper signals must remove private files')
                with self.assertRaises(ProcessLookupError, msg='keeper must stop and reap its server'):
                    os.kill(serve['pid'], 0)
                self.release.touch()

    def test_missing_any_integration_file_refuses_before_creating_tab(self):
        for name in ('plugins/herdr-agent-state.js', 'herdr-tui-session.js', 'herdr-opencode/tui.js'):
            with self.subTest(name=name):
                path = self.integration / name
                contents = path.read_text()
                path.unlink()
                result = self.start()
                path.write_text(contents)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('herdr integration install opencode', result.stderr)
                self.assertFalse(self.calls.exists(), 'integration must be checked before any Herdr call')

    def test_failed_server_or_session_names_step_and_cleans_up(self):
        for step, operation_count in (('serve', 1), ('session POST', 2)):
            with self.subTest(step=step):
                self.events.unlink(missing_ok=True)
                self.env['FAIL_STEP'] = step
                result = self.start()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('OpenCode ' + step + ' failed:', result.stdout)
                events = self.wait_events(operation_count)
                self.assertFalse(Path(events[0]['config']).parent.exists())
                with self.assertRaises(ProcessLookupError, msg='failed setup must reap the server'):
                    os.kill(events[0]['pid'], 0)

    def test_failed_pane_lookup_stops_server_and_removes_private_folder(self):
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        serve, _, _ = self.wait_events(3)
        stub = self.bin / 'herdr'
        stub.write_text(stub.read_text().replace("if args[:2] == ['tab', 'create']:",
            "if args[:2] == ['pane', 'get']:\n"
            "    print(json.dumps({'error': {'code': 'connection_lost'}})); sys.exit(1)\n"
            "if args[:2] == ['tab', 'create']:"))
        private = Path(serve['config']).parent
        deadline = time.monotonic() + 6
        while private.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(private.exists(), 'failed keeper lookup must not retain configuration or password')
        with self.assertRaises(ProcessLookupError):
            os.kill(serve['pid'], 0)

    def test_failed_herdr_start_waits_for_keeper_cleanup_before_returning(self):
        self.env['HERDR_FAIL'] = '1'
        result = self.start()
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {'error': {'code': 'agent_not_ready'},
            'opencode_error': 'OpenCode Herdr agent start failed'})
        serve, _ = self.wait_events(2)
        self.assertFalse(Path(serve['config']).parent.exists(), 'failed start must finish server cleanup')
        with self.assertRaises(ProcessLookupError):
            os.kill(serve['pid'], 0)

    def test_unparseable_herdr_response_names_step_and_still_stops_keeper(self):
        self.env.update(HERDR_FAIL='1', HERDR_FAIL_JSON='1')
        result = self.start()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('OpenCode Herdr agent start failed', result.stdout)
        serve, _ = self.wait_events(2)
        self.assertFalse(Path(serve['config']).parent.exists(), 'Herdr response errors must still clean up')



if __name__ == '__main__':
    unittest.main()
