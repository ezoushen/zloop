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
    JSON array or a no-project message; `environment list --json` answers with `links` when given."""

    def __init__(self, root, *, server=True, project=True, raw=None, doctor_repairs=False,
                 links=None, environment_fails=False):
        self.argv_log, self.root, self.doctor_repairs = 't3-preflight-argv.log', Path(root), doctor_repairs
        self.server, self.project, self.raw = server, project, raw
        self.links, self.environment_fails = links, environment_fails
        directory = Path(root) / 'bin'
        directory.mkdir(exist_ok=True)
        self.executable = directory / 't3'
        self.executable.write_text('#!' + sys.executable + '\n' + f'''
import json, sys
args = sys.argv[1:]
with open({str(Path(root) / self.argv_log)!r}, 'a') as log:
    log.write(' '.join(args) + chr(10))
if args[:2] == ['environment', 'list'] and '--json' in args:
    if {self.environment_fails!r}:
        sys.exit(3)
    print(json.dumps({self.links!r} if {self.links!r} is not None else []))
    sys.exit(0)
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


class EnvironmentGate(Home):
    """The t3 preflight's environment layer reads `t3 environment list --json`: a route's target must be linked and
    answering, with an access level at or above the route's need (writes none needs approval-required, writes
    worktree needs auto-accept-edits). Its refusal is its own layer, `environment`, with one problem per rule:
    unlinked, unreachable or ceiling — never the local home/server/project reasons."""

    PEER = {'environmentId': 'env-peer-1', 'label': 'peer', 'urls': ['https://peer.example:3773'],
            'access': 'approval-required', 'status': 'reachable'}

    def verify(self, target, writes, links, *, fails=False):
        self.stub = Stub(self.root, links=links, environment_fails=fails)
        with patch.dict(os.environ, self.stub.env):
            return preflight.verify_environment(target, writes)

    def test_a_linked_answering_environment_passes_and_probes_the_real_source(self):
        result = self.verify('peer', 'none', [self.PEER])
        self.assertEqual(result['ok'], True)
        self.assertEqual(result['reason'], None)
        self.assertEqual((result['environment']['id'], result['environment']['label'],
                          result['environment']['access']), ('env-peer-1', 'peer', 'approval-required'))
        # The environment layer reads the JSON answer of the real command.
        self.assertIn('environment list --json', self.stub.calls())
        # The link answers by its label and by its environmentId alike.
        by_id = self.verify('env-peer-1', 'none', [self.PEER])
        self.assertEqual(by_id['ok'], True)

    def test_an_environment_that_is_not_linked_refuses_unlinked(self):
        # An empty answer and a populated one both refuse: the target is simply not among the linked ones.
        for links in ([], [self.PEER]):
            result = self.verify('nowhere', 'none', links)
            self.assertEqual((result['ok'], result['reason'], result['problem']), (False, 'environment', 'unlinked'))
            self.assertIn("'nowhere'", result['detail'])

    def test_an_environment_that_does_not_answer_refuses_unreachable(self):
        for status in ('unreachable', 'expired'):
            result = self.verify('peer', 'none', [dict(self.PEER, status=status)])
            self.assertEqual((result['ok'], result['reason'], result['problem']),
                             (False, 'environment', 'unreachable'))
            self.assertIn(status, result['detail'])
        # A listing that fails or does not answer usefully is not a linked environment either.
        failed = self.verify('peer', 'none', None, fails=True)
        self.assertEqual((failed['ok'], failed['reason'], failed['problem']), (False, 'environment', 'unreachable'))

    def test_an_access_ceiling_below_the_route_need_refuses_naming_the_level(self):
        # writes none needs approval-required: a read-only link cannot launch a thread there.
        readonly = self.verify('peer', 'none', [dict(self.PEER, access='read-only')])
        self.assertEqual((readonly['ok'], readonly['reason'], readonly['problem']),
                         (False, 'environment', 'ceiling'))
        self.assertIn('read-only', readonly['detail'])
        self.assertIn('approval-required', readonly['detail'])
        # writes worktree needs auto-accept-edits: approval-required is narrower than the route's need.
        narrow = self.verify('peer', 'worktree', [self.PEER])
        self.assertEqual((narrow['ok'], narrow['problem']), (False, 'ceiling'))
        self.assertIn('auto-accept-edits', narrow['detail'])
        # At the floor exactly, the route passes: the ceiling is inclusive.
        self.assertEqual(self.verify('peer', 'worktree', [dict(self.PEER, access='auto-accept-edits')])['ok'], True)
        self.assertEqual(self.verify('peer', 'none', [self.PEER])['ok'], True)


if __name__ == '__main__':
    unittest.main()