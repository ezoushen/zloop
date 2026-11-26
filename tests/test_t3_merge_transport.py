#!/usr/bin/env python3
"""Exercise managed merge through the CLI with a fake GitHub and a local T3 host."""
import json
import unittest

import support
import test_pr_safety
from test_pr_safety import BASE, BRANCH, REPO, SHA


READ = {'name': 'pull_request_read', 'arguments': {'repository': 'fixture/zloop', 'number': 12}}
SUBMIT = {'name': 'pull_request_merge', 'arguments': {
    'repository': 'fixture/zloop', 'number': 12, 'expectedHeadSha': 'a' * 40, 'method': 'squash',
    'auto': False, 'stack': False}}


class T3MergeTransport(unittest.TestCase):
    def setUp(self):
        self.github = test_pr_safety.PRSafety()
        self.github.setUp()
        self.addCleanup(self.github.doCleanups)
        self.github.replies[f'api --hostname github.com {BRANCH}'] = {
            'name': BASE, 'protected': True, 'protection': {'enabled': True,
                'required_status_checks': {'enforcement_level': 'everyone', 'contexts': ['checks']}}}
        # A fallback would succeed on the old code, but only in the local fixture.
        self.github.replies[f'pr merge 12 --repo github.com/{REPO} --match-head-commit {SHA} --squash'] = 'submitted'

    def grant(self, scenario):
        self.card = support.run_card(self.github.root / scenario / 'autopilot.md', phase='pr')
        self.control = self.card.with_name('autopilot-control.json')
        self.github.grant(self.card)
        self.host = self.github.t3
        self.host.pr_calls.clear()
        self.host.on_merge = None
        self.host.pr_readback(REPO, 12, SHA, BASE)

    def state(self):
        return json.loads(self.control.read_bytes())

    def merge(self, revision, head=SHA):
        return self.github.run_cli('run_control.py', 'merge', str(self.card), '--expect', str(revision),
                                   '--merge-pr', '12', '--head', head, '--method', 'squash')

    def end(self, request='merged'):
        return self.github.run_cli('run_control.py', 'end', str(self.card), '--expect', '3',
                                   '--terminal', 'merged', '--request-id', request,
                                   '--source', 'untrusted operator summary', '--reason', 'record PR outcome')

    def assert_waiting(self, result, detail):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('waiting-on-user:', result.stderr)
        self.assertIn(detail, result.stderr)

    def assert_read_only_github(self):
        rows = [json.loads(line) for line in self.github.calls.read_text().splitlines()]
        self.assertTrue(rows)
        self.assertTrue(all(row[:3] == ['api', '--hostname', 'github.com'] for row in rows), rows)

    def test_verified_grant_uses_exact_native_head(self):
        self.grant('native')
        during = []

        def submit():
            during.append(self.state())
            self.host.pr_readback(REPO, 12, SHA, BASE, 'merged')
            return 200, {key: self.host.pull[key] for key in ('host', 'repository', 'number', 'url', 'state')}

        self.host.on_merge = submit
        result = self.merge(2)
        self.assertEqual(self.host.pr_calls, [READ, SUBMIT, READ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(during), 1)
        self.assertEqual((during[0]['revision'], during[0]['merge_submission']), (3, {
            'repo': 'fixture/zloop', 'base': 'release/next', 'pr': 12, 'head': 'a' * 40,
            'method': 'squash', 'authorization': 'merge-grant-1', 'outcome': 'pending'}))
        self.assertEqual(json.loads(result.stdout)['outcome'], 'merged')
        self.assertEqual(self.state()['merge_submission']['outcome'], 'merged')
        self.assertIn('terminal:\n', self.card.read_text())
        self.assert_read_only_github()

    def test_stale_or_unsupported_submission_never_falls_back(self):
        self.grant('unavailable')
        before = self.card.read_bytes(), self.control.read_bytes()
        token = self.github.env.pop('T3_MCP_BEARER_TOKEN')
        self.assert_waiting(self.merge(2), 'local T3 merge transport is unavailable')
        self.github.env['T3_MCP_BEARER_TOKEN'] = token
        self.assert_waiting(self.merge(2, 'b' * 40), 'reviewed head')
        self.assertEqual(self.host.pr_calls, [])
        self.assertEqual((self.card.read_bytes(), self.control.read_bytes()), before)

        self.host.pull = None
        self.assert_waiting(self.merge(2), 'fresh T3 pull request readback is unavailable')
        self.assertEqual(self.host.pr_calls, [READ])
        self.assertEqual((self.card.read_bytes(), self.control.read_bytes()), before)

        self.host.pr_readback(REPO, 12, 'b' * 40, BASE)
        self.assert_waiting(self.merge(2), 'readback does not match the reviewed pull request')
        self.assertEqual(self.host.pr_calls, [READ, READ])
        self.assertEqual((self.card.read_bytes(), self.control.read_bytes()), before)

        self.host.pr_readback(REPO, 12, SHA, BASE)
        self.host.on_merge = lambda: (403, None)
        self.assert_waiting(self.merge(2), 'merge outcome is uncertain')
        self.assertEqual(self.host.pr_calls, [READ, READ, READ, SUBMIT, READ])
        self.assertEqual(self.state()['merge_submission']['outcome'], 'uncertain')
        after = self.control.read_bytes()
        self.assert_waiting(self.merge(3), 'already consumed')
        self.assertEqual(self.host.pr_calls, [READ, READ, READ, SUBMIT, READ])
        self.assertEqual((self.card.read_bytes(), self.control.read_bytes()), (before[0], after))
        self.assert_read_only_github()

        relayed = support.run_card(self.github.root / 'relay' / 'autopilot.md', phase='pr',
                                   **{'confirmed-by': 'relayed(Grok Bot)'})
        adopted = self.github.run_cli('run_control.py', 'adopt', str(relayed), '--source', 'relayed run')
        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        self.card = relayed
        self.assert_waiting(self.merge(1), 'relayed confirmation')
        self.assertEqual(self.host.pr_calls, [READ, READ, READ, SUBMIT, READ])
        self.assert_read_only_github()

    def test_only_live_readback_can_mark_merged(self):
        for scenario, read_state, overrides, outcome, detail in (
                ('queued', 'open', {}, 'queued', 'is still open'),
                ('retargeted', 'merged', {'baseBranch': 'other'}, 'mismatch', 'does not match'),
                ('moved', 'merged', {'headSha': 'b' * 40}, 'mismatch', 'does not match'),
                ('wrong-repo', 'merged', {'repository': 'other/zloop'}, 'mismatch', 'does not match'),
                ('wrong-number', 'merged', {'number': 13}, 'mismatch', 'does not match'),
                ('wrong-host', 'merged', {'host': 'other.invalid'}, 'mismatch', 'does not match'),
                ('unavailable', None, {}, 'uncertain', 'readback is unavailable'),
                ('confirmed', 'merged', {}, 'merged', None)):
            self.grant(scenario)

            def submit():
                summary = {key: self.host.pull[key] for key in ('host', 'repository', 'number', 'url')}
                if read_state is None:
                    self.host.pull = None
                else:
                    self.host.pull = dict(self.host.pull, state=read_state, **overrides)
                return 200, dict(summary, state='open' if scenario == 'queued' else 'merged')

            self.host.on_merge = submit
            used = self.merge(2)
            before = self.card.read_bytes(), self.control.read_bytes()
            if detail:
                self.assert_waiting(self.end(), detail)
                self.assertEqual((self.card.read_bytes(), self.control.read_bytes()), before, scenario)
                self.assert_waiting(used, detail)
                self.assertEqual(self.host.pr_calls, [READ, SUBMIT, READ, READ], scenario)
            else:
                self.assertEqual(used.returncode, 0, used.stderr)
                self.host.pull = None
                self.assert_waiting(self.end(), 'readback is unavailable')
                self.assertEqual((self.card.read_bytes(), self.control.read_bytes()), before)
            self.assertEqual(self.state()['merge_submission']['outcome'], outcome, scenario)
            self.assert_waiting(self.merge(3), 'already consumed')
            self.host.pr_readback(REPO, 12, SHA, BASE, 'merged')
            terminal = self.end()
            self.assertEqual(terminal.returncode, 0, terminal.stderr)
            self.assertEqual(json.loads(terminal.stdout)['terminal'], 'merged')
            self.assertEqual(self.state()['merge_submission']['outcome'], 'merged')
            self.assertEqual(self.host.pr_calls, [READ, SUBMIT, READ, READ, READ], scenario)
            self.assert_read_only_github()

        self.grant('legacy-submission')
        state = self.state()
        state['merge_submission'] = {'pr': 12, 'head': 'a' * 40, 'method': 'squash'}
        state['revision'] = 3
        self.control.write_text(json.dumps(state))
        self.assert_waiting(self.merge(3), 'already consumed')
        self.assert_waiting(self.end(), 'is still open')
        self.host.pr_readback(REPO, 12, SHA, BASE, 'merged')
        terminal = self.end()
        self.assertEqual(terminal.returncode, 0, terminal.stderr)
        self.assertEqual(json.loads(terminal.stdout)['terminal'], 'merged')
        self.assertEqual(self.host.pr_calls, [READ, READ])
        self.assert_read_only_github()


if __name__ == '__main__':
    unittest.main()
