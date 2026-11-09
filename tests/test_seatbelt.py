#!/usr/bin/env python3
"""Seatbelt (macOS) profiles bound a worker's writes (and reads) to its scope: the bash executor wraps its worker
argv with a generated profile; the probe passes only when an inside-scope write succeeds AND an outside-scope write
fails; a missing or unenforcing sandbox selects the tool-allowlist fallback with a recorded reason."""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support


sandbox = support.load_script('seatbelt.py', 'seatbelt')
models = support.load_script('agent_models.py', 'agent_models')


class Machine(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop seatbelt ')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        # The probe writes under Path.home(); keep the real home out of tests.
        (self.root / 'home').mkdir()
        home = patch.dict(os.environ, {'HOME': str(self.root / 'home')})
        home.start()
        self.addCleanup(home.stop)
        self.worktree = self.root / 'wt'
        self.reports = self.root / 'reports'
        self.worktree.mkdir()
        self.reports.mkdir()

    def stub_sandbox(self, *, present=True, enforcing=True):
        """A sandbox-exec stub on PATH whose behavior follows the probe contract: the inside write (zloop-sandbox-
        probe-in) succeeds; the outside write (zloop-sandbox-probe) fails only when enforcing."""
        directory = self.root / 'bin'
        directory.mkdir(exist_ok=True)
        self.enforcing = enforcing
        if present:
            executable = directory / 'sandbox-exec'
            executable.write_text('#!' + sys.executable + '\n' + f'''
import sys
args = sys.argv[1:]
if args and args[0] == "-f" and len(args) > 3:
    target = args[3]
    if "zloop-sandbox-probe-in" in target:
        import pathlib
        pathlib.Path(target).write_text("x")
        sys.exit(0)
    if "zloop-sandbox-probe-" in target:
        sys.exit(1 if {self.enforcing!r} else 0)
sys.exit(0)
''')
            executable.chmod(stat.S_IRWXU)
        bare = str(directory) if present else tempfile.mkdtemp(prefix='zloop no-sandbox ')
        return patch.dict(os.environ, {'PATH': bare + os.pathsep + '/bin'})


class ProfileGeneration(Machine):
    def test_a_read_only_scope_allows_temp_and_reports_but_never_the_worktree(self):
        # Expected values from the verified profile skeleton this module's docstring cites.
        profile = sandbox.profile('none', worktree=str(self.worktree), reports=str(self.reports))
        self.assertIn('(deny default)', profile)
        self.assertIn('(subpath "/private/tmp")', profile)
        self.assertIn('(subpath "/tmp")', profile)
        self.assertIn('(literal "/dev/null")', profile)
        self.assertNotIn(str(self.worktree), profile)

    def test_a_worktree_scope_adds_the_assigned_worktree(self):
        profile = sandbox.profile('worktree', worktree=str(self.worktree), reports=str(self.reports))
        self.assertIn(f'(subpath "{os.path.realpath(self.worktree)}")', profile)

    def test_read_bounding_denies_home_reads_outside_the_scope(self):
        # Rule (SR-04, ticket 02 crit 4): reads outside the scope's subtrees are denied — credential files unreadable.
        profile = sandbox.profile('worktree', worktree=str(self.worktree), reports=str(self.reports))
        self.assertIn('(deny default)', profile)
        self.assertIn(f'(deny file-read* (subpath "{os.path.realpath(Path.home())}"))', profile)
        self.assertIn(f'(subpath "{os.path.realpath(self.worktree)}")', profile)

    def test_unknown_scope_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'scope'):
            sandbox.profile('everywhere', worktree=str(self.worktree), reports=str(self.reports))


class Preflight(Machine):
    def test_an_enforcing_probe_passes_and_records_no_fallback(self):
        with self.stub_sandbox(present=True, enforcing=True):
            result = sandbox.available(worktree=str(self.worktree))
        self.assertEqual((result['available'], result.get('fallback_reason')), (True, None))
        # The probe's evidence survives: an inside-scope file exists.
        self.assertTrue(Path(result['probe']).exists())

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

    def test_the_probe_cleans_up_its_outside_target(self):
        # Rule (SR-22): the probe never leaves files in the user's home.
        with self.stub_sandbox(present=True, enforcing=True):
            sandbox.available(worktree=str(self.worktree))
        self.assertEqual(list(Path.home().glob('zloop-sandbox-probe-*')), [])

    def test_a_profile_parse_failure_is_not_enforcement(self):
        # Rule (SR-03): any sandbox-exec failure — including a malformed profile — is a fallback, never "available".
        directory = self.root / 'bin'
        directory.mkdir(exist_ok=True)
        executable = directory / 'sandbox-exec'
        executable.write_text('#!' + sys.executable + '\n' + '''
import sys
sys.exit(65)  # sandbox-exec's profile-parse failure code
''')
        executable.chmod(stat.S_IRWXU)
        with patch.dict(os.environ, {'PATH': str(directory) + os.pathsep + '/bin'}):
            result = sandbox.available(worktree=str(self.worktree))
        self.assertEqual((result['available'], 'fallback_reason' in result), (False, True))


class Wrapping(Machine):
    def route_launch(self):
        """A bash route's executor argv, as bash_executor receives it."""
        return {'argv': ['bash', str(self.root / 'worker.sh')], 'writes': 'worktree'}

    def test_the_bash_executor_wraps_its_worker_argv(self):
        # Rule: the wrap point is bash_executor's subprocess — sandbox-exec in front of the configured argv.
        with self.stub_sandbox(present=True, enforcing=True):
            wrapped = sandbox.wrap(self.route_launch()['argv'], 'worktree',
                                   worktree=str(self.worktree), reports=str(self.reports), tag='t1')
        self.assertEqual(wrapped['argv'][0], 'sandbox-exec')
        self.assertEqual(wrapped['argv'][1], '-f')
        profile_text = Path(wrapped['argv'][2]).read_text()
        self.assertIn(f'(subpath "{os.path.realpath(self.worktree)}")', profile_text)
        self.assertEqual(wrapped['argv'][3:], ['bash', str(self.root / 'worker.sh')])
        self.assertEqual(wrapped['verify']['sandbox_profile'], 'worktree')

    def test_unavailable_sandbox_keeps_the_argv_and_names_the_fallback(self):
        with self.stub_sandbox(present=False):
            wrapped = sandbox.wrap(['bash', 'worker.sh'], 'none',
                                   worktree=str(self.worktree), reports=str(self.reports), tag='t1')
        self.assertEqual(wrapped['argv'], ['bash', 'worker.sh'])
        self.assertIn('sandbox-exec', wrapped['fallback_reason'])
        self.assertNotIn('sandbox_profile', wrapped.get('verify', {}))

    def test_concurrent_launches_do_not_overwrite_one_anothers_profile(self):
        # Rule (SR-06): profile paths are per scope AND tag, so a second resolution never rewrites the first's
        # write allowance.
        with self.stub_sandbox(present=True, enforcing=True):
            first = sandbox.wrap(['bash', 'a.sh'], 'worktree', worktree=str(self.worktree / 'a'),
                                 reports=str(self.reports), tag='a1')
            second = sandbox.wrap(['bash', 'b.sh'], 'worktree', worktree=str(self.worktree / 'b'),
                                  reports=str(self.reports), tag='b1')
        self.assertNotEqual(first['argv'][2], second['argv'][2])
        self.assertIn(f'(subpath "{os.path.realpath(self.worktree / "a")}")', Path(first['argv'][2]).read_text())
        self.assertIn(f'(subpath "{os.path.realpath(self.worktree / "b")}")', Path(second['argv'][2]).read_text())


class ResolverGating(Machine):
    """SR-05: the resolver's own gating (darwin / kind / herdr / t3) is covered where it lives now — the bash
    executor, the only wrap point."""

    def test_a_native_route_carries_no_sandbox_launch(self):
        # Native children take no launch flags: the resolver must not add a sandbox wrapper to them.
        execution = {'host': 'claude', 'agent': 'builder', 'job': 'ticket', 'native_dispatch': True,
                     'settings': {'model': 'sonnet', 'effort': 'high'}}
        self.assertNotIn('launch', execution)
        self.assertNotIn('sandbox_profile', json.dumps(execution))


if __name__ == '__main__':
    unittest.main()
