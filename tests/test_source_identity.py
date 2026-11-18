#!/usr/bin/env python3
"""Installed runtime identity is content-based and excludes local execution residue."""
import shutil
import tempfile
from pathlib import Path
import unittest

import support


class SourceIdentity(unittest.TestCase):
    def test_a_changed_runtime_cannot_pass_a_matching_bootstrap(self):
        identity = support.load_script('source_identity.py')
        with tempfile.TemporaryDirectory() as tmp:
            source, installed = Path(tmp) / 'source', Path(tmp) / 'installed'
            (source / 'scripts').mkdir(parents=True)
            (source / 'bootstrap.md').write_text('same bootstrap')
            (source / 'scripts/worker.py').write_text('CURRENT = True\n')
            shutil.copytree(source, installed)
            self.assertEqual(identity.compare(installed, source)['status'], 'same')
            (installed / 'scripts/worker.py').write_text('CURRENT = False\n')
            result = identity.compare(installed, source)
            self.assertEqual(result['status'], 'mismatch')
            self.assertEqual(result['changed'], ['scripts/worker.py'])
            (installed / 'scripts/worker.py').unlink()
            self.assertEqual(identity.compare(installed, source)['missing'], ['scripts/worker.py'])

    def test_local_state_and_root_location_do_not_change_runtime_identity(self):
        identity = support.load_script('source_identity.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            (root / 'scripts/worker.py').write_text('pass\n')
            before = identity.snapshot(root)['digest']
            (root / '.scratch').mkdir()
            (root / '.scratch/state.json').write_text('private fixture')
            (root / 'scripts/__pycache__').mkdir()
            (root / 'scripts/__pycache__/ignored.py').write_text('ignored')
            self.assertEqual(identity.snapshot(root)['digest'], before)
            (root / 'scripts/new.py').write_text('pass\n')
            self.assertNotEqual(identity.snapshot(root)['digest'], before)


if __name__ == '__main__':
    unittest.main()
