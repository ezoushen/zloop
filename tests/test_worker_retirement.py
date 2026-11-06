#!/usr/bin/env python3
"""Collection and retirement of native workers."""

import json
import shlex
import sys
import unittest

from lifecycle_case import SCRIPT, WorkerLifecycleCase


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
        request = self.root / 'collect.json'
        request.write_text(json.dumps({'status': 'completed', 'head': 'head-1', 'report': str(report),
                                       **self.completion_evidence(), 'evidence': ['current report checked'],
                                       'disposition': disposition}))
        return request

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

    def test_retiring_a_worker_whose_pane_now_holds_another_conversation_names_the_command_that_closes_it(self):
        # Live (stforestkit, remediate-taut-534): the next attempt's worker was started in this worker's pane. Retire
        # leaves that pane alone and names the reap, which Herdr's answer for the pane lets close the attempt.
        self.herdr_fixture()
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        self.ready()
        self.collect()
        self.acknowledge()
        self.herdr_fixture(native_session='native-2', agent_name='builder-2', pane_session='native-2')
        request = self.root / 'retire.json'
        request.write_text(json.dumps({'session': 'fixture-session', 'pane': 'pane-1',
                                       'jobs_evidence': ['owned jobs inspected'],
                                       'acceptance': {'decision': 'deferred', 'reason': 'acceptance checks pending'}}))
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
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
        request = self.root / 'retire.json'
        request.write_text(json.dumps({'session': 'fixture-session', 'pane': 'pane-1',
                                       'jobs_evidence': ['owned jobs inspected'],
                                       'acceptance': {'decision': 'deferred', 'reason': 'acceptance checks pending'}}))
        error = self.cli('retire', '--attempt', 'ticket-2/1', '--input', str(request), code=2)
        self.assertEqual(error.strip(), 'Inspect the missing native worker before recording closure')
        self.assertFalse((self.root / 'native-closed').exists())
        # An agent lookup that failed while the pane still shows its agent is not an exit.
        self.herdr_fixture(agent_exited=True, pane_session='native-1')
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertEqual(error.strip(), 'Inspect the missing native worker before recording closure')
        self.assertFalse((self.root / 'native-closed').exists())
        # The same pane ID on another Herdr server, or another empty pane on its server, is not the worker's pane.
        self.herdr_fixture(agent_exited=True, servers=('fixture-session', 'other-session'), empty_panes=('pane-2',))
        for case, where in (('another server', {'session': 'other-session'}), ('another pane', {'pane': 'pane-2'})):
            with self.subTest(case=case):
                elsewhere = self.root / 'retire-elsewhere.json'
                elsewhere.write_text(json.dumps(dict(json.loads(request.read_text()), **where)))
                error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(elsewhere), code=2)
                self.assertEqual(error.strip(), 'Inspect the missing native worker before recording closure')
                self.assertFalse((self.root / 'native-closed').exists())
                self.assertNotIn('pane close', (self.root / 'herdr-argv.log').read_text())
        closed = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual((closed['attempt'], closed['closed']), ('ticket-1/1', True))
        self.assertEqual((self.root / 'native-closed').read_text(), 'closed\n')
        self.assertEqual([row.get('closed') is not None for row in self.cli('status')['attempts']], [True, False])

    def test_retiring_an_exited_worker_again_after_its_pane_failed_to_close_closes_it(self):
        # Review (Sonnet): the retirement recorded before a failed close must not stop the retry from closing the pane
        # the exited worker left, as it does not for a worker still in its pane.
        self.herdr_fixture()
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        self.ready()
        self.collect()
        self.acknowledge()
        self.herdr_fixture(agent_exited=True, close_failures=1)
        request = self.root / 'retire.json'
        request.write_text(json.dumps({'session': 'fixture-session', 'pane': 'pane-1',
                                       'jobs_evidence': ['owned jobs inspected'],
                                       'acceptance': {'decision': 'deferred', 'reason': 'acceptance checks pending'}}))
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertEqual(error.strip(), 'Native close failed; reconcile the original pane before retrying')
        # Review (Codex): an agent lookup that fails while the pane shows an agent is no exit on a retry either.
        self.herdr_fixture(agent_exited=True, pane_session='native-1', close_failures=1)
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertEqual(error.strip(), 'Native closure is not confirmed; preserve the original retirement target')
        self.assertEqual((self.root / 'herdr-argv.log').read_text().count('pane close'), 1)
        self.herdr_fixture(agent_exited=True, close_failures=1)
        closed = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual((closed['attempt'], closed['closed']), ('ticket-1/1', True))
        self.assertEqual((self.root / 'native-closed').read_text(), 'closed\n')

    def test_retiring_a_worker_whose_pane_herdr_cannot_identify_does_not_send_it_to_the_reap(self):
        # Review (Sonnet): the reap closes nothing Herdr cannot identify, so retire names it only for another
        # conversation; an identity with no value keeps the refusal that the conversation no longer matches.
        self.herdr_fixture()
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        self.ready()
        self.collect()
        self.acknowledge()
        self.herdr_fixture(native_session='', pane_session='')
        request = self.root / 'retire.json'
        request.write_text(json.dumps({'session': 'fixture-session', 'pane': 'pane-1',
                                       'jobs_evidence': ['owned jobs inspected'],
                                       'acceptance': {'decision': 'deferred', 'reason': 'acceptance checks pending'}}))
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertEqual(('Native conversation no longer matches the attempt', False), (error.strip(), 'reap' in error))
        self.assertFalse((self.root / 'native-closed').exists())

    def test_native_closure_recovery_preserves_report_and_does_not_close_twice(self):
        self.register()
        self.ready()
        self.herdr_fixture(verify_error='server_unavailable')
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

    def test_retirement_rechecks_the_decline_once_its_herdr_wait_is_over(self):
        # Review: an attempt accepted, and its report changed, while retirement waited on Herdr is not declined by
        # the request; it owes no intact report any more, so it retires, still accepted.
        self.register()
        self.ready()
        report = self.collect()
        self.acknowledge()
        accepted = self.root / 'accepted.json'
        accepted.write_text(json.dumps({'kind': 'accepted', 'attempt': 'ticket-1/1', 'commit': 'merge-1',
                                        'evidence': ['git log merge-1']}))
        meanwhile = (f'import subprocess, sys; subprocess.run([sys.executable, "-B", {str(SCRIPT)!r}, "record", '
                     f'"--state", {str(self.state)!r}, "--input", {str(accepted)!r}], check=True); '
                     f'open({str(report)!r}, "a").write("changed meanwhile\\n")')
        self.herdr_fixture(agent_delay=1, during_delay=[sys.executable, '-c', meanwhile])
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
        request = self.root / 'retire.json'
        request.write_text(json.dumps({'session': 'fixture-session', 'pane': 'pane-1', 'jobs_evidence': ['jobs stopped'],
                                       'acceptance': {'decision': 'deferred', 'reason': 'acceptance pending'}}))
        self.cli('retire', '--attempt', 'ticket-1/2', '--input', str(request))
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
        self.herdr_fixture()
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        self.ready()
        self.collect()
        self.acknowledge()
        self.herdr_fixture(pane_session='native-1')
        error = self.record('closed', code=2, evidence=['herdr agent get worker-5: agent_not_found'],
                            acceptance=deferred)
        self.assertEqual(error.strip(), "Herdr pane pane-1 in tab tab-1 still holds this attempt's conversation, so "
                         f"its worker has not ended: retire it with `python3 {SCRIPT} retire --state "
                         f"{shlex.quote(str(self.state.resolve()))} --attempt ticket-1/1 --input <request>`, which "
                         'closes that pane and records the closure')
        self.assertNotIn('closed', self.cli('status')['attempts'][0])
        self.herdr_fixture(pane_session='native-2')
        self.record('closed', evidence=['pane-1 holds native-2'], acceptance=deferred)
        # An empty pane (the worker exited), then a pane Herdr no longer lists.
        for number, gone in ((2, False), (3, True)):
            with self.subTest(gone=gone):
                attempt = f'ticket-{number}/1'
                self.herdr_fixture(native_session=f'native-{number}')
                self.register(attempt, f'ticket-{number}', session=f'native-{number}', worker='builder-1',
                              herdr={'session': 'fixture-session'})
                self.ready(attempt)
                self.collect(attempt)
                self.acknowledge()
                self.herdr_fixture(native_session=f'native-{number}', pane_session=None)
                if gone:
                    (self.root / 'native-closed').write_text('closed\n')
                self.record('closed', attempt, evidence=['pane-1 holds no worker'], acceptance=deferred)

    def test_a_herdr_worker_is_not_recorded_closed_while_herdr_cannot_say_where_it_is(self):
        # Neither a Herdr that does not answer nor one that answers with an error tells that the worker has ended.
        self.herdr_fixture()
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        self.ready()
        self.collect()
        self.acknowledge()
        retire = (f'retire the worker with `python3 {SCRIPT} retire --state {shlex.quote(str(self.state.resolve()))} '
                  '--attempt ticket-1/1 --input <request>`')
        deferred = {'decision': 'deferred', 'reason': 'acceptance checks pending'}
        self.herdr_fixture(pane_session='native-2', pane_delay=8)
        error = self.record('closed', code=2, evidence=['pane closed'], acceptance=deferred)
        self.assertEqual(error.strip(), "Herdr cannot confirm whether pane pane-1 still holds this attempt's "
                         'conversation (Herdr did not answer pane get pane-1 within 5 s); record the closure once it '
                         f'does, or {retire}')
        self.herdr_fixture(verify_error='server_unavailable')
        (self.root / 'native-closed').write_text('closed\n')
        error = self.record('closed', code=2, evidence=['pane closed'], acceptance=deferred)
        self.assertEqual(error.strip(), "Herdr cannot confirm whether pane pane-1 still holds this attempt's "
                         'conversation (pane get answered server_unavailable); record the closure once it does, or '
                         f'{retire}')
        self.assertNotIn('closed', self.cli('status')['attempts'][0])

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
