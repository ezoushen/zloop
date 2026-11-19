#!/usr/bin/env python3
"""Inspection commands expose executable contracts without starting work."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import support


class Inspection(unittest.TestCase):
    def cli(self, script, *args):
        return subprocess.run([sys.executable, str(support.ROOT / 'scripts' / script), *map(str, args)],
                              capture_output=True, text=True)

    def test_title_inspection_refuses_invalid_and_empty_titles_and_preserves_hook(self):
        for flag, text in [('--ticket', ''), ('--ticket', 'The title.'), ('--commit', 'Missing type')]:
            with self.subTest(flag=flag, text=text):
                result = self.cli('title_rules.py', flag, text)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertFalse(json.loads(result.stdout)['ok'])
        self.assertEqual(self.cli('title_rules.py', '--ticket', 'Inspect current work').returncode, 0)
        with tempfile.TemporaryDirectory() as tmp:
            message = Path(tmp) / 'message'
            message.write_text('# comment\n\nfix: inspect current work\n')
            self.assertEqual(self.cli('title_rules.py', message).returncode, 0)
            message.write_text('invalid\n')
            self.assertEqual(self.cli('title_rules.py', message).returncode, 1)

    def test_job_inspection_uses_actual_permissions_and_declared_inputs(self):
        result = self.cli('agent_defs.py', '--job', 'ticket')
        self.assertEqual(result.returncode, 0, result.stderr)
        job = json.loads(result.stdout)
        self.assertEqual(job['agent']['writes'], 'worktree')
        self.assertIn('ticket', job['inputs'])
        self.assertIn('adapter', job['inputs'])
        self.assertTrue(Path(job['job_file']).is_file())
        self.assertEqual(self.cli('agent_defs.py', '--job', 'missing').returncode, 2)

    def test_remote_branch_names_are_generated_without_creating_refs(self):
        before = subprocess.check_output(['git', '-C', str(support.ROOT), 'for-each-ref'])
        title = "Don't duplicate café setup across the deterministic worker inspection commands"
        result = self.cli('title_rules.py', '--branch', title, '--slug', 'audit')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), dict(
            branch='audit/dont-duplicate-cafe-setup-across-the-deterministic',
            worktree='.worktrees/audit--dont-duplicate-cafe-setup-across-the-deterministic'))
        self.assertEqual(self.cli('title_rules.py', '--branch', title, '--slug', '../bad').returncode, 2)
        self.assertEqual(subprocess.check_output(['git', '-C', str(support.ROOT), 'for-each-ref']), before)

    def test_feature_inspection_does_not_run_steps_and_rejects_broken_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'must-not-run'
            argv = [sys.executable, '-c', f'from pathlib import Path; Path({str(marker)!r}).touch()']
            feature = dict(name='inspect', **{step: argv for step in ('launch', 'doctor', 'drive', 'evidence', 'cleanup')})
            manifest = Path(tmp) / 'features.json'
            manifest.write_text(json.dumps({'features': [feature]}))
            result = self.cli('verify_features.py', manifest, '--inspect')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['features'][0]['name'], 'inspect')
            self.assertFalse(marker.exists())
            feature['doctor'] = 'shell command'
            manifest.write_text(json.dumps({'features': [feature]}))
            result = self.cli('verify_features.py', manifest, '--inspect')
            self.assertEqual(result.returncode, 2)
            self.assertIn('literal argv', result.stderr)
            self.assertFalse(marker.exists())

    def test_t3_inspection_names_failure_and_builds_exact_native_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.cli('t3_preflight.py', '--project', tmp, '--home', Path(tmp) / 'missing')
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(json.loads(result.stdout)['reason'], 'home')
        preflight = support.load_script('t3_preflight.py')
        for runtime, option in [('codex', 'reasoningEffort'), ('claude', 'effort'),
                                ('pi', 'thinking'), ('opencode', 'variant')]:
            execution = dict(executor_type='t3', job='ticket', target_runtime=runtime,
                             settings=dict(provider='provider-id', model='exact-model', effort='high'))
            request = preflight.launch_arguments(execution)
            self.assertEqual(request, dict(mode='async', isolation='worktree', target=dict(
                providerInstanceId='provider-id', model='exact-model', options={option: 'high'})))
        execution['job'] = 'diff-review'
        self.assertEqual(preflight.launch_arguments(execution)['isolation'], 'shared')
        execution['executor_type'] = 'native'
        with self.assertRaisesRegex(ValueError, 'T3 route'):
            preflight.launch_arguments(execution)

    def test_t3_cli_checks_the_routes_write_scope_and_never_starts_a_thread(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / 'home/userdata'
            home.mkdir(parents=True)
            (home / 'server-runtime.json').write_text('{}')
            log = root / 'calls'
            cli = root / 't3'
            cli.write_text('#!' + sys.executable + '\nimport json,sys\nfrom pathlib import Path\n'
                f'with Path({str(log)!r}).open("a") as f: f.write(" ".join(sys.argv[1:])+"\\n")\n'
                'if sys.argv[1:3]==["thread","list"]: print("[]")\n'
                'elif sys.argv[1:3]==["environment","list"]: print(json.dumps(['
                '{"environmentId":"peer","status":"reachable","access":"approval-required"}]))\n'
                'else: raise SystemExit(99)\n')
            cli.chmod(0o700)
            route = root / 'route.json'
            route.write_text(json.dumps(dict(executor_type='t3', environment='peer', job='ticket',
                settings=dict(provider='pi', model='exact-model', effort='high'), target_runtime='pi')))
            result = subprocess.run([sys.executable, str(support.ROOT / 'scripts/t3_preflight.py'),
                '--project', tmp, '--home', str(home.parent), '--execution', str(route), '--writes', 'none'],
                env=dict(os.environ, PATH=tmp + os.pathsep + os.environ['PATH']), capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(json.loads(result.stdout)['problem'], 'ceiling')
            self.assertEqual(log.read_text().splitlines(), [f'thread list --json --project {tmp}', 'environment list --json'])


if __name__ == '__main__':
    unittest.main()
