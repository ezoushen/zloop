#!/usr/bin/env python3
"""Pi's extension logs every Herdr agent launch for the yield gate, a subagent's under its parent session."""
import shutil
import subprocess
import unittest

import support


class PiLaunches(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node runs the Pi extension')
    def test_every_launch_is_logged_and_a_childs_under_its_parent(self):
        run = subprocess.run(['node', str(support.ROOT / 'tests/test_pi_launches.mjs')], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("Pi launch logging: every launch, a child's under its parent session: ok", run.stdout)


if __name__ == '__main__':
    unittest.main()
