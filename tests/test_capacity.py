#!/usr/bin/env python3
"""Exercise machine-wide local backend leases through the lifecycle CLI."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import support
import capacity
import worker_lifecycle
from native_records import assistant_turn, session_record, user_message


LIFECYCLE = support.script('worker_lifecycle.py')
LOCAL_ROUTE = {'native_dispatch': False, 'executor_type': 'bash', 'launch': {'verify': {'tools': []}}}


class Capacity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='zloop capacity ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.enterContext(patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'state'),
                                                 'CODEX_SESSION_ID': 'codex-host'}))
        self.number = 0

    def run_script(self, *args, code=0):
        process = subprocess.run([sys.executable, '-B', str(LIFECYCLE), *args], capture_output=True, text=True)
        self.assertEqual(process.returncode, code, process.stdout + process.stderr)
        return json.loads(process.stdout) if process.stdout else process.stderr

    def record(self, state, kind, attempt, code=0, **fields):
        path = self.root / 'event.json'
        path.write_text(json.dumps(dict(kind=kind, attempt=attempt, **fields)))
        return self.run_script('record', '--state', str(state), '--input', str(path), code=code)

    def register(self, attempt, state=None, code=0, *, owner=None, route=None, **capacity):
        request = self.registration(attempt, owner=owner, route=route, **capacity)
        return self.record(state or self.root / 'workers.sqlite', 'register', attempt, code=code, **request)

    def registration(self, attempt, *, owner=None, route=None, **capacity):
        """Register a worker on the local `lane` backend."""
        self.number += 1
        execution = self.root / f'execution-{self.number}.json'
        execution.write_text(json.dumps({'host': 'pi', 'native_dispatch': True, 'agent': 'verifier', 'job': 'e2e-gate', 'candidate': 'ornith',
                                         'settings': {'model': 'fixture/ornith'}, 'backend': 'lane',
                                         'capacity': dict({'slots': 2}, **capacity), **(route or {})}))
        return dict(assignment=attempt.split('/')[0], repo=str(self.root), worktree=str(self.root),
                    head='head-1', execution=str(execution), runtime='pi', worker=f'pane-{self.number}',
                    native_session=f'native-{self.number}', owned=True,
                    packet=str(support.assignment_file(self.root, 'e2e-gate', attempt=attempt)),
                    **({'local_owner': owner} if owner else {}))

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

    def test_retry_after_reservation_reuses_one_slot(self):
        state = (self.root / 'workers.sqlite').resolve()
        interrupted = dict(kind='register', attempt='t1/1', **self.registration('t1/1', slots=1))
        stopped = subprocess.run([sys.executable, '-B', '-c', """
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
import worker_lifecycle
db = worker_lifecycle.connect(Path(sys.argv[2]))
db.create_function('crash_registration', 0, lambda: os._exit(73))
db.execute('CREATE TEMP TRIGGER interrupt_attempt BEFORE INSERT ON attempts '
           'BEGIN SELECT crash_registration(); END')
worker_lifecycle.record(db, json.loads(sys.argv[3]))
""", str(LIFECYCLE.parent), str(state), json.dumps(interrupted)], capture_output=True, text=True, timeout=10)
        self.assertEqual(73, stopped.returncode, stopped.stdout + stopped.stderr)
        self.assertEqual(1, capacity.in_use('lane'))
        self.assertEqual([], self.run_script('status', '--state', str(state))['attempts'])
        self.register('t1/1', state, slots=1)
        self.assertEqual(1, capacity.in_use('lane'))
        self.assertIn('Attempt already registered', self.register('t1/1', state, slots=1, code=2))
        self.assertIn('lane is at capacity (1/1)', self.register('t2/1', state, slots=1, code=2))
        self.assertEqual(['t1/1'], [row['attempt'] for row in self.run_script('status', '--state', str(state))['attempts']])

    def test_a_registration_in_progress_keeps_its_reservation_past_the_grace(self):
        state = (self.root / 'workers.sqlite').resolve()
        other = state.with_name('other.sqlite')
        worker_lifecycle.connect(other).close()
        writing, finish = threading.Event(), threading.Event()
        data = dict(kind='register', attempt='t1/1', **self.registration('t1/1', slots=1))

        def pause():
            writing.set()
            return finish.wait(timeout=10)

        def register():
            with closing(worker_lifecycle.connect(state)) as db:
                db.create_function('pause_registration', 0, pause)
                db.execute('CREATE TEMP TRIGGER pause_attempt BEFORE INSERT ON attempts '
                           'BEGIN SELECT pause_registration(); END')
                return worker_lifecycle.record(db, data)

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(register)
            try:
                self.assertTrue(writing.wait(timeout=5), 'registration never reached its attempt write')
                after_grace = time.time() + 61
                with self.assertRaisesRegex(ValueError, r'lane is at capacity \(1/1\)'):
                    capacity.acquire('lane', {'slots': 1}, str(other), 't2/1', now=after_grace)
            finally:
                finish.set()
            self.assertEqual('t1/1', pending.result(timeout=5)['attempt'])
        self.assertEqual(1, capacity.in_use('lane', now=after_grace))

    def test_rerouting_can_replace_only_an_expired_reservation(self):
        state = str((self.root / 'workers.sqlite').resolve())
        worker_lifecycle.connect(Path(state)).close()
        capacity.acquire('lane', {'slots': 1}, state, 't1/1', now=100)
        with self.assertRaisesRegex(ValueError, 'already holds capacity on backend lane'):
            capacity.acquire('other', {'slots': 1}, state, 't1/1', now=159)
        self.assertEqual((1, 0), (capacity.in_use('lane', now=159), capacity.in_use('other', now=159)))
        capacity.acquire('other', {'slots': 1}, state, 't1/1', now=160)
        self.assertEqual((0, 1), (capacity.in_use('lane', now=160), capacity.in_use('other', now=160)))

    def test_an_expired_reservation_probe_does_not_block_retirement(self):
        state = (self.root / 'workers.sqlite').resolve()
        self.register('t1/1', state)
        capacity.acquire('lane', {'slots': 2}, str(state), 't2/1', now=100)
        flock = capacity.fcntl.flock
        checked_retirement = False

        with closing(worker_lifecycle.connect(state)) as db:
            def probe_flock(lock, operation):
                nonlocal checked_retirement
                flock(lock, operation)
                if not checked_retirement:
                    checked_retirement = True
                    with worker_lifecycle.registration_retirement_lock(db):
                        pass

            with patch.object(capacity.fcntl, 'flock', side_effect=probe_flock):
                self.assertEqual(1, capacity.in_use('lane', now=161))
        self.assertTrue(checked_retirement)

    def test_lifecycle_reclaims_a_local_worker_only_after_its_owner_dies_and_the_lease_expires(self):
        state = (self.root / 'workers.sqlite').resolve()
        with subprocess.Popen([sys.executable, '-B', '-c', 'import sys; sys.stdin.read()'],
                              stdin=subprocess.PIPE) as worker:
            stamp = worker_lifecycle.process_stamp(worker.pid)
            self.assertTrue(stamp)
            self.register('t1/1', state, owner={'pid': worker.pid, 'stamp': stamp}, slots=1, route=LOCAL_ROUTE)
            registered = time.time()
            self.assertEqual(1, capacity.in_use('lane', now=registered + 61))
            worker.stdin.close()
            worker.wait(timeout=3)
        self.assertEqual(1, capacity.in_use('lane', now=registered))
        self.assertEqual(0, capacity.in_use('lane', now=registered + 61))
        capacity.acquire('lane', {'slots': 1}, str(state), 't2/1', now=registered + 61)
        self.assertEqual(1, capacity.in_use('lane', now=registered + 61))

    def test_an_expired_owner_with_an_unavailable_stamp_is_protected_but_a_reused_pid_is_not(self):
        with subprocess.Popen([sys.executable, '-B', '-c', 'import sys; sys.stdin.read()'],
                              stdin=subprocess.PIPE) as worker:
            stamp = worker_lifecycle.process_stamp(worker.pid)
            self.assertTrue(stamp)
            self.register('t1/1', owner={'pid': worker.pid, 'stamp': stamp}, slots=1, route=LOCAL_ROUTE)
            expired = time.time() + 61
            with patch('subprocess.run', side_effect=ProcessLookupError()):
                self.assertEqual(1, capacity.in_use('lane', now=expired))
            with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('ps', 3)):
                self.assertEqual(1, capacity.in_use('lane', now=expired))
            with patch('subprocess.run', return_value=subprocess.CompletedProcess(['ps'], 1, stdout='')):
                self.assertEqual(1, capacity.in_use('lane', now=expired))
            with patch('os.kill', side_effect=PermissionError()):
                self.assertEqual(1, capacity.in_use('lane', now=expired))
            with patch('subprocess.run', return_value=subprocess.CompletedProcess(
                    ['ps'], 0, stdout='Thu Jan  1 00:00:00 1970\n')):
                self.assertEqual(0, capacity.in_use('lane', now=expired))

    def test_native_registration_keeps_its_slot_without_trusting_a_local_process(self):
        with subprocess.Popen([sys.executable, '-B', '-c', 'import sys; sys.stdin.read()'],
                              stdin=subprocess.PIPE) as worker:
            stamp = worker_lifecycle.process_stamp(worker.pid)
            self.assertTrue(stamp)
            self.register('t1/1', owner={'pid': worker.pid, 'stamp': stamp}, slots=1,
                          route={**LOCAL_ROUTE, 'native_dispatch': True, 'executor_type': 'subagent'})
            worker.stdin.close()
            worker.wait(timeout=3)
        self.assertEqual(1, capacity.in_use('lane', now=time.time() + 61))

    def test_registration_does_not_attest_a_mismatched_owner_stamp(self):
        with subprocess.Popen([sys.executable, '-B', '-c', 'import sys; sys.stdin.read()'],
                              stdin=subprocess.PIPE) as worker:
            self.register('t1/1', owner={'pid': worker.pid, 'stamp': 'Thu Jan  1 00:00:00 1970'},
                          slots=1, route=LOCAL_ROUTE)
            self.assertEqual(1, capacity.in_use('lane', now=time.time() + 61))

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

    def test_a_reaped_closure_does_not_wait_on_a_locked_capacity_ledger(self):
        # `reap` spends a few seconds in all inside a stop; the lease was released at collection, so a ledger that
        # stays locked costs the closure nothing.
        state = self.root / 'ws' / 'reports' / 'workers.sqlite'
        state.parent.mkdir(parents=True)
        (state.parent.parent / 'autopilot.md').write_text('# ws\n\n## State\nterminal: delivered\n')
        self.register('t1/1', state)
        self.collect('t1/1', state)
        for receipt in self.run_script('status', '--state', str(state))['pending']:
            self.run_script('ack', '--state', str(state), '--event-id', str(receipt['id']))
        self.record(state, 'retain', 't1/1', reason='next round', next_use='fix', evidence=['report'])
        ledger = Path(os.environ['XDG_STATE_HOME']) / 'zloop' / 'capacity.sqlite'
        writer = sqlite3.connect(ledger, isolation_level=None)
        writer.execute('BEGIN IMMEDIATE')
        self.addCleanup(writer.close)
        began = time.monotonic()
        found = self.run_script('reap', '--state', str(state))
        self.assertLess(time.monotonic() - began, 8)
        self.assertEqual(['t1/1'], [row['attempt'] for row in found['closed']])


if __name__ == '__main__':
    unittest.main()
