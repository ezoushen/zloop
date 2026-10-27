#!/usr/bin/env python3
"""The panel's fact source: which effort is live, and the rows a host draws for it."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

import support

PANEL = support.load_script('panel_state.py')


def card(path, phase='dispatch', session='S', terminal='', confirmed='yes 2026-10-27 "go"', resume='`autopilot --take-over`'):
    """A run card with the State section the panel reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join([
        f'# {path.parent.name} — autopilot run card', '', '## Goal', 'a goal', '',
        '## State', f'confirmed: {confirmed}', f'session: {session}', 'auto-merge: no',
        f'phase: {phase}', f'terminal: {terminal}', 'history:', 'evidence:', 'timer:', f'resume: {resume}', '']))
    return path


def coordinator(effort, sessions):
    """The binding `next_tickets.py --coordinate` writes: the session running the effort."""
    path = effort / 'reports' / 'coordinator.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'pane': 'w1J:p36', 'sessions': sessions, 'autofix': True}))
    return path


def ticket(path, name, status='ready', blocker=None):
    """A local ticket with the front matter the board reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ['---', f'ticket: {name}', f'status: {status}', 'blocked_by: []', 'touches: [scripts]',
             'devices: []', 'model: standard', 'worker:']
    if blocker:
        lines.append(f'blocker: {blocker}')
    lines += ['---', f'# {name}: a behaviour', '', '**Story:** a story.', '']
    path.write_text('\n'.join(lines))
    return path


def attempts(state, rows):
    """A workers.sqlite holding the attempts and their newest event times given."""
    state.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(state)
    db.execute('CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, attempt TEXT NOT NULL, '
               'kind TEXT NOT NULL, payload TEXT NOT NULL, acknowledged INTEGER NOT NULL)')
    for row in rows:
        db.execute('INSERT OR REPLACE INTO attempts VALUES (?, ?)', (row['attempt'], json.dumps(row)))
        last = row.pop('_quiet_seconds', 30)
        db.execute('INSERT INTO events (attempt, kind, payload, acknowledged) VALUES (?, ?, ?, ?)',
                   (row['attempt'], 'register', json.dumps({'recorded_at': time.time() - last, 'attempt': row['attempt']}), 1))
    db.commit()
    db.close()
    return state


def live(attempt, assignment='zloop-panel/01', agent='builder', model='sonnet', quiet=30):
    return {'attempt': attempt, 'assignment': assignment, 'repo': '/repo', 'runtime': 'claude',
            'native_session': f'native-{attempt}', 'worker': f'w-{attempt}', 'agent': agent, 'job': 'ticket',
            'owned': True, 'requested': {'model': model}, '_quiet_seconds': quiet}


class PanelState(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='panel state ')
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        self.scratch = self.repo / '.scratch'

    def snapshot(self, session='S'):
        return PANEL.snapshot(self.repo, session, time.time())

    def row_texts(self, snapshot, kind=None):
        return [row['text'] for row in snapshot['rows'] if kind is None or row['kind'] == kind]

    def test_a_live_card_is_active_and_names_effort_and_phase(self):
        card(self.scratch / 'demo' / 'autopilot.md', phase='dispatch')
        found = self.snapshot()
        self.assertTrue(found['active'])
        self.assertEqual('demo', found['effort'])
        self.assertEqual('dispatch', found['phase'])
        self.assertEqual(['demo · dispatch'], self.row_texts(found, 'headline'))


    def test_an_ended_phase_draws_nothing(self):
        # Only the phase says ended: the terminal state is empty, so each rule is exercised on its own.
        card(self.scratch / 'demo' / 'autopilot.md', phase='ended', terminal='')
        found = self.snapshot()
        self.assertFalse(found['active'])
        self.assertEqual([], found['rows'])
        self.assertIsNone(found['strip'])
        self.assertEqual('', PANEL.render_text(found))

    def test_a_delivered_run_with_nothing_live_draws_nothing(self):
        # Only the terminal state says delivered: the phase is still dispatch. The ready ticket shows the terminal
        # state alone decides a carded effort, since a card-less one would stay live on that ticket.
        card(self.scratch / 'demo' / 'autopilot.md', phase='dispatch', terminal='delivered')
        ticket(self.scratch / 'demo' / 'issues' / '01-a.md', 'demo/01', status='ready')
        found = self.snapshot()
        self.assertFalse(found['active'])
        self.assertEqual([], found['rows'])

    def test_run_rows_are_capped_with_a_more_row(self):
        card(self.scratch / 'demo' / 'autopilot.md')
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite',
                 [live(f'12/ticket-{n}', assignment=f'zloop-panel/0{n}', quiet=60 * n) for n in range(1, 6)])
        found = self.snapshot()
        rows = self.row_texts(found, 'run')
        self.assertEqual(3, len(rows))
        self.assertEqual(['+2 more'], self.row_texts(found, 'more'))
        self.assertIn('builder', rows[0])
        self.assertIn('sonnet', rows[0])
        self.assertIn('quiet 1m', rows[0])
        for row, number in zip(rows, (1, 2, 3)):
            self.assertIn(f'zloop-panel/0{number}', row)

    def test_one_blocker_row_by_precedence(self):
        card(self.scratch / 'demo' / 'autopilot.md', terminal='paused-by-user')
        ticket(self.scratch / 'demo' / 'issues' / '01-a.md', 'demo/01', status='blocked', blocker='no approval')
        found = self.snapshot()
        blockers = self.row_texts(found, 'blocker')
        self.assertEqual(1, len(blockers))
        self.assertIn('paused-by-user', blockers[0])
        self.assertNotIn('no approval', blockers[0])

    def test_an_unreadable_state_file_degrades_to_the_card(self):
        card(self.scratch / 'demo' / 'autopilot.md')
        broken = self.scratch / 'demo' / 'reports' / 'workers.sqlite'
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_bytes(b'not a database')
        result = subprocess.run([sys.executable, '-B', str(support.script('panel_state.py')),
                                 '--repo', str(self.repo), '--session-id', 'S', '--json'],
                                capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        found = json.loads(result.stdout)
        self.assertTrue(found['active'])
        self.assertEqual([], [row for row in found['rows'] if row['kind'] in ('run', 'wait')])
        self.assertIn('workers.sqlite', result.stderr)

    def test_the_effort_naming_this_session_wins(self):
        card(self.scratch / 'alpha' / 'autopilot.md', session='S')
        newer = card(self.scratch / 'beta' / 'autopilot.md', session='other')
        later = time.time() + 60
        os.utime(newer, (later, later))
        self.assertEqual('alpha', self.snapshot('S')['effort'])
        self.assertEqual('beta', self.snapshot('other')['effort'])

    def test_an_effort_claimed_by_another_session_is_not_shown(self):
        card(self.scratch / 'beta' / 'autopilot.md', session='other')
        self.assertFalse(self.snapshot('S')['active'])

    def test_counts_come_from_the_efforts_tickets(self):
        card(self.scratch / 'demo' / 'autopilot.md')
        ticket(self.scratch / 'demo' / 'issues' / '01-a.md', 'demo/01', status='done')
        ticket(self.scratch / 'demo' / 'issues' / '02-b.md', 'demo/02', status='ready')
        ticket(self.scratch / 'demo' / 'issues' / '03-c.md', 'demo/03', status='implementing')
        self.assertEqual(['1 done · 0 blocked · 1 ready · 1 live'], self.row_texts(self.snapshot(), 'counts'))

    def test_a_draft_card_says_its_confirmation_is_missing(self):
        card(self.scratch / 'demo' / 'autopilot.md', phase='align', confirmed='no')
        found = self.snapshot()
        self.assertTrue(found['draft'])
        self.assertIn('draft', self.row_texts(found, 'headline')[0])
        self.assertEqual(['awaiting your confirmation'], self.row_texts(found, 'next'))

    def test_a_ready_ticket_is_the_next_row(self):
        card(self.scratch / 'demo' / 'autopilot.md')
        ticket(self.scratch / 'demo' / 'issues' / '01-a.md', 'demo/01', status='done')
        ticket(self.scratch / 'demo' / 'issues' / '02-b.md', 'demo/02', status='ready')
        self.assertEqual(['next: demo/02'], self.row_texts(self.snapshot(), 'next'))

    def test_the_strip_names_the_effort_its_state_and_its_next_step(self):
        card(self.scratch / 'demo' / 'autopilot.md')
        ticket(self.scratch / 'demo' / 'issues' / '02-b.md', 'demo/02', status='ready')
        ticket(self.scratch / 'demo' / 'issues' / '03-c.md', 'demo/03', status='blocked', blocker='no credential')
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite', [live('12/ticket-1')])
        strip = self.snapshot()['strip']
        self.assertIn('demo · dispatch', strip)
        self.assertIn('1 running', strip)
        # A blocker outranks the next step in the closer.
        self.assertIn('no credential', strip)
        self.assertNotIn('next:', strip)

    def test_a_card_confirmed_the_templates_way_is_not_a_draft(self):
        # run-card.md writes `confirmed: no | <date> "<the user's words>"`; a date and words are the confirmation.
        card(self.scratch / 'demo' / 'autopilot.md', phase='dispatch', confirmed='2026-10-27 "Confirmed, go"')
        found = self.snapshot()
        self.assertFalse(found['draft'])
        self.assertNotIn('draft', self.row_texts(found, 'headline')[0])

    def test_a_supervised_worker_is_a_run_row_not_a_wait(self):
        # The yield gate requires an armed wait on every live worker, so monitor.state running cannot mean "waiting".
        card(self.scratch / 'demo' / 'autopilot.md')
        supervised = dict(live('12/ticket-1', assignment='zloop-panel/01'), monitor={'state': 'running'})
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite', [supervised])
        found = self.snapshot()
        self.assertEqual(1, len(self.row_texts(found, 'run')))
        self.assertEqual([], self.row_texts(found, 'wait'))
        self.assertIn('1 running', found['strip'])

    def test_a_result_awaiting_collection_is_the_wait_row(self):
        card(self.scratch / 'demo' / 'autopilot.md')
        # The lifecycle records a collected-looking result and its pending event in one step, so the attempt does
        # carry `result` while the orchestrator has not acknowledged it yet.
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite',
                 [dict(live('12/ticket-1'), monitor={'state': 'settled'}, result={'status': 'completed'})])
        state = self.scratch / 'demo' / 'reports' / 'workers.sqlite'
        db = sqlite3.connect(state)
        db.execute("INSERT INTO events (attempt, kind, payload, acknowledged) VALUES (?, ?, ?, 0)",
                   ('12/ticket-1', 'result', json.dumps({'recorded_at': time.time() - 30, 'status': 'completed'})))
        db.commit()
        db.close()
        found = self.snapshot()
        self.assertEqual([], self.row_texts(found, 'run'))
        self.assertEqual(1, len(self.row_texts(found, 'wait')))

    def test_a_live_worker_keeps_the_panel_up_after_the_card_ended(self):
        card(self.scratch / 'demo' / 'autopilot.md', phase='ended', terminal='delivered')
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite', [live('12/ticket-1')])
        found = self.snapshot()
        self.assertTrue(found['active'])
        self.assertEqual(1, len(self.row_texts(found, 'run')))

    def test_a_blocked_ticket_keeps_the_panel_up_after_the_card_ended(self):
        card(self.scratch / 'demo' / 'autopilot.md', phase='ended', terminal='delivered')
        ticket(self.scratch / 'demo' / 'issues' / '01-a.md', 'demo/01', status='blocked', blocker='no approval')
        found = self.snapshot()
        self.assertTrue(found['active'])
        self.assertIn('no approval', self.row_texts(found, 'blocker')[0])

    def test_the_sessions_journal_names_the_effort_when_no_card_does(self):
        # Journals live in the effort's artifact directory, which is its reports folder.
        journal = self.scratch / 'gamma' / 'reports' / 'decisions-S.jsonl'
        journal.parent.mkdir(parents=True)
        journal.write_text('')
        ticket(journal.parent.parent / 'issues' / '01-a.md', 'gamma/01', status='implementing')
        # A newer effort sits beside it, so only the journal rule can pick gamma.
        newer = self.scratch / 'omega' / 'issues' / '01-a.md'
        ticket(newer, 'omega/01', status='ready')
        later = time.time() + 120
        os.utime(newer, (later, later))
        found = self.snapshot('S')
        self.assertEqual('gamma', found['effort'])
        self.assertIsNone(found['phase'])
        self.assertTrue(found['active'])

    def test_an_effort_nothing_binds_to_this_session_is_not_shown(self):
        # A session just opened has no binding, card or journal of its own, so another session's live work stays off
        # its panel, however recent: the card says `unknown`, as a host that exposes no session ID writes it.
        card(self.scratch / 'demo' / 'autopilot.md', session='unknown')
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite', [live('12/ticket-1')])
        ticket(self.scratch / 'omega' / 'issues' / '01-a.md', 'omega/01', status='ready')
        found = self.snapshot('fresh')
        self.assertEqual({'active': False, 'effort': None, 'phase': None, 'draft': False, 'rows': [], 'strip': None},
                         found)

    def test_a_worktree_checkout_reads_the_main_checkouts_scratch(self):
        card(self.scratch / 'demo' / 'autopilot.md')
        worktree = Path(tempfile.mkdtemp(prefix='panel worktree '))
        self.addCleanup(lambda: subprocess.run(['rm', '-rf', str(worktree)]))
        # The state directory is ignored, so a worktree checkout never carries it.
        (self.repo / '.gitignore').write_text('.scratch/\n')
        subprocess.run(['git', '-C', str(self.repo), 'init', '-q'], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'add', '-A'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.email=a@b', '-c', 'user.name=a',
                        'commit', '-qm', 'fixture'], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'worktree', 'add', '-q', '--detach', str(worktree)],
                       check=True)
        found = PANEL.snapshot(worktree, 'S', time.time())
        self.assertEqual('demo', found['effort'])

    def test_an_allowance_pause_names_the_card_timers_resume(self):
        card(self.scratch / 'demo' / 'autopilot.md', terminal='paused-allowance')
        path = self.scratch / 'demo' / 'autopilot.md'
        path.write_text(path.read_text().replace('timer:', 'timer: 2026-10-27T06:00Z via zloop_wake'))
        self.assertIn('06:00', self.row_texts(self.snapshot(), 'blocker')[0])

    def test_the_coordinated_effort_wins_over_a_newer_unclaimed_one(self):
        card(self.scratch / 'alpha' / 'autopilot.md', session='unknown')
        coordinator(self.scratch / 'alpha', ['S'])
        newer = self.scratch / 'beta' / 'issues' / '01-a.md'
        ticket(newer, 'beta/01', status='ready')
        later = time.time() + 120
        os.utime(newer, (later, later))
        self.assertEqual('alpha', self.snapshot('S')['effort'])

    def test_a_binding_that_is_not_an_object_is_unreadable_not_fatal(self):
        # The malformed binding is skipped, so the card naming this session still picks its effort.
        card(self.scratch / 'alpha' / 'autopilot.md', session='S')
        (self.scratch / 'alpha' / 'reports').mkdir(parents=True)
        (self.scratch / 'alpha' / 'reports' / 'coordinator.json').write_text('[]')
        self.assertEqual('alpha', self.snapshot('S')['effort'])

    def test_two_sessions_each_see_their_own_effort(self):
        for slug, session in (('alpha', 'S'), ('beta', 'T')):
            card(self.scratch / slug / 'autopilot.md', session='unknown')
            coordinator(self.scratch / slug, [session])
        self.assertEqual('alpha', self.snapshot('S')['effort'])
        self.assertEqual('beta', self.snapshot('T')['effort'])


if __name__ == '__main__':
    unittest.main()
