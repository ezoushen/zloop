#!/usr/bin/env python3
"""Decision-model worker selection over preflighted, narrowed candidate pools."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import support
from router_case import RouterCase, models


class DecisionRouting(RouterCase):
    def test_decision_model_selects_only_preflighted_model_effort_pairs(self):
        self.decision_router()
        result = self.resolve(assignment='Implement the agreed bounded fix.',
                              eligible_candidates=['sol', 'opus'], target_runtime='codex')
        self.assertEqual(result['candidate'], 'sol')
        self.assertEqual(result['settings'], {'model': 'codex/sol', 'effort': 'medium'})
        self.assertEqual(result['selection_source'], 'decision-model')
        self.assertEqual(result['executor_context'], 'herdr')
        self.assertEqual(len(self.requests), 2)  # one decision, scored in both option orders
        options = result['decision_model']['options']
        self.assertEqual(list(options.values()), [
            {'candidate': 'sol', 'thinking': 'medium'}, {'candidate': 'sol', 'thinking': 'high'}])
        self.assertIn('codex/sol', str(self.requests[0]))
        self.assertNotIn('Run one worker.', str(self.requests[0]))
        self.assertNotIn(str(self.root), str(self.requests[0]))

    def test_excluded_candidates_alone_leave_the_decision_and_shadow(self):
        self.decision_router()
        result = self.resolve(assignment='Implement the agreed bounded fix.',
                              excluded_candidates={'luna': 'Codex xhigh quota exhausted today.',
                                                   'opus': 'Claude weekly allowance critical.'})
        self.assertEqual(result['candidate'], 'sol')
        offered = {option['candidate'] for option in result['decision_model']['options'].values()}
        self.assertEqual(offered, {'sol'})
        self.assertIn('opus: Claude weekly allowance critical.', self.requests[0]['state'])
        self.assertEqual(set(result['shadow_selection']['candidates']), {'sol'})

    def test_exclusions_apply_before_thinking_preflight_and_recommendation_pins(self):
        self.decision_router()
        result = self.resolve(assignment='Implement the agreed bounded fix.', eligible_thinking={'sol': ['medium']},
                              excluded_candidates={'luna': 'Quota exhausted.', 'opus': 'Allowance critical.'})
        self.assertEqual(result['candidate'], 'sol')
        self.profile({'codex': {'candidate': 'sol'}, 'claude': {'candidate': 'opus'}})
        result = self.resolve(selection=self.choose('opus'), excluded_candidates={'opus': 'Allowance critical.'})
        self.assertEqual(result['status'], 'selection_required')
        self.assertIn('sol', result['eligible_candidates'])

    def test_a_candidate_cannot_be_both_eligible_and_excluded(self):
        self.decision_router()
        with self.assertRaisesRegex(ValueError, 'both eligible and excluded: opus'):
            self.resolve(assignment='Implement the agreed bounded fix.', eligible_candidates=['sol', 'opus'],
                         excluded_candidates={'luna': 'Quota exhausted.', 'opus': 'Allowance critical.'})
        self.assertEqual(self.requests, [])

    def test_host_narrowed_pool_needs_a_reason_for_each_excluded_candidate(self):
        self.decision_router()
        with self.assertRaisesRegex(ValueError, 'reason.*luna'):
            self.resolve(assignment='Implement the agreed bounded fix.', eligible_candidates=['sol', 'opus'],
                         excluded_candidates=None)
        with self.assertRaisesRegex(ValueError, 'reason'):
            self.resolve(assignment='Implement the agreed bounded fix.', eligible_candidates=['sol', 'opus'],
                         excluded_candidates={'luna': ' '})
        with self.assertRaisesRegex(ValueError, 'nova.*configured: luna, opus, sol'):
            self.resolve(assignment='Implement the agreed bounded fix.', eligible_candidates=['sol', 'opus'],
                         excluded_candidates={'luna': 'Quota exhausted.', 'nova': 'Not configured.'})
        self.assertEqual(self.requests, [])
        result = self.resolve(assignment='Implement the agreed bounded fix.', eligible_candidates=['sol', 'opus'],
                              excluded_candidates={'luna': 'Codex xhigh quota exhausted today.'})
        self.assertEqual(result['selection_source'], 'decision-model')
        # The configured pool and each exclusion reason travel with the decision request.
        self.assertIn('luna: Codex xhigh quota exhausted today.', self.requests[0]['state'])
        # Preflight removing every thinking level is its own reason.
        self.resolve(assignment='Implement the agreed bounded fix.', eligible_thinking={'luna': []})
        self.assertIn('luna: no eligible thinking level after runtime preflight', self.requests[-1]['state'])

    def test_worker_selection_sees_earlier_outcomes_for_the_role(self):
        self.decision_router()
        journal = self.root / 'decisions.jsonl'
        record = models.decisions.journal_store.record
        first = self.resolve(assignment='Implement ticket 1.', journal=journal, assignment_id='t1/attempt-1')
        did = first['decision_model']['decision_id']
        record(journal, {'event': 'action', 'decision_id': did, 'actor': 'host', 'disposition': 'accepted',
                         'choice': first['decision_model']['choice'], 'reason': 'Fits.'})
        record(journal, {'event': 'execution', 'decision_id': did,
                         'requested': {'runtime': 'codex', 'model': 'codex/luna', 'effort': 'xhigh'},
                         'observed': {'runtime': 'codex', 'model': 'codex/luna', 'effort': 'xhigh'},
                         'evidence': ['rollout'], 'revision': 'abc'})
        record(journal, {'event': 'outcome', 'decision_id': did, 'status': 'failed',
                         'evidence': ['reports/1.md'], 'revision': 'abc'})
        self.resolve(assignment='Implement ticket 2.', journal=journal, assignment_id='t2/attempt-1')
        third = self.resolve(assignment='Implement ticket 3.', journal=journal, assignment_id='t3/attempt-1')
        state = self.requests[-1]['state']
        self.assertIn('Earlier builder ticket outcomes in this journal', state)
        self.assertIn('codex/luna xhigh: 1 failed', state)
        warnings = [w['code'] for w in third['decision_warnings']]
        self.assertIn('missing_outcomes', warnings)

    def test_worker_selection_sees_quality_signals_attributed_to_earlier_work(self):
        self.decision_router()
        journal = self.root / 'decisions.jsonl'
        record = models.decisions.journal_store.record
        first = self.resolve(assignment='Implement ticket 1.', journal=journal, assignment_id='t1/attempt-1')
        did = first['decision_model']['decision_id']
        record(journal, {'event': 'action', 'decision_id': did, 'actor': 'worker-lifecycle', 'disposition': 'accepted',
                         'choice': first['decision_model']['choice'], 'reason': 'Registered.'})
        record(journal, {'event': 'execution', 'decision_id': did,
                         'requested': {'runtime': 'codex', 'model': 'codex/luna', 'effort': 'xhigh'},
                         'observed': {'runtime': 'codex', 'model': 'codex/luna', 'effort': 'xhigh'},
                         'evidence': ['rollout'], 'revision': 'abc'})
        for metrics in ({'worker_seconds': 30}, {'worker_seconds': 30, 'review_defects': 2, 'repair_rounds': 1}):
            record(journal, {'event': 'outcome', 'decision_id': did, 'status': 'succeeded', 'metrics': metrics,
                             'evidence': ['reports/1.md'], 'revision': 'abc'})
        self.resolve(assignment='Implement ticket 2.', journal=journal, assignment_id='t2/attempt-1')
        self.assertIn('- codex/luna xhigh: 1 succeeded; review defects: 2, repair rounds: 1\n',
                      self.requests[-1]['state'])

    def test_resume_reuses_its_decision_after_other_assignments_record_outcomes(self):
        self.decision_router()
        journal = self.root / 'decisions.jsonl'
        record = models.decisions.journal_store.record
        first = self.resolve(assignment='Implement ticket 1.', journal=journal, assignment_id='a/1')
        other = self.resolve(assignment='Implement ticket 2.', journal=journal, assignment_id='b/1')
        did = other['decision_model']['decision_id']
        record(journal, {'event': 'action', 'decision_id': did, 'actor': 'host', 'disposition': 'accepted',
                         'choice': other['decision_model']['choice'], 'reason': 'Fits.'})
        record(journal, {'event': 'execution', 'decision_id': did,
                         'requested': {'runtime': 'codex', 'model': 'codex/luna', 'effort': 'xhigh'},
                         'observed': {'runtime': 'codex', 'model': 'codex/luna', 'effort': 'xhigh'},
                         'evidence': ['rollout'], 'revision': 'abc'})
        record(journal, {'event': 'outcome', 'decision_id': did, 'status': 'succeeded',
                         'evidence': ['reports/2.md'], 'revision': 'abc'})
        resumed = self.resolve(assignment='Implement ticket 1.', journal=journal, assignment_id='a/1')
        self.assertTrue(resumed['decision_model']['reused'])
        self.assertEqual(resumed['decision_model']['decision_id'], first['decision_model']['decision_id'])

    def test_resume_after_failure_reuses_its_decision(self):
        self.decision_router()
        journal = self.root / 'decisions.jsonl'
        record = models.decisions.journal_store.record
        first = self.resolve(assignment='Implement ticket 1.', journal=journal, assignment_id='t1/attempt-1')
        did = first['decision_model']['decision_id']
        record(journal, {'event': 'action', 'decision_id': did, 'actor': 'host', 'disposition': 'accepted',
                         'choice': first['decision_model']['choice'], 'reason': 'Fits.'})
        record(journal, {'event': 'execution', 'decision_id': did,
                         'requested': {'runtime': 'codex', 'model': 'codex/luna', 'effort': 'xhigh'},
                         'observed': {'runtime': 'codex', 'model': 'codex/luna', 'effort': 'xhigh'},
                         'evidence': ['rollout'], 'revision': 'abc'})
        record(journal, {'event': 'outcome', 'decision_id': did, 'status': 'failed',
                         'evidence': ['reports/1.md'], 'revision': 'abc'})
        retry = dict(assignment='Implement ticket 1.', journal=journal, assignment_id='t1/attempt-2',
                     after_failure='Attempt 1 failed review.')
        second = self.resolve(**retry)
        resumed = self.resolve(**retry)
        self.assertTrue(resumed['decision_model']['reused'])
        self.assertEqual(resumed['decision_model']['decision_id'], second['decision_model']['decision_id'])

    def test_worker_decision_is_recorded_once_with_semantic_option_mapping(self):
        self.decision_router()
        journal = self.root / 'decisions.jsonl'
        result = self.resolve(assignment='Implement the agreed bounded fix.',
                              eligible_candidates=['sol'], target_runtime='codex',
                              journal=journal, assignment_id='ticket-2/attempt-1')
        recorded = models.decisions.journal_store.inspect(
            journal, result['decision_model']['decision_id'])['decision']
        self.assertEqual(len(self.requests), 2)  # one decision, scored in both option orders
        self.assertEqual(recorded['payload'], self.requests[0])
        option = json.loads(recorded['request']['options'][recorded['result']['choice']])
        self.assertEqual(option['candidate'], result['candidate'])
        self.assertEqual(option['thinking'], result['settings']['effort'])
        self.assertEqual(recorded['assignment'], 'ticket-2/attempt-1')
        resumed = self.resolve(assignment='Implement the agreed bounded fix.',
                               eligible_candidates=['sol'], target_runtime='codex',
                               journal=journal, assignment_id='ticket-2/attempt-1')
        self.assertEqual(resumed['decision_model']['decision_id'], result['decision_model']['decision_id'])
        self.assertTrue(resumed['decision_model']['reused'])
        self.assertEqual(len(self.requests), 2)  # one decision, scored in both option orders
        trace = models.decisions.journal_store.trace_config(journal, self.root, 'ticket-2/attempt-1')
        check = models.decisions.journal_store.check_checkpoint(journal, trace, 'worker-selection', 'launch')
        self.assertEqual(check['checkpoint_id'], result['decision_model']['checkpoint_id'])
        self.assertEqual(check['missing_evidence'], ['action'])

    def test_single_permitted_option_is_settled_without_consultation(self):
        self.decision_router()
        journal = self.root / 'decisions.jsonl'
        result = self.resolve(assignment='Implement the agreed bounded fix.',
                              eligible_candidates=['sol'], eligible_thinking={'sol': ['high']},
                              target_runtime='codex', journal=journal, assignment_id='ticket-3/attempt-1')
        self.assertEqual(self.requests, [])
        self.assertEqual(result['candidate'], 'sol')
        self.assertEqual(result['settings'], {'model': 'codex/sol', 'effort': 'high'})
        self.assertEqual(result['selection_source'], 'sole-option')
        self.assertEqual(result['decision_model']['checkpoint_status'], 'skipped')
        self.assertNotIn('decision_id', result['decision_model'])
        self.assertEqual(result['decision_model']['proposer'], 'resolver')
        trace = models.decisions.journal_store.trace_config(journal, self.root, 'ticket-3/attempt-1')
        check = models.decisions.journal_store.check_checkpoint(
            journal, trace, 'worker-selection', 'completion')
        self.assertEqual(check['checkpoint_id'], result['decision_model']['checkpoint_id'])
        self.assertEqual(check['missing_evidence'], [])
        resumed = self.resolve(assignment='Implement the agreed bounded fix.',
                               eligible_candidates=['sol'], eligible_thinking={'sol': ['high']},
                               target_runtime='codex', journal=journal, assignment_id='ticket-3/attempt-1')
        self.assertTrue(resumed['decision_model']['reused'])
        self.assertEqual(resumed['decision_model']['checkpoint_id'], result['decision_model']['checkpoint_id'])
        self.assertEqual(self.requests, [])

    def test_decision_journal_cli_records_inspects_and_replays_without_changing_inputs(self):
        self.decision_router()
        directory = support.SCRIPTS
        journal = self.root / 'decisions.jsonl'
        request = self.root / 'request.json'
        request.write_text(json.dumps({'family': 'worker-selection', 'state': 'Bounded edit.',
                                       'instruction': 'Choose the adequate worker.',
                                       'options': {'small': 'Bounded changes.', 'large': 'Complex changes.'}}))
        def cli(script, *args):
            return subprocess.run([sys.executable, '-B', str(directory / script), *map(str, args)],
                                  capture_output=True, text=True)
        result = cli('decisions.py', 'decide', '--repo', self.root, '--user-executors', self.user,
                     '--request', request, '--journal', journal, '--assignment-id', 'edit/1')
        self.assertEqual(result.returncode, 0, result.stderr)
        did = json.loads(result.stdout)['decision_id']
        event = self.root / 'action.json'
        event.write_text(json.dumps({'event': 'action', 'decision_id': did, 'actor': 'host',
                                     'disposition': 'accepted', 'choice': 'small', 'reason': 'Bounded change.'}))
        result = cli('decision_journal.py', 'record', '--journal', journal, '--event', event)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = cli('decision_journal.py', 'inspect', '--journal', journal)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['decisions'][0]['host_choice'], 'small')
        self.assertEqual(len(self.requests), 2)  # one decision, scored in both option orders
        inputs = {p: p.read_bytes() for p in (journal, self.user, request)}
        cases = self.root / 'cases.json'
        cases.write_text(json.dumps([{'decision_id': did, 'expected': ['small'],
                                     'evidence': 'Reviewer verified scope.', 'instruction': 'Pick the lightest adequate worker.'}]))
        output = self.root / 'replay.json'
        args = ('replay', '--journal', journal, '--repo', self.root, '--user-executors', self.user,
                '--cases', cases, '--output', output)
        result = cli('decision_journal.py', *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.requests), 5)  # replays send each recorded variant once
        report = json.loads(output.read_text())
        self.assertEqual(report['variants']['original']['accuracy'], 1)
        self.assertEqual(report['variants']['reversed']['accuracy'], 0)
        self.assertEqual(report['results'][0]['payload'], self.requests[0])
        self.assertEqual(inputs, {p: p.read_bytes() for p in inputs})
        result = cli('decision_journal.py', *args)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.requests), 5)

    def test_shared_decisions_config_preserves_routing_and_disabled_family_is_offline(self):
        self.decision_router()
        self.data['decisions'] = {'backend': {key: value for key, value in self.data['router'].items()
                                            if key != 'type'},
                                  'enabled': ['worker-selection', 'recovery']}
        self.data['router'] = {'type': 'decision-model'}
        self.save()
        self.assertEqual(self.resolve(assignment='Fix the constant.')['candidate'], 'luna')
        self.assertEqual(len(self.requests), 2)  # one decision, scored in both option orders
        self.data['decisions']['enabled'] = ['recovery']
        self.save()
        result = self.resolve(assignment='Fix the constant.')
        self.assertEqual(result['status'], 'selection_required')
        self.assertEqual(result['selection_router'], {'type': 'host'})
        self.assertEqual(len(self.requests), 2)  # one decision, scored in both option orders

    def test_decision_config_rejects_bad_calibration_and_unknown_family(self):
        backend = {'protocol': 'systemone', 'endpoint': 'http://localhost:1241/v1/systemone', 'model': 'test'}
        for extra in ({'enabled': ['unknown']}, {'enabled': ['recovery', 'recovery']},
                      {'calibration': {'suite': 'relative.json', 'baseline': '/tmp/base.json'}},
                      {'calibration': {'suite': '/tmp/cases.json', 'baseline': '/tmp/base.json', 'check_every': 0}},
                      {'calibration': {'suite': '/tmp/cases.json', 'baseline': '/tmp/cases.json'}},
                      {'calibration': {'suite': '/tmp/cases.json', 'baseline': '/tmp/base.json', 'max_accuracy_drop': 2}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                models.validate_execution_config({'decisions': {'backend': backend, 'enabled': ['recovery'], **extra}}, 'test')

    def test_decision_model_can_select_higher_permitted_effort(self):
        def reply(result):
            answer = result['answers']['worker']
            keys = list(answer['probabilities'])
            answer.update(choice='option_1', probabilities={key: float(key == 'option_1') for key in keys})
        self.decision_router(reply)
        result = self.resolve(assignment='Diagnose a subtle contract failure.', eligible_candidates=['sol'])
        self.assertEqual(result['settings']['effort'], 'high')

    def test_native_profile_availability_filters_effort_before_routing(self):
        self.decision_router()
        result = self.resolve(assignment='Complete the accepted fix; preserve remaining allowance.',
                              eligible_candidates=['opus'], eligible_thinking={'opus': ['medium']})
        self.assertEqual(result['settings']['effort'], 'medium')
        self.assertEqual(list(result['decision_model']['options'].values()),
                         [{'candidate': 'opus', 'thinking': 'medium'}])
        self.assertEqual(result['eligible_thinking'], {'opus': ['medium']})

    def test_profile_discovery_failure_recovers_with_verified_session_effort_not_null(self):
        self.decision_router()
        with self.assertRaisesRegex(ValueError, 'exceeds configured thinking'):
            self.resolve(assignment='Concurrency repair.', eligible_candidates=['opus'],
                         eligible_thinking={'opus': [None]})
        self.assertEqual(self.requests, [])
        result = self.resolve(assignment='Concurrency repair; native session effort verified medium.',
                              eligible_candidates=['opus'], eligible_thinking={'opus': ['medium']})
        self.assertEqual(result['settings'], {'model': 'claude/opus', 'effort': 'medium'})
        self.assertEqual(self.requests, [])
        self.assertEqual(list(result['decision_model']['options'].values()),
                         [{'candidate': 'opus', 'thinking': 'medium'}])

    def test_preflight_blocks_unavailable_pins_profiles_and_host_recommendations(self):
        self.profile({'claude': {'candidate': 'opus', 'thinking': 'high'}})
        for override in ({}, {'candidate': 'opus', 'task': {'effort': 'high'}},
                         {'selection': self.choose('opus', thinking='high')}):
            with self.subTest(override=override), self.assertRaisesRegex(ValueError, 'runtime preflight'):
                self.resolve(target_runtime='claude', eligible_thinking={'opus': ['medium']}, **override)
        result = self.resolve(target_runtime='claude', eligible_thinking={'opus': ['medium']},
                              candidate='opus', task={'effort': 'medium'})
        self.assertEqual(result['settings']['effort'], 'medium')

    def test_preflight_exhaustion_and_invalid_levels_do_not_call_router(self):
        self.decision_router()
        for levels in ({'opus': []}, {'opus': ['ultra']}, {'unknown': ['medium']},
                       {'opus': ['medium', 'medium']}, {'opus': [{}]}):
            with self.subTest(levels=levels), self.assertRaises(ValueError):
                self.resolve(assignment='Fix the constant.', eligible_candidates=['opus'], eligible_thinking=levels)
        blocked = self.resolve(assignment='Fix the constant.', eligible_candidates=['opus'],
                               eligible_thinking={'opus': ['medium']}, task={'effort': 'high'})
        self.assertEqual(blocked['status'], 'selection_required')
        self.assertNotIn('settings', blocked)
        self.assertEqual(self.requests, [])

    def test_preflight_cli_rejects_unavailable_profile_without_rendering(self):
        self.profile({'claude': {'candidate': 'opus', 'thinking': 'high'}})
        result = subprocess.run([sys.executable, str(Path(models.__file__)), '--route', '--profile',
                                 '--repo', str(self.root), '--user-executors', str(self.user),
                                 '--host', 'claude', '--job', 'ticket', '--target-runtime', 'claude',
                                 '--eligible-thinking', '{"opus":["medium"]}'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('runtime preflight', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_decision_model_preserves_explicit_pins_and_inspection_is_offline(self):
        self.decision_router()
        for kwargs in ({'list_candidates': True}, {}, {'candidate': 'sol'},
                       {'selection': self.choose('sol')},
                       {'choice': {'executor': {'type': 'subagent'}}}):
            with self.subTest(kwargs=kwargs):
                self.resolve(**kwargs, **({} if not kwargs or kwargs.get('list_candidates')
                                         else {'assignment': 'Some task.'}))
        self.assertEqual(self.requests, [])
        self.data['agents']['builder'] = {'model': 'sol'}
        self.save()
        self.resolve(assignment='Some task.')
        self.assertEqual(self.requests, [])

    def test_decision_model_effort_pin_filters_before_query(self):
        self.decision_router()
        result = self.resolve(assignment='Follow the explicit effort setting.', task={'effort': 'high'})
        self.assertEqual(result['settings']['effort'], 'high')
        self.assertEqual({value['candidate'] for value in result['decision_model']['options'].values()},
                         {'sol', 'opus'})
        self.assertEqual({value['thinking'] for value in result['decision_model']['options'].values()}, {'high'})
        blocked = self.resolve(assignment='No matching effort.', task={'effort': 'ultra'})
        self.assertEqual(blocked['status'], 'selection_required')
        self.assertIn('No permitted', blocked['selection_error'])
        self.assertNotIn('settings', blocked)
        self.assertEqual(len(self.requests), 2)  # one decision, scored in both option orders

    def test_decision_model_does_not_invent_effort_for_inherited_or_fixed_models(self):
        self.decision_router()
        self.data['agents']['builder'] = {'candidates': {
            'inherit': {'executor': {'type': 'subagent'}},
            'haiku': {'executor': {'type': 'subagent'}, 'models': {'mode': 'override', 'model': 'haiku'}},
            'fixed': {'executor': {'type': 'subagent'}, 'models': {
                'mode': 'override', 'model': 'fixed', 'thinking': 'medium', 'allow_thinking_override': True}}
        }}
        self.save()
        result = self.resolve(assignment='Complete a small edit.')
        self.assertEqual(result['decision_model']['options'], {
            'option_0': {'candidate': 'inherit'}, 'option_1': {'candidate': 'haiku'},
            'option_2': {'candidate': 'fixed', 'thinking': 'medium'}})

    def test_decision_model_abstention_and_invalid_answers_fall_back_to_host(self):
        self.decision_router()
        cases = [
            {},
            {'type': 'choice', 'choice': 'outside', 'probabilities': {}},
            {'type': 'choice', 'choice': 'option_0', 'probabilities': {'option_0': float('nan')}},
        ]
        for answer in cases:
            with self.subTest(answer=answer), patch.object(models, 'systemone_request', return_value={
                    'model': self.data['router']['model'], 'answers': {'worker': answer}}):
                result = self.resolve(assignment='Route this assignment.', eligible_candidates=['sol'])
                self.assertEqual(result['status'], 'selection_required')
                self.assertEqual(result['selection_router'], {'type': 'host'})
                self.assertIn('selection_error', result)
                self.assertNotIn('settings', result)
        def abstain(result):
            answer = result['answers']['worker']
            answer.update(choice='abstain', probabilities={key: float(key == 'abstain')
                                                         for key in answer['probabilities']})
        self.decision_router(abstain)
        result = self.resolve(assignment='Task is underspecified.')
        self.assertEqual(result['status'], 'selection_required')
        self.assertEqual(result['selection_router'], {'type': 'host'})
        self.assertEqual(result['decision_model']['choice'], 'abstain')
        self.assertNotIn('settings', result)
        final = self.resolve(selection=self.choose('sol'), assignment='Task is underspecified.')
        self.assertEqual(final['candidate'], 'sol')
        self.assertEqual(len(self.requests), 2)  # one decision, scored in both option orders

    def test_decision_model_timeout_does_not_consume_worker_attempts_or_retry(self):
        self.decision_router()
        with patch.object(models, 'systemone_request', side_effect=TimeoutError('timed out')) as request:
            result = self.resolve(assignment='Implement the fix.')
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result['status'], 'selection_required')
        self.assertEqual(result['selection_router'], {'type': 'host'})
        self.assertNotIn('settings', result)

    def test_decision_model_rejects_empty_pool_without_network(self):
        self.decision_router()
        for kwargs in ({'eligible_candidates': []}, {'candidate': 'sol', 'eligible_candidates': ['luna']}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.resolve(assignment='Implement the fix.', **kwargs)
        self.assertEqual(self.requests, [])

    def test_decision_router_schema_rejects_invalid_protocol_endpoint_and_timeout(self):
        self.decision_router()
        for change in ({'protocol': 'chat'}, {'endpoint': 'file:///tmp/a'},
                       {'timeout_seconds': 0}, {'timeout_seconds': 61}, {'model': ''}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                models.validate_execution_config({'router': dict(self.data['router'], **change)}, 'fixture')

    def test_decision_router_cli_uses_assignment_and_rejects_inspection_mix(self):
        self.decision_router()
        assignment = self.root / 'assignment.txt'
        assignment.write_text('Implement the exact supplied fix and run its regression test.')
        command = [sys.executable, '-B', str(models.ROOT / 'scripts/agent_models.py'),
                   '--repo', str(self.root), '--host', 'codex', '--job', 'ticket',
                   '--route', '--user-executors', str(self.user), '--assignment', str(assignment),
                   '--eligible-candidates', '["sol"]']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--excluded-candidates', result.stderr)
        command += ['--excluded-candidates', '{"luna": "Quota exhausted.", "opus": "Wrong runtime."}']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['selection_source'], 'decision-model')
        result = subprocess.run(command + ['--list-candidates'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(self.requests), 2)  # one decision, scored in both option orders

    def test_decision_router_respects_runtime_availability_without_herdr(self):
        self.decision_router()
        with patch.dict(os.environ, {'HERDR_ENV': ''}):
            result = self.resolve(host='codex', assignment='Implement the supplied fix.')
        self.assertEqual({entry['candidate'] for entry in result['decision_model']['options'].values()},
                         {'luna', 'sol'})

    def test_decision_router_model_pin_and_explicit_profile_bypass(self):
        self.decision_router()
        result = self.resolve(assignment='Implement the fix.', task={'model': 'codex/sol'})
        self.assertEqual({entry['candidate'] for entry in result['decision_model']['options'].values()}, {'sol'})
        self.data['profiles'] = {'fixed': {'builder': {'codex': {'candidate': 'sol'}}}}
        self.save()
        result = self.resolve(assignment='Some task.', config_profile='fixed', target_runtime='codex')
        self.assertEqual(result['selection_source'], 'profile')
        self.assertEqual(len(self.requests), 2)  # one decision, scored in both option orders

    def test_custom_skill_is_inspectable_and_accepts_normalized_decision(self):
        self.data['router'] = {'type': 'skill', 'skill': 'my:jev-router', 'instruction': 'Prefer native work.'}
        self.save()
        before = self.user.read_bytes()
        self.assertEqual(self.resolve(list_candidates=True)['router'], self.data['router'])
        self.assertEqual(self.resolve()['status'], 'selection_required')
        result = self.resolve(selection=self.choose('sol', thinking='high'))
        self.assertEqual(result['settings'], {'model': 'codex/sol', 'effort': 'high'})
        self.assertEqual(self.user.read_bytes(), before)

    def test_excluded_recommendation_does_not_pin_runtime_during_recovery(self):
        self.profile({'codex': {'candidate': 'sol'}})
        result = self.resolve(eligible_candidates=['opus'], selection=self.choose('sol'))
        self.assertEqual(result['status'], 'selection_required')
        self.assertEqual(result['eligible_candidates'], ['opus'])
        result = self.resolve(eligible_candidates=['opus'], selection=self.choose('opus'))
        self.assertEqual(result['candidate'], 'opus')




if __name__ == '__main__':
    unittest.main()
