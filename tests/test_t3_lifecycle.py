#!/usr/bin/env python3
"""A T3 Code worker lives in a thread of its own: registration asks T3 where it is; the wait reads its state;
retirement leaves the thread idle. The `t3` CLI is stubbed like the Herdr fixture stubs `herdr`."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from lifecycle_case import WorkerLifecycleCase, SCRIPT
import worker_lifecycle as worker_lifecycle_module


class T3Threads(WorkerLifecycleCase):
    def t3_route(self):
        """Resolve the worker through the built-in t3 type, as the resolver does for a configured t3 route."""
        execution = json.loads(self.execution.read_text())
        execution.update(host='claude', target_runtime='claude', executor='t3', executor_context='t3',
                         executor_type='t3', executor_config={}, native_dispatch=False,
                         launch={'writes': 'worktree', 'verify': {'permission_mode': 'dontAsk'}})
        self.execution.write_text(json.dumps(execution))

    def t3_fixture(self, thread='th-1', status='completed', show_error=None, runtime='claude',
                   model='sonnet', title='zloop/slug/a-1 builder'):
        """A `t3` stub whose `thread show --json <thread>` answers with the fixture's state."""
        directory = self.root / 'bin'
        directory.mkdir(exist_ok=True)
        executable = directory / 't3'
        # The real CLI nests the thread under `thread` beside `messages`.
        payload = {'thread': {'threadId': thread, 'title': title, 'status': status,
                              'providerInstanceId': runtime, 'model': model},
                   'messages': [{'role': 'assistant', 'text': 'assignment done'}]}
        body = (json.dumps(payload) if show_error is None
                else json.dumps({'error': {'code': show_error}}))
        exit_code = 1 if show_error else 0
        listing = json.dumps([{'threadId': thread, 'title': title, 'status': status}])
        executable.write_text('#!' + sys.executable + '\n' + f'''
import json, sys
args = sys.argv[1:]
with open({str(self.root / 't3-argv.log')!r}, 'a') as log:
    log.write(' '.join(args) + chr(10))
if args[:2] == ['thread', 'show'] and args[2] == '--json':
    print({body!r} if args[3] == {thread!r} else json.dumps({{'error': {{'code': 'not_found'}}}}))
    sys.exit({exit_code} if args[3] == {thread!r} else 1)
elif args[:2] == ['thread', 'list'] and args[2] == '--json':
    print({listing!r})
    sys.exit(0)
sys.exit(3)
''')
        executable.chmod(0o700)
        import os
        self.native_env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'])

    def registered(self, worker='th-1'):
        self.t3_route()
        self.t3_fixture()
        self.register(worker=worker, t3={'thread': 'th-1'})
        return self.cli('status')['attempts'][0]


    def t3_retire_request(self):
        """A retirement request for the fixture's T3 thread: acceptance deferred, evidence naming the thread state."""
        path = self.root / 't3-retire.json'
        path.write_text(json.dumps({'evidence': ['t3 thread show th-1: completed'],
                                    'acceptance': {'decision': 'deferred', 'reason': 'review pending'}}))
        return path


class RemoteEnvironment(T3Threads):
    """A t3 route targeting a linked environment: the lifecycle reaches the thread through the T3 MCP server
    (the local CLI answers only local threads — ledger A1); the endpoint is addressed by T3_MCP_URL, and the
    call arguments carry only thread-scoped fields (no environmentId — ledger A8)."""

    def mcp_fixture(self, thread='mcp:peer-1', status='completed', reply='assignment done'):
        """A stub T3 MCP server: /mcp answers initialize + tools/call for thread read/send/organize with the
        fixture's thread state, recording the arguments it was given."""
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        recorded = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length) or b'{}')
                sid = self.headers.get('mcp-session-id')
                if body.get('method') == 'initialize':
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('mcp-session-id', 'stub-session')
                    self.end_headers()
                    self.wfile.write(json.dumps({'jsonrpc': '2.0', 'id': body.get('id'), 'result': {
                        'protocolVersion': '2025-06-18', 'capabilities': {'tools': {}},
                        'serverInfo': {'name': 'T3 Code', 'version': 'stub'}}}).encode())
                    return
                params = body.get('params', {})
                tool = params.get('name')
                args = params.get('arguments', {})
                recorded.setdefault('calls', []).append({'tool': tool, 'args': args})
                result = {}
                if tool == 't3_thread_read':
                    result = {'thread': {'threadId': args.get('threadId'), 'status': status,
                                         'providerInstanceId': 'claude', 'model': 'sonnet'},
                              'messages': [{'role': 'assistant', 'text': reply}]}
                elif tool == 't3_thread_send':
                    result = {'threadId': args.get('threadId'), 'status': 'completed',
                              'delivery': 'started'}
                elif tool == 't3_thread_organize':
                    result = {'sequence': 1}
                payload = {'jsonrpc': '2.0', 'id': body.get('id'), 'result': {
                    'content': [{'type': 'text', 'text': json.dumps(result)}],
                    'structuredContent': result, 'isError': False}}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def log_message(self, *a):
                pass

        server = HTTPServer(('127.0.0.1', 0), Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.mcp_url = f'http://127.0.0.1:{port}/mcp'
        self.mcp_recorded = recorded
        return recorded

    def environment_route(self, environment='peer'):
        execution = json.loads(self.execution.read_text())
        execution.update(host='claude', target_runtime='claude', executor='t3', executor_context='t3',
                         executor_type='t3', executor_config={'environment': environment},
                         native_dispatch=False, environment=environment,
                         launch={'writes': 'worktree', 'verify': {'permission_mode': 'dontAsk'}})
        self.execution.write_text(json.dumps(execution))


class RemoteLifecycle(RemoteEnvironment):
    def registered_remote(self, thread='mcp:peer-1'):
        self.environment_route()
        self.t3_fixture()  # the local CLI stub must NOT be consulted for a remote thread
        self.mcp_fixture(thread=thread)
        import os
        self.native_env = dict(os.environ, T3_MCP_URL=self.mcp_url,
                               T3_MCP_BEARER_TOKEN='stub-token')
        self.register(worker=thread, t3={'thread': thread})
        return self.cli('status')['attempts'][0]

    def test_a_remote_attempt_registers_through_the_environment_transport(self):
        # Rule (ledger A1): a thread on a linked environment is confirmed through the T3 MCP server; the
        # local CLI is never asked about it, and the arguments carry only thread-scoped fields (A8).
        saved = self.registered_remote()
        calls = self.mcp_recorded['calls']
        self.assertTrue(calls, 'the lifecycle never reached the MCP transport')
        self.assertTrue(all('environmentId' not in c['args'] for c in calls))
        read_calls = [c for c in calls if c['tool'] == 't3_thread_read']
        self.assertTrue(read_calls and read_calls[0]['args']['threadId'] == 'mcp:peer-1')
        self.assertEqual(saved['t3']['thread'], 'mcp:peer-1')
        self.assertNotIn('t3-argv.log', [p.name for p in self.root.iterdir()])

    def test_a_remote_collection_reads_through_the_environment_transport(self):
        # Rule (ledger A1, wire shape per A8): collection of an environment-targeted attempt reads the remote
        # thread through the MCP server with thread-scoped arguments, and the wait maps the thread's status
        # like the CLI's exit codes.
        self.registered_remote()
        self.ready('ticket-1/1')
        native = self.native('ticket-1/1')
        from support import user_message, assistant_turn
        user_message(native, 'claude', 'assignment: ticket-1  attempt: ticket-1/1')
        assistant_turn(native, 'claude')
        report = self.root / 'ticket-1-1.md'
        report.write_text('## Implemented\n\ndone\n')
        self.mcp_recorded['calls'].clear()
        self.record('result', status='completed', head='head-1',
                    report=str(report), native=str(native),
                    evidence=['mcp: the thread settled and answered'])
        read_calls = [c for c in self.mcp_recorded['calls'] if c['tool'] == 't3_thread_read']
        self.assertTrue(read_calls, 'collection never read the remote thread')
        self.assertTrue(all(c['args'].get('threadId') == 'mcp:peer-1' for c in read_calls))

    def test_a_remote_retire_archives_through_the_environment_transport(self):
        self.registered_remote()
        self.ready('ticket-1/1')
        native = self.native('ticket-1/1')
        from support import user_message, assistant_turn
        user_message(native, 'claude', 'assignment: ticket-1  attempt: ticket-1/1')
        assistant_turn(native, 'claude')
        report = self.root / 'ticket-1-1.md'
        report.write_text('## Implemented\n\ndone\n')
        self.record('result', status='completed', head='head-1',
                    report=str(report), native=str(native),
                    evidence=['mcp: the thread settled and answered'])
        self.mcp_recorded['calls'].clear()
        for receipt in self.cli('status').get('pending', []):
            self.cli('ack', '--event-id', str(receipt['id']))
        result = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(self.t3_retire_request()))
        organize = [c for c in self.mcp_recorded['calls'] if c['tool'] == 't3_thread_organize']
        self.assertTrue(organize, 'retirement never archived the remote thread')
        self.assertEqual(organize[0]['args'].get('action'), 'archive')
        self.assertEqual(organize[0]['args'].get('threadId'), 'mcp:peer-1')

    def test_a_remote_attempt_without_a_credential_refuses_naming_the_fix(self):
        self.environment_route()
        import os
        env = {k: v for k, v in os.environ.items() if k not in ('T3_MCP_URL', 'T3_MCP_BEARER_TOKEN')}
        env.pop('T3_MCP_URL', None)
        saved_env = dict(os.environ)
        os.environ.clear(); os.environ.update(env)
        try:
            error = self.register(worker='mcp:peer-1', t3={'thread': 'mcp:peer-1'}, code=2)
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        self.assertIn('T3_MCP_URL', str(error))


class ResolverSeam(RemoteEnvironment):
    def test_the_resolver_output_feeds_the_lifecycle_transport(self):
        # Review F1: the seam the fixtures masked — the resolver's actual `environment` key (the id string, with
        # environment_access beside it) must select the MCP transport, not the local CLI.
        import os
        resolved = {'environment': 'env-peer-1', 'environment_access': {'id': 'env-peer-1', 'access':
                    'approval-required'}, 'executor_config': {}, 'executor_type': 't3'}
        self.mcp_fixture(thread='mcp:peer-1')
        os.environ['T3_MCP_URL'] = self.mcp_url
        os.environ['T3_MCP_BEARER_TOKEN'] = 'stub-token'
        self.addCleanup(os.environ.pop, 'T3_MCP_URL', None)
        self.addCleanup(os.environ.pop, 'T3_MCP_BEARER_TOKEN', None)
        self.assertNotIn('t3-argv.log', [p.name for p in self.root.iterdir()])
        data = dict(resolved, t3={'thread': 'mcp:peer-1'})
        code, response = worker_lifecycle_module.t3_show(data, 'mcp:peer-1')
        self.assertEqual(code, 0)
        calls = self.mcp_recorded['calls']
        reads = [c for c in calls if c['tool'] == 't3_thread_read']
        self.assertTrue(reads and all(c['args'].get('threadId') == 'mcp:peer-1' for c in reads))


class RemoteTransport(RemoteEnvironment):
    def test_the_remote_send_transport_carries_the_environment_and_thread(self):
        # Rule (ledger A1): the lifecycle's remote send is the MCP t3_thread_send — the local CLI cannot reach
        # another environment's thread; the arguments carry only thread-scoped fields (A8).
        import os
        import urllib.request
        self.environment_route()
        self.mcp_fixture(thread='mcp:peer-1')
        os.environ['T3_MCP_URL'] = self.mcp_url
        os.environ['T3_MCP_BEARER_TOKEN'] = 'stub-token'
        self.addCleanup(os.environ.pop, 'T3_MCP_URL', None)
        self.addCleanup(os.environ.pop, 'T3_MCP_BEARER_TOKEN', None)
        data = {'environment': 'peer', 'execution': '/unused'}
        code, response = worker_lifecycle_module.t3_mcp(data, 't3_thread_send',
                                                        {'threadId': 'mcp:peer-1', 'message': 'packet text'})
        self.assertEqual(code, 0)
        sent = [c for c in self.mcp_recorded['calls'] if c['tool'] == 't3_thread_send']
        self.assertTrue(sent and sent[0]['args']['threadId'] == 'mcp:peer-1'
                        and sent[0]['args']['message'] == 'packet text'
                        and 'environmentId' not in sent[0]['args'])


class Registration(T3Threads):
    def test_registration_records_the_thread_t3_itself_reports(self):
        # Rule: a t3 worker registers the thread id T3 confirms; a thread that does not exist refuses.
        saved = self.registered()
        self.assertEqual(saved['t3'], {'thread': 'th-1', 'status': 'completed', 'runtime': 'claude',
                                       'model': 'sonnet'})
        self.assertNotIn('herdr', saved)  # No Herdr placement: the two transports stay apart.

    def test_a_herdr_registration_does_not_ask_t3(self):
        # Rule: the transports are distinct; a herdr route never triggers a t3 lookup — while a t3 registration on
        # the same fixture does consult the CLI, so the log's absence for herdr is meaningful, not vacuous.
        self.t3_fixture()  # the stub exists and logs any t3 invocation
        self.herdr_fixture()
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        self.assertFalse((self.root / 't3-argv.log').exists())
        self.t3_route()
        self.register(worker='th-1', assignment='ticket-2', attempt='ticket-2/1', session='th-thread',
                      t3={'thread': 'th-1'})
        self.assertTrue((self.root / 't3-argv.log').exists())


class ResolvedRegistration(T3Threads):
    PROVIDERS = {'claude': 'claudeAgent', 'codex': 'codex', 'pi': 'pi', 'opencode': 'opencode'}

    def setUp(self):
        super().setUp()
        self.git(self.root, 'init', '-b', 'main')
        (self.root / '.gitignore').write_text('*\n')
        self.git(self.root, 'add', '-f', '.gitignore')
        self.git(self.root, 'commit', '-m', 'base')
        self.head = self.git(self.root, 'rev-parse', 'HEAD')

    def registration(self, *args, **fields):
        data = super().registration(*args, **fields)
        data['head'] = self.head
        return data

    def t3_fixture(self, thread, runtime, model):
        state = self.root / 't3-state.json'
        state.write_text(json.dumps({'thread': {'threadId': thread, 'status': 'completed',
            'providerInstanceId': runtime, 'model': model}, 'messages': []}))
        directory = self.root / 'bin'
        directory.mkdir(exist_ok=True)
        executable = directory / 't3'
        executable.write_text('#!' + sys.executable + '\n' + f'''
import json, sys
from pathlib import Path
path = Path({str(state)!r})
body = json.loads(path.read_text())
args = sys.argv[1:]
if args[:2] == ['thread', 'list']:
    print(json.dumps([body['thread']]))
elif args[:2] == ['thread', 'show']:
    print(json.dumps(body))
elif args[:2] == ['thread', 'send']:
    body['messages'] += [{{'role': 'user', 'text': args[3]}},
                         {{'role': 'assistant', 'text': 'assignment done'}}]
    path.write_text(json.dumps(body))
    print('assignment done')
else:
    sys.exit(3)
''')
        executable.chmod(0o700)
        self.native_env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'])

    def snapshot(self, thread):
        process = subprocess.run(['t3', 'thread', 'show', '--json', thread],
            capture_output=True, text=True, env=self.native_env)
        self.assertEqual(process.returncode, 0, process.stderr)
        native = self.root / f'{thread}.json'
        native.write_text(process.stdout)
        return native

    def resolved_route(self, caller, runtime, effort=None):
        thread = f'{caller}-{runtime}'
        model = f'{runtime}-model'
        self.t3_fixture(thread=thread, runtime=self.PROVIDERS[runtime], model=model)
        home = self.root / 't3home' / 'userdata'
        home.mkdir(parents=True, exist_ok=True)
        (home / 'server-runtime.json').write_text('{}')
        self.native_env['T3CODE_HOME'] = str(home.parent)
        config = self.root / 'executors.json'
        binding = {'id': model, 'provider': self.PROVIDERS[runtime]}
        if effort:
            binding['thinking'] = effort
        config.write_text(json.dumps({
            'models': {'smoke': {'description': 'Read-only lifecycle check.', 'runtimes': {
                runtime: binding}}},
            'runtimes': {runtime: {'executors': {'t3': {'type': 't3'}}}},
            'agents': {'verifier': {'model': 'smoke'}}}))
        process = subprocess.run([sys.executable, '-B', str(Path(SCRIPT).with_name('agent_models.py')),
            '--repo', str(self.root), '--host', caller, '--job', 'e2e-gate', '--route',
            '--user-executors', str(config), '--target-runtime', runtime,
            '--worktree', str(self.root), '--reports', str(self.root)],
            capture_output=True, text=True, env=self.native_env)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.execution.write_text(process.stdout)
        return thread

    def test_resolved_workers_submit_collect_and_retire_for_all_hosts(self):
        for caller in self.PROVIDERS:
            for runtime in self.PROVIDERS:
                with self.subTest(caller=caller, runtime=runtime):
                    thread = self.resolved_route(caller, runtime)
                    attempt = f'{thread}/1'
                    self.register(attempt, assignment=thread, session=thread, worker=thread,
                                  runtime='t3', t3={'thread': thread})
                    native = self.snapshot(thread)
                    self.record('startup', attempt=attempt, native=str(native))
                    saved = self.cli('status', '--attempt', attempt)['attempts'][0]
                    self.assertTrue(saved['verified'], saved)
                    self.assertEqual(saved['t3']['runtime'], self.PROVIDERS[runtime])
                    self.assertEqual(saved['requested']['runtime'], 't3')
                    self.assertEqual(saved['observed']['provider'], self.PROVIDERS[runtime])
                    request = self.root / 'submit.json'
                    request.write_text(json.dumps({'native': str(native), 'timeout': 5,
                        'argv': ['t3', 'thread', 'send', thread, self.packet(attempt).read_text(), '--wait']}))
                    submitted = self.cli('submit', '--attempt', attempt, '--input', str(request))
                    self.assertEqual((submitted['reason'], submitted['exit_code']), ('settled', 0))
                    self.record('reconciled', attempt=attempt, evidence=['send completed; thread inspected'])
                    self.snapshot(thread)
                    report = self.root / f'{thread}-report.md'
                    report.write_text('## E2E\n\nassignment done\n')
                    request.write_text(json.dumps({'status': 'completed', 'head': self.head,
                        'report': str(report), 'native': str(native), 'evidence': ['assignment done'],
                        'disposition': {'kind': 'retire', 'evidence': ['T3 thread completed']}}))
                    self.cli('collect', '--attempt', attempt, '--input', str(request))
                    saved = self.cli('status', '--attempt', attempt)['attempts'][0]
                    self.assertEqual(saved['result']['status'], 'completed')
                    self.assertTrue(saved['closed'])
        self.assertFalse(self.cli('status')['pending'])
        self.assertTrue(self.cli('gate', '--stage', 'yield')['complete'])

    def test_startup_refuses_a_matching_model_from_another_thread(self):
        thread = self.resolved_route('pi', 'pi')
        self.register('pi-pi/1', assignment=thread, session=thread, worker=thread,
                      runtime='t3', t3={'thread': thread})
        native = self.snapshot(thread)
        record = json.loads(native.read_text())
        record['thread']['threadId'] = 'another-thread'
        native.write_text(json.dumps(record))
        error = self.record('startup', attempt='pi-pi/1', native=str(native), code=2)
        self.assertIn('registered T3 thread', error)

    def test_registration_binds_native_identity_to_its_t3_thread(self):
        thread = self.resolved_route('pi', 'pi')
        error = self.register('pi-pi/1', assignment=thread, session='another-thread', worker=thread,
                              runtime='t3', t3={'thread': thread}, code=2)
        self.assertIn('native_session', error)

    def test_a_different_provider_cannot_pass_the_work_gate(self):
        thread = self.resolved_route('pi', 'pi')
        self.register('pi-pi/1', assignment=thread, session=thread, worker=thread,
                      runtime='t3', t3={'thread': thread})
        native = self.snapshot(thread)
        record = json.loads(native.read_text())
        record['thread']['providerInstanceId'] = 'codex'
        native.write_text(json.dumps(record))
        self.record('startup', attempt='pi-pi/1', native=str(native))
        self.assertFalse(self.cli('status', '--attempt', 'pi-pi/1')['attempts'][0]['verified'])
        self.assertFalse(self.cli('gate', '--stage', 'work', '--attempt', 'pi-pi/1', code=1)['complete'])

    def test_t3_configuration_proves_requested_thinking_for_each_provider(self):
        options = {'claude': 'effort', 'codex': 'reasoningEffort', 'pi': 'thinking', 'opencode': 'variant'}
        for runtime, option in options.items():
            with self.subTest(runtime=runtime):
                thread = self.resolved_route('pi', runtime, effort='high')
                attempt = f'{thread}/1'
                self.register(attempt, assignment=thread, session=thread, worker=thread,
                              runtime='t3', t3={'thread': thread})
                native = self.snapshot(thread)
                record = json.loads(native.read_text())
                record['configuration'] = {'threadId': thread, 'modelSelection': {
                    'instanceId': self.PROVIDERS[runtime], 'model': f'{runtime}-model',
                    'options': [{'id': option, 'value': 'high'}]}}
                native.write_text(json.dumps(record))
                self.record('startup', attempt=attempt, native=str(native))
                saved = self.cli('status', '--attempt', attempt)['attempts'][0]
                self.assertTrue(saved['verified'], saved)
                self.assertEqual(saved['observed']['effort'], 'high')

    def test_thinking_without_configuration_evidence_cannot_pass_the_work_gate(self):
        thread = self.resolved_route('pi', 'pi', effort='high')
        self.register('pi-pi/1', assignment=thread, session=thread, worker=thread,
                      runtime='t3', t3={'thread': thread})
        self.record('startup', attempt='pi-pi/1', native=str(self.snapshot(thread)))
        self.assertFalse(self.cli('gate', '--stage', 'work', '--attempt', 'pi-pi/1', code=1)['complete'])

    def test_configuration_from_another_thread_cannot_prove_thinking(self):
        thread = self.resolved_route('pi', 'pi', effort='high')
        self.register('pi-pi/1', assignment=thread, session=thread, worker=thread,
                      runtime='t3', t3={'thread': thread})
        native = self.snapshot(thread)
        record = json.loads(native.read_text())
        record['configuration'] = {'threadId': 'another-thread', 'modelSelection': {
            'instanceId': 'pi', 'model': 'pi-model', 'options': [{'id': 'thinking', 'value': 'high'}]}}
        native.write_text(json.dumps(record))
        error = self.record('startup', attempt='pi-pi/1', native=str(native), code=2)
        self.assertIn('configuration does not belong', error)


class RegistrationRefusals(T3Threads):
    def test_registration_refuses_a_thread_another_live_attempt_holds(self):
        # Rule: a T3 thread runs one worker; a second attempt on the same live thread is refused with the
        # holder named, even from a fresh worker handle and session.
        self.registered()
        self.record('startup', attempt='ticket-1/1', native=str(self.native('ticket-1/1')))
        error = self.register('ticket-2/1', assignment='ticket-2', session='native-2', worker='th-2',
                              t3={'thread': 'th-1'}, code=2)
        self.assertIn('already holds attempt ticket-1/1', str(error))

    def test_registration_refuses_an_unknown_thread_with_its_reason(self):
        # Rule: the not-found reason is the refusal, not a generic failure.
        self.t3_route()
        self.t3_fixture(show_error='not_found')
        error = self.register(worker='th-1', t3={'thread': 'th-1'}, code=2)
        self.assertIn('not_found', str(error))


class Retirement(T3Threads):
    def test_retirement_leaves_the_thread_idle_and_needs_no_pane(self):
        # Rule: T3 has no CLI stop, so retirement records the thread idle-retained with its state evidence.
        self.registered()
        self.ready()
        report = self.collect()
        self.acknowledge()
        result = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(self.t3_retire_request()))
        self.assertTrue(result['closed'])
        attempt = self.cli('status', '--attempt', 'ticket-1/1')['attempts'][0]
        self.assertTrue(attempt['closed'])
        self.assertIn('left idle (no CLI stop)', result['evidence'][0])




class WaitingThreadRetirement(T3Threads):
    def test_retirement_refuses_a_thread_waiting_for_an_answer(self):
        # Rule: waiting-on-answer is the user's decision, not the orchestrator's.
        self.registered()
        self.ready()
        report = self.collect()
        self.acknowledge()
        self.t3_fixture(status='waiting')
        request = self.root / 'retire-waiting.json'
        request.write_text(json.dumps({'evidence': ['t3 thread show th-1: waiting'],
                                       'acceptance': {'decision': 'deferred', 'reason': 'waiting on the user'}}))
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertIn('waits for an answer', str(error))


class T3Submit(T3Threads):
    """A t3 submit goes through `t3 thread send --wait`; the lifecycle checks the packet text, maps the
    exit codes (0 done, 2 waiting-on-answer, 1 failed) and collects the final reply from `thread show --json`."""

    def t3_send_fixture(self, *, exit_code=0, reply='assignment done', send_error=None):
        import stat
        directory = self.root / 'sendbin'
        directory.mkdir(exist_ok=True)
        executable = directory / 't3'
        body = json.dumps({'threadId': 'th-1', 'status': 'completed',
                           'messages': [{'role': 'assistant', 'content': [{'type': 'text', 'text': reply}]}]})
        executable.write_text('#!' + sys.executable + '\n' + f'''
import json, sys
args = sys.argv[1:]
with open({str(self.root / 't3-send-argv.log')!r}, 'a') as log:
    log.write(' '.join(args) + chr(10))
if args[:2] == ['thread', 'send']:
    if {send_error!r}:
        print({send_error!r}); sys.exit(2)
    print({reply!r})
    sys.exit({exit_code!r})
if args[:2] == ['thread', 'show'] and args[2] == '--json':
    print({body!r}); sys.exit(0)
sys.exit(3)
''')
        executable.chmod(stat.S_IRWXU)
        import os
        self.send_env = dict(self.native_env, PATH=str(directory) + os.pathsep + self.native_env['PATH'])

    def submit_send(self, thread='th-1', text='PACKET', **fixture):
        if fixture:
            self.t3_send_fixture(**fixture)
        if not hasattr(self, 'send_env'):
            self.send_env = self.native_env
        request = self.root / 'submit-send.json'
        request.write_text(json.dumps({'argv': ['t3', 'thread', 'send', thread, text, '--wait'], 'timeout': 5,
                                       'native': str(self.native('ticket-1/1'))}))
        import subprocess
        return subprocess.run([sys.executable, '-B', str(SCRIPT),
                               'submit', '--state', str(self.state), '--attempt', 'ticket-1/1',
                               '--input', str(request)], capture_output=True, text=True, env=self.send_env)

    def test_submit_rejects_a_message_that_is_not_the_registered_packet(self):
        # Rule: the acceptance a t3 submit records is the packet's; any other text is refused before it runs.
        self.registered()
        self.ready()
        process = self.submit_send(text='something else')
        self.assertEqual(process.returncode, 2)
        self.assertIn('registered packet', process.stderr + process.stdout)

    def test_submit_rejects_a_thread_other_than_the_registered_one(self):
        # Rule: a t3 submit sends the registered thread.
        self.registered()
        self.ready()
        process = self.submit_send(thread='th-other')
        self.assertEqual(process.returncode, 2)
        self.assertIn('registered thread', process.stderr + process.stdout)

    def test_submit_runs_the_registered_send_and_arms_the_wait(self):
        # Rule: a t3 submit records the startup, sends the registered packet text to the registered thread through
        # `t3 thread send --wait`, and arms the wait on that send.
        import subprocess
        self.registered()
        # Startup was already recorded by registered()? registered() only registers. Record startup with the native record.
        self.record('startup', native=str(self.native('ticket-1/1')))
        self.t3_send_fixture(exit_code=0, reply='packet received')
        packet = Path(self.packet('ticket-1/1'))
        process = self.submit_send(text=packet.read_text(errors='replace').strip())
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        sends = (self.root / 't3-send-argv.log').read_text()
        self.assertIn('thread send th-1', sends)
        saved = json.loads(process.stdout)
        # The stub settles instantly: the wait ran to its settled receipt.
        self.assertEqual((saved['reason'], saved['exit_code']), ('settled', 0))

    def test_submit_exit_two_maps_to_waiting_on_answer(self):
        # Rule: a send exit 2 means the agent waits for an answer; the wait result carries the mapped reason.
        self.registered()
        self.record('startup', native=str(self.native('ticket-1/1')))
        self.t3_send_fixture(exit_code=2, send_error='agent waiting for an answer')
        packet = Path(self.packet('ticket-1/1'))
        process = self.submit_send(text=packet.read_text(errors='replace').strip())
        # The CLI mirrors the semantics: exit 2 IS the waiting-on-answer state.
        self.assertEqual(process.returncode, 2)
        saved = json.loads(process.stdout)
        self.assertEqual((saved['reason'], saved['exit_code']), ('waiting-on-answer', 2))

    def test_submit_rejects_an_argv_that_is_not_a_t3_thread_send(self):
        # Rule: the registered packet is what a t3 submit sends; any other command is refused before it runs.
        self.registered()
        self.ready()
        request = self.root / 'submit-bad.json'
        request.write_text(json.dumps({'argv': ['t3', 'thread', 'list'], 'timeout': 5}))
        error = self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertIn('t3 thread send', error)

if __name__ == '__main__':
    unittest.main()
