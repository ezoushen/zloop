"""Shared fixture for the test_worker_*.py files: a temporary lifecycle state driven through its CLI."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import support
from native_records import assistant_turn, session_record, user_message

import journal_store
import decisions


SCRIPT = support.script('worker_lifecycle.py')


class WorkerLifecycleCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='zloop worker ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.native_paths = {}
        # Outcomes go to a ledger under the state home; keep the user's ledger out of tests.
        self.enterContext(patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'state')}))
        self.state = self.root / 'workers.sqlite'
        self.execution = self.root / 'execution.json'
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': 'builder', 'job': 'ticket',
                                             'settings': {'model': 'sonnet', 'effort': 'high'}}))

    def cli(self, action, *args, code=0):
        process = subprocess.run([sys.executable, '-B', str(SCRIPT), action,
                                  '--state', str(self.state), *args], capture_output=True, text=True,
                                 env=getattr(self, 'native_env', None))
        self.assertEqual(process.returncode, code, process.stdout + process.stderr)
        if action == 'submit' and process.returncode == 0:
            request = json.loads(Path(args[args.index('--input') + 1]).read_text())
            self.native_paths[args[args.index('--attempt') + 1]] = Path(request['native'])
        return json.loads(process.stdout) if process.stdout else process.stderr

    def record(self, kind, attempt='ticket-1/1', code=0, **fields):
        path = self.root / 'event.json'
        path.write_text(json.dumps(dict(kind=kind, attempt=attempt, **fields)))
        result = self.cli('record', '--input', str(path), code=code)
        if kind == 'startup' and code == 0:
            self.native_paths[attempt] = Path(fields['native'])
        return result

    def registration(self, attempt='ticket-1/1', assignment='ticket-1', session='native-1',
                     worker='pane-1', owned=True, runtime='claude', **fields):
        """The register event's fields, as register records them."""
        fields.setdefault('packet', str(self.packet()))
        return dict(assignment=assignment, repo=str(self.root), worktree=str(self.root), head='head-1',
                    execution=str(self.execution), runtime=runtime, worker=worker, native_session=session,
                    owned=owned, **fields)

    def register(self, attempt='ticket-1/1', *args, code=0, **fields):
        return self.record('register', attempt, code=code, **self.registration(attempt, *args, **fields))

    def packet(self):
        """A complete assignment for the job the current execution names."""
        return support.assignment_file(self.root, json.loads(self.execution.read_text()).get('job', 'ticket'))

    def ready(self, attempt='ticket-1/1'):
        """A worker whose native record shows the settings and launch controls it was given, prompted."""
        self.record('startup', attempt, native=str(self.native(attempt)))
        self.record('submitted', attempt, evidence=['native.jsonl:2:accepted prompt'])

    def native(self, attempt='ticket-1/1', *, model='sonnet', effort='high', scope=None):
        """A session fixture with explicit settings, named by the registered attempt."""
        saved = self.cli('status', '--attempt', attempt)['attempts'][0]
        path = self.root / (attempt.replace('/', '-') + '.jsonl')
        scope = scope if scope is not None else json.loads(self.execution.read_text()).get('launch', {}).get('verify', {})
        return session_record(path, saved['requested']['runtime'], saved['native_session'], self.root,
                              model=model, effort=effort, scope=scope)

    def completion_evidence(self, attempt='ticket-1/1'):
        """The fixture worker answers after its prompt, leaving proof to pass to result/collect."""
        saved = self.cli('status', '--attempt', attempt)['attempts'][0]
        path = self.native_paths[attempt]
        user_message(path, saved['requested']['runtime'], f'assignment: {saved["assignment"]}  attempt: {attempt}')
        assistant_turn(path, saved['requested']['runtime'])
        return {'native': str(path)}

    def completed(self, attempt='ticket-1/1', **fields):
        """Record a completed result backed by the fixture worker's turn."""
        return self.record('result', attempt, **self.completion_evidence(attempt), **fields)

    def watch(self, attempt='ticket-1/1', script='print("idle")', code=0, timeout=2):
        path = self.root / 'wait.json'
        path.write_text(json.dumps({'argv': [sys.executable, '-c', script], 'timeout': timeout}))
        return self.cli('watch', '--attempt', attempt, '--input', str(path), code=code)

    def collect(self, attempt='ticket-1/1', report=None):
        if report is None:
            report = self.root / (attempt.replace('/', '-') + '.md')
            report.write_text('## Implemented\n\nVerified assignment report at head-1\n')
        self.completed(attempt, status='completed', head='head-1', report=str(report),
                    evidence=['checks.log@head-1'])
        return report

    def acknowledge(self):
        for receipt in self.cli('status')['pending']:
            self.cli('ack', '--event-id', str(receipt['id']))

    def routed(self, assignment='ticket-1/route', runtime='claude'):
        """Journal a recommended worker-selection decision and register its resolved route."""
        self.journal = self.root / 'decisions.jsonl'
        option = json.dumps({'candidate': 'sonnet', 'model': 'sonnet', 'runtime': runtime, 'thinking': 'high'})

        def transport(backend, payload):
            qid, question = next(iter(payload['questions'].items()))
            return {'model': backend['model'], 'answers': {qid: {
                'type': 'choice', 'choice': 'option_0',
                'probabilities': {key: float(key == 'option_0') for key in question['criteria']}}}}
        policy = {'enabled': ['worker-selection'], 'backend': {
            'protocol': 'systemone', 'endpoint': 'http://localhost:1241/v1/systemone', 'model': 'test-model'}}
        trace = journal_store.trace_config(self.journal, self.root, assignment)
        result = decisions.decide(policy, {'family': 'worker-selection', 'state': 'Agent: builder\nJob: ticket\n',
                                           'instruction': 'Choose a worker.', 'options': {'option_0': option}},
                                  trace=trace, transport=transport)
        execution = json.loads(self.execution.read_text())
        execution.update(selection_source='decision-model',
                         decision_model=dict(result, journal=str(self.journal)))
        self.execution.write_text(json.dumps(execution))
        return result['decision_id']

    def system_one(self):
        """Serve a local System One endpoint that answers each question's first option."""
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                qid, question = next(iter(payload['questions'].items()))
                choice = next(iter(question['criteria']))
                body = json.dumps({'model': payload['model'], 'answers': {qid: {
                    'type': 'choice', 'choice': choice,
                    'probabilities': {key: float(key == choice) for key in question['criteria']}}}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.native_env = dict(os.environ, XDG_STATE_HOME=str(self.root / 'state'))
        return {'protocol': 'systemone', 'model': 'local/test',
                'endpoint': f'http://127.0.0.1:{server.server_port}/v1/systemone'}

    def annotations(self, decision_id):
        return [row for row in journal_store.read(self.journal)
                if row['decision_id'] == decision_id and row['event'] != 'decision']

    def escalate(self, risk='possible-breach', attempt='ticket-1/1', **fields):
        criteria = self.root / 'criteria.md'
        if not criteria.exists():
            criteria.write_text('Accepted: preserve public API; compatible engineering is authorized.\n')
        brief = self.root / 'decision.md'
        brief.write_text('Evidence, alternatives and recommendation; dependent work paused.\n')
        request = self.root / 'escalate.json'
        request.write_text(json.dumps(dict(risk=risk, reason='Model uncertain about compatibility',
            brief=str(brief), criteria=str(criteria), evidence=['native blocker report'], **fields)))
        return self.cli('escalate', '--attempt', attempt, '--input', str(request))

    def herdr_fixture(self, native_session='native-1', verify_error='pane_not_found', caller=None, tab_panes=1,
                      caller_tab='caller-tab', tab_survives=False, agent_tab='tab-1',
                      agent_delay=0, tab_delay=0, during_delay=None, lock_probe=None,
                      agent_kind='claude', session_kind='id'):
        # during_delay: an argv the fixture runs while a delayed call waits, recording its exit code in
        # during-delay.json, so a test can act inside that wait without racing its own thread against it.
        # lock_probe: a state database the fixture tries to write-lock, in its own process and without waiting,
        # while a delayed call waits, recording whether it could; it needs no new interpreter, so load cannot delay it.
        directory = self.root / 'bin'
        directory.mkdir(exist_ok=True)
        executable = directory / 'herdr'
        executable.write_text('#!' + sys.executable + '\n' + f'''
import json,sys,time
from pathlib import Path
marker=Path({str(self.root / 'native-closed')!r})
args=sys.argv[1:]
assert args[:2]==['--session','fixture-session']
for command,delay in ((['agent','get'],{agent_delay!r}),(['tab','get'],{tab_delay!r})):
    if args[2:4]==command and delay:
        Path({str(self.root / 'herdr-waiting')!r}).touch()
        if {lock_probe!r}:
            import sqlite3
            try:
                probe=sqlite3.connect({lock_probe!r},timeout=0,isolation_level=None)
                probe.execute('BEGIN IMMEDIATE');probe.execute('ROLLBACK');code,output=0,''
            except sqlite3.OperationalError as error:
                code,output=1,str(error)
            Path({str(self.root / 'during-delay.json')!r}).write_text(json.dumps({{'code':code,'output':output}}))
        if {during_delay!r}:
            import subprocess
            done=subprocess.run({during_delay!r},capture_output=True,text=True)
            Path({str(self.root / 'during-delay.json')!r}).write_text(json.dumps({{'code':done.returncode,'output':done.stdout+done.stderr}}))
        time.sleep(delay)
if args[2:]==['pane','current','--current']:
    if {caller!r} is None:
        print(json.dumps({{'error':{{'code':'pane_not_found'}}}}),file=sys.stderr);sys.exit(1)
    print(json.dumps({{'result':{{'pane':{{'pane_id':{caller!r},'tab_id':{caller_tab!r}}}}}}}))
elif args[2:4]==['agent','get']:
    if marker.exists() or args[4:] not in (['pane-1'],['builder-1']):
        print(json.dumps({{'error':{{'code':'agent_not_found'}}}}),file=sys.stderr);sys.exit(1)
    print(json.dumps({{'result':{{'agent':{{'agent':{agent_kind!r},'name':'builder-1','pane_id':'pane-1',
        'tab_id':{agent_tab!r},'workspace_id':'ws-1','agent_status':'idle',
        'agent_session':{{'kind':{session_kind!r},'value':{native_session!r}}}}}}}}}))
elif args[2:]==['tab','get','tab-1']:
    panes={tab_panes!r}
    if marker.exists() and not {tab_survives!r}:
        print(json.dumps({{'error':{{'code':'tab_not_found'}}}}),file=sys.stderr);sys.exit(1)
    if panes is None:
        print(json.dumps({{'error':{{'code':'server_unavailable'}}}}),file=sys.stderr);sys.exit(1)
    print(json.dumps({{'result':{{'tab':{{'tab_id':'tab-1','workspace_id':'ws-1','pane_count':panes}}}}}}))
elif args[2:]==['pane','close','pane-1']:
    marker.write_text(marker.read_text()+'closed'+chr(10) if marker.exists() else 'closed'+chr(10))
    print(json.dumps({{'result':{{'type':'ok'}}}}))
elif args[2:]==['pane','get','pane-1']:
    if marker.exists():
        print(json.dumps({{'error':{{'code':{verify_error!r}}}}}),file=sys.stderr);sys.exit(1)
    print(json.dumps({{'result':{{'pane':{{'pane_id':'pane-1'}}}}}}))
else: sys.exit(3)
''')
        executable.chmod(0o700)
        self.native_env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'],
                               HERDR_PANE_ID='caller-pane', HERDR_TAB_ID='caller-tab')

    def opencode_v2_export(self, *content, directory=None, session='native-1', permissions=None):
        """An `opencode session export` document as OpenCode 2 writes it, with the session's permissions when given."""
        model = {'providerID': 'opencode', 'id': 'deepseek-v4.1-flash-free', 'variant': 'high'}
        native = self.root / 'native-v2.json'
        native.write_text(json.dumps({
            'info': {'id': session, 'model': {'providerID': 'opencode', 'id': 'session-default'},
                     'location': {'directory': str(directory or self.root)},
                     **({'permissions': permissions} if permissions is not None else {})},
            'messages': [{'id': 'm1', 'type': 'user', 'text': 'probe'},
                         *({'id': f'a{index}', 'type': 'assistant', 'agent': 'build', 'model': model,
                            'content': list(parts)} for index, parts in enumerate(content)),
                         {'id': 'idle', 'type': 'idle', 'outcome': 'succeeded'}]}))
        return native

    def submit_opencode_v2(self, native, code=0):
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
            'argv': [sys.executable, '-c', 'from pathlib import Path; Path("productive-work").touch()']}))
        return self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=code)
