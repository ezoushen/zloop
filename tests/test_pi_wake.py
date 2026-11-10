#!/usr/bin/env python3
"""A Pi run schedules its own resume through the bootstrap extension's zloop_wake tool."""
import shutil
import subprocess
import unittest

import support


class PiWake(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node runs the Pi extension')
    def test_the_wake_tool_schedules_cancels_and_refuses_bad_times(self):
        run = subprocess.run(['node', str(support.ROOT / 'tests/test_pi_wake.mjs')], capture_output=True, text=True,
                             timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('Pi zloop_wake: schedules, cancels, refuses bad times, never holds the process: ok', run.stdout)


if __name__ == '__main__':
    unittest.main()
