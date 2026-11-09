#!/usr/bin/env python3
"""A T3 Code worker lives in a thread of its own: registration asks T3 where it is; the wait reads its state;
retirement leaves the thread idle. The `t3` CLI is stubbed like the Herdr fixture stubs `herdr`."""

import json
import sys
import unittest
from unittest.mock import patch

from lifecycle_case import WorkerLifecycleCase


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
                   'messages': [{'role': 'assistant', 'content': [{'type': 'text', 'text': 'assignment done'}]}]}
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


if __name__ == '__main__':
    unittest.main()
