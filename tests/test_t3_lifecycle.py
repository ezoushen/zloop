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
    def setUp(self):
        super().setUp()
        # Each test says whether T3 hosts the orchestrator; the shell running the suite may itself be a T3 session.
        for marker in ('T3CODE_CLI_PATH', 'T3CODE_THREAD_ID'):
            os.environ.pop(marker, None)

    def t3_route(self):
        """Resolve the worker through the built-in t3 type, as the resolver does for a configured t3 route: the
        provider beside the model, and no launch controls, since T3 starts the worker."""
        execution = json.loads(self.execution.read_text())
        execution.update(host='claude', target_runtime='claude', executor='t3', executor_context='t3',
                         executor_type='t3', executor_config={}, native_dispatch=False,
                         settings={'model': 'sonnet', 'provider': 'claude'})
        self.execution.write_text(json.dumps(execution))

    def registration(self, *args, **fields):
        """A T3 worker registers as runtime t3, with its thread as the native session."""
        if 't3' in fields:
            fields = dict({'runtime': 't3', 'session': fields['t3']['thread']}, **fields)
        return super().registration(*args, **fields)

    def native(self, attempt='ticket-1/1'):
        """A T3 worker's native record: the thread state `t3 thread show --json` writes for its thread."""
        saved = self.cli('status', '--attempt', attempt)['attempts'][0]
        path = self.root / (attempt.replace('/', '-') + '.json')
        path.write_text(json.dumps({'thread': {'threadId': saved['native_session'], 'status': 'completed',
                                               'providerInstanceId': 'claude', 'model': 'sonnet'}, 'messages': []}))
        return path

    def completion_evidence(self, attempt='ticket-1/1'):
        """The thread answers its packet; the native record, rewritten before collection, holds both."""
        saved = self.cli('status', '--attempt', attempt)['attempts'][0]
        path = self.native_paths[attempt]
        record = json.loads(path.read_text())
        record['messages'] += [{'role': 'user', 'text': f'assignment: {saved["assignment"]}  attempt: {attempt}'},
                               {'role': 'assistant', 'text': 'assignment done'}]
        path.write_text(json.dumps(record))
        return {'native': str(path)}

    def t3_fixture(self, thread='th-1', status='completed', show_error=None, runtime='claude',
                   model='sonnet', title='zloop/slug/a-1 builder', parent=None, archived=False, host=None,
                   says=None):
        """A `t3` stub whose `thread show --json <thread>` answers with the fixture's state. `parent` makes the
        thread that thread's child, as `delegate_task` does; `host` is what T3 puts in the environment of an
        orchestrator it hosts; `says` is a sentence the CLI answers instead, on stderr with exit 1 and no JSON."""
        directory = self.root / 'bin'
        directory.mkdir(exist_ok=True)
        executable = directory / 't3'
        # The real CLI nests the thread under `thread` beside `messages`.
        payload = {'thread': {'threadId': thread, 'title': title, 'status': status,
                              'providerInstanceId': runtime, 'model': model, 'parentThreadId': parent,
                              'relationshipToParent': 'subagent' if parent else None, 'archived': archived},
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
if args[:2] == ['thread', 'show'] and args[2] == '--json' and {says!r}:
    sys.exit({says!r})
elif args[:2] == ['thread', 'show'] and args[2] == '--json':
    print({body!r} if args[3] == {thread!r} else json.dumps({{'error': {{'code': 'not_found'}}}}))
    sys.exit({exit_code} if args[3] == {thread!r} else 1)
elif args[:2] == ['thread', 'list'] and args[2] == '--json':
    print({listing!r})
    sys.exit(0)
sys.exit(3)
''')
        executable.chmod(0o700)
        import os
        self.native_env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'], **(host or {}))

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
                         settings={'model': 'sonnet', 'provider': 'claude'})
        self.execution.write_text(json.dumps(execution))


class RemoteLifecycle(RemoteEnvironment):
    def registered_remote(self, thread='mcp:peer-1', host=None):
        self.environment_route()
        self.t3_fixture()  # the local CLI stub must NOT be consulted for a remote thread
        self.mcp_fixture(thread=thread)
        import os
        self.native_env = dict(os.environ, T3_MCP_URL=self.mcp_url,
                               T3_MCP_BEARER_TOKEN='stub-token', **(host or {}))
        self.register(worker=thread, t3={'thread': thread})
        return self.cli('status')['attempts'][0]

    def test_a_hosted_orchestrator_registers_a_linked_environments_thread_unparented(self):
        # Rule: the child-thread rule is the local environment's. A linked environment's worker thread is launched
        # on that machine, where this orchestrator's thread is not its parent.
        saved = self.registered_remote(host={'T3CODE_CLI_PATH': '/t3/bin/t3', 'T3CODE_THREAD_ID': 'orch-1'})
        self.assertEqual(saved['t3']['thread'], 'mcp:peer-1')

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
        native = self.completion_evidence()['native']
        report = self.root / 'ticket-1-1.md'
        report.write_text('## Implemented\n\ndone\n')
        self.mcp_recorded['calls'].clear()
        self.record('result', status='completed', head='head-1',
                    report=str(report), native=native,
                    evidence=['mcp: the thread settled and answered'])
        read_calls = [c for c in self.mcp_recorded['calls'] if c['tool'] == 't3_thread_read']
        self.assertTrue(read_calls, 'collection never read the remote thread')
        self.assertTrue(all(c['args'].get('threadId') == 'mcp:peer-1' for c in read_calls))

    def test_a_remote_retire_archives_through_the_environment_transport(self):
        self.registered_remote()
        self.ready('ticket-1/1')
        native = self.completion_evidence()['native']
        report = self.root / 'ticket-1-1.md'
        report.write_text('## Implemented\n\ndone\n')
        self.record('result', status='completed', head='head-1',
                    report=str(report), native=native,
                    evidence=['mcp: the thread settled and answered'])
        self.mcp_recorded['calls'].clear()
        for receipt in self.cli('status').get('pending', []):
            self.cli('ack', '--event-id', str(receipt['id']))
        result = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(self.t3_retire_request()))
        organize = [c for c in self.mcp_recorded['calls'] if c['tool'] == 't3_thread_organize']
        self.assertTrue(organize, 'retirement never archived the remote thread')
        self.assertEqual(organize[0]['args'].get('action'), 'archive')
        self.assertEqual(organize[0]['args'].get('threadId'), 'mcp:peer-1')

    def test_a_linked_environments_thread_registers_while_its_packet_runs(self):
        # Rule: on a linked environment the packet is the thread's start message, so that thread is registered
        # while its turn runs; only a thread on this machine must have settled.
        self.environment_route()
        self.mcp_fixture(status='running')
        self.native_env = dict(os.environ, T3_MCP_URL=self.mcp_url, T3_MCP_BEARER_TOKEN='stub-token')
        self.register(worker='mcp:peer-1', t3={'thread': 'mcp:peer-1'})
        self.assertEqual(self.cli('status')['attempts'][0]['t3']['status'], 'running')

    def test_a_linked_environments_thread_the_orchestrator_does_not_own_is_released(self):
        # Rule: a worker the orchestrator does not own is released; on a linked environment, where the lifecycle
        # archives an owned thread itself, it archives nothing.
        self.environment_route()
        self.mcp_fixture()
        self.native_env = dict(os.environ, T3_MCP_URL=self.mcp_url, T3_MCP_BEARER_TOKEN='stub-token')
        self.register(worker='mcp:peer-1', owned=False, t3={'thread': 'mcp:peer-1'})
        self.ready()
        self.collect()
        self.acknowledge()
        self.mcp_recorded['calls'].clear()
        result = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(self.t3_retire_request()))
        self.assertEqual(result['evidence'], ['released: not owned by this orchestrator, its thread is left open'])
        self.assertEqual([call['tool'] for call in self.mcp_recorded['calls']], [])

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

    def test_a_t3_worker_registers_as_runtime_t3(self):
        # Rule: a T3 worker's identity is its thread. Registered as its provider's runtime it would be verified
        # from a session record of that provider, which need not be the thread, so only runtime t3 registers.
        self.t3_route()
        self.t3_fixture()
        error = self.register(worker='th-1', runtime='claude', session='th-1', t3={'thread': 'th-1'}, code=2)
        self.assertIn('A T3 worker registers as runtime "t3"', str(error))
        self.assertFalse(self.cli('status')['attempts'])

    def test_registration_takes_a_listed_thread_whose_turn_has_settled(self):
        # Rule: a worker registers where its transport shows it present and idle, a thread as a pane: an archived
        # thread has left, and one whose turn still runs would answer a packet with that turn's reply.
        self.t3_route()
        self.t3_fixture(archived=True)
        error = self.register(worker='th-1', t3={'thread': 'th-1'}, code=2)
        self.assertIn('T3 thread th-1 is archived', str(error))
        self.t3_fixture(status='running')
        error = self.register(worker='th-1', t3={'thread': 'th-1'}, code=2)
        self.assertIn('T3 thread th-1 is running: register it once its turn has settled', str(error))
        self.assertFalse(self.cli('status')['attempts'])

    def test_a_t3_route_registers_with_its_thread_field(self):
        # Rule: a transport's registration names where the worker runs in that transport's own field, and a refusal
        # says which field is missing (Herdr: herdr.session; T3: t3.thread).
        self.t3_route()
        self.t3_fixture()
        error = self.register(worker='th-1', runtime='t3', session='th-1', code=2)
        self.assertIn('A T3 worker registers with t3.thread, the thread it runs in', str(error))
        self.assertFalse(self.cli('status')['attempts'])

    def test_a_herdr_registration_does_not_ask_t3(self):
        # Rule: the transports are distinct; a herdr route never triggers a t3 lookup — while a t3 registration on
        # the same fixture does consult the CLI, so the log's absence for herdr is meaningful, not vacuous.
        self.t3_fixture()  # the stub exists and logs any t3 invocation
        self.herdr_fixture()
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        self.assertFalse((self.root / 't3-argv.log').exists())
        self.t3_route()
        self.register(worker='th-1', assignment='ticket-2', attempt='ticket-2/1', t3={'thread': 'th-1'})
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

    def test_configuration_for_another_provider_or_model_cannot_prove_thinking(self):
        # Rule: a thinking level is evidence only for the provider and the model the registered thread runs.
        for caller, field, other in (('claude', 'instanceId', 'codex'), ('codex', 'model', 'another-model')):
            with self.subTest(field=field):
                thread = self.resolved_route(caller, 'pi', effort='high')
                attempt = f'{thread}/1'
                self.register(attempt, assignment=thread, session=thread, worker=thread,
                              runtime='t3', t3={'thread': thread})
                native = self.snapshot(thread)
                record = json.loads(native.read_text())
                selection = {'instanceId': 'pi', 'model': 'pi-model', 'options': [{'id': 'thinking', 'value': 'high'}]}
                record['configuration'] = {'threadId': thread, 'modelSelection': dict(selection, **{field: other})}
                native.write_text(json.dumps(record))
                error = self.record('startup', attempt=attempt, native=str(native), code=2)
                self.assertIn('configuration does not belong', error)


class ThreadReuse(T3Threads):
    """A T3 thread is its worker's session, so the reuse rules of any worker decide what registers on it."""

    def test_a_retained_thread_takes_its_assignments_next_attempt(self):
        # Rule: a worker takes the next attempt of its assignment once the last one is collected and retained, in
        # a T3 thread as in a Herdr pane. That attempt completes on its own packet and reply: the thread still
        # holds the earlier attempt's, which do not stand for it.
        self.registered()
        self.ready()
        error = self.register('ticket-1/2', worker='th-1', t3={'thread': 'th-1'}, code=2)
        self.assertIn('Same-assignment reuse requires collected result, explicit retention', str(error))
        self.collect()
        self.acknowledge()
        self.record('retain', reason='the repair round', next_use='fix round', evidence=['report'])
        self.register('ticket-1/2', worker='th-1', t3={'thread': 'th-1'})
        self.assertEqual([row['t3']['thread'] for row in self.cli('status')['attempts']], ['th-1', 'th-1'])
        earlier = json.loads(self.native_paths['ticket-1/1'].read_text())['messages']
        self.ready('ticket-1/2')
        native = self.native_paths['ticket-1/2']
        native.write_text(json.dumps(dict(json.loads(native.read_text()), messages=earlier)))
        report = self.root / 'ticket-1-2.md'
        report.write_text('## Implemented\n\nVerified assignment report at head-1\n')
        result = dict(status='completed', head='head-1', report=str(report), evidence=['checks.log@head-1'])
        error = self.record('result', 'ticket-1/2', native=str(native), code=2, **result)
        self.assertIn('Completion requires the assignment packet', str(error))
        self.record('result', 'ticket-1/2', **self.completion_evidence('ticket-1/2'), **result)
        self.assertEqual(self.cli('status', '--attempt', 'ticket-1/2')['attempts'][0]['result']['status'], 'completed')

    def test_a_thread_is_never_shared_across_assignments(self):
        # Rule: a native session is never reused across tickets, so another assignment is refused on a thread even
        # when the attempt that holds it is collected and retained.
        self.registered()
        self.ready()
        self.collect()
        self.acknowledge()
        self.record('retain', reason='the repair round', next_use='fix round', evidence=['report'])
        error = self.register('ticket-2/1', assignment='ticket-2', worker='th-1', t3={'thread': 'th-1'}, code=2)
        self.assertIn('A new assignment or agent requires a fresh worker and native session', str(error))


class RegistrationRefusals(T3Threads):
    def test_registration_refuses_an_unknown_thread_with_its_reason(self):
        # Rule: the not-found reason is the refusal, not a generic failure.
        self.t3_route()
        self.t3_fixture(show_error='not_found')
        error = self.register(worker='th-1', t3={'thread': 'th-1'}, code=2)
        self.assertIn('not_found', str(error))


class HostedOrchestrator(T3Threads):
    """T3 hosts the orchestrator. Its session then carries T3CODE_CLI_PATH on every provider, and its own thread id
    in T3CODE_THREAD_ID on Claude and Codex (probed on T3 Code v0.1.9)."""
    HOST = {'T3CODE_CLI_PATH': '/t3/bin/t3'}

    def test_a_hosted_orchestrator_registers_only_a_child_thread(self):
        # Rule: a worker of an orchestrator T3 hosts is a child thread. `t3 thread start` makes a top-level one, so
        # that thread is refused with the tool that makes a child.
        self.t3_route()
        self.t3_fixture(host=self.HOST)
        error = self.register(worker='th-1', t3={'thread': 'th-1'}, code=2)
        self.assertIn('T3 thread th-1 is not a child thread', str(error))
        self.assertIn('delegate_task', str(error))
        self.t3_fixture(host=self.HOST, parent='orch-9')
        self.register(worker='th-1', t3={'thread': 'th-1'})
        self.assertEqual(self.cli('status')['attempts'][0]['t3']['thread'], 'th-1')

    def test_the_child_belongs_to_the_orchestrators_own_thread_where_t3_names_it(self):
        # Rule: where T3 names the orchestrator's thread, the worker is that thread's child, not another's.
        host = dict(self.HOST, T3CODE_THREAD_ID='orch-1')
        self.t3_route()
        self.t3_fixture(host=host, parent='orch-2')
        error = self.register(worker='th-1', t3={'thread': 'th-1'}, code=2)
        self.assertIn("T3 thread th-1 is a child of orch-2, not of this orchestrator's thread orch-1", str(error))
        self.t3_fixture(host=host, parent='orch-1')
        self.register(worker='th-1', t3={'thread': 'th-1'})
        self.assertEqual(self.cli('status')['attempts'][0]['t3']['thread'], 'th-1')

    def test_a_hosted_orchestrator_archives_its_worker_thread_before_retiring_it(self):
        # Rule: the t3 CLI cannot archive, so an orchestrator T3 hosts archives its finished worker thread with
        # T3's own tool, and retirement refuses until T3 reports the thread archived.
        self.t3_route()
        self.t3_fixture(host=self.HOST, parent='orch-1')
        self.register(worker='th-1', t3={'thread': 'th-1'})
        self.ready()
        self.collect()
        self.acknowledge()
        error = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(self.t3_retire_request()), code=2)
        self.assertIn('T3 thread th-1 is not archived', str(error))
        self.assertIn('t3_thread_organize', str(error))
        self.assertFalse(self.cli('status', '--attempt', 'ticket-1/1')['attempts'][0].get('closed'))
        self.t3_fixture(host=self.HOST, parent='orch-1', archived=True)
        result = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(self.t3_retire_request()))
        self.assertTrue(result['closed'])
        self.assertIn('th-1: completed, archived (no CLI stop)', result['evidence'][0])

    def test_collection_retires_a_t3_worker_through_t3_not_on_stated_evidence(self):
        # Rule: T3 reports the state of a T3 worker's thread, so collecting it with a retire disposition applies
        # the retirement checks and never closes on the evidence the disposition states.
        self.t3_route()
        self.t3_fixture(host=self.HOST, parent='orch-1')
        self.register(worker='th-1', t3={'thread': 'th-1'})
        self.ready()
        report = self.root / 'ticket-1-1.md'
        report.write_text('## Implemented\n\nVerified assignment report at head-1\n')
        request = self.collect_request('head-1', report, 'ticket-1/1', evidence=['checks.log@head-1'], disposition={
            'kind': 'retire', 'evidence': ['thread left idle'],
            'acceptance': {'decision': 'deferred', 'reason': 'review pending'}})
        error = self.cli('collect', '--attempt', 'ticket-1/1', '--input', request, code=2)
        self.assertIn('T3 thread th-1 is not archived', str(error))
        self.assertFalse(self.cli('status', '--attempt', 'ticket-1/1')['attempts'][0].get('closed'))
        self.t3_fixture(host=self.HOST, parent='orch-1', archived=True)
        final = self.cli('collect', '--attempt', 'ticket-1/1', '--input', request)['disposition']
        self.assertIn('th-1: completed, archived (no CLI stop)', final['evidence'][0])


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

    def test_a_t3_worker_the_orchestrator_does_not_own_is_released_and_its_thread_left_alone(self):
        # Rule: a worker the orchestrator does not own is released at retirement and its place left to its owner, a
        # T3 thread as a Herdr pane: no archive is asked of an orchestrator T3 hosts, and none is made.
        self.t3_route()
        self.t3_fixture(host=HostedOrchestrator.HOST, parent='orch-1')
        self.register(worker='th-1', owned=False, t3={'thread': 'th-1'})
        self.ready()
        self.collect()
        self.acknowledge()
        result = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(self.t3_retire_request()))
        self.assertEqual(result['evidence'], ['released: not owned by this orchestrator, its thread is left open'])
        self.assertTrue(self.cli('status', '--attempt', 'ticket-1/1')['attempts'][0]['closed'])

    def test_a_thread_its_user_deleted_is_gone(self):
        # Rule: a worker whose place its transport says no longer exists has ended, a deleted thread as a closed
        # pane: retirement records it on T3's words and asks no archive of a thread that is not there.
        self.t3_route()
        self.t3_fixture(host=HostedOrchestrator.HOST, parent='orch-1')
        self.register(worker='th-1', t3={'thread': 'th-1'})
        self.ready()
        self.collect()
        self.acknowledge()
        self.t3_fixture(host=HostedOrchestrator.HOST, says='Thread th-1 is no longer available.')
        result = self.cli('retire', '--attempt', 'ticket-1/1', '--input', str(self.t3_retire_request()))
        self.assertEqual(result['evidence'][0], 't3 thread show --json th-1: no longer available (no CLI stop)')
        self.assertTrue(self.cli('status', '--attempt', 'ticket-1/1')['attempts'][0]['closed'])

    def test_a_hand_recorded_closure_rests_on_t3s_answer(self):
        # Rule: a closure recorded by hand takes the transport's answer, as it does Herdr's, not the evidence it
        # states: refused while T3 lists the thread or gives no answer, with the command that retires the worker, and
        # recorded once T3 reports the thread settled and archived.
        self.registered()
        self.ready()
        self.collect()
        self.acknowledge()
        closure = dict(evidence=['said so'], acceptance={'decision': 'deferred', 'reason': 'review pending'})
        error = self.record('closed', code=2, **closure)
        self.assertIn('T3 still lists thread th-1 (t3 thread show --json th-1: completed)', str(error))
        self.assertIn('retire --state', str(error))
        self.t3_fixture(show_error='not_found')
        error = self.record('closed', code=2, **closure)
        self.assertIn('T3 cannot confirm whether thread th-1 is archived', str(error))
        self.assertFalse(self.cli('status', '--attempt', 'ticket-1/1')['attempts'][0].get('closed'))
        self.t3_fixture(status='running', archived=True)  # Archiving hides a thread; it does not stop its turn.
        error = self.record('closed', code=2, **closure)
        self.assertIn('T3 still lists thread th-1 (t3 thread show --json th-1: running)', str(error))
        self.t3_fixture(archived=True)
        self.record('closed', **closure)
        self.assertTrue(self.cli('status', '--attempt', 'ticket-1/1')['attempts'][0]['closed'])

    def test_reap_closes_a_collected_t3_worker_on_t3s_answer_alone(self):
        # Rule: at a stop the reap closes a collected worker its transport confirms gone, and no other. For T3 that
        # is a settled, archived thread; a listed one stays open with T3's answer, even after its run card ended.
        import support
        self.state = self.root / 'ws' / 'reports' / 'workers.sqlite'
        support.run_card(self.root / 'ws' / 'autopilot.md', confirmed='yes', terminal='delivered')
        self.registered()
        self.ready()
        self.collect()
        self.acknowledge()
        self.record('retain', reason='the repair round', next_use='fix round', evidence=['report'])
        self.assertEqual(self.cli('reap'), {'closed': [], 'refused': [], 'open': [
            {'attempt': 'ticket-1/1', 'reason': 't3 thread show --json th-1: completed'}]})
        # No answer closes nothing: a thread T3 does not confirm, no T3 server running (its threads are still
        # there), and a CLI that cannot run at all.
        self.t3_fixture(show_error='not_found')
        self.assertEqual(self.cli('reap')['open'], [
            {'attempt': 'ticket-1/1', 'reason': 't3 thread show --json th-1: not confirmed'}])
        self.t3_fixture(says='No running T3 Code server uses /t3/home. Start T3 Code, then retry.')
        stopped = self.cli('reap')
        self.assertEqual((stopped['closed'], stopped['refused']), ([], []))
        self.assertIn('no answer', stopped['open'][0]['reason'])
        (self.root / 'bin' / 't3').chmod(0o600)
        self.native_env = dict(self.native_env, PATH=str(self.root / 'bin'))  # No other t3 to fall back on.
        unanswered = self.cli('reap')
        self.assertEqual((unanswered['closed'], unanswered['refused']), ([], []))
        self.assertIn('t3 thread show --json th-1: no answer', unanswered['open'][0]['reason'])
        self.assertFalse(self.cli('status', '--attempt', 'ticket-1/1')['attempts'][0].get('closed'))
        self.t3_fixture(archived=True)
        self.assertEqual(self.cli('reap'), {'open': [], 'refused': [], 'closed': [
            {'attempt': 'ticket-1/1', 'evidence': ['t3 thread show --json th-1: completed, archived']}]})
        self.assertTrue(self.cli('status', '--attempt', 'ticket-1/1')['attempts'][0]['closed'])


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
