#!/usr/bin/env python3
"""Pi's startup hint reads `zloop host: pi`, as a system prompt suffix or as a section Pi diffs."""
import shutil
import subprocess
import unittest

import support


class PiHint(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node runs the Pi extension')
    def test_the_hint_names_the_host(self):
        run = subprocess.run(['node', str(support.ROOT / 'tests/test_pi_hint.mjs')], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('Pi startup hint: names the host as `zloop host: pi`: ok', run.stdout)


if __name__ == '__main__':
    unittest.main()
