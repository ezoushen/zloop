#!/usr/bin/env python3
"""Only the registered worker's session record can prove startup and completed work."""

import json
import os
from pathlib import Path
import unittest

from lifecycle_case import WorkerLifecycleCase
from native_records import assistant_turn, session_record, user_message


class NativeProof(WorkerLifecycleCase):
    def test_registration_refuses_every_registering_host_session_as_a_worker(self):
        for runtime in ('claude', 'codex', 'pi', 'opencode'):
            self.state = self.root / f'{runtime}.sqlite'
            self.execution.write_text(json.dumps({'host': runtime, 'native_dispatch': True,
                'agent': 'builder', 'job': 'ticket', 'settings': {}}))
            self.native_env = dict(os.environ, CLAUDE_CODE_SESSION_ID='claude-host',
                                   CODEX_SESSION_ID='codex-host', PI_SESSION_ID='pi-host')
            for session in ('claude-host', 'codex-host', 'pi-host'):
                with self.subTest(runtime=runtime, session=session):
                    error = self.register(f'{runtime}/{session}', runtime=runtime, session=session,
                                          worker=session, code=2)
                    self.assertIn('host session', error)
                    self.assertIn('fresh worker', error)
            with self.subTest(runtime=runtime, persisted=True):
                self.assertEqual(self.cli('status')['attempts'], [])

    def runtime_attempt(self, runtime):
        model = 'fixture/model' if runtime in ('pi', 'opencode') else 'model'
        self.execution.write_text(json.dumps({'host': runtime, 'native_dispatch': True,
            'agent': 'builder', 'job': 'ticket', 'settings': {'model': model, 'effort': 'high'}}))
        attempt = f'{runtime}/1'
        self.register(attempt, assignment=runtime, session=runtime, worker=runtime, runtime=runtime)
        return attempt, model

    def test_startup_requires_native_and_refuses_host_typed_settings_for_every_runtime(self):
        for runtime in ('claude', 'codex', 'pi', 'opencode'):
            self.execution.write_text(json.dumps({'host': runtime, 'native_dispatch': True,
                'agent': 'builder', 'job': 'ticket', 'settings': {}}))
            attempt = f'{runtime}/1'
            self.register(attempt, assignment=runtime, session=runtime, worker=runtime, runtime=runtime)
            for fields in ({}, {'observed': {'runtime': runtime}, 'evidence': ['host says it ran']}):
                with self.subTest(runtime=runtime, fields=fields):
                    error = self.record('startup', attempt, code=2, **fields)
                    self.assertIn('native', error)
                    self.assertIn('record', error)
            self.assertFalse(self.cli('status', '--attempt', attempt)['attempts'][0]['verified'])

    def test_startup_reads_settings_and_identity_from_each_registered_session(self):
        for runtime in ('claude', 'codex', 'pi', 'opencode'):
            with self.subTest(runtime=runtime):
                attempt, model = self.runtime_attempt(runtime)
                path = self.root / f'{runtime}.jsonl'
                for child in (False, True) if runtime in ('claude', 'codex') else (False,):
                    session_record(path, runtime, 'someone-else', self.root, model=model, child=child)
                    error = self.record('startup', attempt, native=str(path), code=2)
                    self.assertIn('registered session', error)
                    self.assertIn('native', error.lower())
                    session_record(path, runtime, runtime, self.root, model=model, child=child)
                    for field in ('observed', 'evidence'):
                        error = self.record('startup', attempt, native=str(path), code=2,
                                            **{field: {} if field == 'observed' else ['typed']})
                        self.assertIn('native', error)
                    self.assertTrue(self.record('startup', attempt, native=str(path))['verified'])
                    saved = self.cli('status', '--attempt', attempt)['attempts'][0]
                    self.assertEqual(saved['observed'], {'runtime': runtime, 'model': model, 'effort': 'high'})

    def result_request(self, attempt, path):
        report = self.root / (attempt.replace('/', '-') + '.md')
        report.write_text('## Implemented\n\nWorker report.\n')
        request = self.root / 'collect.json'
        request.write_text(json.dumps({'status': 'completed', 'head': 'head-1',
            'report': str(report), 'evidence': ['checks'], 'native': str(path),
            'disposition': {'kind': 'retain', 'reason': 'recheck', 'next_use': 'recheck',
                            'evidence': ['report read']}}))
        return str(request)

    def test_collection_requires_this_attempt_in_the_users_own_text(self):
        for runtime in ('claude', 'codex', 'pi', 'opencode'):
            with self.subTest(runtime=runtime):
                attempt, model = self.runtime_attempt(runtime)
                path = session_record(self.root / f'{runtime}.jsonl', runtime, runtime, self.root, model=model)
                self.record('startup', attempt, native=str(path))
                request = self.result_request(attempt, path)
                for text, kind in (('an unrelated live session', 'text'),
                                   (f'attempt: {attempt}-different', 'text'),
                                   (f'assignment: fixture  attempt: {attempt}', 'tool_result'),
                                   (f'assignment: fixture  attempt: {attempt}', 'tool_input')):
                    with self.subTest(kind=kind, text=text):
                        user_message(path, runtime, text, kind=kind)
                        assistant_turn(path, runtime)
                        error = self.cli('collect', '--attempt', attempt, '--input', request, code=2)
                        self.assertIn('send the worker its packet', error)
                        self.assertIn(f'assignment: {runtime}  attempt: {attempt}', error)
                user_message(path, runtime, f'assignment: {runtime}  attempt: {attempt}')
                assistant_turn(path, runtime)
                self.cli('collect', '--attempt', attempt, '--input', request)
                self.assertEqual(self.cli('status', '--attempt', attempt)['attempts'][0]['result']['status'],
                                 'completed')

    def test_collection_waits_for_an_assistant_reply_after_the_packet(self):
        for runtime in ('claude', 'codex', 'pi', 'opencode'):
            with self.subTest(runtime=runtime):
                attempt, model = self.runtime_attempt(runtime)
                path = session_record(self.root / f'{runtime}.jsonl', runtime, runtime, self.root, model=model)
                self.record('startup', attempt, native=str(path))
                assistant_turn(path, runtime)
                user_message(path, runtime, f'assignment: {runtime}  attempt: {attempt}')
                request = self.result_request(attempt, path)
                error = self.cli('collect', '--attempt', attempt, '--input', request, code=2)
                self.assertIn('let the worker answer it, then collect', error)
                assistant_turn(path, runtime)
                self.cli('collect', '--attempt', attempt, '--input', request)

    def test_a_finished_native_child_is_collectible_with_its_packet_and_reply_before_startup(self):
        for runtime in ('claude', 'codex', 'pi', 'opencode'):
            with self.subTest(runtime=runtime):
                attempt, model = self.runtime_attempt(runtime)
                path = session_record(self.root / f'{runtime}.jsonl', runtime, runtime, self.root,
                                      model=model, timestamp=1, child=runtime in ('claude', 'codex'))
                user_message(path, runtime, f'assignment: {runtime}  attempt: {attempt}')
                assistant_turn(path, runtime, timestamp=1)
                os.utime(path, (1, 1))
                self.record('startup', attempt, native=str(path))
                request = self.result_request(attempt, path)
                fields = json.loads(Path(request).read_text())
                del fields['native']
                Path(request).write_text(json.dumps(fields))
                error = self.cli('collect', '--attempt', attempt, '--input', request, code=2)
                self.assertIn('requires native', error)
                other = self.root / f'other-{runtime}.jsonl'
                other.write_bytes(path.read_bytes())
                request = self.result_request(attempt, other)
                if runtime != 'opencode':
                    error = self.cli('collect', '--attempt', attempt, '--input', request, code=2)
                    self.assertIn('startup session record', error)
                    request = self.result_request(attempt, path)
                self.cli('collect', '--attempt', attempt, '--input', request)

    def test_pi_ignores_packets_and_replies_on_an_abandoned_branch(self):
        attempt, model = self.runtime_attempt('pi')
        path = session_record(self.root / 'pi.jsonl', 'pi', 'pi', self.root, model=model)
        self.record('startup', attempt, native=str(path))
        user_message(path, 'pi', f'assignment: pi  attempt: {attempt}')
        assistant_turn(path, 'pi')
        with path.open('a') as stream:
            stream.write(json.dumps({'type': 'model_change', 'id': 'branch', 'parentId': 'probe',
                                     'provider': 'fixture', 'modelId': 'model'}) + '\n')
        error = self.cli('collect', '--attempt', attempt, '--input', self.result_request(attempt, path), code=2)
        self.assertIn('send the worker its packet', error)

    def test_effort_levels_pi_run3_cannot_verify_a_claude_worker_without_its_record(self):
        # The recorded failure had worker_seconds=0.294 and used the orchestrator's own spec-review.md.
        self.execution.write_text(json.dumps({'host': 'pi', 'target_runtime': 'claude', 'native_dispatch': True,
            'agent': 'reviewer', 'job': 'diff-review', 'settings': {'model': 'sonnet', 'effort': 'high'}}))
        report = self.root / 'spec-review.md'
        report.write_text('review_status: clean\n## Coverage\n## Review round 1\n')
        self.register('review/two-tickets/1', assignment='review/two-tickets', session='native-review/two-tickets/1')
        error = self.record('startup', 'review/two-tickets/1', code=2,
                            observed={'runtime': 'claude', 'model': 'sonnet', 'effort': 'high'},
                            evidence=['native.jsonl:1'])
        self.assertIn('native', error)
        self.assertIn('record startup', error)
        saved = self.cli('status')['attempts'][0]
        self.assertFalse(saved['verified'])
        self.assertNotIn('result', saved)

    def test_opencode_2_collection_reads_its_user_text_and_reply_from_a_fresh_export(self):
        self.execution.write_text(json.dumps({'host': 'opencode', 'native_dispatch': True,
            'agent': 'builder', 'job': 'ticket', 'settings': {
                'model': 'opencode/deepseek-v4.1-flash-free', 'effort': 'high'}}))
        self.register(runtime='opencode')
        native = self.opencode_v2_export(({'type': 'text', 'text': 'READY'},))
        self.record('startup', native=str(native))
        self.record('submitted', evidence=['productive prompt accepted'])
        fresh = self.root / 'fresh-export.json'
        fresh.write_bytes(native.read_bytes())
        request = self.result_request('ticket-1/1', fresh)
        error = self.cli('collect', '--attempt', 'ticket-1/1', '--input', request, code=2)
        self.assertIn('send the worker its packet', error)
        user_message(fresh, 'opencode', 'assignment: ticket-1  attempt: ticket-1/1')
        error = self.cli('collect', '--attempt', 'ticket-1/1', '--input', request, code=2)
        self.assertIn('let the worker answer it, then collect', error)
        data = json.loads(fresh.read_text())
        data['messages'].append(dict(data['messages'][1], id='reply'))
        fresh.write_text(json.dumps(data))
        self.cli('collect', '--attempt', 'ticket-1/1', '--input', request)


if __name__ == '__main__':
    unittest.main()
