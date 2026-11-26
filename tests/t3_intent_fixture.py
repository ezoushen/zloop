"""Loopback stand-in for T3's authenticated thread-read endpoint."""
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import threading


class T3Read:
    def __init__(self, test, root, env, thread='host-1'):
        self.thread = thread
        self.run = 'fixture-active-run'
        self.answer = {}
        self.reads = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                method = request['method']
                if self.path != '/mcp' or self.headers.get('Authorization') != 'Bearer fixture-token':
                    self.send_error(403)
                    return
                if method == 'tools/call':
                    fixture.reads.append(request['params'])
                    if request['params']['name'] != 't3_thread_read':
                        self.send_error(400)
                        return
                    result = {'structuredContent': fixture.answer}
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
        self.runtime = root / '.t3/userdata/server-runtime.json'
        self.runtime.parent.mkdir(parents=True)
        self.runtime.write_text(json.dumps({'origin': origin, 'pid': os.getpid(), 'port': server.server_port}))
        self.runtime.chmod(0o600)
        env.update(HOME=str(root), T3CODE_THREAD_ID=thread, T3_MCP_URL=origin + '/mcp',
                   T3_MCP_BEARER_TOKEN='fixture-token')

    def item(self, words, *, source='web', author='user', thread=None, run=None, item='fixture-item'):
        thread, run = thread or self.thread, run or self.run
        return {'sourceThreadId': thread, 'runId': run, 'itemId': item, 'messageId': 'message-' + item,
                'type': 'user_message', 'textTruncated': False,
                'visibility': 'local', 'createdBy': author, 'creationSource': source, 'text': words,
                'updatedAt': datetime.datetime.now(datetime.timezone.utc).isoformat()}

    def readback(self, *items, thread=None, run=None):
        run = run or self.run
        self.answer = {'thread': {'threadId': thread or self.thread, 'activeRunId': run,
                                  'status': 'running'}, 'items': items,
                       'recentRuns': [{'runId': run, 'status': 'running',
                                       'requestedAt': datetime.datetime.now(datetime.timezone.utc).isoformat()}]}

    def accept(self, preview, words):
        match = re.search(rf'(?m)^{re.escape(words)}\nzloop-intent: [0-9a-f]{{64}}$', preview.stderr)
        if preview.returncode != 2 or match is None:
            raise AssertionError(preview.stdout + preview.stderr)
        self.readback(self.item(match[0], item=f'fixture-item-{len(self.reads)}'))
