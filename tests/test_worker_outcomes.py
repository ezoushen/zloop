#!/usr/bin/env python3
"""Routed decisions, acceptance and quality signals recorded through the lifecycle."""

import json
import sys
import unittest

from lifecycle_case import WorkerLifecycleCase
import journal_store


class Outcomes(WorkerLifecycleCase):
    def test_lifecycle_records_the_routed_decision_through_its_outcome(self):
        decision_id = self.routed()
        self.register()
        self.assertEqual([(row['event'], row['disposition'], row['choice']) for row in self.annotations(decision_id)],
                         [('action', 'accepted', 'option_0')])
        self.ready()
        execution = self.annotations(decision_id)[-1]
        self.assertEqual((execution['event'], execution['requested'], execution['observed'], execution['revision']),
                         ('execution', {'model': 'sonnet', 'effort': 'high', 'runtime': 'claude'},
                          {'runtime': 'claude', 'model': 'sonnet', 'effort': 'high'}, 'head-1'))
        self.watch()
        self.record('reconciled', evidence=['native worker idle with its report'])
        report = self.collect()
        outcome = self.annotations(decision_id)[-1]
        self.assertEqual((outcome['event'], outcome['status'], outcome['revision']), ('outcome', 'succeeded', 'head-1'))
        self.assertEqual(outcome['evidence'], [str(report), 'checks.log@head-1'])
        self.assertGreaterEqual(outcome['metrics']['worker_seconds'], 0)
        self.assertTrue(journal_store.check(self.journal, decision_id, 'completion', 'head-1')['complete'])

    def test_a_scoped_launch_still_records_its_routed_execution(self):
        # Rule: the decision journal records the verified runtime, model and effort; the write-scope controls are
        # the lifecycle's to verify and never cost the decision its execution record.
        decision_id = self.routed(runtime='codex')
        execution = json.loads(self.execution.read_text())
        execution.update(host='codex', native_dispatch=True, launch={'writes': 'worktree',
                                                  'verify': {'sandbox': 'workspace-write', 'approval': 'never'}})
        self.execution.write_text(json.dumps(execution))
        self.register(runtime='codex')
        result = self.record('startup', native=str(self.native()))
        self.assertEqual((result['verified'], result['journal']), (True, {'execution': 'recorded'}))
        recorded = self.annotations(decision_id)[-1]
        self.assertEqual((recorded['event'], recorded['requested'], recorded['observed']),
                         ('execution', {'runtime': 'codex', 'model': 'sonnet', 'effort': 'high'},
                          {'runtime': 'codex', 'model': 'sonnet', 'effort': 'high'}))

    def test_journal_capture_failure_is_reported_without_blocking_the_worker(self):
        decision_id = self.routed(runtime='executor-defined')
        self.register()
        result = self.record('startup', native=str(self.native()))
        self.assertTrue(result['verified'])
        self.assertIn('not recorded: Requested settings must match', result['journal']['execution'])
        self.assertEqual([row['event'] for row in self.annotations(decision_id)], ['action'])

    def test_later_quality_signals_update_the_routed_outcome(self):
        decision_id = self.routed()
        self.register()
        self.ready()
        self.watch()
        self.record('reconciled', evidence=['native worker idle with its report'])
        self.collect()
        self.cli('attribute', '--attempt', 'ticket-1/1', '--kind', 'review-defect', '--count', '2',
                 '--evidence', 'spec-review.md#F-01')
        self.register('ticket-1/repair-1', 'ticket-1-repair', 'native-2', 'pane-2', repairs='ticket-1/1')
        self.cli('attribute', '--attempt', 'ticket-1/1', '--kind', 'revert', '--evidence', 'revert of head-1')
        outcome = self.annotations(decision_id)[-1]
        self.assertEqual((outcome['event'], outcome['status'], outcome['revision']), ('outcome', 'failed', 'head-1'))
        self.assertEqual({key: value for key, value in outcome['metrics'].items() if key != 'worker_seconds'},
                         {'review_defects': 2, 'repair_rounds': 1, 'reverts': 1})
        self.assertIn('spec-review.md#F-01', outcome['evidence'])
        self.register('ticket-2/1', 'ticket-2', 'native-3', 'pane-3')
        error = self.cli('attribute', '--attempt', 'ticket-2/1', '--kind', 'owner-defect',
                         '--evidence', 'owner report', code=2)
        self.assertIn('collected attempt', error)
        self.register('ticket-3/1', 'ticket-3', 'native-4', 'pane-4', repairs='ticket-2/1', code=2)

    def collected(self, attempt, number, model='sonnet', agent='builder', job='ticket', **fields):
        """A collected attempt on the claude/<model> route, registered with fields."""
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': agent, 'job': job,
                                             'settings': {'model': model, 'effort': 'high'}}))
        self.register(attempt, attempt.split('/')[0], f'native-{number}', f'pane-{number}', **fields)
        self.record('startup', attempt, native=str(self.native(attempt, model=model)))
        self.record('submitted', attempt, evidence=['native.jsonl:2:accepted prompt'])
        self.watch(attempt)
        self.record('reconciled', attempt, evidence=['native worker idle with its report'])
        self.collect(attempt)

    def test_a_fix_of_a_fix_of_a_fix_needs_the_premise_the_earlier_fixes_shared(self):
        # Rule: a repair whose repaired attempt is itself a repair of a repair is the third fix in a row that review
        # found wanting; it is refused until the orchestrator states the premise the earlier fixes shared.
        self.collected('ticket-1/1', 1)
        self.collected('ticket-1/repair-1', 2, repairs='ticket-1/1')
        self.collected('ticket-1/repair-2', 3, repairs='ticket-1/repair-1')
        error = self.register('ticket-1/repair-3', 'ticket-1', 'native-4', 'pane-4', repairs='ticket-1/repair-2',
                              code=2)
        self.assertIn('premise', error)
        error = self.register('ticket-1/repair-3', 'ticket-1', 'native-4', 'pane-4', repairs='ticket-1/repair-2',
                              premise='  ', code=2)
        self.assertIn('premise', error)
        self.assertEqual(self.cli('status')['attempts'][-1]['attempt'], 'ticket-1/repair-2')

    def test_first_and_second_fixes_need_no_premise_however_many_one_round_needs(self):
        # Rule: one round may repair an attempt several times (one repair per review layer or batch); those are all
        # first fixes, and a fix of one of them is a second fix. None of them needs a premise.
        self.collected('ticket-1/1', 1)
        self.collected('ticket-1/repair-1', 2, repairs='ticket-1/1')
        for number in (3, 4):
            self.register(f'ticket-1/repair-{number - 1}', 'ticket-1', f'native-{number}', f'pane-{number}',
                          repairs='ticket-1/1')
        self.register('ticket-1/repair-4', 'ticket-1', 'native-5', 'pane-5', repairs='ticket-1/repair-1')
        # A premise given anyway is still a sentence.
        error = self.register('ticket-1/repair-5', 'ticket-1', 'native-6', 'pane-6', repairs='ticket-1/1',
                              premise={'not': 'a sentence'}, code=2)
        self.assertIn('premise', error)

    def test_a_premised_third_repair_records_what_each_route_was_found_and_repaired_for(self):
        # Rule: the census a third repair is weighed against comes from the state, never from the orchestrator: per
        # route, the workstream's collected attempts that wrote work and the review defects and repair rounds attributed
        # to them before this one. Read-only reviewers on a route are not work review could find defects in.
        error = self.register('ticket-1/1', census={'claude/opus': {'attempts': 2}}, code=2)
        self.assertIn('census', error)
        self.collected('ticket-1/1', 1)
        self.collected('ticket-2/1', 5)
        self.collected('review/1', 6, model='opus', agent='reviewer', job='diff-review')
        self.cli('attribute', '--attempt', 'ticket-1/1', '--kind', 'review-defect', '--count', '3',
                 '--evidence', 'round-1.md#F1')
        self.collected('ticket-1/repair-1', 2, model='opus', repairs='ticket-1/1')
        self.cli('attribute', '--attempt', 'ticket-1/repair-1', '--kind', 'review-defect', '--count', '2',
                 '--evidence', 'round-2.md#F1')
        self.collected('ticket-1/repair-2', 3, model='opus', repairs='ticket-1/repair-1')
        self.cli('attribute', '--attempt', 'ticket-1/repair-2', '--kind', 'review-defect', '--evidence', 'round-3.md#F1')
        premise = 'A check run names the revision it checked.'
        result = self.register('ticket-1/repair-3', 'ticket-1', 'native-4', 'pane-4', repairs='ticket-1/repair-2',
                               premise=premise)
        census = {'claude/sonnet': {'attempts': 2, 'review_defects': 3, 'repair_rounds': 1},
                  'claude/opus': {'attempts': 2, 'review_defects': 3, 'repair_rounds': 1}}
        self.assertEqual(result['census'], census)
        saved = self.cli('status', '--attempt', 'ticket-1/repair-3')['attempts'][0]
        self.assertEqual((saved['premise'], saved['census']), (premise, census))

    def test_acceptance_is_recorded_once_on_a_completed_collected_attempt(self):
        self.register()
        self.ready()
        error = self.record('accepted', commit='merge-1', evidence=['git log merge-1'], code=2)
        self.assertIn('completed result', error)
        self.watch()
        self.record('reconciled', evidence=['native worker idle with its report'])
        self.collect()
        self.acknowledge()
        self.record('closed', evidence=['native session exited'], acceptance={'decision': 'deferred', 'reason': 'acceptance checks pending'})
        self.assertIn('commit', self.record('accepted', 'ticket-1/1', evidence=['log'], code=2))
        self.record('accepted', commit='merge-1', evidence=['git log merge-1'])
        accepted = self.cli('status')['attempts'][0]['accepted']
        self.assertEqual((accepted['commit'], accepted['evidence']), ('merge-1', ['git log merge-1']))
        self.assertIn('already accepted', self.record('accepted', commit='merge-2', evidence=['log'], code=2))

    def test_an_attempt_whose_report_changed_after_collection_closes_only_declined(self):
        # Live E2E 2026-10-26: a worker's report changed after it returned; that attempt can never be accepted, and
        # it must still be closable, as declined, so it does not stay open with no process behind it.
        self.register()
        self.ready()
        report = self.collect()
        self.acknowledge()
        report.write_text(report.read_text() + 'changed after collection\n')
        error = self.record('closed', evidence=['native session exited'], code=2,
                            acceptance={'decision': 'accepted', 'commit': 'merge-1', 'evidence': ['git log merge-1']})
        self.assertIn('intact report', error)
        self.record('closed', evidence=['native session exited'],
                    acceptance={'decision': 'declined', 'reason': 'its report changed after it was collected'})
        attempt = self.cli('status')['attempts'][0]
        self.assertEqual(attempt['declined']['reason'], 'its report changed after it was collected')
        # Asking to decline an attempt already accepted declines nothing, so it is refused too.
        self.register('ticket-2/1', 'ticket-2', 'native-2', 'pane-2')
        self.ready('ticket-2/1')
        accepted = self.collect('ticket-2/1')
        self.acknowledge()
        self.record('accepted', 'ticket-2/1', commit='merge-2', evidence=['git log merge-2'])
        accepted.write_text(accepted.read_text() + 'changed after acceptance\n')
        error = self.record('closed', 'ticket-2/1', evidence=['native session exited'], code=2,
                            acceptance={'decision': 'declined', 'reason': 'report changed'})
        self.assertIn('intact report', error)

    def test_a_conversation_closes_only_when_each_of_its_changed_reports_is_declined(self):
        # Two attempts of one native conversation: the earlier one's report changed and stays undeclined.
        self.register()
        self.ready()
        earlier = self.collect()
        self.acknowledge()
        self.record('retain', reason='Same-assignment recheck needed', next_use='recheck', evidence=['report'])
        self.register('ticket-1/2')
        self.ready('ticket-1/2')
        self.collect('ticket-1/2')
        self.acknowledge()
        earlier.write_text(earlier.read_text() + 'changed after collection\n')
        closing = dict(evidence=['native session exited'], acceptance={'decision': 'deferred', 'reason': 'pending'})
        self.assertIn('intact report', self.record('closed', 'ticket-1/2', code=2, **closing))
        self.record('declined', 'ticket-1/1', reason='its report changed after it was collected', evidence=['report digest'])
        self.record('closed', 'ticket-1/2', **closing)

    def test_closing_a_completed_ticket_settles_its_acceptance(self):
        self.register()
        self.ready()
        self.collect()
        self.acknowledge()
        error = self.record('closed', evidence=['native session exited'], code=2)
        self.assertIn('acceptance', error)
        self.assertIn('Settle acceptance', self.record(
            'closed', evidence=['native session exited'], acceptance={'decision': 'deferred'}, code=2))
        self.record('closed', evidence=['native session exited'],
                    acceptance={'decision': 'accepted', 'commit': 'merge-1', 'evidence': ['git log merge-1']})
        attempt = self.cli('status')['attempts'][0]
        self.assertEqual(attempt['accepted']['commit'], 'merge-1')
        self.assertEqual(attempt['attention'], [])
        # Acceptance can wait for checks; until then the attempt asks for it.
        self.register('ticket-2/1', 'ticket-2', 'native-2', 'pane-2')
        self.ready('ticket-2/1')
        self.collect('ticket-2/1')
        self.acknowledge()
        self.record('closed', 'ticket-2/1', evidence=['native session exited'],
                    acceptance={'decision': 'deferred', 'reason': 'integration gate still running'})
        deferred = self.cli('status', '--attempt', 'ticket-2/1')['attempts'][0]
        self.assertIn('record acceptance or decline', deferred['attention'])
        self.record('accepted', 'ticket-2/1', commit='merge-2', evidence=['git log merge-2'])
        self.assertEqual(self.cli('status', '--attempt', 'ticket-2/1')['attempts'][0]['attention'], [])
        # A deferred attempt may also be declined once its checks fail.
        self.register('ticket-4/1', 'ticket-4', 'native-4', 'pane-4')
        self.ready('ticket-4/1')
        self.collect('ticket-4/1')
        self.acknowledge()
        self.record('closed', 'ticket-4/1', evidence=['native session exited'],
                    acceptance={'decision': 'deferred', 'reason': 'integration gate still running'})
        self.assertIn('reason', self.record('declined', 'ticket-4/1', evidence=['gate log'], code=2))
        self.record('declined', 'ticket-4/1', reason='integration gate failed', evidence=['gate log'])
        self.assertEqual(self.cli('status', '--attempt', 'ticket-4/1')['attempts'][0]['attention'], [])
        self.assertIn('declined', self.record('accepted', 'ticket-4/1', commit='m', evidence=['log'], code=2))
        self.assertIn('already accepted', self.record('declined', 'ticket-2/1', reason='late', evidence=['x'], code=2))
        # A declined attempt is settled and cannot be accepted later.
        self.register('ticket-3/1', 'ticket-3', 'native-3', 'pane-3')
        self.ready('ticket-3/1')
        self.collect('ticket-3/1')
        self.acknowledge()
        self.record('closed', 'ticket-3/1', evidence=['native session exited'],
                    acceptance={'decision': 'declined', 'reason': 'criteria 2 unmet; replaced by attempt 2'})
        declined = self.cli('status', '--attempt', 'ticket-3/1')['attempts'][0]
        self.assertEqual((declined['declined']['reason'], declined['attention']),
                         ('criteria 2 unmet; replaced by attempt 2', []))
        self.assertIn('declined', self.record('accepted', 'ticket-3/1', commit='m', evidence=['log'], code=2))

    def test_other_jobs_close_without_an_acceptance_decision(self):
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': 'reviewer', 'job': 'diff-review',
                                             'settings': {'model': 'sonnet', 'effort': 'high'}}))
        self.register()
        self.ready()
        self.collect()
        self.acknowledge()
        self.record('closed', evidence=['native session exited'])
        self.assertEqual(self.cli('status')['attempts'][0]['attention'], [])

    def test_each_notified_wake_is_triaged_in_shadow_and_a_direct_collection_labels_it(self):
        execution = json.loads(self.execution.read_text())
        execution['decision_policy'] = {'enabled': ['wake-triage'], 'backend': self.system_one()}
        self.execution.write_text(json.dumps(execution))
        journal = self.root / 'decisions.jsonl'
        self.register(journal=str(journal))
        self.ready()
        report = self.root / 'report.md'
        report.write_text('Implemented at head-1; all checks pass.\n')
        parent = self.root / 'parent.jsonl'
        request = self.root / 'wait.json'
        request.write_text(json.dumps({'argv': [sys.executable, '-c', 'print("idle")'], 'timeout': 2,
            'notify': {'argv': [sys.executable, '-c',
                                f'import sys; open({str(parent)!r}, "a").write(sys.argv[1] + "\\n")', '{receipt}'],
                       'timeout': 2},
            'capture': [sys.executable, '-c', 'print("Implemented; report written. All checks pass.")']}))
        receipt = self.cli('watch', '--attempt', 'ticket-1/1', '--input', str(request))
        triage = self.cli('status')['pending'][0]['payload']['triage']
        self.assertEqual(triage['status'], 'recorded')
        decision = [row for row in journal_store.read(journal) if row['event'] == 'decision'][0]
        self.assertEqual(decision['decision_id'], triage['decision_id'])
        self.assertEqual(decision['request']['family'], 'wake-triage')
        self.assertEqual(decision['assignment'], f"ticket-1/1/wake-{receipt['event_id']}")
        self.assertIn('All checks pass.', decision['request']['state'])
        # The host is woken with the receipt alone; the label stays hidden.
        self.assertNotIn('triage', parent.read_text())
        self.record('reconciled', evidence=['native worker idle with its report'])
        self.collect(report=report)
        assessment = [row for row in journal_store.read(journal) if row['event'] == 'assessment'][0]
        self.assertEqual((assessment['expected'], assessment['source'], assessment['evidence']),
                         (['done'], 'objective', [str(report)]))

    def test_retiring_a_completed_ticket_settles_acceptance_before_closing_the_pane(self):
        self.register()
        self.ready()
        self.collect()
        self.acknowledge()
        self.herdr_fixture()
        request = self.root / 'retire.json'
        retire = {'session': 'fixture-session', 'pane': 'pane-1', 'jobs_evidence': ['owned jobs stopped']}
        request.write_text(json.dumps(retire))
        self.assertIn('acceptance', self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2))
        self.assertFalse((self.root / 'native-closed').exists())
        request.write_text(json.dumps(dict(retire, acceptance={
            'decision': 'accepted', 'commit': 'merge-1', 'evidence': ['topic fast-forwarded to merge-1']})))
        self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request))
        attempt = self.cli('status')['attempts'][0]
        self.assertEqual((attempt['accepted']['commit'], attempt['closed']['acceptance']['decision']),
                         ('merge-1', 'accepted'))




if __name__ == '__main__':
    unittest.main()
