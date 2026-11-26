#!/usr/bin/env python3
"""Exercise a managed external wait through the CLI and the stop hook."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import unittest

import support
import test_pr_safety
from t3_intent_fixture import T3Read


REPO, PR, BASE = test_pr_safety.REPO, 12, test_pr_safety.BASE
PULL = f'repos/{REPO}/pulls/{PR}'
THREAD = 'thread-fixture'
BRANCH = 'feat/watch'
START = '2026-11-26T10:30:00.000Z'
HOLD = 'this session\'s autopilot run has no terminal state or no evidence'


class PRWatch(unittest.TestCase):
    def setUp(self):
        self.github = test_pr_safety.PRSafety()
        self.github.addCleanup = self.addCleanup
        self.github.setUp()
        self.root = self.github.root
        self.git('checkout', '-qb', BRANCH)
        self.commit()
        self.head = self.git('rev-parse', 'HEAD')
        self.card = support.run_card(self.root / '.scratch/watch/autopilot.md', phase='pr',
                                     **{'intent-thread': THREAD})
        support.ticket_file(self.root, 'watch/01', status='done')
        adopted = self.github.run_cli('run_control.py', 'adopt', self.card, '--source', 'existing confirmation')
        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        self.host = T3Read(self, self.root, self.github.env, thread=THREAD)
        self.github.t3 = self.host
        self.github.env['PYTHONPATH'] = os.fspath(support.SCRIPTS)
        self.thread_state = self.root / 'thread-state.json'
        shim = self.root / '.t3/bin/t3'
        shim.parent.mkdir()
        shim.write_text(f'#!/bin/sh\n'
                        f'[ "$*" = {shlex.quote(f"thread show --json --messages 0 --base-dir {shim.parent.parent} {THREAD}")} ] '
                        f'|| exit 2\nexec /bin/cat {shlex.quote(str(self.thread_state))}\n')
        shim.chmod(0o700)
        self.watch_link()
        self.pull = {'number': PR, 'state': 'open',
                     'base': {'ref': BASE, 'sha': 'b' * 40, 'repo': {'full_name': REPO}},
                     'head': {'ref': BRANCH, 'sha': self.head, 'repo': {'full_name': REPO}}}
        self.github.replies = {f'api --hostname github.com {PULL}': self.pull}
        self.check_path = f'repos/{REPO}/commits/{self.head}/check-runs?per_page=100'
        self.checks({'id': 101, 'name': 'tests', 'head_sha': self.head, 'status': 'in_progress', 'conclusion': None})

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True, text=True,
                              capture_output=True).stdout.strip()

    def commit(self):
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-qm', 'Fixture')

    def watch_link(self, *, thread=THREAD, start=START, watching=True, worktree=None, branch=BRANCH):
        link = {'host': 'github.com', 'repository': REPO, 'number': PR, 'state': 'open',
                'url': f'https://github.com/{REPO}/pull/{PR}', 'watching': watching, 'watchStartedAt': start}
        self.thread_state.write_text(json.dumps({'thread': {
            'threadId': thread, 'archived': False, 'worktreePath': str(worktree or self.root.resolve()),
            'branch': branch, 'pullRequests': [link]}}))

    def checks(self, *runs):
        self.github.replies[f'api --hostname github.com --paginate --slurp {self.check_path}'] = [
            {'total_count': len(runs), 'check_runs': list(runs)}]

    def arm(self):
        result = self.github.run_cli('pr_watch.py', 'arm', self.card, '--pr', PR, '--head', self.head)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def refresh(self, receipt):
        self.github.calls.unlink(missing_ok=True)
        return self.github.run_cli('pr_watch.py', 'refresh', self.card, '--watch-id', receipt['watch_id'])

    def yield_gaps(self):
        result = subprocess.run(self.host.command(support.HOOKS / 'yield_gate.py', 'check', self.root, 'host-1'),
                                cwd=self.root, env=self.github.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['missing']

    def assert_held(self):
        self.assertEqual(len(gaps := self.yield_gaps()), 1, gaps)
        self.assertIn(HOLD, gaps[0])

    def test_native_watch_yields_only_at_its_bound_head(self):
        self.assert_held()
        canonical = self.card.with_name('autopilot-control.json')
        before_arm = json.loads(canonical.read_text())
        receipt = self.arm()
        self.assertEqual((receipt['status'], receipt['head_sha']), ('pending', self.head))
        self.assertRegex(receipt['watch_id'], r'^[0-9a-f]{64}$')
        self.assertEqual(json.loads(canonical.read_text()), dict(before_arm, external_wait=receipt))
        github_reads = self.github.calls.read_bytes()
        self.assertEqual(self.yield_gaps(), [])
        for fields in ({'thread': 'some-other-thread'}, {'worktree': self.root / 'other'},
                       {'branch': 'feat/other'}, {'start': '2026-11-26T11:00:00Z'},
                       {'watching': False, 'start': None}):
            with self.subTest(**fields):
                self.watch_link(**fields)
                self.assert_held()
        self.watch_link()
        before = canonical.read_text()
        canonical.write_text(json.dumps(dict(json.loads(before), revision=2)))
        self.assert_held()
        canonical.write_text(json.dumps(dict(before_arm, external_wait=dict(receipt, watch_id='0' * 64))))
        self.assert_held()
        canonical.write_text(before)
        self.assertEqual(self.yield_gaps(), [])
        self.host.runtime.chmod(0o666)
        self.assert_held()
        self.host.runtime.chmod(0o600)
        self.commit()
        self.assert_held()
        self.assertEqual(self.github.calls.read_bytes(), github_reads)

    def test_wake_refresh_rechecks_the_pr_and_commit_checks(self):
        receipt = self.arm()
        self.watch_link(watching=False, start=None)
        self.assert_held()
        pending = {'id': 101, 'name': 'tests', 'head_sha': self.head, 'status': 'in_progress', 'conclusion': None}
        passing = dict(pending, status='completed', conclusion='success')
        second = dict(passing, id=102, name='lint')
        failing = dict(second, conclusion='failure')
        pr_call = ['api', '--hostname', 'github.com', PULL]
        check_call = ['api', '--hostname', 'github.com', '--paginate', '--slurp', self.check_path]
        pr_key, check_key = ' '.join(pr_call), ' '.join(check_call)
        for status, runs in (('failure', [pending, failing]), ('success', [passing, second]),
                             ('pending', [passing, dict(second, status='queued', conclusion=None)]),
                             ('pending', [])):
            with self.subTest(status=status, runs=runs):
                self.github.replies[check_key] = (
                    [dict(total_count=len(runs), check_runs=[row]) for row in runs]
                    or [dict(total_count=0, check_runs=[])])
                result = self.refresh(receipt)
                self.assertEqual(result.returncode, 0, result.stderr)
                observed = json.loads(result.stdout)
                self.assertEqual((observed['watch_id'], observed['head_sha'], observed['base_ref'],
                                  observed['base_sha'], observed['status'], observed['check_runs']),
                                 (receipt['watch_id'], self.head, BASE, 'b' * 40, status, runs))
                self.assertEqual([json.loads(line) for line in self.github.calls.read_text().splitlines()],
                                 [pr_call, check_call, pr_call])
        cases = (
            ('head moved', dict(self.pull, head=dict(self.pull['head'], sha='c' * 40)),
             [passing], 'selected feature branch and head'),
            ('closed', dict(self.pull, state='closed'), [passing], 'selected feature branch and head'),
            ('base retargeted', dict(self.pull, base=dict(self.pull['base'], ref='other')),
             [passing], 'base changed during check verification'),
            ('base advanced', dict(self.pull, base=dict(self.pull['base'], sha='c' * 40)),
             [passing], 'base changed during check verification'),
            ('wrong check head', self.pull, [dict(passing, head_sha='c' * 40)], 'complete set of GitHub check runs'),
            ('duplicate ID', self.pull, [passing, passing], 'complete set of GitHub check runs'),
            ('missing check record', self.pull, [dict(passing, id=None)], 'complete set of GitHub check runs'),
            ('HTTP error', self.pull, None, 'cannot verify GitHub response for'),
        )
        control_path = self.card.with_name('autopilot-control.json')
        for case, after, runs, expected in cases:
            with self.subTest(case=case):
                self.github.replies[pr_key] = self.pull
                self.checks(passing)
                previous = self.refresh(receipt)
                self.assertEqual(previous.returncode, 0, previous.stderr)
                self.assertEqual(json.loads(previous.stdout)['check_runs'], [passing])

                self.github.replies[pr_key] = {'sequence': [self.pull, after]}
                self.github.replies[check_key] = None if runs is None else [dict(total_count=len(runs), check_runs=runs)]
                result = self.refresh(receipt)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(expected, result.stderr)
                self.assertEqual(result.stdout, '')
                wait = json.loads(control_path.read_text())['external_wait']
                self.assertEqual((wait['status'], wait['check_runs']), ('unverified', []))
        self.assert_held()

    def test_watch_for_another_workstream_cannot_cover_yield(self):
        self.git('branch', 'feat/other')
        other = support.run_card(self.root / '.scratch/other/autopilot.md', phase='pr',
                                 **{'intent-thread': THREAD})
        support.ticket_file(self.root, 'other/01', status='done')
        adopted = self.github.run_cli('run_control.py', 'adopt', other, '--source', 'existing confirmation')
        self.assertEqual(adopted.returncode, 0, adopted.stderr)

        rejected = self.github.run_cli('pr_watch.py', 'arm', other, '--pr', PR, '--head', self.head)
        self.assertEqual(rejected.returncode, 2, rejected.stderr or rejected.stdout)
        self.assertIn('workstream branch', rejected.stderr)
        self.assertFalse(self.github.calls.exists())
        self.assertTrue(any(str(other) in gap for gap in self.yield_gaps()))

        receipt = self.arm()
        self.assertEqual(receipt['branch'], BRANCH)
        report = self.card.with_name('reports') / 'spec-review.md'
        report.parent.mkdir(exist_ok=True)
        report.write_text('---\nworkstream_branch: feat/other\n---\n')
        self.assertTrue(any(str(self.card) in gap for gap in self.yield_gaps()))

    def test_only_neutral_or_skipped_checks_do_not_hold_the_wait_open(self):
        receipt = self.arm()
        neutral = {'id': 101, 'name': 'tests', 'head_sha': self.head,
                   'status': 'completed', 'conclusion': 'neutral'}
        skipped = dict(neutral, id=102, name='lint', conclusion='skipped')
        self.checks(neutral, skipped)

        refreshed = self.refresh(receipt)
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        result = json.loads(refreshed.stdout)
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['check_runs'], [neutral, skipped])
        self.assert_held()


if __name__ == '__main__':
    unittest.main()
