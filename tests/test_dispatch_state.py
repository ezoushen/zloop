#!/usr/bin/env python3
"""Managed dispatch checks routing provenance, known work and justified serialization."""
import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import support


class DispatchFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.card = support.run_card(self.repo / '.scratch/run/autopilot.md', phase='dispatch')
        self.control = support.load_script('run_control.py')
        self.control.adopt(self.card, 'approved independent tasks; configured provider pool', allow_resume=True)
        self.dispatch = support.load_script('dispatch_state.py')
        self.git('init', '-q')
        with (self.repo / '.git/info/exclude').open('a') as excludes:
            excludes.write('\n.scratch/\n.worktrees/\n')
        self.git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base', '--allow-empty')
        self.items = [self.item('a'), self.item('b')]
        self.proof = self.repo / 'capacity.txt'
        self.proof.write_text('native status reconciled: two free account slots; no other reservations\n')
        self.request = dict(assignments=self.items, launch=['a/1', 'b/1'], deferred={},
                            capacity=dict(limit=2, evidence=[str(self.proof)]),
                            decomposition='two approved independent file changes', integration_owner='host-1')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def item(self, name):
        worktree = self.repo / '.worktrees' / name
        self.git('worktree', 'add', '-q', '--detach', str(worktree), 'HEAD')
        packet = self.repo / f'{name}.txt'
        route = self.repo / f'{name}.json'
        complete = support.assignment_file(worktree, 'ticket', attempt=f'{name}/1').read_text()
        packet.write_text(f'assignment: {name}  attempt: {name}/1\ndispatch_card: {self.card}\n'
                          f'execution: {route}\n{complete}\nchange {name}.txt\n')
        execution = dict(host='pi', agent='builder', job='ticket', native_dispatch=True,
                         executor='native', settings={}, model_mode='inherit', route_source='native-default')
        routed = self.dispatch.issue_route(self.card, self.repo, f'{name}/1', packet, worktree, execution, [])
        route.write_text(json.dumps(routed))
        return dict(assignment=name, attempt=f'{name}/1', job='ticket', packet=str(packet),
                    execution=str(route), worktree=str(worktree), depends_on=[], touches=[name])

    def plan(self, request=None):
        return self.dispatch.plan(self.card, request or self.request,
                                  expected_revision=self.control.read(self.card)['revision'])

    def check(self, name='a', **kwargs):
        item = next(row for row in self.items if row['assignment'] == name)
        return self.dispatch.check(self.card, f'{name}/1', json.loads(Path(item['execution']).read_text()),
                                   item['packet'], item['worktree'], **kwargs)


class ManagedDispatch(DispatchFixture, unittest.TestCase):

    def test_inspection_reports_current_gaps_without_changing_admission(self):
        self.plan()
        before = {str(p): p.read_bytes() for p in self.card.parent.rglob('*') if p.is_file()}
        status = self.dispatch.inspect(self.card)
        self.assertEqual(status['revision'], self.control.read(self.card)['revision'])
        self.assertEqual(status['known_unfinished'], ['ticket:a', 'ticket:b'])
        self.assertEqual(status['batch']['launch'], ['a/1', 'b/1'])
        self.assertEqual(len(status['gaps']), 2)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.card.parent.rglob('*') if p.is_file()})
        execution = Path(self.items[0]['execution'])
        routed = json.loads(execution.read_text())
        execution.write_text(json.dumps(dict(routed, settings=dict(model='substituted'))))
        self.assertTrue(any('Resolved route was changed' in gap for gap in self.dispatch.inspect(self.card)['gaps']))
        execution.write_text(json.dumps(routed))
        self.proof.write_text('capacity changed\n')
        self.assertIn('Dispatch state changed', self.dispatch.inspect(self.card)['gaps'][0])
        with self.assertRaisesRegex(ValueError, 'Dispatch state changed'):
            self.check()

    def test_inspection_exposes_unplanned_board_work_without_creating_lifecycle_state(self):
        issues = self.card.parent / 'issues'
        issues.mkdir()
        (issues / '03-missing.md').write_text('---\nticket: missing\nstatus: todo\nblocked_by: []\ntouches: [api]\n---\n')
        status = self.dispatch.inspect(self.card)
        self.assertIn('ticket:missing', status['known_unfinished'])
        self.assertIn('checked dispatch batch', status['gaps'][0])
        self.assertEqual(status['input_template']['capacity']['limit'], None)
        self.assertEqual(status['input_template']['assignments'], [])
        self.assertFalse((self.card.parent / 'reports').exists())
        self.control.transition(self.card, 'blocked', dict(kind='host', reference='capacity unavailable'),
                                'temporary failure', 1, 'block-inspection')
        self.assertEqual(self.dispatch.inspect(self.card)['state']['terminal'], 'blocked')

    def test_incomplete_selection_and_handwritten_inherit_cannot_be_admitted(self):
        item = self.items[0]
        unresolved = dict(status='selection_required', eligible_candidates=['sol', 'sonnet'])
        with self.assertRaisesRegex(ValueError, 'selection_required'):
            self.dispatch.issue_route(self.card, self.repo, 'a/1', Path(item['packet']),
                                      Path(item['worktree']), unresolved, [])
        self.plan()
        forged = json.loads(Path(item['execution']).read_text())
        forged.pop('dispatch')
        with self.assertRaisesRegex(ValueError, 'route receipt'):
            self.dispatch.check(self.card, 'a/1', forged, item['packet'], item['worktree'])

    def test_two_independent_tasks_must_both_be_chosen(self):
        self.request['launch'] = ['a/1']
        with self.assertRaisesRegex(ValueError, 'unexplained'):
            self.plan()
        self.request['deferred'] = {'b/1': dict(kind='capacity')}
        with self.assertRaisesRegex(ValueError, 'capacity'):
            self.plan()

    def test_capacity_one_and_actual_dependency_can_justify_serial_work(self):
        self.request['capacity']['limit'] = 1
        self.request['launch'] = ['a/1']
        self.request['deferred'] = {'b/1': dict(kind='capacity')}
        self.plan()
        self.check()
        self.request['capacity']['limit'] = 2
        self.items[1]['depends_on'] = ['ticket:a']
        self.request['deferred']['b/1'] = dict(kind='dependency', because=['ticket:a'])
        self.plan()
        self.check()

    def test_conflict_requires_a_real_shared_write_area(self):
        self.request['launch'] = ['a/1']
        self.request['deferred'] = {'b/1': dict(kind='conflict', because=['ticket:a'])}
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.plan()
        self.items[1]['touches'] = ['a']
        self.plan()

    def test_concurrent_writers_cannot_share_a_checkout(self):
        self.items[1]['worktree'] = self.items[0]['worktree']
        item = self.items[1]
        execution = json.loads(Path(item['execution']).read_text())
        routed = self.dispatch.issue_route(self.card, self.repo, item['attempt'], Path(item['packet']),
                                          Path(item['worktree']), execution, [])
        Path(item['execution']).write_text(json.dumps(routed))
        with self.assertRaisesRegex(ValueError, 'worktree|route'):
            self.plan()

    def test_route_is_bound_to_settings_packet_worktree_and_configuration(self):
        self.plan()
        item = self.items[0]
        original = Path(item['execution']).read_text()
        edited = json.loads(original)
        edited['settings'] = {'model': 'unconfigured'}
        Path(item['execution']).write_text(json.dumps(edited))
        with self.assertRaisesRegex(ValueError, 'route'):
            self.check()
        Path(item['execution']).write_text(original)
        Path(item['packet']).write_text('different assignment')
        with self.assertRaisesRegex(ValueError, 'packet|route'):
            self.check()

    def test_configuration_changes_require_routing_again(self):
        config = self.repo / 'executors.json'
        config.write_text('{}')
        item = self.items[0]
        execution = json.loads(Path(item['execution']).read_text())
        execution.pop('dispatch')
        routed = self.dispatch.issue_route(self.card, self.repo, 'a/1', Path(item['packet']),
                                          Path(item['worktree']), execution, [config])
        Path(item['execution']).write_text(json.dumps(routed))
        self.plan()
        config.write_text('{"models":{}}')
        with self.assertRaisesRegex(ValueError, 'configuration'):
            self.check()

    def test_changed_route_requires_a_new_batch_capacity_decision(self):
        self.plan()
        item = self.items[0]
        execution = json.loads(Path(item['execution']).read_text())
        execution['settings'] = {'model': 'another-allowed-route'}
        routed = self.dispatch.issue_route(self.card, self.repo, 'a/1', Path(item['packet']),
                                          Path(item['worktree']), execution, [])
        Path(item['execution']).write_text(json.dumps(routed))
        with self.assertRaisesRegex(ValueError, 're-evaluate'):
            self.check()

    def test_yield_names_every_unlaunched_selection_and_duplicate_launch_is_blocked(self):
        self.plan()
        self.assertIn('b/1', ' '.join(self.dispatch.gaps(self.card)))
        self.check(call_id='call-a')
        self.check(call_id='call-a')  # A retried hook for the same invocation is idempotent.
        with self.assertRaisesRegex(ValueError, 'already'):
            self.check(call_id='second-child')

    def test_known_lifecycle_plan_cannot_be_omitted(self):
        import sqlite3
        state = self.card.parent / 'reports/workers.sqlite'
        state.parent.mkdir(exist_ok=True)
        with closing(sqlite3.connect(state)) as db, db:
            db.execute('CREATE TABLE plans (job TEXT, assignments TEXT)')
            db.execute('CREATE TABLE attempts (id TEXT, data TEXT)')
            db.execute('INSERT INTO plans VALUES (?, ?)', ('ticket', '["a", "b", "forgotten"]'))
        with self.assertRaisesRegex(ValueError, 'forgotten'):
            self.plan()

    def test_board_prerequisite_cannot_be_hidden_by_an_empty_dependency_list(self):
        issues = self.card.parent / 'issues'
        issues.mkdir()
        for number, item in enumerate(self.items, 1):
            ticket = f'run/{number:02}'
            item['ticket'] = ticket
            (issues / f'{number:02}.md').write_text(f'---\nticket: {ticket}\nstatus: ready\n'
                f'blocked_by: [{"run/01" if number == 2 else ""}]\ntouches: [{item["assignment"]}]\n---\n')
            packet = Path(item['packet'])
            packet.write_text(packet.read_text().replace('ticket: fx/01', f'ticket: {ticket}'))
            execution = json.loads(Path(item['execution']).read_text())
            routed = self.dispatch.issue_route(self.card, self.repo, item['attempt'], packet,
                Path(item['worktree']), execution, [])
            Path(item['execution']).write_text(json.dumps(routed))
        with self.assertRaisesRegex(ValueError, 'dependency'):
            self.plan()

    def test_completion_and_resume_invalidate_batch_but_preserve_policy(self):
        import sqlite3
        self.plan()
        policy = copy.deepcopy(self.control.read(self.card)['dispatch_policy'])
        state = self.card.parent / 'reports/workers.sqlite'
        state.parent.mkdir(exist_ok=True)
        with closing(sqlite3.connect(state)) as db, db:
            db.execute('CREATE TABLE plans (job TEXT, assignments TEXT)')
            db.execute('CREATE TABLE attempts (id TEXT, data TEXT)')
            db.execute('INSERT INTO attempts VALUES (?, ?)', ('a/1', json.dumps(dict(
                attempt='a/1', assignment='a', job='ticket', result=dict(status='completed')))))
        with self.assertRaisesRegex(ValueError, 're-evaluate'):
            self.check('b')
        self.assertIn('re-evaluate', ' '.join(self.dispatch.gaps(self.card)))
        paused = self.control.transition(self.card, 'blocked', dict(kind='host', reference='capacity check'),
                                         'temporary failure', 1, 'block')
        import datetime
        native = self.repo / 'resume.jsonl'
        native.write_text(json.dumps(dict(type='session_meta', payload=dict(id='host-1'))) + '\n' +
            json.dumps(dict(type='response_item', timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            payload=dict(role='user', content=[dict(type='input_text', text='autopilot --take-over')]))) + '\n')
        user = self.control.user_source(native, 'codex', ['host-1'], 'resume', 0)
        self.control.transition(self.card, '', user,
                                'reconciled', paused['revision'], 'resume')
        self.assertEqual(self.control.read(self.card)['dispatch_policy'], policy)
        self.assertIn('re-evaluate', ' '.join(self.dispatch.gaps(self.card)))

    def test_legacy_card_keeps_existing_behavior(self):
        other = support.run_card(self.repo / '.scratch/legacy/autopilot.md', phase='dispatch')
        self.assertIsNone(self.dispatch.check(other, 'legacy/1', {}, '', ''))


class NativeAdmission(DispatchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.hook = support.load_script('../hooks/managed_launch.py')

    def event(self, names=('a', 'b'), call='launch-batch'):
        tasks = [dict(agent='worker', task=Path(item['packet']).read_text(), cwd=item['worktree'], worktree=False)
                 for item in self.items if item['assignment'] in names]
        return dict(tool_name='subagent', tool_input=dict(tasks=tasks), session_id='host-1',
                    cwd=str(self.repo), tool_call_id=call)

    def lifecycle(self, action, data=None, *args):
        state = self.card.parent / 'reports/workers.sqlite'
        command = [sys.executable, str(support.script('worker_lifecycle.py')), action, '--state', str(state), *args]
        if data is not None:
            request = self.card.parent / 'request.json'
            request.write_text(json.dumps(data))
            command += ['--input', str(request)]
        return subprocess.run(command, capture_output=True, text=True,
                              env=dict(support.without_host_sessions(), CODEX_SESSION_ID=getattr(self, 'registering_host', 'host-1'),
                                       XDG_STATE_HOME=str(self.repo / '.scratch/state')))

    def registration(self, item):
        name = item['assignment']
        return dict(kind='register', attempt=item['attempt'], assignment=name, repo=str(self.repo),
                    worktree=item['worktree'], head='head-1', execution=item['execution'], runtime='pi',
                    worker=f'worker-{name}', native_session=f'native-{name}', owned=True, packet=item['packet'])

    def test_managed_registration_with_no_launch_claim_records_a_gap(self):
        self.plan()
        result = self.lifecycle('record', self.registration(self.items[0]))
        self.assertEqual(result.returncode, 0, result.stderr)
        status = self.lifecycle('status', None, '--attempt', 'a/1')
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn('unclaimed-launch', json.loads(status.stdout)['attempts'][0]['attention'])
        with closing(sqlite3.connect(self.card.parent / 'reports/workers.sqlite')) as db:
            self.assertEqual(db.execute("SELECT attempt FROM events WHERE kind = 'unclaimed-launch'").fetchall(),
                             [('a/1',)])

    def completion(self, item):
        attempt = item['attempt']
        registration = self.registration(item)
        native = support.session_record(self.card.parent / 'native.jsonl', 'pi', registration['native_session'],
                                        item['worktree'], model='fixture/model')
        support.user_message(native, 'pi', Path(item['packet']).read_text())
        support.assistant_turn(native, 'pi')
        startup = self.lifecycle('record', dict(kind='startup', attempt=attempt, native=str(native)))
        self.assertEqual(startup.returncode, 0, startup.stderr)
        self.assertTrue(json.loads(startup.stdout)['verified'])
        report = self.card.parent / 'reports/01.md'
        base = self.git('rev-parse', 'HEAD')
        worktree = item['worktree']
        subprocess.run(['git', '-C', worktree, '-c', 'user.name=t', '-c', 'user.email=t@t',
                        'commit', '-qm', 'work', '--allow-empty'], check=True, capture_output=True)
        head = subprocess.check_output(['git', '-C', worktree, 'rev-parse', 'HEAD'], text=True).strip()
        self.git('branch', 'run/build', head)
        report.write_text(f'base: {base}\nbranch: run/build\n\n## Implemented\nCommit: {head}\n- [x] fixture\n')
        return dict(status='completed', head=head, report=str(report), native=str(native), evidence=['fixture reply'],
                    disposition=dict(kind='retain', reason='review', next_use='review', evidence=['fixture worker']))

    def test_acceptance_and_stop_require_an_audited_exception_for_unclaimed_work(self):
        self.registering_host = 'lifecycle-host'
        self.plan()
        item = self.items[0]
        registration = self.lifecycle('record', self.registration(item))
        self.assertEqual(registration.returncode, 0, registration.stderr)
        request = self.completion(item)
        collected = self.lifecycle('collect', request, '--attempt', 'a/1')
        self.assertEqual(collected.returncode, 0, collected.stderr)
        gate = self.lifecycle('gate', None, '--stage', 'accept', '--attempt', 'a/1', '--revision', request['head'])
        self.assertEqual((gate.returncode, json.loads(gate.stdout)['missing']), (1, ['unclaimed-launch']))
        acceptance = dict(kind='accepted', attempt='a/1', commit=request['head'], evidence=['fixture review'])
        refused = self.lifecycle('record', acceptance)
        self.assertIn('--accept-unclaimed', refused.stderr)
        closure = dict(kind='closed', attempt='a/1', evidence=['fixture child ended'], acceptance=dict(
            decision='accepted', commit=request['head'], evidence=['fixture review']))
        refused = self.lifecycle('record', closure)
        self.assertIn('--accept-unclaimed', refused.stderr)
        stop = [sys.executable, str(support.HOOKS / 'yield_gate.py'), 'stop', str(self.repo), self.registering_host]
        for _ in range(4):
            result = json.loads(subprocess.check_output(stop, text=True))
            self.assertTrue(result['hold'])
            self.assertTrue(any('a/1: unclaimed-launch' in gap for gap in result['missing']))
        reason = 'Host launch hook failed to fire. The retained packet and child record were checked.'
        accepted = self.lifecycle('record', acceptance, '--accept-unclaimed', reason)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        status = json.loads(self.lifecycle('status', None, '--attempt', 'a/1').stdout)
        self.assertEqual(status['attempts'][0]['accepted']['accept_unclaimed'], reason)
        gate = self.lifecycle('gate', None, '--stage', 'accept', '--attempt', 'a/1', '--revision', request['head'])
        self.assertEqual((gate.returncode, json.loads(gate.stdout)['missing']), (0, []))
        result = json.loads(subprocess.check_output(stop, text=True))
        self.assertFalse(any('a/1: unclaimed-launch' in gap for gap in result['missing']))

    def test_collection_refuses_main_checkout_drift_and_can_retry_after_recovery(self):
        self.plan()
        item = self.items[0]
        self.hook.check_event(self.event())
        registration = self.lifecycle('record', self.registration(item))
        self.assertEqual(registration.returncode, 0, registration.stderr)
        request = self.completion(item)
        drift = self.repo / 'unexpected.txt'
        drift.write_text('unexpected main checkout write')
        refused = self.lifecycle('collect', request, '--attempt', 'a/1')
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn('Main checkout Git status changed since registration', refused.stderr)
        status = json.loads(self.lifecycle('status', None, '--attempt', 'a/1').stdout)
        self.assertNotIn('result', status['attempts'][0])
        drift.rename(self.card.parent / 'unexpected.txt')
        collected = self.lifecycle('collect', request, '--attempt', 'a/1')
        self.assertEqual(collected.returncode, 0, collected.stderr)
        replay = self.lifecycle('collect', request, '--attempt', 'a/1')
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertTrue(json.loads(replay.stdout)['result']['already_collected'])

    def test_board_acceptance_records_the_reason_for_the_returned_attempt(self):
        ticket = support.ticket_file(self.repo, 'run/01')
        item = self.items[0]
        execution = Path(item['execution'])
        packet = Path(item['packet'])
        packet.write_text(packet.read_text().replace('ticket: fx/01', 'ticket: run/01'))
        routed = self.dispatch.issue_route(self.card, self.repo, item['attempt'], packet, Path(item['worktree']),
                                          json.loads(execution.read_text()), [])
        execution.write_text(json.dumps(routed))
        self.plan()
        registration = self.lifecycle('record', self.registration(item))
        self.assertEqual(registration.returncode, 0, registration.stderr)
        request = self.completion(item)
        collected = self.lifecycle('collect', request, '--attempt', 'a/1')
        self.assertEqual(collected.returncode, 0, collected.stderr)
        board = [sys.executable, str(support.script('next_tickets.py')), '--repo', str(self.repo), '--accept', 'run/01']
        refused = subprocess.run(board, capture_output=True, text=True)
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn('a/1: unclaimed-launch', refused.stderr)
        self.assertNotIn('accepted:', ticket.read_text())
        reason = 'Checked the native child and the packet after recovering the host.'
        accepted = subprocess.run([*board, '--accept-unclaimed', reason], capture_output=True, text=True)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertIn(f"accepted: {request['head']}", ticket.read_text())
        replay = subprocess.run(board, capture_output=True, text=True)
        self.assertEqual(replay.returncode, 0, replay.stdout + replay.stderr)
        with closing(sqlite3.connect(self.card.parent / 'reports/workers.sqlite')) as db:
            audits = db.execute("SELECT attempt, payload FROM events WHERE kind = 'accept-unclaimed'").fetchall()
        self.assertEqual([(attempt, json.loads(payload)['reason']) for attempt, payload in audits], [('a/1', reason)])

    def test_rerouting_a_different_packet_cannot_reuse_a_launch_claim(self):
        self.plan()
        self.hook.check_event(self.event())
        a, b = self.items
        packet, execution = Path(a['packet']), Path(a['execution'])
        packet.write_text(packet.read_text() + '\nAdditional work after launch.\n')
        routed = self.dispatch.issue_route(self.card, self.repo, a['attempt'], packet, Path(a['worktree']),
                                          json.loads(execution.read_text()), [])
        execution.write_text(json.dumps(routed))
        self.plan()
        with self.assertRaisesRegex(ValueError, 'Attempt already launched'):
            self.hook.check_event(self.event(names=('a',)))
        self.hook.check_event(self.event(names=('b',)))
        for item in (a, b):
            registered = self.lifecycle('record', self.registration(item))
            self.assertEqual(registered.returncode, 0, registered.stderr)
        raw = self.lifecycle('status')
        self.assertEqual(raw.returncode, 0, raw.stderr)
        first, second = json.loads(raw.stdout)['attempts']
        self.assertIn('unclaimed-launch', first['attention'])
        self.assertNotIn('unclaimed-launch', second['attention'])
        self.assertEqual(second['launch_claim'], dict(assignment='b', call_id='launch-batch',
                         packet_hash={str(Path(b['packet']).resolve()): hashlib.sha256(Path(b['packet']).read_bytes()).hexdigest()}))

    def test_a_claim_cannot_move_to_another_assignment_with_the_same_packet(self):
        self.plan()
        self.hook.check_event(self.event())
        a = self.items[0]
        a['assignment'] = 'renamed-a'
        c = self.item('c')
        c['assignment'] = 'a'
        self.items.append(c)
        self.request['deferred'] = {'c/1': dict(kind='capacity')}
        self.plan()
        registered = self.lifecycle('record', self.registration(a))
        self.assertEqual(registered.returncode, 0, registered.stderr)
        status = self.lifecycle('status', None, '--attempt', 'a/1')
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn('unclaimed-launch', json.loads(status.stdout)['attempts'][0]['attention'])

    def test_each_native_launch_tool_reaches_the_admission_hook(self):
        tools = {
            'Agent': {'prompt': 'missing packet'},
            'Task': {'prompt': 'missing packet'},
            'spawn_agent': {'message': 'missing packet'},
            'collaboration.spawn_agent': {'message': 'missing packet'},
            'mcp__collaboration__spawn_agent': {'message': 'missing packet'},
            'followup_task': {'message': 'missing packet'},
            'collaboration.followup_task': {'message': 'missing packet'},
            'mcp__t3_code__delegate_task': {'task': 'missing packet'},
            'mcp__t3_code_direct__delegate_task': {'task': 'missing packet'},
            'subagent': {'task': 'missing packet'},
        }
        for manifest in (support.HOOKS / 'hooks.json', support.ROOT / 'hosts/codex/hooks.json'):
            hooks = json.loads(manifest.read_text())['hooks']['PreToolUse']
            matcher, = [entry['matcher'] for entry in hooks if any('managed_launch.py' in hook['command']
                                                                  for hook in entry['hooks'])]
            expected = set(tools) if manifest.parent == support.HOOKS else set(tools) - {'Agent', 'Task'}
            names = {*tools, 'AgentStatus', 'respawn_agent', 'mcp__t3_code__task_status'}
            self.assertEqual({name for name in names if re.search(matcher, name)}, expected, manifest)
        pi = (support.ROOT / 'hosts/pi/bootstrap.ts').read_text()
        matcher, = re.findall(r'if \(!/(.+subagent.+)/\.test\(event\.toolName\)\)', pi)
        self.assertEqual({name for name in names if re.search(matcher, name)},
                         set(tools) - {'Agent', 'Task'})
        for name, data in tools.items():
            with self.subTest(tool=name):
                event = dict(tool_name=name, tool_input=data, session_id='host-1', cwd=str(self.repo))
                check = subprocess.run([sys.executable, str(support.HOOKS / 'managed_launch.py'), 'check'],
                                       input=json.dumps(event), capture_output=True, text=True, check=True)
                self.assertIn('dispatch_card: and execution:', json.loads(check.stdout)['reason'])

    def registered(self):
        import worker_lifecycle as life
        db = life.connect(self.card.parent / 'reports/workers.sqlite')
        self.addCleanup(db.close)
        self.plan()
        with patch.dict('os.environ', CODEX_SESSION_ID='host-1'):
            for item in self.items:
                life.record(db, dict(kind='register', attempt=item['attempt'], assignment=item['assignment'],
                    repo=str(self.repo), worktree=item['worktree'], head='head-1', execution=item['execution'], runtime='pi',
                    worker=f'worker-{item["assignment"]}', native_session=f'native-{item["assignment"]}',
                    owned=True, packet=item['packet']))
        return db

    def test_native_batch_requires_a_plan_and_exact_routed_settings(self):
        with self.assertRaisesRegex(ValueError, 'batch'):
            self.hook.check_event(self.event())
        self.plan()
        wrong = self.event()
        wrong['tool_input']['tasks'][1]['model'] = 'handwritten-inherit-substitution'
        with self.assertRaisesRegex(ValueError, 'substitute'):
            self.hook.check_event(wrong)
        # The invalid second sibling must not consume the first sibling's launch claim.
        self.hook.check_event(self.event())
        with self.assertRaisesRegex(ValueError, 'already'):
            self.hook.check_event(self.event(call='duplicate-batch'))

    def test_unrelated_session_and_unmanaged_helpers_are_unaffected(self):
        event = self.event()
        event['session_id'] = 'another-host'
        event['tool_input'] = dict(task='ordinary helper')
        self.hook.check_event(event)
        event['tool_name'] = 'subagent'
        event['session_id'] = 'host-1'
        event['tool_input'] = dict(action='list', capabilities=True)
        self.hook.check_event(event)

    def test_foreground_calls_cannot_serialize_a_selected_sibling_batch(self):
        self.plan()
        event = self.event(names=('a',))
        event['tool_input']['tasks'][0]['async'] = False
        with self.assertRaisesRegex(ValueError, 'background'):
            self.hook.check_event(event)

    def test_opaque_workflows_cannot_bypass_managed_launch_admission(self):
        self.plan()
        event = self.event()
        event['tool_input'] = dict(workflow='/fixture/workflow.js')
        with self.assertRaisesRegex(ValueError, 'workflow'):
            self.hook.check_event(event)
        event['session_id'] = 'unmanaged-host'
        self.hook.check_event(event)

    def test_t3_probe_uses_the_checked_target_and_provider_thinking_option(self):
        item = self.items[0]
        execution = json.loads(Path(item['execution']).read_text())
        execution.update(executor_type='t3', native_dispatch=False, target_runtime='codex',
                         settings=dict(provider='codex', model='sol', effort='high'))
        routed = self.dispatch.issue_route(self.card, self.repo, item['attempt'], Path(item['packet']),
                                          Path(item['worktree']), execution, [])
        Path(item['execution']).write_text(json.dumps(routed))
        self.request.update(launch=['a/1'], deferred={'b/1': dict(kind='capacity')})
        self.request['capacity']['limit'] = 1
        self.plan()
        task = self.dispatch.startup_probe(self.card, item['execution'], routed['dispatch'])
        event = dict(tool_name='mcp__t3_code__delegate_task', cwd=str(self.repo), session_id='host-1', tool_call_id='probe',
            tool_input=dict(task=task, isolation='worktree', target=dict(providerInstanceId='codex', model='sol',
                options=[dict(id='reasoningEffort', value='low')])) )
        with self.assertRaisesRegex(ValueError, 'thinking'):
            self.hook.check_event(event)
        event['tool_input']['target']['options'][0]['value'] = 'high'
        self.hook.check_event(event)

    def test_paused_run_blocks_launch_but_status_and_policy_remain_readable(self):
        self.plan()
        self.control.transition(self.card, 'blocked', dict(kind='host', reference='provider unavailable'),
                                'provider outage', 1, 'blocked')
        with self.assertRaisesRegex(ValueError, 'blocked'):
            self.hook.check_event(self.event())
        self.assertEqual(self.dispatch.gaps(self.card), [])
        self.assertEqual(self.dispatch.managed(self.card, active=False)['dispatch_policy']['scheduling'],
                         'independent-ready')

    def test_managed_yield_does_not_release_unlaunched_work_after_three_holds(self):
        gate = support.load_script('../hooks/yield_gate.py')
        self.plan()
        required = [gate.Required(gap) for gap in self.dispatch.gaps(self.card)]
        for _ in range(5):
            self.assertEqual(gate.count_hold(self.repo, 'host-1', required), (True, None))

    def test_active_managed_goal_cannot_give_up_between_phases_without_a_wake(self):
        gate = support.load_script('../hooks/yield_gate.py')
        self.control.phase(self.card, self.card.read_text().replace('phase: dispatch', 'phase: align'), 1)
        gaps = gate.unfinished_runs(self.repo, 'host-1', set())
        self.assertTrue(gaps)
        for _ in range(5):
            self.assertTrue(gate.count_hold(self.repo, 'host-1', gaps)[0])

    def test_missing_managed_projection_keeps_run_discoverable_without_writing(self):
        gate = support.load_script('../hooks/yield_gate.py')
        self.card.unlink()
        with self.subTest(reader='dispatch inspection'):
            facts = self.dispatch.inspect(self.card)
            self.assertEqual((facts.get('revision'), facts.get('state', {}).get('phase')), (1, 'dispatch'))
        with self.subTest(reader='native launch admission'):
            with self.assertRaisesRegex(ValueError, 'dispatch_card: and execution:'):
                self.hook.check_event(dict(tool_name='subagent', tool_input=dict(agent='worker', task='unrouted'),
                                           session_id='host-1', cwd=str(self.repo)))
        with self.subTest(reader='yield gate'):
            gaps = gate.unfinished_runs(self.repo, 'host-1', set())
            self.assertIn('has no terminal state', ' '.join(gaps))
            self.assertTrue(gate.count_hold(self.repo, 'host-1', gaps)[0])
        self.assertFalse(self.card.exists())

    def test_handwritten_terminal_cannot_clear_an_active_managed_goal(self):
        gate = support.load_script('../hooks/yield_gate.py')
        self.card.write_text(self.card.read_text().replace('terminal:\n', 'terminal: delivered-local\n'))
        gaps = gate.unfinished_runs(self.repo, 'host-1', set())
        self.assertIn('diverged', ' '.join(gaps))
        for _ in range(5):
            self.assertTrue(gate.count_hold(self.repo, 'host-1', gaps)[0])

    def test_supported_lifecycle_admission_rejects_forgery_and_keeps_valid_batch_metadata(self):
        import worker_lifecycle as life
        state = self.card.parent / 'reports/workers.sqlite'
        db = life.connect(state)
        self.addCleanup(db.close)
        self.plan()
        item = self.items[0]
        registration = dict(kind='register', attempt='a/1', assignment='a', repo=str(self.repo),
            worktree=item['worktree'], head='head-1', execution=item['execution'], runtime='pi',
            worker='worker-a', native_session='native-a', owned=True, packet=item['packet'])
        original = Path(item['execution']).read_text()
        forged = json.loads(original)
        forged.pop('dispatch')
        Path(item['execution']).write_text(json.dumps(forged))
        with patch.dict('os.environ', CODEX_SESSION_ID='host-1'):
            with self.assertRaisesRegex(ValueError, 'route receipt'):
                life.record(db, registration.copy())
            Path(item['execution']).write_text(original)
            life.record(db, registration.copy())
        saved = life.get(db, 'a/1')
        self.assertEqual(saved['dispatch_assignment']['touches'], ['a'])
        self.assertEqual(saved['dispatch_assignment']['assignment'], 'a')

    def test_registered_pending_work_can_continue_after_a_completion_recheck(self):
        import worker_lifecycle as life
        db = self.registered()
        finished = life.get(db, 'a/1')
        finished['result'] = dict(status='completed')
        with db:
            life.save(db, finished)
        request = dict(self.request, assignments=[], launch=[], deferred={})
        self.plan(request)
        item = self.items[1]
        try:
            admitted = self.dispatch.check(self.card, item['attempt'], json.loads(Path(item['execution']).read_text()),
                                           item['packet'], item['worktree'], db=db, admitted=True)
        except ValueError as error:
            self.fail(f'An unchanged pending admission must continue after re-evaluation: {error}')
        self.assertEqual(admitted['assignment'], 'b')

    def test_failed_active_work_cannot_disappear_from_the_next_pass(self):
        import worker_lifecycle as life
        db = self.registered()
        request = dict(self.request, assignments=[], launch=[], deferred={})
        self.plan(request)
        failed = life.get(db, 'b/1')
        failed['result'] = dict(status='failed')
        with db:
            life.save(db, failed)
        with self.assertRaisesRegex(ValueError, 'ticket:b'):
            self.plan(request)


class ResolverReceipt(DispatchFixture, unittest.TestCase):
    def test_fresh_host_keeps_the_runs_original_session_routing_policy(self):
        import os
        import sys
        item = self.items[0]
        config = self.repo / 'session-policy.json'
        config.write_text(json.dumps(dict(agents=dict(builder=dict(executor=dict(type='subagent'),
            models=dict(mode='override', model='approved-session-model'))))))
        base = [sys.executable, '-B', str(support.SCRIPTS / 'agent_models.py'), '--repo', str(self.repo),
                '--host', 'pi', '--job', 'ticket', '--route', '--user-config', str(self.repo / 'absent-models'),
                '--user-executors', str(self.repo / 'absent-executors')]
        env = dict(os.environ, ZLOOP_SESSION='fresh-host', HERDR_ENV='', ZLOOP_PROFILE='',
                   XDG_STATE_HOME=str(self.repo / 'state'), CODEX_HOME=str(self.repo / 'codex-home'))
        saved = subprocess.run(base + ['--session', 'host-1', '--save-session', str(config)],
                               capture_output=True, text=True, env=env)
        self.assertEqual(saved.returncode, 0, saved.stdout + saved.stderr)
        import datetime
        native = self.repo / 'takeover.jsonl'
        native.write_text(json.dumps(dict(type='session_meta', payload=dict(id='host-1'))) + '\n' +
            json.dumps(dict(type='response_item', timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            payload=dict(role='user', content=[dict(type='input_text', text='autopilot --take-over')]))) + '\n')
        user = self.control.user_source(native, 'codex', ['host-1'], 'resume', 0)
        self.control.transition(self.card, '', user, 'new host took over', 1, 'new-host',
                                updates={'session': 'fresh-host, host-1'})
        routed = subprocess.run(base + ['--dispatch-card', str(self.card), '--assignment-id', item['attempt'],
            '--assignment', item['packet'], '--worktree', item['worktree']], capture_output=True, text=True, env=env)
        self.assertEqual(routed.returncode, 0, routed.stdout + routed.stderr)
        result = json.loads(routed.stdout)
        self.assertEqual(result['settings'].get('model'), 'approved-session-model')
        self.assertEqual(result['session']['id'], 'host-1')

    def test_bash_executor_cannot_run_a_handwritten_managed_route(self):
        import sys
        import bash_executor
        item = self.items[0]
        marker = Path(item['worktree']) / 'should-not-launch'
        execution = json.loads(Path(item['execution']).read_text())
        execution.pop('dispatch')
        reports = self.card.parent / 'reports'
        reports.mkdir()
        execution.update(executor_type='bash', reports=str(reports), executor_config=dict(argv=[sys.executable,
            '-c', f'import json;from pathlib import Path;Path({str(marker)!r}).touch();'
                  'print(json.dumps(dict(assignment_id="a/1",status="completed",report="done")))']))
        assignment = dict(assignment_id='a/1', worktree=item['worktree'], head='head-1',
            agent_file=str(support.ROOT / 'agents/builder.md'),
            job_file=str(support.ROOT / 'skills/dispatch/jobs/ticket.md'), prompt=Path(item['packet']).read_text())
        with self.assertRaisesRegex(ValueError, 'receipt'):
            bash_executor.run(execution, assignment)
        self.assertFalse(marker.exists())

    def test_real_resolver_refuses_selection_required_then_emits_a_bound_allowed_route(self):
        import os
        import sys
        config = self.repo / '.zloop/executors.json'
        config.parent.mkdir()
        config.write_text(json.dumps({'agents': {'builder': {'candidates': {
            name: dict(executor=dict(type='subagent'), models=dict(mode='override', model=name))
            for name in ('sol', 'sonnet')}}}}))
        item = self.items[0]
        argv = [sys.executable, '-B', str(support.SCRIPTS / 'agent_models.py'), '--repo', str(self.repo),
                '--host', 'pi', '--job', 'ticket', '--route', '--dispatch-card', str(self.card),
                '--assignment-id', 'a/1', '--assignment', item['packet'], '--worktree', item['worktree'],
                '--user-config', str(self.repo / 'absent-models'), '--user-executors', str(self.repo / 'absent-executors')]
        env = dict(os.environ, HERDR_ENV='', ZLOOP_SESSION='', ZLOOP_PROFILE='',
                   XDG_STATE_HOME=str(self.repo / 'state'), CODEX_HOME=str(self.repo / 'codex-home'))
        unresolved = subprocess.run(argv, capture_output=True, text=True, env=env)
        self.assertEqual(unresolved.returncode, 2, unresolved.stdout + unresolved.stderr)
        self.assertIn('selection_required', unresolved.stderr)
        valid = subprocess.run(argv + ['--candidate', 'sol'], capture_output=True, text=True, env=env)
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        execution = json.loads(valid.stdout)
        self.assertEqual(execution['settings']['model'], 'sol')
        self.assertEqual(execution['dispatch']['attempt'], 'a/1')
        self.assertEqual(execution['dispatch']['packet'], str(Path(item['packet']).resolve()))
        denied = subprocess.run(argv + ['--candidate', 'sol', '--model', 'outside'],
                                capture_output=True, text=True, env=env)
        self.assertEqual(denied.returncode, 2, denied.stdout + denied.stderr)


if __name__ == '__main__':
    unittest.main()
