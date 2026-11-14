#!/usr/bin/env python3
"""native_wait.py, a `watch` argv for a worker with no wait process: a native Claude subagent's wait ends when its
session record ends its turn, a T3 worker's when T3 reports its thread's turn over."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import support

SCRIPT = support.ROOT / 'scripts/native_wait.py'


def turn(stop_reason, role='assistant'):
    return json.dumps({'type': role, 'message': {'role': role, 'stop_reason': stop_reason}}) + '\n'


class NativeWait(unittest.TestCase):
    def wait(self, *rows, runtime='claude', missing=False):
        """Run the script on a record holding `rows` (or on no record) with a short timeout; its exit code and output."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'agent.jsonl'
        if not missing:
            path.write_text(''.join(rows))
        process = subprocess.run([sys.executable, '-B', str(SCRIPT), runtime, str(path), '--timeout', '0.3',
                                  '--interval', '0.05'], capture_output=True, text=True, timeout=30)
        return process.returncode, process.stdout + process.stderr

    def test_a_turn_that_ended_before_the_script_started_exits_zero(self):
        # Rule: the last assistant row ended its turn and nothing follows it, whenever that happened.
        code, output = self.wait(turn('tool_use'), turn(None, 'user'), turn('end_turn'))
        self.assertEqual(code, 0, output)

    def test_an_assignment_sent_after_an_ended_turn_keeps_waiting(self):
        # Rule: a user row after the last end_turn is the next assignment, not a finished turn.
        code, output = self.wait(turn('end_turn'), turn(None, 'user'))
        self.assertEqual(code, 124, output)

    def test_a_tool_call_and_its_result_are_not_the_end_of_the_turn(self):
        # Rule: tool results are user rows, so tool_use then tool_result is a turn in progress.
        for rows in ((turn('tool_use'),), (turn('tool_use'), turn(None, 'user'))):
            with self.subTest(rows=len(rows)):
                code, output = self.wait(*rows)
                self.assertEqual(code, 124, output)

    def test_a_missing_record_is_waited_for(self):
        code, output = self.wait(missing=True)
        self.assertEqual(code, 124, output)

    def test_a_runtime_other_than_claude_and_t3_is_refused_with_the_supported_ones(self):
        code, output = self.wait(turn('end_turn'), runtime='codex')
        self.assertEqual(code, 2, output)
        self.assertIn('claude', output)
        self.assertIn('t3', output)


class T3Wait(unittest.TestCase):
    def wait(self, *answers, timeout='0.6'):
        """Run the script on thread th-1 against a `t3` stub whose `thread show` gives the next of `answers` each time
        it is asked (the last one repeats): a status, `archived:<status>`, or `says:<sentence>`, which the CLI
        answers instead on stderr with exit 1 and no JSON. Its exit code and output."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        stub = root / 't3'
        stub.write_text('#!' + sys.executable + '\n' + f'''
import json, sys
from pathlib import Path
answers, asked = {list(answers)!r}, Path({str(root / 'asked')!r})
count = int(asked.read_text()) if asked.exists() else 0
asked.write_text(str(count + 1))
answer = answers[min(count, len(answers) - 1)]
if sys.argv[1:4] != ['thread', 'show', '--json'] or sys.argv[4] != 'th-1':
    sys.exit(3)
if answer.startswith('says:'):
    sys.exit(answer[5:])
print(json.dumps({{'thread': {{'threadId': 'th-1', 'status': answer.split(':')[-1],
                             'archived': answer.startswith('archived:')}}, 'messages': []}}))
''')
        stub.chmod(0o700)
        environment = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'])
        for marker in ('T3CODE_CLI_PATH', 'T3CODE_THREAD_ID', 'T3_MCP_URL', 'T3_MCP_BEARER_TOKEN'):
            environment.pop(marker, None)
        process = subprocess.run([sys.executable, '-B', str(SCRIPT), 't3', 'th-1', '--timeout', timeout,
                                  '--interval', '0.05'], capture_output=True, text=True, timeout=60, env=environment)
        return process.returncode, process.stdout + process.stderr

    def test_the_wait_ends_once_the_threads_turn_completed(self):
        # Rule: T3's own answer ends the wait: 0 once the thread's turn completed, whenever that happened.
        for answers in (('running', 'queued', 'completed'), ('completed',)):
            with self.subTest(answers=answers):
                code, output = self.wait(*answers, timeout='20')
                self.assertEqual(code, 0, output)
                self.assertIn('t3 thread show --json th-1: completed', output)

    def test_a_turn_that_did_not_complete_ends_the_wait_with_one_and_t3s_answer(self):
        # Rule: a turn that ended another way, an agent waiting for an answer in the app, and a thread that is gone
        # all need the orchestrator: the wait ends 1 and says what T3 answered.
        for answer, said in (('failed', 'th-1: failed'), ('interrupted', 'th-1: interrupted'),
                             ('cancelled', 'th-1: cancelled'), ('waiting', 'th-1: waiting'),
                             ('archived:completed', 'th-1: completed, archived'),
                             ('says:Thread th-1 is no longer available.', 'th-1: no longer available')):
            with self.subTest(answer=answer):
                code, output = self.wait('running', answer, timeout='20')
                self.assertEqual(code, 1, output)
                self.assertIn(said, output)

    def test_a_running_turn_and_an_unanswered_read_are_waited_on(self):
        # Rule: neither a turn still running nor a read T3 does not answer is the end of the turn.
        unanswered = 'says:No running T3 Code server uses /home. Start T3 Code, then retry.'
        for answers, last in ((('running',), 'th-1: running'), (('preparing', 'starting'), 'th-1: starting'),
                              ((unanswered,), 'th-1: no answer (t3 thread show --json th-1 answered no JSON: No run')):
            with self.subTest(answers=answers):
                code, output = self.wait(*answers)
                self.assertEqual(code, 124, output)
                self.assertIn(last, output)  # The log of a wait that timed out names what T3 last answered.
        code, output = self.wait(unanswered, 'running', 'completed', timeout='20')
        self.assertEqual(code, 0, output)


if __name__ == '__main__':
    unittest.main()
