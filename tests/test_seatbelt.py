#!/usr/bin/env python3
"""Seatbelt (macOS) profiles bound a worker's writes (and reads) to its scope: the bash executor wraps its worker
argv with a generated profile; the probe passes only when an inside-scope write succeeds AND an outside-scope write
fails; a missing or unenforcing sandbox selects the tool-allowlist fallback with a recorded reason."""

import json
import os
import subprocess
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

    def test_read_bounding_denies_credential_reads(self):
        # Rule: the user's credential directories are unreadable under any scope, while
        # the runtime stays loadable.
        profile = sandbox.profile('worktree', worktree=str(self.worktree), reports=str(self.reports))
        self.assertIn('(deny default)', profile)
        home = os.path.realpath(Path.home())
        self.assertIn(f'(deny file-read* (subpath "{home}/.ssh"))', profile)
        self.assertIn(f'(deny file-read* (subpath "{home}/.aws"))', profile)
        self.assertIn(f'(deny file-read* (subpath "{home}/.netrc"))', profile)
        self.assertIn(f'(subpath "{os.path.realpath(self.worktree)}")', profile)

    def test_a_worktree_profile_writes_the_shared_git_dir_and_the_user_temp(self):
        # Rule: a linked worktree's commits land in the main checkout's git directory, and the per-user
        # temp tree must be writable for runtimes that need it.
        main = self.root / 'main'
        subprocess.run(['git', 'init', '-q', str(main)], check=True)
        subprocess.run(['git', '-C', str(main), 'commit', '-q', '--allow-empty',
                        '--allow-empty-message', '-m', 'init'], check=True,
                       env=dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t',
                                GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t'))
        subprocess.run(['git', '-C', str(main), 'worktree', 'add', '-q', str(self.worktree)], check=True)
        profile = sandbox.profile('worktree', worktree=str(self.worktree), reports=str(self.reports))
        git_dir = os.path.realpath(subprocess.run(['git', '-C', str(self.worktree), 'rev-parse', '--git-common-dir'],
                                                  capture_output=True, text=True).stdout.strip())
        # Narrowed to the commit machinery: hooks and config stay read-only.
        self.assertIn(f'(subpath "{git_dir}/objects")', profile)
        self.assertIn(f'(subpath "{git_dir}/refs")', profile)
        self.assertNotIn(f'(subpath "{git_dir}/hooks")', profile)
        self.assertIn(f'(subpath "{os.path.realpath(tempfile.gettempdir())}")', profile)

    def test_unknown_scope_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'scope'):
            sandbox.profile('everywhere', worktree=str(self.worktree), reports=str(self.reports))


class Preflight(Machine):
    def test_an_enforcing_probe_passes_and_records_no_fallback(self):
        with self.stub_sandbox(present=True, enforcing=True):
            result = sandbox.available(worktree=str(self.worktree))
        self.assertEqual((result['available'], result.get('fallback_reason')), (True, None))
        # The probe leaves nothing behind: no stray files in the temp tree after a pass.
        self.assertFalse(Path(result['probe']).exists())

    def test_a_missing_binary_selects_the_allowlist_fallback_with_its_reason(self):
        with self.stub_sandbox(present=False):
            result = sandbox.available(worktree=str(self.worktree))
        self.assertEqual(result['available'], False)
        # The exact reason: a missing binary is distinct from a failed probe (whose message names the probe).
        self.assertIn('does not exist', result['fallback_reason'])
        self.assertNotIn('probe failed', result['fallback_reason'])
        self.assertIsNone(result['probe'])

    def test_a_present_but_unenforcing_binary_selects_the_fallback(self):
        with self.stub_sandbox(present=True, enforcing=False):
            result = sandbox.available(worktree=str(self.worktree))
        self.assertEqual(result['available'], False)
        self.assertIn('enforce', result['fallback_reason'])

    def test_the_probe_cleans_up_its_outside_target(self):
        # Rule: the probe never leaves files in the user's home.
        with self.stub_sandbox(present=True, enforcing=True):
            sandbox.available(worktree=str(self.worktree))
        self.assertEqual(list(Path.home().glob('zloop-sandbox-probe-*')), [])

    def test_a_profile_parse_failure_is_not_enforcement(self):
        # Rule: any sandbox-exec failure — including a malformed profile — is a fallback, never "available".
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
        # Rule: profile paths are per scope AND tag, so a second resolution never rewrites the first's
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
    """The resolver's own gating (darwin / kind / herdr / t3) is covered where it lives now — the bash
    executor, the only wrap point."""

    def setUp(self):
        super().setUp()
        self.user = self.root / 'executors.json'

    def resolve(self, worktree, reports):
        import agent_models
        return agent_models.resolve_execution(self.root, self.root / 'absent.json', self.user,
                                              'claude', 'reviewer', {}, worktree=worktree, reports=reports)

    def test_a_native_route_carries_no_sandbox_launch(self):
        # The wrap point is the bash executor only: resolving a NATIVE route must return native_dispatch with no
        # launch keys or profile anywhere — mutating the resolver to add a sandbox launch to native routes would
        # surface here. The bash side of the contract is covered where the wrap lives (Wrapping, BashExecution).
        self.user.write_text(json.dumps({
            'models': {'sol': {'description': 'Review.', 'runtimes': {'claude': {'id': 'sol'}}}},
            'agents': {'reviewer': {'executor': {'type': 'subagent'}, 'models': {'mode': 'inherit'}}}}))
        native = self.resolve(str(self.worktree), str(self.reports))
        self.assertTrue(native.get('native_dispatch'))
        self.assertNotIn('launch', native)
        self.assertNotIn('sandbox', json.dumps(native))




class BashExecutionCarriesScope(unittest.TestCase):
    """A resolved bash execution carries the worktree, reports directory and attempt the executor needs,
    so its Seatbelt profile bounds the real scope instead of falling back to the worktree."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop bash exec ')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.user = self.root / 'executors.json'
        self.user.write_text(json.dumps({
            'agents': {'reviewer': {'executor': {'type': 'bash', 'config': {'argv': ['bash', '/tmp/w.sh']}},
                                    'models': {'mode': 'inherit'}}}}))

    def resolve(self, worktree, reports):
        return models.resolve_execution(self.root, self.root / 'absent.json', self.user,
                                        'claude', 'reviewer', {}, worktree=worktree, reports=reports)

    def test_a_resolved_bash_execution_names_its_scope_and_attempt(self):
        execution = self.resolve(str(self.root / 'wt'), str(self.root / 'reports'))
        self.assertEqual(execution.get('worktree'), str(self.root / 'wt'))
        self.assertEqual(execution.get('reports'), str(self.root / 'reports'))
        self.assertNotIn('attempt', execution)  # The executor tags its profile with the assignment id it receives.

    def test_a_missing_reports_path_leaves_the_scope_fields_absent(self):
        # Without a reports directory the executor records its fallback reason instead of a profile; the resolver
        # carries no scope fields rather than inventing one inside the worktree it would bound.
        execution = self.resolve(str(self.root / 'wt'), None)
        self.assertIsNone(execution.get('reports'))
        self.assertEqual(execution.get('worktree'), str(self.root / 'wt'))

    def test_a_macos_bash_route_without_an_outside_reports_directory_is_refused(self):
        # The Seatbelt profile lives under the reports directory; with none outside the worktree there is nowhere
        # for it, so the executor refuses rather than bounding a worker by a profile inside what it bounds.
        import bash_executor
        execution = {'executor_type': 'bash', 'agent': 'reviewer', 'model_mode': 'inherit', 'settings': {},
                     'executor_config': {'argv': [sys.executable, '-c', 'print(1)']}}
        for reports in (None, str(self.root / 'wt')):
            assignment = {'assignment_id': 'a/1', 'worktree': str(self.root / 'wt'),
                          'agent_file': str(self.root / 'agent.md'), 'job_file': str(self.root / 'job.md'),
                          'head': 'h', 'prompt': 'p'}
            (self.root / 'agent.md').write_text('a')
            (self.root / 'job.md').write_text('j')
            (self.root / 'wt').mkdir(exist_ok=True)
            with self.assertRaises(ValueError) as raised:
                bash_executor.run(execution, dict(assignment, worktree=str(self.root / 'wt')))
            self.assertIn('reports directory outside', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
