#!/usr/bin/env python3
"""A worker's capability probe reports each capability with evidence, and never reports one it could not check."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import support

PROBE = support.script('capability_probe.py')
CAPABILITIES = ['network', 'docker', 'setuid', 'repo-write', 'dev-fd']


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
        self.stub('grep', f'printf "NoNewPrivs:\\t{0 if works else 1}\\n"')
        self.stub('bash', f'[ {code} = 0 ] && echo ok; exit {code}')
        self.stub('git', f'echo {self.git_dir if works else self.root / "missing"}')

    def probe(self):
        process = subprocess.run([sys.executable, '-B', str(PROBE)], capture_output=True, text=True,
                                 env=dict(os.environ, PATH=str(self.bin)), cwd=self.root)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        return json.loads(process.stdout)

    def test_a_probe_reports_each_capability_available_with_its_evidence(self):
        self.stub_all(True)
        report = self.probe()
        self.assertEqual(list(report), CAPABILITIES)
        for name in CAPABILITIES:
            self.assertEqual(report[name]['status'], 'available', name)
            self.assertTrue(report[name]['evidence'].strip(), name)

    def test_a_probe_reports_a_capability_the_environment_lacks_as_unavailable_and_still_exits_zero(self):
        self.stub_all(False)
        report = self.probe()
        self.assertEqual({name: report[name]['status'] for name in CAPABILITIES},
                         dict.fromkeys(CAPABILITIES, 'unavailable'))

    def test_a_step_that_cannot_run_is_unknown_never_available(self):
        report = self.probe()  # No stubs: none of the probe's commands exist on this PATH.
        self.assertEqual({name: report[name]['status'] for name in CAPABILITIES},
                         dict.fromkeys(CAPABILITIES, 'unknown'))
        for name in CAPABILITIES:
            self.assertTrue(report[name]['evidence'].strip(), name)


if __name__ == '__main__':
    unittest.main()
