#!/usr/bin/env python3
"""A worker's capability probe reports each capability with evidence, and never reports one it could not check."""

import json
import os
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

import support

PROBE = support.script('capability_probe.py')
REAL_CHMOD = os.chmod
CAPABILITIES = ['network', 'docker', 'setuid', 'repo-write', 'dev-fd']
COMMANDED = ['network', 'docker', 'repo-write', 'dev-fd']  # Probed through commands the tests stub; setuid is not.


class CapabilityProbe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='zloop probe ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.git_dir = self.root / 'repo.git'
        self.git_dir.mkdir()

    def stub(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)

    def stub_all(self, works):
        """Stub every command the probe uses so each reports the capability present, or absent."""
        code = 0 if works else 1
        self.stub('curl', f'exit {code}')
        self.stub('docker', f'exit {code}')
        self.stub('bash', f'[ {code} = 0 ] && echo ok; exit {code}')
        self.stub('git', f'echo {self.git_dir if works else self.root / "missing"}')

    def probe(self, **env):
        process = subprocess.run([sys.executable, '-B', str(PROBE)], capture_output=True, text=True,
                                 env=dict(os.environ, PATH=str(self.bin), **env), cwd=self.root)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        return json.loads(process.stdout)

    def test_a_probe_reports_each_capability_available_with_its_evidence(self):
        self.stub_all(True)
        report = self.probe()
        self.assertEqual(list(report)[:5], CAPABILITIES)
        for name in CAPABILITIES:
            self.assertEqual(report[name]['status'], 'available', name)
            self.assertTrue(report[name]['evidence'].strip(), name)

    def test_a_probe_reports_a_capability_the_environment_lacks_as_unavailable_and_still_exits_zero(self):
        self.stub_all(False)
        report = self.probe()
        self.assertEqual({name: report[name]['status'] for name in COMMANDED}, dict.fromkeys(COMMANDED, 'unavailable'))

    def test_a_step_that_cannot_run_is_unknown_never_available(self):
        report = self.probe()  # No stubs: none of the probe's commands exist on this PATH.
        self.assertEqual({name: report[name]['status'] for name in COMMANDED}, dict.fromkeys(COMMANDED, 'unknown'))
        for name in COMMANDED:
            self.assertTrue(report[name]['evidence'].strip(), name)

    def test_setuid_is_unknown_when_no_file_can_be_created_to_test_it(self):
        probe = support.load_script('capability_probe.py')
        with patch.object(probe.tempfile, 'NamedTemporaryFile', side_effect=PermissionError('read-only')):
            status, evidence = probe.setuid()
        self.assertEqual((status, 'read-only' in evidence), ('unknown', True), evidence)

    def test_setuid_is_available_when_the_bit_set_on_a_file_reads_back(self):
        report = self.probe(TMPDIR=str(self.root))
        self.assertEqual(report['setuid']['status'], 'available', report['setuid'])
        self.assertEqual(sorted(self.root.glob('*')), [self.bin, self.git_dir])  # The probe file is removed.

    def test_setuid_is_unavailable_when_the_file_system_strips_the_bit(self):
        probe = support.load_script('capability_probe.py')
        with patch.object(probe.os, 'chmod', lambda path, mode: REAL_CHMOD(path, mode & ~stat.S_ISUID)):
            status, evidence = probe.setuid()
        self.assertEqual(status, 'unavailable', evidence)
        self.assertIn('read back without the setuid bit', evidence)

    def test_the_report_names_the_worktree_it_was_taken_in(self):
        self.assertEqual(self.probe()['worktree'], str(self.root.resolve()))


if __name__ == '__main__':
    unittest.main()
