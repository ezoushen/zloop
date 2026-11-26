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

    def schedule_expecting(self, head):
        """Schedule wake-1 through the command line, as an operator would, expecting the branch at `head`."""
        config = self.root / 'drivers.json'
        config.write_text(json.dumps(self.config))
        return subprocess.run([sys.executable, '-B', str(support.SCRIPTS / 'supervisor.py'), 'schedule', str(self.queue),
                               '--card', str(self.card), '--config', str(config), '--due', '0',
                               '--event-id', 'wake-1', '--expect-head', head], capture_output=True, text=True)

    def git(self, *args):
        return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false',
                               '-C', str(self.root), *args], check=True, capture_output=True, text=True).stdout.strip()

    def workstream_head(self):
        """Create the workstream's branch with one commit, as the reviewed branch override names it, and return its head."""
        (self.root / 'reports').mkdir()
        (self.root / 'reports' / 'spec-review.md').write_text('---\nworkstream_branch: feat/wake\n---\n')
        self.git('init', '-q', '-b', 'feat/wake')
        return self.advance_branch()

    def advance_branch(self):
        self.git('commit', '-q', '--allow-empty', '-m', 'work')
        return self.git('rev-parse', 'refs/heads/feat/wake')

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
            'print(\'{"ready": true, "allowance": "critical", "approval": "clear"}\')', '{receipt}']
        self.schedule()
        self.assertEqual(self.supervisor.tick(self.queue)['state'], 'pending')
        self.assertFalse(self.output.exists())

    def test_readiness_and_approval_each_prevent_notification(self):
        for name, checked in [('unready', dict(ready=False, allowance='eligible', approval='clear')),
                              ('approval', dict(ready=True, allowance='eligible', approval='pending'))]:
            with self.subTest(name=name):
                config = dict(self.config, preflight=[sys.executable, '-c',
                    f'print({json.dumps(checked)!r})', '{receipt}'])
                queue = self.root / (name + '.json')
                self.supervisor.schedule(queue, self.card, 0, config, name)
                self.assertEqual(self.supervisor.tick(queue)['state'], 'pending')
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

    def test_a_queued_parent_can_consume_its_receipt_after_sender_loss(self):
        work = self.config['notify']
        queued = self.root / 'queued.json'
        self.config['notify'] = [sys.executable, '-c',
            'from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2])',
            str(queued), '{receipt}']
        self.schedule()
        invoke = self.supervisor.invoke
        calls = []
        def accepted_then_lost(*args):
            calls.append(args)
            result = invoke(*args)
            if len(calls) == 2:
                raise KeyboardInterrupt('transport accepted before parent consumed')
            return result
        with patch.object(self.supervisor, 'invoke', side_effect=accepted_then_lost):
            with self.assertRaises(KeyboardInterrupt):
                self.supervisor.tick(self.queue)
        self.assertEqual(self.supervisor.tick(self.queue)['state'], 'uncertain')
        self.assertFalse(self.output.exists())
        result = subprocess.run([queued.read_text() if arg == '{receipt}' else arg for arg in work],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.output.read_text())['event_id'], 'wake-1')
        self.assertEqual(self.supervisor.reconcile(self.queue, 'wake-1')['state'], 'progressed')
        queued.unlink()
        self.assertEqual(self.supervisor.tick(self.queue)['state'], 'progressed')
        self.assertFalse(queued.exists(), 'the accepted receipt must not be sent again')

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

    def test_a_wake_expecting_a_head_is_cancelled_as_stale_when_the_branch_moves(self):
        expected = self.workstream_head()
        scheduled = self.schedule_expecting(expected)
        self.assertEqual(scheduled.returncode, 0, scheduled.stderr)
        moved = self.advance_branch()
        with patch.object(self.supervisor, 'invoke', wraps=self.supervisor.invoke) as invoke:
            result = self.supervisor.tick(self.queue)
        self.assertEqual(result['state'], 'cancelled')
        self.assertIn(expected, result['reason'])
        self.assertIn(moved, result['reason'])
        invoke.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_a_wake_expecting_the_branchs_current_head_proceeds(self):
        scheduled = self.schedule_expecting(self.workstream_head())
        self.assertEqual(scheduled.returncode, 0, scheduled.stderr)
        self.assertEqual(self.supervisor.tick(self.queue)['state'], 'progressed')
        self.assertTrue(self.output.exists())

    def test_work_the_resumed_run_commits_does_not_cancel_its_sent_wake(self):
        flag = self.root / 'target-progressed'
        self.config['progress'] = [sys.executable, '-c',
            'import json,pathlib,sys; r=json.loads(sys.argv[1]); '
            f'print(json.dumps(dict(event_id=r["event_id"], progressed=pathlib.Path({str(flag)!r}).exists())))',
            '{receipt}']
        scheduled = self.schedule_expecting(self.workstream_head())
        self.assertEqual(scheduled.returncode, 0, scheduled.stderr)
        self.assertEqual(self.supervisor.tick(self.queue)['state'], 'sent')
        self.advance_branch()
        flag.touch()
        self.assertEqual(self.supervisor.tick(self.queue)['state'], 'progressed')

    def test_scheduling_refuses_an_expected_head_the_branch_does_not_point_at(self):
        actual = self.workstream_head()
        expected = self.advance_branch()
        self.git('reset', '-q', '--hard', actual)
        refused = self.schedule_expecting(expected)
        self.assertEqual(refused.returncode, 2)
        self.assertRegex(refused.stderr, f'feat/wake.*{actual}.*{expected}')
        self.assertFalse(self.queue.exists())

    def test_scheduling_refuses_an_expected_head_that_is_not_a_full_sha(self):
        (self.root / 'reports').mkdir()
        (self.root / 'reports' / 'spec-review.md').write_text('---\nworkstream_branch: feat/wake\n---\n')
        self.git('init', '-q', '-b', 'main')
        self.git('commit', '-q', '--allow-empty', '-m', 'base')
        for head in ('', self.git('rev-parse', '--short', 'HEAD')):
            with self.subTest(head=head):
                refused = self.schedule_expecting(head)
                self.assertEqual(refused.returncode, 2)
                self.assertIn('full SHA', refused.stderr)
                self.assertFalse(self.queue.exists())


if __name__ == '__main__':
    unittest.main()
