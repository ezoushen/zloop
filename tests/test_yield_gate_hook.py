#!/usr/bin/env python3
"""The Stop hook keeps a host working while a lifecycle worker could finish unseen."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import support

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
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        self.state = self.repo / '.scratch/effort/reports/workers.sqlite'
        self.execution = self.repo / '.scratch/effort/reports/execution.json'
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
if args[:1] == ['--session']:
    session, args = args[1], args[2:]
if served['hang']:
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
else:
    sys.exit(3)
''')
        stub.chmod(0o700)

    def serve(self, agents=(), panes=None, fail=None, hang=False, tabs=None):
        """What the stub Herdr server reports: live agents, panes and tabs by server, or a failure."""
        self.served.write_text(json.dumps({'agents': list(agents), 'panes': panes or {}, 'tabs': tabs or {},
                                           'fail': fail, 'hang': hang}))

    def env(self, pane='w1:p1', **values):
        """The environment of a session in Herdr pane `pane` of workspace w1, or outside Herdr when None."""
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('HERDR_') and key not in ('ZLOOP_WORKER', *SESSION_VARIABLES)}
        env['PATH'] = f"{self.bin}{os.pathsep}{env['PATH']}"
        return dict(env, **({'HERDR_PANE_ID': pane, 'HERDR_WORKSPACE_ID': 'w1'} if pane else {}), **values)

    def lifecycle(self, kind, attempt='ticket-1/1', pane='w1:p1', env=None, **fields):
        event = self.state.parent / 'event.json'
        event.write_text(json.dumps(dict(kind=kind, attempt=attempt, **fields)))
        subprocess.run([sys.executable, '-B', str(LIFECYCLE), 'record', '--state', str(self.state),
                        '--input', str(event)], check=True, capture_output=True, env=self.env(pane, **(env or {})))

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
bootstrap.default({{ on: (event, handler) => {{ handlers[event] = handler; }}, registerTool() {{}}, registerCommand() {{}} }});
const ctx = {{ cwd: {json.dumps(str(self.repo))}, sessionManager: {{ getSessionId: () => 'pi-1' }} }};
{body}
"""
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True,
                                timeout=60, env=self.env(pane, **(env or {})))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def bind(self, pane='w1:p1', slug='fix', autofix=False, code=0, **sessions):
        """Run dispatch's binding for effort `slug` (None: every effort), with `--autofix` when `autofix`, as an
        orchestrator in Herdr pane `pane` (None: outside Herdr) whose host exports `sessions` (a SESSION_VARIABLES name
        to its value); the finished process, which must exit `code`."""
        result = subprocess.run([sys.executable, '-B', str(TICKETS), '--repo', str(self.repo), *filter(None, [slug]),
                                 '--coordinate', *(['--autofix'] if autofix else [])],
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


class YieldGateHook(GateRepo):
    def setUp(self):
        super().setUp()
        self.lifecycle('register', assignment='ticket-1', repo=str(self.repo), worktree=str(self.repo),
                       head='head-1', execution=str(self.execution), runtime='claude', worker='pane-1',
                       native_session='native-1', owned=True,
                       packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.lifecycle('startup', observed={'runtime': 'claude', 'model': 'sonnet'}, evidence=['native.jsonl:1'])
        self.lifecycle('submitted', evidence=['native.jsonl:2:accepted prompt'])

    def test_a_live_worker_without_a_wake_path_blocks_the_stop_once(self):
        decision = self.stop(cwd=self.repo / '.scratch')
        self.assertEqual(decision['decision'], 'block')
        self.assertIn('ticket-1/1: no armed wait', decision['reason'])
        self.assertIn(str(self.state), decision['reason'])
        self.assertIn('workers/wait.md', decision['reason'])
        # A stop that a stop hook already continued is let through, so a gap the host cannot fix never loops.
        self.assertIsNone(self.stop(active=True))
        self.assertIsNone(self.stop(env={'ZLOOP_YIELD_HOOK': 'off'}))

    def test_an_unreadable_launch_log_line_is_named_and_hides_no_other_gap(self):
        # Review 6: a torn or hand-broken line is reported by number and ignored for matching; the lifecycle gaps and
        # the readable launches still show.
        log = self.repo / '.scratch/herdr-launches.jsonl'
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5')
        with log.open('a') as stream:
            stream.write('{"time": "2026-10-2\n{"agent": "builder-9"}\n')
        self.launch('herdr agent start builder-2 --kind codex --pane w1:p6')
        self.assertEqual(self.check(), [f'{self.state}: ticket-1/1: no armed wait', self.gap('builder-1', 'w1:p5'),
                                        self.gap('builder-2', 'w1:p6'),
                                        f'{log}: launch log line 2 is unreadable; fix or remove it',
                                        f'{log}: launch log line 3 is unreadable; fix or remove it'])

    def test_a_line_with_malformed_fields_is_named_like_an_unreadable_one(self):
        # Re-review 3: valid JSON whose fields have the wrong shape (the probe's empty coordinator, a bad time, a
        # number for a name or cwd, a dismissal without its host session) is named and ignored for matching.
        log = self.repo / '.scratch/herdr-launches.jsonl'
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5')
        at, coordinator = '2026-10-20T10:00:00Z', {'pane': 'w1:p1', 'session': None}
        bad = [{'time': 'x', 'agent': 'b', 'pane': 'w1:p9', 'coordinator': {}},
               {'time': 'yesterday', 'agent': 'b', 'pane': 'w1:p9', 'coordinator': coordinator},
               {'time': at, 'agent': 7, 'pane': 'w1:p9', 'coordinator': coordinator},
               {'time': at, 'agent': 'b', 'pane': 'w1:p9', 'coordinator': coordinator, 'cwd': 5},
               {'time': at, 'dismissed': {'pane': 'w1:p5'}, 'by': {'pane': 'w1:p1'}, 'reason': 'probe'}]
        with log.open('a') as stream:
            stream.writelines(json.dumps(record) + '\n' for record in bad)
        self.assertEqual(self.check(session='host-1'), [
            f'{self.state}: ticket-1/1: no armed wait', self.gap('builder-1', 'w1:p5'),
            *(f'{log}: launch log line {number} is unreadable; fix or remove it' for number in range(2, 7))])

    def test_a_checkout_without_worktrees_never_asks_herdr(self):
        # Review 4: a Herdr session in a repository with no .worktrees/ has no ticket worker to look for, so a broken
        # Herdr neither slows nor holds it; its lifecycle gaps still show.
        self.serve(fail='connection refused')
        self.assertEqual(self.check(), [f'{self.state}: ticket-1/1: no armed wait'])

    def test_codex_uses_the_same_stop_protocol(self):
        self.assertEqual(self.stop(host='codex')['decision'], 'block')
        self.assertIsNone(self.stop(host='codex', active=True))

    def test_only_the_herdr_pane_that_registered_a_worker_is_held(self):
        self.assertIsNone(self.stop(pane='w1:p9'))
        self.assertEqual(self.stop(pane='w1:p1')['decision'], 'block')

    def test_a_worker_session_in_its_own_worktree_is_not_held(self):
        worktree = self.repo / '.worktrees/ticket-2'
        worktree.mkdir(parents=True)
        execution = self.state.parent / 'execution.json'
        self.lifecycle('register', 'ticket-2/1', None, assignment='ticket-2', repo=str(self.repo),
                       worktree=str(worktree), head='head-1', execution=str(execution), runtime='claude',
                       worker='pane-2', native_session='native-2', owned=True,
                       packet=str(support.assignment_file(execution.parent, 'ticket')))
        self.assertIsNone(self.stop(cwd=worktree, pane=None))
        self.assertEqual(self.stop(pane=None)['decision'], 'block')

    def test_runs_idle_for_a_day_and_other_directories_are_not_checked(self):
        before = self.state.stat().st_mtime
        stale = time.time() - 2 * 24 * 3600
        os.utime(self.state, (stale, stale))
        self.assertIsNone(self.stop())
        # Checking reads the state without touching it.
        self.assertEqual(self.state.stat().st_mtime, stale)
        self.assertGreater(before, stale)
        outside = Path(self.tmp.name) / 'elsewhere'
        outside.mkdir()
        self.assertIsNone(self.stop(cwd=outside))

    def test_check_mode_reports_gaps_for_other_hosts(self):
        result = subprocess.run([sys.executable, str(HOOK), 'check', str(self.repo)],
                                capture_output=True, text=True, timeout=20, env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['missing'], [f'{self.state}: ticket-1/1: no armed wait'])

    @unittest.skipUnless(shutil.which('node'), 'Node.js runs the Pi extension')
    def test_pi_continues_once_when_a_completed_run_would_settle_with_a_gap(self):
        script = f"""
const bootstrap = await import({json.dumps((ROOT / 'hosts/pi/bootstrap.ts').as_uri())});
const handlers = {{}};
bootstrap.default({{ on: (event, handler) => {{ handlers[event] = handler; }}, registerTool() {{}}, registerCommand() {{}} }});
const settle = (outcome) => handlers.agent_before_settle(
  {{ type: 'agent_before_settle', outcome, entries: [], continue: false }},
  {{ cwd: {json.dumps(str(self.repo))}, sessionManager: {{ getSessionId: () => 'pi-1' }} }});
const first = await settle('completed');
const repeated = await settle('completed');
handlers.agent_settled({{ type: 'agent_settled', aborted: false }});
console.log(JSON.stringify([first, repeated, await settle('aborted'), await settle('completed')]));
"""
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True,
                                timeout=30, env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        first, repeated, aborted, next_run = json.loads(result.stdout)
        self.assertTrue(first['continue'])
        self.assertEqual(first['entries'][0]['type'], 'custom_message')
        self.assertIn('ticket-1/1: no armed wait', first['entries'][0]['content'])
        # One continuation per run, so a gap the host cannot fix never loops; an abort is never resumed.
        self.assertIsNone(repeated)
        self.assertIsNone(aborted)
        self.assertTrue(next_run['continue'])


class UnregisteredWorkers(GateRepo):
    """Each `herdr agent start` is logged, so a worker its coordinator never registered is named at stop."""

    def setUp(self):
        super().setUp()
        self.worktree = self.repo / '.worktrees/ticket-1'
        self.worktree.mkdir(parents=True)
        self.serve(panes={'default': {'w1:p5': {'pane_id': 'w1:p5', 'cwd': str(self.worktree)}}})

    def register(self, worker, attempt, native_session=None, pane='w1:p1', worktree=None, session=None):
        """Register `worker` for an attempt in the run's workers.sqlite, as prepare.md has the coordinator in
        Herdr pane `pane` (None: outside Herdr) do, from a Claude Code host exporting `session` when given."""
        self.lifecycle('register', attempt, pane, env={'CLAUDE_CODE_SESSION_ID': session} if session else None,
                       assignment=attempt.split('/')[0], repo=str(self.repo),
                       worktree=str(worktree or self.worktree), head='head-1', execution=str(self.execution), runtime='claude',
                       worker=worker, native_session=native_session or f'native-{attempt}', owned=True,
                       packet=str(support.assignment_file(self.execution.parent, 'ticket')))

    @staticmethod
    def agent(name, pane, cwd, workspace='w1'):
        """A live agent as `herdr agent list` reports it."""
        return {'agent': 'codex', 'agent_status': 'idle', 'name': name, 'pane_id': pane, 'tab_id': f'{workspace}:t9',
                'workspace_id': workspace, 'cwd': str(cwd)}

    @staticmethod
    def live(name, pane, cwd):
        return f'Herdr agent {name} (pane {pane}) runs in {cwd} without a registered attempt'

    def test_each_herdr_agent_start_is_logged_in_the_main_checkout_for_its_coordinator(self):
        # Rule 1: time, agent name, pane, coordinator (its Herdr pane and host session, each when known) and the
        # pane's cwd as the named Herdr server reports it; a command that only mentions a launch records nothing.
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
            {'agent': 'builder-1', 'pane': 'w1:p5', 'coordinator': {'pane': 'w1:p1', 'session': 'host-1'},
             'cwd': str(self.worktree)},
            {'agent': 'reviewer-2', 'pane': 'w1:p6', 'coordinator': {'pane': None, 'session': 'host-1'}}])

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
        self.assertEqual([{key: value for key, value in record.items() if key not in ('time', 'coordinator')}
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

    def test_logging_never_blocks_the_command_even_when_herdr_or_the_log_fails(self):
        # Rule 1: a hung Herdr still leaves a record with the cwd unknown; an unwritable log only loses the record.
        self.serve(hang=True)
        started = time.monotonic()
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5')
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual([{key: value for key, value in record.items() if key != 'time'} for record in self.logged()],
                         [{'agent': 'builder-1', 'pane': 'w1:p5', 'coordinator': {'pane': 'w1:p1', 'session': 'host-1'}}])
        (self.repo / '.scratch/herdr-launches.jsonl').unlink()
        (self.repo / '.scratch/herdr-launches.jsonl').mkdir()
        self.serve()
        self.launch('herdr agent start builder-2 --kind codex --pane w1:p5')

    @unittest.skipUnless(shutil.which('node'), 'Node.js runs the Pi extension')
    def test_pi_logs_launches_from_bash_and_bg_run_without_blocking_them(self):
        # Rule 1 on Pi: its tool_call covers bash and bg_run; the coordinator's session is Pi's session ID.
        verdicts = self.pi("""
const call = (toolName, command) => handlers.tool_call({ type: 'tool_call', toolCallId: toolName, toolName,
  input: { command, name: 'launch' } }, ctx);
console.log(JSON.stringify([await call('bash', 'herdr agent start builder-1 --kind codex --pane w1:p5'),
  await call('bg_run', 'herdr agent start builder-2 --kind pi --pane w1:p6')]));
""")
        self.assertEqual(verdicts, [None, None])
        self.assertEqual([{key: value for key, value in record.items() if key != 'time'} for record in self.logged()], [
            {'agent': 'builder-1', 'pane': 'w1:p5', 'coordinator': {'pane': None, 'session': 'pi-1'},
             'cwd': str(self.worktree)},
            {'agent': 'builder-2', 'pane': 'w1:p6', 'coordinator': {'pane': None, 'session': 'pi-1'}}])

    def test_an_unregistered_launch_into_a_worktree_holds_its_coordinator_after_the_pane_closed(self):
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

    def test_a_dismissal_clears_only_the_launches_of_the_coordinator_that_dismissed(self):
        # Review 2: a dismissal records who dismissed (pane and host session) and accounts only for that
        # coordinator's launches; with no identity it could account for nothing, so it is refused.
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

    def test_a_dismissal_also_accounts_for_a_live_agent_of_the_coordinator_that_dismissed(self):
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
        # Re-review 2: a launch made inside Herdr is cleared only by an attempt whose recorded coordinator is that
        # pane; an attempt with no recorded coordinator, registered at or after it, clears only a launch made outside
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
        self.state = run('effort')
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
        # Rule (T11): a registration records its host session, and where it and the launch's coordinator both know
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

    def test_a_session_pane_name_handle_clears_a_launch_matching_its_pane_or_its_name(self):
        # Decision 2: the lifecycle also accepts `<session>/<pane>/<name>` as a worker handle.
        self.serve(panes={'default': {pane: {'pane_id': pane, 'cwd': str(self.worktree)}
                                      for pane in ('w1:p5', 'w1:p6', 'w1:p7')}})
        for agent, pane in (('builder-1', 'w1:p5'), ('builder-2', 'w1:p6'), ('builder-3', 'w1:p7')):
            self.launch(f'herdr agent start {agent} --kind codex --pane {pane}', pane=None)
        self.serve()
        self.register('default/w1:p5/renamed-1', 'ticket-1/1', pane=None)
        self.register('s2/w1:p9/builder-2', 'ticket-2/1', pane=None)
        self.register('default/w1:p8/builder-9', 'ticket-3/1', pane=None)
        self.assertEqual(self.check(pane=None, session='host-1'), [self.gap('builder-3', 'w1:p7')])

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

    def test_only_the_coordinator_that_launched_a_worker_is_held_for_it(self):
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

    def test_a_live_agent_in_a_worktree_is_a_gap_until_an_attempt_registers_it(self):
        # Rule 3: an agent nobody logged (started by hand, or before the hook) still holds its workspace's coordinator.
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
        # clears it; only a launch the coordinator itself logged is scoped to its session. Review probes: the same
        # pane's restarted session, and a second coordinator's worker in the same workspace.
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

    def test_only_other_live_agents_in_the_coordinators_workspace_and_worktrees_count(self):
        # Rule 3: other workspaces, the coordinator's own pane and agents outside .worktrees/ are not its workers; a
        # session outside Herdr has no workspace to check; a logged launch that is still live is named once.
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5')
        self.serve(agents=[self.agent('elsewhere-1', 'w2:p5', self.worktree, workspace='w2'),
                           self.agent('coordinator', 'w1:p1', self.worktree),
                           self.agent('e2e-1', 'w1:p7', self.repo),
                           self.agent('builder-1', 'w1:p5', self.worktree)])
        self.assertEqual(self.check(), [self.gap('builder-1', 'w1:p5')])
        # Outside Herdr only the launch host-1 logged is named, not the workspace's live agents.
        self.assertEqual(self.check(pane=None, session='host-1'), [self.gap('builder-1', 'w1:p5')])

    def test_an_unmarked_worker_in_its_registered_worktree_is_not_held_for_the_runs_lifecycle_gaps(self):
        # Re-review 3 (main's rule, restored for the lifecycle gaps): a session whose cwd is inside a worktree an
        # attempt registered is that worker, marked or not. Probe: a native worker outside Herdr, unmarked.
        sibling = self.repo / '.worktrees/ticket-2'
        sibling.mkdir()
        for attempt, worktree in (('ticket-1/1', self.worktree), ('ticket-2/1', sibling)):
            self.register(f'builder-{attempt[7]}', attempt, worktree=worktree)
            self.lifecycle('startup', attempt, observed={'runtime': 'claude', 'model': 'sonnet'},
                           evidence=['native.jsonl:1'])
            self.lifecycle('submitted', attempt, evidence=['native.jsonl:2:accepted prompt'])
        self.assertEqual(self.check(cwd=self.worktree, pane=None, session='native-child'), [])
        self.assertEqual(self.check(pane=None, session='host-1'),
                         [f'{self.state}: ticket-1/1: no armed wait', f'{self.state}: ticket-2/1: no armed wait'])

    def test_a_marked_worker_answers_for_its_own_launches_but_not_its_siblings_wherever_it_runs(self):
        # Rule 4: ZLOOP_WORKER skips only the live-sibling scan. Each marked session (in its worktree, at the
        # checkout root, in its coordinator's inherited pane, outside Herdr) is held for the helper it launched,
        # even after the helper's pane closed (probe H), and never for its siblings or its coordinator's launches.
        sibling, marked = self.repo / '.worktrees/ticket-2', {'ZLOOP_WORKER': 'effort/ticket-1'}
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
        # coordinators now in those panes held for their own unregistered launches and live agents.
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
    def test_a_pi_subagents_child_skips_the_settle_check_but_a_forked_coordinator_does_not(self):
        # Third review 1 (probe G): pi-subagents hosts background children in a runner process marked
        # PI_SUBAGENT_CHILD=1, where they load ambient extensions such as this one; a child does not stop for its
        # coordinator's gaps, and its own launches stay its own. /fork also sets the header's parentSession, so a
        # forked coordinator, without the runner's marker, is still checked.
        sibling = self.repo / '.worktrees/ticket-2'
        sibling.mkdir()
        self.register('builder-1', 'ticket-1/1')
        self.lifecycle('startup', observed={'runtime': 'claude', 'model': 'sonnet'}, evidence=['native.jsonl:1'])
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
        self.assertEqual(self.logged()[-1]['coordinator'], {'pane': 'w1:p1', 'session': 'child-1'})
        forked = self.pi(settle, pane='w1:p1')
        self.assertTrue(forked['continue'])
        self.assertIn(f'{self.state}: ticket-1/1: no armed wait', forked['entries'][0]['content'])

    def test_a_marked_worker_of_a_coordinator_in_another_checkout_is_not_held(self):
        # Rule 4 (review probe): the coordinator in this checkout launched and registered both workers in another
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
                                    env={'ZLOOP_WORKER': 'effort/ticket-1'}), [])
        self.assertEqual(self.check(session='host-1'), [])

    def test_a_coordinator_inside_a_worktree_is_still_held(self):
        # Rule 4: an unmarked session is a coordinator wherever it works, so cd'ing into .worktrees/ keeps both checks.
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
        self.assertIsNone(self.stop(active=True))
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
        self.assertIsNone(self.stop(env=env, active=True))
        outside = Path(self.tmp.name) / 'elsewhere'
        outside.mkdir()
        self.assertEqual(self.check(cwd=outside), [])

    @unittest.skipUnless(shutil.which('node'), 'Node.js runs the Pi extension')
    def test_pi_surfaces_a_check_that_could_not_run_and_continues_once(self):
        # Rule 5 on Pi: when the yield gate itself cannot run, the settle hook names the failure, once per run.
        broken = Path(self.tmp.name) / 'broken-python'
        broken.mkdir()
        (broken / 'python3').write_text('#!/bin/sh\necho "python3: simulated crash" >&2\nexit 1\n')
        (broken / 'python3').chmod(0o700)
        first, repeated = self.pi("""
const settle = async () => (await handlers.agent_before_settle(
  { type: 'agent_before_settle', outcome: 'completed', entries: [], continue: false }, ctx)) ?? null;
console.log(JSON.stringify([await settle(), await settle()]));
""", pane='w1:p1', env={'PATH': f"{broken}{os.pathsep}{self.env()['PATH']}"})
        self.assertTrue(first['continue'])
        self.assertRegex(first['entries'][0]['content'],
                         r'\n- could not check workers: Command failed: python3 \S+/hooks/yield_gate\.py check .+ pi-1\n'
                         r'python3: simulated crash\n')
        self.assertIsNone(repeated)


class DeliveryHold(GateRepo):
    """Dispatch binds the session that coordinates an effort; that session alone is held once when it stops with the
    effort's delivery failing under autofix. The fixture is the Pi run: every ticket done, a repair committed after
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

    def test_the_bound_coordinator_stopping_with_delivery_failing_under_autofix_is_held_once(self):
        # Rules 3 and 4: dispatch bound this Herdr pane. Its stop is held once on Claude and Codex, naming the failing
        # fields and the command to continue; the hook's off switch still disables it; delivered, nothing holds it.
        self.bind(pane='w1:p1')
        self.assertEqual(self.check(pane='w1:p1'), [self.gap()])
        for host in ('claude', 'codex'):
            decision = self.stop(pane='w1:p1', host=host)
            self.assertEqual(decision['decision'], 'block')
            # Review 6: a delivery-only hold carries its gap and command, without the worker-registration advice.
            self.assertEqual(decision['reason'], f'Stopping now leaves these gaps:\n- {self.gap()}\n')
            self.assertIsNone(self.stop(pane='w1:p1', host=host, active=True))
        self.assertIsNone(self.stop(pane='w1:p1', env={'ZLOOP_YIELD_HOOK': 'off'}))
        self.review(delivered=True)
        self.assertEqual(self.check(pane='w1:p1'), [])

    def test_outside_herdr_the_session_id_each_host_exports_binds_the_coordinator(self):
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

    def test_a_codex_coordinator_bound_under_a_stale_herdr_pane_is_held_by_its_session(self):
        # Rule (E4 codex): the binding recorded the app-server's stale pane w1:p17 beside the right CODEX_SESSION_ID;
        # the stop check holds that session in whatever pane it reports, and no other session in the stale pane.
        self.bind(pane='w1:p17', CODEX_SESSION_ID='codex-1')
        self.assertEqual(self.check(pane='w2:p1', session='codex-1'), [self.gap()])
        self.assertEqual(self.check(pane=None, session='codex-1'), [self.gap()])
        self.assertEqual(self.stop(pane='w2:p1', session='codex-1', host='codex')['decision'], 'block')
        self.assertEqual(self.check(pane='w1:p17', session='codex-2'), [])
        self.assertIsNone(self.stop(pane='w1:p17', session='codex-2', host='codex'))

    def test_a_coordinator_with_no_identity_is_refused(self):
        # Review 4: outside Herdr, on a host that exports no session ID, nothing could match a stopping session, so the
        # binding refuses, saying the stop hold cannot bind, and records nothing.
        refused = self.bind(pane=None, code=1)
        self.assertEqual(refused.stderr, 'no Herdr pane (HERDR_PANE_ID) or host session ID (CLAUDE_CODE_SESSION_ID, '
                                         'CODEX_SESSION_ID, PI_SESSION_ID) here: the stop hold cannot bind this '
                                         'session\n')
        self.assertFalse((self.repo / '.scratch/fix/reports/coordinator.json').exists())

    def test_no_other_session_is_held(self):
        # Rule 4: when the binding and the stopping session both know a host session, it alone decides (T10), so the
        # bound pane now running another conversation (or a restarted Herdr's reused pane ID) is not held, while the
        # same host session in another pane is; with no session on one side the pane decides.
        self.bind(pane='w1:p1', CLAUDE_CODE_SESSION_ID='host-1')
        for pane, session in (('w1:p1', 'host-1'), ('w1:p1', None), ('w1:p9', 'host-1')):
            self.assertEqual(self.check(pane=pane, session=session), [self.gap()], (pane, session))
        for pane, session in (('w1:p1', 'host-2'), ('w1:p9', None), (None, 'host-2')):
            self.assertEqual(self.check(pane=pane, session=session), [], (pane, session))

    def test_a_recorded_blocker_lets_the_coordinator_stop(self):
        # Rules 1 and 4: `blocker: <reason>` in the review report records why delivery cannot progress, and with it the
        # coordinator stops; `none`, the report template's value, and an empty value are no blocker.
        self.bind(pane='w1:p1')
        self.review(blocker='the staging device is offline')
        self.assertEqual(self.check(pane='w1:p1'), [])
        self.assertIsNone(self.stop(pane='w1:p1'))
        for none in ('none', ''):
            self.review(blocker=none)
            self.assertEqual(self.check(pane='w1:p1'), [self.gap()], none)

    def test_without_autofix_the_coordinator_stops(self):
        # Rules 1 and 4: only `autofix: true`, the recorded repair authorization, holds. A read-only review (`false`),
        # a report that records no authorization, and no report at all stop after review, as dispatch says.
        self.bind(pane='w1:p1')
        for autofix in ('false', None):
            self.review(autofix=autofix)
            self.assertEqual(self.check(pane='w1:p1'), [], autofix)
        self.report.unlink()
        self.assertEqual(self.check(pane='w1:p1'), [])

    def test_autofix_recorded_at_dispatch_start_holds_until_the_review_report_records_its_own(self):
        # Decision 2: `--coordinate --autofix` records the authorization at dispatch start, so the window between the
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
        """The delivery hold's gap for effort `slug`, naming `problems`."""
        return (f"{slug}: delivery incomplete under autofix: {'; '.join(problems)}. Continue the autofix handoff "
                f'({ROOT}/skills/dispatch/handoff.md) until `python3 {ROOT}/scripts/next_tickets.py --repo {self.repo} '
                f'{slug} --delivery` passes, or record `blocker: <reason>` in '
                f'{self.repo}/.scratch/{slug}/reports/spec-review.md')

    def test_a_live_worker_with_a_running_wait_lets_the_coordinator_yield_but_a_lost_one_does_not(self):
        # Rule: the hold is for a delivery stuck with nobody working on it. While a live worker of the effort has a
        # wait running that will wake the host, the host yields; once the wait is lost, or the worker is collected,
        # the delivery hold names the effort again.
        self.bind(pane='w1:p1')
        self.state = self.repo / '.scratch/fix/reports/workers.sqlite'
        self.lifecycle('register', 'fix/01/1', assignment='fix/01', repo=str(self.repo), worktree=str(self.repo),
                       head='head-1', execution=str(self.execution), runtime='claude', worker='pane-1',
                       native_session='native-1', owned=True,
                       packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.lifecycle('startup', 'fix/01/1', observed={'runtime': 'claude', 'model': 'sonnet'},
                       evidence=['native.jsonl:1'])
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

    def test_an_effort_is_finished_only_when_merged_deleted_and_e2e_passed_at_a_head_in_main(self):
        # Review 1, re-review F1: the board records a finished effort as its branch gone, its reviewed head in main and
        # a passing E2E at a head in main. Merged and deleted before E2E ran (the Pi run's report), it is still held;
        # so is a passing E2E at a head main lacks, and a reviewed head main lacks.
        self.bind(pane='w1:p1')
        self.git('update-ref', 'refs/heads/main', self.head)
        self.git('branch', '-D', 'feat/fix')
        missing = 'effort branch feat/fix is not an existing branch'
        self.assertEqual(self.check(pane='w1:p1'), [
            self.held('fix', 'e2e_status: not-run (needs passed)', 'e2e_by: none names no worker run', missing)])
        self.review(delivered=True)
        self.assertEqual(self.check(pane='w1:p1'), [])
        self.review(delivered=True, e2e_status='failed')
        self.assertEqual(self.check(pane='w1:p1'), [self.held('fix', 'e2e_status: failed (needs passed)', missing)])
        self.review(delivered=True, e2e_head='f' * 40)
        self.assertEqual(self.check(pane='w1:p1'), [self.held('fix', missing)])
        self.review(delivered=True)
        self.git('update-ref', 'refs/heads/main', self.start)
        self.assertEqual(self.check(pane='w1:p1'), [self.held('fix', missing)])

    def test_an_old_effort_without_e2e_fields_is_bound_only_by_name_and_then_held_until_finished(self):
        # Re-review F1, in an old real effort's shape: reviewed clean, no E2E or producer fields, merged into main
        # and its branch deleted. Coordinating every effort binds only those with active work (a branch, or a ticket
        # not done), so not it; named explicitly, it is held until finished by the definition above.
        path = self.repo / '.scratch/audit/issues/01-task.md'
        path.parent.mkdir(parents=True)
        path.write_text('---\nticket: audit/01\nstatus: done\nblocked_by: []\ntouches: [a]\ndevices: []\n---\n')
        report = self.repo / '.scratch/audit/reports/spec-review.md'
        report.parent.mkdir(parents=True)
        report.write_text(f'---\nreview_status: clean\ndocumentation_status: passed\nreview_base: {self.start}\n'
                          f'review_diff_base: {self.start}\nreview_head: {self.reviewed}\n---\n')
        self.git('update-ref', 'refs/heads/main', self.head)
        self.review(delivered=True)  # A live effort branch is active work even once its delivery passes.
        bound = self.bind(slug=None).stdout
        self.assertEqual([line.split(':')[0] for line in bound.splitlines()], ['fix'], bound)
        self.review()
        self.bind(slug='audit', autofix=True)
        self.assertEqual(self.check(pane='w1:p1'), [
            self.held('audit', 'e2e_status: missing (needs passed)', 'review_by: missing names no worker run',
                      'e2e_by: missing names no worker run', 'effort branch feat/audit is not an existing branch'),
            self.gap()])

    def test_coordinating_every_effort_binds_only_those_with_work_left_and_refuses_when_none_has(self):
        # Review 1: with no slug, --coordinate binds an effort with a ticket not done, or with delivery incomplete and
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
        self.assertFalse((self.repo / '.scratch/old/reports/coordinator.json').exists())
        for slug in ('fix', 'wip'):
            (self.repo / f'.scratch/{slug}/reports/coordinator.json').unlink(missing_ok=True)
        self.ticket('01', 'done')
        (self.repo / '.scratch/wip/issues/01-task.md').unlink()
        self.git('update-ref', 'refs/heads/main', self.head)
        self.git('branch', '-D', 'feat/fix')
        refused = self.bind(slug=None, code=1)
        self.assertEqual(refused.stderr, 'no selected effort has work left to coordinate\n')
        self.assertEqual(sorted(self.repo.glob('.scratch/*/reports/coordinator.json')), [])

    def test_while_a_ticket_is_not_done_the_coordinator_is_not_held_for_delivery(self):
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
        # a named gap for the coordinator, held once, never a pass or a crashed hook.
        self.bind(pane='w1:p1')
        self.ticket('02', 'done')
        bad = self.repo / '.scratch/fix/issues/02-task.md'
        bad.write_text(bad.read_text().replace('ticket: fix/02', 'ticket: Fix 2'))
        gap = f"could not check delivery of fix: {bad}: invalid ticket id 'Fix 2'"
        self.assertEqual(self.check(pane='w1:p1'), [gap])
        self.assertIn(f'- {gap}\n', self.stop(pane='w1:p1')['reason'])
        self.assertIsNone(self.stop(pane='w1:p1', active=True))
        self.assertEqual(self.check(pane='w1:p9'), [])

    @unittest.skipUnless(shutil.which('node'), 'Node.js runs the Pi extension')
    def test_pi_holds_the_bound_coordinator_once_per_run(self):
        # Rules 3 and 4 on Pi: its bash tool exports PI_SESSION_ID; the settle check holds that session once per run,
        # with the same message, and not another Pi session.
        self.bind(pane=None, PI_SESSION_ID='pi-1')
        first, repeated, other = self.pi("""
const settle = async (use) => (await handlers.agent_before_settle(
  { type: 'agent_before_settle', outcome: 'completed', entries: [], continue: false }, use)) ?? null;
const first = await settle(ctx), repeated = await settle(ctx);
handlers.agent_settled({ type: 'agent_settled', aborted: false });
console.log(JSON.stringify([first, repeated,
  await settle({ ...ctx, sessionManager: { getSessionId: () => 'pi-2' } })]));
""")
        self.assertTrue(first['continue'])
        self.assertEqual(first['entries'][0]['content'], f'Stopping now leaves these gaps:\n- {self.gap()}\n')
        self.assertIsNone(repeated)
        self.assertIsNone(other)


class StaleTabs(GateRepo):
    """A Herdr worker's tab outlives collection only when its attempt is retained; otherwise it is a gap at stop."""

    def setUp(self):
        super().setUp()
        self.tab = {'tab_id': 'w1:t5', 'workspace_id': 'w1', 'label': 'effort/ticket-1 builder', 'pane_count': 1}
        self.serve(agents=[{'agent': 'claude', 'agent_status': 'idle', 'name': 'builder-1', 'pane_id': 'w1:p5',
                            'tab_id': 'w1:t5', 'workspace_id': 'w1'}], tabs={'default': [self.tab]})
        self.report = self.state.parent / 'ticket-1.md'
        self.report.write_text('## Implemented\n')

    def register(self, pane='w1:p1'):
        """Register builder-1 as Herdr places it (tab w1:t5 of session `default`) from Herdr pane `pane`."""
        self.lifecycle('register', pane=pane, assignment='ticket-1', repo=str(self.repo), worktree=str(self.repo),
                       head='head-1', execution=str(self.execution), runtime='claude', worker='builder-1',
                       native_session='native-1', owned=True, herdr={'session': 'default'},
                       packet=str(support.assignment_file(self.execution.parent, 'ticket')))

    def collect(self, **disposition):
        """Collect the attempt's result; with a `disposition`, through `collect` (which records it)."""
        result = dict(status='failed', head='head-1', report=str(self.report), evidence=['worker.log'])
        if not disposition:
            return self.lifecycle('result', **result)
        request = self.state.parent / 'collect.json'
        request.write_text(json.dumps(dict(result, disposition=disposition)))
        subprocess.run([sys.executable, '-B', str(LIFECYCLE), 'collect', '--state', str(self.state), '--attempt',
                        'ticket-1/1', '--input', str(request)], check=True, capture_output=True, env=self.env())

    def gap(self):
        return (f'{self.state}: ticket-1/1: Herdr tab w1:t5 is still open after collection; retire the worker or '
                f'record its retention ({ROOT}/runtime/workers/collect.md)')

    def test_a_collected_attempt_whose_tab_is_still_open_is_a_gap_for_its_coordinator_until_retained(self):
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
        self.assertEqual(self.check(pane='w1:p1'), [])

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

    def test_the_session_bound_to_coordinate_the_effort_sees_its_stale_tabs_instead(self):
        # Rules 3 and 5: once dispatch binds a session to the effort (a dispatch resumed in pane w1:p2, Claude session
        # host-2), that session coordinates the run: it sees the open tab, and the pane that registered the worker,
        # or another conversation in w1:p2, does not.
        self.register(pane='w1:p1')
        self.collect()
        ticket = self.repo / '.scratch/effort/issues/01-task.md'
        ticket.parent.mkdir(parents=True)
        ticket.write_text('---\nticket: effort/01\nstatus: done\nblocked_by: []\ntouches: [a]\ndevices: []\n---\n')
        self.bind(pane='w1:p2', slug='effort', CLAUDE_CODE_SESSION_ID='host-2')
        self.assertEqual(self.check(pane='w1:p2', session='host-2'), [self.gap()])
        self.assertEqual(self.check(pane='w1:p1'), [])
        self.assertEqual(self.check(pane='w1:p2', session='host-3'), [])

    def test_a_malformed_binding_is_one_gap_for_a_session_it_names_and_hides_no_other_gap(self):
        # Review 3: whether a session is bound is decided before anything else can fail. A binding that cannot be read
        # (torn JSON, or the wrong shape) is one named gap for a session whose pane or host session its raw text
        # names, and skipped for every other; the open tab of the run is still reported (by its registering pane).
        self.register()
        self.collect()
        binding = self.state.with_name('coordinator.json')
        rebind = f'python3 {ROOT}/scripts/next_tickets.py --repo {self.repo} effort --coordinate'
        gap = f'{binding}: the coordinator binding cannot be read; rebind with `{rebind}`'
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
        # Rule 5: when Herdr fails or does not answer, the tab check is a named gap for the coordinator, held once,
        # and the run's other gaps (here a live worker with no wait) still show.
        self.register()
        self.collect()
        self.lifecycle('register', 'ticket-2/1', assignment='ticket-2', repo=str(self.repo), worktree=str(self.repo),
                       head='head-1', execution=str(self.execution), runtime='claude', worker='pane-2',
                       native_session='native-2', owned=True,
                       packet=str(support.assignment_file(self.execution.parent, 'ticket')))
        self.lifecycle('startup', 'ticket-2/1', observed={'runtime': 'claude', 'model': 'sonnet'},
                       evidence=['native.jsonl:1'])
        self.lifecycle('submitted', 'ticket-2/1', evidence=['native.jsonl:2:accepted prompt'])
        self.serve(fail='connection refused')
        gap = ('could not check worker tabs: herdr tab list exited 1: '
               '{"error": {"code": "server_unavailable", "message": "connection refused"}}')
        self.assertEqual(self.check(pane='w1:p1'), [f'{self.state}: ticket-2/1: no armed wait', gap])
        self.assertIn(f'- {gap}\n', self.stop(pane='w1:p1')['reason'])
        self.assertIsNone(self.stop(pane='w1:p1', active=True))
        self.serve(hang=True)
        self.assertRegex(self.check(pane='w1:p1')[-1],
                         r"^could not check worker tabs: .*'tab', 'list'.* timed out after 5 seconds$")


if __name__ == '__main__':
    unittest.main()
