#!/usr/bin/env python3
"""A worker's capability probe reports each capability with evidence, and never reports one it could not check."""

import json
import os
import shutil
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
        self.common = self.root / 'repo.git'  # A linked worktree's git dir sits inside the repository's common dir.
        self.git_dir = self.common / 'worktrees' / 'w'
        for path in (self.git_dir, self.common / 'objects', self.common / 'refs'):
            path.mkdir(parents=True)

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
        git_dir, common = (self.git_dir, self.common) if works else (self.root / 'missing', self.root / 'missing')
        self.stub('git', f'case "$*" in *--git-common-dir*) echo {git_dir}; echo {common};; *) echo {git_dir};; esac')

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
        self.assertEqual(sorted(self.root.glob('*')), [self.bin, self.common])  # The probe file is removed.

    def test_setuid_is_unavailable_when_the_file_system_strips_the_bit(self):
        probe = support.load_script('capability_probe.py')
        with patch.object(probe.os, 'chmod', lambda path, mode: REAL_CHMOD(path, mode & ~stat.S_ISUID)):
            status, evidence = probe.setuid()
        self.assertEqual(status, 'unavailable', evidence)
        self.assertIn('read back without the setuid bit', evidence)

    def real_worktree(self, *init):
        """A linked worktree of a real repository with one commit, probed with the real git."""
        (self.bin / 'git').unlink(missing_ok=True)
        (self.bin / 'git').symlink_to(shutil.which('git'))
        main = self.root / 'main'
        git = ['git', '-c', 'user.name=t', '-c', 'user.email=t@t']
        subprocess.run([*git, 'init', '-q', '-b', 'main', *init, str(main)], check=True)
        subprocess.run([*git, '-C', str(main), 'commit', '-q', '--allow-empty', '-m', 'one'], check=True)
        subprocess.run([*git, '-C', str(main), 'worktree', 'add', '-q', str(self.root / 'linked')], check=True)
        return main / '.git'

    def probe_in(self, cwd):
        process = subprocess.run([sys.executable, '-B', str(PROBE)], capture_output=True, text=True,
                                 env=dict(os.environ, PATH=str(self.bin)), cwd=cwd)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        return json.loads(process.stdout)['repo-write']

    def read_only(self, path):
        path.chmod(0o555)
        self.addCleanup(path.chmod, 0o755)

    def test_repo_write_is_unavailable_when_the_branch_refs_of_the_common_git_dir_refuse_writes(self):
        # Rule: a commit writes its branch ref into the repository's common git dir, so repo-write is available only
        # when that write succeeds, not only when the linked worktree's own git dir takes a file.
        common = self.real_worktree()
        self.read_only(common / 'refs' / 'heads')
        result = self.probe_in(self.root / 'linked')
        self.assertEqual(result['status'], 'unavailable', result)

    def test_repo_write_is_unavailable_when_a_reftable_store_refuses_writes(self):
        # Rule: a repository may keep its refs outside refs/; the probe judges the store git actually writes.
        common = self.real_worktree('--ref-format=reftable')
        self.read_only(common / 'reftable')
        result = self.probe_in(self.root / 'linked')
        self.assertEqual(result['status'], 'unavailable', result)

    def test_repo_write_is_available_when_only_the_probe_ref_cannot_be_deleted_again(self):
        # Rule: repo-write means a commit can land. With the common git dir's root read-only a commit still lands (git
        # only fails to lock packed-refs on deletion), so the probe reports available and names the ref it left.
        common = self.real_worktree()
        self.read_only(common)
        result = self.probe_in(self.root / 'linked')
        self.assertEqual(result['status'], 'available', result)
        self.assertRegex(result['evidence'], r'could not delete refs/heads/zloop-probe-\d+')

    def test_repo_write_in_a_writable_linked_worktree_is_available_and_leaves_no_probe_branch(self):
        common = self.real_worktree()
        result = self.probe_in(self.root / 'linked')
        self.assertEqual(result['status'], 'available', result)
        branches = subprocess.run(['git', '--git-dir', str(common), 'branch', '--list'], capture_output=True, text=True, check=True)
        self.assertEqual(branches.stdout.split(), ['+', 'linked', '*', 'main'])

    def test_the_report_names_the_worktree_it_was_taken_in(self):
        self.assertEqual(self.probe()['worktree'], str(self.root.resolve()))


if __name__ == '__main__':
    unittest.main()
