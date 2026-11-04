#!/usr/bin/env python3
"""`worker_lifecycle.py reap` closes the attempts whose status is already decided: a collected worker whose Herdr tab
is confirmed gone, and a retained native subagent whose run ended."""

import json
import os
import sqlite3
import unittest

from lifecycle_case import WorkerLifecycleCase


NOTHING = {'closed': [], 'open': [], 'refused': []}


class Reap(WorkerLifecycleCase):
    def placed(self, attempt='ticket-1/1', **fixture):
        """A collected attempt of the fixture's Herdr worker (session fixture-session, tab tab-1), its tab still open."""
        self.herdr_fixture(**fixture)
        self.register(attempt, worker='builder-1', herdr={'session': 'fixture-session'})
        self.ready(attempt)
        self.collect(attempt)
        self.acknowledge()

    def tab_closed(self):
        """The fixture's Herdr now answers that the worker's pane and tab are gone."""
        (self.root / 'native-closed').write_text('closed\n')

    def closed(self, attempt='ticket-1/1'):
        return self.cli('status', '--attempt', attempt)['attempts'][0].get('closed')

    def test_a_collected_attempt_whose_herdr_tab_is_confirmed_gone_is_closed_with_the_answer_as_evidence(self):
        # Rule 1: Herdr's own answer for the recorded tab decides; a tab it still lists closes nothing.
        self.placed()
        self.assertEqual([], self.cli('reap')['closed'])
        self.assertIsNone(self.closed())
        self.tab_closed()
        found = self.cli('reap')
        self.assertEqual([{'attempt': 'ticket-1/1',
                           'evidence': ['herdr --session fixture-session tab get tab-1: tab_not_found']}],
                         found['closed'])
        self.assertEqual(['herdr --session fixture-session tab get tab-1: tab_not_found'],
                         self.closed()['evidence'])

    def test_a_herdr_server_that_is_not_running_confirms_the_tab_gone(self):
        self.placed(tab_gone_error='server_not_running')
        self.tab_closed()
        self.assertEqual([{'attempt': 'ticket-1/1',
                           'evidence': ['herdr --session fixture-session tab get tab-1: server_not_running']}],
                         self.cli('reap')['closed'])

    def test_a_herdr_answer_that_is_not_a_definite_absence_closes_nothing(self):
        # Rule 1: only tab_not_found (pane_not_found without a tab) or a server that is not running is absence; any
        # other answer, an unreachable server, an unknown workspace or a surviving tab (another pane joined it)
        # leaves the attempt open, and the report says it stayed open.
        for fixture, answer in (({'tab_gone_error': 'server_unavailable'}, 'server_unavailable'),
                                ({'tab_gone_error': 'workspace_not_found'}, 'workspace_not_found'),
                                ({'tab_survives': True}, 'listed')):
            with self.subTest(fixture=fixture):
                self.setUp()
                self.placed(**fixture)
                self.tab_closed()
                self.assertEqual({'closed': [], 'refused': [], 'open': [{
                    'attempt': 'ticket-1/1', 'reason': f'herdr --session fixture-session tab get tab-1: {answer}'}]},
                    self.cli('reap'))
                self.assertIsNone(self.closed())

    def test_an_attempt_registered_without_a_tab_is_judged_by_its_pane(self):
        self.placed()
        db = sqlite3.connect(self.state)
        data = json.loads(db.execute("SELECT data FROM attempts WHERE id = 'ticket-1/1'").fetchone()[0])
        del data['herdr']['tab_id']
        db.execute("UPDATE attempts SET data = ? WHERE id = 'ticket-1/1'", (json.dumps(data),))
        db.commit()
        db.close()
        self.assertEqual([], self.cli('reap')['closed'])
        self.tab_closed()
        self.assertEqual([{'attempt': 'ticket-1/1',
                           'evidence': ['herdr --session fixture-session pane get pane-1: pane_not_found']}],
                         self.cli('reap')['closed'])

    def run_card(self, terminal):
        """A run card beside the state's `reports/`, ended as `terminal` says (empty: still running)."""
        self.state = self.root / 'ws' / 'reports' / 'workers.sqlite'
        card = self.root / 'ws' / 'autopilot.md'
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_text(f'# ws \u2014 autopilot run card\n\n## State\nconfirmed: yes\nterminal: {terminal}\nevidence:\n')
        return card

    def retained(self, attempt='ticket-1/1', assignment='ticket-1', session='native-1', worker='pane-1'):
        """A collected native subagent (no Herdr placement) kept for a next use."""
        self.register(attempt, assignment, session, worker)
        self.ready(attempt)
        self.collect(attempt)
        self.acknowledge()
        self.record('retain', attempt, reason='the repair round', next_use='fix round', evidence=['report'])

    def test_a_retained_native_attempt_is_closed_once_its_run_card_ended_delivered_or_merged(self):
        # Rule 2: retention is for a next use the ended run no longer has; the card is the evidence.
        for terminal in ('delivered', 'delivered-local', 'merged'):
            with self.subTest(terminal=terminal):
                self.setUp()
                card = self.run_card('')
                self.retained()
                self.assertEqual(NOTHING, self.cli('reap'))
                self.run_card(terminal)
                self.assertEqual({'closed': [{'attempt': 'ticket-1/1', 'evidence': [f'{card}: terminal: {terminal}']}],
                                  'open': [], 'refused': []}, self.cli('reap'))
                self.assertEqual([f'{card}: terminal: {terminal}'], self.closed()['evidence'])

    def test_a_run_that_stopped_or_has_no_card_leaves_a_retained_native_attempt_open(self):
        # Rule 2: only an ended run settles it; a run waiting on the user, paused or blocked will use it again.
        for terminal in ('waiting-on-user', 'paused-allowance', 'paused-by-user', 'blocked'):
            with self.subTest(terminal=terminal):
                self.setUp()
                self.run_card(terminal)
                self.retained()
                self.assertEqual(NOTHING, self.cli('reap'))
        self.setUp()
        self.retained()
        self.assertEqual(NOTHING, self.cli('reap'))

    def test_only_a_retained_attempt_with_no_herdr_placement_closes_on_its_ended_run(self):
        # Rule 2's boundaries: a collected attempt nobody retained, and a Herdr worker whose tab is still listed, stay
        # open when the run ended (the first awaits its own disposition, the second Herdr's answer).
        self.run_card('delivered')
        self.register('ticket-1/1', 'ticket-1', 'native-1', 'pane-1')
        self.ready()
        self.collect()
        self.acknowledge()
        self.assertEqual(NOTHING, self.cli('reap'))
        self.setUp()
        self.run_card('delivered')
        self.placed()
        self.record('retain', reason='the repair round', next_use='fix round', evidence=['report'])
        found = self.cli('reap')
        self.assertEqual([], found['closed'])
        self.assertEqual(['ticket-1/1'], [row['attempt'] for row in found['open']])

    def test_reap_for_a_host_session_closes_only_the_attempts_that_session_registered(self):
        # Sessions share one state: `--session` limits reap to the attempts that host session registered (its
        # exported session, as the yield gate attributes them), so one session never closes another's.
        card = self.run_card('delivered')
        for attempt, assignment, native, worker, session in (('ticket-1/1', 'ticket-1', 'native-1', 'pane-1', 'host-A'),
                                                           ('ticket-2/1', 'ticket-2', 'native-2', 'pane-2', 'host-B')):
            self.native_env = dict(os.environ, CLAUDE_CODE_SESSION_ID=session)
            self.retained(attempt, assignment, native, worker)
        self.assertEqual([{'attempt': 'ticket-2/1', 'evidence': [f'{card}: terminal: delivered']}],
                         self.cli('reap', '--session', 'host-B')['closed'])
        self.assertIsNone(self.closed('ticket-1/1'))
        self.assertEqual(['ticket-1/1'], [row['attempt'] for row in self.cli('reap')['closed']])

    def test_a_conversation_closes_through_its_latest_attempt(self):
        # Rule 1: a conversation's attempts share one pane, so the closure is recorded once, through the attempt
        # registered last; with that one not yet collected, nothing of the conversation closes.
        self.placed()
        self.record('retain', reason='the repair round', next_use='fix round', evidence=['report'])
        self.register('ticket-1/2', worker='builder-1', herdr={'session': 'fixture-session'})
        self.tab_closed()
        self.assertEqual(NOTHING, self.cli('reap'))
        self.ready('ticket-1/2')
        self.collect('ticket-1/2')
        self.acknowledge()
        found = self.cli('reap')
        self.assertEqual(['ticket-1/2'], [row['attempt'] for row in found['closed']])
        self.assertEqual('ticket-1/2', self.cli('status', '--attempt', 'ticket-1/1')['attempts'][0]['native_closed_by'])
        self.assertEqual(NOTHING, self.cli('reap'))

    def test_an_attempt_not_yet_collected_or_with_receipts_unacknowledged_is_left_alone(self):
        # Rule 1 closes only a collected result: recorded, its receipts acknowledged. Before that the host still has
        # the result to read, so a gone tab closes nothing and is no refusal either.
        self.herdr_fixture()
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        self.ready()
        self.tab_closed()
        self.assertEqual(NOTHING, self.cli('reap'))
        self.collect()
        self.assertEqual(NOTHING, self.cli('reap'))
        self.assertIsNone(self.closed())
        self.acknowledge()
        self.assertEqual(['ticket-1/1'], [row['attempt'] for row in self.cli('reap')['closed']])

    def test_an_attempt_whose_wait_is_still_running_is_left_alone(self):
        self.placed()
        self.tab_closed()
        db = sqlite3.connect(self.state)
        data = json.loads(db.execute("SELECT data FROM attempts WHERE id = 'ticket-1/1'").fetchone()[0])
        data['monitor'] = {'state': 'running', 'pid': os.getpid(), 'stamp': 'a wait that is still running'}
        db.execute("UPDATE attempts SET data = ? WHERE id = 'ticket-1/1'", (json.dumps(data),))
        db.commit()
        db.close()
        self.assertEqual(NOTHING, self.cli('reap'))
        self.assertIsNone(self.closed())

    def test_a_completed_attempt_of_a_job_needing_acceptance_is_closed_with_acceptance_deferred(self):
        # Rule 1: reap never accepts or declines the work; the closure defers acceptance with the reason, so
        # `status` still asks for `accepted` or `declined`, and a later one is recorded after the closure.
        self.placed()
        self.tab_closed()
        self.cli('reap')
        saved = self.cli('status', '--attempt', 'ticket-1/1')['attempts'][0]
        self.assertEqual({'reason': 'closed by reap: its Herdr tab is gone'}, saved['acceptance_deferred'])
        self.assertIn('record acceptance or decline', saved['attention'])
        self.setUp()
        self.run_card('merged')
        self.retained()
        self.cli('reap')
        self.assertEqual({'reason': 'closed by reap: its run card ended merged'},
                         self.cli('status')['attempts'][0]['acceptance_deferred'])

    def test_a_closure_the_lifecycle_refuses_is_reported_with_its_refusal_and_closes_nothing_else_wrongly(self):
        # Rule 3: a report that changed after collection cannot be closed over; reap names the refusal, exits 0 and
        # still closes the other attempts it may.
        card = self.run_card('delivered')
        self.retained('ticket-1/1', 'ticket-1', 'native-1', 'pane-1')
        self.retained('ticket-2/1', 'ticket-2', 'native-2', 'pane-2')
        (self.root / 'ticket-1-1.md').write_text('## Implemented\n\nrewritten after collection\n')
        self.assertEqual({
            'closed': [{'attempt': 'ticket-2/1', 'evidence': [f'{card}: terminal: delivered']}], 'open': [],
            'refused': [{'attempt': 'ticket-1/1', 'reason': 'Collect all conversation attempts, each completed one '
                         'with an intact report or declined, and reconcile their receipts and waits before '
                         'retirement'}]}, self.cli('reap'))
        self.assertIsNone(self.closed('ticket-1/1'))

    def test_a_worker_the_orchestrator_does_not_own_is_no_cleanup_target(self):
        # The lifecycle refuses to close a user's own worker; reap leaves it out instead of naming a refusal at
        # every stop.
        self.run_card('delivered')
        self.register('ticket-1/1', owned=False)
        self.ready()
        self.collect()
        self.acknowledge()
        self.record('retain', reason='the user keeps it', next_use='their own', evidence=['report'])
        self.assertEqual(NOTHING, self.cli('reap'))


if __name__ == '__main__':
    unittest.main()
