#!/usr/bin/env python3
"""Feature verification grades post-state and retains proof across cleanup."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import support


class FeatureVerification(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runner = support.load_script('verify_features.py')
        py = sys.executable
        self.feature = dict(name='example', timeout=5,
            launch=[py, '-c', 'print("launched")'], doctor=[py, '-c', 'print("ready")'],
            drive=[py, '-c', 'from pathlib import Path; Path("observed").write_text("right")'],
            evidence=[py, '-c', 'from pathlib import Path; import sys; '
                      'ok=Path("observed").read_text()=="right"; '
                      'Path(sys.argv[1]).write_text(str(ok)); sys.exit(0 if ok else 1)', '{evidence}/proof.txt'],
            cleanup=[py, '-c', 'print("owned cleanup complete")'], required_artifacts=['proof.txt'])

    def run_feature(self):
        return self.runner.verify(self.feature, self.root / 'evidence')

    def test_real_post_state_and_retained_artifacts_are_required(self):
        result = self.run_feature()
        self.assertEqual(result['status'], 'passed')
        self.assertTrue((self.root / 'evidence/proof.txt').is_file())
        self.assertFalse(Path(result['workspace']).exists())

    def test_a_successful_driver_with_wrong_behavior_fails_the_independent_grader(self):
        self.feature['drive'] = [sys.executable, '-c', 'from pathlib import Path; Path("observed").write_text("wrong")']
        result = self.run_feature()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['steps']['drive']['exit_code'], 0)
        self.assertNotEqual(result['steps']['evidence']['exit_code'], 0)

    def test_missing_setup_is_blocked_and_owned_cleanup_still_runs(self):
        self.feature['launch'] = [sys.executable, '-c', 'raise SystemExit(7)']
        result = self.run_feature()
        self.assertEqual(result['status'], 'blocked')
        self.assertNotIn('drive', result['steps'])
        self.assertEqual(result['steps']['cleanup']['exit_code'], 0)

    def test_cleanup_cannot_delete_evidence_and_leave_a_pass(self):
        self.feature['cleanup'] = [sys.executable, '-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).unlink()',
                                   '{evidence}/proof.txt']
        result = self.run_feature()
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['missing_artifacts'], ['proof.txt'])

    def test_driver_cannot_edit_the_retained_oracle_to_manufacture_a_pass(self):
        self.feature['doctor'] = [sys.executable, '-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("right")',
                                  '{evidence}/oracle.txt']
        self.feature['protected_inputs'] = ['{evidence}/oracle.txt']
        self.feature['drive'] = [sys.executable, '-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("changed")',
                                '{evidence}/oracle.txt']
        self.feature['cleanup'] = [sys.executable, '-c',
            'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("cleaned")', '{evidence}/cleanup.txt']
        result = self.run_feature()
        self.assertEqual(result['status'], 'blocked')
        self.assertNotIn('evidence', result['steps'])
        self.assertEqual(result['steps']['cleanup']['exit_code'], 0)
        self.assertEqual((self.root / 'evidence/cleanup.txt').read_text(), 'cleaned')

    def test_a_modified_cleanup_program_requires_explicit_reconciliation(self):
        program = self.root / 'cleanup.py'
        program.write_text('from pathlib import Path; Path("cleaned").write_text("yes")')
        marker = self.root / 'untrusted-ran'
        self.feature['cleanup'] = [sys.executable, str(program)]
        self.feature['drive'] = [sys.executable, '-c',
            'from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2])',
            str(program), f'from pathlib import Path; Path({str(marker)!r}).write_text("yes")']
        result = self.run_feature()
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['steps']['cleanup']['exit_code'], 125)
        self.assertIn('reconciliation', result['steps']['cleanup']['reason'])
        self.assertFalse(marker.exists())

    def test_cleanup_created_by_launch_is_protected_before_drive(self):
        self.feature['launch'] = [sys.executable, '-c',
            'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("print(123)")',
            '{workspace}/cleanup.py']
        self.feature['drive'] = [sys.executable, '-c',
            'from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2])',
            '{workspace}/cleanup.py',
            'from pathlib import Path; Path("{evidence}/untrusted-ran").write_text("untrusted")']
        for name, path in [('absolute', '{workspace}/cleanup.py'), ('relative', 'cleanup.py')]:
            with self.subTest(path=path):
                self.feature['cleanup'] = [sys.executable, path]
                evidence = self.root / name
                result = self.runner.verify(self.feature, evidence)
                self.assertEqual(result['steps']['drive']['exit_code'], 0)
                self.assertEqual(result['status'], 'blocked')
                self.assertEqual(result['steps']['cleanup']['exit_code'], 125)
                self.assertIn('reconciliation', result['steps']['cleanup']['reason'])
                self.assertFalse((evidence / 'untrusted-ran').exists())

    def test_relative_cleanup_runs_after_success_and_driver_failure(self):
        self.feature['launch'] = [sys.executable, '-c',
            'from pathlib import Path; import sys; Path("cleanup.py").write_text(sys.argv[1])',
            'from pathlib import Path; Path("{evidence}/cleaned").write_text("yes")']
        self.feature['cleanup'] = [sys.executable, 'cleanup.py']
        for name, status in [('success', 'passed'), ('failed-drive', 'blocked')]:
            with self.subTest(name=name):
                if name == 'failed-drive':
                    self.feature['drive'] = [sys.executable, '-c', 'raise SystemExit(7)']
                evidence = self.root / name
                result = self.runner.verify(self.feature, evidence)
                self.assertEqual(result['status'], status)
                self.assertEqual(result['steps']['cleanup']['exit_code'], 0)
                self.assertEqual((evidence / 'cleaned').read_text(), 'yes')

    def test_driver_cannot_replace_a_relative_verification_program(self):
        self.feature['launch'] = [sys.executable, '-c',
            'from pathlib import Path; Path("grader.py").write_text("raise SystemExit(1)")']
        self.feature['drive'] = [sys.executable, '-c',
            'from pathlib import Path; import sys; Path("grader.py").write_text(sys.argv[1])',
            'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("fake proof")']
        self.feature['evidence'] = [sys.executable, 'grader.py', '{evidence}/proof.txt']
        result = self.run_feature()
        self.assertEqual(result['steps']['drive']['exit_code'], 0)
        self.assertEqual(result['status'], 'blocked')
        self.assertNotIn('evidence', result['steps'])

    def test_project_feature_map_exercises_its_real_drivers_and_graders(self):
        manifest = json.loads((support.ROOT / 'benchmarks/workflows/features.json').read_text())
        for feature in manifest['features']:
            with self.subTest(feature=feature['name']):
                result = self.runner.verify(feature, self.root / feature['name'])
                self.assertEqual(result['status'], 'passed', result)
                self.assertTrue((self.root / feature['name'] / 'observed.json').is_file())

    def test_paused_completion_grader_rejects_a_broken_acceptance_guard(self):
        manifest = json.loads((support.ROOT / 'benchmarks/workflows/features.json').read_text())
        feature = next(f for f in manifest['features'] if f['name'] == 'paused-completion')
        feature['drive'] = [sys.executable, '-c',
            f'import sys; sys.path.insert(0,{str(support.SCRIPTS)!r}); '
            'from pathlib import Path; import subprocess,run_control,worker_lifecycle as life; '
            f'subprocess.run([sys.executable,{str(support.SCRIPTS / "worker_lifecycle.py")!r},"ack",'
            '"--state",sys.argv[1],"--event-id","1"],check=True); '
            'run_control.require_active=lambda card:None; db=life.connect(Path(sys.argv[1])); '
            'life.record(db,dict(kind="accepted",attempt="worker/1",commit="integrated",reason="broken guard",evidence=["fixture"])); '
            'db.close()', '{workspace}/paused/reports/workers.sqlite']
        result = self.runner.verify(feature, self.root / 'broken-guard')
        self.assertEqual(result['steps']['drive']['exit_code'], 0)
        self.assertEqual(result['status'], 'failed')
        self.assertNotEqual(result['steps']['evidence']['exit_code'], 0)


if __name__ == '__main__':
    unittest.main()
