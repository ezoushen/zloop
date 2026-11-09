#!/usr/bin/env python3
"""A launch on macOS restricts a worker's writes with a Seatbelt profile when its host runtime cannot: native
children and the bash executor get a generated profile; Herdr workers get the CLI's built-in sandbox flags; T3
workers are documented as provider-default. A missing or unenforcing sandbox-exec falls back to the tool
allowlist, with the reason recorded."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support


models = support.load_script('agent_models.py', 'agent_models')
sandbox = support.load_script('seatbelt.py', 'seatbelt')


class Machine(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop seatbelt ')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.worktree = self.root / 'wt'
        self.reports = self.root / 'reports'
        self.worktree.mkdir()
        self.reports.mkdir()

    def stub_sandbox(self, *, present=True, enforcing=True):
        directory = self.root / 'bin'
        directory.mkdir(exist_ok=True)
        self.enforcing = enforcing
        if present:
            executable = directory / 'sandbox-exec'
            probe = ('sys.exit(1)' if enforcing else
                     'sys.exit(0)')
            # The probe writes nothing when enforcing; when not, it touches the target file.
            executable.write_text('#!' + sys.executable + '\n' + f'''
import sys
args = sys.argv[1:]
if args and args[0] == "-f" and len(args) > 3 and "zloop-sandbox-probe" in args[3]:
    import pathlib
    if {self.enforcing!r}:
        sys.exit(1)
    pathlib.Path(args[3]).write_text("x")
    sys.exit(0)
sys.exit(0)
''')
            executable.chmod(stat.S_IRWXU)
        import shutil
        import shutil
        bare = str(directory) if present else tempfile.mkdtemp(prefix='zloop no-sandbox ')
        env = dict(os.environ, PATH=bare + os.pathsep + '/bin' if present
                   else bare + os.pathsep + '/bin')
        return patch.dict(os.environ, env)


class ProfileGeneration(Machine):
    def test_a_read_only_scope_allows_temp_and_reports_but_never_the_worktree(self):
        # Expected values from the verified skeleton in .scratch/t3code-support/macos-sandbox.md §4.
        profile = sandbox.profile('none', worktree=str(self.worktree), reports=str(self.reports))
        self.assertIn('(deny file-write*)', profile)
        self.assertIn('(subpath "/private/tmp")', profile)
        self.assertIn('(subpath "/tmp")', profile)
        self.assertIn('(literal "/dev/null")', profile)
        self.assertNotIn(str(self.worktree), profile)

    def test_a_worktree_scope_adds_the_assigned_worktree(self):
        profile = sandbox.profile('worktree', worktree=str(self.worktree), reports=str(self.reports))
        self.assertIn(f'(subpath "{self.worktree}")', profile)

    def test_unknown_scope_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'scope'):
            sandbox.profile('everywhere', worktree=str(self.worktree), reports=str(self.reports))


class Preflight(Machine):
    def test_an_enforcing_probe_passes_and_records_no_fallback(self):
        self.worktree.rmdir()
        with self.stub_sandbox(present=True, enforcing=True):
            result = sandbox.available(worktree=str(self.worktree))
        self.assertEqual((result['available'], result.get('fallback_reason')), (True, None))

    def test_a_missing_binary_selects_the_allowlist_fallback_with_its_reason(self):
        with self.stub_sandbox(present=False):
            result = sandbox.available(worktree=str(self.worktree))
        self.assertEqual(result['available'], False)
        self.assertIn('sandbox-exec', result['fallback_reason'])

    def test_a_present_but_unenforcing_binary_selects_the_fallback(self):
        with self.stub_sandbox(present=True, enforcing=False):
            result = sandbox.available(worktree=str(self.worktree))
        self.assertEqual(result['available'], False)
        self.assertIn('enforce', result['fallback_reason'])

    def test_the_probe_writes_only_inside_its_own_scope(self):
        # The probe's outside target must live under the reports-adjacent temp dir, never the worker's tree.
        self.worktree.rmdir()
        with self.stub_sandbox(present=True, enforcing=True):
            result = sandbox.available(worktree=str(self.worktree))
        self.assertNotIn(str(self.root / 'wt'), result.get('probe', ''))


class Wrapping(Machine):
    def route(self, model, job, host='claude', *options):
        return models.resolve_execution(
            self.root, self.root / 'absent.json', self.user_executors(),
            host, 'builder' if job == 'ticket' else 'reviewer', {}, candidate=model,
            worktree=str(self.worktree), reports=str(self.reports)) if False else None

    def test_native_children_wrap_their_argv_on_darwin_when_available(self):
        # Rule: a non-native child of a runtime whose CLI the profile wraps gets sandbox-exec in front of the
        # launch argv, and the wrap is recorded so the lifecycle can verify it.
        if sys.platform != 'darwin':
            self.skipTest('Seatbelt is macOS-only')
        launch = {'writes': 'none', 'argv': ['--permission-mode', 'dontAsk'],
                  'verify': {'permission_mode': 'dontAsk'}}
        with self.stub_sandbox(present=True, enforcing=True):
            wrapped = sandbox.wrap(launch, worktree=str(self.worktree), reports=str(self.reports))
        self.assertEqual(wrapped['argv'][0], 'sandbox-exec')
        self.assertEqual(wrapped['argv'][1], '-f')
        self.assertTrue(Path(wrapped['argv'][2]).is_absolute())
        self.assertIn('(deny file-write*)', Path(wrapped['argv'][2]).read_text())
        self.assertEqual(wrapped['argv'][3:], launch['argv'])
        self.assertEqual(wrapped['verify']['sandbox_profile'], 'none')

    def test_the_bash_executor_wraps_its_worker_argv(self):
        launch = {'writes': 'worktree', 'argv': ['bash', 'worker.sh'], 'verify': {}}
        with self.stub_sandbox(present=True, enforcing=True):
            wrapped = sandbox.wrap(launch, worktree=str(self.worktree), reports=str(self.reports))
        self.assertEqual(wrapped['argv'][:3], ['sandbox-exec', '-f', wrapped['argv'][2]])
        self.assertEqual(wrapped['argv'][3:], ['bash', 'worker.sh'])

    def test_unavailable_sandbox_keeps_the_argv_and_names_the_fallback(self):
        launch = {'writes': 'none', 'argv': ['--x'], 'verify': {}}
        with self.stub_sandbox(present=False):
            wrapped = sandbox.wrap(launch, worktree=str(self.worktree), reports=str(self.reports))
        self.assertEqual(wrapped['argv'], ['--x'])
        self.assertIn('sandbox-exec', wrapped['fallback_reason'])
        self.assertNotIn('sandbox_profile', wrapped.get('verify', {}))


if __name__ == '__main__':
    unittest.main()
