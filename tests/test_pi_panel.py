#!/usr/bin/env python3
"""The Pi panel draws a live effort's rows below the editor, clears both surfaces when it is not live, and /zloop-panel
opens or closes it."""
import shutil
import subprocess
import unittest

import support


class PiPanel(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node runs the Pi extension')
    def test_the_panel_draws_below_the_editor_and_clears_when_not_live(self):
        run = subprocess.run(['node', str(support.ROOT / 'tests/test_pi_panel.mjs')], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('pi panel: 15 cases passed', run.stdout)


if __name__ == '__main__':
    unittest.main()
