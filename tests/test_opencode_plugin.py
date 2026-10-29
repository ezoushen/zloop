#!/usr/bin/env python3
"""The OpenCode plugin holds an orchestrator that stops with a yield-gate gap and logs its Herdr launches."""
import shutil
import subprocess
import unittest

import support


class OpenCodePlugin(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node runs the OpenCode plugin')
    def test_the_plugin_holds_once_per_stop_skips_children_fails_closed_and_logs_launches(self):
        run = subprocess.run(['node', str(support.ROOT / 'tests/test_opencode_plugin.mjs')], capture_output=True,
                             text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('OpenCode yield gate plugin: ok', run.stdout)


if __name__ == '__main__':
    unittest.main()
