#!/usr/bin/env python3
"""Taking over an autopilot run card ends the old run's merge grant and keeps its terminal state as history."""
import datetime
import os
import shlex
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


class TakeOver(unittest.TestCase):
    def test_a_repeated_state_key_reads_the_first_line_that_the_writer_edits(self):
        text = '## State\nterminal: delivered\nterminal: blocked\n'
        self.assertEqual(card.field(text, 'terminal'), 'delivered')
        cleared = card.set_field(text, 'terminal', '')
        self.assertEqual(cleared, '## State\nterminal:\nterminal: blocked\n')
        self.assertEqual(card.field(cleared, 'terminal'), '')

    def take_over(self, *flags, card=CARD, sessions=None, code=0, error=None):
        """Take over a copy of `card` from only `sessions` (default: Pi host-2), expecting `code` and optional `error`."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'autopilot.md'
        path.write_text(card)
        env = support.without_host_sessions()
        result = subprocess.run([sys.executable, '-B', str(COMMAND), 'take-over', str(path), *flags],
                                capture_output=True, text=True, timeout=20,
                                env=dict(env, **({'PI_SESSION_ID': 'host-2'} if sessions is None else sessions)))
        self.assertEqual(result.returncode, code, result.stderr)
        if error is not None:
            self.assertIn(error, result.stderr)
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
        self.assertEqual(self.take_over(card=without, code=1, error='## State'), without)

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
                self.assertEqual(self.take_over(card=card, code=1, error=key), card)

    def test_a_level_outside_the_five_is_refused_and_changes_nothing(self):
        # Rule: only low, medium, high, xhigh and max are levels; anything else fails without touching the card.
        self.assertEqual(self.take_over('--effort', 'ultra', code=2, error='ultra'), CARD)

    def test_a_card_without_the_level_lines_is_refused(self):
        # Rule: a card always records both levels, so one without those lines has no reader here.
        without = CARD.replace('dispatch-effort: low\n', '').replace('review-effort: xhigh\n', '')
        self.assertEqual(self.take_over(card=without, code=1, error='dispatch-effort'), without)

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
board: local
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
        env = support.without_host_sessions()
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
                         'board, dispatch-effort, review-effort, evidence, timer, resume: write each line of the run '
                         "card's "
                         f'State ({run_card}), then confirm'))
        # Every line of the format's State, as run-card.md writes it.
        template = run_card.read_text().split('\n## State\n', 1)[1].split('```', 1)[0]
        keys = [line.split(':', 1)[0] for line in template.splitlines() if ':' in line]
        self.assertEqual(len(keys), 12)
        for key in keys:
            with self.subTest(missing=key):
                head, state = DRAFT.split('## State\n')  # The Goal's quoted `session:` line stays.
                lacking = head + '## State\n' + '\n'.join(line for line in state.split('\n') if not line.startswith(f'{key}:'))
                text, error = self.confirm('--words', 'go', card=lacking, sessions={'PI_SESSION_ID': 'pi-1'}, code=1)
                self.assertEqual((text, error.split(':', 1)[0]), (lacking, f"the card's State lacks {key}"))
        for words, sessions, refusal in (
                (' ', {'PI_SESSION_ID': 'pi-1'}, "confirm takes --words: the user's words that confirmed this card"),
                ('go', {}, 'no host session ID is exported here (CLAUDE_CODE_SESSION_ID, CODEX_SESSION_ID, PI_SESSION_ID, '
                           'OPENCODE_SESSION_ID): confirm from the shell of the host session that runs this autopilot')):
            with self.subTest(refusal=refusal):
                self.assertEqual(self.confirm('--words', words, sessions=sessions, code=1), (DRAFT, refusal + '\n'))


class Phase(unittest.TestCase):
    """`phase` sets the run's phase; dispatch and pr need the tickets on the board the card names."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop-phase-')
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(self.repo)], check=True)
        self.card = self.repo / '.scratch' / 'run' / 'autopilot.md'
        self.card.parent.mkdir(parents=True)

    def phase(self, phase, board='local', code=0, phase_line=True):
        """Set `phase` on a card naming `board` (None: no board line), with or without a `phase:` line; the card
        after, and stderr."""
        text = DRAFT.replace('confirmed: no', 'confirmed: 2026-11-06 "go"').replace(UNNAMED, 'session: host-1')
        text = text.replace('board: local\n', f'board: {board}\n' if board is not None else '')
        text = text if phase_line else text.replace('phase: refine\n', '')
        self.card.write_text(text)
        result = subprocess.run([sys.executable, '-B', str(COMMAND), 'phase', str(self.card), phase],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stderr)
        return self.card.read_text(), result.stderr

    def ticket(self, number, blocked_by=''):
        support.ticket_file(self.repo, f'run/{number}', blocked_by=blocked_by, touches='')

    def test_a_phase_is_one_of_the_four_and_dispatch_needs_the_local_boards_tickets(self):
        # Live (jose, Pi): the run set `phase: dispatch (inline)` by hand with no ticket on any board, so the panel
        # had nothing to count. A phase is set by this command, and dispatch and pr only with tickets to work.
        text, _ = self.phase('align')
        self.assertIn('\nphase: align\n', text)
        self.assertEqual(self.phase('dispatch (inline)', code=2)[0], text.replace('phase: align', 'phase: refine'))
        new = shlex.join(['python3', str(COMMAND.resolve().parent / 'next_tickets.py'), '--repo', str(self.repo), 'run',
                          '--new', '<title>'])
        # Review (Sonnet): a note in the board's folder is no ticket.
        (self.repo / '.scratch' / 'run' / 'issues').mkdir()
        (self.repo / '.scratch' / 'run' / 'issues' / 'README.md').write_text('# notes\nticket: none yet\n')
        # Review (Codex): a front matter that never closes is no ticket either.
        (self.repo / '.scratch' / 'run' / 'issues' / '00-draft.md').write_text('---\nticket: run/00\n')
        for phase in ('dispatch', 'pr'):
            with self.subTest(phase=phase):
                text, error = self.phase(phase, code=1)
                self.assertEqual(error.strip(), f'the local board of run has no tickets: write each with `{new}`, then '
                                 f'set the phase')
                self.assertIn('\nphase: refine\n', text)
        # Review (Codex, Sonnet): a ticket with a blank id is the board's fault to name, not a missing ticket.
        (self.repo / '.scratch' / 'run' / 'issues' / '00-blank.md').write_text('---\nticket:\nstatus: ready\n---\n')
        self.assertIn("00-blank.md: invalid ticket id ''", self.phase('dispatch', code=1)[1])
        for draft in ('00-draft.md', '00-blank.md'):
            (self.repo / '.scratch' / 'run' / 'issues' / draft).unlink()
        self.ticket('01', blocked_by='run/09')
        for phase in ('dispatch', 'pr'):
            with self.subTest(phase=phase):
                text, error = self.phase(phase, code=1)
                self.assertIn('the local board of run fails its check: run/01: blocked_by run/09 does not exist', error)
        self.ticket('01')
        self.assertIn('\nphase: dispatch\n', self.phase('dispatch')[0])
        # Review (Sonnet): the stop hook names this command for a card with no `phase:` line too, so it writes one.
        text, _ = self.phase('align', phase_line=False)
        self.assertIn('\nreview-effort: high\nterminal:\nhistory:\nevidence:\ntimer:\nresume: `autopilot --take-over`\nphase: align\n', text)

    def test_dispatch_on_a_tracker_board_needs_no_local_tickets_and_a_card_names_its_board(self):
        # Question tool (board-tools, "Card names its board"): a tracker holds its own tickets; the card says which.
        self.assertIn('\nphase: dispatch\n', self.phase('dispatch', board='https://linear.app/team/project/x')[0])
        for board, refusal in ((None, 'the card has no `board:` line'),
                               ("<local | the tracker's URL>", "the card's `board:` is neither `local` nor a tracker's URL: ''"),
                               ('jira', "the card's `board:` is neither `local` nor a tracker's URL: 'jira'"),
                               ('https://', "the card's `board:` is neither `local` nor a tracker's URL: 'https://'"),
                               ('https://?project=1', "the card's `board:` is neither `local` nor a tracker's URL: "
                                                      "'https://?project=1'"),
                               ('https://:443', "the card's `board:` is neither `local` nor a tracker's URL: 'https://:443'"),
                               *((bad, f"the card's `board:` is neither `local` nor a tracker's URL: {bad!r}")
                                 for bad in ('https://host:abc/x', 'https://<tracker>', 'https://linear.app/a b',
                                             'https://[bad/x'))):
            with self.subTest(board=board):
                text, error = self.phase('dispatch', board=board, code=1)
                self.assertEqual(error.strip(), refusal)
                self.assertIn('\nphase: refine\n', text)


if __name__ == '__main__':
    unittest.main()
