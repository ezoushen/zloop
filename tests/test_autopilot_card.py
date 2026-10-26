#!/usr/bin/env python3
"""Taking over an autopilot run card ends the old run's merge grant and keeps its terminal state as history."""
import datetime
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support

COMMAND = support.script('autopilot_card.py')
CARD = """# run — autopilot run card

## Goal
Ship it.

## State
confirmed: 2026-10-25 "go"
session: host-1
auto-merge: yes
phase: pr
terminal: merged
history: waiting-on-user 2026-10-24
evidence: abc123
resume: `autopilot --take-over`
"""


class TakeOver(unittest.TestCase):
    def take_over(self, *flags, card=CARD):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'autopilot.md'
        path.write_text(card)
        result = subprocess.run([sys.executable, '-B', str(COMMAND), 'take-over', str(path), *flags],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return path.read_text()

    def test_a_take_over_grants_merging_only_with_its_own_flag_and_keeps_the_old_state_as_history(self):
        # Rule: the merge grant lapses with the terminal state; the new run holds only what its take-over grants.
        today = datetime.date.today().isoformat()
        for flags, merge in (((), 'no'), (('--auto-merge',), 'yes')):
            text = self.take_over(*flags)
            self.assertEqual(text, CARD.replace('auto-merge: yes', f'auto-merge: {merge}')
                             .replace('terminal: merged', 'terminal:')
                             .replace('history: waiting-on-user 2026-10-24',
                                      f'history: waiting-on-user 2026-10-24; merged {today}'))

    def test_a_card_that_never_ended_keeps_its_history_and_still_takes_its_grant_from_the_flag(self):
        open_card = CARD.replace('terminal: merged', 'terminal:').replace('history: waiting-on-user 2026-10-24', 'history:')
        text = self.take_over(card=open_card)
        self.assertEqual(text, open_card.replace('auto-merge: yes', 'auto-merge: no'))

    def test_a_first_ended_state_starts_the_history(self):
        today = datetime.date.today().isoformat()
        first = CARD.replace('history: waiting-on-user 2026-10-24', 'history: <earlier terminal states>')
        self.assertIn(f'history: merged {today}\n', self.take_over(card=first))


if __name__ == '__main__':
    unittest.main()
