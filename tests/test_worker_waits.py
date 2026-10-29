#!/usr/bin/env python3
"""Wait channels, receipts and the yield gate, with real local wait processes."""

import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import unittest

from lifecycle_case import SCRIPT, WorkerLifecycleCase
import journal_store


class Waits(WorkerLifecycleCase):
    def test_terminal_receipt_delivers_once_and_survives_parent_downtime(self):
        self.register()
        self.ready()
        marker = self.root / 'parent-events.jsonl'
        unavailable = self.root / 'parent-down'
        unavailable.touch()
        # The parent accepts the channel check while arming, then goes down before the receipt.
        notify = {'argv': [sys.executable, '-c',
            'import json,pathlib,sys; '
            'sys.exit(0) if json.loads(sys.argv[1])["kind"] == "channel-check" else None; '
            f'sys.exit(7) if pathlib.Path({str(unavailable)!r}).exists() else None; '
            f'p=pathlib.Path({str(marker)!r}); p.open("a").write(sys.argv[1]+"\\n")',
            '{receipt}'], 'timeout': 2}
        request = self.root / 'wait.json'
        request.write_text(json.dumps({'argv': [sys.executable, '-c', 'print("settled")'],
                                       'timeout': 2, 'notify': notify}))
        receipt = self.cli('watch', '--attempt', 'ticket-1/1', '--input', str(request))
        queued = self.cli('status')['pending'][0]
        self.assertEqual(queued['payload']['delivery']['state'], 'failed')
        self.assertEqual(queued['payload']['delivery']['exit_code'], 7)
        self.assertFalse(marker.exists())
        unavailable.unlink()
        retry = self.root / 'retry.json'
        retry.write_text(json.dumps({'retry': True, 'evidence': ['same parent resumed; failed delivery inspected']}))
        delivered = self.cli('deliver', '--event-id', str(receipt['event_id']), '--input', str(retry))
        self.assertEqual(delivered['state'], 'sent')
        self.cli('deliver', '--event-id', str(receipt['event_id']), '--input', str(retry))
        events = [json.loads(line) for line in marker.read_text().splitlines()]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['attempt'], 'ticket-1/1')
        self.assertEqual(events[0]['assignment'], 'ticket-1')
        self.assertEqual(events[0]['event_id'], receipt['event_id'])
        self.assertEqual(events[0]['kind'], 'wait-finished')
        self.assertEqual(len(self.cli('status')['pending']), 1)
        self.cli('ack', '--event-id', str(receipt['event_id']))
        self.assertEqual(self.cli('status')['pending'], [])

    def test_yield_gate_requires_a_wake_path_for_every_live_worker(self):
        self.register()
        self.ready()
        gate = self.cli('gate', '--stage', 'yield', code=1)
        self.assertEqual(gate['missing'], ['ticket-1/1: no armed wait'])
        self.assertEqual(self.cli('status')['yield'], gate)
        receipt = self.watch()
        gate = self.cli('gate', '--stage', 'yield', code=1)
        self.assertEqual(gate['missing'], [f"ticket-1/1: receipt {receipt['event_id']} was not delivered"])
        self.record('reconciled', evidence=['native worker idle with its report'])
        self.collect()
        self.acknowledge()
        self.assertEqual(self.cli('gate', '--stage', 'yield'), {'complete': True, 'missing': []})

    def test_yield_gate_needs_a_rearm_after_a_wake_is_reconciled(self):
        self.register()
        self.ready()
        parent = self.root / 'parent.jsonl'
        request = self.root / 'wait.json'
        request.write_text(json.dumps({'argv': [sys.executable, '-c', 'print("idle")'], 'timeout': 2,
            'notify': {'argv': [sys.executable, '-c',
                                f'import sys; open({str(parent)!r}, "a").write(sys.argv[1] + "\\n")', '{receipt}'],
                       'timeout': 2}}))
        self.cli('watch', '--attempt', 'ticket-1/1', '--input', str(request))
        # A delivered wake will reach the host: no gap yet.
        self.assertEqual(self.cli('gate', '--stage', 'yield'), {'complete': True, 'missing': []})
        self.record('reconciled', evidence=['native worker still running tests'])
        gate = self.cli('gate', '--stage', 'yield', code=1)
        self.assertEqual(gate['missing'], ['ticket-1/1: no armed wait'])

    def test_yield_gate_rejects_waits_that_cannot_wake_the_host(self):
        self.register()
        self.ready()
        pidfile = self.root / 'wait.pid'
        command = self.root / 'wait.json'
        command.write_text(json.dumps({'argv': [sys.executable, '-c',
            f'import os,time; open({str(pidfile)!r} + ".tmp", "w").write(str(os.getpid())); '
            f'os.replace({str(pidfile)!r} + ".tmp", {str(pidfile)!r}); time.sleep(30)'], 'timeout': 40}))
        watcher = subprocess.Popen([sys.executable, '-B', str(SCRIPT), 'watch', '--state', str(self.state),
                                    '--attempt', 'ticket-1/1', '--input', str(command)], stdout=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(pidfile.exists())
            gate = self.cli('gate', '--stage', 'yield', code=1)
            self.assertEqual(gate['missing'], ['ticket-1/1: wait has no wake channel'])
            watcher.kill()
            watcher.communicate(timeout=5)
            gate = self.cli('gate', '--stage', 'yield', code=1)
            self.assertEqual(gate['missing'], ['ticket-1/1: wait process lost'])
        finally:
            if watcher.poll() is None:
                watcher.kill()
                watcher.communicate()
            if pidfile.exists():
                try:
                    os.killpg(int(pidfile.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_watch_checks_a_new_wake_channel_before_running_the_wait(self):
        self.register()
        self.ready()
        invoked = self.root / 'invoked'
        request = self.root / 'wait.json'
        request.write_text(json.dumps({'argv': [sys.executable, '-c',
            f'from pathlib import Path; Path({str(invoked)!r}).touch()'], 'timeout': 2,
            'notify': {'argv': [sys.executable, '-c', 'import sys; sys.exit(2)', '{receipt}'], 'timeout': 2}}))
        error = self.cli('watch', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertIn('Wake channel failed its check (exit 2', error)
        self.assertFalse(invoked.exists())
        self.assertNotIn('monitor', self.cli('status')['attempts'][0])
        parent = self.root / 'parent.jsonl'
        notify = {'argv': [sys.executable, '-c',
            f'import sys; open({str(parent)!r}, "a").write(sys.argv[1] + "\\n")', '{receipt}'], 'timeout': 2}
        request.write_text(json.dumps({'argv': [sys.executable, '-c', 'print("idle")'], 'timeout': 2,
                                       'notify': notify}))
        first = self.cli('watch', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual(first['delivery']['state'], 'sent')
        self.record('reconciled', evidence=['native worker still running'])
        self.cli('watch', '--attempt', 'ticket-1/1', '--input', str(request))
        messages = [json.loads(line) for line in parent.read_text().splitlines()]
        self.assertEqual([m['kind'] for m in messages], ['channel-check', 'wait-finished', 'wait-finished'])
        self.assertIn('No action needed', messages[0]['instruction'])
        self.assertEqual({r['kind'] for r in self.cli('status')['pending']}, {'wait-finished'})

    def test_a_report_written_after_the_wake_does_not_label_it_done(self):
        execution = json.loads(self.execution.read_text())
        execution['decision_policy'] = {'enabled': ['wake-triage'], 'backend': self.system_one()}
        self.execution.write_text(json.dumps(execution))
        journal = self.root / 'decisions.jsonl'
        self.register(journal=str(journal))
        self.ready()
        request = self.root / 'wait.json'
        request.write_text(json.dumps({'argv': [sys.executable, '-c', 'print("idle")'], 'timeout': 2,
            'notify': {'argv': [sys.executable, '-c', 'pass', '{receipt}'], 'timeout': 2},
            'capture': [sys.executable, '-c', 'print("Running tests... 12/40")']}))
        self.cli('watch', '--attempt', 'ticket-1/1', '--input', str(request))
        self.record('reconciled', evidence=['native worker still running tests'])
        time.sleep(1.1)
        self.collect()
        self.assertEqual([row['event'] for row in journal_store.read(journal)], ['decision'])

    def test_an_invalid_wait_is_refused_before_its_channel_is_checked(self):
        self.register()
        parent = self.root / 'parent.jsonl'
        request = self.root / 'wait.json'
        request.write_text(json.dumps({'argv': [sys.executable, '-c', 'print("idle")'], 'timeout': 2,
            'notify': {'argv': [sys.executable, '-c',
                                f'import sys; open({str(parent)!r}, "a").write(sys.argv[1])', '{receipt}'],
                       'timeout': 2}}))
        error = self.cli('watch', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertIn('Wait requires a verified, unfinished worker', error)
        self.assertFalse(parent.exists())

    def test_a_channel_whose_latest_delivery_failed_is_checked_again(self):
        self.register()
        self.ready()
        parent = self.root / 'parent.jsonl'
        argv = [sys.executable, '-c', f'import sys; open({str(parent)!r}, "a").write(sys.argv[1] + "\\n")', '{receipt}']
        self.cli('status')
        db = sqlite3.connect(self.state)
        # Recorded out of order: the later-numbered delivery finished first.
        for finished_at, state in ((145, 'failed'), (100, 'sent')):
            db.execute('INSERT INTO events (attempt, kind, payload, acknowledged) VALUES (?, ?, ?, 1)',
                       ('ticket-1/1', 'wait-finished', json.dumps({'notify': {'argv': argv, 'timeout': 2},
                        'delivery': {'state': state, 'finished_at': finished_at}, 'recorded_at': finished_at})))
        db.commit()
        db.close()
        request = self.root / 'wait.json'
        request.write_text(json.dumps({'argv': [sys.executable, '-c', 'print("idle")'], 'timeout': 2,
                                       'notify': {'argv': argv, 'timeout': 2}}))
        self.cli('watch', '--attempt', 'ticket-1/1', '--input', str(request))
        kinds = [json.loads(line)['kind'] for line in parent.read_text().splitlines()]
        self.assertEqual(kinds, ['channel-check', 'wait-finished'])

    def test_long_wait_requires_completion_channel_before_command_invocation(self):
        self.register()
        self.ready()
        marker = self.root / 'invoked'
        request = self.root / 'wait.json'
        request.write_text(json.dumps({'argv': [sys.executable, '-c',
            f'from pathlib import Path; Path({str(marker)!r}).touch()'], 'timeout': 61}))
        self.cli('watch', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertFalse(marker.exists())
        self.assertNotIn('monitor', self.cli('status')['attempts'][0])

    def test_delivery_timeout_is_durable_without_changing_worker_receipt(self):
        self.register()
        self.ready()
        request = self.root / 'wait.json'
        request.write_text(json.dumps({'argv': [sys.executable, '-c', 'print("settled")'],
            'timeout': 2, 'notify': {'argv': [sys.executable, '-c',
                'import json,sys,time; json.loads(sys.argv[1])["kind"] == "channel-check" or time.sleep(30)',
                '{receipt}'], 'timeout': 3}}))  # Long enough for the channel check to start on a loaded machine.
        receipt = self.cli('watch', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual(receipt['exit_code'], 0)
        pending = self.cli('status')['pending'][0]
        self.assertEqual(pending['payload']['delivery']['exit_code'], 124)
        self.assertEqual(pending['payload']['delivery']['state'], 'failed')

    def test_lost_delivery_owner_requires_callback_teardown_before_retry(self):
        self.register()
        self.ready()
        pidfile = self.root / 'notification-pid'
        request = self.root / 'wait.json'
        request.write_text(json.dumps({'argv': [sys.executable, '-c', 'print("settled")'],
            'timeout': 2, 'notify': {'argv': [sys.executable, '-c',
                'import json,sys; json.loads(sys.argv[1])["kind"] == "channel-check" and sys.exit(); '
                f'import os,time; open({str(pidfile)!r} + ".tmp", "w").write(str(os.getpid())); '
            f'os.replace({str(pidfile)!r} + ".tmp", {str(pidfile)!r}); time.sleep(30)',
                '{receipt}'], 'timeout': 40}}))
        watcher = subprocess.Popen([sys.executable, '-B', str(SCRIPT), 'watch', '--state', str(self.state),
                                    '--attempt', 'ticket-1/1', '--input', str(request)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        callback_pid = None
        try:
            # The callback creates the file before writing its pid, so wait for the pid itself.
            deadline = time.monotonic() + 15
            while not (pidfile.exists() and pidfile.read_text().strip()) and time.monotonic() < deadline:
                time.sleep(.02)
            callback_pid = int(pidfile.read_text())
            watcher.kill()
            watcher.communicate(timeout=5)
            queued = self.cli('status')['pending'][0]
            self.assertEqual(queued['payload']['delivery']['state'], 'sending')
            retry = self.root / 'retry.json'
            retry.write_text(json.dumps({'retry': True, 'evidence': ['lost owner inspected']}))
            error = self.cli('deliver', '--event-id', str(queued['id']), '--input', str(retry), code=2)
            self.assertIn('Owned notification process', error)
        finally:
            if callback_pid:
                try:
                    os.killpg(callback_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if watcher.poll() is None:
                watcher.kill()
                watcher.communicate(timeout=5)

    def test_wait_failure_survives_restart_until_acknowledged(self):
        self.register()
        self.ready()
        self.watch(script='import sys; print("provider overloaded", file=sys.stderr); sys.exit(7)', code=7)
        first = self.cli('status')
        self.assertEqual(first['pending'][0]['kind'], 'wait-finished')
        self.assertEqual(first['pending'][0]['payload']['exit_code'], 7)
        log = Path(first['pending'][0]['payload']['log'])
        self.assertIn('provider overloaded', log.read_text())
        self.assertEqual(self.cli('status')['pending'], first['pending'])
        self.assertIn('reconcile wait with native worker and report', first['attempts'][0]['attention'])
        self.cli('ack', '--event-id', str(first['pending'][0]['id']))
        self.assertEqual(self.cli('status')['pending'], [])
        self.assertTrue(log.is_file())

    def test_stale_idle_is_a_wait_receipt_and_requires_prompt_and_report(self):
        self.register()
        self.record('startup', native=str(self.native()))
        self.watch()
        self.cli('gate', '--attempt', 'ticket-1/1', '--stage', 'accept', '--revision', 'head-1', code=1)
        report = self.root / 'report.md'
        report.write_text('An older report\n')
        self.record('result', code=2, status='completed', head='head-1', report=str(report), evidence=['older report'])
        self.record('reconciled', evidence=['native idle, prompt not received'])
        self.record('result', code=2, status='completed', head='head-1', report=str(report), evidence=['older report'])
        self.record('submitted', evidence=['accepted prompt with working activity'])
        self.record('submitted', code=2, evidence=['duplicate prompt'])
        self.watch()
        self.watch(code=2)
        self.record('reconciled', evidence=['native completed this assignment at head-1'])
        self.collect()

    def test_independent_waits_do_not_mask_first_completion(self):
        self.register()
        self.ready()
        self.register('ticket-2/1', 'ticket-2', 'native-2', 'pane-2')
        self.ready('ticket-2/1')
        command = self.root / 'slow-wait.json'
        release = self.root / 'release-slow-wait'
        command.write_text(json.dumps({'argv': [sys.executable, '-c',
            f'import time,pathlib\nwhile not pathlib.Path({str(release)!r}).exists(): time.sleep(.02)'], 'timeout': 10}))
        slow = subprocess.Popen([sys.executable, '-B', str(SCRIPT), 'watch', '--state', str(self.state),
                                 '--attempt', 'ticket-1/1', '--input', str(command)], stdout=subprocess.PIPE, text=True)
        try:
            self.watch('ticket-2/1')
            self.assertIsNone(slow.poll())
            receipts = self.cli('status')['pending']
            self.assertEqual([r['attempt'] for r in receipts], ['ticket-2/1'])
            release.write_text('first receipt collected\n')
            slow.communicate(timeout=6)
            self.assertEqual(slow.returncode, 0)
            self.assertEqual({r['attempt'] for r in self.cli('status')['pending']}, {'ticket-1/1', 'ticket-2/1'})
        finally:
            if slow.poll() is None:
                slow.terminate()
                slow.communicate(timeout=5)

    def test_timeout_retains_receipt_and_stops_owned_wait_process(self):
        self.register()
        self.ready()
        pidfile = self.root / 'wait.pid'
        receipt = self.watch(script=f'import os,time; open({str(pidfile)!r} + ".tmp", "w").write(str(os.getpid())); '
            f'os.replace({str(pidfile)!r} + ".tmp", {str(pidfile)!r}); time.sleep(30)',
                             timeout=3, code=124)
        self.assertEqual(receipt['reason'], 'timeout')
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)
        self.assertEqual(self.cli('status')['pending'][0]['payload']['exit_code'], 124)

    def test_lost_monitor_requires_reconciliation_and_cancelled_wait_keeps_receipt(self):
        self.register()
        self.ready()
        marker = self.root / 'waiting'
        command = self.root / 'wait.json'
        command.write_text(json.dumps({'argv': [sys.executable, '-c',
                                                f'import time; open({str(marker)!r}, "w").write("ready"); time.sleep(30)'],
                                       'timeout': 40}))
        watcher = subprocess.Popen([sys.executable, '-B', str(SCRIPT), 'watch', '--state', str(self.state),
                                    '--attempt', 'ticket-1/1', '--input', str(command)], stdout=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(marker.exists())
            self.watch(code=2)
            self.record('reconciled', code=2, evidence=['wait still owned'])
            watcher.send_signal(signal.SIGTERM)
            watcher.communicate(timeout=5)
            self.assertEqual(watcher.returncode, 130)
            self.assertEqual(self.cli('status')['pending'][0]['payload']['reason'], 'interrupted')
            self.record('reconciled', evidence=['native worker still running; old wait stopped'])
            self.watch()
        finally:
            if watcher.poll() is None:
                watcher.kill()
                watcher.communicate()

    def test_a_monitor_killed_as_its_wait_starts_under_a_busy_state_database_never_loses_the_wait(self):
        # Rule: a wait that is running is always recorded as owned (monitor.wait_pid), so a rearm can see it.
        self.register()
        self.ready()
        pidfile = self.root / 'wait.pid'
        command = self.root / 'wait.json'
        command.write_text(json.dumps({'argv': [sys.executable, '-c',
            f'import os,time; open({str(pidfile)!r} + ".tmp", "w").write(str(os.getpid())); '
            f'os.replace({str(pidfile)!r} + ".tmp", {str(pidfile)!r}); time.sleep(30)'], 'timeout': 40}))
        watcher = subprocess.Popen([sys.executable, '-B', str(SCRIPT), 'watch', '--state', str(self.state),
                                    '--attempt', 'ticket-1/1', '--input', str(command)], stdout=subprocess.PIPE, text=True)
        lock = sqlite3.connect(self.state, timeout=10, isolation_level=None)
        logs = self.state.parent / (self.state.name + '.logs')
        wait_pid = None
        try:
            # Hold the database's write lock from the moment the monitor has launched (its log exists).
            deadline = time.monotonic() + 10
            while not (logs.is_dir() and any(logs.iterdir())) and time.monotonic() < deadline:
                pass
            lock.execute('BEGIN IMMEDIATE')
            # Kill the monitor as soon as its wait process has started, before the lock is released.
            deadline = time.monotonic() + 1.5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(.005)
            if pidfile.exists():
                watcher.kill()
                watcher.communicate(timeout=5)
            lock.execute('ROLLBACK')
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(pidfile.exists())
            wait_pid = int(pidfile.read_text())
            if watcher.poll() is None:
                watcher.kill()
                watcher.communicate(timeout=5)
            attempt = self.cli('status')['attempts'][0]
            self.assertEqual(attempt['monitor']['wait_pid'], wait_pid)
            self.assertIn('reconcile wait with native worker and report', attempt['attention'])
        finally:
            lock.close()
            if watcher.poll() is None:
                watcher.kill()
                watcher.communicate()
            if wait_pid is None and pidfile.exists():
                wait_pid = int(pidfile.read_text())
            if wait_pid is not None:
                try:
                    os.killpg(wait_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_abrupt_monitor_loss_blocks_rearm_until_owned_wait_is_stopped(self):
        self.register()
        self.ready()
        pidfile = self.root / 'wait.pid'
        command = self.root / 'wait.json'
        command.write_text(json.dumps({'argv': [sys.executable, '-c',
            f'import os,time; open({str(pidfile)!r} + ".tmp", "w").write(str(os.getpid())); '
            f'os.replace({str(pidfile)!r} + ".tmp", {str(pidfile)!r}); time.sleep(30)'], 'timeout': 40}))
        watcher = subprocess.Popen([sys.executable, '-B', str(SCRIPT), 'watch', '--state', str(self.state),
                                    '--attempt', 'ticket-1/1', '--input', str(command)], stdout=subprocess.PIPE, text=True)
        wait_pid = None
        try:
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(pidfile.exists())
            wait_pid = int(pidfile.read_text())
            # Kill the monitor only once its wait is recorded as owned: the situation this test is about.
            while 'wait_pid' not in self.cli('status')['attempts'][0].get('monitor', {}) and time.monotonic() < deadline:
                time.sleep(.02)
            watcher.kill()
            watcher.communicate(timeout=5)
            attempt = self.cli('status')['attempts'][0]
            self.assertEqual(attempt['monitor']['wait_pid'], wait_pid)
            self.assertIn('reconcile wait with native worker and report', attempt['attention'])
            self.watch(code=2)
            self.record('reconciled', code=2, evidence=['native running, local wait still alive'])
            os.killpg(wait_pid, signal.SIGKILL)
            deadline = time.monotonic() + 3
            while subprocess.run(['ps', '-p', str(wait_pid), '-o', 'stat='], capture_output=True,
                                 text=True).stdout.strip().strip('Z') and time.monotonic() < deadline:
                time.sleep(.02)
            self.record('reconciled', evidence=['native running, lost wait stopped'])
            self.watch()
        finally:
            if watcher.poll() is None:
                watcher.kill()
                watcher.communicate()
            if wait_pid is not None:
                try:
                    os.killpg(wait_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass




if __name__ == '__main__':
    unittest.main()
