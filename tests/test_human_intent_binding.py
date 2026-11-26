#!/usr/bin/env python3
"""Check conversation binding and provider scope through the control commands."""
import re
import unittest

import support
import test_pr_safety
from t3_intent_fixture import T3Read


class ConversationBinding(unittest.TestCase):
    THREAD = 'verified-t3-conversation'
    PROVIDER = 'codex-session-before'
    TAKE_OVER = 'autopilot --take-over'

    def setUp(self):
        self.github = test_pr_safety.PRSafety()
        self.github.setUp()
        self.addCleanup(self.github.doCleanups)
        self.env = self.github.env
        self.env['CODEX_SESSION_ID'] = self.PROVIDER
        self.host = self.github.t3 = T3Read(self, self.github.root, self.env, self.THREAD)
        self.cli = self.github.run_cli

    def card(self, name, **state):
        return support.run_card(self.github.root / name / 'autopilot.md', phase='pr',
                                board='https://example.org/tickets',
                                **{'mode': 'full', 'dispatch-effort': 'medium', 'review-effort': 'medium', **state})

    def request(self, result, words):
        self.assertEqual(result.returncode, 2, result.stderr)
        match = re.search(rf'(?m)^{re.escape(words)}\nzloop-intent: [0-9a-f]{{64}}$', result.stderr)
        self.assertIsNotNone(match, result.stderr)
        return match[0]

    def test_confirmation_pins_the_t3_conversation_through_adoption(self):
        card = self.card('fresh', confirmed='no', session='')
        confirm = ('autopilot_card.py', 'confirm', str(card), '--words', 'Confirm this card')
        reply = self.request(self.cli(*confirm), 'Confirm this card')
        self.host.readback(self.host.item(reply, item='original-confirmation'))
        result = self.cli(*confirm)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'session: {self.PROVIDER}, {self.THREAD}\n', card.read_text())
        result = self.cli('run_control.py', 'adopt', str(card), '--source', 'confirmed')
        self.assertEqual(result.returncode, 0, result.stderr)
        control = card.with_name('autopilot-control.json')
        before = card.read_bytes(), control.read_bytes()
        resume = ('run_control.py', 'resume', str(card), '--expect', '1', '--request-id', 'resume',
                  '--reason', 'continue the confirmed run')

        for thread in (self.PROVIDER, 'unrelated-t3-conversation'):
            self.env['T3CODE_THREAD_ID'] = thread
            self.host.readback()
            reply = self.request(self.cli(*resume), self.TAKE_OVER)
            self.host.readback(self.host.item(reply, thread=thread, item='other-thread-item'), thread=thread)
            reads = list(self.host.reads)
            refused = self.cli(*resume)
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
            self.assertIn('waiting-on-user:', refused.stderr)
            self.assertIn('no current T3 thread is bound', refused.stderr)
            self.assertEqual(self.host.reads, reads)
            self.assertEqual((card.read_bytes(), control.read_bytes()), before)

        self.env['T3CODE_THREAD_ID'] = self.THREAD
        self.host.readback()
        reply = self.request(self.cli(*resume), self.TAKE_OVER)
        self.host.readback(self.host.item(reply, item='original-confirmation'))
        replay = self.cli(*resume)
        self.assertEqual(replay.returncode, 2, replay.stdout + replay.stderr)
        self.assertIn('not a fresh matching human response', replay.stderr)
        self.assertEqual((card.read_bytes(), control.read_bytes()), before)
        self.host.readback(self.host.item(reply, item='bound-takeover'))
        result = self.cli(*resume)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.host.reads[-1]['arguments']['threadId'], self.THREAD)
        self.assertIn(f'intent-thread: {self.THREAD}\n', card.read_text())

        old = self.card('old', session=f'{self.PROVIDER}, {self.THREAD}',
                        **{'confirmed-by': 'human', 'confirmation-item': 'prior-confirmation'})
        before = old.read_bytes()
        legacy = self.cli('autopilot_card.py', 'take-over', str(old))
        self.assertEqual(legacy.returncode, 2, legacy.stderr)
        self.assertIn('waiting-on-user:', legacy.stderr)
        self.assertIn('no current T3 thread is bound', legacy.stderr)
        self.assertIn('confirm a fresh run card in T3', legacy.stderr)
        self.assertNotIn('zloop-intent:', legacy.stderr)
        reads = list(self.host.reads)
        old_confirmation = self.cli('autopilot_card.py', 'confirm', str(old), '--confirmed-by', 'human',
                                    '--words', 'Confirm the historical card')
        self.assertEqual(old_confirmation.returncode, 2, old_confirmation.stdout + old_confirmation.stderr)
        self.assertIn('confirm a fresh run card in T3', old_confirmation.stderr)
        self.assertNotIn('zloop-intent:', old_confirmation.stderr)
        self.assertEqual(self.host.reads, reads)
        self.assertEqual(old.read_bytes(), before)
        phase = self.cli('autopilot_card.py', 'phase', str(old), 'dispatch')
        self.assertEqual(phase.returncode, 0, phase.stderr)
        adopted = self.cli('run_control.py', 'adopt', str(old), '--source', 'old PR-only work')
        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        old_control = old.with_name('autopilot-control.json')
        before = old.read_bytes(), old_control.read_bytes()
        old_resume = (*resume[:2], str(old), *resume[3:])
        self.host.readback()
        self.host.readback(self.host.item(self.TAKE_OVER, item='ambiguous-card-item'))
        reads = list(self.host.reads)
        refused = self.cli(*old_resume)
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn('no current T3 thread is bound', refused.stderr)
        self.assertIn('confirm a fresh run card in T3', refused.stderr)
        self.assertNotIn('zloop-intent:', refused.stderr)
        self.assertEqual(self.host.reads, reads)
        self.assertEqual((old.read_bytes(), old_control.read_bytes()), before)

        for mode, flags in (('full', ('--confirmed-by', 'relayed(Grok Bot)')), ('direct', ())):
            setup = self.card(f'setup-{mode}', confirmed='no', session='', mode=mode)
            reads = list(self.host.reads)
            confirmed = self.cli('autopilot_card.py', 'confirm', str(setup), '--words', 'Continue PR work', *flags)
            self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
            self.assertEqual(self.host.reads, reads)
            self.assertIn(f'intent-thread: {self.THREAD}\n', setup.read_text())
            adopted = self.cli('run_control.py', 'adopt', str(setup), '--source', 'PR-only setup')
            self.assertEqual(adopted.returncode, 0, adopted.stderr)
            retry = (*resume[:2], str(setup), *resume[3:])
            if flags:
                refused = self.cli(*retry, '--merge-pr', '12')
                self.assertEqual(refused.returncode, 2, refused.stderr)
                self.assertIn('relayed confirmation cannot authorize auto-merge', refused.stderr)
            self.host.readback()
            self.host.accept(self.cli(*retry), self.TAKE_OVER)
            taken = self.cli(*retry)
            self.assertEqual(taken.returncode, 0, taken.stderr)

    def test_provider_sessions_are_bound_in_both_legacy_previews(self):
        confirmation = self.card('confirmation', confirmed='no', session='')
        takeover = self.card('takeover', session=self.THREAD, terminal='delivered', **{'intent-thread': self.THREAD})
        provider_after = 'codex-session-after'
        self.env['CLAUDE_CODE_SESSION_ID'] = 'claude-session-also-proposed'
        for card, action, words, options in (
                (confirmation, 'confirm', 'Confirm this card', ('--words', 'Confirm this card')),
                (takeover, 'take-over', self.TAKE_OVER, ())):
            command = ('autopilot_card.py', action, str(card), *options)
            before = card.read_bytes()
            self.env['CODEX_SESSION_ID'] = self.PROVIDER
            self.host.readback()
            preview = self.cli(*command)
            first = self.request(preview, words)
            self.env['CODEX_SESSION_ID'] = provider_after
            changed = self.cli(*command)
            second = self.request(changed, words)
            self.assertNotEqual(first, second, changed.stderr)
            self.assertIn('with provider sessions ["claude-session-also-proposed", "codex-session-before"]',
                          preview.stderr)
            self.assertIn('with provider sessions ["claude-session-also-proposed", "codex-session-after"]',
                          changed.stderr)
            self.assertEqual(card.read_bytes(), before)

            self.host.readback(self.host.item(first, item=f'{action}-intent'))
            refused = self.cli(*command)
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
            self.assertIn('not a fresh matching human response', refused.stderr)
            self.assertIn(second, refused.stderr)
            self.assertEqual(card.read_bytes(), before)

            self.env['CODEX_SESSION_ID'] = self.PROVIDER
            result = self.cli(*command)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f'session: claude-session-also-proposed, {self.PROVIDER}, {self.THREAD}\n',
                          card.read_text())


if __name__ == '__main__':
    unittest.main()
