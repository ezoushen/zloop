#!/usr/bin/env python3
"""Exercise machine-wide local backend leases through the lifecycle CLI."""

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


LIFECYCLE = support.script('worker_lifecycle.py')


class Capacity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='zloop capacity ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.enterContext(patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'state')}))
        self.number = 0

    def run_script(self, *args, code=0):
        process = subprocess.run([sys.executable, '-B', str(LIFECYCLE), *args], capture_output=True, text=True)
        self.assertEqual(process.returncode, code, process.stdout + process.stderr)
        return json.loads(process.stdout) if process.stdout else process.stderr

    def record(self, state, kind, attempt, code=0, **fields):
        path = self.root / 'event.json'
        path.write_text(json.dumps(dict(kind=kind, attempt=attempt, **fields)))
        return self.run_script('record', '--state', str(state), '--input', str(path), code=code)

    def register(self, attempt, state=None, code=0, **capacity):
        """Register a worker on the local `lane` backend."""
        self.number += 1
        execution = self.root / f'execution-{self.number}.json'
        execution.write_text(json.dumps({'host': 'pi', 'native_dispatch': True, 'agent': 'verifier', 'job': 'e2e-gate', 'candidate': 'ornith',
                                         'settings': {'model': 'fixture/ornith'}, 'backend': 'lane',
                                         'capacity': dict({'slots': 2}, **capacity)}))
        return self.record(state or self.root / 'workers.sqlite', 'register', attempt, code=code,
                           assignment=attempt.split('/')[0], repo=str(self.root), worktree=str(self.root),
                           head='head-1', execution=str(execution), runtime='pi', worker=f'pane-{self.number}',
                           native_session=f'native-{self.number}', owned=True,
                           packet=str(support.assignment_file(self.root, 'e2e-gate')))

    def collect(self, attempt, state=None):
        state = state or self.root / 'workers.sqlite'
        saved = next(row for row in self.run_script('status', '--state', str(state))['attempts']
                     if row['attempt'] == attempt)
        native = session_record(self.root / f'{attempt.replace("/", "-")}.jsonl', 'pi',
                                saved['native_session'], self.root, model='fixture/ornith', effort=None)
        self.record(state, 'startup', attempt, native=str(native))
        self.record(state, 'submitted', attempt, evidence=['n:2'])
        report = self.root / f'{attempt.replace("/", "-")}.md'
        report.write_text('done\n')
        user_message(native, 'pi', f'assignment: {attempt.split("/")[0]}  attempt: {attempt}')
        assistant_turn(native, 'pi')
        self.record(state, 'result', attempt, status='completed', head='head-1', report=str(report), native=str(native),
                    evidence=['checks.log'])

    def test_a_full_backend_refuses_registration_until_a_worker_finishes(self):
        self.register('t1/1')
        self.register('t2/1')
        error = self.register('t3/1', code=2)
        self.assertIn('lane is at capacity (2/2)', error)
        self.collect('t1/1')
        self.register('t3/1')
        # A lease whose workstream state is gone is reclaimed.
        other = self.root / 'other/workers.sqlite'
        other.parent.mkdir()
        self.register('t4/1', other, code=2)
        self.collect('t2/1')
        self.register('t4/1', other)
        for suffix in ('', '-wal', '-shm'):
            Path(str(other) + suffix).unlink(missing_ok=True)
        self.register('t5/1')

    def test_an_unhealthy_backend_or_a_short_machine_refuses_registration(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(503 if self.path == '/down' else 200)
                self.end_headers()

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'
        self.assertIn('lane is unhealthy', self.register('t1/1', health=base + '/down', code=2))
        self.register('t1/1', health=base + '/health')
        if sys.platform == 'darwin':
            self.assertIn('free memory', self.register('t2/1', min_free_memory_percent=100.5, code=2))


if __name__ == '__main__':
    unittest.main()
