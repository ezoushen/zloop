#!/usr/bin/env python3
"""Managed controls reject invented user pauses, stale writes and diverged views."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

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

    def test_interrupted_transition_retry_publishes_the_view_once(self):
        for interrupted in ('autopilot-control.json', 'autopilot.md'):
            with self.subTest(interrupted=interrupted):
                card = support.run_card(self.root / f'crash-{interrupted}' / 'autopilot.md', phase='align')
                self.control.adopt(card, 'confirmed run')
                before = card.read_text()
                replace = os.replace

                def interrupt(source, destination):
                    if destination.name == interrupted:
                        raise OSError('interrupted replacement')
                    replace(source, destination)

                request = (card, 'blocked', {'kind': 'host', 'reference': 'gate'}, 'blocked', 1, 'b1')
                with mock.patch('os.replace', side_effect=interrupt):
                    with self.assertRaisesRegex(OSError, 'interrupted replacement'):
                        self.control.transition(*request)
                self.assertEqual(card.read_text(), before)
                try:
                    result = self.control.transition(*request)
                except ValueError as error:
                    self.fail(f'Retrying the interrupted request should publish its view: {error}')
                self.assertEqual(result['revision'], 2)
                self.assertEqual([event['id'] for event in result['events']], ['adopt', 'b1'])
                self.assertIn('terminal: blocked\n', card.read_text())
                self.assertEqual(self.control.read(card), result)

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

    def test_end_cli_refuses_local_delivery_on_pr_only_and_keeps_legacy_and_pr_delivery(self):
        command = [sys.executable, '-B', str(support.SCRIPTS / 'run_control.py')]
        for delivery, terminal, reason in (
                ('pr-only', 'delivered-local', 'PR-only delivery requires a pull request'),
                (None, 'delivered-local', ''),
                ('pr-only', 'delivered', '')):
            with self.subTest(delivery=delivery, terminal=terminal):
                card = support.run_card(self.card.parent / f'{delivery}-{terminal}' / 'autopilot.md',
                                        phase='pr', **({'delivery': delivery} if delivery else {}))
                adopted = subprocess.run([*command, 'adopt', str(card), '--source', 'confirmed run'],
                                         capture_output=True, text=True)
                self.assertEqual(adopted.returncode, 0, adopted.stderr)
                record = card.with_name('autopilot-control.json')
                before = card.read_bytes(), record.read_bytes()
                end = subprocess.run([*command, 'end', str(card), '--terminal', terminal,
                                      '--source', 'delivery proof', '--reason', 'verified delivery',
                                      '--expect', '1', '--request-id', 'delivery'],
                                     capture_output=True, text=True)
                if reason:
                    self.assertEqual((end.returncode, end.stderr.strip()), (2, reason))
                    self.assertEqual((card.read_bytes(), record.read_bytes()), before)
                else:
                    self.assertEqual(end.returncode, 0, end.stderr)
                status = subprocess.run([*command, 'status', str(card)], capture_output=True, text=True)
                self.assertEqual(status.returncode, 0, status.stderr)
                view = json.loads(status.stdout)
                self.assertEqual((view['terminal'], view['revision'], view['last_event']['id']),
                                 ('', 1, 'adopt') if reason else (terminal, 2, 'delivery'))

    def test_status_reads_the_committed_transition_without_republishing_the_view(self):
        self.control.adopt(self.card, 'confirmed run')
        before = self.card.read_bytes()
        replace = os.replace

        def interrupt(source, destination):
            if destination == self.card:
                raise OSError('interrupted projection')
            replace(source, destination)

        with mock.patch('os.replace', side_effect=interrupt):
            with self.assertRaisesRegex(OSError, 'interrupted projection'):
                self.control.transition(self.card, 'blocked', {'kind': 'host', 'reference': 'gate'}, 'blocked', 1, 'b1')
        record = self.card.with_name('autopilot-control.json')
        saved = record.read_bytes()
        for present in (True, False):
            with self.subTest(present=present):
                if not present:
                    self.card.unlink()
                result = subprocess.run([sys.executable, '-B', str(support.SCRIPTS / 'run_control.py'),
                                         'status', str(self.card)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                status = json.loads(result.stdout)
                self.assertEqual((status['revision'], status['terminal'], status['last_event']['id']), (2, 'blocked', 'b1'))
                with self.assertRaisesRegex(ValueError, 'Run is blocked'):
                    self.control.require_active(self.card)
                self.assertEqual(record.read_bytes(), saved)
                if present:
                    self.assertEqual(self.card.read_bytes(), before)
                else:
                    self.assertFalse(self.card.exists())

    def test_restore_requires_a_review_of_the_current_bytes_and_discards_forged_authority(self):
        self.control.adopt(self.card, 'confirmed run')
        canonical = self.card.read_bytes()
        manual = (canonical.replace(b'phase: align', b'phase: pr')
                  .replace(b'terminal:\n', b'terminal: paused-by-user\n')
                  .replace(b'auto-merge: no', b'auto-merge: yes'))
        reviewed = hashlib.sha256(manual).hexdigest()
        current = manual.replace(b'\n', b'\r\n')
        self.card.write_bytes(current)
        request = (self.card, 'merged', {'kind': 'host', 'reference': 'gate'}, 'merge', 1, 'm1')
        with self.assertRaisesRegex(ValueError, 'diverged'):
            self.control.transition(*request)
        with self.assertRaisesRegex(ValueError, 'Manual view changed'):
            self.control.restore_view(self.card, reviewed, 'reviewed diff', 'r1')
        self.assertEqual(self.card.read_bytes(), current)
        restored = self.control.restore_view(self.card, hashlib.sha256(current).hexdigest(), 'fresh review', 'r1')
        self.assertEqual(restored['events'][-1]['displaced_view'].encode(), current)
        self.assertEqual(self.card.read_bytes(), canonical)
        with self.assertRaisesRegex(ValueError, 'existing merge grant'):
            self.control.transition(*request[:4], 2, request[-1])

    def test_restore_cli_preserves_reviewed_malformed_utf8_bytes(self):
        self.control.adopt(self.card, 'confirmed run')
        canonical = self.card.read_bytes()
        manual = canonical + b'\r\nReviewed edit: \xff\x80\xe2\x82\r\n'
        self.card.write_bytes(manual)
        reviewed = hashlib.sha256(manual).hexdigest()
        record = self.card.with_name('autopilot-control.json')
        command = [sys.executable, '-B', str(support.SCRIPTS / 'run_control.py')]
        restore = command + ['restore-view', str(self.card), '--source', 'reviewed bytes',
                             '--request-id', 'restore-1', '--expect-view', reviewed]

        result = subprocess.run(restore, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        event = json.loads(record.read_bytes())['events'][-1]
        self.assertEqual(event['view_hash'], reviewed)
        self.assertEqual(event['displaced_view'].encode('utf-8', errors='surrogateescape'), manual)
        self.assertEqual(self.card.read_bytes(), canonical)

        changed = manual.replace(b'\xff', b'\xfe')
        self.card.write_bytes(changed)
        saved = record.read_bytes()
        result = subprocess.run(command + ['status', str(self.card)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('diverged', result.stderr)
        result = subprocess.run(restore, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('Manual view changed', result.stderr)
        self.assertEqual(self.card.read_bytes(), changed)
        self.assertEqual(record.read_bytes(), saved)

    def test_interrupted_reconciliation_archives_manual_edit_only_once(self):
        self.control.adopt(self.card, 'confirmed run')
        canonical = self.card.read_bytes()
        manual = canonical + b'\nUnrelated edit: \xff\x80\n'
        self.card.write_bytes(manual)
        record = self.card.with_name('autopilot-control.json')
        replace = os.replace

        for step in ('publish view', 'clear pending'):
            with self.subTest(step=step):
                self.card.write_bytes(manual)
                request = (self.card, hashlib.sha256(manual).hexdigest(), 'reviewed diff', step)
                events_before = len(json.loads(record.read_bytes())['events'])

                def interrupt(source, destination):
                    if (step == 'publish view' and destination == self.card
                            or step == 'clear pending' and destination == record and self.card.read_bytes() == canonical):
                        raise OSError('interrupted reconciliation')
                    replace(source, destination)

                with mock.patch('os.replace', side_effect=interrupt):
                    with self.assertRaisesRegex(OSError, 'interrupted reconciliation'):
                        self.control.restore_view(*request)
                self.assertEqual(self.card.read_bytes(), manual if step == 'publish view' else canonical)
                try:
                    result = self.control.read(self.card)
                except ValueError as error:
                    self.fail(f'Reading the interrupted reconciliation should accept the reviewed predecessor: {error}')
                self.assertEqual(len(result['events']), events_before + 1)
                displaced = result['events'][-1]['displaced_view']
                self.assertEqual(displaced.encode('utf-8', errors='surrogateescape'), manual)
                self.assertEqual(result['revision'], events_before + 1)
                recovered = self.control.restore_view(*request)
                self.assertEqual(recovered['events'], result['events'])
                self.assertEqual(self.card.read_bytes(), canonical)
                self.card.write_bytes(manual)
                with self.assertRaisesRegex(ValueError, 'Manual view changed'):
                    self.control.restore_view(*request)

    def test_an_old_restore_request_does_not_overwrite_a_newer_reviewed_view(self):
        self.control.adopt(self.card, 'confirmed run')
        canonical = self.card.read_bytes()
        first = canonical + b'\nEarlier manual view.\n'
        self.card.write_bytes(first)
        old_request = (self.card, hashlib.sha256(first).hexdigest(), 'first review', 'r1')
        self.control.restore_view(*old_request)
        second = canonical + b'\nLater manual view.\n'
        self.card.write_bytes(second)
        new_request = (self.card, hashlib.sha256(second).hexdigest(), 'second review', 'r2')
        replace = os.replace

        def interrupt(source, destination):
            if destination == self.card:
                raise OSError('interrupted later reconciliation')
            replace(source, destination)

        with mock.patch('os.replace', side_effect=interrupt):
            with self.assertRaisesRegex(OSError, 'interrupted later reconciliation'):
                self.control.restore_view(*new_request)
        with self.assertRaisesRegex(ValueError, 'Manual view changed'):
            self.control.restore_view(*old_request)
        self.assertEqual(self.card.read_bytes(), second)
        self.control.restore_view(*new_request)
        self.assertEqual(self.card.read_bytes(), canonical)

    def test_host_assertions_cannot_be_labeled_user_pause(self):
        self.control.adopt(self.card, 'confirmed run')
        with self.assertRaisesRegex(ValueError, 'user command'):
            self.control.transition(self.card, 'paused-by-user', {'kind': 'host', 'reference': 'hook diagnostic'},
                                    'pause', 1, 'p1')
        self.control.require_active(self.card)

    def test_a_takeover_older_than_the_user_pause_cannot_resume_it(self):
        self.control.adopt(self.card, 'confirmed run')
        native = self.root / 'ordered.jsonl'
        rows = [{'type': 'session_meta', 'payload': {'id': 'host-1'}}]
        def append(command):
            rows.append({'type': 'response_item',
                         'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                         'payload': {'role': 'user', 'content': [{'type': 'input_text', 'text': command}]}})
            native.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        append('autopilot --take-over')
        old = self.control.user_source(native, 'codex', ['host-1'], 'resume', 0)
        append('autopilot --pause')
        pause = self.control.user_source(native, 'codex', ['host-1'], 'pause', 0)
        self.control.transition(self.card, 'paused-by-user', pause, 'actual pause', 1, 'pause')
        with self.assertRaisesRegex(ValueError, 'recent explicit user command'):
            self.control.transition(self.card, '', old, 'stale takeover', 2, 'old-resume')
        with self.assertRaisesRegex(ValueError, 'paused-by-user'):
            self.control.require_active(self.card)
        append('autopilot --take-over')
        fresh = self.control.user_source(native, 'codex', ['host-1'], 'resume', 0)
        self.control.transition(self.card, '', fresh, 'new takeover', 2, 'new-resume')
        self.control.require_active(self.card)

    def test_a_host_decision_cannot_replace_an_explicit_user_pause(self):
        self.control.adopt(self.card, 'confirmed recovery grant', allow_resume=True)
        proof = self.user('autopilot --pause')
        self.control.transition(self.card, 'blocked', {'kind': 'host', 'reference': 'diagnostic'},
                                'host decision before pause was processed', 1, 'block')
        paused = self.control.transition(self.card, 'paused-by-user', proof, 'pause', 2, 'pause')
        for terminal in self.control.TERMINAL_STATES:
            if terminal == 'paused-by-user':
                continue
            with self.subTest(terminal=terminal):
                with self.assertRaisesRegex(ValueError, 'paused-by-user'):
                    self.control.transition(self.card, terminal, {'kind': 'host', 'reference': 'diagnostic'},
                                            'host decision', 3, 'host-' + terminal)
        self.assertEqual(self.control.read(self.card), paused)

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

    def test_retrying_phase_command_republishes_without_recording_the_phase_twice(self):
        self.control.adopt(self.card, 'confirmed run')
        replace = os.replace

        def interrupt(source, destination):
            if destination == self.card:
                raise OSError('interrupted phase publication')
            replace(source, destination)

        text = self.card.read_text().replace('phase: align', 'phase: refine')
        with mock.patch('os.replace', side_effect=interrupt):
            with self.assertRaisesRegex(OSError, 'interrupted phase publication'):
                self.control.phase(self.card, text, 1)
        self.card.unlink()
        result = subprocess.run([sys.executable, '-B', str(support.SCRIPTS / 'autopilot_card.py'),
                                 'phase', str(self.card), 'refine'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.control.read(self.card)
        self.assertEqual(state['revision'], 2)
        self.assertEqual([event.get('phase') for event in state['events']], [None, 'refine'])
        self.assertIn('phase: refine\n', self.card.read_text())

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

    def test_native_claude_slash_command_rows_are_exact_user_commands(self):
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        tags = ('<command-message>zloop:autopilot</command-message>\n<command-name>/zloop:autopilot</command-name>\n'
                '<command-args>{}</command-args>')

        def source(content, action='pause', **row):
            path = self.root / 'claude.jsonl'
            body = dict(type='user', sessionId='host-1', timestamp=stamp,
                        message={'role': 'user', 'content': content})
            body.update(row)
            bound = dict(type='user', sessionId='host-1', timestamp=stamp,
                         message={'role': 'user', 'content': 'hello'})
            path.write_text(json.dumps(bound) + '\n' + json.dumps(body) + '\n')
            return self.control.user_source(path, 'claude', ['host-1'], action, 0)

        self.assertEqual(source(tags.format('--pause'))['command'], 'pause')
        self.assertEqual(source([{'type': 'text', 'text': tags.format('--pause')}])['command'], 'pause')
        resumed = source(tags.format('--take-over --auto-merge'), 'resume')
        self.assertEqual((resumed['command'], resumed['flags']), ('resume', ['--take-over', '--auto-merge']))
        self.assertEqual(source(tags.format('--take-over'), 'resume')['flags'], ['--take-over'])
        for content, action, row in [
                (tags.format('--pause --auto-merge'), 'pause', {}),
                (tags.format('--pause now'), 'pause', {}),
                (tags.format('--take-over --other'), 'resume', {}),
                (tags.format('--pause'), 'resume', {}),
                (tags.format('--pause').replace('/zloop:autopilot', '/zloop:other'), 'pause', {}),
                ('I typed ' + tags.format('--pause'), 'pause', {}),
                (tags.format('--pause') + '\n' + tags.format('--pause'), 'pause', {}),
                (tags.format('--pause'), 'pause', {'sessionId': 'another'}),
                (tags.format('--pause'), 'pause', {'isSidechain': True}),
                (tags.format('--pause'), 'pause', {'agentId': 'a1'})]:
            with self.subTest(content=content, action=action, row=row):
                with self.assertRaises(ValueError):
                    source(content, action, **row)
        path = self.root / 'late.jsonl'
        path.write_text(json.dumps(dict(type='user', sessionId='host-1', timestamp=stamp,
                                        message={'role': 'user', 'content': tags.format('--pause')})) + '\n')
        with self.assertRaises(ValueError):
            self.control.user_source(path, 'claude', ['host-1'], 'pause', time.time() + 100)


if __name__ == '__main__':
    unittest.main()
