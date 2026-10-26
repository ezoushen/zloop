#!/usr/bin/env python3
"""Collection and retirement of native workers."""

import json
import unittest

from lifecycle_case import WorkerLifecycleCase


class Retirement(WorkerLifecycleCase):
    def test_old_attempt_cannot_retire_an_unfinished_continuation(self):
        self.register()
        self.ready()
        self.collect()
        self.acknowledge()
        self.record('retain', reason='same-assignment recheck', next_use='recheck', evidence=['report'])
        self.register('ticket-1/2')
        self.herdr_fixture()
        request = self.root / 'retire.json'
        request.write_text(json.dumps({'session': 'fixture-session', 'pane': 'pane-1',
                                       'jobs_evidence': ['owned jobs inspected']}))
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertIn('final registered attempt', error)
        self.assertFalse((self.root / 'native-closed').exists())

    def test_retirement_resolves_moved_caller_identity(self):
        self.register()
        self.ready()
        self.collect()
        self.acknowledge()
        self.herdr_fixture(caller='pane-1')
        self.native_env['HERDR_PANE_ID'] = 'old-caller-id'
        request = self.root / 'retire.json'
        request.write_text(json.dumps({'session': 'fixture-session', 'pane': 'pane-1',
                                       'jobs_evidence': ['owned jobs inspected']}))
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertIn('Caller pane', error)
        self.assertFalse((self.root / 'native-closed').exists())

    def test_collect_persists_and_acknowledges_result_then_confirms_native_retirement(self):
        self.register()
        self.ready()
        self.watch()
        self.record('reconciled', evidence=['owned wait stopped and native report inspected'])
        self.herdr_fixture()
        report = self.root / 'report.md'
        report.write_text('Completed assignment at head-1\n')
        request = self.root / 'collect.json'
        request.write_text(json.dumps({'status': 'completed', 'head': 'head-1', 'report': str(report),
            'evidence': ['current report checked'], 'disposition': {'kind': 'retire',
            'session': 'fixture-session', 'pane': 'pane-1', 'jobs_evidence': ['owned jobs stopped'],
            'acceptance': {'decision': 'deferred', 'reason': 'acceptance checks pending'}}}))
        self.cli('collect', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertTrue((self.root / 'native-closed').is_file())
        status = self.cli('status')
        self.assertTrue(status['attempts'][0]['closed'])
        self.assertEqual(status['pending'], [])
        self.assertTrue(report.is_file())

    def test_collect_rejects_missing_disposition_before_recording_result(self):
        self.register()
        self.ready()
        report = self.root / 'report.md'
        report.write_text('Completed at head-1\n')
        request = self.root / 'collect.json'
        request.write_text(json.dumps({'status': 'completed', 'head': 'head-1', 'report': str(report),
                                       'evidence': ['checked report']}))
        self.cli('collect', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertNotIn('result', self.cli('status')['attempts'][0])

    def test_retirement_rejects_a_user_owned_pane(self):
        self.register(owned=False)
        self.ready()
        self.collect()
        self.acknowledge()
        self.herdr_fixture()
        request = self.root / 'retire.json'
        request.write_text(json.dumps({'session': 'fixture-session', 'pane': 'pane-1',
                                       'jobs_evidence': ['owned jobs inspected']}))
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertIn('Existing user worker is not a cleanup target', error)
        self.assertFalse((self.root / 'native-closed').exists())

    def test_retirement_rejects_the_caller_pane(self):
        self.register()
        self.ready()
        self.collect()
        self.acknowledge()
        self.herdr_fixture()
        self.native_env['HERDR_PANE_ID'] = 'pane-1'
        request = self.root / 'retire.json'
        request.write_text(json.dumps({'session': 'fixture-session', 'pane': 'pane-1',
                                       'jobs_evidence': ['owned jobs stopped'],
                                       'acceptance': {'decision': 'deferred', 'reason': 'acceptance pending'}}))
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertIn('Caller pane is not a cleanup target', error)
        self.assertFalse((self.root / 'native-closed').exists())

    def test_retirement_rejects_a_replaced_native_conversation(self):
        self.register()
        self.ready()
        self.collect()
        self.acknowledge()
        self.herdr_fixture(native_session='different-native-session')
        request = self.root / 'retire.json'
        request.write_text(json.dumps({'session': 'fixture-session', 'pane': 'pane-1',
                                       'jobs_evidence': ['owned jobs inspected'], 'acceptance': {'decision': 'deferred', 'reason': 'acceptance checks pending'}}))
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertIn('conversation no longer matches', error)
        self.assertFalse((self.root / 'native-closed').exists())

    def test_native_closure_recovery_preserves_report_and_does_not_close_twice(self):
        self.register()
        self.ready()
        self.herdr_fixture(verify_error='server_unavailable')
        report = self.root / 'report.md'
        report.write_text('Completed at head-1\n')
        request = self.root / 'collect.json'
        request.write_text(json.dumps({'status': 'completed', 'head': 'head-1', 'report': str(report),
            'evidence': ['checked report'], 'disposition': {'kind': 'retire',
            'session': 'fixture-session', 'pane': 'pane-1', 'jobs_evidence': ['owned jobs stopped'],
            'acceptance': {'decision': 'deferred', 'reason': 'acceptance checks pending'}}}))
        self.cli('collect', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        status = self.cli('status')
        self.assertIn('result', status['attempts'][0])
        self.assertNotIn('closed', status['attempts'][0])
        self.assertEqual(status['attempts'][0]['retirement_requested']['pane'], 'pane-1')
        self.herdr_fixture()
        self.cli('collect', '--attempt', 'ticket-1/1', '--input', str(request))
        self.cli('collect', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual((self.root / 'native-closed').read_text(), 'closed\n')

    def test_final_retirement_releases_worker_handle_of_earlier_retained_attempts(self):
        self.register()
        self.ready()
        self.collect()
        self.acknowledge()
        self.record('retain', reason='Same-assignment recheck needed', next_use='final recheck',
                    evidence=['review report'])
        self.register('ticket-1/2')
        self.ready('ticket-1/2')
        self.collect('ticket-1/2')
        self.acknowledge()
        self.herdr_fixture()
        request = self.root / 'retire.json'
        request.write_text(json.dumps({'session': 'fixture-session', 'pane': 'pane-1',
                                       'jobs_evidence': ['final same-assignment use, jobs stopped'],
                                       'acceptance': {'decision': 'deferred', 'reason': 'acceptance checks pending'}}))
        self.cli('retire', '--attempt', 'ticket-1/2', '--input', str(request))
        status = self.cli('status')
        self.assertEqual(status['attempts'][0]['native_closed_by'], 'ticket-1/2')
        self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual((self.root / 'native-closed').read_text(), 'closed\n')
        self.register('ticket-2/1', 'ticket-2', 'native-2', 'pane-1')
        self.register('ticket-3/1', 'ticket-3', 'native-1', 'different-pane', code=2)

    def test_report_must_survive_retirement_and_gate_checks_closed_worker(self):
        self.register()
        self.ready()
        report = self.collect()
        self.acknowledge()
        report.unlink()
        self.cli('gate', '--attempt', 'ticket-1/1', '--stage', 'accept', '--revision', 'head-1', code=1)
        self.record('closed', code=2, evidence=['native pane closure confirmed'])
        report.write_text('Different report content\n')
        self.cli('gate', '--attempt', 'ticket-1/1', '--stage', 'accept', '--revision', 'head-1', code=1)
        self.record('closed', code=2, evidence=['native pane closure confirmed'])
        report.write_text('## Implemented\n\nVerified assignment report at head-1\n')
        self.record('closed', evidence=['native pane closure confirmed'], acceptance={'decision': 'deferred', 'reason': 'acceptance checks pending'})
        self.cli('gate', '--attempt', 'ticket-1/1', '--stage', 'work', code=1)

    def test_retiring_a_worker_whose_report_changed_declines_it(self):
        # As closing does: a changed report can never be accepted, and its worker still retires once declined.
        self.register()
        self.ready()
        report = self.collect()
        self.acknowledge()
        report.write_text(report.read_text() + 'changed after collection\n')
        self.herdr_fixture()
        request = self.root / 'retire.json'
        retire = {'session': 'fixture-session', 'pane': 'pane-1', 'jobs_evidence': ['jobs stopped']}
        request.write_text(json.dumps(dict(retire, acceptance={'decision': 'deferred', 'reason': 'pending'})))
        self.assertIn('intact report', self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2))
        request.write_text(json.dumps(dict(retire, acceptance={'decision': 'declined', 'reason': 'report changed'})))
        self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual(self.cli('status')['attempts'][0]['declined']['reason'], 'report changed')

    def test_existing_user_worker_cannot_be_recorded_as_closed(self):
        self.register(owned=False)
        self.ready()
        self.collect()
        self.acknowledge()
        error = self.record('closed', code=2, evidence=['claimed pane closure'])
        self.assertIn('not a cleanup target', error)
        self.record('retain', reason='User-owned worker', next_use='Return control to owner', evidence=['ownership receipt'])




if __name__ == '__main__':
    unittest.main()
