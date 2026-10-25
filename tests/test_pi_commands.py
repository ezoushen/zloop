#!/usr/bin/env python3
"""Pi gets a /zloop:<skill> command for each skill the user types, completing the flags and modes its hint declares."""
import shutil
import subprocess
import unittest

import support


class PiCommands(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node runs the Pi extension')
    def test_commands_hand_off_as_skill_commands_and_complete_the_hint(self):
        run = subprocess.run(['node', str(support.ROOT / 'tests/test_pi_commands.mjs')], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('Pi zloop commands: registered, hand off as /skill:, complete the hinted flags and modes: ok',
                      run.stdout)


if __name__ == '__main__':
    unittest.main()
