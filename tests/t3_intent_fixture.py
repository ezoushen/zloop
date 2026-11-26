"""Loopback stand-in for authenticated T3 thread and pull request tools."""
import datetime
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch


@contextmanager
def fixture_trust(home, pid, port):
    import human_intent

    with ExitStack() as stack:
        stack.enter_context(patch('pwd.getpwuid', return_value=SimpleNamespace(pw_dir=home)))
        stack.enter_context(patch.object(human_intent, 'listener_owned_by', create=True,
                                         side_effect=lambda owner, address, socket_port:
                                         (owner, address, socket_port) == (pid, '127.0.0.1', port)))
        yield


class T3Read:
    def __init__(self, test, root, env, thread='host-1'):
        self.thread = thread
        self.run = 'fixture-active-run'
        self.answer = {}
        self.pages = {}
        self.malformed = False
        self.on_read = None
        self.reads = []
        self.pr_calls = []
        self.pull = None
        self.on_merge = None
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                method = request['method']
                if self.path != '/mcp' or self.headers.get('Authorization') != 'Bearer fixture-token':
                    self.send_error(403)
                    return
                if fixture.malformed:
                    self.wfile.write(b'not an HTTP status\r\n')
                    return
                if method == 'tools/call':
                    params = request['params']
                    if params['name'] == 't3_thread_read':
                        fixture.reads.append(params)
                        if fixture.on_read:
                            fixture.on_read()
                        cursor = params['arguments'].get('beforePosition')
                        answer = fixture.answer if cursor is None else fixture.pages.get(cursor, {})
                    elif params['name'] in ('pull_request_read', 'pull_request_merge'):
                        fixture.pr_calls.append(params)
                        if params['name'] == 'pull_request_read':
                            answer = fixture.pull
                        elif fixture.on_merge:
                            status, answer = fixture.on_merge()
                            if status != 200:
                                self.send_error(status)
                                return
                        else:
                            fixture.pull = dict(fixture.pull, state='merged')
                            answer = {key: fixture.pull[key] for key in ('host', 'repository', 'number', 'url', 'state')}
                        if answer is None:
                            self.send_error(503)
                            return
                    else:
                        self.send_error(400)
                        return
                    result = {'structuredContent': answer}
                elif method == 'initialize':
                    result = {'protocolVersion': '2025-06-18', 'capabilities': {}, 'serverInfo': {'name': 'Fixture'}}
                else:
                    self.send_response(202)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}).encode())

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        test.addCleanup(worker.join, 5)
        test.addCleanup(server.server_close)
        test.addCleanup(server.shutdown)
        origin = f'http://127.0.0.1:{server.server_port}'
        self.home, self.pid, self.port = root, os.getpid(), server.server_port
        self.runtime = root / '.t3/userdata/server-runtime.json'
        self.runtime.parent.mkdir(parents=True)
        self.runtime.write_text(json.dumps({'origin': origin, 'pid': os.getpid(), 'port': server.server_port}))
        self.runtime.chmod(0o600)
        env.update(HOME=str(root), T3CODE_THREAD_ID=thread, T3_MCP_URL=origin + '/mcp',
                   T3_MCP_BEARER_TOKEN='fixture-token')

    def trust(self):
        return fixture_trust(str(self.home), self.pid, self.port)

    def command(self, script, *args):
        code = '''
from pathlib import Path
import runpy
import sys
fixture_dir, home, pid, port, script = sys.argv[1:6]
sys.path[:0] = [fixture_dir, str(Path(script).parent)]
sys.argv = sys.argv[5:]
from t3_intent_fixture import fixture_trust
with fixture_trust(home, int(pid), int(port)):
    runpy.run_path(script, run_name='__main__')
'''
        return [sys.executable, '-B', '-c', code, str(Path(__file__).resolve().parent), str(self.home),
                str(self.pid), str(self.port), str(script), *map(str, args)]

    def item(self, words, *, source='web', author='user', thread=None, run=None, item='fixture-item'):
        thread, run = thread or self.thread, run or self.run
        return {'sourceThreadId': thread, 'runId': run, 'itemId': item, 'messageId': 'message-' + item,
                'type': 'user_message', 'textTruncated': False,
                'visibility': 'local', 'createdBy': author, 'creationSource': source, 'text': words,
                'updatedAt': datetime.datetime.now(datetime.timezone.utc).isoformat()}

    def readback(self, *items, thread=None, run=None):
        run = run or self.run
        items = [dict(item, position=item.get('position', len(items) - index)) for index, item in enumerate(items)]
        self.answer = {'thread': {'threadId': thread or self.thread, 'activeRunId': run,
                                  'status': 'running', 'archived': False, 'itemCount': len(items)}, 'items': items,
                       'hasMore': False, 'earlierPosition': None,
                       'recentRuns': [{'runId': run, 'status': 'running',
                                       'requestedAt': datetime.datetime.now(datetime.timezone.utc).isoformat()}]}

    def pr_readback(self, repo, number, head, base, state='open'):
        self.pull = {'host': 'github.com', 'repository': repo, 'number': number,
                     'url': f'https://github.com/{repo}/pull/{number}', 'state': state,
                     'headSha': head, 'baseBranch': base}

    def accept(self, preview, words):
        match = re.search(rf'(?m)^{re.escape(words)}\nzloop-intent: [0-9a-f]{{64}}$', preview.stderr)
        if preview.returncode != 2 or match is None:
            raise AssertionError(preview.stdout + preview.stderr)
        self.readback(self.item(match[0], item=f'fixture-item-{len(self.reads)}'))
