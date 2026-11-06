#!/usr/bin/env python3
"""Taking over an autopilot run card ends the old run's merge grant and keeps its terminal state as history."""
import datetime
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support

COMMAND = support.script('autopilot_card.py')
card = support.load_script('autopilot_card.py', 'autopilot_card')
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

SESSIONS = ('CLAUDE_CODE_SESSION_ID', 'CODEX_SESSION_ID', 'PI_SESSION_ID', 'OPENCODE_SESSION_ID')


class TakeOver(unittest.TestCase):
    def take_over(self, *flags, card=CARD, sessions=None, code=0):
        """Take over a copy of `card` from a shell exporting only `sessions` (default: Pi session host-2)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'autopilot.md'
        path.write_text(card)
        env = {key: value for key, value in os.environ.items() if key not in SESSIONS}
        result = subprocess.run([sys.executable, '-B', str(COMMAND), 'take-over', str(path), *flags],
                                capture_output=True, text=True, timeout=20,
                                env=dict(env, **({'PI_SESSION_ID': 'host-2'} if sessions is None else sessions)))
        self.assertEqual(result.returncode, code, result.stderr)
        return path.read_text()

    def test_a_take_over_grants_merging_only_with_its_own_flag_and_keeps_the_old_state_as_history(self):
        # Rule: the merge grant lapses with the terminal state; the new run holds only what its take-over grants, and
        # it is the run of the session that took it over (card-confirm: the panel and the stop hold go by `session:`).
        today = datetime.date.today().isoformat()
        for flags, merge in (((), 'no'), (('--auto-merge',), 'yes')):
            text = self.take_over(*flags)
            self.assertEqual(text, CARD.replace('auto-merge: yes', f'auto-merge: {merge}')
                             .replace('session: host-1', 'session: host-2')
                             .replace('terminal: merged', 'terminal:')
                             .replace('history: waiting-on-user 2026-10-24',
                                      f'history: waiting-on-user 2026-10-24; merged {today}'))

    def test_an_interrupted_take_over_leaves_the_card_whole(self):
        # Audit A1: the card holds the unfinished run at stop; a write cut short must not leave it empty.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'autopilot.md'
        path.write_text(CARD)
        with (patch.object(sys, 'argv', ['autopilot_card.py', 'take-over', str(path)]),
              patch.dict(os.environ, {'PI_SESSION_ID': 'host-2'}),
              patch('os.replace', side_effect=OSError('interrupted')), self.assertRaises(OSError)):
            card.main()
        self.assertEqual(path.read_text(), CARD)
        self.assertEqual([entry.name for entry in path.parent.iterdir()], [path.name])

    def test_a_goal_that_quotes_state_lines_is_left_alone(self):
        # Audit A10: the State section is the card's state; the same words in the Goal are prose, never edited.
        example = ('Ship it. A take-over sets:\n```\nterminal: merged\nhistory: none\nauto-merge: yes\n'
                   'dispatch-effort: low\nreview-effort: high\n```\n')
        goal = CARD.replace('Ship it.\n', example)
        text = self.take_over(card=goal)
        self.assertEqual(text.split('## State')[0], goal.split('## State')[0])
        self.assertIn('\nauto-merge: no\n', text.split('## State')[1])

    def test_a_card_with_state_lines_only_outside_the_state_section_is_refused(self):
        # Audit A10: a line the State section lacks is not found in the Goal either.
        without = CARD.replace('## State', '## Notes')
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'autopilot.md'
        path.write_text(without)
        result = subprocess.run([sys.executable, '-B', str(COMMAND), 'take-over', str(path)],
                                capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('## State', result.stderr)
        self.assertEqual(path.read_text(), without)

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
        self.assertEqual(text, open_card.replace('auto-merge: yes', 'auto-merge: no').replace('session: host-1',
                                                                                             'session: host-2'))

    def test_a_first_ended_state_starts_the_history(self):
        today = datetime.date.today().isoformat()
        first = CARD.replace('history: waiting-on-user 2026-10-24', 'history: <earlier terminal states>')
        self.assertIn(f'history: merged {today}\n', self.take_over(card=first))

    def test_a_take_over_from_a_shell_with_no_host_session_is_refused(self):
        # Rule (card-confirm): a run nobody can tie to a session has no panel and no stop hold.
        self.assertEqual(self.take_over(sessions={}, code=1), CARD)

    def test_a_take_over_names_each_host_session_and_refuses_a_card_without_a_session_line(self):
        # Review (Sonnet, card-confirm): a take-over wrote no session on a card that had no `session:` line, so the
        # resumed run stayed untied; and a host started from another host's shell is both sessions'.
        text = self.take_over(sessions={'CLAUDE_CODE_SESSION_ID': 'claude-1', 'CODEX_SESSION_ID': 'codex-2'})
        self.assertIn('\nsession: claude-1, codex-2\n', text)
        without = CARD.replace('session: host-1\n', '')
        self.assertEqual(self.take_over(card=without, code=1), without)


DRAFT = """# run — autopilot run card

## Goal
Ship it. The user wrote:
session: theirs, quoted, not this card's

## State
confirmed: no
session: <each host session ID this shell exports, comma-separated; `confirm` and `take-over` rewrite it>
auto-merge: no
dispatch-effort: medium
review-effort: high
phase: refine
terminal:
history:
evidence:
timer:
resume: `autopilot --take-over`
"""
UNNAMED = 'session: <each host session ID this shell exports, comma-separated; `confirm` and `take-over` rewrite it>'



class Confirm(unittest.TestCase):
    def confirm(self, *args, card=DRAFT, sessions=None, code=0):
        """Run `confirm` on a copy of `card` from a shell exporting only `sessions` (a SESSIONS name to its value);
        the card's text after it, and what it printed to stderr."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'autopilot.md'
        path.write_text(card)
        env = {key: value for key, value in os.environ.items() if key not in SESSIONS}
        result = subprocess.run([sys.executable, '-B', str(COMMAND), 'confirm', str(path), *args], capture_output=True,
                                text=True, timeout=20, env=dict(env, **(sessions or {})))
        self.assertEqual(result.returncode, code, result.stderr)
        return path.read_text(), result.stderr

    def test_confirming_writes_the_users_words_and_each_host_session_exported_here_and_nothing_else(self):
        # Live (jose, Pi): a hand-written card named no session, so neither the panel nor the stop hold found its run.
        today = datetime.date.today().isoformat()
        confirmed = DRAFT.replace('confirmed: no', f'confirmed: {today} "go ahead, inline"')
        text, _ = self.confirm('--words', 'go ahead, inline', sessions={'PI_SESSION_ID': 'pi-1'})
        self.assertEqual(text, confirmed.replace(UNNAMED, 'session: pi-1'))
        # A host started from another host's shell exports both, and the stop hook counts each.
        text, _ = self.confirm('--words', 'go ahead, inline',
                               sessions={'CODEX_SESSION_ID': 'codex-2', 'CLAUDE_CODE_SESSION_ID': 'claude-1'})
        self.assertEqual(text, confirmed.replace(UNNAMED, 'session: claude-1, codex-2'))
        # Review (Sonnet): words on several lines wrote their own `session:` line into the State; they are one line.
        text, _ = self.confirm('--words', 'go ahead,\n\nsession: mine\u2028  inline', sessions={'PI_SESSION_ID': 'pi-1'})
        self.assertEqual(text, DRAFT.replace('confirmed: no', f'confirmed: {today} "go ahead, session: mine inline"')
                         .replace(UNNAMED, 'session: pi-1'))


    def test_confirming_refuses_a_card_missing_a_state_line_empty_words_or_no_host_session(self):
        # Live (jose, Pi): its State held only phase, terminal and history. The lines expected are the run-card format's.
        run_card = COMMAND.resolve().parents[1] / 'skills/autopilot/run-card.md'
        hand_written = '# maven-warp-closeout\n\n## Goal\nx\n\n## State\nphase: refine\nterminal:\nhistory:\n- 10:04 HOLD\n'
        text, error = self.confirm('--words', 'go', card=hand_written, sessions={'PI_SESSION_ID': 'pi-1'}, code=1)
        self.assertEqual((text, error.strip()), (hand_written, "the card's State lacks confirmed, session, auto-merge, "
                         'dispatch-effort, review-effort, evidence, timer, resume: write each line of the run card\'s '
                         f'State ({run_card}), then confirm'))
        # Every line of the format's State, as run-card.md writes it.
        template = run_card.read_text().split('\n## State\n', 1)[1].split('```', 1)[0]
        keys = [line.split(':', 1)[0] for line in template.splitlines() if ':' in line]
        self.assertEqual(len(keys), 11)
        for key in keys:
            with self.subTest(missing=key):
                lacking = '\n'.join(line for line in DRAFT.split('\n') if not line.startswith(f'{key}:'))
                text, error = self.confirm('--words', 'go', card=lacking, sessions={'PI_SESSION_ID': 'pi-1'}, code=1)
                self.assertEqual((text, error.split(':', 1)[0]), (lacking, f"the card's State lacks {key}"))
        for words, sessions, refusal in (
                (' ', {'PI_SESSION_ID': 'pi-1'}, "confirm takes --words: the user's words that confirmed this card"),
                ('go', {}, 'no host session ID is exported here (CLAUDE_CODE_SESSION_ID, CODEX_SESSION_ID, PI_SESSION_ID, '
                           'OPENCODE_SESSION_ID): confirm from the shell of the host session that runs this autopilot')):
            with self.subTest(refusal=refusal):
                self.assertEqual(self.confirm('--words', words, sessions=sessions, code=1), (DRAFT, refusal + '\n'))


if __name__ == '__main__':
    unittest.main()
