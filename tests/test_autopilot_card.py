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
dispatch-effort: low
review-effort: xhigh
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

    def test_a_take_over_without_level_flags_keeps_the_cards_levels(self):
        # Rule: a take-over leaves both confirmed levels as they are when no level flag is given.
        text = self.take_over()
        self.assertIn('dispatch-effort: low\n', text)
        self.assertIn('review-effort: xhigh\n', text)

    def test_a_split_flag_changes_the_level_of_its_own_step(self):
        # Rule: a take-over may set one step's level from its split flag.
        text = self.take_over('--review-effort', 'max')
        self.assertIn('dispatch-effort: low\n', text)
        self.assertIn('review-effort: max\n', text)

    def test_effort_sets_both_levels(self):
        # Rule: `--effort` sets the dispatch and the review levels together.
        text = self.take_over('--effort', 'high')
        self.assertIn('dispatch-effort: high\n', text)
        self.assertIn('review-effort: high\n', text)

    def test_a_split_flag_overrides_effort_for_its_step(self):
        # Rule: the split flag wins over `--effort` for its own step and leaves the other at it.
        text = self.take_over('--effort', 'xhigh', '--dispatch-effort', 'low')
        self.assertIn('dispatch-effort: low\n', text)
        self.assertIn('review-effort: xhigh\n', text)

    def test_a_card_level_line_that_is_not_a_level_is_refused(self):
        # Rule: both card level lines must hold one of the five levels, blank or misspelled alike.
        lines = {'dispatch-effort': ('dispatch-effort: low', 'dispatch-effort:'),
                 'review-effort': ('review-effort: xhigh', 'review-effort: hgih')}
        for key, (current, broken) in lines.items():
            with self.subTest(line=broken):
                card = CARD.replace(current, broken)
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                path = Path(tmp.name) / 'autopilot.md'
                path.write_text(card)
                result = subprocess.run([sys.executable, '-B', str(COMMAND), 'take-over', str(path)],
                                        capture_output=True, text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stderr)
                self.assertEqual(path.read_text(), card)

    def test_a_level_outside_the_five_is_refused_and_changes_nothing(self):
        # Rule: only low, medium, high, xhigh and max are levels; anything else fails without touching the card.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'autopilot.md'
        path.write_text(CARD)
        result = subprocess.run([sys.executable, '-B', str(COMMAND), 'take-over', str(path), '--effort', 'ultra'],
                                capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ultra', result.stderr)
        self.assertEqual(path.read_text(), CARD)

    def test_a_card_without_the_level_lines_is_refused(self):
        # Rule: a card always records both levels, so one without those lines has no reader here.
        without = CARD.replace('dispatch-effort: low\n', '').replace('review-effort: xhigh\n', '')
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'autopilot.md'
        path.write_text(without)
        result = subprocess.run([sys.executable, '-B', str(COMMAND), 'take-over', str(path)],
                                capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('dispatch-effort', result.stderr)
        self.assertEqual(path.read_text(), without)

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
