#!/usr/bin/env python3
"""Collection and retirement of native workers."""

import json
import shlex
import subprocess
import sys
import unittest

import support
from lifecycle_case import SCRIPT, WorkerLifecycleCase
from native_records import codex_child_session, session_record


class Retirement(WorkerLifecycleCase):
    def test_old_attempt_cannot_retire_an_unfinished_continuation(self):
        self.settled()
        self.record('retain', reason='same-assignment recheck', next_use='recheck', evidence=['report'])
        self.register('ticket-1/2')
        self.herdr_fixture()
        request = self.root / 'retire.json'
        request.write_text(json.dumps({'session': 'fixture-session', 'pane': 'pane-1',
                                       'jobs_evidence': ['owned jobs inspected']}))
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertIn('final registered attempt', error)
        self.assertFalse((self.root / 'native-closed').exists())

    def test_continuation_cannot_register_during_pane_close(self):
        self.herdr_fixture()
        self.settled(worker='builder-1', herdr={'session': 'fixture-session'})
        self.record('retain', reason='recheck', next_use='same assignment recheck', evidence=['report'])
        registration = self.root / 'continuation.json'
        registration.write_text(json.dumps(dict(kind='register', attempt='ticket-1/2',
            **self.registration('ticket-1/2', worker='builder-1', herdr={'session': 'fixture-session'}))))
        argv = [sys.executable, '-B', str(SCRIPT), 'record', '--state', str(self.state),
                '--input', str(registration)]
        outcome = self.root / 'continuation-outcome.json'
        fake = self.root / 'bin/herdr'
        boundary = "elif args[2:]==['pane','close','pane-1']:\n"
        injection = ("    import subprocess\n"
                     f"    done=subprocess.run({argv!r}, capture_output=True, text=True, timeout=10)\n"
                     f"    Path({str(outcome)!r}).write_text(json.dumps(dict(code=done.returncode, error=done.stderr)))\n")
        fake.write_text(fake.read_text().replace(boundary, boundary + injection))
        request = self.retire_request(jobs='jobs stopped', reason='recheck pending')
        retired = subprocess.run([sys.executable, '-B', str(SCRIPT), 'retire', '--state', str(self.state),
                                  '--attempt', 'ticket-1/1', '--input', str(request)],
                                 env=self.native_env, capture_output=True, text=True, timeout=20)
        continuation = json.loads(outcome.read_text())
        self.assertEqual(continuation['code'], 2)
        self.assertIn('retry', continuation['error'])
        self.assertEqual(retired.returncode, 0, retired.stdout + retired.stderr)
        rows = self.cli('status')['attempts']
        self.assertEqual([row['attempt'] for row in rows], ['ticket-1/1'])
        self.assertTrue(rows[0]['closed'])
        self.assertTrue((self.root / 'native-closed').exists())

    def test_wrong_target_refusal_names_the_recorded_target_to_retire(self):
        self.shown(empty_panes=('pane-2',))
        error = self.retire(pane='pane-2', jobs='jobs stopped', reason='pending', code=2)
        self.assertIn('retire again with session: fixture-session and pane: pane-1', error)
        self.assertNotIn('pane close', (self.root / 'herdr-argv.log').read_text())
        self.assertTrue(self.retire(jobs='jobs stopped', reason='pending')['closed'])
        self.assertTrue(self.cli('status')['attempts'][0]['closed'])

    def test_retirement_resolves_moved_caller_identity(self):
        self.settled()
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
            **self.completion_evidence(),
            'evidence': ['current report checked'], 'disposition': {'kind': 'retire',
            'session': 'fixture-session', 'pane': 'pane-1', 'jobs_evidence': ['owned jobs stopped'],
            'acceptance': {'decision': 'deferred', 'reason': 'acceptance checks pending'}}}))
        self.cli('collect', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertTrue((self.root / 'native-closed').is_file())
        status = self.cli('status')
        self.assertTrue(status['attempts'][0]['closed'])
        self.assertEqual(status['pending'], [])
        self.assertTrue(report.is_file())

    def collect_request(self, report, disposition):
        return super().collect_request('head-1', report, 'ticket-1/1', disposition=disposition,
                                       evidence=['current report checked'])

    def test_a_native_subagent_with_no_herdr_pane_retires_on_its_evidence(self):
        # A native subagent has no pane to close: collect records it closed with the request's evidence, and refuses
        # before recording the result when that evidence is missing.
        self.register()
        self.ready()
        report = self.root / 'report.md'
        report.write_text('Completed assignment at head-1\n')
        retire = {'kind': 'retire', 'acceptance': {'decision': 'deferred', 'reason': 'acceptance checks pending'}}
        error = self.cli('collect', '--attempt', 'ticket-1/1', '--input', str(self.collect_request(report, retire)),
                         code=2)
        self.assertIn('no Herdr pane', error)
        self.assertIn('disposition evidence', error)
        self.assertNotIn('result', self.cli('status')['attempts'][0])
        evidence = ['subagent finished; no pane to close']
        self.cli('collect', '--attempt', 'ticket-1/1',
                 '--input', str(self.collect_request(report, dict(retire, evidence=evidence))))
        saved = self.cli('status')['attempts'][0]
        self.assertEqual(saved['closed']['evidence'], evidence)
        self.assertEqual(self.cli('status')['pending'], [])
        self.cli('collect', '--attempt', 'ticket-1/1',
                 '--input', str(self.collect_request(report, dict(retire, evidence=evidence))))

    def test_collect_rejects_missing_disposition_before_recording_result(self):
        self.register()
        self.ready()
        report = self.root / 'report.md'
        report.write_text('Completed at head-1\n')
        request = self.root / 'collect.json'
        request.write_text(json.dumps({'status': 'completed', 'head': 'head-1', 'report': str(report),
            **self.completion_evidence(),
                                       'evidence': ['checked report']}))
        self.cli('collect', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertNotIn('result', self.cli('status')['attempts'][0])

    def test_collect_retires_a_finished_codex_child_without_a_herdr_pane(self):
        self.execution.write_text(json.dumps({'host': 'codex', 'native_dispatch': True,
            'agent': 'builder', 'job': 'ticket', 'settings': {'model': 'm', 'effort': 'high'}}))
        child = codex_child_session('ticket-1/1')
        self.register(runtime='codex', worker=child, session=child)
        native = session_record(self.root / 'child.jsonl', 'codex', child, self.root, model='m', child=True)
        self.record('startup', native=str(native))
        report = self.root / 'report.md'
        report.write_text('Completed assignment at head-1\n')
        request = self.collect_request(report, {'kind': 'retire',
            'evidence': ['Codex child finished; owned jobs reconciled; no pane'],
            'acceptance': {'decision': 'deferred', 'reason': 'integration checks pending'}})
        result = self.cli('collect', '--attempt', 'ticket-1/1', '--input', request)
        self.assertTrue(result['disposition']['closed'])
        status = self.cli('status')
        saved = status['attempts'][0]
        self.assertTrue(saved['verified'])
        self.assertIn('closed', saved)
        self.assertNotIn('retained', saved)
        self.assertEqual(status['pending'], [])
        self.assertEqual(report.read_text(), 'Completed assignment at head-1\n')

    def panel_rows(self):
        """The rows the panel draws for the host session that registered the fixture's workers."""
        process = subprocess.run([sys.executable, '-B', str(support.script('panel_state.py')), '--repo', str(self.root),
                                  '--session-id', 'codex-host'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        return [row['text'] for row in json.loads(process.stdout)['rows'] if row['kind'] == 'wait']

    def test_retiring_a_worker_the_orchestrator_does_not_own_releases_it_without_touching_its_pane(self):
        self.state = self.root / '.scratch' / 'ws' / 'reports' / 'workers.sqlite'
        self.state.parent.mkdir(parents=True)
        self.settled(owned=False)
        self.record('retain', reason='User-owned worker', next_use='Return control to owner', evidence=['receipt'])
        self.assertEqual(1, len(self.panel_rows()))
        self.herdr_fixture()
        request = self.root / 'retire.json'
        request.write_text(json.dumps({'session': 'fixture-session', 'pane': 'pane-1',
                                       'acceptance': {'decision': 'deferred', 'reason': 'acceptance checks pending'}}))
        released = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual((released['attempt'], released['closed']), ('ticket-1/1', True))
        self.assertEqual(['released: not owned by this orchestrator, its pane is left open'],
                         self.cli('status')['attempts'][0]['closed']['evidence'])
        self.assertFalse((self.root / 'herdr-argv.log').exists())
        self.assertEqual([], self.panel_rows())

    def test_collecting_an_unowned_worker_with_a_retire_disposition_releases_it(self):
        self.register(owned=False)
        self.ready()
        self.herdr_fixture()
        report = self.root / 'report.md'
        report.write_text('Completed assignment at head-1\n')
        request = self.collect_request(report, {'kind': 'retire', 'acceptance': {
            'decision': 'deferred', 'reason': 'acceptance checks pending'}})
        self.cli('collect', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertIn('released', self.cli('status')['attempts'][0]['closed']['evidence'][0])
        self.assertFalse((self.root / 'herdr-argv.log').exists())

    def test_a_release_still_meets_the_collected_result_and_receipt_checks(self):
        self.register(owned=False)
        self.ready()
        request = self.root / 'retire.json'
        request.write_text(json.dumps({}))
        self.assertIn('lacks a collected result', self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2))
        self.collect()
        self.assertIn('acknowledged', self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2))
        self.assertNotIn('closed', self.cli('status')['attempts'][0])

    def test_retirement_rejects_the_caller_pane(self):
        self.settled()
        self.herdr_fixture()
        self.native_env['HERDR_PANE_ID'] = 'pane-1'
        error = self.retire(code=2, jobs='owned jobs stopped', reason='acceptance pending')
        self.assertIn('Caller pane is not a cleanup target', error)
        self.assertFalse((self.root / 'native-closed').exists())

    def test_retirement_rejects_a_replaced_native_conversation(self):
        self.settled()
        self.herdr_fixture(native_session='different-native-session')
        error = self.retire(code=2)
        self.assertIn('conversation no longer matches', error)
        self.assertFalse((self.root / 'native-closed').exists())

    def test_retiring_a_worker_whose_pane_now_holds_another_conversation_names_the_command_that_closes_it(self):
        # Live (stforestkit, remediate-taut-534): the next attempt's worker was started in this worker's pane. Retire
        # leaves that pane alone and names the reap, which Herdr's answer for the pane lets close the attempt.
        self.shown(native_session='native-2', agent_name='builder-2', pane_session='native-2')
        error = self.retire(code=2)
        self.assertIn(f"Pane pane-1 now holds another conversation (native-2), so this attempt's worker is gone: close "
                      f"it with `python3 {SCRIPT} reap --state {shlex.quote(str(self.state.resolve()))}`", error)
        self.assertFalse((self.root / 'native-closed').exists())
        self.assertEqual(['ticket-1/1'], [row['attempt'] for row in self.cli('reap')['closed']])

    def test_retiring_a_worker_that_exited_in_its_own_pane_closes_that_pane_and_tab(self):
        # Live (stforestkit R01-R04/repair-2): a retained worker's Codex exited and left a shell in its own pane, where
        # Herdr finds no agent. Retirement closes that pane, so its tab, and records the closure; a pane the attempt
        # never recorded is not its to close.
        self.herdr_fixture()
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        self.register('ticket-2/1', 'ticket-2', session='native-2', worker='pane-1')
        for attempt in ('ticket-1/1', 'ticket-2/1'):
            self.ready(attempt)
            self.collect(attempt)
        self.acknowledge()
        self.herdr_fixture(agent_exited=True)
        request = self.retire_request()
        error = self.cli('retire', '--attempt', 'ticket-2/1', '--input', str(request), code=2)
        self.assertIn("is not this attempt's recorded Herdr pane", error)
        self.assertFalse((self.root / 'native-closed').exists())
        # The same pane ID on another Herdr server, or another empty pane on its server, is not the worker's pane.
        self.herdr_fixture(agent_exited=True, servers=('fixture-session', 'other-session'), empty_panes=('pane-2',))
        for case, where in (('another server', {'session': 'other-session'}), ('another pane', {'pane': 'pane-2'})):
            with self.subTest(case=case):
                elsewhere = self.root / 'retire-elsewhere.json'
                elsewhere.write_text(json.dumps(dict(json.loads(request.read_text()), **where)))
                error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(elsewhere), code=2)
                self.assertIn("is not this attempt's recorded Herdr pane", error)
                self.assertFalse((self.root / 'native-closed').exists())
                self.assertNotIn('pane close', (self.root / 'herdr-argv.log').read_text())
        closed = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual((closed['attempt'], closed['closed']), ('ticket-1/1', True))
        self.assertEqual((self.root / 'native-closed').read_text(), 'closed\n')
        self.assertEqual([row.get('closed') is not None for row in self.cli('status')['attempts']], [True, False])

    def test_retiring_an_exited_worker_again_after_its_pane_failed_to_close_closes_it(self):
        self.shown(agent_exited=True, close_failures=1)
        request = self.retire_request()
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertEqual(error.strip(), 'Native close failed; retire it again once the pane is reconciled')
        closed = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual((closed['attempt'], closed['closed']), ('ticket-1/1', True))
        self.assertEqual((self.root / 'herdr-argv.log').read_text().count('pane close'), 2)
        self.assertEqual((self.root / 'native-closed').read_text(), 'closed\n')

    def test_retiring_a_worker_whose_pane_herdr_cannot_identify_does_not_send_it_to_the_reap(self):
        # Review (Sonnet): the reap closes nothing Herdr cannot identify, so retire names it only for another
        # conversation; an identity with no value keeps the refusal that the conversation no longer matches.
        self.shown(native_session='', pane_session='')
        error = self.retire(code=2)
        self.assertEqual(('Native conversation no longer matches the attempt', False), (error.strip(), 'reap' in error))
        self.assertFalse((self.root / 'native-closed').exists())

    def test_native_closure_recovery_preserves_report_and_does_not_close_twice(self):
        self.herdr_fixture(tab_gone_error='server_unavailable')
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        self.ready()
        report = self.root / 'report.md'
        report.write_text('Completed at head-1\n')
        request = self.root / 'collect.json'
        request.write_text(json.dumps({'status': 'completed', 'head': 'head-1', 'report': str(report),
            **self.completion_evidence(),
            'evidence': ['checked report'], 'disposition': {'kind': 'retire',
            'session': 'fixture-session', 'pane': 'pane-1', 'jobs_evidence': ['owned jobs stopped'],
            'acceptance': {'decision': 'deferred', 'reason': 'acceptance checks pending'}}}))
        self.cli('collect', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        status = self.cli('status')
        self.assertIn('result', status['attempts'][0])
        self.assertNotIn('closed', status['attempts'][0])
        self.herdr_fixture()
        self.cli('collect', '--attempt', 'ticket-1/1', '--input', str(request))
        self.cli('collect', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual((self.root / 'native-closed').read_text(), 'closed\n')

    def test_final_retirement_releases_worker_handle_of_earlier_retained_attempts(self):
        self.settled()
        self.record('retain', reason='Same-assignment recheck needed', next_use='final recheck',
                    evidence=['review report'])
        self.settled('ticket-1/2')
        self.herdr_fixture()
        request = self.retire_request(jobs='final same-assignment use, jobs stopped')
        self.cli('retire', '--attempt', 'ticket-1/2', '--input', str(request))
        status = self.cli('status')
        self.assertEqual(status['attempts'][0]['native_closed_by'], 'ticket-1/2')
        self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual((self.root / 'native-closed').read_text(), 'closed\n')
        self.register('ticket-2/1', 'ticket-2', 'native-2', 'pane-1')
        self.register('ticket-3/1', 'ticket-3', 'native-1', 'different-pane', code=2)

    def test_report_must_survive_retirement_and_gate_checks_closed_worker(self):
        report = self.settled()
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
        report = self.settled()
        report.write_text(report.read_text() + 'changed after collection\n')
        self.herdr_fixture()
        request = self.root / 'retire.json'
        retire = {'session': 'fixture-session', 'pane': 'pane-1', 'jobs_evidence': ['jobs stopped']}
        request.write_text(json.dumps(dict(retire, acceptance={'decision': 'deferred', 'reason': 'pending'})))
        self.assertIn('intact report', self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2))
        request.write_text(json.dumps(dict(retire, acceptance={'decision': 'declined', 'reason': 'report changed'})))
        self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual(self.cli('status')['attempts'][0]['declined']['reason'], 'report changed')

    def test_retirement_refuses_a_changed_runtime_before_closing_the_pane(self):
        self.shown(agent_kind='codex')
        saved = self.cli('status')['attempts'][0]
        self.assertEqual((saved['requested']['runtime'], saved['native_session']), ('claude', 'native-1'))
        error = self.retire(code=2)
        self.assertEqual(error.strip(), 'Native pane no longer holds the registered runtime')
        self.assertFalse((self.root / 'native-closed').exists())
        self.assertNotIn('pane close', (self.root / 'herdr-argv.log').read_text())
        self.assertNotIn('closed', self.cli('status')['attempts'][0])

    def test_a_report_changed_during_retirement_is_refused_before_the_pane_closes(self):
        report = self.settled()
        meanwhile = f'open({str(report)!r}, "a").write("changed meanwhile\\n")'
        self.herdr_fixture(pane_delay=1, during_delay=[sys.executable, '-c', meanwhile])
        error = self.retire(code=2)
        self.assertEqual(json.loads((self.root / 'during-delay.json').read_text())['code'], 0)
        self.assertIn('lacks an intact report', error)
        self.assertFalse((self.root / 'native-closed').exists())
        self.assertNotIn('pane close', (self.root / 'herdr-argv.log').read_text())
        self.assertNotIn('closed', self.cli('status')['attempts'][0])

    def test_retirement_rechecks_the_decline_once_its_herdr_wait_is_over(self):
        # Review: an attempt accepted, and its report changed, while retirement waited on Herdr is not declined by
        # the request; it owes no intact report any more, so it retires, still accepted.
        report = self.settled()
        accepted = self.root / 'accepted.json'
        accepted.write_text(json.dumps({'kind': 'accepted', 'attempt': 'ticket-1/1', 'commit': 'merge-1',
                                        'evidence': ['git log merge-1']}))
        meanwhile = (f'import subprocess, sys; subprocess.run([sys.executable, "-B", {str(SCRIPT)!r}, "record", '
                     f'"--state", {str(self.state)!r}, "--input", {str(accepted)!r}], check=True); '
                     f'open({str(report)!r}, "a").write("changed meanwhile\\n")')
        self.herdr_fixture(pane_delay=1, during_delay=[sys.executable, '-c', meanwhile])
        request = self.root / 'retire.json'
        request.write_text(json.dumps({'session': 'fixture-session', 'pane': 'pane-1', 'jobs_evidence': ['stopped'],
                                       'acceptance': {'decision': 'declined', 'reason': 'discard attempt'}}))
        self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual(json.loads((self.root / 'during-delay.json').read_text())['code'], 0)
        retired = self.cli('status')['attempts'][0]
        self.assertEqual(('merge-1', False), (retired['accepted']['commit'], 'declined' in retired))

    def test_a_failed_attempts_changed_report_does_not_block_a_later_attempt_retiring(self):
        # A failed, blocked or cancelled attempt owes no intact report: only a completed result does. A retry in the
        # same conversation that rewrites the shared report must still retire.
        self.register()
        self.ready()
        report = self.root / 'shared.md'
        report.write_text('## Implemented\n\nnote from the failed attempt\n')
        self.completed(status='failed', head='head-1', report=str(report), evidence=['provider outage'])
        self.acknowledge()
        self.record('retain', reason='retry', next_use='continue', evidence=['failed attempt kept'])
        self.register('ticket-1/2')
        self.ready('ticket-1/2')
        report.write_text('## Implemented\n\nVerified assignment report at head-1\n')
        self.completed('ticket-1/2', status='completed', head='head-1', report=str(report), evidence=['checks.log'])
        self.acknowledge()
        self.herdr_fixture()
        self.retire('ticket-1/2', jobs='jobs stopped', reason='acceptance pending')
        self.assertTrue(self.cli('status')['attempts'][1]['closed'])

    def test_a_blocked_attempt_retires_after_its_report_changed(self):
        self.register()
        self.ready()
        report = self.root / 'blocked.md'
        report.write_text('blocked on a missing credential\n')
        self.completed(status='blocked', head='head-1', report=str(report), evidence=['credential missing'])
        self.acknowledge()
        report.write_text('blocked report, edited later\n')
        self.herdr_fixture()
        request = self.root / 'retire.json'
        request.write_text(json.dumps({'session': 'fixture-session', 'pane': 'pane-1', 'jobs_evidence': ['jobs stopped']}))
        self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertTrue(self.cli('status')['attempts'][0]['closed'])

    def test_a_failed_attempt_is_retained_after_its_report_changed(self):
        self.register()
        self.ready()
        report = self.root / 'failed.md'
        report.write_text('provider outage\n')
        self.completed(status='failed', head='head-1', report=str(report), evidence=['provider outage'])
        self.acknowledge()
        report.write_text('provider outage, edited later\n')
        self.record('retain', reason='retry', next_use='continue', evidence=['failed attempt kept'])
        self.assertIn('retained', self.cli('status')['attempts'][0])

    def test_a_failed_result_recorded_but_not_collected_is_collected_after_its_report_changed(self):
        # The yield gate names an uncollected result and advises collecting it; a failed result owes no intact report.
        self.register()
        self.ready()
        report = self.root / 'failed.md'
        report.write_text('provider outage\n')
        self.completed(status='failed', head='head-1', report=str(report), evidence=['provider outage'])
        report.write_text('provider outage, edited by a later attempt\n')
        request = self.root / 'collect.json'
        request.write_text(json.dumps({'status': 'failed', 'head': 'head-1', 'report': str(report),
                                       'evidence': ['provider outage'], **self.completion_evidence(),
                                       'disposition': {'kind': 'retain', 'reason': 'retry', 'next_use': 'continue',
                                                       'evidence': ['failed attempt kept']}}))
        collected = self.cli('collect', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertTrue(collected['result']['already_collected'])
        self.assertEqual([], self.cli('status')['pending'])

    def test_a_herdr_worker_its_own_pane_still_holds_is_not_recorded_closed(self):
        # Live (stforestkit R15/repair-1): Pi recorded R15 closed on "herdr agent get tautrep17 and pane get w2W:pB8
        # both not found", R05's worker and pane, while R15's own Codex sat idle in its pane. Only retire, which closes
        # that pane, ends such a worker; a pane Herdr shows gone, empty or holding another conversation has no worker.
        deferred = {'decision': 'deferred', 'reason': 'acceptance checks pending'}
        self.shown(pane_session='native-1')
        error = self.record('closed', code=2, evidence=['herdr agent get worker-5: agent_not_found'],
                            acceptance=deferred)
        self.assertEqual(error.strip(), "Herdr pane pane-1 in tab tab-1 still holds this attempt's conversation, so "
                         f"its worker has not ended: retire it with `python3 {SCRIPT} retire --state "
                         f"{shlex.quote(str(self.state.resolve()))} --attempt ticket-1/1 --input <request>`, which "
                         'closes that pane and records the closure')
        self.assertNotIn('closed', self.cli('status')['attempts'][0])
        self.herdr_fixture(pane_session='native-2')
        self.record('closed', evidence=['pane-1 holds native-2'], acceptance=deferred)
        self.herdr_fixture(native_session='native-2')
        self.register('ticket-2/1', 'ticket-2', session='native-2', worker='builder-1',
                      herdr={'session': 'fixture-session'})
        self.ready('ticket-2/1')
        self.collect('ticket-2/1')
        self.acknowledge()
        self.herdr_fixture(native_session='native-2', pane_session=None)
        error = self.record('closed', 'ticket-2/1', code=2, evidence=['pane-1 holds no worker'], acceptance=deferred)
        self.assertIn('is open with no worker in it: retire it with', error)
        (self.root / 'native-closed').write_text('closed\n')
        self.record('closed', 'ticket-2/1', evidence=['pane-1 and its tab are gone'], acceptance=deferred)

    def test_a_herdr_worker_its_pane_holds_with_a_conversation_it_cannot_compare_is_not_recorded_closed(self):
        # Codex review: Herdr names a Pi worker's conversation by its session record's path, which an attempt with no
        # startup record cannot compare; the pane may hold this very worker, so only closing that pane ends it.
        self.herdr_fixture(session_kind='path')
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        report = self.root / 'report.md'
        report.write_text('Blocked before its first answer\n')
        self.record('result', status='blocked', head='head-1', report=str(report), evidence=['worker never answered'])
        self.acknowledge()
        self.herdr_fixture(session_kind='path', pane_session='/sessions/pi-worker.jsonl')
        deferred = {'decision': 'deferred', 'reason': 'acceptance checks pending'}
        error = self.record('closed', code=2, evidence=['pane closed'], acceptance=deferred)
        self.assertEqual(error.strip(), 'Herdr pane pane-1 in tab tab-1 holds a conversation '
                         "(/sessions/pi-worker.jsonl) this attempt cannot tell from its own: once you have checked it is "
                         "this attempt's worker, close it with `herdr --session fixture-session pane close pane-1`, then "
                         'record the closure')
        # Live: a Pi without Herdr's extension has a conversation Herdr does not name at all.
        self.herdr_fixture(session_kind='path', pane_session='')
        error = self.record('closed', code=2, evidence=['pane closed'], acceptance=deferred)
        self.assertIn('holds a conversation (Herdr names none) this attempt cannot tell from its own', error)
        self.assertNotIn('closed', self.cli('status')['attempts'][0])

    def test_a_worker_its_pane_names_by_its_session_record_is_not_recorded_closed(self):
        # Codex review: Herdr names a Pi worker's conversation by the path of its session record, which the attempt's
        # startup observed: the same conversation, so retire ends it.
        self.herdr_fixture(session_kind='path')
        self.settled(worker='builder-1', herdr={'session': 'fixture-session'})
        self.herdr_fixture(session_kind='path', pane_session=str(self.native_paths['ticket-1/1']))
        error = self.record('closed', code=2, evidence=['pane closed'],
                            acceptance={'decision': 'deferred', 'reason': 'acceptance checks pending'})
        self.assertIn("Herdr pane pane-1 in tab tab-1 still holds this attempt's conversation", error)

    def test_a_closure_by_hand_meets_the_lifecycle_s_own_refusals_before_herdr_is_asked(self):
        for number, (owned, collected, worker, refusal) in enumerate((
                (True, False, 'builder-1', 'lacks a collected result'),
                (True, True, 'pane-1', 'acknowledged (`python3')), 1):
            with self.subTest(refusal=refusal):
                attempt, session = f'ticket-{number}/1', f'native-{number}'
                self.herdr_fixture(native_session=session)
                self.register(attempt, f'ticket-{number}', session=session, worker=worker, owned=owned,
                              herdr={'session': 'fixture-session'})
                if collected:
                    self.ready(attempt)
                    self.collect(attempt)
                self.herdr_fixture(native_session=session, pane_session=session)
                log = self.root / 'herdr-argv.log'
                asked = len(log.read_text().splitlines())
                error = self.record('closed', attempt, code=2, evidence=['pane closed'])
                self.assertIn(refusal, error)
                self.assertEqual(log.read_text().splitlines()[asked:], [])

    def closure_before_herdr(self, attempt='ticket-1/1', **fields):
        """The refusal of a closure by hand while Herdr shows the attempt's pane holding it, which must come before
        Herdr is asked."""
        self.herdr_fixture(pane_session='native-1')
        log = self.root / 'herdr-argv.log'
        asked = len(log.read_text().splitlines())
        error = self.record('closed', attempt, code=2, evidence=['pane closed'], **fields)
        self.assertEqual(log.read_text().splitlines()[asked:], [])
        return error

    def test_a_closure_by_hand_meets_a_missing_acceptance_before_herdr_is_asked(self):
        # Sonnet review: retire refuses it for the same reason, so naming retire first sends the host round in a loop.
        self.herdr_fixture()
        self.settled(worker='builder-1', herdr={'session': 'fixture-session'})
        self.assertIn('Settle acceptance of the completed ticket attempt before closing it', self.closure_before_herdr())

    def test_a_closure_by_hand_meets_an_earlier_attempt_s_receipt_before_herdr_is_asked(self):
        # Codex review: the earlier attempt of the conversation holds a receipt to acknowledge, which retire names too.
        self.herdr_fixture()
        self.settled(worker='builder-1', herdr={'session': 'fixture-session'})
        self.record('retain', reason='same-assignment recheck', next_use='recheck', evidence=['report'])
        self.settled('ticket-1/2', worker='builder-1', herdr={'session': 'fixture-session'})
        brief = self.root / 'brief.md'
        brief.write_text('# Ruling\n')
        self.record('hold', reason='a ruling is needed', brief=str(brief), evidence=['finding'])
        receipt = self.cli('status')['pending'][0]['id']
        error = self.closure_before_herdr('ticket-1/2', acceptance={'decision': 'deferred', 'reason': 'pending'})
        self.assertIn(f'ticket-1/1 lacks receipt {receipt} acknowledged (`python3 {SCRIPT} ack', error)

    def test_a_herdr_worker_is_not_recorded_closed_while_herdr_cannot_say_where_it_is(self):
        # Neither a Herdr that does not answer nor one that answers with an error tells that the worker has ended.
        self.herdr_fixture()
        self.settled(worker='builder-1', herdr={'session': 'fixture-session'})
        retire = (f'retire the worker with `python3 {SCRIPT} retire --state {shlex.quote(str(self.state.resolve()))} '
                  '--attempt ticket-1/1 --input <request>`')
        deferred = {'decision': 'deferred', 'reason': 'acceptance checks pending'}
        self.herdr_fixture(pane_session='native-2', pane_delay=8)
        error = self.record('closed', code=2, evidence=['pane closed'], acceptance=deferred)
        self.assertEqual(error.strip(), "Herdr cannot confirm whether pane pane-1 still holds this attempt's "
                         'conversation (herdr --session fixture-session pane get pane-1: no answer (Herdr did not '
                         f'answer pane get pane-1 within 5 s)); record the closure once it does, or {retire}')
        self.herdr_fixture(verify_error='server_unavailable', tab_survives=True)
        (self.root / 'native-closed').write_text('closed\n')
        error = self.record('closed', code=2, evidence=['pane closed'], acceptance=deferred)
        self.assertEqual(error.strip(), "Herdr cannot confirm whether pane pane-1 still holds this attempt's "
                         'conversation (herdr --session fixture-session pane get pane-1: server_unavailable); record the '
                         f'closure once it does, or {retire}')
        self.assertNotIn('closed', self.cli('status')['attempts'][0])

    def test_a_worker_the_orchestrator_does_not_own_is_recorded_closed_by_hand_as_a_release(self):
        self.settled(owned=False)
        self.record('closed', evidence=['released to its user'],
                    acceptance={'decision': 'deferred', 'reason': 'acceptance checks pending'})
        self.assertEqual(['released to its user'], self.cli('status')['attempts'][0]['closed']['evidence'])

CLOSURES = {
    'an empty pane the worker exited': (
        dict(agent_exited=True), (None, True), 'is open with no worker in it: retire it with', False),
    'a tab Herdr shows gone': (dict(gone=True), (None, False), None, True),
    'a pane gone with its tab alive': (dict(gone=True, tab_survives=True),
                                       ('Native closure is not confirmed (herdr --session fixture-session tab get tab-1: listed)', False),
                                       'Herdr cannot confirm whether pane pane-1', False),
    'a pane holding another conversation': (
        dict(native_session='native-2', agent_name='builder-2', agent_exited=True, pane_session='native-2'),
        ('now holds another conversation (native-2)', False), None, True),
}


class ClosureJudgement(WorkerLifecycleCase):
    def test_missing_or_unavailable_old_tab_does_not_end_a_moved_active_worker(self):
        for tab_reply in ('tab_not_found', 'server_unavailable'):
            with self.subTest(tab_reply=tab_reply):
                self.setUp()
                self.shown(agent_tab='tab-2', agent_status='working')
                fake = self.root / 'bin/herdr'
                source = fake.read_text()
                start = source.index("elif args[2:]==['tab','get','tab-1']:")
                end = source.index("elif args[2:]==['pane','close','pane-1']", start)
                fake.write_text(source[:start] + "elif args[2:]==['tab','get','tab-1']:\n"
                    f"    print(json.dumps(dict(error=dict(code={tab_reply!r}))),file=sys.stderr);sys.exit(1)\n"
                    + source[end:])
                error = self.retire(jobs='jobs', reason='pending', code=2)
                self.assertIn('Native worker is active or blocked', error)
                error = self.record('closed', code=2, evidence=['old tab missing'],
                                    acceptance={'decision': 'deferred', 'reason': 'pending'})
                self.assertIn("still holds this attempt's conversation", error)
                self.assertEqual([], self.cli('reap')['closed'])
                self.assertNotIn('closed', self.cli('status')['attempts'][0])
                self.assertFalse(self.closes_pane())

    def closes_pane(self):
        return 'pane close' in (self.root / 'herdr-argv.log').read_text()

    def test_retire_judges_each_answer_of_herdr_as_the_hand_record_and_the_reap_do(self):
        for case, (fixture, (error, closes), _, _) in CLOSURES.items():
            with self.subTest(case=case):
                self.setUp()
                self.shown(**fixture)
                result = self.retire(jobs='jobs', reason='pending', code=2 if error else 0)
                if error:
                    self.assertIn(error, result)
                self.assertEqual(not error, 'closed' in self.cli('status')['attempts'][0])
                self.assertEqual(closes, self.closes_pane())

    def test_a_closure_by_hand_judges_each_answer_of_herdr_as_retire_and_the_reap_do(self):
        for case, (fixture, _, error, _) in CLOSURES.items():
            with self.subTest(case=case):
                self.setUp()
                self.shown(**fixture)
                result = self.record('closed', code=2 if error else 0, evidence=['herdr answered'],
                                     acceptance={'decision': 'deferred', 'reason': 'pending'})
                if error:
                    self.assertIn(error, result)
                self.assertEqual(not error, 'closed' in self.cli('status')['attempts'][0])
                self.assertFalse(self.closes_pane())

    def test_the_reap_judges_each_answer_of_herdr_as_retire_and_a_closure_by_hand_do(self):
        for case, (fixture, _, _, closes) in CLOSURES.items():
            with self.subTest(case=case):
                self.setUp()
                self.shown(**fixture)
                self.assertEqual(['ticket-1/1'] if closes else [],
                                 [row['attempt'] for row in self.cli('reap')['closed']])
                self.assertFalse(self.closes_pane())

    def test_a_retirement_interrupted_after_its_pane_closed_is_recorded_closed_by_the_next_reap(self):
        self.shown(gone=True)
        found = self.cli('reap')
        self.assertEqual([{'attempt': 'ticket-1/1',
                           'evidence': ['herdr --session fixture-session tab get tab-1: tab_not_found']}],
                         found['closed'])
        self.assertIn('closed', self.cli('status')['attempts'][0])





if __name__ == '__main__':
    unittest.main()
