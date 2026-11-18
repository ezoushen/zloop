#!/usr/bin/env python3
"""Persisted wakes survive process loss without repeating an uncertain notification."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import support
import run_control


class Supervisor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.card = support.run_card(self.root / 'autopilot.md', phase='align', terminal='paused-allowance',
                                     evidence='needed job has no eligible provider')
        self.state = run_control.adopt(self.card, 'confirmed resume grant', allow_resume=True)
        self.supervisor = support.load_script('supervisor.py')
        self.queue = self.root / 'wakes.json'
        self.output = self.root / 'actual-progress.json'
        preflight = 'import json; print(json.dumps(dict(ready=True, allowance="eligible", approval="clear")))'
        notify = ('import json,pathlib,sys; '
                  f'sys.path.insert(0,{str(support.SCRIPTS)!r}); import run_control; '
                  'r=json.loads(sys.argv[1]); '
                  'source=run_control.timer_source(r["queue"],r["event_id"]); '
                  'run_control.transition(r["card"],"",source,"due wake",r["control_revision"],"resume-"+r["event_id"]); '
                  'run_control.require_active(r["card"]); '
                  f'pathlib.Path({str(self.output)!r}).write_text(json.dumps(dict(event_id=r["event_id"], next_phase="refine")))')
        progress = ('import json,pathlib,sys; r=json.loads(sys.argv[1]); '
                    f'p=pathlib.Path({str(self.output)!r}); '
                    'print(json.dumps(dict(event_id=r["event_id"], progressed=p.exists() and '
                    'json.loads(p.read_text()).get("event_id")==r["event_id"])))')
        self.config = dict(preflight=[sys.executable, '-c', preflight, '{receipt}'],
                           notify=[sys.executable, '-c', notify, '{receipt}'],
                           progress=[sys.executable, '-c', progress, '{receipt}'], timeout=5)

    def schedule(self):
        return self.supervisor.schedule(self.queue, self.card, 0, self.config, 'wake-1')

    def test_another_process_resumes_persisted_work_and_duplicates_do_not_resend(self):
        self.schedule()
        result = subprocess.run([sys.executable, '-B', str(support.SCRIPTS / 'supervisor.py'),
                                 'tick', str(self.queue)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.output.read_text())['next_phase'], 'refine')
        run_control.require_active(self.card)
        self.assertEqual(self.supervisor.tick(self.queue)['state'], 'progressed')
        self.output.unlink()
        self.assertEqual(self.supervisor.tick(self.queue)['state'], 'progressed')
        self.assertFalse(self.output.exists(), 'a completed wake was sent twice')

    def test_explicit_pause_or_unknown_control_cancels_before_notification(self):
        self.schedule()
        self.card.write_text(self.card.read_text().replace('paused-allowance', 'paused-by-user'))
        result = self.supervisor.tick(self.queue)
        self.assertEqual(result['state'], 'cancelled')
        self.assertFalse(self.output.exists())

    def test_critical_allowance_is_pending_and_does_not_notify(self):
        self.config['preflight'] = [sys.executable, '-c',
            'print(\'{"ready": false, "allowance": "critical", "approval": "clear"}\')', '{receipt}']
        self.schedule()
        self.assertEqual(self.supervisor.tick(self.queue)['state'], 'pending')
        self.assertFalse(self.output.exists())

    def test_lost_sender_is_uncertain_until_target_readback_reconciles_same_event(self):
        self.schedule()
        invoke = self.supervisor.invoke
        calls = []
        def delivered_then_lost(*args):
            calls.append(args)
            result = invoke(*args)
            if len(calls) == 2:
                raise KeyboardInterrupt('sender lost after target accepted')
            return result
        with patch.object(self.supervisor, 'invoke', side_effect=delivered_then_lost):
            with self.assertRaises(KeyboardInterrupt):
                self.supervisor.tick(self.queue)
        self.assertEqual(self.supervisor.tick(self.queue)['state'], 'uncertain')
        self.assertTrue(self.output.exists())
        self.assertEqual(self.supervisor.reconcile(self.queue, 'wake-1')['state'], 'progressed')

    def test_a_lost_sender_before_delivery_is_not_guessed_safe_to_retry(self):
        self.schedule()
        with patch.object(self.supervisor, 'invoke', side_effect=[{'ready': True, 'allowance': 'eligible',
                                                                  'approval': 'clear'}, KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):
                self.supervisor.tick(self.queue)
        self.assertEqual(self.supervisor.tick(self.queue)['state'], 'uncertain')
        self.assertFalse(self.output.exists())
        with self.assertRaisesRegex(ValueError, 'does not prove'):
            self.supervisor.reconcile(self.queue, 'wake-1')


if __name__ == '__main__':
    unittest.main()
