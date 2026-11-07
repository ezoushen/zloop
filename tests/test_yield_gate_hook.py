#!/usr/bin/env python3
"""The Stop hook keeps a host working while a lifecycle worker could finish unseen."""
import calendar
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import support
from native_records import session_record

ROOT = support.ROOT
HOOK = ROOT / 'hooks' / 'yield_gate.py'
LAUNCH = ROOT / 'hooks' / 'herdr_launch.py'
LIFECYCLE = ROOT / 'scripts' / 'worker_lifecycle.py'
TICKETS = ROOT / 'scripts' / 'next_tickets.py'
# What Claude Code, Codex and Pi export to the orchestrator's shell as their session ID; the test run's own are dropped.
SESSION_VARIABLES = ('CLAUDE_CODE_SESSION_ID', 'CODEX_SESSION_ID', 'PI_SESSION_ID')


class GateRepo(unittest.TestCase):
    """A repository whose sessions reach a stub `herdr` on PATH, never a live Herdr server."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='zloop yield ')
        self.addCleanup(self.tmp.cleanup)
        # Outcomes go to a ledger under the state home; keep the user's ledger out of tests.
        self.enterContext(patch.dict(os.environ, {'XDG_STATE_HOME': str(Path(self.tmp.name) / 'state')}))
        self.repo = Path(self.tmp.name).resolve() / 'project'
        self.repo.mkdir()
        self.registrations = {}
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        self.state = self.repo / '.scratch/workstream/reports/workers.sqlite'
        self.execution = self.repo / '.scratch/workstream/reports/execution.json'
        self.execution.parent.mkdir(parents=True)
        self.execution.write_text(json.dumps({'agent': 'builder', 'job': 'ticket', 'settings': {'model': 'sonnet'}}))
        self.bin = Path(self.tmp.name) / 'bin'
        self.bin.mkdir()
        self.served = self.bin / 'herdr.json'
        self.serve()
        # `agent list` and `agent get` answer with the served agents; `pane get` with the served panes and `tab list`
        # and `tab get` with the served tabs of the `--session` server; `pane current` finds no pane there. As real
        # Herdr does, `tab list` answers server_not_running for a session that serves no tabs, and workspace_not_found
        # for a workspace with none.
        stub = self.bin / 'herdr'
        stub.write_text(f'#!{sys.executable}\n' + f'''
import json, sys, time
served = json.load(open({str(self.served)!r}))
args, session = sys.argv[1:], 'default'
open({str(self.bin / 'argv')!r}, 'a').write(' '.join(args) + '\\n')
if args[:1] == ['--session']:
    session, args = args[1], args[2:]
open({str(self.bin / 'calls')!r}, 'a').write(session + ' ' + ' '.join(args) + '\\n')
if served['hang'] or served.get('hang_panes') and args[:2] == ['pane', 'get']:
    time.sleep(30)
if served['fail']:
    print(json.dumps({{'error': {{'code': 'server_unavailable', 'message': served['fail']}}}}), file=sys.stderr)
    sys.exit(1)
if args == ['agent', 'list']:
    print(json.dumps({{'result': {{'agents': served['agents'], 'type': 'agent_list'}}}}))
elif args[:2] == ['pane', 'get'] and args[2] in served['panes'].get(session, {{}}):
    print(json.dumps({{'result': {{'pane': served['panes'][session][args[2]], 'type': 'pane_info'}}}}))
elif args[:2] in (['pane', 'get'], ['pane', 'current']):
    print(json.dumps({{'error': {{'code': 'pane_not_found'}}}}), file=sys.stderr)
    sys.exit(1)
elif args[:2] == ['agent', 'get'] and any(args[2] in (a['name'], a['pane_id']) for a in served['agents']):
    agent = next(a for a in served['agents'] if args[2] in (a['name'], a['pane_id']))
    print(json.dumps({{'result': {{'agent': agent, 'type': 'agent_info'}}}}))
elif args[:2] == ['tab', 'list'] and session not in served['tabs']:
    print(json.dumps({{'error': {{'code': 'server_not_running', 'message': 'no server for ' + session}}}}),
          file=sys.stderr)
    sys.exit(1)
elif args[:3] == ['tab', 'list', '--workspace'] and not any(tab['workspace_id'] == args[3]
                                                            for tab in served['tabs'][session]):
    print(json.dumps({{'error': {{'code': 'workspace_not_found', 'message': args[3]}}}}), file=sys.stderr)
    sys.exit(1)
elif args[:3] == ['tab', 'list', '--workspace']:
    tabs = [tab for tab in served['tabs'][session] if tab['workspace_id'] == args[3]]
    print(json.dumps({{'result': {{'tabs': tabs, 'type': 'tab_list'}}}}))
elif args[:2] == ['tab', 'get'] and any(tab['tab_id'] == args[2] for tab in served['tabs'].get(session, [])):
    print(json.dumps({{'result': {{'tab': {{'tab_id': args[2], 'pane_count': 1}}, 'type': 'tab_info'}}}}))
elif args[:2] == ['tab', 'get']:
    print(json.dumps({{'error': {{'code': 'tab_not_found' if session in served['tabs'] else 'server_not_running'}}}}),
          file=sys.stderr)
    sys.exit(1)
else:
    sys.exit(3)
''')
        stub.chmod(0o700)

    def serve(self, agents=(), panes=None, fail=None, hang=False, tabs=None, hang_panes=False):
        """What the stub Herdr server reports: live agents, panes and tabs by server, or a failure; `hang_panes` hangs
        `pane get` alone."""
        self.served.write_text(json.dumps({'agents': list(agents), 'panes': panes or {}, 'tabs': tabs or {},
                                           'fail': fail, 'hang': hang, 'hang_panes': hang_panes}))

    def env(self, pane='w1:p1', **values):
        """The environment of a session in Herdr pane `pane` of workspace w1, or outside Herdr when None."""
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('HERDR_') and key not in ('ZLOOP_WORKER', *SESSION_VARIABLES)}
        env['PATH'] = f"{self.bin}{os.pathsep}{env['PATH']}"
        return dict(env, **({'HERDR_PANE_ID': pane, 'HERDR_WORKSPACE_ID': 'w1'} if pane else {}), **values)

    def lifecycle(self, kind, attempt='ticket-1/1', pane='w1:p1', env=None, **fields):
        if kind == 'register':
            packet = Path(fields['packet'])
            packet.write_text(f'attempt: {attempt}\n' + packet.read_text())
        event = self.state.parent / 'event.json'
        event.write_text(json.dumps(dict(kind=kind, attempt=attempt, **fields)))
        done = subprocess.run([sys.executable, '-B', str(LIFECYCLE), 'record', '--state', str(self.state),
                               '--input', str(event)], check=True, capture_output=True, env=self.env(pane, **(env or {})))
        if kind == 'result':
            # Collection acknowledges the result receipt; one left unacknowledged is a yield gap of its own.
            subprocess.run([sys.executable, '-B', str(LIFECYCLE), 'ack', '--state', str(self.state), '--event-id',
                            str(json.loads(done.stdout)['event_id'])], check=True, capture_output=True)
        if kind == 'register':
            self.registrations[attempt] = fields

    def native(self, attempt='ticket-1/1'):
        """The registered fixture worker's session record, outside the checkout it may only read."""
        saved = self.registrations[attempt]
        return session_record(self.execution.parent / (attempt.replace('/', '-') + '.jsonl'), saved['runtime'],
                              saved['native_session'], saved['worktree'], effort=None)

    def stop(self, cwd=None, active=False, env=None, pane='w1:p1', host='claude', session=None):
        payload = {'hook_event_name': 'Stop', 'cwd': str(cwd or self.repo), 'stop_hook_active': active,
                   **({'session_id': session} if session else {})}
        result = subprocess.run([sys.executable, str(HOOK), host], input=json.dumps(payload),
                                capture_output=True, text=True, env=self.env(pane, **(env or {})), timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def check(self, cwd=None, pane='w1:p1', session=None, env=None):
        """The gaps `yield_gate.py check <cwd> [session]` reports, as Pi's extension runs it."""
        result = subprocess.run([sys.executable, str(HOOK), 'check', str(cwd or self.repo), *filter(None, [session])],
                                capture_output=True, text=True, timeout=20, env=self.env(pane, **(env or {})))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['missing']


    def pi(self, body, pane=None, env=None):
        """Run `body` with Pi's extension `handlers` (the last of each event) and a `ctx` for session pi-1 here."""
        script = f"""
const bootstrap = await import({json.dumps((ROOT / 'hosts/pi/bootstrap.ts').as_uri())});
const handlers = {{}};
bootstrap.default({{ on: (event, handler) => {{ handlers[event] = handler; }}, registerTool() {{}}, registerCommand() {{}},
                   registerShortcut() {{}} }});
const ctx = {{ cwd: {json.dumps(str(self.repo))}, sessionManager: {{ getSessionId: () => 'pi-1' }} }};
{body}
"""
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True,
                                timeout=60, env=self.env(pane, **(env or {})))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def bind(self, pane='w1:p1', slug='fix', autofix=False, code=0, **sessions):
        """Run dispatch's binding for workstream `slug` (None: every workstream), with `--autofix` when `autofix`, as an
        orchestrator in Herdr pane `pane` (None: outside Herdr) whose host exports `sessions` (a SESSION_VARIABLES name
        to its value); the finished process, which must exit `code`."""
        result = subprocess.run([sys.executable, '-B', str(TICKETS), '--repo', str(self.repo), *filter(None, [slug]),
                                 '--orchestrate', *(['--autofix'] if autofix else [])],
                                capture_output=True, text=True, env=self.env(pane, **sessions), timeout=20)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def launch(self, command, cwd=None, pane='w1:p1', session='host-1', env=None):
        """Run the Bash PreToolUse hook as Claude or Codex does before `command`."""
        payload = {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'tool_input': {'command': command},
                   'cwd': str(cwd or self.repo), 'session_id': session}
        result = subprocess.run([sys.executable, str(LAUNCH)], input=json.dumps(payload), capture_output=True,
                                text=True, env=self.env(pane, **(env or {})), timeout=20)
        # The hook never blocks the command: no decision and a clean exit, whatever happened.
        self.assertEqual((result.returncode, result.stdout), (0, ''), result.stderr)

    def logged(self):
        log = self.repo / '.scratch/herdr-launches.jsonl'
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def gap(self, agent, pane):
        return f"{self.repo / '.scratch/herdr-launches.jsonl'}: {agent} (pane {pane}): launched without a registered attempt"


NO_CODEX_REVIEWER = {'models': {'local': {'description': 'Local reviewer', 'runtimes': {'pi': {'id': 'local/reviewer'}}}},
                     'runtimes': {'pi': {'executors': {'pi': {'type': 'subagent'}}}},
                     'agents': {'reviewer': {'model': 'local'}}}


class ReviewHold(GateRepo):
    def setUp(self):
        super().setUp()
        self.report = self.repo / '.scratch/reviews/feature-abc/review.md'
        self.report.parent.mkdir(parents=True)
        self.state = self.report.with_name('workers.sqlite')
        self.command = f'python3 {ROOT}/scripts/agent_models.py --repo {self.repo} --host codex --route --job diff-review'
        self.binding = self.report.with_name('review.json')
        self.binding.write_text(json.dumps(dict(pane='w1:p1', sessions=['host-1'], report=str(self.report),
                                                state=str(self.state), route={'status': 'resolved', 'reason': 'configured reviewer'},
                                                next_commands={'route': self.command,
                                                               'submit': 'worker_lifecycle.py submit --state review-state',
                                                               'collect': 'worker_lifecycle.py collect --state review-state'})))
        self.review()

    def review(self, **fields):
        values = dict(dict(review_head='a' * 40, review_by='none', blocker='none'), **fields)
        self.report.write_text('---\n' + ''.join(f'{key}: {value}\n' for key, value in values.items()) + '---\n')

    def collected(self, **fields):
        shutil.rmtree(self.repo / '.scratch/reviewer', ignore_errors=True)
        state = support.collected_attempt(self.repo, 'reviewer', 'review/1', self.report,
                                           **dict(dict(job='diff-review', head='a' * 40), **fields))
        shutil.copy2(state, self.state)

    def test_a_review_without_a_collected_reviewer_holds_with_the_next_command(self):
        for host in ('claude', 'codex'):
            decision = self.stop(host=host, session='host-1')
            self.assertIsNotNone(decision)
            self.assertEqual(decision['decision'], 'block')
            self.assertIn('review_by: none names no collected worker attempt', decision['reason'])
            self.assertIn(self.command, decision['reason'])
        gaps = self.check(session='host-1')
        self.assertEqual(len(gaps), 1)
        self.assertIn(self.command, gaps[0])

    def test_a_completed_collected_reviewer_at_the_report_head_releases_even_with_findings(self):
        self.review(review_by='review/1', review_status='findings', documentation_status='findings')
        self.collected()
        self.assertEqual(self.check(session='host-1'), [])
        self.assertIsNone(self.stop(session='host-1'))

    def test_only_a_completed_diff_review_at_the_report_head_counts(self):
        for options, reason in (({'status': 'failed'}, 'returned failed'),
                                 ({'job': 'e2e-gate'}, 'is not a diff-review attempt'),
                                 ({'head': 'b' * 40}, 'is not review_head')):
            with self.subTest(options=options):
                self.review(review_by='review/1')
                self.collected(**options)
                gaps = self.check(session='host-1')
                self.assertEqual(len(gaps), 1)
                self.assertIn(reason, gaps[0])

    def test_a_blocker_without_a_reviewer_attempt_still_requires_launching_the_reviewer(self):
        self.review(blocker='reviewer capacity could not be reconciled')
        gaps = self.check(session='host-1')
        self.assertEqual(len(gaps), 1)
        self.assertIn('review_by: none names no collected worker attempt', gaps[0])
        self.assertIn(self.command, gaps[0])
        for host in ('claude', 'codex'):
            decision = self.stop(host=host, session='host-1')
            self.assertIsNotNone(decision)
            self.assertEqual(decision['decision'], 'block')
            self.assertIn(self.command, decision['reason'])

    def test_a_blocker_releases_after_a_diff_reviewer_settles_without_completing(self):
        self.review(blocker='reviewer could not complete')
        for status in ('failed', 'blocked', 'cancelled'):
            with self.subTest(status=status):
                self.collected(status=status)
                self.assertEqual(self.check(session='host-1'), [])
                self.assertIsNone(self.stop(session='host-1'))

    def test_a_blocker_releases_with_a_recorded_no_route_only_while_the_router_still_finds_no_reviewer(self):
        # Review (Sonnet): a blocker with a hand-written no_route record released the hold without asking the router.
        home = Path(self.tmp.name) / 'home'
        home.mkdir()
        env = {'HOME': str(home)}
        self.review(blocker='no reviewer route is configured')
        binding = json.loads(self.binding.read_text())
        binding['route'] = {'status': 'no_route', 'reason': 'no reachable reviewer executor'}
        self.binding.write_text(json.dumps(binding))
        self.assertEqual(len(self.check(session='host-1', env=env)), 1)  # It names no host.
        self.binding.write_text(json.dumps(dict(binding, host='codex')))
        self.assertEqual(len(self.check(session='host-1', env=env)), 1)  # A native subagent is routable now.
        config = self.repo / '.zloop/executors.json'
        config.parent.mkdir()
        config.write_text(json.dumps(NO_CODEX_REVIEWER))
        self.assertEqual(self.check(session='host-1', env=env), [])
        self.assertIsNone(self.stop(session='host-1', env=env))

    def test_a_blocker_cannot_use_a_registered_then_cancelled_reviewer_that_never_launched(self):
        self.review(blocker='reviewer capacity could not be reconciled')
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': 'reviewer',
                                              'job': 'diff-review', 'settings': {'model': 'sonnet'}}))
        self.lifecycle('register', attempt='review/1', assignment='review', repo=str(self.repo),
                       worktree=str(self.repo), head='a' * 40, execution=str(self.execution), runtime='claude',
                       worker='ghost', native_session='ghost-1', owned=True,
                       packet=str(support.assignment_file(self.execution.parent, 'diff-review')))
        self.lifecycle('result', attempt='review/1', status='cancelled', head='a' * 40,
                       report=str(self.report), evidence=['no launch'])
        gaps = self.check(session='host-1')
        self.assertEqual(len(gaps), 1)
        self.assertIn('review_by: none names no collected worker attempt', gaps[0])
        self.assertIn(self.command, gaps[0])

    def test_a_blocker_cannot_use_a_reviewer_from_an_older_head(self):
        self.review(blocker='reviewer capacity could not be reconciled')
        for registered, returned in (('b', 'b'), ('b', 'a'), ('a', 'b')):
            with self.subTest(registered=registered, returned=returned):
                self.collected(status='failed', head=registered * 40)
                with sqlite3.connect(self.state) as db:
                    saved = json.loads(db.execute('SELECT data FROM attempts').fetchone()[0])
                    saved['result']['head'] = returned * 40
                    db.execute('UPDATE attempts SET data = ?', (json.dumps(saved),))
                gaps = self.check(session='host-1')
                self.assertEqual(len(gaps), 1)
                self.assertIn('review_by: none names no collected worker attempt', gaps[0])
                self.assertIn(self.command, gaps[0])

    def test_a_blocker_with_an_unsettled_or_unrelated_attempt_still_names_the_launch_command(self):
        self.review(blocker='reviewer unavailable', review_by='local')
        for job, settled in (('diff-review', False), ('e2e-gate', True)):
            with self.subTest(job=job, settled=settled):
                self.collected(job=job, status='failed')
                if not settled:
                    with sqlite3.connect(self.state) as db:
                        saved = json.loads(db.execute('SELECT data FROM attempts').fetchone()[0])
                        del saved['result']
                        db.execute('UPDATE attempts SET data = ?', (json.dumps(saved),))
                gaps = self.check(session='host-1')
                review_gaps = [gap for gap in gaps if str(self.report) in gap]
                self.assertEqual(len(review_gaps), 1)
                review_gap = review_gaps[0]
                self.assertIn('review_by: local counts only where review_target.py found no reviewer route', review_gap)
                self.assertIn(self.command, review_gap)

    def test_local_review_releases_only_while_the_router_still_finds_no_reviewer_for_its_host(self):
        # User (2026-11-07, "re-ask the router, but would prefer the same host"): Pi reviewed its own work and wrote the
        # no_route record by hand while the router routed it a reviewer; a review the orchestrator did itself counts
        # only while the router, asked again for the host the review started from, still finds none.
        home = Path(self.tmp.name) / 'home'  # No user routing configuration.
        home.mkdir()
        env = {'HOME': str(home)}
        self.review(review_by='local')
        recorded = ('review_by: local counts only where review_target.py found no reviewer route for its host; launch '
                    'the reviewer it routes and name its attempt in review_by')
        binding = json.loads(self.binding.read_text())
        for route, host in (({'status': 'resolved'}, 'codex'), ({'status': 'no_route', 'reason': 'inline by order'}, None)):
            with self.subTest(route=route, host=host):
                self.binding.write_text(json.dumps(dict(binding, route=route, **({'host': host} if host else {}))))
                gaps = self.check(session='host-1', env=env)
                self.assertEqual(len(gaps), 1)
                self.assertIn(recorded, gaps[0])
        self.binding.write_text(json.dumps(dict(binding, route={'status': 'no_route', 'reason': 'none then'}, host='codex')))
        gaps = self.check(session='host-1', env=env)  # The router routes codex's reviewer to a native subagent now.
        self.assertEqual(len(gaps), 1)
        self.assertIn('review_by: local, but the router now routes diff-review from codex to a codex native subagent: '
                      'launch that reviewer and name its attempt in review_by', gaps[0])
        config = self.repo / '.zloop/executors.json'
        config.parent.mkdir()
        config.write_text(json.dumps(NO_CODEX_REVIEWER))
        self.assertEqual(self.check(session='host-1', env=env), [])
        config.write_text('{broken')
        gaps = self.check(session='host-1', env=env)
        self.assertEqual(len(gaps), 1)
        self.assertIn('review_by: local cannot be judged, the router failed: ', gaps[0])
        # Review (Sonnet): under Herdr a reviewer on another runtime is a route too, asked with the review's reports folder.
        config.write_text(json.dumps({'models': {'cx': {'description': 'Codex reviewer', 'runtimes': {'codex': {'id': 'gpt-x'}}}},
                                      'runtimes': {'codex': {'executors': {'codex': {'type': 'subagent'}}}},
                                      'agents': {'reviewer': {'model': 'cx'}}}))
        self.binding.write_text(json.dumps(dict(binding, route={'status': 'no_route', 'reason': 'none then'}, host='pi')))
        gaps = self.check(session='host-1', env=dict(env, HERDR_ENV='1'))
        self.assertEqual(len(gaps), 1)
        self.assertIn('review_by: local, but the router now routes diff-review from pi to codex (gpt-x) through herdr: '
                      'launch that reviewer and name its attempt in review_by', gaps[0])
        self.binding.write_text(json.dumps(dict(binding, route={'status': 'no_route', 'reason': 'none then'}, host='codex')))
        # Review (Codex): a pool of reviewers that needs a choice is a reviewer to choose, not "None" to launch.
        config.write_text(json.dumps({'agents': {'reviewer': {'candidates': {
            'first': {'executor': {'type': 'subagent'}}, 'second': {'executor': {'type': 'subagent'}}}}}}))
        gaps = self.check(session='host-1', env=env)
        self.assertEqual(len(gaps), 1)
        self.assertIn('review_by: local, but the router has reviewers for codex to choose from (first, second): choose '
                      'one with --candidate on the route command, launch it and name its attempt in review_by', gaps[0])

    def test_a_live_reviewer_with_an_armed_wait_yields_until_its_wait_is_lost(self):
        self.collected()
        with sqlite3.connect(self.state) as db:
            saved = json.loads(db.execute('SELECT data FROM attempts').fetchone()[0])
            del saved['result']
            saved['monitor'] = dict(state='running', pid=os.getpid(), stamp='live-process',
                                    notify={'argv': ['wake-parent']})
            db.execute('UPDATE attempts SET data = ?', (json.dumps(saved),))
        process_table = self.bin / 'ps'
        process_table.write_text(f'#!{sys.executable}\nprint("live-process")\n')
        process_table.chmod(0o700)
        self.assertEqual(self.check(session='host-1'), [])
        process_table.write_text(f'#!{sys.executable}\n')
        gaps = self.check(session='host-1')
        self.assertEqual(len(gaps), 1)
        self.assertIn('review_by: none names no collected worker attempt', gaps[0])

    def test_review_bindings_hold_only_their_session_else_their_pane(self):
        self.assertEqual(self.check(session='host-2'), [])
        self.assertEqual(self.check(pane='w1:p9', session=None), [])
        self.assertEqual(len(self.check(pane='w1:p9', session='host-1')), 1)
        self.assertEqual(len(self.check(session=None)), 1)
        self.binding.rename(self.repo / '.scratch/workstream/reports/review.json')
        self.assertEqual(self.check(session='host-2'), [])
        self.assertEqual(len(self.check(session='host-1')), 1)

    def test_another_sessions_unreadable_standalone_state_cannot_hold_this_session(self):
        self.state.write_bytes(b'not a sqlite database')
        self.assertEqual(self.check(session='host-2'), [])
        mine = self.check(session='host-1')
        self.assertTrue(any('cannot be read' in gap for gap in mine))

    def test_a_standalone_review_state_accounts_for_its_registered_herdr_launch(self):
        self.review(review_by='review/1')
        self.collected()
        shutil.rmtree(self.repo / '.scratch/reviewer')
        with sqlite3.connect(self.state) as db:
            saved = json.loads(db.execute('SELECT data FROM attempts').fetchone()[0])
            saved.update(worker='w1:p5', session='host-1', sessions=['host-1'])
            db.execute('UPDATE attempts SET data = ?', (json.dumps(saved),))
        worktree = self.repo / '.worktrees/reviewer'
        worktree.mkdir(parents=True)
        self.launch('herdr agent start reviewer --kind codex --pane w1:p5', cwd=worktree)
        self.assertEqual(self.check(session='host-1'), [])

    def test_an_uncollected_reviewer_names_the_collect_command(self):
        self.review(review_by='review/1')
        self.collected()
        with sqlite3.connect(self.state) as db:
            saved = json.loads(db.execute('SELECT data FROM attempts').fetchone()[0])
            del saved['result']
            db.execute('UPDATE attempts SET data = ?', (json.dumps(saved),))
        gaps = self.check(session='host-1')
        review_gap = next(gap for gap in gaps if str(self.report) in gap)
        self.assertIn('review_by: review/1 names no collected worker attempt', review_gap)
        self.assertIn('worker_lifecycle.py collect --state review-state', review_gap)


class YieldGateHook(GateRepo):
    def setUp(self):
        super().setUp()
        self.lifecycle('register', assignment='ticket-1', repo=str(self.repo), worktree=str(self.repo),
                       head='head-1', execution=str(self.execution), runtime='claude', worker='pane-1',
                       native_session='native-1', owned=True,
                       packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.lifecycle('startup', native=str(self.native('ticket-1/1')))
        self.lifecycle('submitted', evidence=['native.jsonl:2:accepted prompt'])

    def test_a_live_worker_without_a_wake_path_blocks_the_stop(self):
        decision = self.stop(cwd=self.repo / '.scratch')
        self.assertEqual(decision['decision'], 'block')
        self.assertIn('ticket-1/1: no armed wait', decision['reason'])
        self.assertIn(str(self.state), decision['reason'])
        self.assertIn('workers/wait.md', decision['reason'])
        # A stop that a stop hook already continued is held again: StopHolds bounds the repeats.
        self.assertEqual(self.stop(active=True)['decision'], 'block')
        self.assertIsNone(self.stop(env={'ZLOOP_YIELD_HOOK': 'off'}))

    def test_an_unreadable_launch_log_line_is_named_to_the_session_it_names_and_hides_no_other_gap(self):
        # Review 6, gap 10: a torn or hand-broken line is reported by number to a session its raw text names, and
        # ignored for matching; the lifecycle gaps and the readable launches still show. One that names no session or
        # pane holds no one.
        log = self.repo / '.scratch/herdr-launches.jsonl'
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5')
        with log.open('a') as stream:
            stream.write('{"time": "2026-10-2\n{"agent": "builder-9"}\n')
        self.launch('herdr agent start builder-2 --kind codex --pane w1:p6')
        with log.open('a') as stream:
            stream.write('{"time": "2026-10-2", "orchestrator": {"pane": null, "session": "host-1"\n')
        self.assertEqual(self.check(), [f'{self.state}: ticket-1/1: no armed wait', self.gap('builder-1', 'w1:p5'),
                                        self.gap('builder-2', 'w1:p6')])
        self.assertEqual(self.check(pane=None, session='host-1'), [
            self.gap('builder-1', 'w1:p5'), self.gap('builder-2', 'w1:p6'),
            f'{log}: launch log line 5 is unreadable; fix or remove it'])
        self.assertEqual(self.check(pane=None, session='host-2'), [])

    def test_a_line_with_malformed_fields_is_named_like_an_unreadable_one(self):
        # Re-review 3: valid JSON whose fields have the wrong shape (the probe's empty orchestrator, a bad time, a
        # number for a name or cwd, a dismissal without its host session) is named and ignored for matching.
        log = self.repo / '.scratch/herdr-launches.jsonl'
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5')
        at, orchestrator = '2026-10-20T10:00:00Z', {'pane': 'w1:p1', 'session': None}
        bad = [{'time': 'x', 'agent': 'b', 'pane': 'w1:p9', 'orchestrator': {}},
               {'time': 'yesterday', 'agent': 'b', 'pane': 'w1:p9', 'orchestrator': orchestrator},
               {'time': at, 'agent': 7, 'pane': 'w1:p9', 'orchestrator': orchestrator},
               {'time': at, 'agent': 'b', 'pane': 'w1:p9', 'orchestrator': orchestrator, 'cwd': 5},
               {'time': at, 'dismissed': {'pane': 'w1:p5'}, 'by': {'pane': 'w1:p1'}, 'reason': 'probe'}]
        with log.open('a') as stream:
            stream.writelines(json.dumps(record) + '\n' for record in bad)
        # The line with the empty orchestrator, like the other three of pane w1:p9, names no session or pane that
        # launched it, so only the session in the pane its text names is held.
        self.assertEqual(self.check(session='host-1'), [
            f'{self.state}: ticket-1/1: no armed wait', self.gap('builder-1', 'w1:p5'),
            *(f'{log}: launch log line {number} is unreadable; fix or remove it' for number in range(3, 7))])
        self.assertEqual(self.check(pane='w1:p9', session='host-3'),
                         [f'{log}: launch log line {number} is unreadable; fix or remove it' for number in range(2, 6)])
        self.assertEqual(self.check(pane='w1:p7', session='host-4'), [])

    def test_a_checkout_without_worktrees_never_asks_herdr(self):
        # Review 4: a Herdr session in a repository with no .worktrees/ has no ticket worker to look for, so a broken
        # Herdr neither slows nor holds it; its lifecycle gaps still show.
        self.serve(fail='connection refused')
        self.assertEqual(self.check(), [f'{self.state}: ticket-1/1: no armed wait'])

    def test_malformed_attempt_rows_are_named_only_to_their_workstreams_orchestrator_and_break_no_check(self):
        # Rule: a run state written by hand (a demo or fixture) may hold rows the lifecycle never writes. Each is left
        # out of every check and named only to the session bound to its workstream, so it neither breaks nor holds another
        # session's check; a row with no worktree but otherwise whole is an ordinary attempt.
        other = self.repo / '.scratch/demo/reports/workers.sqlite'
        other.parent.mkdir(parents=True)
        db = sqlite3.connect(other)
        db.execute('CREATE TABLE attempts (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        db.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, attempt TEXT NOT NULL, kind TEXT NOT NULL, '
                   'payload TEXT NOT NULL, acknowledged INTEGER NOT NULL)')
        whole = {'attempt': 'demo/1', 'assignment': 'demo/01', 'repo': str(self.repo), 'runtime': 'claude',
                 'native_session': 'demo-a', 'worker': 'w-demo-a', 'owned': True}
        rows = {'demo/1': whole,
                'demo/2': {key: value for key, value in whole.items() if key != 'worker'},
                'demo/3': {key: value for key, value in whole.items() if key != 'attempt'},
                'demo/4': dict({key: value for key, value in whole.items() if key != 'runtime'}, closed=True),
                'demo/5': dict(whole, attempt='demo/5', worktree=7),
                'demo/6': dict(whole, attempt='demo/6', herdr={'tab_id': 'w1:t1'}, result={'status': 'completed'},
                               orchestrator='w1:p7'),
                'demo/7': dict(whole, attempt='demo/7', launch_sessions='pi-parent')}
        db.executemany('INSERT INTO attempts VALUES (?, ?)', [(key, json.dumps(row)) for key, row in rows.items()])
        db.execute('INSERT INTO events VALUES (1, ?, ?, ?, 0)', ('demo/1', 'register', json.dumps({})))
        db.commit()
        db.close()
        old = time.time() - 3 * 24 * 3600
        os.utime(other, (old, old))  # However old the state, it holds no other session.
        self.assertEqual(self.check(), [f'{self.state}: ticket-1/1: no armed wait'])
        ticket = self.repo / '.scratch/demo/issues/01-task.md'
        ticket.parent.mkdir(parents=True)
        ticket.write_text('---\nticket: demo/01\nstatus: ready\nblocked_by: []\ntouches: [t01]\ndevices: []\n---\n')
        self.bind(pane=None, slug='demo', CLAUDE_CODE_SESSION_ID='host-9')
        os.utime(other, (old, old))
        self.assertEqual(self.check(pane=None, session='host-9'),
                         [f'{other}: attempt row {key} is malformed; fix or remove it'
                          for key in ('demo/2', 'demo/3', 'demo/4', 'demo/5', 'demo/6', 'demo/7')])

    def test_codex_uses_the_same_stop_protocol(self):
        self.assertEqual(self.stop(host='codex')['decision'], 'block')
        self.assertEqual(self.stop(host='codex', active=True)['decision'], 'block')

    def test_only_the_herdr_pane_that_registered_a_worker_is_held(self):
        self.assertIsNone(self.stop(pane='w1:p9'))
        self.assertEqual(self.stop(pane='w1:p1')['decision'], 'block')

    def test_a_worker_session_in_its_own_worktree_is_not_held(self):
        worktree = self.repo / '.worktrees/ticket-2'
        worktree.mkdir(parents=True)
        # A started native child, so its own wake is not judged here: only where its session runs.
        execution = self.state.parent / 'child-execution.json'
        execution.write_text(json.dumps(dict(json.loads(self.execution.read_text()), native_dispatch=True)))
        self.lifecycle('register', 'ticket-2/1', None, assignment='ticket-2', repo=str(self.repo),
                       worktree=str(worktree), head='head-1', execution=str(execution), runtime='claude',
                       worker='pane-2', native_session='native-2', owned=True,
                       packet=str(support.assignment_file(execution.parent, 'ticket')))
        self.lifecycle('startup', 'ticket-2/1', None, native=str(self.native('ticket-2/1')))
        self.assertIsNone(self.stop(cwd=worktree, pane=None))
        # ticket-1/1 holds the pane that registered it, not a session elsewhere.
        self.assertEqual(self.stop()['decision'], 'block')
        self.assertIsNone(self.stop(pane=None))

    def test_a_worker_this_session_registered_is_judged_however_old_its_state(self):
        # Gap 22 (gate half), audit A8: attribution, not the state's age, decides for an attributed attempt.
        stale = time.time() - 2 * 24 * 3600
        os.utime(self.state, (stale, stale))
        self.assertEqual(self.stop()['decision'], 'block')
        # Checking reads the state without touching it.
        self.assertEqual(self.state.stat().st_mtime, stale)
        self.assertIsNone(self.stop(pane='w1:p9'))
        outside = Path(self.tmp.name) / 'elsewhere'
        outside.mkdir()
        self.assertIsNone(self.stop(cwd=outside))

    def test_a_worker_registered_without_session_or_pane_holds_only_while_its_state_is_recent(self):
        # The 24-hour cutoff keeps only the rows nobody can be attributed to: one that ran outside any host.
        self.lifecycle('register', 'ticket-2/1', None, assignment='ticket-2', repo=str(self.repo),
                       worktree=str(self.repo), head='head-1', execution=str(self.execution), runtime='claude',
                       worker='pane-2', native_session='native-2', owned=True,
                       packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.lifecycle('startup', 'ticket-2/1', native=str(self.native('ticket-2/1')))
        self.lifecycle('submitted', 'ticket-2/1', evidence=['native.jsonl:2:accepted prompt'])
        self.assertEqual(self.check(pane='w1:p9'), [f'{self.state}: ticket-2/1: no armed wait'])
        stale = time.time() - 2 * 24 * 3600
        os.utime(self.state, (stale, stale))
        self.assertEqual(self.check(pane='w1:p9'), [])
        self.assertEqual(self.check(pane='w1:p1'), [f'{self.state}: ticket-1/1: no armed wait'])

    def test_check_mode_reports_gaps_for_other_hosts(self):
        result = subprocess.run([sys.executable, str(HOOK), 'check', str(self.repo)],
                                capture_output=True, text=True, timeout=20, env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['missing'], [f'{self.state}: ticket-1/1: no armed wait'])

    @unittest.skipUnless(shutil.which('node'), 'Node.js runs the Pi extension')
    def test_pi_continues_a_completed_run_while_the_gate_holds_then_says_it_gave_up(self):
        # Gap 17 on Pi: the extension obeys the check, three holds of an identical gap set, then the notice; an abort is
        # never resumed, and the extension keeps no once-per-run guard of its own.
        results = self.pi("""
const settle = async (outcome) => (await handlers.agent_before_settle(
  { type: 'agent_before_settle', outcome, entries: [], continue: false }, ctx)) ?? null;
console.log(JSON.stringify([await settle('completed'), await settle('completed'), await settle('aborted'),
  await settle('completed'), await settle('completed'), await settle('completed'), 'agent_settled' in handlers]));
""", pane='w1:p1')
        first, second, aborted, third, notice, after, has_settled_handler = results
        for decision in (first, second, third):
            self.assertTrue(decision['continue'])
            self.assertEqual(decision['entries'][0]['type'], 'custom_message')
            self.assertIn('ticket-1/1: no armed wait', decision['entries'][0]['content'])
        self.assertIsNone(aborted)
        self.assertFalse(notice['continue'])
        self.assertIn('gave up holding the stop after 3 holds of the same gaps', notice['entries'][0]['content'])
        self.assertIsNone(after, 'the notice is said once')
        self.assertFalse(has_settled_handler)


class StopHolds(GateRepo):
    """A stop is held while its gaps stand, up to three holds of an identical gap set per session; every host asks the
    same gate."""

    def setUp(self):
        super().setUp()
        (self.repo / '.worktrees').mkdir()

    def launch_gap(self, name='builder-1', pane='w1:p5', session='host-1'):
        """An unregistered launch of `session`: one gap that only a registration or a dismissal clears."""
        self.launch(f'herdr agent start {name} --kind codex --pane {pane}', session=session)

    def clear_gap(self, pane='w1:p5', session='host-1'):
        subprocess.run([sys.executable, str(LAUNCH), 'dismiss', '--pane', pane, '--session', session, '--reason',
                        'probe'], cwd=self.repo, check=True, capture_output=True, env=self.env())

    def verdict(self, session='host-1', env=None):
        result = subprocess.run([sys.executable, str(HOOK), 'stop', str(self.repo), session], capture_output=True,
                                text=True, timeout=20, env=self.env(**(env or {})))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def held(self, session='host-1', host='claude', active=False):
        """Whether the stop is held; a stop let through carries no decision."""
        decision = self.stop(session=session, host=host, active=active)
        self.assertTrue(decision is None or decision.get('decision') in (None, 'block'), decision)
        return bool(decision and decision.get('decision') == 'block')

    def test_claude_and_codex_hold_a_stop_three_times_while_its_gaps_stand_then_say_they_gave_up(self):
        # Gap 17: stop_hook_active no longer means "allow"; the bound is three consecutive holds of an identical set.
        for host, session in (('claude', 'host-1'), ('codex', 'host-2')):
            self.launch_gap(session=session)
            for round in range(3):
                decision = self.stop(session=session, host=host, active=bool(round))
                self.assertEqual(decision['decision'], 'block', (host, round))
                self.assertIn(f'- {self.gap("builder-1", "w1:p5")}\n', decision['reason'])
            given_up = self.stop(session=session, host=host, active=True)
            self.assertNotIn('decision', given_up)
            self.assertIn('gave up holding the stop after 3 holds of the same gaps', given_up['systemMessage'])
            self.assertIn(self.gap('builder-1', 'w1:p5'), given_up['systemMessage'])
            self.assertIsNone(self.stop(session=session, host=host), 'a later stop passes without saying it again')

    def test_a_changed_gap_set_restarts_the_count(self):
        self.launch_gap()
        self.assertEqual([self.held(), self.held()], [True, True])
        self.launch_gap('builder-2', 'w1:p6')  # A different set: three new holds.
        self.assertEqual([self.held() for _ in range(4)], [True, True, True, False])

    def test_a_cleared_gap_set_ends_the_count_so_the_same_gap_again_is_held_afresh(self):
        self.launch_gap()
        self.assertEqual([self.held(), self.held()], [True, True])
        self.clear_gap()
        self.assertFalse(self.held(), 'no gap, no hold')
        self.launch_gap()
        self.assertEqual([self.held() for _ in range(4)], [True, True, True, False])

    def test_each_session_counts_its_own_holds_in_a_record_under_scratch_holds(self):
        self.launch_gap(session='host-1')
        self.launch_gap('builder-2', 'w1:p6', session='host-2')
        self.assertEqual([self.held('host-1') for _ in range(4)], [True, True, True, False])
        self.assertEqual([self.held('host-2') for _ in range(4)], [True, True, True, False])
        records = sorted((self.repo / '.scratch/holds').glob('*.json'))
        self.assertEqual(len(records), 2, records)
        for record in records:
            self.assertEqual(json.loads(record.read_text())['holds'], 3)  # Written whole: no temporary file is left.

    def test_concurrent_stops_of_one_session_are_counted_one_at_a_time(self):
        # Review 1: two stops that read the record before either wrote it counted one hold, giving four. The count is
        # read, raised and written under the record's lock, so a stop waits while another holds it.
        sys.path.insert(0, str(ROOT / 'scripts'))
        self.addCleanup(sys.path.remove, str(ROOT / 'scripts'))
        import state_files
        self.launch_gap()
        self.assertTrue(self.held())  # The record exists now.
        record = self.repo / '.scratch/holds/session-host-1.json'
        with state_files.locked(record):
            stop = subprocess.Popen([sys.executable, str(HOOK), 'stop', str(self.repo), 'host-1'], env=self.env(),
                                    stdout=subprocess.PIPE, text=True)
            self.addCleanup(stop.kill)
            time.sleep(1.5)
            self.assertIsNone(stop.poll(), 'the stop counted its hold while another had the record locked')
        self.assertTrue(json.loads(stop.communicate(timeout=20)[0])['hold'])

    def test_the_stop_command_says_to_hold_and_then_that_it_gave_up(self):
        self.launch_gap()
        for _ in range(4):  # Reporting the gaps with `check` counts no stop.
            self.assertEqual(self.check(session='host-1'), [self.gap('builder-1', 'w1:p5')])
        verdicts = [self.verdict() for _ in range(5)]
        self.assertEqual([verdict['hold'] for verdict in verdicts], [True, True, True, False, False])
        self.assertEqual(verdicts[4]['missing'], [self.gap('builder-1', 'w1:p5')], 'the gaps are reported whatever the count')
        self.assertIn('gave up holding the stop after 3 holds of the same gaps', verdicts[3]['notice'])
        self.assertEqual([verdict['notice'] for verdict in verdicts[:3] + verdicts[4:]], [None] * 4, 'said once')
        self.launch_gap('builder-2', 'w1:p6')
        self.assertEqual((self.verdict()['hold'], self.verdict()['notice']), (True, None))

    def test_a_check_that_could_not_run_holds_once_but_a_real_gap_beside_it_holds_three_times(self):
        broken = Path(self.tmp.name) / 'broken-git'
        broken.mkdir()
        (broken / 'git').write_text('#!/bin/sh\necho "fatal: unable to read index" >&2\nexit 128\n')
        (broken / 'git').chmod(0o700)
        env = {'PATH': f"{broken}{os.pathsep}{self.env()['PATH']}"}
        self.assertTrue(self.stop(env=env, session='host-1', active=True)['decision'])
        self.assertNotIn('decision', self.stop(env=env, session='host-1') or {})
        self.serve(fail='connection refused')  # Herdr cannot be asked, beside a real launch gap.
        self.launch_gap(session='host-3')
        self.assertEqual([self.held('host-3') for _ in range(4)], [True, True, True, False])


class UnregisteredWorkers(GateRepo):
    """Each `herdr agent start` is logged, so a worker its orchestrator never registered is named at stop."""

    def setUp(self):
        super().setUp()
        self.worktree = self.repo / '.worktrees/ticket-1'
        self.worktree.mkdir(parents=True)
        self.serve(panes={'default': {'w1:p5': {'pane_id': 'w1:p5', 'cwd': str(self.worktree)}}})
        # These registrations stand for any worker its orchestrator accounted for; as native children with a recorded
        # startup they need no wait, which these tests do not judge (Waits does).
        self.execution.write_text(json.dumps(dict(json.loads(self.execution.read_text()), native_dispatch=True)))

    def register(self, worker, attempt, native_session=None, pane='w1:p1', worktree=None, session=None):
        """Register `worker` for an attempt in the run's workers.sqlite, as prepare.md has the orchestrator in
        Herdr pane `pane` (None: outside Herdr) do, from a Claude Code host exporting `session` when given."""
        self.lifecycle('register', attempt, pane, env={'CLAUDE_CODE_SESSION_ID': session} if session else None,
                       assignment=attempt.split('/')[0], repo=str(self.repo),
                       worktree=str(worktree or self.worktree), head='head-1', execution=str(self.execution), runtime='claude',
                       worker=worker, native_session=native_session or f'native-{attempt}', owned=True,
                       packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.lifecycle('startup', attempt, pane, native=str(self.native(attempt)))

    @staticmethod
    def agent(name, pane, cwd, workspace='w1'):
        """A live agent as `herdr agent list` reports it."""
        return {'agent': 'codex', 'agent_status': 'idle', 'name': name, 'pane_id': pane, 'tab_id': f'{workspace}:t9',
                'workspace_id': workspace, 'cwd': str(cwd)}

    @staticmethod
    def live(name, pane, cwd):
        return f'Herdr agent {name} (pane {pane}) runs in {cwd} without a registered attempt'

    def test_each_herdr_agent_start_is_logged_in_the_main_checkout_for_its_orchestrator(self):
        # Rule 1: time, agent name, pane, the Herdr server session it ran in (None for the default one), orchestrator
        # (its Herdr pane and host session, each when known) and the pane's cwd as the named Herdr server reports it; a
        # command that only mentions a launch records nothing.
        self.serve(panes={'s1': {'w1:p5': {'pane_id': 'w1:p5', 'cwd': str(self.worktree)}},
                          'default': {'w1:p5': {'pane_id': 'w1:p5', 'cwd': '/elsewhere'}}})
        before = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        self.launch('herdr --session s1 agent start builder-1 --kind codex --pane w1:p5 -- -m x', cwd=self.worktree)
        self.launch('git commit -qm "herdr agent start x --pane w1:p5" && echo herdr agent start y --pane w1:p5\n'
                    'FOO=1 herdr agent start reviewer-2 --kind claude --pane w1:p6', pane=None)
        after = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        logged = self.logged()
        for record in logged:
            self.assertTrue(before <= record.pop('time') <= after)
        self.assertEqual(logged, [
            {'agent': 'builder-1', 'pane': 'w1:p5', 'herdr_session': 's1',
             'orchestrator': {'pane': 'w1:p1', 'session': 'host-1'}, 'cwd': str(self.worktree)},
            {'agent': 'reviewer-2', 'pane': 'w1:p6', 'herdr_session': 'default',
             'orchestrator': {'pane': None, 'session': 'host-1'}}])

    def test_one_command_with_many_launches_stays_inside_the_hook_timeout(self):
        # Review 7: the Herdr lookups of one hook run share a budget inside the hosts' hook timeout, so a hung
        # Herdr still leaves every launch logged (cwd unknown) before the host kills the hook.
        limit = min(hook['timeout'] for path in ('hooks/hooks.json', 'hosts/codex/hooks.json')
                    for entry in json.loads((ROOT / path).read_text())['hooks']['PreToolUse']
                    for hook in entry['hooks'] if hook['command'].endswith('hooks/herdr_launch.py"'))
        self.serve(hang=True)
        started = time.monotonic()
        self.launch(' && '.join(f'herdr agent start b-{index} --kind codex --pane w1:p{index}' for index in range(1, 6)))
        self.assertLess(time.monotonic() - started, limit - 2)
        self.assertEqual([{key: value for key, value in record.items() if key not in ('time', 'orchestrator', 'herdr_session')}
                          for record in self.logged()],
                         [{'agent': f'b-{index}', 'pane': f'w1:p{index}'} for index in range(1, 6)])

    def test_launches_behind_wrappers_and_shell_strings_are_logged(self):
        # Review 5: `timeout`, `env` with its options, `--session=S` and a shell's `-c` string still run a launch.
        self.serve(panes={'s1': {pane: {'pane_id': pane, 'cwd': str(self.worktree)} for pane in ('w1:p14', 'w1:p16')}})
        for command in ('timeout 60 herdr agent start a-1 --kind codex --pane w1:p11',
                        'timeout -s KILL -k 5 60 herdr agent start a-2 --kind codex --pane w1:p12',
                        'env -u HERDR_PANE_ID -i FOO=1 herdr agent start a-3 --kind codex --pane w1:p13',
                        'herdr --session=s1 agent start a-4 --kind codex --pane w1:p14',
                        'sh -c "herdr agent start a-5 --kind codex --pane w1:p15"',
                        "bash -lc 'cd /tmp && herdr --session s1 agent start a-6 --kind codex --pane w1:p16'",
                        'bash -c "timeout 30 herdr agent start a-7 --kind codex --pane w1:p17" && echo done'):
            self.launch(command, pane=None)
        self.assertEqual([(record['agent'], record['pane'], record.get('cwd')) for record in self.logged()], [
            ('a-1', 'w1:p11', None), ('a-2', 'w1:p12', None), ('a-3', 'w1:p13', None),
            ('a-4', 'w1:p14', str(self.worktree)), ('a-5', 'w1:p15', None), ('a-6', 'w1:p16', str(self.worktree)),
            ('a-7', 'w1:p17', None)])

    def test_a_start_herdr_would_refuse_or_only_explain_is_not_logged(self):
        # Rule: `herdr agent start` needs a name and a pane, and with --help only prints its usage, so none of these
        # starts an agent; logged, each would hold its orchestrator for a worker nothing can register or dismiss.
        for command in ('herdr agent start a-1 --kind codex --pane w1:p11 --help',
                        'herdr agent start a-4 -h --kind codex --pane w1:p14',
                        'herdr agent start --kind codex --pane w1:p12', 'herdr agent start a-2 --kind codex',
                        'herdr agent start a-3 --kind codex --pane w1:p13'):
            self.launch(command, pane=None)
        self.assertEqual([(record['agent'], record['pane']) for record in self.logged()], [('a-3', 'w1:p13')])

    def test_a_logged_launch_with_no_name_or_pane_is_named_only_to_its_orchestrator(self):
        # Rule: an older hook logged such a line; no registration or dismissal can match it, so the stop names the line
        # to remove, and only to the session that logged it.
        log = self.repo / '.scratch/herdr-launches.jsonl'
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5')
        with log.open('a') as stream:
            stream.write(json.dumps({'time': '2026-10-20T10:00:00Z', 'agent': None, 'pane': None,
                                     'orchestrator': {'pane': 'w1:p1', 'session': 'host-1'}}) + '\n')
        unnamed = f'{log}: launch log line 2 names no agent or pane, so no attempt or dismissal can clear it; remove it'
        self.assertEqual(self.check(session='host-1'), [self.gap('builder-1', 'w1:p5'), unnamed])
        self.assertNotIn(unnamed, self.check(pane='w1:p2', session='host-2'))

    def test_a_start_whose_name_or_pane_is_a_shell_expansion_is_not_logged_but_the_live_agent_still_is_named(self):
        # Gap 14: `herdr agent start $name --pane $pane` in a function or script names no real agent or pane, so
        # logging its text would demand a registration for a pane that does not exist; the live agent it started is
        # found by the live-agent scan.
        for command in ('spawn() { herdr agent start "$1" --pane "$2"; }; spawn w1 p1',
                        'herdr agent start $name --pane $pane', 'herdr agent start w1 --pane "${PANE}"',
                        'herdr agent start w1 --pane `herdr pane current`', 'herdr agent start "$(name)" --pane w1:p5',
                        'herdr --session "$server" agent start w1 --pane w1:p5',
                        'herdr --session=$server agent start w1 --pane w1:p5',
                        'HERDR_SOCKET_PATH="$sock" herdr agent start w1 --pane w1:p5',
                        'env HERDR_SOCKET_PATH=`pwd`/herdr.sock herdr agent start w1 --pane w1:p5'):
            self.launch(command)
            self.assertEqual(self.logged(), [], command)
        self.launch('herdr agent start w1 --kind codex --pane w1:p5')
        self.assertEqual([record['agent'] for record in self.logged()], ['w1'])
        self.serve(agents=[self.agent('real-1', 'w1:p6', self.worktree)])
        self.assertEqual(self.check(session='host-1'), [self.gap('w1', 'w1:p5'),
                                                        self.live('real-1', 'w1:p6', self.worktree)])

    def test_logging_never_blocks_the_command_even_when_herdr_or_the_log_fails(self):
        # Rule 1: a hung Herdr still leaves a record with the cwd unknown; an unwritable log only loses the record.
        self.serve(hang=True)
        started = time.monotonic()
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5')
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual([{key: value for key, value in record.items() if key != 'time'} for record in self.logged()],
                         [{'agent': 'builder-1', 'pane': 'w1:p5', 'herdr_session': 'default',
                           'orchestrator': {'pane': 'w1:p1', 'session': 'host-1'}}])
        (self.repo / '.scratch/herdr-launches.jsonl').unlink()
        (self.repo / '.scratch/herdr-launches.jsonl').mkdir()
        self.serve()
        self.launch('herdr agent start builder-2 --kind codex --pane w1:p5')

    @unittest.skipUnless(shutil.which('node'), 'Node.js runs the Pi extension')
    def test_pi_logs_launches_from_bash_and_bg_run_without_blocking_them(self):
        # Rule 1 on Pi: its tool_call covers bash and bg_run; the orchestrator's session is Pi's session ID.
        verdicts = self.pi("""
const call = (toolName, command) => handlers.tool_call({ type: 'tool_call', toolCallId: toolName, toolName,
  input: { command, name: 'launch' } }, ctx);
console.log(JSON.stringify([await call('bash', 'herdr agent start builder-1 --kind codex --pane w1:p5'),
  await call('bg_run', 'herdr agent start builder-2 --kind pi --pane w1:p6')]));
""")
        self.assertEqual(verdicts, [None, None])
        self.assertEqual([{key: value for key, value in record.items() if key != 'time'} for record in self.logged()], [
            {'agent': 'builder-1', 'pane': 'w1:p5', 'herdr_session': 'default',
             'orchestrator': {'pane': None, 'session': 'pi-1'}, 'cwd': str(self.worktree)},
            {'agent': 'builder-2', 'pane': 'w1:p6', 'herdr_session': 'default',
             'orchestrator': {'pane': None, 'session': 'pi-1'}}])

    def test_an_unregistered_launch_into_a_worktree_holds_its_orchestrator_after_the_pane_closed(self):
        # Rule 2: the log outlives the panes; an attempt whose worker is the pane or the agent name clears the gap.
        self.serve(panes={'default': {pane: {'pane_id': pane, 'cwd': str(self.worktree)} for pane in ('w1:p5', 'w1:p6')}})
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', pane=None)
        self.launch('herdr agent start builder-2 --kind codex --pane w1:p6', pane=None)
        self.serve()  # Both panes closed: Herdr no longer reports them.
        missing = [self.gap('builder-1', 'w1:p5'), self.gap('builder-2', 'w1:p6')]
        self.assertEqual(self.check(pane=None, session='host-1'), missing)
        for host in ('claude', 'codex'):
            decision = self.stop(pane=None, session='host-1', host=host)
            self.assertEqual(decision['decision'], 'block')
            self.assertIn(''.join(f'- {gap}\n' for gap in missing), decision['reason'])
            self.assertIn('workers/prepare.md', decision['reason'])
        self.register('w1:p5', 'ticket-1/1', pane=None)
        self.assertEqual(self.check(pane=None, session='host-1'), [self.gap('builder-2', 'w1:p6')])
        self.register('builder-2', 'ticket-2/1', pane=None)
        self.assertEqual(self.check(pane=None, session='host-1'), [])
        self.assertIsNone(self.stop(pane=None, session='host-1'))

    def dismiss(self, *args, pane=None):
        """Run the launch hook's `dismiss` command from the ticket worktree, in Herdr pane `pane` or outside Herdr."""
        return subprocess.run([sys.executable, str(LAUNCH), 'dismiss', *args], cwd=self.worktree, capture_output=True,
                              text=True, env=self.env(pane), timeout=20)

    def test_a_dismissal_clears_only_the_launches_of_the_orchestrator_that_dismissed(self):
        # Review 2: a dismissal records who dismissed (pane and host session) and accounts only for that
        # orchestrator's launches; with no identity it could account for nothing, so it is refused.
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', pane=None, session='host-1')
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p6', pane=None, session='host-2')
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p7', pane='w1:p1', session='host-3')
        self.serve()
        self.assertIn(f'herdr_launch.py dismiss --pane <pane> --session host-1 --reason <why>',
                      self.stop(pane=None, session='host-1')['reason'])
        self.assertEqual(self.dismiss('--name', 'builder-1', '--session', 'host-2', '--reason', 'probe').returncode, 0)
        self.assertEqual(self.logged()[-1]['by'], {'pane': None, 'session': 'host-2'})
        self.assertEqual(self.dismiss('--pane', 'w1:p7', '--reason', 'probe', pane='w1:p1').returncode, 0)
        self.assertEqual(self.check(pane=None, session='host-1'), [self.gap('builder-1', 'w1:p5')])
        self.assertEqual(self.check(pane=None, session='host-2'), [])
        self.assertEqual(self.check(session='host-3'), [])
        refused = self.dismiss('--name', 'builder-1', '--reason', 'probe')
        self.assertEqual(refused.returncode, 2)
        self.assertIn('--session', refused.stderr)

    def test_a_dismissal_also_accounts_for_a_live_agent_of_the_orchestrator_that_dismissed(self):
        # Review 3: a live agent that needs no attempt (a probe left running) is dismissed the same way.
        self.serve(agents=[self.agent('probe-1', 'w1:p5', self.worktree), self.agent('probe-2', 'w1:p6', self.worktree)])
        self.assertEqual(self.dismiss('--name', 'probe-1', '--reason', 'probe', pane='w1:p1').returncode, 0)
        self.assertEqual(self.dismiss('--pane', 'w1:p6', '--reason', 'probe', pane='w1:p9').returncode, 0)
        self.assertEqual(self.check(session='host-1'), [self.live('probe-2', 'w1:p6', self.worktree)])

    def test_a_dismissal_with_a_reason_accounts_for_a_launch_logged_before_it(self):
        # Decision 1: a failed, denied or probe launch stays a gap until a dismissal with a reason accounts for it;
        # a later launch of the same pane or name is a new launch.
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', pane=None)
        self.launch('herdr agent start builder-2 --kind codex --pane w1:p6', pane=None)
        self.serve()
        self.assertIn('herdr_launch.py dismiss', self.stop(pane=None, session='host-1')['reason'])
        result = self.dismiss('--pane', 'w1:p5', '--session', 'host-1', '--reason',
                              'agent_not_ready; the user closed the pane')
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertEqual(self.check(pane=None, session='host-1'), [self.gap('builder-2', 'w1:p6')])
        self.assertEqual(self.dismiss('--name', 'builder-2', '--session', 'host-1', '--reason', 'probe').returncode, 0)
        self.assertEqual(self.check(pane=None, session='host-1'), [])
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', pane=None)
        self.assertEqual(self.check(pane=None, session='host-1'), [self.gap('builder-1', 'w1:p5')])

    def test_a_launch_without_a_server_option_ran_in_its_shells_server_and_only_that_servers_registration_clears_it(self):
        # Review 1: no `--session` means the server the launching shell talks to (its HERDR_SOCKET_PATH names it), not
        # a wildcard: a handle on another server clears neither the launch nor hides the live agent of the same pane.
        s2 = {'HERDR_SOCKET_PATH': '/home/u/.config/herdr/sessions/s2/herdr.sock'}
        self.launch('herdr agent start alpha --kind codex --pane w1:p5', pane=None)
        self.launch('herdr agent start beta --kind codex --pane w1:p6', pane=None, env=s2)
        self.assertEqual([record['herdr_session'] for record in self.logged()], ['default', 's2'])
        self.serve()
        self.register('s2/w1:p5/x', 'ticket-1/1', pane=None)
        self.register('default/w1:p6/y', 'ticket-2/1', pane=None)
        with (self.repo / '.scratch/herdr-launches.jsonl').open('a') as stream:  # A line that names no server.
            stream.write(json.dumps({'time': '2026-10-20T10:00:00Z', 'agent': 'gamma', 'pane': 'w1:p7',
                                     'herdr_session': None, 'orchestrator': {'pane': None, 'session': 'host-1'}}) + '\n')
        self.register('s2/w1:p7/z', 'ticket-7/1', pane=None)
        gaps = [self.gap('alpha', 'w1:p5'), self.gap('beta', 'w1:p6'), self.gap('gamma', 'w1:p7')]
        self.assertEqual(self.check(pane=None, session='host-1'), gaps)
        self.register('default/w1:p5/x', 'ticket-3/1', pane=None)
        self.register('s2/w1:p6/y', 'ticket-4/1', pane=None)
        self.register('default/w1:p7/z', 'ticket-8/1', pane=None)
        self.assertEqual(self.check(pane=None, session='host-1'), [])
        live = self.agent('live-1', 'w1:p9', self.worktree)
        self.serve(agents=[live])
        self.register('s2/w1:p9/other', 'ticket-5/1', pane='w1:p3', session='host-2')
        self.assertEqual(self.check(session='host-1'), [self.live('live-1', 'w1:p9', self.worktree)])
        self.assertEqual(self.check(session='host-1', env=s2), [])
        self.register('default/w1:p9/other', 'ticket-6/1', pane='w1:p3', session='host-2')
        self.assertEqual(self.check(session='host-1'), [])

    def test_a_command_local_socket_path_names_the_server_a_launch_ran_in(self):
        # Review 2: `HERDR_SOCKET_PATH=<s2 socket> herdr agent start ...` from a default-server shell runs in s2, so
        # that is the server logged, and an s2 registration clears it; an explicit --session still wins.
        s2 = '/home/u/.config/herdr/sessions/s2/herdr.sock'
        self.launch(f'HERDR_SOCKET_PATH={s2} herdr agent start alpha --kind codex --pane w1:p5', pane=None)
        self.launch(f'env -u X HERDR_SOCKET_PATH={s2} herdr agent start beta --kind codex --pane w1:p6', pane=None)
        self.launch(f'HERDR_SOCKET_PATH={s2} herdr --session s3 agent start gamma --kind codex --pane w1:p7', pane=None)
        self.launch('herdr agent start delta --kind codex --pane w1:p8', pane=None)
        self.assertEqual([record['herdr_session'] for record in self.logged()], ['s2', 's2', 's3', 'default'])
        self.serve()
        self.register('s2/w1:p5/alpha', 'ticket-1/1', pane=None)
        self.register('s2/w1:p6/beta', 'ticket-2/1', pane=None)
        self.assertEqual(self.check(pane=None, session='host-1'),
                         [self.gap('gamma', 'w1:p7'), self.gap('delta', 'w1:p8')])

    def test_the_server_a_launch_ran_in_follows_assignments_into_shell_strings_and_env_resets(self):
        # Review 3: an assignment before `bash -c '...'` reaches the launch inside it; `env -u HERDR_SOCKET_PATH`, `env -i`
        # and an empty `HERDR_SOCKET_PATH=` run it on the default server whatever the logger's own environment names.
        sock = lambda name: f'/home/u/.config/herdr/sessions/{name}/herdr.sock'
        shell = f"HERDR_SOCKET_PATH={sock('s2')} bash -c 'herdr agent start a%d --kind codex --pane w1:p%d'"
        cases = [(shell, 's2'),
                 (f"HERDR_SOCKET_PATH={sock('s2')} bash -c \"HERDR_SOCKET_PATH={sock('s3')} herdr agent start a%d --pane w1:p%d\"", 's3'),
                 (f"HERDR_SOCKET_PATH={sock('s2')} env -u HERDR_SOCKET_PATH herdr agent start a%d --pane w1:p%d", 'default'),
                 (f"HERDR_SOCKET_PATH={sock('s2')} env -i herdr agent start a%d --pane w1:p%d", 'default'),
                 (f"HERDR_SOCKET_PATH={sock('s2')} env HERDR_SOCKET_PATH= herdr agent start a%d --pane w1:p%d", 'default'),
                 (f"env -i HERDR_SOCKET_PATH={sock('s2')} herdr agent start a%d --pane w1:p%d", 's2'),
                 (f"HERDR_SOCKET_PATH={sock('s2')} sh -c 'env -u HERDR_SOCKET_PATH herdr agent start a%d --pane w1:p%d'", 'default'),
                 (f"HERDR_SOCKET_PATH={sock('s2')} env -u OTHER herdr agent start a%d --pane w1:p%d", 's2'),
                 (f"HERDR_SOCKET_PATH={sock('s2')} bash -c 'herdr agent start x%d --pane w1:p8'; "
                  "herdr agent start a%d --pane w1:p%d", 's4'),
                 # A shell the command starts keeps its own changes: the launch beside it runs on the logger's server.
                 ("bash -c 'unset HERDR_SOCKET_PATH'; herdr agent start a%d --pane w1:p%d", 's4'),
                 # Review 6: combined short options and the long unset form of env.
                 (f"HERDR_SOCKET_PATH={sock('s2')} env -iv herdr agent start a%d --pane w1:p%d", 'default'),
                 (f"HERDR_SOCKET_PATH={sock('s2')} env --unset=HERDR_SOCKET_PATH herdr agent start a%d --pane w1:p%d",
                  'default')]
        for number, (command, _) in enumerate(cases):
            self.launch(command.replace('%d', str(number)), pane=None, env={'HERDR_SOCKET_PATH': sock('s4')})
        self.assertEqual([record['herdr_session'] for record in self.logged() if not record['agent'].startswith('x')],
                         [server for _, server in cases])

    def test_a_launch_beside_a_statement_that_changes_the_socket_is_not_logged(self):
        # Review 4 and 5: a body's own assignments, exports, unsets or bare `env` may change the server for the launches
        # beside it in ways a reading of the text cannot settle (subshells, loops that never run, unexported variables),
        # so those launches are not logged; the stop check's live-agent scan still names a real one.
        sock = lambda name: f'/home/u/.config/herdr/sessions/{name}/herdr.sock'
        for command in (f"HERDR_SOCKET_PATH={sock('s2')} bash -c 'unset HERDR_SOCKET_PATH; herdr agent start a --pane w1:p5'",
                        f"bash -c 'export HERDR_SOCKET_PATH={sock('s3')}; herdr agent start a --pane w1:p5'",
                        f"HERDR_SOCKET_PATH={sock('s2')}; herdr agent start a --pane w1:p5",
                        f"HERDR_SOCKET_PATH={sock('s2')} bash -c '(\n  unset HERDR_SOCKET_PATH\n)\nherdr agent start a --pane w1:p5'",
                        f"HERDR_SOCKET_PATH={sock('s2')} bash -c 'while false; do unset HERDR_SOCKET_PATH; done\n"
                        "herdr agent start a --pane w1:p5'",
                        f"env -u HERDR_SOCKET_PATH bash -c 'HERDR_SOCKET_PATH={sock('s3')}\nherdr agent start a --pane w1:p5'",
                        f"HERDR_SOCKET_PATH={sock('s2')} bash -c 'env -u HERDR_SOCKET_PATH\nherdr agent start a --pane w1:p5'",
                        # Review 6: a name the shell expands still names the variable.
                        f"HERDR_SOCKET_PATH={sock('s2')} bash -c 'unset HERDR_SOCKET_{{PATH,UNUSED}}; herdr agent start a --pane w1:p5'"):
            with self.subTest(command=command):
                before = len(self.logged())
                self.launch(command, pane=None, env={'HERDR_SOCKET_PATH': sock('s4')})
                self.assertEqual(before, len(self.logged()))

    def test_matching_stays_linear_when_one_name_is_registered_by_many_sessions(self):
        # Review 1: sixteen thousand `builder` registrations from other sessions all landed in one bucket that every
        # `builder` launch scanned; attempts are indexed by server, pane or name, and session.
        count = 12000
        rows = [dict(attempt=f'run/{number}', assignment='run/01', repo=str(self.repo), runtime='claude',
                     native_session=f'n-{number}', worker='builder', session=f'other-{number}', orchestrator='w1:p3',
                     owned=True, result={'status': 'completed'}) for number in range(count)]
        rows += [dict(attempt=f'mine/{number}', assignment='run/01', repo=str(self.repo), runtime='claude',
                      native_session=f'm-{number}', worker=f'w1:p{number}', session='host-1', orchestrator='w1:p1',
                      owned=True, result={'status': 'completed'}) for number in range(count)]
        self.write_rows(*rows)
        (self.repo / '.scratch/herdr-launches.jsonl').write_text(''.join(json.dumps({
            'time': '2026-10-20T10:00:00Z', 'agent': 'builder', 'pane': f'w1:p{number}', 'herdr_session': 'default',
            'orchestrator': {'pane': 'w1:p1', 'session': 'host-1'}, 'cwd': str(self.worktree)}) + '\n'
            for number in range(count)))
        started = time.monotonic()
        self.assertEqual(self.check(session='host-1'), [])
        self.assertLess(time.monotonic() - started, 10)

    def test_a_registration_with_a_placement_hides_a_live_agent_only_in_its_server_and_tab(self):
        # Review 2: a bare name registered on another Herdr server (or in another tab) is not the default server's
        # live agent of that name.
        live = self.agent('builder', 'w1:p9', self.worktree)  # Tab w1:t9 of the stopping session's server.
        self.serve(agents=[live])
        def row(server, tab):
            return dict(attempt='run/1', assignment='run/01', repo=str(self.repo), runtime='claude',
                        native_session='n-1', worker='builder', session='host-1', orchestrator='w1:p1', owned=True,
                        result={'status': 'completed'},
                        herdr={'session': server, 'workspace_id': 'w1', 'tab_id': tab})
        expected = [self.live('builder', 'w1:p9', self.worktree)]
        for server, tab in (('s2', 'w1:t9'), ('default', 'w1:t3')):
            self.write_rows(row(server, tab))
            self.assertEqual(self.check(session='host-1'), expected, (server, tab))
        self.write_rows(row('default', 'w1:t9'))
        self.assertEqual(self.check(session='host-1'), [])

    def test_matching_stays_linear_for_ownerless_registrations_of_one_name(self):
        # Review 2: ownerless rows (no host session, no orchestrator pane) of one name shared a bucket that every later
        # launch scanned before rejecting it by time; one registered after the launch clears it, however many are older.
        count = 12000
        rows = [dict(attempt=f'run/{number}', assignment='run/01', repo=str(self.repo), runtime='claude',
                     native_session=f'n-{number}', worker='builder', owned=True, result={'status': 'completed'})
                for number in range(count)]
        rows.append(dict(rows[0], attempt='run/other', native_session='n-o', worker='other'))
        self.write_rows(*rows)
        db = sqlite3.connect(self.state)
        # Every row registered a day before the launches, except the last `builder`, a day after.
        before, after = calendar.timegm((2026, 10, 19, 10, 0, 0)), calendar.timegm((2026, 10, 21, 10, 0, 0))
        db.executemany('INSERT INTO events (attempt, kind, payload, acknowledged) VALUES (?, ?, ?, 0)',
                       [(row['attempt'], 'register',
                         json.dumps({'recorded_at': after if row['attempt'] == f'run/{count - 1}' else before}))
                        for row in rows])
        db.commit()
        db.close()
        launch = lambda agent, pane: json.dumps({
            'time': '2026-10-20T10:00:00Z', 'agent': agent, 'pane': pane, 'herdr_session': 'default',
            'orchestrator': {'pane': None, 'session': 'host-1'}, 'cwd': str(self.worktree)}) + '\n'
        (self.repo / '.scratch/herdr-launches.jsonl').write_text(
            ''.join(launch('builder', f'w1:p{number}') for number in range(count)) + launch('other', 'w1:p99999'))
        started = time.monotonic()
        self.assertEqual(self.check(pane=None, session='host-1'), [self.gap('other', 'w1:p99999')])
        self.assertLess(time.monotonic() - started, 10)

    def test_an_opencode_launch_logged_with_its_session_and_no_pane_is_cleared_by_that_sessions_registration(self):
        # Gap 20: OpenCode exports no Herdr pane, so its launches are logged under the host session alone; the attempt
        # its OPENCODE_SESSION_ID registration records (session and sessions, no orchestrator pane) clears them, and
        # only that session's. Written against the record's shape, which T1's registration produces.
        self.launch('herdr agent start oc-builder --kind codex --pane w1:p5', pane=None, session='oc-1')
        self.launch('herdr agent start oc-other --kind codex --pane w1:p6', pane=None, session='oc-2')
        self.assertEqual(self.logged()[0]['orchestrator'], {'pane': None, 'session': 'oc-1'})
        self.serve()
        self.assertEqual(self.check(pane=None, session='oc-1'), [self.gap('oc-builder', 'w1:p5')])
        self.write_rows(dict(attempt='ticket-1/1', assignment='ticket-1', repo=str(self.repo), runtime='opencode',
                             native_session='n-1', worker='oc-builder', session='oc-1', sessions=['oc-1'], owned=True,
                             result={'status': 'completed'}))
        self.assertEqual(self.check(pane=None, session='oc-1'), [])
        self.assertEqual(self.check(pane=None, session='oc-2'), [self.gap('oc-other', 'w1:p6')])

    def test_matching_launches_to_attempts_stays_well_inside_the_hook_timeout_for_a_long_history(self):
        # Audit A11: sixteen thousand registered launches took about 29 s to match against as many attempts; the
        # hook's timeout is 20 s, so a long-lived checkout stopped without a check. Matching is by identity, not a scan.
        count = 16000
        self.write_rows(*(dict(attempt=f'run/{number}', assignment='run/01', repo=str(self.repo), runtime='claude',
                               native_session=f'n-{number}', worker=f'builder-{number}', session='host-1',
                               orchestrator='w1:p1', owned=True, result={'status': 'completed'})
                          for number in range(count)))
        log = self.repo / '.scratch/herdr-launches.jsonl'
        log.write_text(''.join(json.dumps({
            'time': '2026-10-20T10:00:00Z', 'agent': f'builder-{number}', 'pane': f'w1:p{number}', 'herdr_session': None,
            'orchestrator': {'pane': 'w1:p1', 'session': 'host-1'}, 'cwd': str(self.worktree)}) + '\n'
            for number in range(count)) + json.dumps({
                'time': '2026-10-20T10:00:00Z', 'agent': 'unregistered', 'pane': 'w1:p99999', 'herdr_session': None,
                'orchestrator': {'pane': 'w1:p1', 'session': 'host-1'}, 'cwd': str(self.worktree)}) + '\n')
        started = time.monotonic()
        self.assertEqual(self.check(session='host-1'), [self.gap('unregistered', 'w1:p99999')])
        self.assertLess(time.monotonic() - started, 10)

    def test_a_dismissed_launch_whose_pane_is_still_open_holds_its_session_until_it_is_closed_or_kept(self):
        # Gap 15: a probe or failed launch dismissed but left open is still a worker nobody accounts for; closing the
        # pane or recording it kept on purpose (`--keep <reason>`) ends the hold, and only the dismissing session is held.
        open_pane = 'w1:p5: dismissed launch is still open; close it, or dismiss with --keep <reason>'
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', pane=None, session='host-1')
        self.launch('herdr agent start builder-2 --kind codex --pane w1:p6', pane=None, session='host-2')
        self.serve(panes={'default': {pane: {'pane_id': pane, 'cwd': str(self.worktree)} for pane in ('w1:p5', 'w1:p6')}})
        self.assertEqual(self.dismiss('--pane', 'w1:p5', '--session', 'host-1', '--reason', 'probe').returncode, 0)
        self.assertEqual(self.check(pane=None, session='host-1'), [open_pane])
        self.assertIn(open_pane, self.stop(pane=None, session='host-1')['reason'])
        self.assertEqual(self.check(pane=None, session='host-2'), [self.gap('builder-2', 'w1:p6')],
                         'the other session is held for its own launch only')
        self.serve(panes={'default': {'w1:p6': {'pane_id': 'w1:p6', 'cwd': str(self.worktree)}}})  # p5 closed.
        self.assertEqual(self.check(pane=None, session='host-1'), [])
        self.serve(panes={'default': {'w1:p5': {'pane_id': 'w1:p5', 'cwd': str(self.worktree)}}})  # Open again.
        self.assertEqual(self.check(pane=None, session='host-1'), [open_pane])
        for reason in ('', '  '):
            refused = self.dismiss('--pane', 'w1:p5', '--session', 'host-1', '--keep', reason)
            self.assertEqual(refused.returncode, 2, reason)
            self.assertIn('--keep', refused.stderr)
        both = self.dismiss('--pane', 'w1:p5', '--session', 'host-1', '--reason', 'probe', '--keep', 'user asked')
        self.assertEqual(both.returncode, 2)
        kept = self.dismiss('--pane', 'w1:p5', '--session', 'host-1', '--keep', 'the user reads its output')
        self.assertEqual((kept.returncode, kept.stderr), (0, ''))
        self.assertEqual(self.check(pane=None, session='host-1'), [], 'a pane kept on purpose never holds')
        self.launch('herdr agent start builder-4 --kind codex --pane w1:p8', pane=None, session='host-1')
        self.serve(panes={'default': {pane: {'pane_id': pane, 'cwd': str(self.worktree)} for pane in ('w1:p5', 'w1:p8')}})
        self.assertEqual(self.dismiss('--pane', 'w1:p8', '--session', 'host-1', '--keep', 'kept from the start').returncode, 0)
        self.assertEqual(self.check(pane=None, session='host-1'), [], 'kept at its first dismissal, it never holds')
        self.serve(fail='connection refused')  # Whether the pane is open cannot be told: named, never a pass.
        self.launch('herdr agent start builder-3 --kind codex --pane w1:p7', pane=None, session='host-1')
        self.assertEqual(self.dismiss('--pane', 'w1:p7', '--session', 'host-1', '--reason', 'probe').returncode, 0)
        [failure] = self.check(pane=None, session='host-1')
        self.assertRegex(failure, r'^could not check Herdr workers: herdr pane get exited 1: .*connection refused')

    def test_a_dismissal_without_a_reason_is_refused_and_changes_nothing(self):
        # Decision 1: accounting for a launch needs a stated reason.
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', pane=None)
        logged = self.logged()
        for reason in ([], ['--reason', ''], ['--reason', '  ']):
            result = self.dismiss('--pane', 'w1:p5', '--session', 'host-1', *reason)
            self.assertEqual(result.returncode, 2, reason)
            self.assertIn('--reason', result.stderr)
        self.assertEqual(self.logged(), logged)
        self.assertEqual(self.check(pane=None, session='host-1'), [self.gap('builder-1', 'w1:p5')])

    def test_only_the_launching_pane_clears_a_herdr_launch_and_a_later_registration_only_one_outside_herdr(self):
        # Re-review 2: a launch made inside Herdr is cleared only by an attempt whose recorded orchestrator is that
        # pane; an attempt with no recorded orchestrator, registered at or after it, clears only a launch made outside
        # Herdr. Review probes: another run's old registration, and an unrelated later one outside Herdr.
        def run(slug):
            (self.repo / f'.scratch/{slug}/reports').mkdir(parents=True, exist_ok=True)
            return self.repo / f'.scratch/{slug}/reports/workers.sqlite'
        self.state = run('old-run')
        self.register('builder-1', 'old-1/1', pane='w1:p9')
        self.register('builder-6', 'old-6/1', pane=None)
        time.sleep(1.1)  # The log keeps whole seconds: the next launches come strictly later.
        for index in range(1, 5):
            self.launch(f'herdr agent start builder-{index} --kind codex --pane w1:p{index + 4}')
        for index in (5, 6):
            self.launch(f'herdr agent start builder-{index} --kind codex --pane w1:p{index + 4}', pane=None)
        self.state = run('workstream')
        self.register('builder-2', 'ticket-2/1', pane='w1:p9')
        self.register('builder-3', 'ticket-3/1', pane='w1:p1')
        self.register('builder-5', 'ticket-5/1', pane=None)
        self.state = run('other-run')
        self.register('builder-4', 'other-4/1', native_session='other-native', pane=None)
        self.serve()
        # Every launch here is host-1's, whose session alone names it, in whatever pane it stops.
        missing = [self.gap(f'builder-{index}', f'w1:p{index + 4}') for index in (1, 2, 4, 6)]
        self.assertEqual(self.check(session='host-1'), missing)
        self.assertEqual(self.check(pane=None, session='host-1'), missing)

    def test_a_registration_from_another_host_session_does_not_clear_a_launch(self):
        # Rule (T11): a registration records its host session, and where it and the launch's orchestrator both know
        # one, the session alone decides whether it accounts for the launch, as for whose launch it is (T10). Review
        # probes: a later registration from another session, in the launching pane or outside Herdr.
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', pane='w1:p1', session='host-1')
        self.launch('herdr agent start builder-2 --kind codex --pane w1:p6', pane=None, session='host-1')
        self.launch('herdr agent start builder-3 --kind codex --pane w1:p7', pane='w1:p17', session='host-1')
        self.launch('herdr agent start builder-4 --kind codex --pane w1:p8', pane='w1:p1', session='host-1')
        self.serve()
        time.sleep(1.1)  # The log keeps whole seconds: the registrations come strictly later.
        self.register('builder-1', 'ticket-1/1', pane='w1:p1', session='host-2')
        self.register('builder-2', 'ticket-2/1', pane=None, session='host-2')
        self.register('builder-3', 'ticket-3/1', pane='w2:p1', session='host-1')  # A stale pane: the session decides.
        self.register('builder-4', 'ticket-4/1', pane='w1:p1', session='host-1')
        missing = [self.gap('builder-1', 'w1:p5'), self.gap('builder-2', 'w1:p6')]
        self.assertEqual(self.check(session='host-1'), missing)
        self.assertEqual(self.check(pane=None, session='host-1'), missing)

    def test_a_session_pane_name_handle_clears_only_the_launch_of_its_pane_and_server(self):
        # Decision 2, audit A3: the lifecycle also accepts `<session>/<pane>/<name>` as a worker handle, and it names
        # one pane of one Herdr server: a name another handle shares, or a pane of another server, clears nothing.
        self.serve(panes={'default': {pane: {'pane_id': pane, 'cwd': str(self.worktree)}
                                      for pane in ('w1:p5', 'w1:p6', 'w1:p7')}})
        for agent, pane in (('builder-1', 'w1:p5'), ('builder-2', 'w1:p6'), ('builder-3', 'w1:p7')):
            self.launch(f'herdr agent start {agent} --kind codex --pane {pane}', pane=None)
        self.serve()
        self.register('default/w1:p5/renamed-1', 'ticket-1/1', pane=None)
        self.register('s2/w1:p9/builder-2', 'ticket-2/1', pane=None)
        self.register('default/w1:p8/builder-9', 'ticket-3/1', pane=None)
        self.assertEqual(self.check(pane=None, session='host-1'),
                         [self.gap('builder-2', 'w1:p6'), self.gap('builder-3', 'w1:p7')])

    def test_a_handle_clears_only_the_launch_in_its_own_herdr_server(self):
        # Audit A3: the launch log keeps the Herdr server a launch ran in, so `s1/w1:p5/alpha` does not also clear an
        # unrelated launch in pane w1:p5 of server s2.
        panes = {server: {'w1:p5': {'pane_id': 'w1:p5', 'cwd': str(self.worktree)}} for server in ('s1', 's2')}
        self.serve(panes=panes)
        self.launch('herdr --session s1 agent start alpha --kind codex --pane w1:p5', pane=None)
        self.launch('herdr --session=s2 agent start beta --kind codex --pane w1:p5', pane=None)
        self.launch('herdr agent start gamma --kind codex --pane w1:p6', pane=None)
        self.assertEqual([record['herdr_session'] for record in self.logged()], ['s1', 's2', 'default'])
        self.serve()
        self.register('s1/w1:p5/alpha', 'ticket-1/1', pane=None)
        self.assertEqual(self.check(pane=None, session='host-1'),
                         [self.gap('beta', 'w1:p5'), self.gap('gamma', 'w1:p6')])
        self.register('s2/w1:p5/beta', 'ticket-2/1', pane=None)
        self.assertEqual(self.check(pane=None, session='host-1'), [self.gap('gamma', 'w1:p6')])

    def test_another_sessions_attempt_hides_a_live_agent_only_by_its_pane_or_its_logged_name_and_pane(self):
        # Audit A3: a name is reused freely, so another session's registered `build-1` does not account for a different
        # pane's `build-1`; this session's own attempt does by name, and the pane or the launch log ties a name to its pane.
        agents = [self.agent('build-1', 'w1:p9', self.worktree), self.agent('build-2', 'w1:p8', self.worktree),
                  self.agent('build-3', 'w1:p7', self.worktree), self.agent('build-4', 'w1:p6', self.worktree)]
        self.serve(agents=agents)
        self.register('build-1', 'ticket-1/1', pane='w1:p3', session='host-2')  # Another session's, name only.
        self.assertEqual(self.check(session='host-1'), [self.live(agent['name'], agent['pane_id'], self.worktree)
                                                        for agent in agents])
        self.register('build-2', 'ticket-2/1', pane='w1:p1', session='host-1')  # Its own.
        self.register('w1:p7', 'ticket-3/1', pane='w1:p3', session='host-2')  # Another's, by pane.
        self.launch('herdr agent start build-4 --kind codex --pane w1:p6', pane='w1:p3', session='host-2')
        self.register('build-4', 'ticket-4/1', pane='w1:p3', session='host-2')  # Another's, with a logged pane.
        self.assertEqual(self.check(session='host-1'), [self.live('build-1', 'w1:p9', self.worktree)])

    def test_launches_outside_worktrees_are_not_gaps_but_an_unknown_cwd_is(self):
        # Rule 2: E2E subjects and demos run in panes outside .worktrees/; a cwd Herdr did not report still counts.
        demo = Path(self.tmp.name) / 'demo'
        demo.mkdir()
        cwds = {'w1:p5': demo, 'w1:p6': self.repo, 'w1:p8': self.repo / '.worktrees-archive/ticket-1'}
        self.serve(panes={'default': {pane: {'pane_id': pane, 'cwd': str(cwd)} for pane, cwd in cwds.items()}})
        for agent, pane in (('demo-1', 'w1:p5'), ('e2e-1', 'w1:p6'), ('helper-1', 'w1:p7'), ('old-1', 'w1:p8')):
            self.launch(f'herdr agent start {agent} --kind codex --pane {pane}', pane=None)
        self.serve()
        self.assertEqual(self.check(pane=None, session='host-1'), [self.gap('helper-1', 'w1:p7')])

    def test_only_the_orchestrator_that_launched_a_worker_is_held_for_it(self):
        # Rule 2: a launch belongs to the host session that ran it, else to its Herdr pane; no identity, no owner.
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', pane='w1:p1', session='host-1')
        self.launch('herdr agent start builder-2 --kind codex --pane w1:p5', pane=None, session='host-1')
        self.launch('herdr agent start builder-3 --kind codex --pane w1:p5', pane=None, session=None)
        self.serve()
        both = [self.gap('builder-1', 'w1:p5'), self.gap('builder-2', 'w1:p5')]
        self.assertEqual(self.check(pane='w1:p1'), [self.gap('builder-1', 'w1:p5')])
        self.assertEqual(self.check(pane='w1:p9', session='host-1'), both)
        self.assertEqual(self.check(pane=None, session='host-1'), both)
        self.assertEqual(self.check(pane=None, session='host-2'), [])
        self.assertEqual(self.check(pane=None), [])

    def test_a_reused_pane_with_another_host_session_inherits_no_launches(self):
        # When the launch and the stopping session both carry a host session, it alone decides (T10, replacing
        # pane and session both); the pane decides only when one side has no session (a restarted Herdr may reuse
        # pane ids, so another session in the reused pane inherits nothing).
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', pane='w1:p1', session='old-1')
        self.launch('herdr agent start builder-2 --kind codex --pane w1:p5', pane='w1:p1', session=None)
        self.serve()
        both = [self.gap('builder-1', 'w1:p5'), self.gap('builder-2', 'w1:p5')]
        self.assertEqual(self.check(pane='w1:p1', session='new-1'), [self.gap('builder-2', 'w1:p5')])
        self.assertEqual(self.check(pane='w1:p1', session='old-1'), both)
        self.assertEqual(self.check(pane='w1:p1'), both)
        self.assertEqual(self.check(pane='w1:p2', session='old-1'), [self.gap('builder-1', 'w1:p5')])

    def test_a_codex_launch_under_a_stale_herdr_pane_holds_the_session_that_made_it(self):
        # Rule (E4 codex): Codex runs commands through a shared app-server whose HERDR_PANE_ID came from another pane,
        # so where a host session ID is known it alone decides whose launch it is; the stale pane decides nothing.
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', pane='w1:p17', session='codex-1')
        self.serve()
        self.assertEqual(self.check(pane='w2:p1', session='codex-1'), [self.gap('builder-1', 'w1:p5')])
        self.assertEqual(self.check(pane=None, session='codex-1'), [self.gap('builder-1', 'w1:p5')])
        self.assertEqual(self.check(pane='w1:p17', session='codex-2'), [])
        for host in ('codex', 'claude'):
            self.assertEqual(self.stop(pane='w2:p1', session='codex-1', host=host)['decision'], 'block', host)

    def test_launches_and_registrations_count_however_long_ago_their_run_was_active(self):
        # Rule 2: neither the launch log nor a run's registrations age out with the gate's one-day filter.
        log, stale = self.repo / '.scratch/herdr-launches.jsonl', time.time() - 2 * 24 * 3600
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', pane=None)
        self.register('builder-1', 'ticket-1/1', pane=None)
        self.launch('herdr agent start builder-2 --kind codex --pane w1:p6', pane=None)
        self.serve()
        for path in (self.state, log):
            os.utime(path, (stale, stale))
        self.assertEqual(self.check(pane=None, session='host-1'), [self.gap('builder-2', 'w1:p6')])

    @unittest.skipUnless(shutil.which('node'), 'Node.js runs the Pi extension')
    def test_pi_holds_the_run_that_launched_an_unregistered_worker(self):
        # Rule 2 on Pi: the settle check runs as this Pi session, so the launch its tool_call logged is named.
        settled = self.pi("""
await handlers.tool_call({ type: 'tool_call', toolCallId: '1', toolName: 'bash',
  input: { command: 'herdr agent start builder-1 --kind codex --pane w1:p5' } }, ctx);
console.log(JSON.stringify(await handlers.agent_before_settle(
  { type: 'agent_before_settle', outcome: 'completed', entries: [], continue: false }, ctx) ?? null));
""")
        self.assertTrue(settled['continue'])
        self.assertIn(f"- {self.gap('builder-1', 'w1:p5')}\n", settled['entries'][0]['content'])
        self.assertIn('workers/prepare.md', settled['entries'][0]['content'])
        self.assertIn('herdr_launch.py dismiss --pane <pane> --session pi-1 --reason <why>',
                      settled['entries'][0]['content'])

    def test_a_pi_subagents_childs_launch_holds_its_parent_until_the_child_registers_it(self):
        # A background pi-subagents child is never held: its launch is its parent's, and so is its registration, so
        # the parent's stop is held for the launch until the child registers it.
        child = {'PI_SUBAGENT_CHILD': '1', 'PI_SUBAGENT_PARENT_SESSION': 'pi-parent', 'PI_SESSION_ID': 'pi-1'}
        self.pi("""
await handlers.tool_call({ type: 'tool_call', toolCallId: '1', toolName: 'bash',
  input: { command: 'herdr agent start builder-1 --kind codex --pane w1:p5' } }, ctx);
console.log('null');
""", env=child)
        self.assertEqual(self.check(pane=None, session='pi-parent'), [self.gap('builder-1', 'w1:p5')])
        self.lifecycle('register', 'ticket-1/2', None, env=child, assignment='ticket-1', repo=str(self.repo),
                       worktree=str(self.worktree), head='head-1', execution=str(self.execution), runtime='claude',
                       worker='w1:p5', native_session='native-2', owned=True,
                       packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.assertNotIn(self.gap('builder-1', 'w1:p5'), self.check(pane=None, session='pi-parent'))

    def test_a_pi_subagents_child_in_a_claude_shell_clears_its_parents_hold_by_registering(self):
        # The whole Pi tree runs in a Claude session's shell, so the child exports the outer session first; its
        # registration still clears the launch Pi logged under the parent.
        child = {'PI_SUBAGENT_CHILD': '1', 'PI_SUBAGENT_PARENT_SESSION': 'pi-parent', 'PI_SESSION_ID': 'pi-1',
                 'CLAUDE_CODE_SESSION_ID': 'claude-outer'}
        self.pi("""
await handlers.tool_call({ type: 'tool_call', toolCallId: '1', toolName: 'bash',
  input: { command: 'herdr agent start builder-1 --kind codex --pane w1:p5' } }, ctx);
console.log('null');
""", env=child)
        self.assertEqual(self.check(pane=None, session='pi-parent'), [self.gap('builder-1', 'w1:p5')])
        self.lifecycle('register', 'ticket-1/2', None, env=child, assignment='ticket-1', repo=str(self.repo),
                       worktree=str(self.worktree), head='head-1', execution=str(self.execution), runtime='claude',
                       worker='w1:p5', native_session='native-2', owned=True,
                       packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.assertEqual(self.check(pane=None, session='pi-parent'), [])

    def test_only_a_pi_subagents_child_registers_under_its_parent_session(self):
        # A Claude session a Pi child started, and a root Pi session with a parent session left in its environment,
        # each register under their own session, as their launches are logged. The Claude session inherits the
        # child's markers and PI_SESSION_ID from Pi's bash.
        child = {'PI_SUBAGENT_CHILD': '1', 'PI_SUBAGENT_PARENT_SESSION': 'pi-parent', 'PI_SESSION_ID': 'pi-child'}
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', pane=None, session='host-1')
        self.pi("""
await handlers.tool_call({ type: 'tool_call', toolCallId: '1', toolName: 'bash',
  input: { command: 'herdr agent start builder-2 --kind codex --pane w1:p6' } }, ctx);
console.log('null');
""", env={'PI_SUBAGENT_PARENT_SESSION': 'pi-stale'})
        self.launch('herdr agent start builder-3 --kind codex --pane w1:p6', pane=None, session='pi-stale')
        for attempt, worker, env in (('ticket-1/2', 'w1:p5', dict(child, CLAUDE_CODE_SESSION_ID='host-1')),
                                     ('ticket-1/3', 'w1:p6', {'PI_SUBAGENT_PARENT_SESSION': 'pi-stale',
                                                              'PI_SESSION_ID': 'pi-1'})):
            self.lifecycle('register', attempt, None, env=env, assignment='ticket-1', repo=str(self.repo),
                           worktree=str(self.worktree), head='head-1', execution=str(self.execution), runtime='claude',
                           worker=worker, native_session=f'native-{attempt}', owned=True,
                           packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.assertNotIn(self.gap('builder-1', 'w1:p5'), self.check(pane=None, session='host-1'))
        self.assertNotIn(self.gap('builder-2', 'w1:p6'), self.check(pane=None, session='pi-1'))
        # The root Pi session clears nothing the stale session it names launched.
        self.assertIn(self.gap('builder-3', 'w1:p6'), self.check(pane=None, session='pi-stale'))
        # Neither session's worker holds the Pi parent, which launched neither.
        claude = dict(child, CLAUDE_CODE_SESSION_ID='host-1')
        self.lifecycle('startup', 'ticket-1/2', None, env=claude, native=str(self.native('ticket-1/2')))
        self.lifecycle('submitted', 'ticket-1/2', None, env=claude, evidence=['native.jsonl:2:accepted prompt'])
        self.assertTrue(any('ticket-1/2' in gap for gap in self.check(pane=None, session='host-1')))
        self.assertFalse([gap for gap in self.check(pane=None, session='pi-parent') if 'ticket-1/' in gap])

    def test_a_live_agent_in_a_worktree_is_a_gap_until_an_attempt_registers_it(self):
        # Rule 3: an agent nobody logged (started by hand, or before the hook) still holds its workspace's orchestrator.
        other = self.repo / '.worktrees/ticket-2'
        self.serve(agents=[self.agent('builder-1', 'w1:p5', self.worktree), self.agent('builder-2', 'w1:p6', other)])
        self.assertEqual(self.check(), [self.live('builder-1', 'w1:p5', self.worktree),
                                        self.live('builder-2', 'w1:p6', other)])
        decision = self.stop()
        self.assertEqual(decision['decision'], 'block')
        self.assertIn(f"- {self.live('builder-1', 'w1:p5', self.worktree)}\n", decision['reason'])
        self.register('w1:p5', 'ticket-1/1')
        self.register('builder-2', 'ticket-2/1')
        self.assertEqual(self.check(), [])

    def test_a_live_agent_registered_by_any_host_session_is_accounted_for(self):
        # Rule (T11 review): a live-agent gap is an agent nobody registered, so a registration from any session
        # clears it; only a launch the orchestrator itself logged is scoped to its session. Review probes: the same
        # pane's restarted session, and a second orchestrator's worker in the same workspace.
        other = self.repo / '.worktrees/ticket-2'
        other.mkdir()
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', session='host-1')
        self.register('builder-1', 'ticket-1/1', session='host-1')
        self.launch('herdr agent start builder-2 --kind codex --pane w1:p6', pane='w1:p9', session='host-2')
        self.register('builder-2', 'ticket-2/1', pane='w1:p9', worktree=other, session='host-2')
        self.serve(agents=[self.agent('builder-1', 'w1:p5', self.worktree), self.agent('builder-2', 'w1:p6', other)])
        self.assertEqual(self.check(session='host-2'), [])
        self.assertEqual(self.check(session='host-1'), [])
        self.assertEqual(self.check(), [])

    def test_only_other_live_agents_in_the_orchestrators_workspace_and_worktrees_count(self):
        # Rule 3: other workspaces, the orchestrator's own pane and agents outside .worktrees/ are not its workers; a
        # session outside Herdr has no workspace to check; a logged launch that is still live is named once.
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5')
        self.serve(agents=[self.agent('elsewhere-1', 'w2:p5', self.worktree, workspace='w2'),
                           self.agent('orchestrator', 'w1:p1', self.worktree),
                           self.agent('e2e-1', 'w1:p7', self.repo),
                           self.agent('builder-1', 'w1:p5', self.worktree)])
        self.assertEqual(self.check(), [self.gap('builder-1', 'w1:p5')])
        # Outside Herdr only the launch host-1 logged is named, not the workspace's live agents.
        self.assertEqual(self.check(pane=None, session='host-1'), [self.gap('builder-1', 'w1:p5')])

    def test_a_live_agent_another_orchestrator_launched_holds_only_that_orchestrator(self):
        # Rule (sessions sharing a folder): an agent whose launch another session logged is that session's worker, not
        # a sibling of every orchestrator in the Herdr workspace.
        self.launch('herdr agent start panel-1 --kind codex --pane w1:p5', pane='w1:p2', session='host-2')
        self.serve(agents=[self.agent('panel-1', 'w1:p5', self.worktree)])
        self.assertEqual(self.check(session='host-1'), [])
        self.assertEqual(self.check(pane='w1:p2', session='host-2'), [self.gap('panel-1', 'w1:p5')])
        # Its orchestrator dismissing it hands it to nobody else.
        self.assertEqual(self.dismiss('--pane', 'w1:p5', '--session', 'host-2', '--reason', 'a demo pane',
                                      pane='w1:p2').returncode, 0)
        self.assertEqual(self.check(session='host-1'), [])

    def test_a_name_another_session_once_launched_does_not_hide_a_new_agent_in_another_pane(self):
        # Rule: another session's launch identifies its agent by pane, which Herdr never reuses; an agent name may be.
        self.launch('herdr agent start builder --kind codex --pane w1:p5', pane='w1:p2', session='host-2')
        self.serve(agents=[self.agent('builder', 'w1:p9', self.worktree)])
        self.assertEqual(self.check(session='host-1'), [self.live('builder', 'w1:p9', self.worktree)])

    def test_a_row_that_cannot_be_read_hides_no_gap_of_the_sessions_own_workers(self):
        # Rule: a broken row of a shared run state is left out; this session's own workers are still checked.
        self.live_attempt('ticket-1/1', 'w1:p1', 'host-1')
        db = sqlite3.connect(self.state)
        db.execute('INSERT INTO attempts VALUES (?, ?)', ('zz/1', '{not json'))
        db.commit()
        db.close()
        self.assertEqual(self.check(session='host-1'), [f'{self.state}: ticket-1/1: no armed wait'])

    def test_a_state_that_cannot_be_read_is_named_only_to_the_sessions_it_can_be_attributed_to(self):
        # Gap 10, audit A9: an unreadable state hides its owners' gaps, so it holds a session whose binding sits beside
        # it or whose pane or host session its bytes still record, however recent; any other session is not held.
        self.live_attempt('ticket-1/1', 'w1:p1', 'host-1')
        db = sqlite3.connect(self.state)
        db.execute('DROP TABLE events')
        db.commit()
        db.close()
        unreadable = self.check(session='host-1')
        self.assertEqual(len(unreadable), 1, unreadable)
        self.assertRegex(unreadable[0], rf'^{re.escape(str(self.state))}: cannot be read \(.+\); fix or remove it$')
        self.assertEqual(self.check(pane='w1:p2', session='host-2'), [])
        self.state.write_bytes(b'not a database')
        self.assertEqual(self.check(pane='w1:p2', session='host-2'), [])
        self.state.with_name('orchestrator.json').write_text(json.dumps({'pane': None, 'sessions': ['host-2']}))
        self.assertRegex(self.check(pane='w1:p2', session='host-2')[0], 'cannot be read')
        self.assertEqual(self.check(pane='w1:p3', session='host-3'), [])
        self.state.with_name('orchestrator.json').unlink()
        self.state.write_bytes(b'not a database ' + b'"orchestrator": "w1:p1"')
        self.assertRegex(self.check(session='host-1')[0], 'cannot be read')

    def test_a_launch_is_cleared_by_its_hosts_registration_under_an_inherited_outer_session(self):
        # Rule: the attempt counts every host session its registrar exported, for launches as for its gaps.
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', session='codex-inner')
        self.lifecycle('register', 'ticket-1/1', 'w1:p1',
                       env={'CLAUDE_CODE_SESSION_ID': 'claude-outer', 'CODEX_SESSION_ID': 'codex-inner'},
                       assignment='ticket-1', repo=str(self.repo), worktree=str(self.worktree), head='head-1',
                       execution=str(self.execution), runtime='claude', worker='builder-1', native_session='native-1',
                       owned=True, packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.lifecycle('startup', 'ticket-1/1', 'w1:p1', native=str(self.native('ticket-1/1')))
        self.assertEqual(self.check(session='codex-inner'), [])

    def write_rows(self, *rows, events=True):
        """A run state of hand-written attempt rows, recent, with no orchestrator binding."""
        self.state.unlink(missing_ok=True)
        db = sqlite3.connect(self.state)
        db.execute('CREATE TABLE attempts (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        if events:
            db.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, attempt TEXT NOT NULL, kind TEXT NOT NULL, '
                       'payload TEXT NOT NULL, acknowledged INTEGER NOT NULL)')
        db.executemany('INSERT INTO attempts VALUES (?, ?)', [(row['attempt'], json.dumps(row)) for row in rows])
        db.commit()
        db.close()

    def ownerless(self, attempt='e/1', **fields):
        """A live attempt with no recorded pane or host session and no wait: every session's gap unless bound."""
        return dict(attempt=attempt, assignment='e/01', repo=str(self.repo), runtime='claude', native_session='n-1',
                    worker='w-1', owned=True, submitted=True, **fields)

    def test_a_malformed_session_list_breaks_no_other_sessions_check(self):
        self.write_rows(dict(self.ownerless(), session='host-A', sessions=42))
        self.assertEqual(self.check(pane='w1:p2', session='host-B'), [])

    def test_an_unreadable_ownerless_state_with_herdr_placement_holds_no_one(self):
        # Rule: a Herdr server's session name in a row is no host session; recording no owner, the state is attributed
        # to no one, so it is named to no session.
        self.write_rows(self.ownerless(herdr={'session': 'default', 'workspace_id': 'w1', 'tab_id': 'w1:t5'}),
                        events=False)
        self.assertEqual(self.check(pane='w1:p2', session='host-A'), [])

    def test_the_ownerless_fallback_never_holds_a_marked_worker(self):
        # Rule: a session launched as a worker (ZLOOP_WORKER) answers for its own launches only, wherever it runs.
        self.write_rows(self.ownerless())
        self.assertEqual(self.check(pane='w1:p2', session='host-B'), [f'{self.state}: e/1: no armed wait'])
        self.assertEqual(self.check(pane='w1:p2', session='host-B', env={'ZLOOP_WORKER': 'e/t'}), [])
        self.write_rows(self.ownerless(), events=False)
        self.assertEqual(self.check(pane='w1:p2', session='host-B', env={'ZLOOP_WORKER': 'e/t'}), [])

    def live_attempt(self, attempt, pane, session):
        self.register(f'builder-{attempt[7]}', attempt, pane=pane, session=session)
        self.lifecycle('startup', attempt, pane, native=str(self.native(attempt)))
        self.lifecycle('submitted', attempt, pane, evidence=['native.jsonl:2:accepted prompt'])

    def test_a_workers_lifecycle_gaps_hold_only_the_session_that_registered_it(self):
        # Rule (sessions sharing a folder): another session's worker never holds this session, in Herdr or outside it.
        self.live_attempt('ticket-1/1', 'w1:p1', 'host-1')
        mine = [f'{self.state}: ticket-1/1: no armed wait']
        self.assertEqual(self.check(session='host-1'), mine)
        self.assertEqual(self.check(pane=None, session='host-1'), mine)
        self.assertEqual(self.check(pane='w1:p2', session='host-2'), [])
        self.assertEqual(self.check(pane=None, session='host-2'), [])

    def test_a_worker_registered_by_a_host_started_inside_another_holds_the_inner_host(self):
        # Rule: a host started in another host's shell inherits the outer session ID; the worker still holds the
        # session that registered it.
        self.lifecycle('register', 'ticket-1/1', None,
                       env={'CLAUDE_CODE_SESSION_ID': 'claude-outer', 'CODEX_SESSION_ID': 'codex-inner'},
                       assignment='ticket-1', repo=str(self.repo), worktree=str(self.worktree), head='head-1',
                       execution=str(self.execution), runtime='claude', worker='builder-1', native_session='native-1',
                       owned=True, packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.lifecycle('startup', 'ticket-1/1', None, native=str(self.native('ticket-1/1')))
        self.lifecycle('submitted', 'ticket-1/1', None, evidence=['native.jsonl:2:accepted prompt'])
        self.assertEqual(self.check(pane=None, session='codex-inner'), [f'{self.state}: ticket-1/1: no armed wait'])
        self.assertEqual(self.check(pane=None, session='host-2'), [])

    def test_a_worker_registered_without_a_orchestrator_holds_the_session_bound_to_its_workstream(self):
        # Rule: with no pane and no host session recorded (OpenCode exports no session ID), the workstream's bound
        # orchestrator answers for the worker; with none bound, nothing tells its owner apart, so every session that is
        # not a worker is held rather than none.
        self.live_attempt('ticket-1/1', None, None)
        self.assertEqual(self.check(pane=None, session='host-2'), [f'{self.state}: ticket-1/1: no armed wait'])
        ticket = self.repo / '.scratch/workstream/issues/01-task.md'
        ticket.parent.mkdir(parents=True)
        ticket.write_text('---\nticket: workstream/01\nstatus: ready\nblocked_by: []\ntouches: [t01]\ndevices: []\n---\n')
        self.bind(pane=None, slug='workstream', CLAUDE_CODE_SESSION_ID='host-3')
        self.assertEqual(self.check(pane=None, session='host-3'), [f'{self.state}: ticket-1/1: no armed wait'])
        self.assertEqual(self.check(pane=None, session='host-2'), [])

    def test_an_unmarked_worker_in_its_registered_worktree_is_not_held_for_the_runs_lifecycle_gaps(self):
        # Re-review 3 (main's rule, restored for the lifecycle gaps): a session whose cwd is inside a worktree an
        # attempt registered is that worker, marked or not. Probe: a native worker outside Herdr, unmarked.
        sibling = self.repo / '.worktrees/ticket-2'
        sibling.mkdir()
        for attempt, worktree in (('ticket-1/1', self.worktree), ('ticket-2/1', sibling)):
            self.register(f'builder-{attempt[7]}', attempt, worktree=worktree)
            self.lifecycle('startup', attempt, native=str(self.native(attempt)))
            self.lifecycle('submitted', attempt, evidence=['native.jsonl:2:accepted prompt'])
        self.assertEqual(self.check(cwd=self.worktree, pane=None, session='native-child'), [])
        self.assertEqual(self.check(session='host-1'),
                         [f'{self.state}: ticket-1/1: no armed wait', f'{self.state}: ticket-2/1: no armed wait'])

    def test_a_marked_worker_answers_for_its_own_launches_but_not_its_siblings_wherever_it_runs(self):
        # Rule 4: ZLOOP_WORKER skips only the live-sibling scan. Each marked session (in its worktree, at the
        # checkout root, in its orchestrator's inherited pane, outside Herdr) is held for the helper it launched,
        # even after the helper's pane closed (probe H), and never for its siblings or its orchestrator's launches.
        sibling, marked = self.repo / '.worktrees/ticket-2', {'ZLOOP_WORKER': 'workstream/ticket-1'}
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', session='coord-1')
        workers = (('w1:p5', 'builder-session', self.worktree), ('w1:p7', 'other-session', self.repo),
                   ('w1:p1', 'worker-1', self.worktree), (None, 'worker-2', self.repo))
        for pane, session, cwd in workers:
            self.launch('herdr agent start helper-1 --kind codex --pane w1:p8', cwd=cwd, pane=pane, session=session,
                        env=marked)
        self.serve(agents=[self.agent('builder-1', 'w1:p5', self.worktree), self.agent('builder-2', 'w1:p6', sibling)])
        for pane, session, cwd in workers:
            self.assertEqual(self.check(cwd=cwd, pane=pane, session=session, env=marked),
                             [self.gap('helper-1', 'w1:p8')], (pane, session))
        self.assertEqual(self.check(session='coord-1'), [self.gap('builder-1', 'w1:p5'),
                                                         self.live('builder-2', 'w1:p6', sibling)])
        if shutil.which('node'):  # Pi's settle check inherits the marker from its process environment.
            self.assertIsNone(self.pi("""
console.log(JSON.stringify((await handlers.agent_before_settle(
  { type: 'agent_before_settle', outcome: 'completed', entries: [], continue: false }, ctx)) ?? null));
""", pane='w1:p5', env=marked))

    def test_an_unmarked_session_in_a_reused_or_once_registered_pane_is_still_held(self):
        # Rule 4 (review probes): pane history says nothing about who runs there now. A launch into another Herdr
        # server's w1:p1, an old run's attempt for w1:p1 and a dismissed failed launch into w1:p7 leave the
        # orchestrators now in those panes held for their own unregistered launches and live agents.
        self.launch('herdr --session s2 agent start remote-1 --kind codex --pane w1:p1', pane=None, session='host-9')
        self.state = self.repo / '.scratch/old-run/reports/workers.sqlite'
        self.state.parent.mkdir(parents=True)
        self.register('w1:p1', 'old-1/1', pane='w1:p0')
        self.launch('herdr agent start builder-0 --kind codex --pane w1:p7', session='coord-1')
        self.assertEqual(self.dismiss('--pane', 'w1:p7', '--session', 'coord-1', '--reason', 'failed at trust dialog',
                                      pane='w1:p1').returncode, 0)
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', session='host-1')
        self.launch('herdr agent start builder-3 --kind codex --pane w1:p9', pane='w1:p7', session='coord-7')
        self.serve(agents=[self.agent('builder-2', 'w1:p6', self.worktree)])
        live = self.live('builder-2', 'w1:p6', self.worktree)
        self.assertEqual(self.check(session='host-1'), [self.gap('builder-1', 'w1:p5'), live])
        self.assertEqual(self.check(pane='w1:p7', session='coord-7'), [self.gap('builder-3', 'w1:p9'), live])

    @unittest.skipUnless(shutil.which('node'), 'Node.js runs the Pi extension')
    def test_a_pi_subagents_child_skips_the_settle_check_but_a_forked_orchestrator_does_not(self):
        # Third review 1 (probe G): pi-subagents hosts background children in a runner process marked
        # PI_SUBAGENT_CHILD=1, where they load ambient extensions such as this one; a child does not stop for its
        # orchestrator's gaps, and its own launches stay its own. /fork also sets the header's parentSession, so a
        # forked orchestrator, without the runner's marker, is still checked.
        sibling = self.repo / '.worktrees/ticket-2'
        sibling.mkdir()
        self.register('builder-1', 'ticket-1/1')
        self.lifecycle('startup', native=str(self.native('ticket-1/1')))
        self.lifecycle('submitted', evidence=['native.jsonl:2:accepted prompt'])
        self.serve(agents=[self.agent('builder-2', 'w1:p6', sibling)])
        settle = """
const forked = { ...ctx, sessionManager: { getSessionId: () => 'pi-2', getHeader: () => ({ parentSession: '/s.jsonl' }) } };
const child = { ...forked, cwd: %s, sessionManager: { getSessionId: () => 'child-1' } };
const use = process.env.PI_SUBAGENT_CHILD === '1' ? child : forked;
await handlers.tool_call({ type: 'tool_call', toolCallId: '1', toolName: 'bash',
  input: { command: 'herdr agent start helper-9 --kind codex --pane w1:p9' } }, use);
console.log(JSON.stringify((await handlers.agent_before_settle(
  { type: 'agent_before_settle', outcome: 'completed', entries: [], continue: false }, use)) ?? null));
""" % json.dumps(str(self.worktree))
        self.assertIsNone(self.pi(settle, pane='w1:p1', env={'PI_SUBAGENT_CHILD': '1', 'PI_SUBAGENT_RUNNER_PID': '1'}))
        self.assertEqual(self.logged()[-1]['orchestrator'], {'pane': 'w1:p1', 'session': 'child-1'})
        forked = self.pi(settle, pane='w1:p1')
        self.assertTrue(forked['continue'])
        self.assertIn(f'{self.state}: ticket-1/1: no armed wait', forked['entries'][0]['content'])

    def test_a_marked_worker_of_a_orchestrator_in_another_checkout_is_not_held(self):
        # Rule 4 (review probe): the orchestrator in this checkout launched and registered both workers in another
        # checkout's worktrees; neither worker has a launch or attempt there, but the marker exempts them.
        other = Path(self.tmp.name).resolve() / 'other'
        subprocess.run(['git', 'init', '-q', str(other)], check=True)
        first, second = other / '.worktrees/t1', other / '.worktrees/t2'
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        self.serve(panes={'default': {'w1:p5': {'pane_id': 'w1:p5', 'cwd': str(first)},
                                      'w1:p6': {'pane_id': 'w1:p6', 'cwd': str(second)}}})
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5; '
                    'herdr agent start builder-2 --kind codex --pane w1:p6')
        self.register('w1:p5', 'ticket-1/1')
        self.register('w1:p6', 'ticket-2/1')
        self.serve(agents=[self.agent('builder-1', 'w1:p5', first), self.agent('builder-2', 'w1:p6', second)])
        self.assertEqual(self.check(cwd=first, pane='w1:p5', session='worker-session',
                                    env={'ZLOOP_WORKER': 'workstream/ticket-1'}), [])
        self.assertEqual(self.check(session='host-1'), [])

    def test_a_orchestrator_inside_a_worktree_is_still_held(self):
        # Rule 4: an unmarked session is an orchestrator wherever it works, so cd'ing into .worktrees/ keeps both checks.
        inside = self.repo / '.worktrees/coord'
        inside.mkdir()
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5', cwd=inside)
        self.launch('herdr agent start builder-3 --kind codex --pane w1:p9', cwd=inside, pane=None, session='host-2')
        self.serve(agents=[self.agent('builder-2', 'w1:p6', self.worktree)])
        self.assertEqual(self.check(cwd=inside, session='host-1'), [self.gap('builder-1', 'w1:p5'),
                                                                    self.live('builder-2', 'w1:p6', self.worktree)])
        self.assertEqual(self.check(cwd=inside, pane=None, session='host-2'), [self.gap('builder-3', 'w1:p9')])

    def test_a_herdr_failure_or_timeout_is_a_named_gap_that_holds_once(self):
        # Rule 5: a check that cannot run is a named gap, never a pass, and Stop is still held only once.
        self.serve(fail='connection refused')
        gap = ('could not check Herdr workers: herdr agent list exited 1: '
               '{"error": {"code": "server_unavailable", "message": "connection refused"}}')
        self.assertEqual(self.check(), [gap])
        decision = self.stop()
        self.assertEqual(decision['decision'], 'block')
        self.assertIn(f'- {gap}\n', decision['reason'])
        self.assertIn('gave up holding the stop after 1 hold', self.stop(active=True)['systemMessage'])  # Held once.
        self.assertIsNone(self.stop(env={'ZLOOP_YIELD_HOOK': 'off'}))
        # A session outside Herdr has no workspace to check, so a broken Herdr is no gap there.
        self.assertEqual(self.check(pane=None, session='host-1'), [])
        self.serve(hang=True)
        [gap] = self.check()
        self.assertRegex(gap, r"^could not check Herdr workers: .*'herdr', 'agent', 'list'.* timed out after 5 seconds$")

    def test_a_check_that_could_not_run_advises_a_retry_not_registering_or_dismissing(self):
        # Registering or dismissing cannot clear an unreachable Herdr; a retry or the hook switch can.
        self.serve(fail='connection refused')
        reason = self.stop()['reason']
        self.assertIn('retry', reason)
        self.assertIn('ZLOOP_YIELD_HOOK=off', reason)
        self.assertNotIn('Register each launched worker', reason)
        self.assertNotIn('herdr_launch.py dismiss', reason)
        # With a real worker gap beside it, the worker advice stays.
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5')
        reason = self.stop(active=False)['reason']
        self.assertIn('Register each launched worker', reason)
        self.assertIn('ZLOOP_YIELD_HOOK=off', reason)

    def test_a_git_failure_is_a_named_gap_not_a_pass(self):
        # Rule 5: when git cannot name the session's checkout the gate says so; outside any repository is no checkout.
        broken = Path(self.tmp.name) / 'broken-git'
        broken.mkdir()
        (broken / 'git').write_text('#!/bin/sh\necho "fatal: unable to read index" >&2\nexit 128\n')
        (broken / 'git').chmod(0o700)
        env = {'PATH': f"{broken}{os.pathsep}{self.env()['PATH']}"}
        gap = 'could not check workers: git rev-parse exited 128: fatal: unable to read index'
        self.assertEqual(self.check(env=env), [gap])
        self.assertIn(f'- {gap}\n', self.stop(env=env)['reason'])
        self.assertNotIn('decision', self.stop(env=env, active=True))  # Held once.
        outside = Path(self.tmp.name) / 'elsewhere'
        outside.mkdir()
        self.assertEqual(self.check(cwd=outside), [])

    @unittest.skipUnless(shutil.which('node'), 'Node.js runs the Pi extension')
    def test_pi_surfaces_a_check_that_could_not_run_and_continues_once(self):
        # Rule 5 on Pi: when the yield gate itself cannot run, the settle hook names the failure and holds once, however
        # often it settles after.
        broken = Path(self.tmp.name) / 'broken-python'
        broken.mkdir()
        (broken / 'python3').write_text('#!/bin/sh\necho "python3: simulated crash" >&2\nexit 1\n')
        (broken / 'python3').chmod(0o700)
        first, repeated, third = self.pi("""
const settle = async () => (await handlers.agent_before_settle(
  { type: 'agent_before_settle', outcome: 'completed', entries: [], continue: false }, ctx)) ?? null;
console.log(JSON.stringify([await settle(), await settle(), await settle()]));
""", pane='w1:p1', env={'PATH': f"{broken}{os.pathsep}{self.env()['PATH']}"})
        self.assertTrue(first['continue'])
        self.assertRegex(first['entries'][0]['content'],
                         r'\n- could not check workers: Command failed: python3 \S+/hooks/yield_gate\.py stop .+ pi-1\n'
                         r'python3: simulated crash\n')
        self.assertIsNone(repeated)
        self.assertIsNone(third)


class DeliveryHold(GateRepo):
    """Dispatch binds the session that orchestrates a workstream; that session alone is held when it stops with the
    workstream's delivery failing under autofix. The fixture is the Pi run: every ticket done, a repair committed after
    review left review_head stale, and E2E never ran."""

    def setUp(self):
        super().setUp()
        self.git('commit', '-q', '--allow-empty', '-m', 'start')
        self.start = self.git('rev-parse', 'HEAD')
        self.git('switch', '-q', '-c', 'feat/fix')
        self.git('commit', '-q', '--allow-empty', '-m', 'reviewed work')
        self.reviewed = self.git('rev-parse', 'HEAD')
        self.git('commit', '-q', '--allow-empty', '-m', 'repair after review')
        self.head = self.git('rev-parse', 'HEAD')
        self.git('switch', '-q', '-')
        self.ticket('01', 'done')
        self.report = self.repo / '.scratch/fix/reports/spec-review.md'
        self.report.parent.mkdir(parents=True)
        self.review()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    def ticket(self, number, status):
        path = self.repo / f'.scratch/fix/issues/{number}-task.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'---\nticket: fix/{number}\nstatus: {status}\nblocked_by: []\ntouches: [t{number}]\n'
                        'devices: []\n---\n')

    def review(self, delivered=False, **fields):
        """The review report's front matter: by default the Pi run's, or current and passed when `delivered`; `fields`
        replace values, and a None value leaves the field out."""
        head = self.head if delivered else self.reviewed
        self.rounds = n = getattr(self, 'rounds', 0) + 1
        recorded = dict(dict(autofix='true', blocker='none', review_status='clean', documentation_status='passed',
                             review_head=head, review_base=self.start, review_diff_base=self.start,
                             e2e_status='passed' if delivered else 'not-run',
                             e2e_head=self.head if delivered else 'none',
                             review_by=f'review/fix/{n}', e2e_by=f'e2e/fix/{n}' if delivered else 'none'), **fields)
        self.report.write_text('---\n' + ''.join(f'{key}: {value}\n' for key, value in recorded.items()
                                                 if value is not None) + '---\n')
        # The reviewer and the E2E worker returned this report, as collected attempts.
        for key, job in (('review_by', 'diff-review'), ('e2e_by', 'e2e-gate')):
            if str(recorded.get(key)).startswith(('review/fix/', 'e2e/fix/')):
                support.collected_attempt(self.repo, 'fix', recorded[key], self.report, job=job, head=head)

    def gap(self):
        return (f'fix: delivery incomplete under autofix: e2e_status: not-run (needs passed); e2e_by: none names no '
                f'worker run; review_head: {self.reviewed} is stale against feat/fix head {self.head}; e2e_head: none '
                f'is stale against feat/fix head {self.head}. Continue the autofix handoff '
                f'({ROOT}/skills/dispatch/handoff.md) until `python3 {ROOT}/scripts/next_tickets.py --repo {self.repo} '
                f'fix --delivery` passes, or record `blocker: <reason>` in {self.report}')

    def test_the_bound_orchestrator_stopping_with_delivery_failing_under_autofix_is_held_once(self):
        # Rules 3 and 4: dispatch bound this Herdr pane. Its stop is held once on Claude and Codex, naming the failing
        # fields and the command to continue; the hook's off switch still disables it; delivered, nothing holds it.
        self.bind(pane='w1:p1')
        self.assertEqual(self.check(pane='w1:p1'), [self.gap()])
        for host in ('claude', 'codex'):
            decision = self.stop(pane='w1:p1', host=host, session=host)
            self.assertEqual(decision['decision'], 'block')
            # Review 6: a delivery-only hold carries its gap and command, without the worker-registration advice.
            self.assertEqual(decision['reason'], f'Stopping now leaves these gaps:\n- {self.gap()}\n')
            self.assertEqual(self.stop(pane='w1:p1', host=host, session=host, active=True)['decision'], 'block')
        self.assertIsNone(self.stop(pane='w1:p1', env={'ZLOOP_YIELD_HOOK': 'off'}))
        self.review(delivered=True)
        self.assertEqual(self.check(pane='w1:p1'), [])

    def test_outside_herdr_the_session_id_each_host_exports_binds_the_orchestrator(self):
        # Rule 3: Claude Code, Codex and Pi export their session ID to the orchestrator's shell; the binding records
        # it, and the stop check holds the session the host then reports, and no other.
        for variable, host in (('CLAUDE_CODE_SESSION_ID', 'claude'), ('CODEX_SESSION_ID', 'codex'),
                               ('PI_SESSION_ID', None)):
            self.bind(pane=None, **{variable: f'{variable}-1'})
            self.assertEqual(self.check(pane=None, session=f'{variable}-1'), [self.gap()], variable)
            self.assertEqual(self.check(pane=None, session='other-1'), [], variable)
            if host:
                self.assertEqual(self.stop(pane=None, session=f'{variable}-1', host=host)['decision'], 'block')
                self.assertIsNone(self.stop(pane=None, session='other-1', host=host))

    def test_a_codex_orchestrator_bound_under_a_stale_herdr_pane_is_held_by_its_session(self):
        # Rule (E4 codex): the binding recorded the app-server's stale pane w1:p17 beside the right CODEX_SESSION_ID;
        # the stop check holds that session in whatever pane it reports, and no other session in the stale pane.
        self.bind(pane='w1:p17', CODEX_SESSION_ID='codex-1')
        self.assertEqual(self.check(pane='w2:p1', session='codex-1'), [self.gap()])
        self.assertEqual(self.check(pane=None, session='codex-1'), [self.gap()])
        self.assertEqual(self.stop(pane='w2:p1', session='codex-1', host='codex')['decision'], 'block')
        self.assertEqual(self.check(pane='w1:p17', session='codex-2'), [])
        self.assertIsNone(self.stop(pane='w1:p17', session='codex-2', host='codex'))

    def test_a_orchestrator_with_no_identity_is_refused(self):
        # Review 4: outside Herdr, on a host that exports no session ID, nothing could match a stopping session, so the
        # binding refuses, saying the stop hold cannot bind, and records nothing.
        refused = self.bind(pane=None, code=1)
        self.assertEqual(refused.stderr, 'no Herdr pane (HERDR_PANE_ID) or host session ID (CLAUDE_CODE_SESSION_ID, '
                                         'CODEX_SESSION_ID, PI_SESSION_ID, OPENCODE_SESSION_ID) here: the stop hold '
                                         'cannot bind this session\n')
        self.assertFalse((self.repo / '.scratch/fix/reports/orchestrator.json').exists())

    def test_no_other_session_is_held(self):
        # Rule 4: when the binding and the stopping session both know a host session, it alone decides (T10), so the
        # bound pane now running another conversation (or a restarted Herdr's reused pane ID) is not held, while the
        # same host session in another pane is; with no session on one side the pane decides.
        self.bind(pane='w1:p1', CLAUDE_CODE_SESSION_ID='host-1')
        for pane, session in (('w1:p1', 'host-1'), ('w1:p1', None), ('w1:p9', 'host-1')):
            self.assertEqual(self.check(pane=pane, session=session), [self.gap()], (pane, session))
        for pane, session in (('w1:p1', 'host-2'), ('w1:p9', None), (None, 'host-2')):
            self.assertEqual(self.check(pane=pane, session=session), [], (pane, session))

    def test_another_workstreams_ticket_the_board_refuses_holds_no_orchestrator(self):
        # Review (Codex): sessions share the checkout, and another workstream's malformed or unreadable ticket made the
        # bound orchestrator's delivery uncheckable, holding it for a file not its own; its own still holds it, named.
        self.bind(pane='w1:p1')
        other = self.repo / '.scratch/other/issues'
        other.mkdir(parents=True)
        (other / '01-task.md').write_text('---\nticket: other/one\nstatus: ready\nblocked_by: []\ntouches: []\n'
                                          'devices: []\n---\n')
        (other / '02-task.md').write_bytes(b'---\nticket: other/02\nstatus: \xff\n---\n')
        self.assertEqual(self.check(pane='w1:p1'), [self.gap()])
        self.review(delivered=True)
        self.assertEqual(self.check(pane='w1:p1'), [])
        own = self.repo / '.scratch/fix/issues/02-task.md'
        own.write_text('---\nticket: fix/two\nstatus: done\nblocked_by: []\ntouches: []\ndevices: []\n---\n')
        self.assertEqual(self.check(pane='w1:p1'), [f"could not check delivery of fix: {own}: invalid ticket id 'fix/two'"])

    def test_a_local_review_is_delivery_evidence_only_while_the_router_finds_no_reviewer(self):
        # User (2026-11-07): Pi's maven-cache-split delivery passed on a review report Pi wrote itself, with a hand-written
        # no_route record, while the router routed a Pi reviewer; --delivery and the stop check now ask the router again.
        home = Path(self.tmp.name) / 'home'
        home.mkdir()
        env = {'HOME': str(home)}
        self.bind(pane='w1:p1', autofix=True)
        (self.repo / '.scratch/fix/issues/01-task.md').write_text(  # Accepted, so `--delivery` passes the board's check.
            '---\nticket: fix/01\nstatus: done\nblocked_by: []\ntouches: [t01]\ndevices: []\n'
            f"accepted: {'a' * 40}\naccepted_report: {'b' * 64}\n---\n")
        self.review(delivered=True, review_by='local')
        record = self.report.with_name('review.json')
        record.write_text(json.dumps({'route': {'status': 'no_route', 'reason': 'inline by order'}, 'host': 'codex',
                                      'report': str(self.report)}))
        routed = ('review_by: local, but the router now routes diff-review from codex to a codex native subagent: launch '
                  'that reviewer and name its attempt in review_by')
        gaps = self.check(pane='w1:p1', env=env)
        self.assertEqual(len(gaps), 1)
        self.assertIn(routed, gaps[0])
        delivery = subprocess.run([sys.executable, '-B', str(TICKETS), '--repo', str(self.repo), 'fix', '--delivery'],
                                  capture_output=True, text=True, env=self.env(None, **env))
        self.assertEqual(delivery.returncode, 1, delivery.stdout + delivery.stderr)
        self.assertIn(routed, delivery.stdout + delivery.stderr)
        config = self.repo / '.zloop/executors.json'
        config.parent.mkdir()
        config.write_text(json.dumps(NO_CODEX_REVIEWER))
        self.assertEqual(self.check(pane='w1:p1', env=env), [])
        delivery = subprocess.run([sys.executable, '-B', str(TICKETS), '--repo', str(self.repo), 'fix', '--delivery'],
                                  capture_output=True, text=True, env=self.env(None, **env))
        self.assertEqual(delivery.returncode, 0, delivery.stdout + delivery.stderr)

    def test_a_router_that_never_answers_costs_one_wait_per_host_within_the_stop_budget(self):
        # Review (Codex): each check asked the router again, ten seconds each: a stop holding a review, an autofix
        # delivery and a delivered card of one host took thirty seconds, past the hosts' 20 s hook budget.
        home = Path(self.tmp.name) / 'home'
        (home / '.config/zloop').mkdir(parents=True)
        os.mkfifo(home / '.config/zloop/executors.json')  # The router opens it and waits for a writer that never comes.
        self.bind(pane='w1:p1', autofix=True, CLAUDE_CODE_SESSION_ID='host-1')
        (self.repo / '.scratch/fix/issues/01-task.md').write_text(
            '---\nticket: fix/01\nstatus: done\nblocked_by: []\ntouches: [t01]\ndevices: []\n'
            f"accepted: {'a' * 40}\naccepted_report: {'b' * 64}\n---\n")
        self.review(delivered=True, review_by='local')
        self.report.with_name('review.json').write_text(json.dumps({
            'pane': 'w1:p1', 'sessions': ['host-1'], 'report': str(self.report),
            'state': str(self.report.with_name('workers.sqlite')), 'host': 'codex',
            'route': {'status': 'no_route', 'reason': 'none then'}, 'next_commands': {'route': 'the route command'}}))
        (self.repo / '.scratch/fix/autopilot.md').write_text(
            '# fix\n\n## State\nconfirmed: 2026-11-07 "go"\nsession: host-1\nauto-merge: no\nboard: local\nphase: pr\n'
            'terminal: merged\nevidence: x\n')
        started = time.monotonic()
        gaps = self.check(pane='w1:p1', session='host-1', env={'HOME': str(home)})
        self.assertLess(time.monotonic() - started, 12)
        self.assertEqual(len([gap for gap in gaps if 'review_by: local cannot be judged, the router did not answer '
                                                     'within 5 s' in gap]), 3, gaps)

    def test_a_recorded_blocker_lets_the_orchestrator_stop(self):
        # Rules 1 and 4: `blocker: <reason>` in the review report records why delivery cannot progress, and with it the
        # orchestrator stops; `none`, the report template's value, and an empty value are no blocker.
        self.bind(pane='w1:p1')
        self.review(blocker='the staging device is offline')
        self.assertEqual(self.check(pane='w1:p1'), [])
        self.assertIsNone(self.stop(pane='w1:p1'))
        for none in ('none', ''):
            self.review(blocker=none)
            self.assertEqual(self.check(pane='w1:p1'), [self.gap()], none)

    def test_without_autofix_the_orchestrator_stops(self):
        # Rules 1 and 4: only `autofix: true`, the recorded repair authorization, holds. A read-only review (`false`),
        # a report that records no authorization, and no report at all stop after review, as dispatch says.
        self.bind(pane='w1:p1')
        for autofix in ('false', None):
            self.review(autofix=autofix)
            self.assertEqual(self.check(pane='w1:p1'), [], autofix)
        self.report.unlink()
        self.assertEqual(self.check(pane='w1:p1'), [])

    def test_autofix_recorded_at_dispatch_start_holds_until_the_review_report_records_its_own(self):
        # Decision 2: `--orchestrate --autofix` records the authorization at dispatch start, so the window between the
        # last ticket done and the review report is held; once the report records `autofix`, its field wins.
        self.report.unlink()
        self.bind(pane='w1:p1', autofix=True)
        self.assertEqual(self.check(pane='w1:p1'), [
            f'fix: delivery incomplete under autofix: no review report at {self.report}. Continue the autofix handoff '
            f'({ROOT}/skills/dispatch/handoff.md) until `python3 {ROOT}/scripts/next_tickets.py --repo {self.repo} fix '
            f'--delivery` passes, or record `blocker: <reason>` in {self.report}'])
        self.review(autofix=None)
        self.assertEqual(self.check(pane='w1:p1'), [self.gap()])
        self.review(autofix='false')
        self.assertEqual(self.check(pane='w1:p1'), [])
        self.bind(pane='w1:p1')
        self.review(autofix='true')
        self.assertEqual(self.check(pane='w1:p1'), [self.gap()])

    def test_the_templates_autofix_placeholder_is_unset_so_dispatch_starts_value_counts(self):
        # Review 5: the report template seeds `autofix: <true|false>`; a report copied with the placeholder left in
        # must not override `--autofix` from dispatch start, either way.
        self.review(autofix='<true|false>')
        self.bind(pane='w1:p1', autofix=True)
        self.assertEqual(self.check(pane='w1:p1'), [self.gap()])
        self.bind(pane='w1:p1')
        self.assertEqual(self.check(pane='w1:p1'), [])

    def held(self, slug, *problems):
        """The delivery hold's gap for workstream `slug`, naming `problems`."""
        return (f"{slug}: delivery incomplete under autofix: {'; '.join(problems)}. Continue the autofix handoff "
                f'({ROOT}/skills/dispatch/handoff.md) until `python3 {ROOT}/scripts/next_tickets.py --repo {self.repo} '
                f'{slug} --delivery` passes, or record `blocker: <reason>` in '
                f'{self.repo}/.scratch/{slug}/reports/spec-review.md')

    def test_a_live_worker_with_a_running_wait_lets_the_orchestrator_yield_but_a_lost_one_does_not(self):
        # Rule: the hold is for a delivery stuck with nobody working on it. While a live worker of the workstream has a
        # wait running that will wake the host, the host yields; once the wait is lost, or the worker is collected,
        # the delivery hold names the workstream again.
        self.bind(pane='w1:p1')
        self.state = self.repo / '.scratch/fix/reports/workers.sqlite'
        self.lifecycle('register', 'fix/01/1', assignment='fix/01', repo=str(self.repo), worktree=str(self.repo),
                       head='head-1', execution=str(self.execution), runtime='claude', worker='pane-1',
                       native_session='native-1', owned=True,
                       packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.lifecycle('startup', 'fix/01/1', native=str(self.native('fix/01/1')))
        self.lifecycle('submitted', 'fix/01/1', evidence=['native.jsonl:2:accepted prompt'])
        pidfile, request = self.state.parent / 'wait.pid', self.state.parent / 'wait.json'
        request.write_text(json.dumps({'argv': [sys.executable, '-c',
            f'import os,time; open({str(pidfile)!r}, "w").write(str(os.getpid())); time.sleep(30)'], 'timeout': 40,
            'notify': {'argv': [sys.executable, '-c', 'pass', '{receipt}'], 'timeout': 2}}))
        watcher = subprocess.Popen([sys.executable, '-B', str(LIFECYCLE), 'watch', '--state', str(self.state),
                                    '--attempt', 'fix/01/1', '--input', str(request)], stdout=subprocess.PIPE,
                                   text=True, env=self.env('w1:p1'))
        self.addCleanup(lambda: watcher.poll() is None and (watcher.kill(), watcher.communicate()))
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(pidfile.exists())
        self.assertEqual(self.check(pane='w1:p1'), [])
        watcher.kill()
        watcher.communicate(timeout=5)
        os.killpg(int(pidfile.read_text()), 9)
        self.assertEqual(self.check(pane='w1:p1'), [f'{self.state}: fix/01/1: wait process lost', self.gap()])

    def test_a_workstream_is_finished_only_when_merged_deleted_and_e2e_passed_at_a_head_in_main(self):
        # Review 1, re-review F1: the board records a finished workstream as its branch gone, its reviewed head in main and
        # a passing E2E at a head in main. Merged and deleted before E2E ran (the Pi run's report), it is still held;
        # so is a passing E2E at a head main lacks, and a reviewed head main lacks.
        self.bind(pane='w1:p1')
        self.git('update-ref', 'refs/heads/main', self.head)
        self.git('branch', '-D', 'feat/fix')
        missing = 'workstream branch feat/fix is not an existing branch'
        self.assertEqual(self.check(pane='w1:p1'), [
            self.held('fix', 'e2e_status: not-run (needs passed)', 'e2e_by: none names no worker run', missing)])
        self.review(delivered=True)
        self.assertEqual(self.check(pane='w1:p1'), [])
        self.review(delivered=True, e2e_status='failed')
        self.assertEqual(self.check(pane='w1:p1'), [self.held('fix', 'e2e_status: failed (needs passed)', missing)])
        update = ('; update local main from the remote (`git -C {} fetch origin main`, then fast-forward main to '
                  'origin/main)').format(self.repo)
        self.review(delivered=True, e2e_head='f' * 40)
        self.assertEqual(self.check(pane='w1:p1'), [
            self.held('fix', f'{missing}, and its e2e_head is in no main{update}')])
        self.review(delivered=True)
        self.git('update-ref', 'refs/heads/main', self.start)
        self.assertEqual(self.check(pane='w1:p1'), [
            self.held('fix', f'{missing}, and its review_head and e2e_head are in no main{update}')])

    def test_an_old_workstream_without_e2e_fields_is_bound_only_by_name_and_then_held_until_finished(self):
        # Re-review F1, in an old real workstream's shape: reviewed clean, no E2E or producer fields, merged into main
        # and its branch deleted. Orchestrating every workstream binds only those with active work (a branch, or a ticket
        # not done), so not it; named explicitly, it is held until finished by the definition above.
        path = self.repo / '.scratch/audit/issues/01-task.md'
        path.parent.mkdir(parents=True)
        path.write_text('---\nticket: audit/01\nstatus: done\nblocked_by: []\ntouches: [a]\ndevices: []\n---\n')
        report = self.repo / '.scratch/audit/reports/spec-review.md'
        report.parent.mkdir(parents=True)
        report.write_text(f'---\nreview_status: clean\ndocumentation_status: passed\nreview_base: {self.start}\n'
                          f'review_diff_base: {self.start}\nreview_head: {self.reviewed}\n---\n')
        self.git('update-ref', 'refs/heads/main', self.head)
        self.review(delivered=True)  # A live workstream branch is active work even once its delivery passes.
        bound = self.bind(slug=None).stdout
        self.assertEqual([line.split(':')[0] for line in bound.splitlines()], ['fix'], bound)
        self.review()
        self.bind(slug='audit', autofix=True)
        self.assertEqual(self.check(pane='w1:p1'), [
            self.held('audit', 'e2e_status: missing (needs passed)', 'review_by: missing names no worker run',
                      'e2e_by: missing names no worker run', 'workstream branch feat/audit is not an existing branch'),
            self.gap()])

    def test_orchestrating_every_workstream_binds_only_those_with_work_left_and_refuses_when_none_has(self):
        # Review 1: with no slug, --orchestrate binds a workstream with a ticket not done, or with delivery incomplete and
        # its branch present; never a finished one (merged into main, branch deleted). With none left it refuses.
        for slug, status in (('wip', 'implementing'), ('old', 'done')):
            path = self.repo / f'.scratch/{slug}/issues/01-task.md'
            path.parent.mkdir(parents=True)
            path.write_text(f'---\nticket: {slug}/01\nstatus: {status}\nblocked_by: []\ntouches: [{slug}]\n'
                            'devices: []\n---\n')
        old = self.repo / '.scratch/old/reports/spec-review.md'
        old.parent.mkdir(parents=True)
        old.write_text(self.report.read_text())
        bound = self.bind(slug=None).stdout
        self.assertEqual(sorted(line.split(':')[0] for line in bound.splitlines()), ['fix', 'wip'], bound)
        self.assertFalse((self.repo / '.scratch/old/reports/orchestrator.json').exists())
        for slug in ('fix', 'wip'):
            (self.repo / f'.scratch/{slug}/reports/orchestrator.json').unlink(missing_ok=True)
        self.ticket('01', 'done')
        (self.repo / '.scratch/wip/issues/01-task.md').unlink()
        self.git('update-ref', 'refs/heads/main', self.head)
        self.git('branch', '-D', 'feat/fix')
        refused = self.bind(slug=None, code=1)
        self.assertEqual(refused.stderr, 'no selected workstream has work left to orchestrate\n')
        self.assertEqual(sorted(self.repo.glob('.scratch/*/reports/orchestrator.json')), [])

    def test_while_a_ticket_is_not_done_the_orchestrator_is_not_held_for_delivery(self):
        # Rule 4: the hold covers delivery once every ticket is done; an unfinished or blocked ticket is still
        # implementation, which the board and the worker gates answer for.
        self.bind(pane='w1:p1')
        for status in ('implementing', 'blocked'):
            self.ticket('02', status)
            self.assertEqual(self.check(pane='w1:p1'), [], status)
        self.ticket('02', 'done')
        self.assertEqual(self.check(pane='w1:p1'), [self.gap()])

    def test_a_delivery_check_that_cannot_run_is_a_named_gap_that_holds_once(self):
        # Rule 4: a board the scheduler refuses (a ticket id that is not <slug>/<NN>) leaves the check unable to run:
        # a named gap for the orchestrator, held once, never a pass or a crashed hook.
        self.bind(pane='w1:p1')
        self.ticket('02', 'done')
        bad = self.repo / '.scratch/fix/issues/02-task.md'
        bad.write_text(bad.read_text().replace('ticket: fix/02', 'ticket: Fix 2'))
        gap = f"could not check delivery of fix: {bad}: invalid ticket id 'Fix 2'"
        self.assertEqual(self.check(pane='w1:p1'), [gap])
        self.assertIn(f'- {gap}\n', self.stop(pane='w1:p1')['reason'])
        self.assertNotIn('decision', self.stop(pane='w1:p1', active=True))  # Held once.
        self.assertEqual(self.check(pane='w1:p9'), [])

    @unittest.skipUnless(shutil.which('node'), 'Node.js runs the Pi extension')
    def test_pi_holds_the_bound_orchestrator_while_the_gate_holds_and_not_another_session(self):
        # Rules 3 and 4 on Pi: its bash tool exports PI_SESSION_ID; the settle check holds that session with the same
        # message for as long as the check says to, and not another Pi session.
        self.bind(pane=None, PI_SESSION_ID='pi-1')
        decisions, other = self.pi("""
const settle = async (use) => (await handlers.agent_before_settle(
  { type: 'agent_before_settle', outcome: 'completed', entries: [], continue: false }, use)) ?? null;
console.log(JSON.stringify([[await settle(ctx), await settle(ctx), await settle(ctx), await settle(ctx)],
  await settle({ ...ctx, sessionManager: { getSessionId: () => 'pi-2' } })]));
""")
        for decision in decisions[:3]:
            self.assertTrue(decision['continue'])
            self.assertEqual(decision['entries'][0]['content'], f'Stopping now leaves these gaps:\n- {self.gap()}\n')
        self.assertFalse(decisions[3]['continue'])
        self.assertIsNone(other)


class HerdrWorkers(GateRepo):
    """A worker placed in a Herdr tab (w1:t5 of the `default` server), registered and collected."""

    def setUp(self):
        super().setUp()
        self.tab = {'tab_id': 'w1:t5', 'workspace_id': 'w1', 'label': 'workstream/ticket-1 builder', 'pane_count': 1}
        self.serve(agents=[{'agent': 'claude', 'agent_status': 'idle', 'name': 'builder-1', 'pane_id': 'w1:p5',
                            'tab_id': 'w1:t5', 'workspace_id': 'w1'}], tabs={'default': [self.tab]})
        self.report = self.state.parent / 'ticket-1.md'
        self.report.write_text('## Implemented\n')

    def register(self, pane='w1:p1', env=None, attempt='ticket-1/1', **fields):
        """Register builder-1 as Herdr places it (tab w1:t5 of session `default`) from Herdr pane `pane`."""
        registration = dict(assignment='ticket-1', repo=str(self.repo), worktree=str(self.repo), head='head-1',
                            execution=str(self.execution), runtime='claude', worker='builder-1',
                            native_session='native-1', owned=True, herdr={'session': 'default'},
                            packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        registration.update(fields)
        self.lifecycle('register', attempt, pane=pane, env=env, **{key: value for key, value in registration.items()
                                                          if value is not None})

    def collect(self, attempt='ticket-1/1', **disposition):
        """Collect the attempt's result; with a `disposition`, through `collect` (which records it)."""
        result = dict(status='failed', head='head-1', report=str(self.report), evidence=['worker.log'])
        if not disposition:
            return self.lifecycle('result', attempt, **result)
        request = self.state.parent / 'collect.json'
        request.write_text(json.dumps(dict(result, disposition=disposition)))
        subprocess.run([sys.executable, '-B', str(LIFECYCLE), 'collect', '--state', str(self.state), '--attempt',
                        attempt, '--input', str(request)], check=True, capture_output=True, env=self.env())



class StaleTabs(HerdrWorkers):
    """A Herdr worker's tab outlives collection only when its attempt is retained; otherwise it is a gap at stop."""

    def gap(self):
        return (f'{self.state}: ticket-1/1: Herdr tab w1:t5 is still open after collection; retire the worker or '
                f'record its retention ({ROOT}/runtime/workers/collect.md)')

    def test_a_collected_attempt_whose_tab_is_still_open_is_a_gap_for_its_orchestrator_until_retained(self):
        # Rule 5: collected (it has a result), not retained, and its recorded tab still listed by its Herdr session: a
        # gap for the pane that registered it alone. A closed tab, or recorded retention, is none.
        self.register()
        self.collect()
        self.assertEqual(self.check(pane='w1:p1'), [self.gap()])
        self.assertIn(f'- {self.gap()}\n', self.stop(pane='w1:p1')['reason'])
        self.assertEqual(self.check(pane='w1:p9'), [])
        self.assertEqual(self.check(pane=None, session='host-1'), [])
        self.serve(tabs={'default': [dict(self.tab, tab_id='w1:t6')]})
        self.assertEqual(self.check(pane='w1:p1'), [])
        self.serve(tabs={'default': [self.tab]})
        self.collect(kind='retain', reason='the repair round reuses this context', next_use='ticket-1 repair',
                     evidence=['review finding F1'])
        # Retention ends the open-tab gap; with nothing else in this run, the retention reminder takes its place.
        self.assertEqual(self.check(pane='w1:p1'), [self.reminder()])

    def reminder(self, attempt='ticket-1/1', next_use='ticket-1 repair'):
        return (f'{self.state}: {attempt}: retained for "{next_use}" in Herdr tab w1:t5, and nothing in its run follows '
                'it: register that next attempt in this worker, raise a design hold for the decision it waits on, or '
                f'retire the worker ({ROOT}/runtime/resources/retire.md)')

    def retain(self):
        self.collect(kind='retain', reason='the repair round reuses this context', next_use='ticket-1 repair',
                     evidence=['review finding F1'])

    def test_a_retained_worker_nothing_in_its_run_follows_is_named_until_its_run_or_a_hold_needs_it(self):
        # Rules 1-2 (stforestkit R14, R15, R01: kept after their run was integrated and pushed, R14 still holding
        # uncommitted repairs): retention keeps a worker for a next use. Once nothing of its run is unfinished and no
        # design hold waits on the user for its assignment, the stop names it.
        child = self.state.parent / 'child-execution.json'
        child.write_text(json.dumps(dict(json.loads(self.execution.read_text()), native_dispatch=True)))
        self.register()
        self.lifecycle('register', 'ticket-2/1', assignment='ticket-2', repo=str(self.repo), worktree=str(self.repo),
                       head='head-1', execution=str(child), runtime='claude', worker='helper-2',
                       native_session='native-2', owned=True,
                       packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.lifecycle('startup', 'ticket-2/1', native=str(self.native('ticket-2/1')))
        self.retain()
        self.assertEqual(self.check(pane='w1:p1'), [], 'ticket-2/1 is still unfinished')
        self.lifecycle('result', 'ticket-2/1', status='failed', head='head-1', report=str(self.report),
                       evidence=['worker.log'])
        self.assertEqual(self.check(pane='w1:p1'), [self.reminder()])
        self.assertEqual(self.check(pane='w1:p9'), [], 'only its orchestrator is reminded')
        brief = self.state.parent / 'decision.md'
        brief.write_text('Repair waits on a product ruling.\n')
        self.lifecycle('hold', 'ticket-2/1', reason='another ticket waits on a ruling', brief=str(brief),
                       evidence=['its own question'])
        self.acknowledge()
        self.assertEqual(self.check(pane='w1:p1'), [self.reminder()], "another assignment's hold is not its own")
        self.lifecycle('hold', reason='the repair waits on a product ruling', brief=str(brief),
                       evidence=['P03-071 corrected test fails'])
        self.acknowledge()
        self.assertEqual(self.check(pane='w1:p1'), [], 'a design hold waits on the user')

    def acknowledge(self):
        status = subprocess.run([sys.executable, '-B', str(LIFECYCLE), 'status', '--state', str(self.state)],
                                check=True, capture_output=True, text=True, env=self.env())
        for receipt in json.loads(status.stdout)['pending']:
            subprocess.run([sys.executable, '-B', str(LIFECYCLE), 'ack', '--state', str(self.state), '--event-id',
                            str(receipt['id'])], check=True, capture_output=True, env=self.env())

    def test_a_user_worker_is_never_reminded_to_be_retired(self):
        # Review (Codex, Sonnet): an existing user worker (`owned: false`) is no cleanup target, so no reminder.
        self.register(owned=False)
        self.retain()
        self.assertEqual(self.check(pane='w1:p1'), [])

    def closed_worker(self, attempt='ticket-1/1', worker='builder-1', session='native-1', assignment='ticket-1'):
        """A Herdr worker registered, collected and recorded closed while it still runs."""
        self.register(attempt=attempt, worker=worker, native_session=session, assignment=assignment)
        self.collect(attempt)
        self.acknowledge()
        self.lifecycle('closed', attempt, evidence=['herdr agent get tautrep17 and pane get w2W:pB8 both not found'])

    @staticmethod
    def occupant(session, kind='id', pane='w1:p5', tab='w1:t5'):
        return {pane: {'pane_id': pane, 'tab_id': tab, 'agent': 'claude',
                       'agent_session': {'kind': kind, 'value': session}}}

    def closure_reminder(self, command='herdr --session default pane close w1:p5'):
        return (f'{self.state}: ticket-1/1: recorded closed, but Herdr pane w1:p5 in tab w1:t5 still holds its '
                f'conversation; end it with `{command}`')

    def test_a_worker_recorded_closed_while_its_own_pane_still_holds_it_is_a_reminder(self):
        # Rule 4 (stforestkit R15/repair-1: recorded closed with another worker's pane as its evidence while its own
        # Codex sat idle in its tab): a closure that the worker's own pane contradicts is named, with the command that
        # ends the worker.
        self.closed_worker()
        agents = [{'agent': 'claude', 'agent_status': 'idle', 'name': 'builder-1', 'pane_id': 'w1:p5',
                   'tab_id': 'w1:t5', 'workspace_id': 'w1'}]
        self.serve(agents=agents, tabs={'default': [self.tab]}, panes={'default': self.occupant('native-1')})
        self.assertEqual(self.check(pane='w1:p1'), [self.closure_reminder()])
        self.assertIn(f'- {self.closure_reminder()}\n', self.stop(pane='w1:p1')['reason'])
        self.assertIsNone((self.stop(pane='w1:p1', active=True) or {}).get('decision'), 'a reminder holds once')
        # Review (Sonnet): the command reaches the server the way the hook does, through the caller's own socket.
        socket = str(self.repo / '.config' / 'herdr' / 'herdr.sock')
        (self.bin / 'argv').write_text('')
        self.assertEqual(self.check(pane='w1:p1', env={'HERDR_SOCKET_PATH': socket}),
                         [self.closure_reminder('herdr pane close w1:p5')])
        self.assertIn('pane get w1:p5', (self.bin / 'argv').read_text().splitlines(), 'asked through the socket too')
        # Another conversation in that pane, or no agent there (an exited worker's shell), is not this worker; a
        # Herdr that cannot be asked names nothing, since a recorded closure is reminded of, not gated.
        for case, panes in (('another conversation', self.occupant('native-9')),
                            ('an identity it cannot compare', self.occupant('builder-1', kind='name')),
                            ('no agent', {'w1:p5': {'pane_id': 'w1:p5', 'tab_id': 'w1:t5'}})):
            with self.subTest(case=case):
                self.serve(tabs={'default': [self.tab]}, panes={'default': panes})
                self.assertEqual(self.check(pane='w1:p1'), [])
        self.serve(tabs={'default': [self.tab]}, panes={'default': self.occupant('native-1')}, fail='connection refused')
        self.assertEqual(self.check(pane='w1:p1'), [])

    def test_a_closure_is_judged_by_a_record_path_as_well_as_an_id(self):
        # Review (Codex, Sonnet): Pi's Herdr identity is its session record's path, compared with the record its
        # startup observed, as the lifecycle compares it.
        self.closed_worker()
        db = sqlite3.connect(self.state)
        data = json.loads(db.execute("SELECT data FROM attempts WHERE id = 'ticket-1/1'").fetchone()[0])
        data['startup_evidence'] = {'native_observation': {'source': '/records/pi-session.jsonl'}}
        db.execute("UPDATE attempts SET data = ? WHERE id = 'ticket-1/1'", (json.dumps(data),))
        db.commit()
        db.close()
        self.serve(tabs={'default': [self.tab]},
                   panes={'default': self.occupant('/records/pi-session.jsonl', kind='path')})
        self.assertEqual(self.check(pane='w1:p1'), [self.closure_reminder()])
        # Review (Codex): another record in that pane is another conversation.
        self.serve(tabs={'default': [self.tab]}, panes={'default': self.occupant('/records/new.jsonl', kind='path')})
        self.assertEqual(self.check(pane='w1:p1'), [])

    def test_a_failed_tab_listing_met_first_by_a_closure_is_still_named_for_an_open_attempt(self):
        # Review (Sonnet, Codex): the closure that met the failure first stays silent; the collected attempt after it in
        # the same workspace still names the check that could not run.
        self.serve(agents=[{'agent': 'claude', 'agent_status': 'idle', 'name': name, 'pane_id': pane,
                            'tab_id': tab, 'workspace_id': 'w1'}
                           for name, pane, tab in (('builder-0', 'w1:p6', 'w1:t6'), ('builder-1', 'w1:p5', 'w1:t5'))],
                   tabs={'default': [self.tab, dict(self.tab, tab_id='w1:t6')]})
        self.closed_worker('ticket-0/1', 'builder-0', 'native-0', 'ticket-0')
        self.register()
        self.collect()
        self.serve(fail='connection refused')
        self.assertEqual(self.check(pane='w1:p1'), [
            'could not check worker tabs: herdr tab list exited 1: '
            '{"error": {"code": "server_unavailable", "message": "connection refused"}}'])

    def test_closure_lookups_stop_at_the_first_one_herdr_does_not_answer(self):
        # Review (Codex, Sonnet): the hosts give the whole stop check 20 seconds, so one pane lookup that times out
        # ends the closure lookups of that stop instead of each waiting its own five seconds.
        tabs = [self.tab, dict(self.tab, tab_id='w1:t6')]
        self.serve(agents=[{'agent': 'claude', 'agent_status': 'idle', 'name': name, 'pane_id': pane,
                            'tab_id': tab, 'workspace_id': 'w1'}
                           for name, pane, tab in (('builder-0', 'w1:p6', 'w1:t6'), ('builder-1', 'w1:p5', 'w1:t5'))],
                   tabs={'default': tabs})
        self.closed_worker('ticket-0/1', 'builder-0', 'native-0', 'ticket-0')
        self.closed_worker()
        self.serve(tabs={'default': tabs}, hang_panes=True,
                   panes={'default': {**self.occupant('native-1'), **self.occupant('native-0', pane='w1:p6', tab='w1:t6')}})
        started = time.monotonic()
        self.assertEqual(self.check(pane='w1:p1'), [])
        self.assertLess(time.monotonic() - started, 9)

    def test_closure_lookups_stop_across_run_states_too(self):
        # Review (Codex): the bound is per stop, not per run state.
        tabs = [self.tab, dict(self.tab, tab_id='w1:t6')]
        self.serve(agents=[{'agent': 'claude', 'agent_status': 'idle', 'name': name, 'pane_id': pane,
                            'tab_id': tab, 'workspace_id': 'w1'}
                           for name, pane, tab in (('builder-0', 'w1:p6', 'w1:t6'), ('builder-1', 'w1:p5', 'w1:t5'))],
                   tabs={'default': tabs})
        self.closed_worker()
        self.state = self.repo / '.scratch/other/reports/workers.sqlite'
        self.state.parent.mkdir(parents=True)
        self.closed_worker('other/1', 'builder-0', 'native-0', 'other')
        self.serve(tabs={'default': tabs}, hang_panes=True,
                   panes={'default': {**self.occupant('native-1'), **self.occupant('native-0', pane='w1:p6', tab='w1:t6')}})
        (self.bin / 'calls').write_text('')
        self.assertEqual(self.check(pane='w1:p1'), [])
        self.assertEqual(sum(' pane get ' in line for line in (self.bin / 'calls').read_text().splitlines()), 1)

    def test_a_state_without_a_holds_table_still_has_its_open_tabs_named(self):
        # Review (own): reading design holds must not cost a state its open-tab check; no table means no hold.
        self.register()
        self.collect()
        db = sqlite3.connect(self.state)
        db.execute('DROP TABLE holds')
        db.commit()
        db.close()
        self.assertEqual(self.check(pane='w1:p1'), [self.gap()])

    def test_only_the_latest_attempt_of_a_retained_worker_is_judged(self):
        # Rule 2: a retained attempt whose worker took a later attempt has had its next use; the later one answers. The
        # later one is the later registration (ticket-1/10 after ticket-1/9), whatever the names' order.
        self.register(attempt='ticket-1/9')
        self.collect('ticket-1/9', kind='retain', reason='the repair round reuses this context',
                     next_use='ticket-1 repair', evidence=['review finding F1'])
        self.register(attempt='ticket-1/10')
        self.lifecycle('result', 'ticket-1/10', status='failed', head='head-1', report=str(self.report),
                       evidence=['worker.log'])
        self.assertEqual(self.check(pane='w1:p1'), [
            f'{self.state}: ticket-1/10: Herdr tab w1:t5 is still open after collection; retire the worker or record '
            f'its retention ({ROOT}/runtime/workers/collect.md)'])

    def test_a_retention_reminder_alone_holds_the_stop_once(self):
        # Rule 3: a reminder is said once; beside a gap that holds, the usual three holds apply.
        self.register()
        self.retain()
        first = self.stop(pane='w1:p1')
        self.assertEqual(first['decision'], 'block')
        self.assertIn(f'- {self.reminder()}\n', first['reason'])
        second = self.stop(pane='w1:p1', active=True) or {}
        self.assertEqual((second.get('decision'), 'gave up holding the stop after 1 hold' in second.get('systemMessage', '')),
                         (None, True))
        self.assertIsNone(self.stop(pane='w1:p1', active=True))
        db = sqlite3.connect(self.state)
        db.execute('INSERT INTO attempts VALUES (?, ?)', ('bad/1', json.dumps({'attempt': 'bad/1', 'orchestrator': 'w1:p1'})))
        db.commit()
        db.close()
        self.assertEqual([(self.stop(pane='w1:p1', active=True) or {}).get('decision') for _ in range(4)],
                         ['block', 'block', 'block', None])

    def test_the_tabs_of_the_server_the_callers_socket_names_are_listed_through_that_socket(self):
        # Live (OpenCode on an isolated XDG_CONFIG_HOME): `--session default` found no socket under that config; the
        # server HERDR_SOCKET_PATH names is asked through the socket, any other server by name.
        self.register()
        self.collect()
        for socket, expected in ((str(self.repo / '.config' / 'herdr' / 'herdr.sock'), 'tab list --workspace w1'),
                                 (str(self.repo / '.config' / 'herdr' / 'sessions' / 's2' / 'herdr.sock'),
                                  '--session default tab list --workspace w1'),
                                 (str(self.repo / 'custom.sock'), '--session default tab list --workspace w1')):
            with self.subTest(socket=socket):
                (self.bin / 'argv').write_text('')
                done = subprocess.run([sys.executable, str(HOOK), 'check', str(self.repo)], capture_output=True, text=True,
                                      env=self.env('w1:p1', HERDR_SOCKET_PATH=socket))
                self.assertIn(self.gap(), json.loads(done.stdout)['missing'])
                self.assertIn(expected, (self.bin / 'argv').read_text().splitlines())

    def test_an_open_tab_holds_only_the_host_session_that_registered_it_in_a_shared_pane(self):
        # Rule (sessions sharing a folder): as for the gate's findings, the host session decides before the pane.
        self.lifecycle('register', pane='w1:p1', env={'CLAUDE_CODE_SESSION_ID': 'host-A'}, assignment='ticket-1',
                       repo=str(self.repo), worktree=str(self.repo), head='head-1', execution=str(self.execution),
                       runtime='claude', worker='builder-1', native_session='native-1', owned=True,
                       herdr={'session': 'default'}, packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.collect()
        self.assertEqual(self.check(pane='w1:p1', session='host-B'), [])
        self.assertEqual(self.check(pane='w1:p1', session='host-A'), [self.gap()])

    def test_a_retired_attempt_is_not_checked_for_an_open_tab(self):
        # Decision 4: retirement recorded closure only once Herdr reported its tab gone (`tab_not_found`), so a tab
        # Herdr lists under that ID later (an ID reused after a restart) is not this worker's: no gap, no Herdr call.
        self.register()
        self.collect()
        status = subprocess.run([sys.executable, '-B', str(LIFECYCLE), 'status', '--state', str(self.state)],
                                check=True, capture_output=True, text=True, env=self.env())
        for receipt in json.loads(status.stdout)['pending']:
            subprocess.run([sys.executable, '-B', str(LIFECYCLE), 'ack', '--state', str(self.state), '--event-id',
                            str(receipt['id'])], check=True, capture_output=True, env=self.env())
        self.assertEqual(self.check(pane='w1:p1'), [self.gap()])
        self.lifecycle('closed', evidence=['retirement proof: pane_not_found, tab_not_found'])
        self.assertEqual(self.check(pane='w1:p1'), [])
        self.serve(fail='connection refused')
        self.assertEqual(self.check(pane='w1:p1'), [])

    def test_the_session_bound_to_orchestrate_the_workstream_sees_its_stale_tabs_instead(self):
        # Rules 3 and 5: once dispatch binds a session to the workstream (a dispatch resumed in pane w1:p2, Claude session
        # host-2), that session orchestrates the run: it sees the open tab, and the pane that registered the worker,
        # or another conversation in w1:p2, does not.
        self.register(pane='w1:p1')
        self.collect()
        ticket = self.repo / '.scratch/workstream/issues/01-task.md'
        ticket.parent.mkdir(parents=True)
        ticket.write_text('---\nticket: workstream/01\nstatus: done\nblocked_by: []\ntouches: [a]\ndevices: []\n---\n')
        self.bind(pane='w1:p2', slug='workstream', CLAUDE_CODE_SESSION_ID='host-2')
        self.assertEqual(self.check(pane='w1:p2', session='host-2'), [self.gap()])
        self.assertEqual(self.check(pane='w1:p1'), [])
        self.assertEqual(self.check(pane='w1:p2', session='host-3'), [])

    def test_a_malformed_binding_is_one_gap_for_a_session_it_names_and_hides_no_other_gap(self):
        # Review 3: whether a session is bound is decided before anything else can fail. A binding that cannot be read
        # (torn JSON, or the wrong shape) is one named gap for a session whose pane or host session its raw text
        # names, and skipped for every other; the open tab of the run is still reported (by its registering pane).
        self.register()
        self.collect()
        binding = self.state.with_name('orchestrator.json')
        rebind = f'python3 {ROOT}/scripts/next_tickets.py --repo {self.repo} workstream --orchestrate'
        gap = f'{binding}: the orchestrator binding cannot be read; rebind with `{rebind}`'
        binding.write_text('{"pane": "w1:p1", "sessions": ["host-1"')
        self.assertEqual(self.check(pane='w1:p1'), [self.gap(), gap])
        self.assertEqual(self.check(pane=None, session='host-1'), [gap])
        self.assertEqual(self.check(pane='w1:p9', session='host-9'), [])
        binding.write_text('["w1:p9"]')
        self.assertEqual(self.check(pane='w1:p9'), [gap])
        self.assertEqual(self.check(pane='w1:p1'), [self.gap()])
        # Re-review F3: an ID is matched whole, so w1:p1 is not named by w1:p12, nor host-1 by host-12 or ghost-1.
        binding.write_text('{"pane": "w1:p12", "sessions": ["host-12", "ghost-1"')
        self.assertEqual(self.check(pane='w1:p1'), [self.gap()])
        self.assertEqual(self.check(pane=None, session='host-1'), [])
        self.assertEqual(self.check(pane='w1:p12'), [gap])

    def test_a_closed_workspace_or_stopped_herdr_session_means_the_tab_is_gone(self):
        # Review 2: real Herdr answers workspace_not_found for a closed workspace and server_not_running for a stopped
        # session; either way the tab is gone, so neither is a gap. Any other failure stays a named gap.
        self.register()
        self.collect()
        self.serve(tabs={'default': [dict(self.tab, tab_id='w2:t1', workspace_id='w2')]})
        self.assertEqual(self.check(pane='w1:p1'), [])
        self.serve(tabs={'other': [self.tab]})
        self.assertEqual(self.check(pane='w1:p1'), [])
        self.serve(tabs={'default': [self.tab]}, fail='connection refused')
        self.assertEqual(self.check(pane='w1:p1'), [
            'could not check worker tabs: herdr tab list exited 1: '
            '{"error": {"code": "server_unavailable", "message": "connection refused"}}'])

    def test_a_herdr_that_cannot_list_tabs_is_a_named_gap_that_hides_no_other_gap(self):
        # Rule 5: when Herdr fails or does not answer, the tab check is a named gap for the orchestrator, held once,
        # and the run's other gaps (here a live worker with no wait) still show.
        self.register()
        self.collect()
        self.lifecycle('register', 'ticket-2/1', assignment='ticket-2', repo=str(self.repo), worktree=str(self.repo),
                       head='head-1', execution=str(self.execution), runtime='claude', worker='pane-2',
                       native_session='native-2', owned=True,
                       packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.lifecycle('startup', 'ticket-2/1', native=str(self.native('ticket-2/1')))
        self.lifecycle('submitted', 'ticket-2/1', evidence=['native.jsonl:2:accepted prompt'])
        self.serve(fail='connection refused')
        gap = ('could not check worker tabs: herdr tab list exited 1: '
               '{"error": {"code": "server_unavailable", "message": "connection refused"}}')
        self.assertEqual(self.check(pane='w1:p1'), [f'{self.state}: ticket-2/1: no armed wait', gap])
        self.assertIn(f'- {gap}\n', self.stop(pane='w1:p1')['reason'])
        # The live worker's gap stands beside the failed check, so the hold is repeated, not given up after one.
        self.assertEqual(self.stop(pane='w1:p1', active=True)['decision'], 'block')
        self.serve(hang=True)
        self.assertRegex(self.check(pane='w1:p1')[-1],
                         r"^could not check worker tabs: .*'tab', 'list'.* timed out after 5 seconds$")


class StopReap(HerdrWorkers):
    """At a stop the gate closes the attempts whose status is already decided (`worker_lifecycle.py reap`); `check`
    only reports."""
    GONE = {'default': [{'tab_id': 'w1:t6', 'workspace_id': 'w1', 'label': 'other', 'pane_count': 1}]}

    def attempt(self, attempt='ticket-1/1'):
        db = sqlite3.connect(self.state)
        try:
            return json.loads(db.execute('SELECT data FROM attempts WHERE id = ?', (attempt,)).fetchone()[0])
        finally:
            db.close()

    def stop_command(self, pane='w1:p1', session=None):
        """What `yield_gate.py stop <cwd> [session]` answers, as Pi's extension and other hosts call it."""
        done = subprocess.run([sys.executable, str(HOOK), 'stop', str(self.repo), *filter(None, [session])],
                              capture_output=True, text=True, timeout=20, env=self.env(pane))
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_a_stop_closes_a_collected_attempt_whose_herdr_tab_is_confirmed_gone_and_a_check_does_not(self):
        # Rule 1: the claude and codex hooks and the `stop` command close it, with Herdr's answer as the evidence;
        # `check` stays a pure report. The tab Herdr still lists is left alone.
        evidence = ['herdr --session default tab get w1:t5: tab_not_found']
        for mode in ('claude', 'codex', 'command'):
            with self.subTest(mode=mode):
                self.setUp()
                self.register()
                self.collect()
                self.assertEqual([self.stale_gap()], self.check(pane='w1:p1'))
                self.serve(tabs=self.GONE)
                self.assertEqual([], self.check(pane='w1:p1'))
                self.assertNotIn('closed', self.attempt())
                if mode == 'command':
                    self.assertEqual([], self.stop_command()['missing'])
                else:
                    self.assertIsNone(self.stop(host=mode))
                self.assertEqual(evidence, self.attempt()['closed']['evidence'])
        self.setUp()
        self.register()
        self.collect()
        self.assertIn(f'- {self.stale_gap()}\n', self.stop()['reason'])
        self.assertNotIn('closed', self.attempt())

    def stale_gap(self):
        return (f'{self.state}: ticket-1/1: Herdr tab w1:t5 is still open after collection; retire the worker or '
                f'record its retention ({ROOT}/runtime/workers/collect.md)')

    def refusal(self):
        return ('Settle each attempt of the conversation before closing it: ticket-1/1 lacks an intact report (record '
                f'it declined: `python3 {ROOT}/scripts/worker_lifecycle.py record --state {shlex.quote(str(self.state))} --input '
                '<JSON {"kind": "declined", "attempt": "ticket-1/1", "reason", "evidence"}>`)')

    def test_a_stop_closes_only_the_attempts_the_stopping_session_registered(self):
        # Rule 1's attribution is the holds': the host session decides before the pane, so another session in the
        # same pane, or a pane with no session, closes nothing.
        self.register(env={'CLAUDE_CODE_SESSION_ID': 'host-A'})
        self.collect()
        self.serve(tabs=self.GONE)
        self.stop(session='host-B')
        self.stop(pane='w1:p9')
        self.assertNotIn('closed', self.attempt())
        self.stop(session='host-A')
        self.assertIn('closed', self.attempt())

    def test_a_stop_closes_a_retained_native_attempt_once_its_run_card_ended(self):
        # Rule 2: no Herdr placement, retained, and the workstream's run card ended delivered, delivered-local or
        # merged; a card still running keeps it.
        self.register(herdr=None)
        self.collect(kind='retain', reason='the repair round reuses this context', next_use='ticket-1 repair',
                     evidence=['review finding F1'])
        card = self.state.parent.parent / 'autopilot.md'
        for terminal in ('', 'blocked', 'delivered'):
            card.write_text(f'# workstream\n\n## State\nconfirmed: yes\nterminal: {terminal}\n')
            self.assertIsNone(self.stop())
            self.assertEqual(terminal == 'delivered', 'closed' in self.attempt())
        self.assertEqual([f'{card}: terminal: delivered'], self.attempt()['closed']['evidence'])

    def test_a_closure_the_lifecycle_refuses_is_a_named_stop_gap_and_no_other_closure_waits_on_it(self):
        # Rule 3: a completed report that changed after collection cannot be closed over; the gap carries the
        # lifecycle's refusal and the command to run once settled, the hook still exits cleanly, and the other
        # attempt is closed. `check` names nothing: it settles nothing.
        second = dict(self.tab, tab_id='w1:t6')
        self.serve(agents=[{'agent': 'claude', 'agent_status': 'idle', 'name': name, 'pane_id': pane,
                            'tab_id': tab, 'workspace_id': 'w1'} for name, pane, tab in
                           (('builder-1', 'w1:p5', 'w1:t5'), ('builder-2', 'w1:p6', 'w1:t6'))],
                   tabs={'default': [self.tab, second]})
        self.register()
        self.collect()
        self.register(attempt='ticket-2/1', assignment='ticket-2', worker='builder-2', native_session='native-2')
        self.lifecycle('result', 'ticket-2/1', status='failed', head='head-1', report=str(self.report),
                       evidence=['worker.log'])
        db = sqlite3.connect(self.state)
        data = json.loads(db.execute("SELECT data FROM attempts WHERE id = 'ticket-1/1'").fetchone()[0])
        data['result']['status'] = 'completed'
        db.execute("UPDATE attempts SET data = ? WHERE id = 'ticket-1/1'", (json.dumps(data),))
        db.commit()
        db.close()
        self.report.write_text('## Implemented\n\nrewritten after collection\n')
        self.serve(tabs={'default': [dict(self.tab, tab_id='w1:t9')]})
        gap = (f'{self.state}: ticket-1/1: not closed at stop: {self.refusal()}; settle it, then run '
               f'`python3 {ROOT}/scripts/worker_lifecycle.py reap --state {self.state}`')
        self.assertEqual([], self.check())
        decision = self.stop()
        self.assertEqual('block', decision['decision'])
        self.assertIn(f'- {gap}\n', decision['reason'])
        self.assertNotIn('closed', self.attempt('ticket-1/1'))
        self.assertIn('closed', self.attempt('ticket-2/1'))
        self.assertEqual([gap], self.stop_command()['missing'])

    def test_a_state_reap_cannot_read_through_is_a_named_gap_not_a_crash(self):
        self.register()
        self.collect()
        self.serve(tabs=self.GONE)
        db = sqlite3.connect(self.state)
        db.execute("DELETE FROM events WHERE kind = 'register'")
        db.commit()
        db.close()
        self.assertEqual([f'could not check settled workers in {self.state}: IndexError(\'list index out of range\')'],
                         self.stop_command()['missing'])

    def test_a_herdr_server_that_does_not_answer_is_asked_once_so_the_stop_stays_inside_the_hook_timeout(self):
        # Rule 1: no answer is no confirmation, and a server that hangs is not asked again for each of its other
        # workers: the 20 s hook budget holds one wait of 5 s, not one per worker.
        second = dict(self.tab, tab_id='w1:t6')
        self.serve(agents=[{'agent': 'claude', 'agent_status': 'idle', 'name': name, 'pane_id': pane,
                            'tab_id': tab, 'workspace_id': 'w1'} for name, pane, tab in
                           (('builder-1', 'w1:p5', 'w1:t5'), ('builder-2', 'w1:p6', 'w1:t6'))],
                   tabs={'default': [self.tab, second]})
        for number in (1, 2):
            self.register(attempt=f'ticket-{number}/1', assignment=f'ticket-{number}', worker=f'builder-{number}',
                          native_session=f'native-{number}')
            self.lifecycle('result', f'ticket-{number}/1', status='failed', head='head-1', report=str(self.report),
                           evidence=['worker.log'])
        self.serve(hang=True)
        calls = self.bin / 'calls'
        calls.write_text('')
        self.stop_command()
        self.assertEqual(1, calls.read_text().count(' tab get '))
        self.assertNotIn('closed', self.attempt('ticket-1/1'))
        self.assertNotIn('closed', self.attempt('ticket-2/1'))

    def test_one_stop_reaps_every_state_inside_one_budget(self):
        # Review 2: each run state had a budget of its own, so several states on a hung Herdr outlasted the 20 s hook
        # timeout; one deadline covers them all and leaves the states it did not reach for the next stop.
        self.serve(agents=[{'agent': 'claude', 'agent_status': 'idle', 'name': 'builder-1', 'pane_id': 'w1:p5',
                            'tab_id': 'w1:t5', 'workspace_id': 'w1'}], tabs={'default': [self.tab]})
        self.register()
        self.lifecycle('result', status='failed', head='head-1', report=str(self.report), evidence=['worker.log'])
        for name in ('other-1', 'other-2', 'other-3'):
            copy = self.repo / '.scratch' / name / 'reports' / 'workers.sqlite'
            copy.parent.mkdir(parents=True)
            shutil.copy(self.state, copy)
        self.serve(hang=True)
        started = time.monotonic()
        self.stop_command()
        # The reap's five seconds plus the gate's own Herdr checks, well inside the hosts' 20 s.
        self.assertLess(time.monotonic() - started, 15)

    def test_a_stop_reaps_the_rows_the_lifecycle_reads_not_only_those_the_gate_validates(self):
        # The gate calls a placement without tab_id malformed and names it; the lifecycle still judges it by its pane,
        # so the stop reap does too.
        self.register()
        self.collect()
        db = sqlite3.connect(self.state)
        data = self.attempt()
        del data['herdr']['tab_id']
        db.execute("UPDATE attempts SET data = ? WHERE id = 'ticket-1/1'", (json.dumps(data),))
        db.commit()
        db.close()
        self.stop_command()
        self.assertEqual(['herdr --session default pane get w1:p5: pane_not_found'],
                         self.attempt()['closed']['evidence'])


class RepairFindingsHold(GateRepo):
    def repair(self, attempt, findings, pane):
        """Register, from Herdr pane `pane`, a repair attempt whose packet's findings: line is findings."""
        packet = support.assignment_file(self.execution.parent, 'repair')
        packet = packet.rename(packet.with_name(attempt.replace('/', '-') + '.txt'))
        packet.write_text(packet.read_text().replace('findings: /fixture/findings', f'findings: {findings}'))
        self.lifecycle('register', attempt=attempt, pane=pane, assignment=attempt.split('/')[0], repo=str(self.repo),
                       worktree=str(self.repo), head='head-1', execution=str(self.execution), runtime='claude',
                       worker='pane-' + attempt, native_session='native-' + attempt, owned=True, packet=str(packet))

    def settle(self, attempt, pane, **findings):
        report = self.state.parent / (attempt.replace('/', '-') + '.md')
        report.write_text('## Fixed round 1\n\nBlocked on a seam\n')
        self.lifecycle('result', attempt=attempt, pane=pane, status='blocked', head='head-1', report=str(report),
                       evidence=['seam decision needed'], findings=findings)

    def plan(self, *findings, replace=False):
        subprocess.run([sys.executable, '-B', str(LIFECYCLE), 'plan', '--state', str(self.state), '--job', 'repair',
                        *(['--replace'] if replace else []), *[part for finding in findings
                                                               for part in ('--finding', finding)]],
                       check=True, capture_output=True)

    def test_a_stop_is_held_for_planned_findings_no_repair_assignment_holds_once_nothing_runs(self):
        # Live (stforestkit remediate-taut-534): every repair worker was done, 90 confirmed findings were never assigned,
        # and Pi's stop passed. A session that registered a repair holding findings of the plan is held once no repair
        # runs anywhere in the run; what any session's attempts hold is held.
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': 'builder',
                                              'job': 'repair', 'settings': {'model': 'sonnet'}}))
        self.plan('P01-001', 'P04-001', 'P05-001')
        self.repair('r1/1', 'P01-001', 'w1:p1')
        self.settle('r1/1', 'w1:p1', **{'P01-001': 'open'})
        self.repair('r2/1', 'P05-001', 'w1:p2')
        self.assertEqual(self.check(), [])  # Another session's repair still runs.
        self.settle('r2/1', 'w1:p2', **{'P05-001': 'open'})
        command = shlex.join(['python3', str(LIFECYCLE), 'plan', '--state', str(self.state), '--job', 'repair',
                              '--replace'])
        gap = (f'{self.state}: No repair assignment holds 1 of the 3 planned repair findings (P04-001): register a '
               "repair attempt whose packet's findings: names them, or, on the user's decision, narrow the plan with "
               f'`{command} [--finding <ID>...]`')
        self.assertEqual(self.check(), [gap])
        self.assertEqual(self.check(pane='w1:p2'), [gap])
        self.assertIn(gap, self.stop()['reason'])
        self.assertEqual(self.check(pane='w1:p3'), [])  # A session that registered none of the plan's repairs.
        # Once the plan no longer holds what a session's repairs hold, that session is not held for it.
        self.plan('P04-001', replace=True)
        self.assertEqual(self.check(), [])

    def test_a_repair_registered_with_no_worker_behind_it_does_not_end_the_hold(self):
        # Live (remediate-loopholes, Pi at 3f9444c): held for the unassigned P04-001, Pi registered r1/2 naming it on a
        # native-child route with no worker behind it, and its stop passed. Every host's stop names that attempt until
        # its startup is recorded (NativeProof pins that) or a cancelled result settling its findings is collected.
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': 'builder',
                                              'job': 'repair', 'settings': {'model': 'sonnet'}}))
        self.plan('P01-001', 'P04-001')
        self.repair('r1/1', 'P01-001', 'w1:p1')
        self.settle('r1/1', 'w1:p1', **{'P01-001': 'open'})
        self.repair('r1/2', 'P04-001', 'w1:p1')
        gap = (f'{self.state}: r1/2: native child with no recorded startup; record `startup` with native: its session '
               'record, or `collect` a cancelled result if it never started')
        self.assertEqual(self.check(), [gap])
        for host in ('claude', 'codex'):
            self.assertIn(gap, self.stop(host=host)['reason'])
        report = self.state.parent / 'r1-2.md'
        report.write_text('## Fixed round 1\n\nNo worker was started\n')
        request = self.state.parent / 'collect.json'
        request.write_text(json.dumps({'status': 'cancelled', 'head': 'head-1', 'report': str(report),
                                       'evidence': ['no worker was started'], 'findings': {'P04-001': 'open'},
                                       'disposition': {'kind': 'retire', 'evidence': ['no worker was started']}}))
        subprocess.run([sys.executable, '-B', str(LIFECYCLE), 'collect', '--state', str(self.state), '--attempt', 'r1/2',
                        '--input', str(request)], check=True, capture_output=True, env=self.env())
        self.assertEqual(self.check(), [])


class AutopilotCardHold(GateRepo):
    """A session that ran a confirmed autopilot card is held until the card records one terminal state with evidence."""
    STATES = ('delivered', 'delivered-local', 'merged', 'waiting-on-user', 'paused-allowance', 'paused-by-user',
              'blocked')

    def card(self, slug='run', confirmed='2026-10-27 "go"', session='host-1', terminal='', evidence='', phase='refine',
             board='local', **extra):
        path = self.repo / f'.scratch/{slug}/autopilot.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = dict(confirmed=confirmed, session=session, **{'auto-merge': 'no'}, **({'board': board} if board else {}),
                      phase=phase, terminal=terminal, history='', evidence=evidence, **extra)
        path.write_text(f'# {slug} \u2014 autopilot run card\n\n## Goal\nx\n\n## State\n'
                        + ''.join(f'{key}: {value}\n'.replace(': \n', ':\n') for key, value in fields.items()))
        return path

    def gap(self, path):
        return (f"{path}: this session's autopilot run has no terminal state or no evidence; record `terminal:` as "
                f"exactly one of {', '.join(f'`{state}`' for state in self.STATES)} and `evidence:` in the card's State")

    def test_a_run_in_no_known_phase_or_working_without_its_boards_tickets_holds_the_stop(self):
        # Live (jose, Pi): the card said `phase: dispatch (inline)`, set by hand with no ticket on any board, so the
        # panel had nothing to count; a phase written by hand is held to what `autopilot_card.py phase` checks.
        path = self.card(phase='dispatch (inline)')
        setter = shlex.join(['python3', str(ROOT / 'scripts/autopilot_card.py'), 'phase', str(path), '<phase>'])
        self.assertEqual(self.check(session='host-1'), [
            f"{path}: this session's autopilot run has phase 'dispatch (inline)', none of align, refine, dispatch, pr: "
            f'set it with `{setter}`', self.gap(path)])
        new = shlex.join(['python3', str(ROOT / 'scripts/next_tickets.py'), '--repo', str(self.repo), 'run', '--new',
                          '<title>'])
        for phase in ('dispatch', 'pr'):
            with self.subTest(phase=phase):
                self.card(phase=phase)
                self.assertEqual(self.check(session='host-1'), [
                    f"{path}: this session's autopilot run is in {phase} with no tickets on its local board: write each "
                    f'with `{new}`', self.gap(path)])
        for board in (None, 'jira', 'https://'):
            with self.subTest(board=board):
                self.card(phase='dispatch', board=board)
                self.assertEqual(self.check(session='host-1'), [
                    f"{path}: this session's autopilot run is in dispatch with no board: write `board: local` or the "
                    "tracker's URL in the card's State", self.gap(path)])
        self.card(phase='dispatch', board='https://linear.app/team/project/x')
        self.assertEqual(self.check(session='host-1'), [self.gap(path)])
        # Review (Codex, Sonnet): only a ticket file is a ticket; notes beside them are not.
        (path.parent / 'issues').mkdir()
        (path.parent / 'issues' / 'history.md').write_text('# T1 done\nticket: run/01, in the ledger\n')
        (path.parent / 'issues' / '00-draft.md').write_text('---\nticket: run/00\n')  # Never closed: no ticket.
        self.card(phase='pr')
        self.assertEqual(len(self.check(session='host-1')), 2)
        # Review (Codex): a section after the State is not the State.
        self.card(phase='dispatch (inline)')
        path.write_text(path.read_text() + '\n## Notes\nphase: refine\n')
        self.assertIn("has phase 'dispatch (inline)'", self.check(session='host-1')[0])
        (path.parent / 'issues' / '01-task.md').write_text('---\nticket: run/01\nstatus: ready\n---\n')
        self.card(phase='dispatch')
        self.assertEqual(self.check(session='host-1'), [self.gap(path)])
        self.card(phase='dispatch (inline)', session='host-2')
        self.assertEqual(self.check(session='host-1'), [])

    def test_a_confirmed_card_of_this_session_without_a_terminal_state_or_evidence_holds_the_stop(self):
        # Rule: no run ends silently. Only this session's confirmed card with `terminal:` or `evidence:` empty holds.
        path = self.card()
        self.assertEqual(self.check(session='host-1'), [self.gap(path)])
        self.assertEqual(self.stop(session='host-1')['reason'], f'Stopping now leaves these gaps:\n- {self.gap(path)}\n')
        self.card(terminal='delivered')
        self.assertEqual(self.check(session='host-1'), [self.gap(path)])
        self.card(evidence='abc123')
        self.assertEqual(self.check(session='host-1'), [self.gap(path)])
        self.card(terminal='finished-ish', evidence='abc123')
        self.assertEqual(self.check(session='host-1'), [self.gap(path)])
        for state in self.STATES:  # A tracker holds its own tickets; a local board's delivery is checked below.
            self.card(terminal=state, evidence='abc123', board='https://linear.app/team/project/x')
            self.assertEqual(self.check(session='host-1'), [], state)
        self.card(confirmed='no')
        self.assertEqual(self.check(session='host-1'), [])
        self.card(session='host-2')
        self.assertEqual(self.check(session='host-1'), [])
        self.card(session='host-1')
        self.assertEqual(self.check(), [])
        # Rule (card-confirm): a card names each host session its confirming shell exported; each one is held.
        path = self.card(session='outer-1, host-1')
        self.assertEqual((self.check(session='host-1'), self.check(session='outer-1')), ([self.gap(path)], [self.gap(path)]))
        self.assertEqual((self.check(session='host-2'), self.check(session='host')), ([], []))
        # Review (Codex): `confirm` takes a State heading with trailing spaces; the hold reads the same card.
        path.write_text(path.read_text().replace('\n## State\n', '\n## State  \n'))
        self.assertEqual(self.check(session='host-1'), [self.gap(path)])

    def test_a_delivered_terminal_state_holds_the_stop_while_its_workstream_is_not_delivered(self):
        # User (2026-11-07, "Go ahead with all three"): Pi's jose card recorded `terminal: merged` while its board held a
        # live ticket and the undone delivery report; delivered, delivered-local and merged are held to the workstream's
        # delivery: its own check problems, its tickets done and the evidence `next_tickets.py --delivery` judges.
        new = shlex.join(['python3', str(ROOT / 'scripts/next_tickets.py'), '--repo', str(self.repo), 'run', '--new',
                          '<title>'])
        path = self.card(terminal='merged', evidence='PR 1 merged', phase='pr')

        def held(problems, terminal='merged'):
            return [f"{path}: this session's autopilot run records `terminal: {terminal}` while its workstream is not "
                    f"delivered: {problems}; finish what it names, or record the terminal state that fits"]
        self.assertEqual(self.check(session='host-1'), held(f'its local board has no tickets: write each with `{new}`'))
        issues = self.repo / '.scratch/run/issues'
        issues.mkdir(parents=True)
        ticket = issues / '01-task.md'
        ticket.write_text('---\nticket: run/01\nstatus: implementing\nblocked_by: []\ntouches: []\ndevices: []\n---\n')
        review = self.repo / '.scratch/run/reports/spec-review.md'
        for terminal in ('delivered', 'delivered-local', 'merged'):
            with self.subTest(terminal=terminal):
                self.card(terminal=terminal, evidence='PR 1 merged', phase='pr')
                self.assertEqual(self.check(session='host-1'),
                                 held(f'tickets not done: run/01 (implementing); no review report at {review}', terminal))
        # Review (Sonnet): a delivered card names its board, or nothing would hold it.
        for board in (None, 'jira', 'Local'):
            with self.subTest(board=board):
                self.card(terminal='merged', evidence='x', phase='pr', board=board)
                self.assertEqual(self.check(session='host-1'), [
                    f"{path}: this session's autopilot run records `terminal: merged` with no board: write "
                    "`board: local` or the tracker's URL in the card's State"])
        # The other terminal states, another session's card and a tracker's board are not held to it.
        for fields in (dict(terminal='blocked'), dict(terminal='waiting-on-user'), dict(terminal='merged', session='host-2'),
                       dict(terminal='merged', board='https://linear.app/team/project/x')):
            with self.subTest(fields=fields):
                self.card(**{'evidence': 'x', 'phase': 'pr', **fields})
                self.assertEqual(self.check(session='host-1'), [])
        # A workstream that delivered: its ticket done and accepted, reviewed and E2E-passed at main's head.
        git = ['git', '-C', str(self.repo), '-c', 'user.name=t', '-c', 'user.email=t@t']
        subprocess.run([*git, 'checkout', '-q', '-B', 'main'], check=True)
        heads = []
        for message in ('start', 'work'):
            subprocess.run([*git, 'commit', '-q', '--allow-empty', '-m', message], check=True)
            heads.append(subprocess.run([*git, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip())
        start, head = heads
        accepted = ticket.read_text().replace('status: implementing', f"status: done\naccepted: {'a' * 40}\n"
                                                                       f"accepted_report: {'b' * 64}")
        ticket.write_text(accepted)
        review.parent.mkdir(parents=True, exist_ok=True)
        evidence = (f'---\nworkstream_branch: main\nreview_status: clean\ndocumentation_status: passed\n'
                    f'review_head: {head}\nreview_base: {start}\nreview_diff_base: {start}\ne2e_status: passed\n'
                    f'e2e_head: {head}\nreview_by: review/run/1\ne2e_by: e2e/run/1\n---\n')
        review.write_text(evidence)
        support.collected_attempt(self.repo, 'run', 'review/run/1', review, job='diff-review', head=head)
        support.collected_attempt(self.repo, 'run', 'e2e/run/1', review, job='e2e-gate', head=head)
        self.card(terminal='merged', evidence='PR 1 merged', phase='pr')
        self.assertEqual(self.check(session='host-1'), [])
        # Review (Sonnet): another workstream's problem is not this session's to fix, so it holds nothing here; this
        # workstream's own does.
        other = self.repo / '.scratch/other/issues'
        other.mkdir(parents=True)
        (other / '01-task.md').write_text('---\nticket: other/01\nstatus: done\nblocked_by: []\ntouches: []\ndevices: []\n---\n')
        (other / '02-task.md').write_text('---\nticket: other/02\nstatus: implementing\nblocked_by: []\ntouches: []\n'
                                          'devices: []\n---\n')
        (other / '03-task.md').write_text('---\nticket: other/03\nstatus: ready\nblocked_by: x\ntouches: []\ndevices: []\n---\n')
        self.assertEqual(self.check(session='host-1'), [])
        # Review (Codex): nor is another workstream's file that cannot be read, as text or at all.
        (other / '04-task.md').write_bytes(b'---\nticket: other/04\nstatus: \xff\n---\n')
        unreadable = other / '05-task.md'
        unreadable.write_text('---\nticket: other/five\n---\n')
        unreadable.chmod(0)
        self.addCleanup(unreadable.chmod, 0o644)
        self.assertEqual(self.check(session='host-1'), [])
        # Review (Codex): a ticket of it waiting on one in another workstream's refused file waits on a ticket the board
        # cannot judge, not on one missing, so leaving that file out makes no problem of its own.
        ours = issues / '02-task.md'
        ours.write_text(accepted.replace('run/01', 'run/02').replace('blocked_by: []', 'blocked_by: [other/03, other/04]'))
        self.assertEqual(self.check(session='host-1'), [])
        # Review (Codex): one it waits on that no file there holds is still missing.
        ours.write_text(accepted.replace('run/01', 'run/02').replace('blocked_by: []', 'blocked_by: [other/99]'))
        self.assertEqual(self.check(session='host-1'), held('run/02: blocked_by other/99 does not exist'))
        ours.unlink()
        # A cycle between this workstream and another is its own.
        ours = issues / '02-task.md'
        ours.write_text(accepted.replace('run/01', 'run/02').replace('blocked_by: []', 'blocked_by: [other/01]'))
        (other / '01-task.md').write_text('---\nticket: other/01\nstatus: done\nblocked_by: [run/01]\ntouches: []\n'
                                          f"devices: []\naccepted: {'a' * 40}\naccepted_report: {'b' * 64}\n---\n")
        cycle = self.check(session='host-1')
        self.assertEqual(len(cycle), 1)
        self.assertRegex(cycle[0], r"while its workstream is not delivered: workstream dependency cycle: "
                                   r"(other -> run -> other|run -> other -> run); finish what it names")
        ours.unlink()
        (other / '01-task.md').write_text('---\nticket: other/01\nstatus: done\nblocked_by: []\ntouches: []\n'
                                          f"devices: []\naccepted: {'a' * 40}\naccepted_report: {'b' * 64}\n---\n")
        # Review (Codex): the check names every cycle, so another workstream's cycle hides none of this one's.
        alpha = self.repo / '.scratch/alpha/issues'
        alpha.mkdir(parents=True)
        for number, waits in (('01', '02'), ('02', '01')):
            (alpha / f'{number}-task.md').write_text(
                f'---\nticket: alpha/{number}\nstatus: done\nblocked_by: [alpha/{waits}]\ntouches: []\ndevices: []\n'
                f"accepted: {'a' * 40}\naccepted_report: {'b' * 64}\n---\n")
        ours.write_text(accepted.replace('run/01', 'run/02').replace('blocked_by: []', 'blocked_by: [run/02]'))
        cycle = self.check(session='host-1')
        self.assertEqual(len(cycle), 1)
        self.assertIn('while its workstream is not delivered: ticket dependency cycle: run/02 -> run/02; finish', cycle[0])
        # Review (Codex): nor does a cycle sharing an edge with this one, as alpha/01 <- beta/01 <- alpha/01 shares
        # beta/01 <- alpha/01 with alpha/01 <- run/02 <- beta/01 <- alpha/01.
        beta = self.repo / '.scratch/beta/issues'
        beta.mkdir(parents=True)
        for written, name, waits in ((alpha / '01-task.md', 'alpha/01', 'beta/01, run/02'),
                                     (alpha / '02-task.md', 'alpha/02', ''), (beta / '01-task.md', 'beta/01', 'alpha/01')):
            written.write_text(f'---\nticket: {name}\nstatus: done\nblocked_by: [{waits}]\ntouches: []\ndevices: []\n'
                            f"accepted: {'a' * 40}\naccepted_report: {'b' * 64}\n---\n")
        ours.write_text(accepted.replace('run/01', 'run/02').replace('blocked_by: []', 'blocked_by: [beta/01]'))
        cycle = self.check(session='host-1')
        self.assertEqual(len(cycle), 1)
        self.assertRegex(cycle[0], r'ticket dependency cycle: [^;]*run/02')
        self.assertRegex(cycle[0], r'workstream dependency cycle: [^;]*run')
        ours.unlink()
        ticket.write_text(accepted.replace(f"accepted: {'a' * 40}\naccepted_report: {'b' * 64}\n", ''))
        self.assertEqual(self.check(session='host-1'), held(
            'run/01: status done with no acceptance; accept it with --accept run/01, or --accept run/01 --pr <url> when '
            'PRs delivered it, or --accept run/01 --run <url> when a successful CI run proves it'))
        ticket.write_text(accepted)
        # Review (Sonnet): after the merge the pr procedure deletes the workstream branch; a finished workstream (its
        # branch gone, its reviewed and E2E heads in main) is delivered, though `--delivery` now needs the branch.
        review.write_text(evidence.replace('workstream_branch: main', 'workstream_branch: feat/run'))
        self.assertEqual(self.check(session='host-1'), [])

    def test_a_ticket_file_that_is_not_text_is_named_not_taken_for_no_tickets(self):
        # Review (Codex): the board refuses a ticket file that is not text, so `--new`, the remedy for a board with no
        # tickets, exits on that file too; the stop names the file instead.
        issues = self.repo / '.scratch/run/issues'
        issues.mkdir(parents=True)
        (issues / '01-task.md').write_bytes(b'\xff---\n')
        path = self.card(terminal='merged', evidence='PR 1 merged', phase='pr')
        self.assertEqual(self.check(session='host-1'), [
            f"{path}: this session's autopilot run records `terminal: merged` while its workstream is not delivered: "
            f"it cannot be checked: {issues / '01-task.md'}: cannot be read: 'utf-8' codec can't decode byte 0xff in "
            "position 0: invalid start byte; finish what it names, or record the terminal state that fits"])
        # Review (Codex): so is one holding a ticket's keys but no `ticket:`, which `--new` exits on as well.
        (issues / '01-task.md').write_text('---\nstatus: ready\nblocked_by: []\ntouches: []\ndevices: []\n---\n')
        self.assertEqual(self.check(session='host-1'), [
            f"{path}: this session's autopilot run records `terminal: merged` while its workstream is not delivered: "
            f"it cannot be checked: {issues / '01-task.md'}: missing ticket front matter; finish what it names, or "
            "record the terminal state that fits"])

    def test_a_delivery_that_cannot_be_checked_still_holds_the_stop(self):
        # Review (Codex, Sonnet): a check that cannot run is a named gap, never a pass.
        issues = self.repo / '.scratch/run/issues'
        issues.mkdir(parents=True)
        (issues / '01-task.md').write_text('---\nticket: run/one\nstatus: done\nblocked_by: []\ntouches: []\ndevices: []\n---\n')
        path = self.card(terminal='merged', evidence='PR 1 merged', phase='pr')
        self.assertEqual(self.check(session='host-1'), [
            f"{path}: this session's autopilot run records `terminal: merged` while its workstream is not delivered: "
            f"it cannot be checked: {issues / '01-task.md'}: invalid ticket id 'run/one'; finish what it names, or "
            "record the terminal state that fits"])
        # Review (Sonnet): so is a check that raises on the way, here a worker state with no tables.
        (issues / '01-task.md').write_text('---\nticket: run/01\nstatus: done\nblocked_by: []\ntouches: []\ndevices: []\n'
                                           f"accepted: {'a' * 40}\naccepted_report: {'b' * 64}\n---\n")
        reports = self.repo / '.scratch/run/reports'
        reports.mkdir(parents=True)
        (reports / 'spec-review.md').write_text('---\nreview_status: clean\ndocumentation_status: passed\n'
                                                'e2e_status: passed\nreview_by: review/run/1\ne2e_by: e2e/run/1\n---\n')
        sqlite3.connect(reports / 'workers.sqlite').close()
        self.assertEqual(self.check(session='host-1'), [
            f"{path}: this session's autopilot run records `terminal: merged` while its workstream is not delivered: "
            "it cannot be checked: no such table: attempts; finish what it names, or record the terminal state that "
            "fits"])
        # Review (Sonnet): a ticket file of its own that cannot be read is named as the board names it, not raised.
        unreadable = issues / '02-task.md'
        unreadable.write_text('---\nticket: run/02\n---\n')
        unreadable.chmod(0)
        self.addCleanup(unreadable.chmod, 0o644)
        self.assertEqual(self.check(session='host-1'), [
            f"{path}: this session's autopilot run records `terminal: merged` while its workstream is not delivered: "
            f"it cannot be checked: {unreadable}: cannot be read: [Errno 13] Permission denied: '{unreadable}'; finish "
            "what it names, or record the terminal state that fits"])

    def test_a_live_worker_with_a_running_wait_lets_the_run_yield_but_a_lost_one_does_not(self):
        # Rule: a yield while a worker of the card's workstream holds a running wait that will wake the host is not an end;
        # the card hold applies again once the wait is lost.
        path = self.card(slug='workstream')
        self.lifecycle('register', 'workstream/01/1', assignment='workstream/01', repo=str(self.repo), worktree=str(self.repo),
                       head='head-1', execution=str(self.execution), runtime='claude', worker='pane-1',
                       native_session='native-1', owned=True,
                       packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.lifecycle('startup', 'workstream/01/1', native=str(self.native('workstream/01/1')))
        self.lifecycle('submitted', 'workstream/01/1', evidence=['native.jsonl:2:accepted prompt'])
        pidfile, request = self.state.parent / 'wait.pid', self.state.parent / 'wait.json'
        request.write_text(json.dumps({'argv': [sys.executable, '-c',
            f'import os,time; open({str(pidfile)!r}, "w").write(str(os.getpid())); time.sleep(30)'], 'timeout': 40,
            'notify': {'argv': [sys.executable, '-c', 'pass', '{receipt}'], 'timeout': 2}}))
        watcher = subprocess.Popen([sys.executable, '-B', str(LIFECYCLE), 'watch', '--state', str(self.state),
                                    '--attempt', 'workstream/01/1', '--input', str(request)], stdout=subprocess.PIPE,
                                   text=True, env=self.env('w1:p1'))
        self.addCleanup(lambda: watcher.poll() is None and (watcher.kill(), watcher.communicate()))
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(pidfile.exists())
        self.assertEqual(self.check(session='host-1'), [])
        watcher.kill()
        watcher.communicate(timeout=5)
        os.killpg(int(pidfile.read_text()), 9)
        self.assertEqual(self.check(session='host-1'), [f'{self.state}: workstream/01/1: wait process lost', self.gap(path)])


if __name__ == '__main__':
    unittest.main()
