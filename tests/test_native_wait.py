#!/usr/bin/env python3
"""native_wait.py, a `watch` argv for a native Claude subagent: it ends when the session record shows a finished turn."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import support

SCRIPT = support.ROOT / 'scripts/native_wait.py'


def turn(stop_reason, role='assistant'):
    return json.dumps({'type': role, 'message': {'role': role, 'stop_reason': stop_reason}}) + '\n'


class NativeWait(unittest.TestCase):
    def record(self, *rows):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'agent.jsonl'
        path.write_text(''.join(rows))
        return path

    def wait(self, path, *, timeout='1', runtime='claude', append=None):
        """Run the script; when `append` is given, add it to the record once the script is running."""
        process = subprocess.Popen([sys.executable, '-B', str(SCRIPT), runtime, str(path), '--timeout', timeout,
                                    '--interval', '0.05'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if append:
            time.sleep(0.5)  # the script has taken the record's size by now
            with path.open('a') as handle:
                handle.write(append)
        out, err = process.communicate(timeout=30)
        return process.returncode, out + err

    def test_a_record_that_grew_and_ended_its_turn_exits_zero(self):
        # Rule: the wait ends once the record is past its starting size and the last assistant turn is end_turn.
        path = self.record(turn('tool_use'))
        code, output = self.wait(path, timeout='10', append=turn('tool_use') + turn(None, 'user') + turn('end_turn'))
        self.assertEqual(code, 0, output)

    def test_a_record_that_already_ended_but_did_not_grow_keeps_waiting(self):
        # Rule: a turn that ended before the wait began is not this wait's completion; it ends only at its timeout.
        path = self.record(turn('end_turn'))
        code, output = self.wait(path)
        self.assertEqual(code, 124, output)

    def test_a_record_that_grew_in_a_running_turn_keeps_waiting(self):
        # Rule: growth alone is not completion; a last assistant turn still in tool_use means the worker runs.
        path = self.record(turn('end_turn'))
        code, output = self.wait(path, append=turn('tool_use') + turn(None, 'user'))
        self.assertEqual(code, 124, output)

    def test_a_runtime_other_than_claude_is_refused_with_the_supported_one(self):
        code, output = self.wait(self.record(turn('end_turn')), runtime='codex')
        self.assertEqual(code, 2, output)
        self.assertIn('claude', output)


if __name__ == '__main__':
    unittest.main()
