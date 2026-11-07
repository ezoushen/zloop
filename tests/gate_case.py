#!/usr/bin/env python3
"""A temporary repository whose sessions reach a stub `herdr` on PATH, never a live Herdr server."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import support
from native_records import session_record

ROOT = support.ROOT
HOOK = ROOT / 'hooks' / 'yield_gate.py'
LAUNCH = ROOT / 'hooks' / 'herdr_launch.py'
LIFECYCLE = ROOT / 'scripts' / 'worker_lifecycle.py'
TICKETS = ROOT / 'scripts' / 'next_tickets.py'


class GateRepo(unittest.TestCase):
    """A repository whose sessions reach a stub `herdr` on PATH, never a live Herdr server."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='zloop yield ')
        self.addCleanup(self.tmp.cleanup)
        # Outcomes go to a ledger under the state home; keep the user's ledger out of tests.
        self.enterContext(patch.dict(os.environ, {'XDG_STATE_HOME': str(Path(self.tmp.name) / 'state'),
                                                 'CODEX_SESSION_ID': 'codex-host'}))
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
        env = {key: value for key, value in support.without_host_sessions().items()
               if not key.startswith('HERDR_') and key != 'ZLOOP_WORKER'}
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

    def held_once_with_retry_advice(self, session):
        """Rule: a check that could not run holds one stop and says to retry, not to register or dismiss anything."""
        reason = self.stop(session=session)['reason']
        self.assertIn('retry the stop', reason)
        self.assertNotIn('Register each launched worker', reason)
        self.assertNotIn('decision', self.stop(session=session, active=True) or {})

    def check(self, cwd=None, pane='w1:p1', session=None, env=None):
        """The gaps `yield_gate.py check <cwd> [session]` reports: what `stop` names, without counting the stop."""
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
        orchestrator in Herdr pane `pane` (None: outside Herdr) whose host exports `sessions` (a `support.SESSION_VARIABLES` name
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
