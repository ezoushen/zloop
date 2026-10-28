#!/usr/bin/env python3
"""Exercise the machine-wide outcome ledger through the lifecycle and outcomes CLIs."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import support

import journal_store
import decisions


LIFECYCLE = support.script('worker_lifecycle.py')
OUTCOMES = support.script('outcomes.py')


class OutcomeLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='zloop outcomes ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.enterContext(patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'state')}))
        self.state = self.root / 'workers.sqlite'

    def run_script(self, script, *args, code=0):
        process = subprocess.run([sys.executable, '-B', str(script), *args], capture_output=True, text=True)
        self.assertEqual(process.returncode, code, process.stdout + process.stderr)
        return json.loads(process.stdout) if process.stdout else process.stderr

    def record(self, kind, attempt, **fields):
        path = self.root / 'event.json'
        path.write_text(json.dumps(dict(kind=kind, attempt=attempt, **fields)))
        return self.run_script(LIFECYCLE, 'record', '--state', str(self.state), '--input', str(path))

    def attempt(self, attempt, job='ticket', candidate='sonnet', status='completed', decision=None, **closure):
        """Register, run and collect one attempt; close it with the given fields when any are given."""
        execution = self.root / f'{attempt.replace("/", "-")}.execution.json'
        agent = {'ticket': 'builder', 'diff-review': 'reviewer'}[job]
        execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': agent, 'job': job, 'candidate': candidate,
                                         'settings': {'model': candidate, 'effort': 'high'},
                                         **({'selection_source': 'decision-model', 'decision_model': decision}
                                            if decision else {})}))
        number = len(list(self.root.glob('*.execution.json')))
        self.record('register', attempt, assignment=attempt.split('/')[0], repo=str(self.root),
                    worktree=str(self.root), head='head-1', execution=str(execution), runtime='claude',
                    worker=f'pane-{number}', native_session=f'native-{number}', owned=True,
                    packet=str(support.assignment_file(self.root, job)))
        self.record('startup', attempt, observed={'runtime': 'claude', 'model': candidate, 'effort': 'high'},
                    evidence=['native.jsonl:1'])
        self.record('submitted', attempt, evidence=['native.jsonl:2'])
        report = self.root / f'{attempt.replace("/", "-")}.md'
        report.write_text(f'## Implemented\n\nReport for {attempt}\n')
        self.record('result', attempt, status=status, head='head-1', report=str(report), evidence=['checks.log'])
        for receipt in self.run_script(LIFECYCLE, 'status', '--state', str(self.state))['pending']:
            self.run_script(LIFECYCLE, 'ack', '--state', str(self.state), '--event-id', str(receipt['id']))
        if closure:
            self.record('closed', attempt, evidence=['native session exited'], **closure)

    def decide(self, journal, assignment, agent='builder', job='ticket'):
        """Journal a recommended worker-selection decision for sonnet or haiku, choosing sonnet."""
        options = {f'option_{index}': json.dumps({'candidate': name, 'model': name, 'runtime': 'claude'})
                   for index, name in enumerate(('sonnet', 'haiku'))}

        def transport(backend, payload):
            qid, question = next(iter(payload['questions'].items()))
            return {'model': backend['model'], 'answers': {qid: {
                'type': 'choice', 'choice': 'option_0',
                'probabilities': {key: float(key == 'option_0') for key in question['criteria']}}}}
        policy = {'enabled': ['worker-selection'], 'backend': {
            'protocol': 'systemone', 'endpoint': 'http://localhost:1241/v1/systemone', 'model': 'test-model'}}
        trace = journal_store.trace_config(journal, self.root, assignment)
        return decisions.decide(policy, {'family': 'worker-selection', 'state': f'Agent: {agent}\nJob: {job}\n',
                                         'instruction': 'Choose a worker.', 'options': options},
                                trace=trace, transport=transport)

    def summary(self, *args):
        return self.run_script(OUTCOMES, 'summary', *args)['agents']

    def test_lifecycle_outcomes_reach_the_ledger_and_count_acceptance_for_jobs_that_need_it(self):
        accepted = {'decision': 'accepted', 'commit': 'merge-1', 'evidence': ['git log merge-1']}
        self.attempt('t1/1', acceptance=accepted)
        self.attempt('t2/1', acceptance={'decision': 'declined', 'reason': 'criteria unmet'})
        self.attempt('t3/1', acceptance={'decision': 'deferred', 'reason': 'gate running'})
        self.attempt('t4/1', candidate='glm', status='failed', acceptance={'decision': 'deferred', 'reason': 'n/a'})
        self.attempt('t5/1', acceptance=accepted)
        self.run_script(LIFECYCLE, 'attribute', '--state', str(self.state), '--attempt', 't5/1', '--kind', 'revert',
                        '--count', '1', '--evidence', 'revert commit r-1')
        self.attempt('r1/1', job='diff-review', candidate='opus')
        # A blocked attempt waits on something outside the worker; it does not count against the model.
        self.attempt('r2/1', job='diff-review', candidate='opus', status='blocked')
        self.attempt('r3/1', job='diff-review', candidate='opus', status='failed')
        # A revert still fails it.
        self.attempt('r4/1', job='diff-review', candidate='opus', status='blocked')
        self.run_script(LIFECYCLE, 'attribute', '--state', str(self.state), '--attempt', 'r4/1', '--kind', 'revert',
                        '--count', '1', '--evidence', 'revert commit r-4')
        self.assertEqual(self.summary(), {
            'builder': {'ticket': {'sonnet': {'successes': 1, 'failures': 2, 'pending': 1},
                                   'glm': {'successes': 0, 'failures': 1, 'pending': 0}}},
            'reviewer': {'diff-review': {'opus': {'successes': 1, 'failures': 2, 'pending': 1}}}})
        # A later acceptance settles the deferred attempt.
        self.record('accepted', 't3/1', commit='merge-3', evidence=['git log merge-3'])
        self.assertEqual(self.summary('--agent', 'builder', '--job', 'ticket')['builder']['ticket']['sonnet'],
                         {'successes': 2, 'failures': 2, 'pending': 0})

    def test_past_decision_journals_import_once(self):
        journal = self.root / 'decisions.jsonl'
        options = {f'option_{index}': json.dumps({'candidate': name, 'model': name, 'runtime': 'claude'})
                   for index, name in enumerate(('sonnet', 'haiku'))}

        def transport(backend, payload):
            qid, question = next(iter(payload['questions'].items()))
            return {'model': backend['model'], 'answers': {qid: {
                'type': 'choice', 'choice': 'option_0',
                'probabilities': {key: float(key == 'option_0') for key in question['criteria']}}}}
        policy = {'enabled': ['worker-selection'], 'backend': {
            'protocol': 'systemone', 'endpoint': 'http://localhost:1241/v1/systemone', 'model': 'test-model'}}
        for assignment, state, chosen, status, metrics in (
                ('t1/1', 'Agent: builder\nJob: ticket\n', None, 'succeeded', {}),
                ('t2/1', 'Agent: builder\nJob: ticket\n', 'option_1', 'failed', {}),
                ('t3/1', 'Agent: builder\nJob: ticket\n', None, 'succeeded', {'reverts': 1}),
                ('t4/1', 'Agent: verifier\nJob: e2e-gate\n', None, None, {}),
                # The lifecycle records success at completion, so its journal outcome is not an acceptance.
                ('t5/1', 'Agent: builder\nJob: ticket\n', 'lifecycle', 'succeeded', {})):
            trace = journal_store.trace_config(journal, self.root, assignment)
            result = decisions.decide(policy, {'family': 'worker-selection', 'state': state,
                                               'instruction': 'Choose a worker.', 'options': options},
                                      trace=trace, transport=transport)
            did = result['decision_id']
            lifecycle = chosen == 'lifecycle'
            chosen = None if lifecycle else chosen
            journal_store.record(journal, {'event': 'action', 'decision_id': did,
                                              'actor': 'worker-lifecycle' if lifecycle else 'host',
                                              'disposition': 'overridden' if chosen else 'accepted',
                                              'choice': chosen or 'option_0', 'reason': 'host choice'})
            if status:
                journal_store.record(journal, {'event': 'outcome', 'decision_id': did, 'status': status,
                                                  'evidence': ['report.md'], 'metrics': metrics})
        self.assertEqual(self.run_script(OUTCOMES, 'import', str(journal)), {'imported': 5, 'already': 0})
        self.assertEqual(self.run_script(OUTCOMES, 'import', str(journal)), {'imported': 0, 'already': 5})
        self.assertEqual(self.summary(), {
            'builder': {'ticket': {'sonnet': {'successes': 1, 'failures': 1, 'pending': 1},
                                   'haiku': {'successes': 0, 'failures': 1, 'pending': 0}}},
            'verifier': {'e2e-gate': {'sonnet': {'successes': 0, 'failures': 0, 'pending': 1}}}})


    def test_lifecycle_states_import_once_and_a_damaged_source_does_not_stop_the_rest(self):
        self.attempt('t1/1', acceptance={'decision': 'accepted', 'commit': 'm', 'evidence': ['git log m']})
        self.attempt('t2/1', candidate='glm', status='failed', acceptance={'decision': 'deferred', 'reason': 'n/a'})
        damaged = self.root / 'damaged-decisions.jsonl'
        damaged.write_text('{"event": "decision"\n')
        os.environ['XDG_STATE_HOME'] = str(self.root / 'other-state')
        result = self.run_script(OUTCOMES, 'import', str(damaged), str(self.state), code=1)
        self.assertEqual((result['imported'], result['already']), (2, 0))
        self.assertEqual(list(result['errors']), [str(damaged)])
        self.assertEqual(self.summary(), {'builder': {'ticket': {
            'sonnet': {'successes': 1, 'failures': 0, 'pending': 0},
            'glm': {'successes': 0, 'failures': 1, 'pending': 0}}}})
        self.assertEqual(self.run_script(OUTCOMES, 'import', str(self.state)), {'imported': 0, 'already': 2})


    def test_a_lifecycle_attempt_counts_once_whichever_of_its_journal_or_state_is_imported(self):
        journal = self.root / 'decisions.jsonl'
        for name, closure in (('t1/1', {'decision': 'declined', 'reason': 'criteria unmet'}),
                              ('t2/1', {'decision': 'deferred', 'reason': 'gate running'})):
            decision = dict(self.decide(journal, name), journal=str(journal))
            self.attempt(name, decision=decision, acceptance=closure)
        expected = {'builder': {'ticket': {'sonnet': {'successes': 0, 'failures': 1, 'pending': 1}}}}
        self.assertEqual(self.summary(), expected)
        self.assertEqual(self.run_script(OUTCOMES, 'import', str(journal)), {'imported': 0, 'already': 2})
        self.assertEqual(self.summary(), expected)
        # Imported the other way round, the state file replaces its journal copies, including a state
        # from before the lifecycle kept the link, which still holds it in the resolved route.
        with sqlite3.connect(self.state) as db:
            for attempt, data in list(db.execute('SELECT id, data FROM attempts')):
                saved = json.loads(data)
                saved.pop('decision', None)
                db.execute('UPDATE attempts SET data = ? WHERE id = ?', (json.dumps(saved), attempt))
        os.environ['XDG_STATE_HOME'] = str(self.root / 'other-state')
        self.run_script(OUTCOMES, 'import', str(journal))
        self.assertEqual(self.summary(), {'builder': {'ticket': {'sonnet': {'successes': 0, 'failures': 0, 'pending': 2}}}})
        self.run_script(OUTCOMES, 'import', str(self.state))
        self.assertEqual(self.summary(), expected)
        # Both in one call, state first, still count each attempt once.
        os.environ['XDG_STATE_HOME'] = str(self.root / 'third-state')
        self.run_script(OUTCOMES, 'import', str(self.state), str(journal))
        self.assertEqual(self.summary(), expected)

    def test_rows_from_before_agents_and_jobs_are_not_counted(self):
        self.attempt('t1/1', acceptance={'decision': 'accepted', 'commit': 'm-1', 'evidence': ['git log m-1']})
        with sqlite3.connect(Path(os.environ['XDG_STATE_HOME']) / 'zloop/outcomes.sqlite') as db:
            db.execute('INSERT INTO outcomes VALUES (?, ?)', ('old#1', json.dumps(
                {'role': 'implementer', 'candidate': 'sonnet', 'status': 'failed'})))
        self.assertEqual(self.summary(), {'builder': {'ticket': {
            'sonnet': {'successes': 1, 'failures': 0, 'pending': 0}}}})

    def test_reimporting_a_state_file_refreshes_its_rows(self):
        self.attempt('t1/1', acceptance={'decision': 'deferred', 'reason': 'gate running'})
        with sqlite3.connect(Path(os.environ['XDG_STATE_HOME']) / 'zloop/outcomes.sqlite') as db:
            key, data = db.execute('SELECT key, data FROM outcomes').fetchone()
            row = json.loads(data)
            row.update(result=None, status='failed')  # As an earlier version wrote it.
            db.execute('UPDATE outcomes SET data = ? WHERE key = ?', (json.dumps(row), key))
        self.assertEqual(self.summary()['builder']['ticket']['sonnet']['failures'], 1)
        self.assertEqual(self.run_script(OUTCOMES, 'import', str(self.state)), {'imported': 0, 'already': 1})
        self.assertEqual(self.summary(), {'builder': {'ticket': {'sonnet': {'successes': 0, 'failures': 0, 'pending': 1}}}})


if __name__ == '__main__':
    unittest.main()
