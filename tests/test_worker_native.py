#!/usr/bin/env python3
"""Only the registered worker's session record can prove startup and completed work."""

import json
import os
import time
import unittest

from lifecycle_case import WorkerLifecycleCase
from native_records import assistant_turn, session_record


class NativeProof(WorkerLifecycleCase):
    @staticmethod
    def without_times(path, runtime):
        if runtime == 'opencode':
            data = json.loads(path.read_text())
            for row in data['messages']:
                row.get('info', row).pop('time', None)
            path.write_text(json.dumps(data))
        else:
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            for row in rows:
                row.pop('timestamp', None)
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows))

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

    def test_startup_refuses_records_older_than_registration_even_when_copied_today(self):
        for runtime in ('claude', 'codex', 'pi', 'opencode'):
            with self.subTest(runtime=runtime):
                attempt, model = self.runtime_attempt(runtime)
                path = session_record(self.root / f'{runtime}.jsonl', runtime, runtime,
                                      self.root, model=model, timestamp=time.time() - 60)
                error = self.record('startup', attempt, native=str(path), code=2)
                self.assertIn('registration', error)
                self.assertIn('native', error)
                self.assertIn('record startup', error)
                session_record(path, runtime, runtime, self.root, model=model)
                self.assertTrue(self.record('startup', attempt, native=str(path))['verified'])

    def test_startup_uses_file_mtime_only_for_records_without_entry_times(self):
        for runtime in ('claude', 'codex', 'pi', 'opencode'):
            with self.subTest(runtime=runtime):
                attempt, model = self.runtime_attempt(runtime)
                path = session_record(self.root / f'{runtime}.jsonl', runtime, runtime, self.root, model=model)
                self.without_times(path, runtime)
                old = time.time() - 60
                os.utime(path, (old, old))
                error = self.record('startup', attempt, native=str(path), code=2)
                self.assertIn('registration', error)
                path.touch()
                self.assertTrue(self.record('startup', attempt, native=str(path))['verified'])

    def completion(self, *, submitted, untimed=False):
        """A result needs an assistant turn newer than the worker's productive-work boundary."""
        for runtime in ('claude', 'codex', 'pi', 'opencode'):
            with self.subTest(runtime=runtime):
                attempt, model = self.runtime_attempt(runtime)
                path = session_record(self.root / f'{runtime}.jsonl', runtime, runtime, self.root, model=model)
                if untimed:
                    self.without_times(path, runtime)
                self.record('startup', attempt, native=str(path))
                if submitted:
                    if not untimed:
                        assistant_turn(path, runtime)
                    self.record('submitted', attempt, evidence=['productive prompt accepted'])
                report = self.root / f'{runtime}.md'
                report.write_text('## Implemented\n\nWorker report.\n')
                request = self.root / 'collect.json'
                fields = {'status': 'completed', 'head': 'head-1', 'report': str(report), 'evidence': ['checks'],
                          'native': str(path), 'disposition': {'kind': 'retain', 'reason': 'recheck',
                          'next_use': 'recheck', 'evidence': ['collected report']}}
                for update in (None, 'user') if untimed else (None, 'user', 'old assistant'):
                    if update == 'user':
                        assistant_turn(path, runtime, role='user')
                        if untimed:
                            self.without_times(path, runtime)
                    elif update:
                        assistant_turn(path, runtime, timestamp=time.time() - 60)
                    request.write_text(json.dumps(fields))
                    error = self.cli('collect', '--attempt', attempt, '--input', str(request), code=2)
                    self.assertIn('assistant turn', error)
                    self.assertIn('native', error)
                assistant_turn(path, runtime)
                if untimed:
                    self.without_times(path, runtime)
                request.write_text(json.dumps({key: value for key, value in fields.items() if key != 'native'}))
                error = self.cli('collect', '--attempt', attempt, '--input', str(request), code=2)
                self.assertIn('native', error)
                other = session_record(self.root / f'other-{runtime}.jsonl', runtime, runtime, self.root, model=model)
                assistant_turn(other, runtime)
                request.write_text(json.dumps(dict(fields, native=str(other))))
                if runtime != 'opencode':
                    error = self.cli('collect', '--attempt', attempt, '--input', str(request), code=2)
                    self.assertIn(str(path), error)
                    self.assertIn('native', error)
                request.write_text(json.dumps(dict(fields, native=str(other if runtime == 'opencode' else path))))
                self.cli('collect', '--attempt', attempt, '--input', str(request))
                saved = self.cli('status', '--attempt', attempt)['attempts'][0]
                self.assertEqual(saved['result']['status'], 'completed')

    def test_collection_requires_an_assistant_turn_after_submission(self):
        self.completion(submitted=True)

    def test_a_native_child_without_submission_requires_a_turn_after_startup(self):
        self.completion(submitted=False)

    def test_untimed_records_need_a_new_assistant_entry_not_just_a_touched_file_or_user_message(self):
        self.completion(submitted=True, untimed=True)

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

    def test_opencode_2_collection_requires_a_new_assistant_in_a_fresh_export_of_the_same_session(self):
        self.execution.write_text(json.dumps({'host': 'opencode', 'native_dispatch': True,
            'agent': 'builder', 'job': 'ticket', 'settings': {
                'model': 'opencode/deepseek-v4.1-flash-free', 'effort': 'high'}}))
        self.register(runtime='opencode')
        ready = ({'type': 'text', 'text': 'READY'},)
        native = self.opencode_v2_export(ready)
        self.record('startup', native=str(native))
        self.record('submitted', evidence=['productive prompt accepted'])
        report = self.root / 'report.md'
        report.write_text('## Implemented\n')
        fields = dict(status='completed', head='head-1', report=str(report), evidence=['checks'], native=str(native))
        self.opencode_v2_export(ready)
        error = self.record('result', code=2, **fields)
        self.assertIn('assistant turn after submission', error)
        self.opencode_v2_export(ready, ({'type': 'text', 'text': 'done'},), session='different-session')
        self.assertIn('registered session', self.record('result', code=2, **fields))
        self.opencode_v2_export(ready, ({'type': 'text', 'text': 'done'},))
        fresh = self.root / 'fresh-export.json'
        fresh.write_bytes(native.read_bytes())
        self.record('result', **dict(fields, native=str(fresh)))
        self.assertEqual(self.cli('status')['attempts'][0]['result']['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
