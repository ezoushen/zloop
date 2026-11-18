#!/usr/bin/env python3
"""Managed controls reject invented user pauses, stale writes and diverged views."""
import datetime
import json
from pathlib import Path
import tempfile
import unittest

import support


class RunControl(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.card = support.run_card(self.root / 'autopilot.md', phase='align')
        self.control = support.load_script('run_control.py')

    def user(self, text, role='user', session='host-1'):
        path = self.root / 'native.jsonl'
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        rows = [{'type': 'session_meta', 'payload': {'id': session}},
                {'type': 'response_item', 'timestamp': stamp,
                 'payload': {'type': 'message', 'role': role,
                             'content': [{'type': 'input_text', 'text': text}]}}]
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        return self.control.user_source(path, 'codex', ['host-1'], 'pause', 0)

    def test_only_the_bound_users_explicit_command_can_pause(self):
        self.control.adopt(self.card, 'confirmed run')
        for text, role, session in [('Stopping now leaves gaps', 'user', 'host-1'),
                                    ('autopilot --pause', 'tool', 'host-1'),
                                    ('autopilot --pause', 'user', 'another-host')]:
            with self.subTest(text=text, role=role, session=session):
                with self.assertRaises(ValueError):
                    self.user(text, role, session)
        proof = self.user('autopilot --pause')
        paused = self.control.transition(self.card, 'paused-by-user', proof, 'user took control', 1, 'pause-1')
        self.assertEqual(paused['revision'], 2)
        with self.assertRaisesRegex(ValueError, 'paused-by-user'):
            self.control.require_active(self.card)

    def test_duplicate_requests_converge_but_stale_or_changed_requests_fail(self):
        self.control.adopt(self.card, 'confirmed run')
        proof = self.user('autopilot --pause')
        first = self.control.transition(self.card, 'paused-by-user', proof, 'pause', 1, 'p1')
        again = self.control.transition(self.card, 'paused-by-user', proof, 'pause', 1, 'p1')
        self.assertEqual(again, first)
        with self.assertRaisesRegex(ValueError, 'request'):
            self.control.transition(self.card, 'blocked', {'kind': 'host', 'reference': 'gate'}, 'blocked', 2, 'p1')
        with self.assertRaisesRegex(ValueError, 'revision'):
            self.control.transition(self.card, 'blocked', {'kind': 'host', 'reference': 'gate'}, 'blocked', 1, 'b1')

    def test_manual_divergence_is_unknown_and_legacy_cards_are_not_adopted_implicitly(self):
        self.assertIsNone(self.control.read(self.card))
        self.control.adopt(self.card, 'confirmed run')
        self.card.write_text(self.card.read_text().replace('terminal:', 'terminal: paused-by-user'))
        with self.assertRaisesRegex(ValueError, 'diverged'):
            self.control.require_active(self.card)
        manual = self.card.read_text()
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.control.restore_view(self.card, 'wrong-hash', 'reviewed diff', 'r1')
        restored = self.control.restore_view(self.card, self.control.digest(manual), 'reviewed diff', 'r1')
        self.assertEqual(restored['events'][-1]['displaced_view'], manual)
        self.control.require_active(self.card)

    def test_host_assertions_cannot_be_labeled_user_pause(self):
        self.control.adopt(self.card, 'confirmed run')
        with self.assertRaisesRegex(ValueError, 'user command'):
            self.control.transition(self.card, 'paused-by-user', {'kind': 'host', 'reference': 'hook diagnostic'},
                                    'pause', 1, 'p1')
        self.control.require_active(self.card)

    def test_delivered_state_cannot_invent_a_merge_grant(self):
        self.control.adopt(self.card, 'confirmed run')
        with self.assertRaisesRegex(ValueError, 'merge grant'):
            self.control.transition(self.card, 'merged', {'kind': 'host', 'reference': 'report'},
                                    'delivered', 1, 'merge')

    def test_phase_keeps_user_control_and_a_paused_run_cannot_advance(self):
        state = self.control.adopt(self.card, 'confirmed run')
        text = self.card.read_text().replace('phase: align', 'phase: refine')
        self.control.phase(self.card, text, state['revision'])
        self.assertEqual(self.control.read(self.card)['revision'], 2)
        proof = self.user('autopilot --pause')
        self.control.transition(self.card, 'paused-by-user', proof, 'pause', 2, 'pause')
        with self.assertRaisesRegex(ValueError, 'paused-by-user'):
            self.control.phase(self.card, text, 3)

    def test_supported_native_shapes_keep_user_role_and_session_checks(self):
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        message = {'role': 'user', 'content': [{'type': 'text', 'text': '/zloop:autopilot --pause'}]}
        cases = {
            'claude': [{'type': 'user', 'sessionId': 'host-1', 'timestamp': stamp, 'message': message}],
            'pi': [{'type': 'session', 'id': 'host-1'},
                   {'type': 'message', 'id': 'u', 'parentId': None, 'timestamp': stamp, 'message': message}],
            'opencode': {'info': {'id': 'host-1'}, 'messages': [
                {'info': {'role': 'user'}, 'timestamp': stamp, 'parts': message['content']}]},
            't3': {'thread': {'threadId': 'host-1'}, 'messages': [
                {'role': 'user', 'timestamp': stamp, 'text': 'autopilot --pause'}]},
        }
        for runtime, content in cases.items():
            with self.subTest(runtime=runtime):
                path = self.root / runtime
                path.write_text(json.dumps(content) if isinstance(content, dict) else
                                ''.join(json.dumps(row) + '\n' for row in content))
                self.assertEqual(self.control.user_source(path, runtime, ['host-1'], 'pause', 0)['kind'], 'user')


if __name__ == '__main__':
    unittest.main()
