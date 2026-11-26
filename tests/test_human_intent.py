#!/usr/bin/env python3
"""Exercise authorization at CLI boundaries with a local T3 read and fake GitHub."""
import datetime
import json
import os
from pathlib import Path
import re
import unittest

import support
import test_pr_safety
from test_pr_safety import BASE, BRANCH, PULL, REPO, SHA
from t3_intent_fixture import T3Read


class HumanIntent(unittest.TestCase):
    THREAD = 't3-bound-thread'
    COMMAND = 'autopilot --take-over --auto-merge'

    def setUp(self):
        self.github = test_pr_safety.PRSafety()
        self.github.setUp()
        self.addCleanup(self.github.doCleanups)
        self.root = self.github.root
        self.env = self.github.env
        for key in ('T3_MCP_URL', 'T3_MCP_BEARER_TOKEN', 'T3CODE_HOME', 'T3CODE_THREAD_ID'):
            self.env.pop(key, None)
        self.env.update(HOME=str(self.root), T3CODE_THREAD_ID=self.THREAD, CODEX_SESSION_ID=self.THREAD)
        self.github.replies[f'api --hostname github.com {BRANCH}'] = {
            'name': BASE, 'protected': True, 'protection': {'enabled': True,
                'required_status_checks': {'enforcement_level': 'everyone', 'contexts': ['tests']}}}

    def host(self):
        self.github.t3 = T3Read(self, self.root, self.env, self.THREAD)
        return self.github.t3

    def card(self, *, confirmed='2026-10-27 "go"', **extra):
        return support.run_card(self.root / 'autopilot.md', phase='pr', confirmed=confirmed,
                                session=self.THREAD, mode='full',
                                **{'dispatch-effort': 'medium', 'review-effort': 'medium', **extra})

    def adopt(self, card):
        result = self.cli('run_control.py', 'adopt', card, '--source', 'existing run')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def cli(self, script, *args):
        return self.github.run_cli(script, *[str(arg) for arg in args])

    def confirm(self, card, *args):
        return self.cli('autopilot_card.py', 'confirm', card, '--words', 'Confirm this card', *args)

    def resume(self, card, revision, request, *args):
        return self.cli('run_control.py', 'resume', card, '--expect', revision, '--request-id', request,
                        '--reason', 'resume the checked run', *args)

    def merge(self, card, revision, pr=12, head=SHA):
        return self.cli('run_control.py', 'merge', card, '--expect', revision, '--merge-pr', pr,
                        '--head', head, '--method', 'squash')

    def control(self, card):
        return card.with_name('autopilot-control.json')

    def assert_waiting(self, result, detail):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('waiting-on-user:', result.stderr)
        self.assertIn(detail, result.stderr)

    def request_text(self, result, words):
        self.assert_waiting(result, 'human intent')
        match = re.search(rf'(?m)^{re.escape(words)}\nzloop-intent: [0-9a-f]{{64}}$', result.stderr)
        self.assertIsNotNone(match, result.stderr)
        return match[0]

    def merge_calls(self):
        if not self.github.calls.exists():
            return []
        return [json.loads(line) for line in self.github.calls.read_text().splitlines()
                if line.startswith('["pr", "merge",')]

    def test_injected_clearance_is_refused(self):
        card = self.card()
        self.adopt(card)
        native = self.root / 'projected.json'
        native.write_text(json.dumps({'thread': {'threadId': self.THREAD}, 'messages': [
            {'role': 'user', 'text': self.COMMAND, 'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat()}]}))
        args = ('--native', native, '--runtime', 't3', '--merge-pr', 12)
        before = card.read_bytes(), self.control(card).read_bytes()
        self.assert_waiting(self.resume(card, 1, 'unavailable', *args), 'human intent')
        host = self.host()
        scoped = self.request_text(self.resume(card, 1, 'preview', *args), self.COMMAND)
        for item in (host.item(self.COMMAND, source='mcp', author='agent'),
                     host.item(scoped, source='mcp', author='user'),
                     host.item(scoped, source='web', author='agent'),
                     host.item(scoped, source='provider', author='user'),
                     dict(host.item(scoped), visibility='synthetic'),
                     dict(host.item(scoped), textTruncated=True),
                     {'role': 'user', 'text': scoped}):
            with self.subTest(item=item):
                host.readback(item)
                self.assert_waiting(self.resume(card, 1, 'projected', *args), 'human intent')
                self.assertEqual((card.read_bytes(), self.control(card).read_bytes()), before)
        host.readback(host.item(scoped))
        host.runtime.write_text(json.dumps({'origin': 'http://127.0.0.1:1', 'pid': os.getpid(), 'port': 1}))
        reads = list(host.reads)
        self.assert_waiting(self.resume(card, 1, 'unbound-endpoint', *args), 'human intent')
        self.assertEqual(host.reads, reads)
        self.assertEqual((card.read_bytes(), self.control(card).read_bytes()), before)
        self.assertEqual(self.merge_calls(), [])

    def test_authoritative_human_intent_is_bound(self):
        host = self.host()
        card = self.card(confirmed='no')
        words = self.request_text(self.confirm(card, '--confirmed-by', 'human'), 'Confirm this card')
        host.readback(host.item(words))
        confirmed = self.confirm(card, '--confirmed-by', 'human')
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        self.assertIn('confirmed-by: human\n', card.read_text())
        self.assert_waiting(self.confirm(card, '--confirmed-by', 'human'), 'human intent')
        self.adopt(card)
        args = ('--merge-pr', 12)
        self.github.replies[f'api --hostname github.com repos/{REPO}/pulls/13'] = dict(
            self.github.replies[f'api --hostname github.com {PULL}'], number=13)
        words = self.request_text(self.resume(card, 1, 'preview', *args), self.COMMAND)
        before = self.control(card).read_bytes()
        for items, thread, run, target in (
                ([host.item(words.replace('--auto-merge', '--pause'))], None, None, 12),
                ([host.item(words, thread='different')], None, None, 12),
                ([host.item(words)], 'different', None, 12),
                ([host.item(words)], None, 'superseded', 12),
                ([host.item(words), host.item(words, item='another')], None, None, 12),
                ([host.item(words)], None, None, 13)):
            with self.subTest(thread=thread, run=run, target=target, items=items):
                host.readback(*items, thread=thread, run=run)
                self.assert_waiting(self.resume(card, 1, 'mismatch', '--merge-pr', target), 'human intent')
                self.assertEqual(self.control(card).read_bytes(), before)
        host.readback(host.item(words, source='mobile', item='merge-item'))
        wrong_rev = self.resume(card, 2, 'wrong-revision', *args)
        self.assert_waiting(wrong_rev, 'revision')
        accepted = self.resume(card, 1, 'verified', *args)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)['revision'], 2)
        self.assertTrue(all(call['arguments']['threadId'] == self.THREAD for call in host.reads), host.reads)
        self.assert_waiting(self.resume(card, 2, 'replay', *args), 'human intent')
        host.readback(host.item(words, item='new-item-for-stale-revision'))
        self.assert_waiting(self.resume(card, 2, 'stale-request', *args), 'human intent')
        self.assert_waiting(self.merge(card, 2, pr=13), 'does not match')
        self.assert_waiting(self.merge(card, 2, head='b' * 40), 'reviewed head')
        self.assertEqual(self.merge_calls(), [])
        copied = self.root / 'copied' / card.name
        copied.parent.mkdir()
        copied.write_bytes(card.read_bytes())
        self.control(copied).write_bytes(self.control(card).read_bytes())
        self.assert_waiting(self.merge(copied, 2), 'verified merge grant')
        self.assertEqual(self.merge_calls(), [])
        host.pr_readback(REPO, 12, SHA, BASE)
        submitted = self.merge(card, 2)
        self.assertEqual(submitted.returncode, 0, submitted.stderr)
        self.assertEqual(json.loads(submitted.stdout)['outcome'], 'merged')
        self.assert_waiting(self.merge(card, 3), 'consumed')
        self.assertEqual([call['name'] for call in host.pr_calls],
                         ['pull_request_read', 'pull_request_merge', 'pull_request_read'])
        self.assertEqual(self.merge_calls(), [])

    def test_relay_and_unattested_grants_cannot_merge(self):
        card = self.card(confirmed='no')
        confirmed = self.confirm(card, '--confirmed-by', 'relayed(Grok Bot)')
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        self.assertIn('delivery: pr-only\n', card.read_text())
        self.adopt(card)
        self.assert_waiting(self.resume(card, 1, 'relayed', '--merge-pr', 12), 'relayed')
        self.assertEqual(self.merge_calls(), [])
        legacy = self.root / 'legacy.md'
        legacy.write_text(card.read_text().replace('confirmed-by: relayed(Grok Bot)', 'confirmed-by: human')
                          .replace('auto-merge: no', 'auto-merge: yes')
                          .split('control:', 1)[0])
        other = self.root / 'legacy'
        other.mkdir()
        legacy = legacy.rename(other / 'autopilot.md')
        self.assertFalse(self.control(legacy).exists())
        command = ('merge', '--pr', 12, '--head', SHA, '--method', 'squash')
        self.assert_waiting(self.cli('pr_safety.py', *command, '--card', legacy), 'managed')
        self.assert_waiting(self.cli('pr_safety.py', *command), 'managed')
        self.assert_waiting(self.cli('run_control.py', 'adopt', legacy, '--source', 'legacy grant', '--merge-pr', 12),
                            'verified merge grant')
        record = self.control(card)
        saved = json.loads(record.read_bytes())
        saved['card_text'] = saved['card_text'].replace('confirmed-by: relayed(Grok Bot)', 'confirmed-by: human')
        saved['card_text'] = saved['card_text'].replace('auto-merge: no', 'auto-merge: yes')
        saved['merge_grant'] = {'repo': REPO, 'base': BASE, 'pr': 12}
        card.write_text(saved['card_text'])
        record.write_text(json.dumps(saved))
        self.assert_waiting(self.merge(card, 1), 'verified merge grant')
        self.assertEqual(self.merge_calls(), [])


if __name__ == '__main__':
    unittest.main()
