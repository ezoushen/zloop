#!/usr/bin/env python3
"""Each Herdr worker lives in a tab of its own: registration asks Herdr where it is; retire confirms the tab closed."""

import fcntl
import json
import sys
import threading
import time
import unittest

from lifecycle_case import WorkerLifecycleCase
from native_records import session_record
import support


class HerdrTabs(WorkerLifecycleCase):
    def herdr_route(self, runtime='claude'):
        """Resolve the worker through the Herdr skill, as the resolver does for a Herdr route."""
        execution = json.loads(self.execution.read_text())
        execution.update(host='claude', target_runtime=runtime, executor='skill', executor_context='herdr',
                         executor_config={'skill': 'herdr', 'instruction': 'Run one worker.'},
                         native_dispatch=False, launch={'writes': 'worktree', 'verify': {'permission_mode': 'dontAsk'}})
        self.execution.write_text(json.dumps(execution))

    def collected(self):
        """Collect the registered worker's result and return a retirement request for its pane."""
        self.ready()
        self.collect()
        self.acknowledge()
        return self.retire_request(jobs='owned jobs stopped')

    def test_registrations_share_the_lock_and_retirement_excludes_them(self):
        self.cli('status')
        request = self.retire_request()
        with open(str(self.state) + '.lifecycle.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.register()
            error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
            self.assertIn('Worker registration or retirement is in progress; retry', error)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            error = self.register('ticket-2/1', assignment='ticket-2', session='native-2', worker='worker-2', code=2)
            self.assertIn('Worker retirement is in progress; retry', error)
            error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
            self.assertIn('Worker registration or retirement is in progress; retry', error)
        self.assertEqual([a['attempt'] for a in self.cli('status')['attempts']], ['ticket-1/1'])
        self.assertFalse((self.root / 'native-closed').exists())
        self.register('ticket-2/1', assignment='ticket-2', session='native-2', worker='worker-2')
        self.collected()
        self.herdr_fixture()
        self.retire()
        self.assertTrue(self.cli('status', '--attempt', 'ticket-1/1')['attempts'][0]['closed'])

    def test_registration_records_where_herdr_itself_places_the_worker(self):
        # Rule: register looks the worker up in Herdr and keeps Herdr's pane, tab, workspace and agent name.
        # The input names only the agent; the pane, tab and workspace can only have come from Herdr.
        self.herdr_route()
        self.herdr_fixture()
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        self.assertEqual(self.cli('status')['attempts'][0]['herdr'],
                         {'session': 'fixture-session', 'workspace_id': 'ws-1', 'tab_id': 'tab-1',
                          'pane_id': 'pane-1', 'name': 'builder-1'})

    def test_registration_waits_for_a_late_session_before_recording_the_worker(self):
        for runtime in ('claude', 'codex', 'pi', 'opencode'):
            with self.subTest(runtime=runtime):
                self.state = self.root / (runtime + '.sqlite')
                (self.root / 'agent-lookups').unlink(missing_ok=True)
                (self.root / 'probe-submitted').unlink(missing_ok=True)
                (self.root / 'herdr-argv.log').unlink(missing_ok=True)
                self.herdr_route(runtime)
                self.herdr_fixture(agent_kind=runtime, session_after=2)
                self.register(worker='builder-1', runtime=runtime, session=None, herdr={'session': 'fixture-session'})
                saved = self.cli('status')['attempts'][0]
                self.assertEqual((saved['native_session'], saved['herdr']['pane_id']), ('native-1', 'pane-1'))
                self.assertIn('agent prompt builder-1 Startup probe: reply with exactly READY and use no tools. '
                              '--wait --timeout 45000', (self.root / 'herdr-argv.log').read_text())

    def test_registration_names_the_session_step_when_no_identity_arrives(self):
        self.herdr_route()
        self.herdr_fixture(session_after=1000000)
        started = time.monotonic()
        error = self.register(worker='builder-1', herdr={'session': 'fixture-session'}, code=2)
        seconds = time.monotonic() - started
        self.assertIn('Herdr launch session step timed out for builder-1 after 45 s', error)
        self.assertIn('agent get builder-1', error)
        self.assertIn('then retry registration', error)
        self.assertGreaterEqual(seconds, 45)
        self.assertLess(seconds, 60)
        self.assertEqual(self.cli('status')['attempts'], [])

    def test_registration_waits_when_herdr_has_not_filled_its_session_id(self):
        self.herdr_route()
        for index, value in enumerate(('', '  ')):
            with self.subTest(value=value):
                self.state = self.root / f'blank-{index}.sqlite'
                (self.root / 'agent-lookups').unlink(missing_ok=True)
                self.herdr_fixture(session_after=2, pending_session={'kind': 'id', 'value': value})
                self.register(worker='builder-1', herdr={'session': 'fixture-session'})
                self.assertEqual(self.cli('status')['attempts'][0]['native_session'], 'native-1')

    def test_registration_refuses_a_probe_that_never_started(self):
        self.herdr_route()
        self.herdr_fixture(probe_error='agent_prompt_stalled')
        error = self.register(worker='builder-1', herdr={'session': 'fixture-session'}, code=2)
        self.assertIn('Herdr launch probe step failed for builder-1: agent_prompt_stalled', error)
        self.assertIn('agent read builder-1', error)
        self.assertEqual(self.cli('status')['attempts'], [])

    def test_registration_does_not_probe_an_already_working_agent(self):
        self.herdr_route()
        self.herdr_fixture(agent_status='working')
        error = self.register(worker='builder-1', herdr={'session': 'fixture-session'}, code=2)
        self.assertIn('Herdr launch probe step requires an idle or done worker', error)
        self.assertIn('agent read builder-1', error)
        self.assertNotIn('agent prompt', (self.root / 'herdr-argv.log').read_text())
        self.assertEqual(self.cli('status')['attempts'], [])

    def test_registration_refuses_a_probe_that_settles_at_a_blocked_ui(self):
        self.herdr_route()
        self.herdr_fixture(probe_status='blocked')
        error = self.register(worker='builder-1', herdr={'session': 'fixture-session'}, code=2)
        self.assertIn('Herdr launch probe step did not settle idle or done for builder-1: blocked', error)
        self.assertIn('agent read builder-1', error)
        self.assertEqual(self.cli('status')['attempts'], [])

    def test_folder_trust_names_the_worker_folder_and_user_step_without_starting_it(self):
        self.herdr_route()
        execution = json.loads(self.execution.read_text())
        execution.update(target_runtime='codex', settings={}, launch={'verify': {'sandbox_mode': 'workspace-write'}})
        self.execution.write_text(json.dumps(execution))
        self.herdr_fixture(agent_kind='codex', agent_status='blocked',
                           agent_screen='Folder access\n/fixture/untrusted repo\nTrust this folder?\n1. Yes\n2. No')
        error = self.register(worker='builder-1', runtime='codex',
                              herdr={'session': 'fixture-session'}, code=2)
        self.assertIn('Codex worker builder-1', error)
        self.assertIn('/fixture/untrusted repo', error)
        self.assertIn('Trust this folder?', error)
        self.assertIn('Ask the user to trust the folder in that pane', error)
        self.assertIn('never answer the dialog', error)
        self.assertEqual(self.cli('status')['attempts'], [])
        self.assertEqual((self.root / 'herdr-argv.log').read_text().splitlines(), [
            '--session fixture-session agent get builder-1',
            '--session fixture-session agent read pane-1 --source visible --lines 200'])

    def test_a_placed_worker_retires_only_through_its_confirmed_pane(self):
        # Rule: collect closes a worker on evidence alone only when it has no Herdr placement; a placed one still
        # needs its session and pane, and nothing closes without them.
        self.herdr_route()
        self.herdr_fixture()
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        self.ready()
        report = self.root / 'report.md'
        report.write_text('Completed assignment at head-1\n')
        request = self.root / 'collect.json'
        request.write_text(json.dumps({'status': 'completed', 'head': 'head-1', 'report': str(report),
            **self.completion_evidence(), 'evidence': ['current report checked'],
            'disposition': {'kind': 'retire', 'evidence': ['subagent finished'],
                            'acceptance': {'decision': 'deferred', 'reason': 'acceptance checks pending'}}}))
        error = self.cli('collect', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertIn('explicit Herdr session and pane', error)
        self.assertNotIn('closed', self.cli('status')['attempts'][0])
        self.assertFalse((self.root / 'native-closed').exists())

    def test_a_placed_worker_registers_with_its_session_record_path(self):
        # Rule: the record path becomes the session id it names before Herdr is asked, so Herdr's own session id
        # for the pane matches it.
        self.herdr_route()
        self.herdr_fixture(native_session='native-1')
        path = session_record(self.root / 'claude-record.jsonl', 'claude', 'native-1', self.root)
        self.register(worker='builder-1', herdr={'session': 'fixture-session'}, session=str(path))
        saved = self.cli('status')['attempts'][0]
        self.assertEqual((saved['native_session'], saved['herdr']['pane_id']), ('native-1', 'pane-1'))

    def test_registration_splits_a_session_pane_name_handle_and_asks_that_session(self):
        # Rule: a `session/pane/name` handle needs no herdr.session; its pane is looked up in its own session,
        # and Herdr must confirm the agent name it gives.
        self.herdr_route()
        self.herdr_fixture()
        error = self.register(worker='fixture-session/pane-1/builder-2', code=2)
        self.assertIn('name builder-2', error)
        self.assertIn('Herdr reports builder-1', error)
        self.register(worker='fixture-session/pane-1/builder-1')
        self.assertEqual(self.cli('status')['attempts'][0]['herdr'],
                         {'session': 'fixture-session', 'workspace_id': 'ws-1', 'tab_id': 'tab-1',
                          'pane_id': 'pane-1', 'name': 'builder-1'})

    def test_registration_refuses_a_runtime_that_herdr_contradicts(self):
        # Rule: the agent kind Herdr reports must be the registration's runtime; the refusal names both.
        self.herdr_route()
        self.herdr_fixture(agent_kind='codex')
        error = self.register(worker='builder-1', herdr={'session': 'fixture-session'}, code=2)
        self.assertIn('runtime claude', error)
        self.assertIn('Herdr reports codex', error)
        self.assertEqual(self.cli('status')['attempts'], [])

    def test_registration_refuses_a_native_session_that_herdr_contradicts(self):
        # Rule: a conversation ID Herdr reports as the agent's session must be the registration's native_session.
        self.herdr_route()
        self.herdr_fixture(native_session='native-9')
        error = self.register(worker='builder-1', herdr={'session': 'fixture-session'}, code=2)
        self.assertIn('native_session native-1', error)
        self.assertIn('Herdr reports native-9', error)
        self.assertEqual(self.cli('status')['attempts'], [])
        self.assertNotIn('agent prompt', (self.root / 'herdr-argv.log').read_text())

    def test_a_session_herdr_reports_as_a_path_is_not_compared_with_native_session(self):
        # Rule: a path-kind agent_session is not a conversation ID, so it is not compared (only both IDs are).
        self.herdr_route()
        self.herdr_fixture(native_session='/sessions/native.jsonl', session_kind='path')
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        self.assertEqual(self.cli('status')['attempts'][0]['native_session'], 'native-1')

    def test_a_handle_whose_session_differs_from_herdr_session_is_refused(self):
        # Rule: the parts of a `session/pane/name` handle are claims like any other; a herdr.session that differs
        # from the handle's is refused, naming both sessions.
        self.herdr_route()
        self.herdr_fixture()
        error = self.register(worker='fixture-session/pane-1/builder-1', herdr={'session': 'other-session'}, code=2)
        self.assertIn('session other-session', error)
        self.assertIn('names fixture-session', error)
        self.assertEqual(self.cli('status')['attempts'], [])

    def test_a_herdr_that_does_not_answer_holds_no_state_lock_and_is_refused_within_five_seconds(self):
        # Rule: Herdr is asked before the state's write lock is taken and waited on about 5 s per call, so a hung
        # Herdr blocks no other writer, and the registration still fails closed with a named reason.
        self.cli('status')
        # The write lock is probed inside the Herdr call itself, so it overlaps the wait by construction.
        self.herdr_fixture(agent_delay=30, lock_probe=str(self.state))
        started = time.monotonic()
        error = self.register(worker='builder-1', herdr={'session': 'fixture-session'}, code=2)
        seconds = time.monotonic() - started
        written = self.root / 'during-delay.json'
        self.assertTrue(written.exists(), 'the state lock was not probed while the registration waited on Herdr')
        writer = json.loads(written.read_text())
        self.assertEqual(writer['code'], 0, writer['output'])
        self.assertIn('Herdr did not answer agent get builder-1 within 5 s', error)
        self.assertLess(seconds, 20)  # bounded by the 5 s wait, not the 30 s Herdr delay
        self.assertEqual(self.cli('status')['attempts'], [])

    def test_a_tab_get_that_does_not_answer_is_refused_within_five_seconds(self):
        # Rule: every registration lookup waits about 5 s, the tab lookup too; then it fails closed, naming the call.
        self.herdr_route()
        self.herdr_fixture(tab_delay=30)
        started = time.monotonic()
        error = self.register(worker='builder-1', herdr={'session': 'fixture-session'}, code=2)
        self.assertLess(time.monotonic() - started, 20)  # bounded by the 5 s wait, not the 30 s Herdr delay
        self.assertIn('Herdr did not answer tab get tab-1 within 5 s', error)
        self.assertEqual(self.cli('status')['attempts'], [])

    def test_registration_keeps_the_execution_it_read_before_asking_herdr(self):
        # Rule: the resolved execution is read once, before Herdr is asked; a change to its file while Herdr
        # answers does not reach the attempt.
        self.herdr_fixture(agent_delay=2)
        started, outcome = time.monotonic(), {}

        def register():
            outcome['result'] = self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        waiting = threading.Thread(target=register)
        waiting.start()
        while not (self.root / 'herdr-waiting').exists() and time.monotonic() - started < 5:
            time.sleep(0.05)
        self.execution.write_text(json.dumps(dict(json.loads(self.execution.read_text()),
                                                  settings={'model': 'opus', 'effort': 'low'})))
        waiting.join(30)
        self.assertEqual(self.cli('status')['attempts'][0]['requested'],
                         {'model': 'sonnet', 'effort': 'high', 'runtime': 'claude'})

    def test_registration_refuses_a_tab_or_pane_that_herdr_contradicts(self):
        # Rule: a tab_id or pane_id the registration supplies must be Herdr's; the refusal names both values.
        self.herdr_route()
        self.herdr_fixture()
        for key, claimed, actual in (('tab_id', 'tab-9', 'tab-1'), ('pane_id', 'pane-9', 'pane-1')):
            with self.subTest(key=key):
                error = self.register(worker='builder-1', herdr={'session': 'fixture-session', key: claimed}, code=2)
                self.assertIn(f'{key} {claimed}', error)
                self.assertIn(f'Herdr reports {actual}', error)
                self.assertEqual(self.cli('status')['attempts'], [])
        self.register(worker='builder-1', herdr={'session': 'fixture-session', 'tab_id': 'tab-1', 'pane_id': 'pane-1'})

    def test_registration_fails_closed_when_herdr_cannot_confirm_a_herdr_worker(self):
        # Rule: a Herdr worker that Herdr cannot find, or whose Herdr session is not given, is refused;
        # a worker not launched through Herdr is never looked up. Herdr here knows only builder-1.
        self.herdr_fixture()
        self.register('native/1', 'native', 'native-0', worker='ghost')
        self.herdr_route()
        error = self.register(worker='phantom', herdr={'session': 'fixture-session'}, code=2)
        self.assertIn('Herdr cannot confirm worker phantom in session fixture-session: agent_not_found', error)
        error = self.register(worker='builder-1', code=2)
        self.assertIn('A Herdr worker registers with herdr.session', error)
        error = self.register(worker=None, herdr={'session': 'fixture-session'}, code=2)
        self.assertIn('Registration requires worker', error)
        self.assertEqual([row['attempt'] for row in self.cli('status')['attempts']], ['native/1'])

    def test_every_herdr_route_is_looked_up_whatever_names_its_skill(self):
        # Rule: a route is a Herdr route when the resolver's executor context is herdr, when it is the built-in
        # herdr type under any caller key, or when its skill is the Herdr skill, plain or under a plugin prefix;
        # such a worker never registers without asking Herdr.
        self.herdr_fixture()
        plain = json.loads(self.execution.read_text())
        for case, route in (('herdr context', {'executor_context': 'herdr', 'executor_config': {'skill': 'launch'}}),
                            ('built-in type', {'executor': 'herdr', 'executor_type': 'herdr',
                                               'executor_context': 'codex', 'executor_config': {}}),
                            ('prefixed skill', {'executor_config': {'skill': 'herdr:herdr'}})):
            with self.subTest(case=case):
                self.execution.write_text(json.dumps(dict(plain, **dict({'executor': 'skill'}, **route))))
                error = self.register(worker='builder-1', code=2)
                self.assertIn('A Herdr worker registers with herdr.session', error)
                self.assertEqual(self.cli('status')['attempts'], [])

    def test_a_registration_that_names_a_herdr_session_is_checked_whatever_its_route(self):
        # Rule: a recorded Herdr identity always comes from Herdr, so a claimed one is checked even when the
        # saved route does not go through Herdr.
        self.herdr_fixture()
        error = self.register(worker='builder-1', herdr={'session': 'fixture-session', 'tab_id': 'tab-9'}, code=2)
        self.assertIn('tab_id tab-9', error)
        self.assertIn('Herdr reports tab-1', error)
        self.assertEqual(self.cli('status')['attempts'], [])

    def test_registration_refuses_a_worker_that_shares_its_tab(self):
        # Rule: one worker per tab. A worker whose tab holds more than one pane is refused, naming the rule
        # and the recipe; a tab holding only the worker's pane registers (the first test).
        self.herdr_route()
        self.herdr_fixture(tab_panes=2)
        error = self.register(worker='builder-1', herdr={'session': 'fixture-session'}, code=2)
        self.assertIn('One worker per tab: tab-1 holds 2 panes', error)
        self.assertIn("start the worker in a tab of its own with the Herdr guide's start command, then register it",
                      error)
        self.assertEqual(self.cli('status')['attempts'], [])

    def test_registration_fails_closed_when_herdr_cannot_report_the_workers_tab(self):
        # Rule: one worker per tab is confirmed by Herdr; when its tab cannot be read, registration is refused.
        self.herdr_route()
        self.herdr_fixture(tab_panes=None)
        error = self.register(worker='builder-1', herdr={'session': 'fixture-session'}, code=2)
        self.assertIn('Herdr cannot confirm tab tab-1: server_unavailable', error)
        self.assertEqual(self.cli('status')['attempts'], [])

    def orchestrator_tab_refusal(self, caller):
        """Register builder-1, alone in tab-1, while Herdr places the orchestrator's pane `caller` in tab-1."""
        self.herdr_route()
        self.herdr_fixture(caller=caller, caller_tab='tab-1')
        error = self.register(worker='builder-1', herdr={'session': 'fixture-session'}, code=2)
        self.assertIn("One worker per tab: tab-1 is the orchestrator's own tab", error)
        self.assertIn("the Herdr guide's start command", error)
        self.assertEqual(self.cli('status')['attempts'], [])

    def test_registration_refuses_a_worker_in_the_orchestrators_own_tab(self):
        # Rule: one worker per tab, and never the orchestrator's: the tab Herdr reports now for the orchestrator's
        # pane (`pane current --current` in the worker's session). The refusal names the rule and the recipe.
        self.orchestrator_tab_refusal('caller-pane')

    def test_registration_refuses_a_worker_that_is_the_orchestrators_own_pane(self):
        # Rule: the same check refuses a worker whose pane is the orchestrator's own, alone in its tab, so the
        # one-pane rule cannot catch it.
        self.orchestrator_tab_refusal('pane-1')

    def test_a_stale_herdr_tab_id_or_a_orchestrator_in_another_session_refuses_nothing(self):
        # Rule: HERDR_TAB_ID can be stale, so it does not name the orchestrator's tab; an orchestrator Herdr does not
        # find in the worker's session (pane_not_found) skips the orchestrator check instead of refusing.
        self.herdr_route()
        self.herdr_fixture()
        self.native_env['HERDR_TAB_ID'] = 'tab-1'
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        self.assertEqual(self.cli('status')['attempts'][0]['herdr']['tab_id'], 'tab-1')

    def test_retirement_records_closure_only_once_the_workers_tab_is_gone(self):
        # Rule: after the pane is confirmed gone, retire asks Herdr for the worker's tab and records closure only
        # on tab_not_found; a tab that survives (another pane joined it) is reported with what to do, and closure
        # is not recorded.
        self.herdr_route()
        self.herdr_fixture()
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        request = self.collected()
        for answer, panes in (('tab get tab-1: listed', 1), ('tab get tab-1: server_unavailable', None)):
            with self.subTest(answer=answer):
                self.herdr_fixture(tab_survives=True, tab_panes=panes)
                error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
                self.assertIn('Native closure is not confirmed', error)
                self.assertIn(answer, error)
                self.assertIn('restore its server connection or reconcile the pane and tab, then retire again'
                              if panes is None else 'close the remaining panes or the tab, then retire again', error)
                self.assertNotIn('closed', self.cli('status')['attempts'][0])
        self.herdr_fixture()
        self.assertTrue(self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request))['closed'])
        self.assertIn('closed', self.cli('status')['attempts'][0])
        self.assertEqual((self.root / 'native-closed').read_text(), 'closed\n')

    def test_retirement_checks_the_tab_recorded_at_registration(self):
        # Rule: retire confirms the tab recorded at registration, even when Herdr now reports the pane in another.
        self.herdr_route()
        self.herdr_fixture()
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        request = self.collected()
        self.herdr_fixture(agent_tab='tab-2', tab_survives=True)
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertIn('Native closure is not confirmed (herdr --session fixture-session tab get tab-1: listed)', error)
        self.herdr_fixture(agent_tab='tab-2')
        self.assertTrue(self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request))['closed'])

    def test_an_attempt_with_no_recorded_tab_retires_on_the_pane_rule_alone(self):
        # Rule: an attempt registered without a Herdr identity (before tabs were recorded) records closure on
        # pane_not_found alone, even though Herdr would say the pane's tab survives.
        self.register()
        request = self.collected()
        self.herdr_fixture(tab_survives=True)
        self.assertTrue(self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request))['closed'])
        self.assertIn('closed', self.cli('status')['attempts'][0])

    def test_a_resumed_retirement_with_no_recorded_placement_is_recorded_by_hand(self):
        self.register()
        request = self.collected()
        self.herdr_fixture(verify_error='server_unavailable', tab_survives=True)
        self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.herdr_fixture(tab_survives=True)
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertIn("is not this attempt's recorded Herdr pane", error)
        self.assertIn('closure with `record`', error)


class TabRecipe(unittest.TestCase):
    def test_folder_trust_is_reported_before_a_launch_counts_as_started(self):
        guide = ' '.join((support.ROOT / 'hosts/herdr.md').read_text().split())
        for instruction in ('`agent_not_ready` or its first wait reports `blocked`',
                            'agent read <worker> --source visible --lines 200',
                            '`Trust this folder?`', 'tell the user the folder shown and the worker',
                            'ask them to trust it in that pane', 'never answer the dialog',
                            'Do not count it as started'):
            self.assertIn(instruction, guide)

    def test_the_herdr_guide_starts_each_worker_in_a_tab_of_its_own_then_registers_it(self):
        guide = ' '.join((support.ROOT / 'hosts/herdr.md').read_text().split())
        position = 0
        for step in ('never as a split of your pane',
                     'python3 <plugin_root>/hooks/herdr_launch.py start --route <saved route.json> '
                     '--worktree <worktree> --assignment <slug>/<assignment> --role <role> '
                     '--name <name>',
                     'register it', 'Retirement closes the pane, which closes the tab'):
            position = guide.find(step, position)
            self.assertNotEqual(position, -1, f'{step!r} in order in hosts/herdr.md')
        self.assertIn('herdr session list', guide)
        self.assertIn("how zloop's stop check tells a worker from its orchestrator", guide)
        self.assertNotIn('herdr agent start', guide)
        self.assertIn('launch.argv', guide)
        shared = ' '.join((support.ROOT / 'runtime/workers/prepare.md').read_text().split())
        self.assertIn('"herdr": {"session"', shared)
        self.assertIn('herdr session list', shared)
        self.assertNotIn('herdr tab create', shared)
        for text in (guide, shared):
            self.assertNotIn('pane split', text)

    def test_the_herdr_guide_submits_until_working_and_reconciles_before_the_completion_wait(self):
        # Rule: submit returns once the worker is working, and the completion wait is armed only after its
        # receipt is reconciled, since watch refuses a monitor that awaits reconciliation.
        guide = ' '.join((support.ROOT / 'hosts/herdr.md').read_text().split())
        position = 0
        for step in ('--wait --until working --until blocked --timeout 45000', 'record `reconciled`',
                     'then arm `watch` with `herdr --session <session> agent wait <worker>'):
            position = guide.find(step, position)
            self.assertNotEqual(position, -1, f'{step!r} in order in hosts/herdr.md')


if __name__ == '__main__':
    unittest.main()
