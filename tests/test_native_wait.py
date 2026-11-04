#!/usr/bin/env python3
"""native_wait.py, a `watch` argv for a native Claude subagent: it ends when the session record ends its turn."""

import json
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
        process = subprocess.run([sys.executable, '-B', str(SCRIPT), runtime, str(path), '--timeout', '1',
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
        code, output = self.wait(turn('tool_use'), turn(None, 'user'), turn('end_turn'))
        self.assertEqual(code, 0, output)

    def test_a_missing_record_is_waited_for(self):
        code, output = self.wait(missing=True)
        self.assertEqual(code, 124, output)

    def test_a_runtime_other_than_claude_is_refused_with_the_supported_one(self):
        code, output = self.wait(turn('end_turn'), runtime='codex')
        self.assertEqual(code, 2, output)
        self.assertIn('claude', output)


if __name__ == '__main__':
    unittest.main()
