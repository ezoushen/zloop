#!/usr/bin/env python3
"""The t3 route's preflight verifies the T3 environment before any launch: an installed home, a server answering,
a registered project — each layer with its own reason — and never `t3 status` or bare `t3 doctor` for liveness."""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support


preflight = support.load_script('t3_preflight.py', 't3_preflight')


class Stub:
    """A stub `t3` on PATH whose thread list answers like the real CLI: exit 1 without a server, exit 0 with a
    JSON array or a no-project message."""

    def __init__(self, root, *, server=True, project=True, raw=None, doctor_repairs=False):
        self.argv_log, self.root, self.doctor_repairs = 't3-preflight-argv.log', Path(root), doctor_repairs
        self.server, self.project, self.raw = server, project, raw
        directory = Path(root) / 'bin'
        directory.mkdir(exist_ok=True)
        self.executable = directory / 't3'
        self.executable.write_text('#!' + sys.executable + '\n' + f'''
import json, sys
args = sys.argv[1:]
with open({str(Path(root) / self.argv_log)!r}, 'a') as log:
    log.write(' '.join(args) + chr(10))
if args[:2] == ['thread', 'list'] and '--json' in args:
    if {self.raw!r} is not None:
        print({self.raw!r})
        sys.exit(0)
    if not {self.server!r}:
        print('No running T3 Code server uses /home. Start T3 Code, then retry.')
        sys.exit(1)
    if {self.project!r}:
        print(json.dumps([{{'threadId': 'th-1', 'status': 'idle'}}]))
    else:
        print('No T3 Code project matches ' + (args[-1] if args else "'.'" ) + '. Pass a project id or a path inside a project.')
    sys.exit(0)
if args[:1] == ['doctor']:
    if '--dry-run' in args:
        print('Nothing to repair.')
    else:
        print('REPAIRED' if {self.doctor_repairs!r} else 'Would repair: database.')
    sys.exit(0)
if args[:1] == ['status']:
    print('Service        not installed')
    sys.exit(0)
sys.exit(3)
''')
        self.executable.chmod(stat.S_IRWXU)
        self.env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'])

    def calls(self):
        log = Path(self.root) / self.argv_log
        return log.read_text().splitlines() if log.exists() else []


class Home(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop t3 preflight ')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.project = self.root / 'repo'
        self.project.mkdir()


class LayeredGate(Home):
    def verify(self, home, stub=None):
        env = stub.env if stub is not None else getattr(self, 'stub', Stub(self.root)).env
        with patch.dict(os.environ, env):
            return preflight.verify(self.project, home=home)

    def test_a_live_server_and_project_pass_and_probe_the_real_gates(self):
        home = self.root / 't3home'
        (home / 'userdata').mkdir(parents=True)
        (home / 'userdata' / 'server-runtime.json').write_text(json.dumps({'port': 3773, 'pid': 1}))
        self.stub = Stub(self.root)
        result = self.verify(home)
        self.assertEqual(result['ok'], True)
        self.assertEqual(result['thread_list']['ok'], True)
        calls = self.stub.calls()
        self.assertTrue(any(line.startswith('thread list') for line in calls), calls)
        # `t3 status` and bare `t3 doctor` are never liveness probes.
        self.assertFalse(any(line.startswith('status') for line in calls), calls)
        self.assertFalse(any(line == 'doctor' for line in calls), calls)

    def test_each_failure_layer_names_its_own_reason(self):
        self.stub = Stub(self.root)
        with patch.dict(os.environ, self.stub.env):
            # No home: installed gate fails before any CLI call.
            missing_home = self.verify(self.root / 'absent-home', self.stub)
            self.assertEqual((missing_home['ok'], missing_home['reason']),
                             (False, 'home'))
            self.assertEqual(self.stub.calls(), [])
            home = self.root / 't3home'
            (home / 'userdata').mkdir(parents=True)
            (home / 'userdata' / 'server-runtime.json').write_text('{}')
            # Home present, no server answering.
            self.stub = Stub(self.root, server=False)
            no_server = self.verify(home, self.stub)
            self.assertEqual((no_server['ok'], no_server['reason']), (False, 'server'))
            # Server answering, project not registered.
            self.stub = Stub(self.root, project=False)
            no_project = self.verify(home, self.stub)
            self.assertEqual((no_project['ok'], no_project['reason']), (False, 'project'))

    def test_exit_zero_with_an_unparseable_body_is_not_success(self):
        home = self.root / 't3home'
        (home / 'userdata').mkdir(parents=True)
        (home / 'userdata' / 'server-runtime.json').write_text('{}')
        self.stub = Stub(self.root, raw='garbage')
        result = self.verify(home, self.stub)
        self.assertEqual((result['ok'], result['reason']), (False, 'body'))

    def test_liveness_never_runs_bare_doctor_even_when_asked_for_diagnostics(self):
        # A diagnostic call must carry --dry-run; the gate itself never calls doctor at all.
        home = self.root / 't3home'
        (home / 'userdata').mkdir(parents=True)
        (home / 'userdata' / 'server-runtime.json').write_text('{}')
        self.stub = Stub(self.root, project=False, doctor_repairs=True)
        with patch.dict(os.environ, self.stub.env):
            result = self.verify(home, self.stub)
            self.assertEqual(result['ok'], False)
            diagnostics = preflight.diagnose(self.project, home=home)
        self.assertNotIn('REPAIRED', str(diagnostics))
        self.assertTrue(any('doctor' in line and '--dry-run' in line for line in self.stub.calls()),
                        self.stub.calls())


if __name__ == '__main__':
    unittest.main()
