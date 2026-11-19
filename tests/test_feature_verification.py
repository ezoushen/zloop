#!/usr/bin/env python3
"""Feature verification grades post-state and retains proof across cleanup."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
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

    def cli(self, manifest, *extra):
        return subprocess.run([sys.executable, str(support.script('verify_features.py')), str(manifest),
                               '--evidence', str(self.root / 'cli'), *extra], capture_output=True, text=True)

    def test_a_manifest_changed_during_drive_keeps_its_original_identity_and_blocks(self):
        manifest = self.root / 'features.json'
        self.feature['drive'] = [sys.executable, '-c',
            f'from pathlib import Path; Path({str(manifest)!r}).write_text(\'{{"features": []}}\'); Path("observed").write_text("right")']
        manifest.write_text(json.dumps(dict(features=[self.feature])))
        original = hashlib.sha256(manifest.read_bytes()).hexdigest()
        process = self.cli(manifest)
        report = json.loads((self.root / 'cli/report.json').read_text())
        self.assertEqual(report['manifest_sha256'], original)
        self.assertEqual(report['results'][0]['status'], 'blocked')
        self.assertIn('changed during verification', report['results'][0]['reason'])
        self.assertEqual(process.returncode, 1)

    def test_a_driver_changed_during_drive_blocks_the_result(self):
        driver = self.root / 'driver.json'
        manifest = self.root / 'features.json'
        self.feature['task'] = 'do it'
        manifest.write_text(json.dumps(dict(features=[self.feature])))
        driver.write_text(json.dumps([sys.executable, '-c',
            f'from pathlib import Path; Path({str(driver)!r}).write_text("[]"); Path("observed").write_text("right")']))
        original = hashlib.sha256(driver.read_bytes()).hexdigest()
        self.cli(manifest, '--driver', str(driver))
        report = json.loads((self.root / 'cli/report.json').read_text())
        self.assertEqual(report['driver_sha256'], original)
        self.assertEqual(report['results'][0]['status'], 'blocked')

    def drift_run(self, victim, trials=1, fail=False, directory=False):
        """Delete the manifest or driver from a drive step; return the aggregate report and process."""
        manifest, driver = self.root / 'features.json', self.root / 'driver.json'
        code = (f'from pathlib import Path; Path({str(manifest if victim == "manifest" else driver)!r})'
                '.unlink(); ' + ('Path({0!r}).mkdir(); '.format(str(manifest if victim == "manifest" else driver)) if directory else '') +
                'Path("observed").write_text("right")' + ('; raise SystemExit(7)' if fail else ''))
        self.feature['task'] = 'do it'
        manifest.write_text(json.dumps(dict(features=[self.feature])))
        driver.write_text(json.dumps([sys.executable, '-c', code]))
        extra = ['--driver', str(driver), '--trials', str(trials)]
        process = self.cli(manifest, *extra)
        return json.loads((self.root / 'cli/report.json').read_text()), process

    def test_a_deleted_manifest_or_driver_blocks_every_trial_and_keeps_the_report(self):
        for victim in ('manifest', 'driver'):
            for fail, directory in ((False, False), (True, False), (False, True)):
                with self.subTest(victim=victim, failed_trial=fail, replaced_by_directory=directory):
                    for leftover in ('cli', 'features.json', 'driver.json'):
                        shutil.rmtree(self.root / leftover, ignore_errors=True)
                        (self.root / leftover).unlink(missing_ok=True)
                    report, process = self.drift_run(victim, trials=2, fail=fail, directory=directory)
                    self.assertEqual(process.returncode, 1, process.stderr)
                    self.assertEqual([r['status'] for r in report['results']], ['blocked', 'blocked'])
                    self.assertTrue(all(r['configuration_changed'] for r in report['results']))
                    self.assertEqual(report['manifest_sha256'], hashlib.sha256(
                        (self.root / 'cli/manifest.original.json').read_bytes()).hexdigest())
                    self.assertEqual(report['driver_sha256'], hashlib.sha256(
                        (self.root / 'cli/driver.original.json').read_bytes()).hexdigest())

    def test_the_executed_manifest_and_driver_bytes_are_retained_in_evidence(self):
        manifest, driver = self.root / 'features.json', self.root / 'driver.json'
        self.feature['task'] = 'do it'
        manifest.write_text(json.dumps(dict(features=[self.feature])))
        driver.write_text(json.dumps([sys.executable, '-c', 'from pathlib import Path; Path("observed").write_text("right")']))
        self.cli(manifest, '--driver', str(driver))
        self.assertEqual((self.root / 'cli/manifest.original.json').read_bytes(), manifest.read_bytes())
        self.assertEqual((self.root / 'cli/driver.original.json').read_bytes(), driver.read_bytes())

    def test_an_unreadable_cleanup_program_is_refused_with_a_retained_blocked_result(self):
        self.feature['launch'] = [sys.executable, '-c',
            'from pathlib import Path; Path("cleanup.py").write_text("print(1)")']
        self.feature['cleanup'] = [sys.executable, 'cleanup.py']
        self.feature['evidence'] = [sys.executable, '-c',
            'from pathlib import Path; import sys; Path("cleanup.py").chmod(0); Path(sys.argv[1]).write_text("True")',
            '{evidence}/proof.txt']
        result = self.run_feature()
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['steps']['cleanup']['exit_code'], 125)
        self.assertIn('reconciliation', result['steps']['cleanup']['reason'])
        self.assertTrue((self.root / 'evidence/result.json').is_file())

    def test_an_unchanged_manifest_still_passes_through_the_cli(self):
        manifest = self.root / 'features.json'
        manifest.write_text(json.dumps(dict(features=[self.feature])))
        process = self.cli(manifest)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads((self.root / 'cli/report.json').read_text())
        self.assertEqual(report['results'][0]['status'], 'passed')

    def test_a_missing_protected_input_keeps_a_blocked_result_and_later_features_run(self):
        broken = dict(self.feature, name='broken', protected_inputs=['{evidence}/absent-oracle.txt'])
        healthy = dict(self.feature, name='healthy')
        manifest = self.root / 'features.json'
        manifest.write_text(json.dumps(dict(features=[broken, healthy])))
        process = self.cli(manifest)
        self.assertEqual(process.returncode, 1, process.stderr)
        report = json.loads((self.root / 'cli/report.json').read_text())
        statuses = {r['feature']: r['status'] for r in report['results']}
        self.assertEqual(statuses, {'broken': 'blocked', 'healthy': 'passed'})
        blocked = json.loads((self.root / 'cli/broken/1/result.json').read_text())
        self.assertIn('absent-oracle.txt', blocked['reason'])
        self.assertEqual(blocked['steps']['cleanup']['exit_code'], 0)
        self.assertNotIn('drive', blocked['steps'])
        self.assertFalse(Path(blocked['workspace']).exists())

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
