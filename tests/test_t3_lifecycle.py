#!/usr/bin/env python3
"""A T3 Code worker lives in a thread of its own: registration asks T3 where it is; the wait reads its state;
retirement leaves the thread idle. The `t3` CLI is stubbed like the Herdr fixture stubs `herdr`."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from lifecycle_case import WorkerLifecycleCase, SCRIPT


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
        payload = {'threadId': thread, 'title': title, 'status': status,
                   'providerInstanceId': runtime, 'model': model,
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


class Registration(T3Threads):
    def test_registration_records_the_thread_t3_itself_reports(self):
        # Rule: a t3 worker registers the thread id T3 confirms; a thread that does not exist refuses.
        saved = self.registered()
        self.assertEqual(saved['t3'], {'thread': 'th-1', 'status': 'completed', 'runtime': 'claude',
                                       'model': 'sonnet'})
        self.assertNotIn('herdr', saved)  # No Herdr placement: the two transports stay apart.

    def test_registration_refuses_a_thread_that_does_not_exist(self):
        self.t3_route()
        self.t3_fixture(show_error='not_found')
        error = self.register(worker='th-1', t3={'thread': 'th-1'}, code=2)
        self.assertIn('th-1', str(error))

    def test_a_herdr_registration_does_not_ask_t3(self):
        # Rule: the transports are distinct; a herdr route never triggers a t3 lookup.
        self.herdr_fixture()
        self.register(worker='builder-1', herdr={'session': 'fixture-session'})
        self.assertFalse((self.root / 't3-argv.log').exists())


class RegistrationRefusals(T3Threads):
    def test_registration_refuses_a_thread_another_live_attempt_holds(self):
        # Rule (SR-10): a T3 thread runs one worker; a second attempt on the same live thread is refused with the
        # holder named, even from a fresh worker handle and session.
        self.registered()
        self.record('startup', attempt='ticket-1/1', native=str(self.native('ticket-1/1')))
        error = self.register('ticket-2/1', assignment='ticket-2', session='native-2', worker='th-2',
                              t3={'thread': 'th-1'}, code=2)
        self.assertIn('already holds attempt ticket-1/1', str(error))

    def test_registration_refuses_an_unknown_thread_with_its_reason(self):
        # Rule (SR-20): the not-found reason is the refusal, not a generic failure.
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
        self.assertIn('t3 thread show --json th-1', str(attempt.get('closed_evidence', attempt)))




class WaitingThreadRetirement(T3Threads):
    def test_retirement_refuses_a_thread_waiting_for_an_answer(self):
        # Rule (SR-14): waiting-on-answer is the user's decision, not the orchestrator's.
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
    """SR-09: a t3 submit goes through `t3 thread send --wait`; the lifecycle checks the packet text, maps the
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
        error = self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=2,
                         env_override=None) if False else None
        # submit runs through the lifecycle CLI; use record-level expectations instead:
        # the submit guard fires inside `submit`; call it via the CLI with a bad argv and expect exit 2.
        import subprocess
        process = subprocess.run([sys.executable, '-B',
                                  '/Users/ezou/Workspace/zloop/.worktrees/t3code-support/scripts/worker_lifecycle.py',
                                  'submit', '--state', str(self.state), '--attempt', 'ticket-1/1',
                                  '--input', str(request)], capture_output=True, text=True)
        self.assertEqual(process.returncode, 2)
        self.assertIn('t3 thread send', process.stderr + process.stdout)

if __name__ == '__main__':
    unittest.main()
