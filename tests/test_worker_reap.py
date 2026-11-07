#!/usr/bin/env python3
"""`worker_lifecycle.py reap` closes the attempts whose status is already decided: a collected worker whose Herdr tab
is confirmed gone or whose pane holds another conversation, and a retained native subagent whose run ended."""

import json
import os
from pathlib import Path
import shlex
import sqlite3
import sys
import time
import unittest

from lifecycle_case import SCRIPT, WorkerLifecycleCase


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

    def refusal(self, attempt, lacks):
        """What the lifecycle says of a conversation it cannot close: each unsettled attempt, what it lacks."""
        return f'Settle each attempt of the conversation before closing it: {attempt} lacks {lacks}'

    def quoted(self):
        return shlex.quote(str(self.state.resolve()))

    def declining(self, attempt):
        return (f'an intact report (record it declined: `python3 {SCRIPT} record --state {self.quoted()} --input '
                f'<JSON {{"kind": "declined", "attempt": "{attempt}", "reason", "evidence"}}>`)')

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

    def test_a_collected_attempt_whose_pane_now_holds_another_conversation_is_closed_with_the_answer_as_evidence(self):
        # Live (stforestkit, remediate-taut-534): the orchestrator started the next attempt's worker in the retained
        # worker's pane, so the tab lived on and the retained rows never closed. Herdr's answer for the pane decides:
        # another conversation there, by ID or by a record path other than the one the startup observed (an existing
        # record or one that names no file), closes it.
        for kind, other in (('id', 'native-2'), ('path', 'other-session.jsonl'), ('path', '/elsewhere/other.jsonl')):
            with self.subTest(kind=kind, other=other):
                self.setUp()
                self.placed()
                if other == 'other-session.jsonl':
                    other = str(self.root / other)
                    Path(other).write_text('{}\n')
                self.herdr_fixture(session_kind=kind, pane_session=other)
                evidence = [f'herdr --session fixture-session pane get pane-1: holds another conversation ({other})']
                self.assertEqual({'closed': [{'attempt': 'ticket-1/1', 'evidence': evidence}], 'open': [],
                                  'refused': []}, self.cli('reap'))
                self.assertEqual(evidence, self.closed()['evidence'])
                self.assertEqual({'reason': 'closed by reap: its Herdr pane holds another conversation'},
                                 self.cli('status', '--attempt', 'ticket-1/1')['attempts'][0]['acceptance_deferred'])

    def test_a_pane_holding_its_own_conversation_or_none_herdr_identifies_leaves_the_attempt_open(self):
        # The worker may still be there (its conversation by ID, or by the record path its startup observed), or Herdr
        # cannot say who is (no agent, an identity with no value, a kind it names otherwise, or a record path when the
        # startup observed none to compare): the tab's answer stays the reason it is open.
        for kind, held, observed in (('id', 'native-1', True), ('path', 'ticket-1-1.jsonl', True),
                                     ('path', 'alias/ticket-1-1.jsonl', True), ('path', 'linked.jsonl', True),
                                     ('path', 'missing/../ticket-1-1.jsonl', True),
                                     ('id', None, True), ('id', '', True), ('name', 'native-2', True),
                                     ('path', 'other-session.jsonl', False)):
            with self.subTest(kind=kind, held=held, observed=observed):
                self.setUp()
                self.placed()
                if not observed:
                    db = sqlite3.connect(self.state)
                    data = json.loads(db.execute("SELECT data FROM attempts WHERE id = 'ticket-1/1'").fetchone()[0])
                    del data['startup_evidence']
                    db.execute("UPDATE attempts SET data = ? WHERE id = 'ticket-1/1'", (json.dumps(data),))
                    db.commit()
                    db.close()
                # The record path is the fixture worker's own native record under this case's root, also spelled
                # through a link to that root, or another name of the same file (as a case alias is on a
                # case-insensitive file system), or through a directory that does not exist, as Herdr may spell it.
                (self.root / 'alias').symlink_to(self.root)
                (self.root / 'linked.jsonl').hardlink_to(self.root / 'ticket-1-1.jsonl')
                self.herdr_fixture(session_kind=kind, pane_session=str(self.root / held) if kind == 'path' else held)
                self.assertEqual({'closed': [], 'refused': [], 'open': [{
                    'attempt': 'ticket-1/1', 'reason': 'herdr --session fixture-session tab get tab-1: listed'}]},
                    self.cli('reap'))
                self.assertIsNone(self.closed())

    def test_the_server_the_callers_socket_names_is_asked_through_that_socket(self):
        # Live (OpenCode on an isolated XDG_CONFIG_HOME): `--session default` found no socket there and answered with a
        # non-JSON error, so nothing closed; the server HERDR_SOCKET_PATH names is reached through the socket itself.
        self.placed()
        self.tab_closed()
        self.native_env = dict(self.native_env or os.environ,
                               HERDR_SOCKET_PATH=str(self.root / 'herdr' / 'sessions' / 'fixture-session' / 'herdr.sock'))
        (self.root / 'herdr-argv.log').write_text('')
        self.assertEqual(['ticket-1/1'], [row['attempt'] for row in self.cli('reap')['closed']])
        self.assertEqual(['tab get tab-1'], (self.root / 'herdr-argv.log').read_text().splitlines())
        # A socket outside Herdr's layout names no server: the recorded one is asked by name.
        self.setUp()
        self.placed()
        self.tab_closed()
        self.native_env = dict(self.native_env or os.environ,
                               HERDR_SOCKET_PATH=str(self.root / 'sessions' / 'fixture-session' / 'herdr.sock'))
        (self.root / 'herdr-argv.log').write_text('')
        self.cli('reap')
        self.assertEqual(['--session fixture-session tab get tab-1'],
                         (self.root / 'herdr-argv.log').read_text().splitlines())

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

    def test_a_card_with_a_statement_heading_and_no_state_ends_nothing(self):
        # Rule: the State is the `## State` heading exactly; a `terminal: merged` under `## Statement` is no ended run.
        card = self.run_card('')
        card.write_text('# ws\n\n## Statement\nterminal: merged\nevidence: x\n')
        self.retained()
        self.assertEqual(NOTHING, self.cli('reap'))

    def test_a_run_card_whose_terminal_is_the_templates_placeholder_ends_nothing(self):
        # Rule: a `<placeholder>` value is empty, so the run has not ended.
        self.run_card('<state, once ended>')
        self.retained()
        self.assertEqual(NOTHING, self.cli('reap'))

    def test_a_closure_the_lifecycle_refuses_is_reported_with_its_refusal_and_closes_nothing_else_wrongly(self):
        # Rule 3: a report that changed after collection cannot be closed over; reap names the refusal, exits 0 and
        # still closes the other attempts it may.
        card = self.run_card('delivered')
        self.retained('ticket-1/1', 'ticket-1', 'native-1', 'pane-1')
        self.retained('ticket-2/1', 'ticket-2', 'native-2', 'pane-2')
        (self.root / 'ticket-1-1.md').write_text('## Implemented\n\nrewritten after collection\n')
        self.assertEqual({
            'closed': [{'attempt': 'ticket-2/1', 'evidence': [f'{card}: terminal: delivered']}], 'open': [],
            'refused': [{'attempt': 'ticket-1/1', 'reason': self.refusal('ticket-1/1', self.declining('ticket-1/1'))}]},
            self.cli('reap'))
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

    def test_a_reap_that_closes_nothing_writes_nothing(self):
        # A state's age decides whether the gate still attributes its unattributed rows, so looking at it must not
        # make an old state recent again.
        self.placed()
        stale = time.time() - 3 * 24 * 3600
        os.utime(self.state, (stale, stale))
        self.assertEqual([], self.cli('reap')['closed'])
        self.assertEqual(stale, self.state.stat().st_mtime)

    def test_an_attempt_whose_report_a_later_attempt_of_its_conversation_rewrote_owes_no_intact_report(self):
        # A conversation's attempts may all write one report path; each later attempt rewrites it, so the earlier
        # completed attempts can never match their digest again, and the later report is the conversation's.
        self.placed()
        self.record('retain', reason='the repair round', next_use='fix round', evidence=['report'])
        report = self.root / 'ticket-1-1.md'
        report.write_text('## Implemented\n\nThe repair round rewrote the report\n')
        self.register('ticket-1/2', worker='builder-1', herdr={'session': 'fixture-session'})
        self.ready('ticket-1/2')
        self.collect('ticket-1/2', report=report)
        self.acknowledge()
        self.tab_closed()
        self.assertEqual(['ticket-1/2'], [row['attempt'] for row in self.cli('reap')['closed']])

    def test_an_accepted_attempt_owes_no_intact_report_because_acceptance_consumed_it(self):
        self.placed()
        self.record('accepted', commit='abc123', evidence=['merged'])
        self.record('retain', reason='the repair round', next_use='fix round', evidence=['report'])
        (self.root / 'ticket-1-1.md').write_text('## Implemented\n\nRewritten after acceptance\n')
        self.register('ticket-1/2', worker='builder-1', herdr={'session': 'fixture-session'})
        self.ready('ticket-1/2')
        self.collect('ticket-1/2')
        self.acknowledge()
        self.tab_closed()
        self.assertEqual(['ticket-1/2'], [row['attempt'] for row in self.cli('reap')['closed']])

    def test_a_refusal_names_each_unsettled_attempt_with_what_it_lacks_and_the_command_that_settles_it(self):
        self.placed()
        self.record('retain', reason='the repair round', next_use='fix round', evidence=['report'])
        self.register('ticket-1/2', worker='builder-1', herdr={'session': 'fixture-session'})
        self.ready('ticket-1/2')
        self.collect('ticket-1/2')
        self.acknowledge()
        db = sqlite3.connect(self.state)
        data = json.loads(db.execute("SELECT data FROM attempts WHERE id = 'ticket-1/1'").fetchone()[0])
        result = data.pop('result')
        db.execute("UPDATE attempts SET data = ? WHERE id = 'ticket-1/1'", (json.dumps(data),))
        db.commit()
        request = self.root / 'closing.json'
        request.write_text(json.dumps({'kind': 'closed', 'attempt': 'ticket-1/2', 'evidence': ['tab gone'],
                                       'acceptance': {'decision': 'deferred', 'reason': 'later'}}))
        error = self.cli('record', '--input', str(request), code=2)
        self.assertIn(self.refusal('ticket-1/1', f'a collected result (`python3 {SCRIPT} collect --state {self.quoted()} '
                                                 '--attempt ticket-1/1 --input <request>`)'), error)
        data['result'] = result
        db.execute("UPDATE attempts SET data = ? WHERE id = 'ticket-1/1'", (json.dumps(data),))
        receipt = db.execute("INSERT INTO events (attempt, kind, payload, acknowledged) VALUES "
                             "('ticket-1/1', 'result', '{}', 0)").lastrowid
        db.commit()
        db.close()
        error = self.cli('record', '--input', str(request), code=2)
        self.assertIn(self.refusal('ticket-1/1', f'receipt {receipt} acknowledged (`python3 {SCRIPT} ack --state '
                                                 f'{self.quoted()} --event-id {receipt}`)'), error)

    def slow_herdr(self, delay):
        """A `herdr` that answers every tab of the `slow` server `tab_not_found`, `delay` seconds after it is asked."""
        directory = self.root / 'slow-bin'
        directory.mkdir()
        (directory / 'herdr').write_text('#!' + sys.executable + f'''
import json, sys, time
time.sleep({delay!r})
print(json.dumps({{'error': {{'code': 'tab_not_found'}}}}), file=sys.stderr)
sys.exit(1)
''')
        (directory / 'herdr').chmod(0o700)
        self.native_env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'])

    def listed_tabs(self, pane):
        """A `herdr` that lists every tab of the `slow` server at once and answers each `pane get` by running `pane`."""
        directory = self.root / 'listed-bin'
        directory.mkdir()
        (directory / 'herdr').write_text('#!' + sys.executable + f'''
import json, sys, time
if 'tab' in sys.argv:
    print(json.dumps({{'result': {{'tab': {{'pane_count': 1}}}}}}))
    sys.exit(0)
{pane}
''')
        (directory / 'herdr').chmod(0o700)
        self.native_env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'])

    def collected_on_slow_server(self, count):
        """`count` collected attempts, each in its own conversation, placed in tabs of the `slow` Herdr server."""
        db = sqlite3.connect(self.state)
        for number in range(1, count + 1):
            attempt = f'ticket-{number}/1'
            self.register(attempt, f'ticket-{number}', f'native-{number}', f'pane-{number}')
            self.ready(attempt)
            self.collect(attempt)
            self.acknowledge()
            data = json.loads(db.execute('SELECT data FROM attempts WHERE id = ?', (attempt,)).fetchone()[0])
            data['herdr'] = {'session': 'slow', 'workspace_id': 'w', 'tab_id': f'tab-{number}',
                             'pane_id': f'pane-{number}', 'name': f'builder-{number}'}
            db.execute('UPDATE attempts SET data = ? WHERE id = ?', (json.dumps(data), attempt))
            db.commit()
        db.close()

    def timed_reap(self, *args):
        began = time.monotonic()
        found = self.cli('reap', *args)
        return found, time.monotonic() - began

    def test_one_reap_stays_inside_a_total_budget_and_leaves_the_rest_for_the_next_stop(self):
        # A stop has 20 s: five lookups that each answer after 2.5 s would take 12.5 s, so the reap spends a few
        # seconds in all, closes what it confirmed, and the next stop takes up the rest.
        self.slow_herdr(2.5)
        self.collected_on_slow_server(5)
        found, seconds = self.timed_reap()
        self.assertLess(seconds, 8)
        self.assertEqual((5, True), (len(found['closed']) + len(found['open']), 0 < len(found['closed']) < 5))
        again, seconds = self.timed_reap()
        self.assertLess(seconds, 8)
        self.assertGreater(len(again['closed']), 0)

    def test_the_pane_lookups_of_listed_tabs_stay_inside_the_budget(self):
        # Review (Sonnet): each listed tab now also costs a pane lookup; one started late gets only the time left, so a
        # pane answering after 4.5 s cannot carry the reap far past its 5 s.
        self.listed_tabs("time.sleep(4.5); print(json.dumps({'result': {'pane': {}}}))")
        self.collected_on_slow_server(3)
        found, seconds = self.timed_reap()
        self.assertLess(seconds, 8)
        self.assertEqual(([], ['ticket-1/1', 'ticket-2/1', 'ticket-3/1']),
                         (found['closed'], [row['attempt'] for row in found['open']]))

    def test_a_pane_lookup_with_no_answer_leaves_the_attempt_open_with_that_answer_and_silences_its_server(self):
        # Review (Sonnet): the reason must name the pane lookup that failed, not the tab that was listed.
        self.listed_tabs("print('not json'); sys.exit(1)")
        self.collected_on_slow_server(2)
        self.assertEqual({'closed': [], 'refused': [], 'open': [
            {'attempt': 'ticket-1/1',
             'reason': 'herdr --session slow pane get pane-1: no answer (Expecting value: line 1 column 1 (char 0))'},
            {'attempt': 'ticket-2/1', 'reason': 'herdr --session slow: no answer earlier'}]}, self.cli('reap'))

    def test_a_closure_does_not_wait_on_a_locked_state_beyond_the_budget(self):
        self.placed()
        self.tab_closed()
        writer = sqlite3.connect(self.state, isolation_level=None)
        writer.execute('BEGIN IMMEDIATE')
        self.addCleanup(writer.close)
        found, seconds = self.timed_reap()
        self.assertLess(seconds, 8)
        self.assertEqual(([], ['ticket-1/1']), (found['closed'], [row['attempt'] for row in found['open']]))
        writer.execute('ROLLBACK')
        self.assertEqual(['ticket-1/1'], [row['attempt'] for row in self.cli('reap')['closed']])

    def test_a_closure_does_not_wait_on_a_locked_outcomes_ledger_beyond_the_budget(self):
        # The ledger is machine-wide and its writers wait up to 30 s; the closure is already in the state by then.
        self.placed()
        self.tab_closed()
        path = Path(os.environ['XDG_STATE_HOME']) / 'zloop' / 'outcomes.sqlite'
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = sqlite3.connect(path, isolation_level=None)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE IF NOT EXISTS outcomes (key TEXT PRIMARY KEY, data TEXT NOT NULL)')
        writer.execute('BEGIN IMMEDIATE')
        self.addCleanup(writer.close)
        found, seconds = self.timed_reap()
        self.assertLess(seconds, 8)
        self.assertEqual(['ticket-1/1'], [row['attempt'] for row in found['closed']])
        self.assertIn('closed', self.cli('status', '--attempt', 'ticket-1/1')['attempts'][0])


if __name__ == '__main__':
    unittest.main()
