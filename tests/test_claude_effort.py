#!/usr/bin/env python3
"""Exercise native Claude subagent effort against a local fake API, without inference."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import support


class ClaudeNativeEffort(unittest.TestCase):
    """A generated Claude profile sets subagent effort; a profile without one inherits the parent."""

    def test_profile_effort_and_parent_inheritance(self):
        models = support.load_script('agent_models.py', 'agent_models')
        for override in ('high', None):
            requests = []
            settings = {'model': 'claude-opus-5-5'}
            if override:
                settings['effort'] = override
            profile = models.native_profile({'host': 'claude', 'agent': 'builder', 'job': 'ticket', 'settings': settings})

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    if 'count_tokens' in self.path:
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(b'{"input_tokens":16}')
                        return
                    requests.append({'model': data.get('model'),
                                     'effort': data.get('output_config', {}).get('effort')})
                    if len(requests) > 6:
                        self.send_error(400, 'Unexpected extra request')
                        return
                    child = data.get('model') == 'claude-opus-5-5'
                    done = any(any(isinstance(b, dict) and b.get('type') == 'tool_result'
                                   for b in m['content']) for m in data.get('messages', [])
                               if isinstance(m.get('content'), list))
                    block = {'type': 'text', 'text': 'probe complete'} if child or done else {
                        'type': 'tool_use', 'id': 'toolu_effort_probe', 'name': 'Agent', 'input': {
                            'description': 'Read-only effort probe', 'subagent_type': profile['name'],
                            'prompt': 'Reply probe complete. Do not use tools.', 'run_in_background': False}}
                    stop = 'end_turn' if block['type'] == 'text' else 'tool_use'
                    message = {'id': 'msg_effort_probe', 'type': 'message', 'role': 'assistant',
                               'model': data['model'], 'content': [block], 'stop_reason': stop,
                               'stop_sequence': None, 'usage': {'input_tokens': 16, 'output_tokens': 5}}
                    self.send_response(200)
                    if not data.get('stream'):
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps(message).encode())
                        return
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    start = dict(block, **({'text': ''} if block['type'] == 'text' else {'input': {}}))
                    delta = ({'type': 'text_delta', 'text': block['text']} if block['type'] == 'text'
                             else {'type': 'input_json_delta', 'partial_json': json.dumps(block['input'])})
                    events = [
                        ('message_start', {'message': dict(message, content=[], stop_reason=None)}),
                        ('content_block_start', {'index': 0, 'content_block': start}),
                        ('content_block_delta', {'index': 0, 'delta': delta}),
                        ('content_block_stop', {'index': 0}),
                        ('message_delta', {'delta': {'stop_reason': stop, 'stop_sequence': None},
                                           'usage': {'output_tokens': 5}}),
                        ('message_stop', {}),
                    ]
                    for kind, event in events:
                        self.wfile.write((f'event: {kind}\ndata: ' + json.dumps(dict(type=kind, **event))
                                          + '\n\n').encode())
                        self.wfile.flush()

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with tempfile.TemporaryDirectory(prefix='zloop native effort ') as tmp:
                    root = Path(tmp)
                    path = root / profile['path']
                    path.parent.mkdir(parents=True)
                    path.write_text(profile['content'])
                    env = {k: v for k, v in os.environ.items()
                           if not k.startswith(('ANTHROPIC_', 'CLAUDE_', 'CLAUDECODE'))}
                    env.update(ANTHROPIC_BASE_URL=f'http://127.0.0.1:{server.server_port}',
                               ANTHROPIC_API_KEY='local-probe-only', CLAUDE_CONFIG_DIR=str(root / 'config'),
                               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
                    # Bare mode disables Agent, so isolate settings/hooks/MCP individually.
                    command = ['claude', '--print', '--output-format', 'stream-json', '--verbose',
                               '--no-session-persistence', '--permission-mode', 'dontAsk',
                               '--settings', '{"disableAllHooks":true}', '--setting-sources', 'project',
                               '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                               '--allowedTools', 'Agent', '--model', 'claude-sonnet-5-5', '--effort', 'medium',
                               'Run the effort probe.']
                    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=20)
                    assert result.returncode == 0, result.stderr
                    assert requests == [
                        {'model': 'claude-sonnet-5-5', 'effort': 'medium'},
                        {'model': 'claude-opus-5-5', 'effort': override or 'medium'},
                        {'model': 'claude-sonnet-5-5', 'effort': 'medium'},
                    ], (requests, result.stdout[-1500:])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
