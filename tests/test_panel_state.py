#!/usr/bin/env python3
"""The panel's fact source: which workstream is live, and the rows a host draws for it."""
import contextlib
import json
import shlex
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import support

PANEL = support.load_script('panel_state.py')


def card(path, phase='dispatch', session='S', terminal='', confirmed='yes 2026-10-27 "go"', resume='`autopilot --take-over`',
         board=None):
    """A run card with the State section the panel reads; `board:` only when given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join([
        f'# {path.parent.name} — autopilot run card', '', '## Goal', 'a goal', '',
        '## State', f'confirmed: {confirmed}', f'session: {session}', 'auto-merge: no',
        *([f'board: {board}'] if board else []),
        f'phase: {phase}', f'terminal: {terminal}', 'history:', 'evidence:', 'timer:', f'resume: {resume}', '']))
    return path


def orchestrator(workstream, sessions):
    """The binding `next_tickets.py --orchestrate` writes: the session running the workstream."""
    path = workstream / 'reports' / 'orchestrator.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'pane': 'w1J:p36', 'sessions': sessions, 'autofix': True}))
    return path


def ticket(path, name, status='ready', blocker=None, worker='', title='a behaviour', blocked_by='', accepted=True):
    """A local ticket with the front matter the board reads, titled as the template titles one; a done one carries the
    acceptance fields `--accept` writes, unless `accepted` is False."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ['---', f'ticket: {name}', f'status: {status}', f'blocked_by: [{blocked_by}]', 'touches: [scripts]',
             'devices: []', 'model: standard', f'worker: {worker}'.rstrip()]
    if blocker:
        lines.append(f'blocker: {blocker}')
    if status == 'done' and accepted:
        lines += [f"accepted: {'a' * 40}", f"accepted_report: {'b' * 64}"]
    lines += ['---', f"# {name.split('/')[-1]}: {title}", '', '**Story:** a story.', '']
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
            'owned': True, 'requested': {'model': model}, '_quiet_seconds': quiet, 'session': 'S', 'sessions': ['S']}


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

    def test_a_live_card_is_active_and_names_workstream_and_phase(self):
        card(self.scratch / 'demo' / 'autopilot.md', phase='dispatch')
        found = self.snapshot()
        self.assertTrue(found['active'])
        self.assertEqual('demo', found['workstream'])
        self.assertNotIn('effort', found)
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
        # state alone decides a carded workstream, since a card-less one would stay live on that ticket.
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

    def test_the_rows_more_stands_for_come_with_the_snapshot_in_rank_order(self):
        card(self.scratch / 'demo' / 'autopilot.md')
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite',
                 [live(f'12/ticket-{n}', assignment=f'zloop-panel/0{n}', quiet=60 * n) for n in range(1, 6)])
        found = self.snapshot()
        self.assertEqual([('run', '▶ builder · zloop-panel/04 · sonnet · quiet 4m'),
                          ('run', '▶ builder · zloop-panel/05 · sonnet · quiet 5m')],
                         [(row['kind'], row['text']) for row in found['more']])
        # Nothing hidden, nothing more; a quiet snapshot holds none either.
        (self.scratch / 'demo' / 'reports' / 'workers.sqlite').unlink()
        self.assertEqual([], self.snapshot()['more'])
        self.assertEqual([], self.snapshot('nobody')['more'])

    def test_wait_rows_are_capped_like_run_rows_and_more_holds_every_hidden_row(self):
        # Five running workers and five retained ones: three of each show, and one `+N more` stands for the other
        # four, which come with the snapshot (run rows first, then wait rows), each group in rank order.
        card(self.scratch / 'demo' / 'autopilot.md')
        retained = {'reason': 'r', 'next_use': 'n'}
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite',
                 [live(f'12/run-{n}', assignment=f'zloop-panel/run{n}', quiet=60 * n) for n in range(1, 6)]
                 + [dict(live(f'12/kept-{n}', assignment=f'zloop-panel/kept{n}', quiet=3600 * n),
                         result={'status': 'completed'}, retained=retained) for n in range(1, 6)])
        found = self.snapshot()
        self.assertEqual(['+4 more'], self.row_texts(found, 'more'))
        self.assertEqual(3, len(self.row_texts(found, 'run')))
        self.assertEqual(['retained · zloop-panel/kept1 · quiet 1h', 'retained · zloop-panel/kept2 · quiet 2h',
                          'retained · zloop-panel/kept3 · quiet 3h'], self.row_texts(found, 'wait'))
        self.assertEqual([('run', '▶ builder · zloop-panel/run4 · sonnet · quiet 4m'),
                          ('run', '▶ builder · zloop-panel/run5 · sonnet · quiet 5m'),
                          ('wait', 'retained · zloop-panel/kept4 · quiet 4h'),
                          ('wait', 'retained · zloop-panel/kept5 · quiet 5h')],
                         [(row['kind'], row['text']) for row in found['more']])

    def test_a_blocker_reason_holding_dashes_is_shown_whole(self):
        # Rule: the front matter ends at the closing `---` line only, so `---` inside a reason cuts nothing off it.
        card(self.scratch / 'demo' / 'autopilot.md')
        ticket(self.scratch / 'demo' / 'issues' / '01-a.md', 'demo/01', status='blocked', blocker='stuck --- see report')
        self.assertEqual(['blocked: demo/01 · stuck --- see report'], self.row_texts(self.snapshot(), 'blocker'))

    def test_a_ticket_with_a_block_list_still_shows_the_fields_after_it(self):
        # Rule: `- item` lines under a key belong to that key and hide none of the fields that follow.
        card(self.scratch / 'demo' / 'autopilot.md')
        path = self.scratch / 'demo' / 'issues' / '01-a.md'
        path.parent.mkdir(parents=True)
        path.write_text('---\nticket: demo/01\nblocked_by: []\ntouches: []\ndevices: []\nneeds:\n  - network\n'
                        'status: blocked\nblocker: no approval\n---\n# 01\n')
        self.assertEqual(['blocked: demo/01 · no approval'], self.row_texts(self.snapshot(), 'blocker'))

    def test_one_blocker_row_by_precedence(self):
        card(self.scratch / 'demo' / 'autopilot.md', terminal='paused-by-user')
        ticket(self.scratch / 'demo' / 'issues' / '01-a.md', 'demo/01', status='blocked', blocker='no approval')
        found = self.snapshot()
        blockers = self.row_texts(found, 'blocker')
        self.assertEqual(1, len(blockers))
        self.assertIn('paused-by-user', blockers[0])
        self.assertNotIn('no approval', blockers[0])

    def test_a_scheduler_setting_the_panel_does_not_use_cannot_silence_it(self):
        # Review (Sonnet): the panel imports the scheduler, which exited at import on a MAX_LIVE it refuses, so the panel
        # printed nothing; the scheduler still refuses it where it schedules.
        card(self.scratch / 'demo' / 'autopilot.md')
        ticket(self.scratch / 'demo' / 'issues' / '01-a.md', 'demo/01')
        for value in ('0', 'abc'):
            with self.subTest(value=value):
                env = dict(os.environ, MAX_LIVE=value)
                result = subprocess.run([sys.executable, '-B', str(support.script('panel_state.py')), '--repo',
                                         str(self.repo), '--session-id', 'S'], capture_output=True, text=True, env=env)
                self.assertEqual((result.returncode, result.stdout), (0, 'demo · dispatch\nnext: demo/01\n'
                                                                      '1 ticket · 0 done · 0 blocked · 1 ready · 0 live\n'))
                refused = subprocess.run([sys.executable, '-B', str(support.script('next_tickets.py')), '--repo',
                                          str(self.repo), 'demo'], capture_output=True, text=True, env=env)
                self.assertEqual((refused.returncode, refused.stderr.strip().splitlines()[-1]),
                                 (2, f'next_tickets.py: error: MAX_LIVE must be a positive integer, got {value!r}'))
        # A valid one still caps what the scheduler starts.
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        second = ticket(self.scratch / 'demo' / 'issues' / '02-b.md', 'demo/02')
        second.write_text(second.read_text().replace('touches: [scripts]', 'touches: [docs]'))
        started = subprocess.run([sys.executable, '-B', str(support.script('next_tickets.py')), '--repo', str(self.repo),
                                  'demo'], capture_output=True, text=True, env=dict(os.environ, MAX_LIVE='1'))
        self.assertEqual([found['ticket'] for found in json.loads(started.stdout)], ['demo/01'])

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

    def test_the_workstream_naming_this_session_wins(self):
        card(self.scratch / 'alpha' / 'autopilot.md', session='S')
        newer = card(self.scratch / 'beta' / 'autopilot.md', session='other')
        later = time.time() + 60
        os.utime(newer, (later, later))
        self.assertEqual('alpha', self.snapshot('S')['workstream'])
        self.assertEqual('beta', self.snapshot('other')['workstream'])

    def test_a_card_naming_several_sessions_is_each_ones(self):
        # Rule (card-confirm): `confirm` names each host session the shell exports, so a host started from another
        # host's shell finds its run by its own session.
        card(self.scratch / 'beta' / 'autopilot.md', session='outer, S')
        self.assertEqual(('beta', True), (self.snapshot('S')['workstream'], self.snapshot('S')['active']))
        self.assertEqual('beta', self.snapshot('outer')['workstream'])
        self.assertFalse(self.snapshot('outer, S')['active'])

    def test_a_workstream_claimed_by_another_session_is_not_shown(self):
        card(self.scratch / 'beta' / 'autopilot.md', session='other')
        self.assertFalse(self.snapshot('S')['active'])

    def test_counts_come_from_the_workstreams_tickets(self):
        card(self.scratch / 'demo' / 'autopilot.md')
        ticket(self.scratch / 'demo' / 'issues' / '01-a.md', 'demo/01', status='done')
        ticket(self.scratch / 'demo' / 'issues' / '02-b.md', 'demo/02', status='ready')
        ticket(self.scratch / 'demo' / 'issues' / '03-c.md', 'demo/03', status='implementing')
        self.assertEqual(['3 tickets · 1 done · 0 blocked · 1 ready · 1 live'], self.row_texts(self.snapshot(), 'counts'))

    def test_the_counts_give_the_total_and_an_inline_ticket_in_progress_is_named(self):
        # Live (jose, Pi): an inline run showed neither how many tickets there are nor which one it was working on; an
        # inline ticket has no worker record to give it a row, so its own front matter does. Every status the board
        # counts as worked on is live; a stray note in the folder is no ticket.
        card(self.scratch / 'demo' / 'autopilot.md')
        issues = self.scratch / 'demo' / 'issues'
        ticket(issues / '01-a.md', 'demo/01', status='done', worker='inline')
        ticket(issues / '02-b.md', 'demo/02', status='implementing', worker='inline', title='Oracle client rollout')
        ticket(issues / '03-c.md', 'demo/03', status='ready')
        ticket(issues / '04-d.md', 'demo/04', status='fixing', worker='inline', title='WARP cadence')
        ticket(issues / '05-e.md', 'demo/05', status='implementing', worker='demo/05')
        ticket(issues / '06-f.md', 'demo/06', status='verifying', worker='inline', title='Smoke the rollout')
        ticket(issues / '07-g.md', 'demo/07', status='reviewing', worker='demo/07')
        (issues / 'README.md').write_text('# Notes on these tickets\n')
        self.assertEqual([row['text'] for row in self.snapshot()['rows']], [
            'demo · dispatch', '▶ inline · demo/02 · Oracle client rollout', '▶ inline · demo/04 · WARP cadence',
            '▶ inline · demo/06 · Smoke the rollout', 'next: demo/03', '7 tickets · 1 done · 0 blocked · 1 ready · 5 live'])

    def test_an_inline_ticket_keeps_its_title_and_its_run_shows_while_it_is_reviewed(self):
        # Review (Codex, Sonnet): a `---` in a ticket's body lost its title; and a run bound by dispatch alone, with
        # no card, went quiet once its inline ticket left implementing.
        workstream = self.scratch / 'demo'
        orchestrator(workstream, ['S'])
        path = ticket(workstream / 'issues' / '01-a.md', 'demo/01', status='reviewing', worker='inline',
                      title='Keep the client online')
        path.write_text(path.read_text() + '---\n```sh\n# run the suite\n```\n')
        found = self.snapshot()
        self.assertEqual((found['active'], [row['text'] for row in found['rows']][1]),
                         (True, '▶ inline · demo/01 · Keep the client online'))

    def test_the_panels_live_statuses_are_the_boards(self):
        # Review (Sonnet): the panel keeps its own list of the statuses next_tickets.py counts as live.
        self.assertEqual(set(PANEL.LIVE_TICKET), support.load_script('next_tickets.py', 'next_tickets_live').LIVE)

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

    def test_the_next_row_names_only_a_ready_ticket_whose_blockers_are_done(self):
        # Live (jose, Pi): the panel showed `next: maven-warp-closeout/08`, whose blockers included two tickets parked as
        # blocked and a standing one, so the scheduler could never start it. Next is a ticket the board's blockers allow.
        card(self.scratch / 'demo' / 'autopilot.md')
        issues = self.scratch / 'demo' / 'issues'
        ticket(issues / '01-a.md', 'demo/01', status='blocked', blocker='finished; no PR the board can check')
        ticket(issues / '02-b.md', 'demo/02', status='implementing', worker='inline')
        ticket(issues / '03-c.md', 'demo/03', status='done')
        ticket(issues / '04-d.md', 'demo/04', blocked_by='demo/01, demo/02, demo/03')
        # Review (Codex): another workstream's ready ticket is not this one's next.
        ticket(self.scratch / 'alpha' / 'issues' / '01-a.md', 'alpha/01')
        self.assertEqual([], self.row_texts(self.snapshot(), 'next'))
        ticket(issues / '05-e.md', 'demo/05', blocked_by='demo/03')
        self.assertEqual(['next: demo/05'], self.row_texts(self.snapshot(), 'next'))
        ticket(issues / '02-b.md', 'demo/02', status='done')  # A blocker parked as blocked is still not done.
        self.assertEqual(['next: demo/05'], self.row_texts(self.snapshot(), 'next'))
        ticket(issues / '01-a.md', 'demo/01', status='done')
        self.assertEqual(['next: demo/04'], self.row_texts(self.snapshot(), 'next'))
        # Another workstream's done ticket blocks until the board counts it integrated: its workstream delivered.
        ticket(self.scratch / 'other' / 'issues' / '01-a.md', 'other/01', status='done')
        for path in (issues / '04-d.md', issues / '05-e.md'):
            path.unlink()
        ticket(issues / '06-f.md', 'demo/06', blocked_by='other/01')
        self.assertEqual([], self.row_texts(self.snapshot(), 'next'))

    def test_a_board_that_fails_its_check_is_the_workstreams_blocker(self):
        # Live (jose, Pi): T4 was marked done by hand, so `--check` refused the board and the scheduler started nothing,
        # while the panel counted it done, named a next ticket and showed no sign of the refusal.
        workstream = self.scratch / 'demo'
        card(workstream / 'autopilot.md')
        issues = workstream / 'issues'
        ticket(issues / '01-a.md', 'demo/01', status='done', accepted=False)
        ticket(issues / '02-b.md', 'demo/02')
        refusal = ('demo/01: status done with no acceptance; accept it with --accept demo/01, or --accept demo/01 --pr '
                   '<url> when PRs delivered it, or --accept demo/01 --run <url> when a successful CI run proves it')
        self.assertEqual([row['text'] for row in self.snapshot()['rows']], [
            'demo · dispatch', f'board fails its check: {refusal}', '2 tickets · 1 done · 0 blocked · 1 ready · 0 live'])
        # This workstream's problem comes first, then how many more the board has.
        ticket(self.scratch / 'alpha' / 'issues' / '01-a.md', 'alpha/01', status='done', accepted=False)
        self.assertEqual([f'board fails its check: {refusal} (+1 more)'], self.row_texts(self.snapshot(), 'blocker'))
        # Another workstream's problem is this one's blocker while it has work left, since the scheduler starts none.
        ticket(issues / '01-a.md', 'demo/01', status='done')
        other = ('alpha/01: status done with no acceptance; accept it with --accept alpha/01, or --accept alpha/01 --pr '
                 '<url> when PRs delivered it, or --accept alpha/01 --run <url> when a successful CI run proves it')
        self.assertEqual([f'board fails its check: {other}'], self.row_texts(self.snapshot(), 'blocker'))
        # A ticket the board blocks comes after it.
        ticket(issues / '03-c.md', 'demo/03', status='blocked', blocker='no credential')
        self.assertEqual([f'board fails its check: {other}'], self.row_texts(self.snapshot(), 'blocker'))
        # A stop the card records comes first.
        card(workstream / 'autopilot.md', terminal='paused-by-user')
        self.assertEqual(['paused-by-user: you took control'], self.row_texts(self.snapshot(), 'blocker'))
        # A run that ended with all its tickets done is not kept on screen by another workstream's problem.
        (issues / '03-c.md').unlink()
        ticket(issues / '02-b.md', 'demo/02', status='done')
        card(workstream / 'autopilot.md', terminal='merged')
        self.assertFalse(self.snapshot()['active'])
        # Review (Codex): a problem is this workstream's wherever one of its tickets appears in it, as in a cycle, and
        # another workstream whose name ends in this one's is not this one.
        ticket(issues / '02-b.md', 'demo/02', status='done', blocked_by='demo/02')
        self.assertEqual(['board fails its check: ticket dependency cycle: demo/02 -> demo/02 (+1 more)'],
                         self.row_texts(self.snapshot(), 'blocker'))
        ticket(issues / '02-b.md', 'demo/02', status='done')
        ticket(self.scratch / 'alpha' / 'issues' / '01-a.md', 'alpha/01', status='done')
        ticket(self.scratch / 'warp-demo' / 'issues' / '01-a.md', 'warp-demo/01', status='done', accepted=False)
        self.assertFalse(self.snapshot()['active'])
        # Review (Sonnet): another workstream's undone ticket gives this finished one no work left.
        ticket(self.scratch / 'warp-demo' / 'issues' / '02-b.md', 'warp-demo/02')
        self.assertFalse(self.snapshot()['active'])
        # Review (Sonnet): a cycle between workstreams lists them by name, and this one in it makes it its own.
        ticket(issues / '02-b.md', 'demo/02', status='done', blocked_by='cyc/01')
        ticket(self.scratch / 'cyc' / 'issues' / '01-a.md', 'cyc/01', status='done')
        ticket(self.scratch / 'cyc' / 'issues' / '02-b.md', 'cyc/02', status='done', blocked_by='demo/01')
        blocker = self.row_texts(self.snapshot(), 'blocker')
        self.assertEqual(len(blocker), 1)
        self.assertRegex(blocker[0], r'^board fails its check: workstream dependency cycle: '
                                     r'(cyc -> demo -> cyc|demo -> cyc -> demo) \(\+1 more\)$')

    def test_a_repository_path_that_holds_the_workstreams_name_does_not_make_anothers_refusal_its_own(self):
        # Review (Sonnet): another workstream's refused ticket is named by its absolute path, and a checkout whose
        # folder is called like the workstream made that refusal look like this workstream's, keeping it on screen.
        repo = self.repo / 'demo'
        scratch = repo / '.scratch'
        card(scratch / 'demo' / 'autopilot.md', terminal='merged')
        ticket(scratch / 'demo' / 'issues' / '01-a.md', 'demo/01', status='done')
        refused = ticket(scratch / 'other' / 'issues' / '01-a.md', 'other/one')
        self.assertFalse(PANEL.snapshot(repo, 'S', time.time())['active'])
        ticket(refused, 'other/01')
        ticket(scratch / 'demo' / 'issues' / '02-b.md', 'demo/two', status='done')  # Its own refused ticket is its own.
        self.assertEqual(self.row_texts(PANEL.snapshot(repo, 'S', time.time()), 'blocker'), [
            f"board fails its check: {scratch / 'demo' / 'issues' / '02-b.md'}: invalid ticket id 'demo/two'"])
        # Review (Sonnet): so it is under a relative repository path.
        with contextlib.chdir(repo):
            self.assertEqual(self.row_texts(PANEL.snapshot(Path('.'), 'S', time.time()), 'blocker'), [
                "board fails its check: .scratch/demo/issues/02-b.md: invalid ticket id 'demo/two'"])
        # Review (Codex): and under a checkout whose path holds `: `, the separator of a refusal's path from its reason.
        odd = self.repo / 'a: copy'
        card(odd / '.scratch' / 'demo' / 'autopilot.md', terminal='merged')
        refused = ticket(odd / '.scratch' / 'demo' / 'issues' / '01-a.md', 'demo/one', status='done')
        self.assertEqual(self.row_texts(PANEL.snapshot(odd, 'S', time.time()), 'blocker'), [
            f"board fails its check: {refused}: invalid ticket id 'demo/one'"])

    def test_another_workstreams_refused_file_hides_none_of_this_workstreams_problems(self):
        # Review (Codex): the scheduler refuses the whole board on one file, so another workstream's refused file left
        # this one's board unchecked: its merged card's unaccepted ticket showed nothing.
        card(self.scratch / 'demo' / 'autopilot.md', terminal='merged')
        ticket(self.scratch / 'demo' / 'issues' / '01-a.md', 'demo/01', status='done', accepted=False)
        ticket(self.scratch / 'alpha' / 'issues' / '01-a.md', 'alpha/one')
        refusal = ('demo/01: status done with no acceptance; accept it with --accept demo/01, or --accept demo/01 --pr '
                   '<url> when PRs delivered it, or --accept demo/01 --run <url> when a successful CI run proves it')
        self.assertEqual(self.row_texts(self.snapshot(), 'blocker'), [f'board fails its check: {refusal} (+1 more)'])
        # A ticket of it waiting on one in that workstream waits on a ticket the board cannot judge, not one missing.
        ticket(self.scratch / 'demo' / 'issues' / '01-a.md', 'demo/01', status='done', blocked_by='alpha/02')
        (self.scratch / 'alpha' / 'issues' / '05-b.md').write_text(  # Named by the ticket it holds, not its number.
            '---\nticket: alpha/02\nstatus: done\nblocked_by: []\ntouches: invalid\ndevices: []\n---\n')
        self.assertFalse(self.snapshot()['active'])

    def test_a_ticket_file_the_panel_cannot_read_leaves_it_its_rows(self):
        # Review (Codex): the board's check names a ticket file it cannot read, but the panel read the file again itself
        # and the PermissionError erased the whole panel.
        card(self.scratch / 'demo' / 'autopilot.md')
        issues = self.scratch / 'demo' / 'issues'
        ticket(issues / '01-a.md', 'demo/01')
        unreadable = ticket(issues / '02-b.md', 'demo/02')
        unreadable.chmod(0)
        self.addCleanup(unreadable.chmod, 0o644)
        self.assertEqual([row['text'] for row in self.snapshot()['rows']], [
            'demo · dispatch', f"board fails its check: {unreadable}: cannot be read: [Errno 13] Permission denied: "
                               f"'{unreadable}'", '1 ticket · 0 done · 0 blocked · 1 ready · 0 live'])

    def test_a_ticket_whose_fields_are_written_as_lists_leaves_the_panel_its_rows(self):
        # Review (Codex): the panel compared and joined a ticket's fields as text, so this workstream's status or ticket
        # id written as a list, which the check names, raised out of the snapshot and erased the whole panel.
        card(self.scratch / 'demo' / 'autopilot.md')
        issues = self.scratch / 'demo' / 'issues'
        ticket(issues / '01-a.md', 'demo/01', status='[done]')
        statuses = "['blocked', 'done', 'fixing', 'implementing', 'ready', 'reviewing', 'verifying']"
        self.assertEqual([row['text'] for row in self.snapshot()['rows']], [
            'demo · dispatch', f"board fails its check: demo/01: status ['done'] is not one of {statuses}",
            '1 ticket · 0 done · 0 blocked · 0 ready · 0 live'])
        listed = ticket(issues / '01-a.md', '[demo/01]', status='implementing', worker='inline')
        self.assertEqual([row['text'] for row in self.snapshot()['rows']], [
            'demo · dispatch', "▶ inline · ['demo/01'] · a behaviour",
            f"board fails its check: {listed}: invalid ticket id ['demo/01']", '1 ticket · 0 done · 0 blocked · 0 ready · 1 live'])

    def test_a_run_that_records_a_delivered_state_its_workstream_has_not_reached_stays_on_screen(self):
        # User (2026-11-07, "Go ahead"): a merged card whose board passed its check but still held a ready ticket showed no
        # panel, while the stop check held its session; the panel names the first thing the stop check names.
        workstream = self.scratch / 'demo'
        issues = workstream / 'issues'
        review = workstream / 'reports' / 'spec-review.md'
        # Review (Codex): with no tickets at all, the panel names what the stop check names.
        card(workstream / 'autopilot.md', phase='pr', terminal='merged', board='local')
        new = shlex.join(['python3', str(PANEL.next_tickets.__file__), '--repo', str(self.repo), 'demo', '--new', '<title>'])
        self.assertEqual(self.row_texts(self.snapshot(), 'blocker'),
                         [f'not delivered: its local board has no tickets: write each with `{new}`'])
        ticket(issues / '01-a.md', 'demo/01', status='done')
        ticket(issues / '02-b.md', 'demo/02')
        for terminal in ('delivered', 'delivered-local', 'merged'):
            with self.subTest(terminal=terminal):
                card(workstream / 'autopilot.md', phase='pr', terminal=terminal, board='local')
                self.assertEqual([row['text'] for row in self.snapshot()['rows']], [
                    'demo · pr', 'not delivered: tickets not done: demo/02 (ready) (+1 more)', 'next: demo/02',
                    '2 tickets · 1 done · 0 blocked · 1 ready · 0 live'])
        # Its tickets done, what is left is the delivery evidence.
        ticket(issues / '02-b.md', 'demo/02', status='done')
        self.assertEqual(self.row_texts(self.snapshot(), 'blocker'), [f'not delivered: no review report at {review}'])
        # It comes before a ticket the board blocks, one of the tickets not done; a run still going shows that ticket.
        ticket(issues / '02-b.md', 'demo/02', status='blocked', blocker='no credential')
        self.assertEqual(self.row_texts(self.snapshot(), 'blocker'),
                         ['not delivered: tickets not done: demo/02 (blocked) (+1 more)'])
        card(workstream / 'autopilot.md', phase='dispatch', board='local')
        self.assertEqual(self.row_texts(self.snapshot(), 'blocker'), ['blocked: demo/02 · no credential'])
        # The board's failed check comes first: this workstream's own problem is named as the check names it.
        card(workstream / 'autopilot.md', phase='pr', terminal='merged', board='local')
        ticket(issues / '02-b.md', 'demo/02', status='done', accepted=False)
        self.assertEqual(self.row_texts(self.snapshot(), 'blocker'), [
            'board fails its check: demo/02: status done with no acceptance; accept it with --accept demo/02, or '
            '--accept demo/02 --pr <url> when PRs delivered it, or --accept demo/02 --run <url> when a successful CI run '
            'proves it'])
        # Review (Sonnet): what the delivery check cannot read is said, as the stop check says it, and the rows stay.
        ticket(issues / '02-b.md', 'demo/02', status='done')
        review.parent.mkdir(parents=True, exist_ok=True)
        review.write_text('---\nreview_status: clean\ndocumentation_status: passed\nreview_by: local\n---\n')
        review.with_name('review.json').write_text('{broken')
        blocker = self.row_texts(self.snapshot(), 'blocker')
        self.assertEqual(len(blocker), 1)
        self.assertTrue(blocker[0].startswith('not delivered: it cannot be checked: '), blocker)
        review.with_name('review.json').unlink()
        review.unlink()
        # Review (Codex): only a confirmed card naming this session is held to it, as the stop check holds it.
        card(workstream / 'autopilot.md', phase='pr', terminal='merged', board='local', session='T')
        orchestrator(workstream, ['S'])
        self.assertEqual(self.row_texts(self.snapshot(), 'blocker'), [])
        card(workstream / 'autopilot.md', phase='pr', terminal='merged', board='local', confirmed='no')
        self.assertEqual(self.row_texts(self.snapshot(), 'blocker'), [])
        (workstream / 'reports' / 'orchestrator.json').unlink()
        # A tracker's board is the tracker's to judge, and a run that ended in another state is not held to it.
        ticket(issues / '02-b.md', 'demo/02', status='done')
        card(workstream / 'autopilot.md', phase='pr', terminal='merged', board='https://linear.app/team/project/x')
        self.assertFalse(self.snapshot()['active'])
        card(workstream / 'autopilot.md', phase='pr', terminal='blocked', board='local')
        self.assertEqual(self.row_texts(self.snapshot(), 'blocker'), ['blocked: no option fits the principles'])

    def test_a_board_the_scheduler_cannot_read_still_leaves_the_panel_its_rows(self):
        # Review (Codex): a status written as a list on another workstream's ticket, or a prerequisite workstream whose
        # worker state lost its tables, raised out of the snapshot and erased the whole panel.
        card(self.scratch / 'demo' / 'autopilot.md')
        issues = self.scratch / 'demo' / 'issues'
        ticket(issues / '01-a.md', 'demo/01', blocked_by='base/01')
        ticket(self.scratch / 'base' / 'issues' / '01-a.md', 'base/01', status='[done]')
        statuses = "['blocked', 'done', 'fixing', 'implementing', 'ready', 'reviewing', 'verifying']"
        self.assertEqual([row['text'] for row in self.snapshot()['rows']], [
            'demo · dispatch', f"board fails its check: base/01: status ['done'] is not one of {statuses}",
            '1 ticket · 0 done · 0 blocked · 1 ready · 0 live'])
        ticket(self.scratch / 'base' / 'issues' / '01-a.md', 'base/01', status='done')
        reports = self.scratch / 'base' / 'reports'
        reports.mkdir(parents=True)
        (reports / 'spec-review.md').write_text('---\nreview_status: clean\ndocumentation_status: passed\n'
                                                'e2e_status: passed\nreview_by: review/base/1\ne2e_by: e2e/base/1\n---\n')
        sqlite3.connect(reports / 'workers.sqlite').close()  # A state file with no tables.
        self.assertEqual([row['text'] for row in self.snapshot()['rows']], [
            'demo · dispatch', 'next: unknown, the board cannot tell: OperationalError: no such table: attempts',
            '1 ticket · 0 done · 0 blocked · 1 ready · 0 live'])
        # Review (Sonnet): whatever the scheduler raises on the way, the panel says it and keeps its rows: a recorded
        # attempt that is no record, a ticket id written as a list, a ticket the board refuses outright.
        db = sqlite3.connect(reports / 'workers.sqlite')
        db.execute('CREATE TABLE attempts (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        db.execute("INSERT INTO attempts VALUES ('review/base/1', 'null')")
        db.commit()
        db.close()
        self.assertEqual(self.row_texts(self.snapshot(), 'next'), [
            "next: unknown, the board cannot tell: AttributeError: 'NoneType' object has no attribute 'get'"])
        ticket(issues / '01-a.md', 'demo/01')  # Waiting on nothing: another workstream's refusal is what it shows.
        listed = ticket(self.scratch / 'base' / 'issues' / '01-a.md', '[base/01]', status='done')
        self.assertEqual(self.row_texts(self.snapshot(), 'blocker'), [
            f"board fails its check: {listed}: invalid ticket id ['base/01']"])
        ticket(listed, 'base/one', status='done')
        self.assertEqual(self.row_texts(self.snapshot(), 'blocker'), [
            f"board fails its check: {listed}: invalid ticket id 'base/one'"])
        listed.write_bytes(b'\xff---\n')
        self.assertEqual(self.row_texts(self.snapshot(), 'blocker'), [
            f"board fails its check: {listed}: cannot be read: 'utf-8' codec can't decode byte 0xff in position 0: "
            "invalid start byte"])

    def test_the_next_row_asks_about_another_workstreams_blocker_once_however_many_tickets_wait_on_it(self):
        # Review (Codex): the panel refreshes every two seconds, and each ready ticket behind the same other workstream's
        # blocker asked git about that workstream again: 64 such tickets took ten seconds, past the hosts' read timeout.
        card(self.scratch / 'demo' / 'autopilot.md')
        ticket(self.scratch / 'base' / 'issues' / '01-a.md', 'base/01', status='done')
        reports = self.scratch / 'base' / 'reports'
        reports.mkdir(parents=True)
        (reports / 'spec-review.md').write_text('---\nworkstream_branch: feat/base\nreview_status: findings\n---\n')

        def git_calls(waiting):
            for n in range(1, waiting + 1):
                ticket(self.scratch / 'demo' / 'issues' / f'{n:02}-t.md', f'demo/{n:02}', blocked_by='base/01')
            with patch('subprocess.run', wraps=subprocess.run) as run:  # git is the process boundary.
                self.assertEqual([], self.row_texts(self.snapshot(), 'next'))
            return sum(1 for call in run.call_args_list if call.args and call.args[0][0] == 'git')
        few = git_calls(2)
        self.assertGreater(few, 0)
        self.assertEqual(few, git_calls(12))

    def test_the_strip_names_the_workstream_its_state_and_its_next_step(self):
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

    def test_a_worker_whose_wait_ended_unconsumed_is_not_shown_running(self):
        # Nothing watches it any more: its wait's receipt waits for the orchestrator, who never collected it (the
        # stforestkit review-pr920 workers read `▶ … quiet 4d` with their panes long gone).
        card(self.scratch / 'demo' / 'autopilot.md')
        state = attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite',
                         [dict(live('12/ticket-1', assignment='zloop-panel/01'), monitor={'state': 'finished'})])
        db = sqlite3.connect(state)
        db.execute("INSERT INTO events (attempt, kind, payload, acknowledged) VALUES (?, ?, ?, 0)",
                   ('12/ticket-1', 'wait-finished', json.dumps({'recorded_at': time.time() - 4 * 86400,
                                                                'reason': 'settled'})))
        db.commit()
        db.close()
        found = self.snapshot()
        self.assertEqual([], self.row_texts(found, 'run'))
        self.assertEqual(['wait ended · zloop-panel/01 · quiet 4d'], self.row_texts(found, 'wait'))
        self.assertNotIn('running', found['strip'])
        # Once the orchestrator consumes the receipt and arms the next wait, the worker runs again.
        db = sqlite3.connect(state)
        db.execute("UPDATE events SET acknowledged = 1 WHERE kind = 'wait-finished'")
        db.commit()
        db.close()
        self.assertEqual(['▶ builder · zloop-panel/01 · sonnet · quiet 4d'], self.row_texts(self.snapshot(), 'run'))

    def test_a_retained_unclosed_attempt_is_a_retained_wait_row(self):
        # A retained attempt has its result collected and stays open for its next use: it is neither running nor done.
        card(self.scratch / 'demo' / 'autopilot.md')
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite',
                 [dict(live('12/ticket-1', assignment='zloop-panel/01', quiet=2 * 3600), monitor={'state': 'settled'},
                       result={'status': 'completed'}, retained={'reason': 'review', 'next_use': 'fix round'})])
        found = self.snapshot()
        self.assertEqual([], self.row_texts(found, 'run'))
        self.assertEqual(['retained · zloop-panel/01 · quiet 2h'], self.row_texts(found, 'wait'))

    def test_a_closed_retained_attempt_shows_no_row(self):
        card(self.scratch / 'demo' / 'autopilot.md')
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite',
                 [dict(live('12/ticket-1'), result={'status': 'completed'}, retained={'reason': 'r', 'next_use': 'n'},
                       closed={'at': 1})])
        found = self.snapshot()
        self.assertEqual([], self.row_texts(found, 'run'))
        self.assertEqual([], self.row_texts(found, 'wait'))

    def test_an_attempt_whose_conversation_was_closed_through_another_attempt_shows_no_row(self):
        # Retirement closes the shared conversation through its final attempt; the earlier retained attempts of that
        # conversation (same runtime and native session) are closed with it, while another conversation's stays.
        card(self.scratch / 'demo' / 'autopilot.md')
        retained = {'reason': 'r', 'next_use': 'n'}
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite',
                 [dict(live('12/ticket-1', assignment='zloop-panel/01'), native_session='shared',
                       result={'status': 'completed'}, retained=retained),
                  dict(live('12/ticket-2', assignment='zloop-panel/01'), native_session='shared',
                       result={'status': 'completed'}, closed={'at': 1}),
                  dict(live('12/ticket-3', assignment='zloop-panel/02'), native_session='other',
                       result={'status': 'completed'}, retained=retained),
                  dict(live('12/ticket-4', assignment='zloop-panel/03'), native_session='shared', runtime='codex',
                       result={'status': 'completed'}, retained=retained)])
        self.assertEqual(['retained · zloop-panel/02 · quiet 30s', 'retained · zloop-panel/03 · quiet 30s'],
                         sorted(self.row_texts(self.snapshot(), 'wait')))

    def test_a_retained_attempt_whose_conversation_has_a_later_attempt_shows_no_row(self):
        # Live (stforestkit, remediate-taut-534): audit-4 and audit-5 were retained, and the same Codex conversation
        # then ran audit-6, the retention's next use; the panel listed both as retained beside the running worker.
        card(self.scratch / 'demo' / 'autopilot.md')
        retained = {'reason': 'r', 'next_use': 'n'}
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite',
                 [dict(live('12/ticket-1', quiet=3600), native_session='shared', monitor={'state': 'reconciled'},
                       result={'status': 'blocked'}, retained=retained),
                  dict(live('12/ticket-2', quiet=600), native_session='shared', monitor={'state': 'reconciled'},
                       result={'status': 'blocked'}, retained=retained),
                  dict(live('12/ticket-3'), native_session='shared', monitor={'state': 'running'})])
        found = self.snapshot()
        self.assertEqual((['▶ builder · zloop-panel/01 · sonnet · quiet 30s'], []),
                         (self.row_texts(found, 'run'), self.row_texts(found, 'wait')))

    def test_a_retention_stands_until_its_own_conversation_runs_again_and_a_pending_result_always_shows(self):
        # A later attempt of another conversation (another session, or the same session ID under another runtime), even
        # on the same assignment, is no use of this retention; a superseded attempt whose result is still
        # unacknowledged is the orchestrator's to collect.
        card(self.scratch / 'demo' / 'autopilot.md')
        retained = {'reason': 'r', 'next_use': 'n'}
        state = attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite', [
            dict(live('12/ticket-1', assignment='zloop-panel/01'), native_session='shared', result={'status': 'blocked'},
                 retained=retained),
            dict(live('12/ticket-2', assignment='zloop-panel/01'), native_session='shared', runtime='codex',
                 monitor={'state': 'running'}),
            dict(live('12/ticket-3', assignment='zloop-panel/01'), native_session='other', monitor={'state': 'running'}),
            dict(live('12/ticket-4', assignment='zloop-panel/04'), native_session='pending',
                 result={'status': 'completed'}, retained=retained),
            dict(live('12/ticket-5', assignment='zloop-panel/05'), native_session='pending',
                 monitor={'state': 'running'})])
        db = sqlite3.connect(state)
        db.execute("INSERT INTO events (attempt, kind, payload, acknowledged) VALUES (?, ?, ?, 0)",
                   ('12/ticket-4', 'result', json.dumps({'recorded_at': time.time() - 30, 'status': 'completed'})))
        db.commit()
        db.close()
        self.assertEqual(['result ready · zloop-panel/04 · quiet 30s', 'retained · zloop-panel/01 · quiet 30s'],
                         sorted(self.row_texts(self.snapshot(), 'wait')))

    def test_the_latest_attempt_of_a_conversation_is_the_last_registered_one(self):
        # Review (Codex, Sonnet): neither the name order (ticket-9 after ticket-10), nor the order of the stored rows
        # (the lifecycle rewrites a row when it saves it), nor the newest event, nor the clock of the register events
        # decides which attempt is the latest: the order the lifecycle recorded them in does.
        card(self.scratch / 'demo' / 'autopilot.md')
        retained = {'reason': 'r', 'next_use': 'n'}
        earlier = dict(live('12/ticket-9', quiet=5), native_session='shared', result={'status': 'blocked'},
                       retained=retained)
        state = attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite', [
            dict(earlier),
            dict(live('12/ticket-10'), native_session='shared', result={'status': 'blocked'}, retained=retained)])
        earlier.pop('_quiet_seconds')
        db = sqlite3.connect(state)
        db.execute("DELETE FROM attempts WHERE id = '12/ticket-9'")
        db.execute('INSERT INTO attempts VALUES (?, ?)', ('12/ticket-9', json.dumps(dict(earlier, accepted=True))))
        db.execute("INSERT INTO events (attempt, kind, payload, acknowledged) VALUES (?, ?, ?, 1)",
                   ('12/ticket-9', 'accepted', json.dumps({'recorded_at': time.time() - 10})))
        db.commit()
        db.close()
        self.assertEqual(['retained · zloop-panel/01 · quiet 30s'], self.row_texts(self.snapshot(), 'wait'))

    def test_a_malformed_or_unregistered_row_neither_hides_a_retention_nor_clears_the_panel(self):
        # Review (Codex, Sonnet): another session's row whose attempt is no name must not fail the snapshot, and a
        # retained row with no register event (partial state) keeps its row.
        card(self.scratch / 'demo' / 'autopilot.md')
        retained = {'reason': 'r', 'next_use': 'n'}
        state = attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite', [
            dict(live('12/ticket-1', assignment='zloop-panel/01'), result={'status': 'blocked'}, retained=retained)])
        db = sqlite3.connect(state)
        for row in (dict(live('12/ticket-2'), attempt=[], session='T', sessions=['T']),
                    dict(live('12/ticket-3', assignment='zloop-panel/03'), result={'status': 'blocked'},
                         retained=retained)):
            row.pop('_quiet_seconds')
            db.execute('INSERT INTO attempts VALUES (?, ?)', (str(row['attempt']), json.dumps(row)))
        db.commit()
        db.close()
        self.assertEqual(['retained · zloop-panel/01 · quiet 30s', 'retained · zloop-panel/03 · quiet 0s'],
                         sorted(self.row_texts(self.snapshot(), 'wait')))

    def test_with_a_binding_only_attempts_of_its_sessions_are_shown(self):
        # Two sessions share one workstream: T's worker is not S's, but a session the binding names also counts.
        card(self.scratch / 'demo' / 'autopilot.md')
        orchestrator(self.scratch / 'demo', ['S', 'U'])
        other = dict(live('12/ticket-2', assignment='zloop-panel/02'), session='T', sessions=['T'])
        bound = dict(live('12/ticket-3', assignment='zloop-panel/03', quiet=90), session='U', sessions=['U'])
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite',
                 [live('12/ticket-1', assignment='zloop-panel/01'), other, bound])
        found = self.snapshot()
        self.assertEqual(['▶ builder · zloop-panel/01 · sonnet · quiet 30s',
                          '▶ builder · zloop-panel/03 · sonnet · quiet 1m'], self.row_texts(found, 'run'))

    def test_without_a_binding_only_this_sessions_attempts_are_shown(self):
        card(self.scratch / 'demo' / 'autopilot.md', session='S')
        other = dict(live('12/ticket-2', assignment='zloop-panel/02'), session='T', sessions=['T'],
                     result={'status': 'completed'}, retained={'reason': 'r', 'next_use': 'n'})
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite',
                 [live('12/ticket-1', assignment='zloop-panel/01'), other])
        found = self.snapshot()
        self.assertEqual(['▶ builder · zloop-panel/01 · sonnet · quiet 30s'], self.row_texts(found, 'run'))
        self.assertEqual([], self.row_texts(found, 'wait'))

    def test_a_binding_that_does_not_name_this_session_hides_its_own_attempts(self):
        # The card ties S to the workstream, but its binding names U: the binding decides whose workers are shown.
        card(self.scratch / 'demo' / 'autopilot.md', session='S')
        orchestrator(self.scratch / 'demo', ['U'])
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite', [live('12/ticket-1')])
        self.assertEqual([], self.row_texts(self.snapshot(), 'run'))

    def test_another_sessions_worker_alone_does_not_keep_a_workstream_live(self):
        card(self.scratch / 'demo' / 'autopilot.md', session='S', phase='ended', terminal='delivered')
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite',
                 [dict(live('12/ticket-2'), session='T', sessions=['T'])])
        self.assertFalse(self.snapshot()['active'])

    def test_a_worker_with_a_new_wait_armed_runs_beside_an_old_unconsumed_receipt(self):
        # `reconciled` does not consume the receipt, so a re-armed worker can still hold its last wait's receipt.
        card(self.scratch / 'demo' / 'autopilot.md')
        state = attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite',
                         [dict(live('12/ticket-1', assignment='zloop-panel/01'), monitor={'state': 'running'})])
        db = sqlite3.connect(state)
        db.execute("INSERT INTO events (attempt, kind, payload, acknowledged) VALUES (?, ?, ?, 0)",
                   ('12/ticket-1', 'wait-finished', json.dumps({'recorded_at': time.time() - 60, 'reason': 'timeout'})))
        db.commit()
        db.close()
        found = self.snapshot()
        self.assertEqual(['▶ builder · zloop-panel/01 · sonnet · quiet 1m'], self.row_texts(found, 'run'))
        self.assertEqual([], self.row_texts(found, 'wait'))

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

    def test_the_sessions_journal_names_the_workstream_when_no_card_does(self):
        # Journals live in the workstream's artifact directory, which is its reports folder.
        journal = self.scratch / 'gamma' / 'reports' / 'decisions-S.jsonl'
        journal.parent.mkdir(parents=True)
        journal.write_text('')
        ticket(journal.parent.parent / 'issues' / '01-a.md', 'gamma/01', status='implementing')
        # A newer workstream sits beside it, so only the journal rule can pick gamma.
        newer = self.scratch / 'omega' / 'issues' / '01-a.md'
        ticket(newer, 'omega/01', status='ready')
        later = time.time() + 120
        os.utime(newer, (later, later))
        found = self.snapshot('S')
        self.assertEqual('gamma', found['workstream'])
        self.assertIsNone(found['phase'])
        self.assertTrue(found['active'])

    def test_a_workstream_nothing_binds_to_this_session_is_not_shown(self):
        # A session just opened has no binding, card or journal of its own, so another session's live work stays off
        # its panel, however recent: the card says `unknown`, as a host that exposes no session ID writes it.
        card(self.scratch / 'demo' / 'autopilot.md', session='unknown')
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite', [live('12/ticket-1')])
        ticket(self.scratch / 'omega' / 'issues' / '01-a.md', 'omega/01', status='ready')
        found = self.snapshot('fresh')
        self.assertEqual({'active': False, 'workstream': None, 'phase': None, 'draft': False, 'rows': [], 'more': [],
                          'strip': None},
                         found)

    def test_another_checkouts_state_is_not_read(self):
        # The panel shows this workstream's attempts only: a fixture checkout beside it, even one this session
        # registered workers in, adds no rows.
        card(self.scratch / 'demo' / 'autopilot.md')
        attempts(self.scratch / 'demo' / 'reports' / 'workers.sqlite', [live('12/ticket-1')])
        with tempfile.TemporaryDirectory(prefix='fixture checkout ') as fixture:
            card(Path(fixture) / '.scratch' / 'fixture-run' / 'autopilot.md')
            attempts(Path(fixture) / '.scratch' / 'fixture-run' / 'reports' / 'workers.sqlite',
                     [live('9/ticket-9', assignment='fixture/09')])
            found = self.snapshot()
        self.assertEqual('demo', found['workstream'])
        self.assertEqual(['▶ builder · zloop-panel/01 · sonnet · quiet 30s'], self.row_texts(found, 'run'))

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
        self.assertEqual('demo', found['workstream'])
        # Review (Sonnet): the board the panel checks is the main checkout's too.
        ticket(self.scratch / 'demo' / 'issues' / '01-a.md', 'demo/01', status='done', accepted=False)
        self.assertEqual(self.row_texts(PANEL.snapshot(worktree, 'S', time.time()), 'blocker'), [
            'board fails its check: demo/01: status done with no acceptance; accept it with --accept demo/01, or '
            '--accept demo/01 --pr <url> when PRs delivered it, or --accept demo/01 --run <url> when a successful CI '
            'run proves it'])

    def test_an_allowance_pause_names_the_card_timers_resume(self):
        card(self.scratch / 'demo' / 'autopilot.md', terminal='paused-allowance')
        path = self.scratch / 'demo' / 'autopilot.md'
        path.write_text(path.read_text().replace('timer:', 'timer: 2026-10-27T06:00Z via zloop_wake'))
        self.assertIn('06:00', self.row_texts(self.snapshot(), 'blocker')[0])

    def test_the_orchestrated_workstream_wins_over_a_newer_unclaimed_one(self):
        card(self.scratch / 'alpha' / 'autopilot.md', session='unknown')
        orchestrator(self.scratch / 'alpha', ['S'])
        newer = self.scratch / 'beta' / 'issues' / '01-a.md'
        ticket(newer, 'beta/01', status='ready')
        later = time.time() + 120
        os.utime(newer, (later, later))
        self.assertEqual('alpha', self.snapshot('S')['workstream'])

    def test_a_binding_that_is_not_an_object_is_unreadable_not_fatal(self):
        # The malformed binding is skipped, so the card naming this session still picks its workstream.
        card(self.scratch / 'alpha' / 'autopilot.md', session='S')
        (self.scratch / 'alpha' / 'reports').mkdir(parents=True)
        (self.scratch / 'alpha' / 'reports' / 'orchestrator.json').write_text('[]')
        self.assertEqual('alpha', self.snapshot('S')['workstream'])

    def test_a_worker_this_session_registered_ties_its_workstream(self):
        # A run with no card, binding or journal, such as remediate's audit workers: the lifecycle records every host
        # session the registrar exported, here S started inside another host's shell, so `session` is the outer one.
        attempts(self.scratch / 'remediate-tests' / 'reports' / 'workers.sqlite',
                 [dict(live('1/audit-1', assignment='remediate-tests/audit-1', agent='reviewer'), session='outer',
                       sessions=['outer', 'S'])])
        found = self.snapshot('S')
        self.assertTrue(found['active'])
        self.assertEqual('remediate-tests', found['workstream'])
        self.assertEqual(['remediate-tests', '▶ reviewer · remediate-tests/audit-1 · sonnet · quiet 30s',
                          'done: ticket 0'],
                         self.row_texts(found))

    def test_of_the_workstreams_tied_to_this_session_a_live_one_wins_then_the_binding_order(self):
        # The session dispatched alpha, which has delivered, and now runs remediate workers: they are what is live.
        card(self.scratch / 'alpha' / 'autopilot.md', session='unknown', phase='ended', terminal='delivered')
        orchestrator(self.scratch / 'alpha', ['S'])
        state = attempts(self.scratch / 'remediate-x' / 'reports' / 'workers.sqlite',
                         [dict(live('1/audit-1'), session='S', sessions=['S'])])
        self.assertEqual(('remediate-x', True), (self.snapshot('S')['workstream'], self.snapshot('S')['active']))
        # Both live: the binding comes before registered workers, however recent the workers.
        card(self.scratch / 'alpha' / 'autopilot.md', session='unknown')
        later = time.time() + 120
        os.utime(state, (later, later))
        self.assertEqual(('alpha', True), (self.snapshot('S')['workstream'], self.snapshot('S')['active']))

    def test_of_two_live_runs_tied_by_workers_the_choice_holds_whichever_changed_last(self):
        # Every worker event rewrites its state; were the newest chosen, two live runs would swap on the panel, and a
        # pane the person closed for one would reopen under the other's name.
        states = {slug: attempts(self.scratch / slug / 'reports' / 'workers.sqlite',
                                 [dict(live(f'1/{slug}'), session='S', sessions=['S'])]) for slug in ('a-run', 'b-run')}
        for touched in ('b-run', 'a-run', 'b-run'):
            later = time.time() + 120 + len(touched)
            os.utime(states[touched], (later, later))
            self.assertEqual('a-run', self.snapshot('S')['workstream'], f'after {touched} changed')

    def test_another_sessions_worker_ties_nothing_to_this_session(self):
        attempts(self.scratch / 'theirs' / 'reports' / 'workers.sqlite',
                 [dict(live('1/ticket-1'), session='T', sessions=['T'])])
        self.assertEqual({'active': False, 'workstream': None, 'phase': None, 'draft': False, 'rows': [], 'more': [],
                          'strip': None},
                         self.snapshot('S'))
        self.assertEqual('theirs', self.snapshot('T')['workstream'])

    def test_a_hand_written_attempt_that_is_not_a_session_record_ties_nothing(self):
        # A `sessions` string that contains this session's id, and a row that is not an object, are skipped as data.
        attempts(self.scratch / 'odd' / 'reports' / 'workers.sqlite', [dict(live('1/ticket-1'), session=None, sessions='xSx')])
        db = sqlite3.connect(attempts(self.scratch / 'odder' / 'reports' / 'workers.sqlite', []))
        db.execute('INSERT INTO attempts VALUES (?, ?)', ('1/ticket-2', json.dumps(['S'])))
        db.commit()
        db.close()
        self.assertEqual({'active': False, 'workstream': None, 'phase': None, 'draft': False, 'rows': [], 'more': [],
                          'strip': None},
                         self.snapshot('S'))

    def test_a_row_that_is_not_an_object_beside_this_sessions_worker_is_skipped(self):
        state = attempts(self.scratch / 'mixed' / 'reports' / 'workers.sqlite',
                         [dict(live('1/ticket-1'), session='S', sessions=['S'])])
        db = sqlite3.connect(state)
        db.execute('INSERT INTO attempts VALUES (?, ?)', ('1/ticket-2', json.dumps(['S'])))
        db.commit()
        db.close()
        self.assertEqual(['mixed', '▶ builder · zloop-panel/01 · sonnet · quiet 30s', 'done: ticket 0'],
                         self.row_texts(self.snapshot('S')))

    def test_an_attempt_registered_before_session_lists_ties_by_its_one_session(self):
        # Registrations from before 2026-10-27 record `session` alone.
        attempts(self.scratch / 'older' / 'reports' / 'workers.sqlite', [dict(live('1/ticket-1'), session='S')])
        self.assertEqual('older', self.snapshot('S')['workstream'])
        self.assertTrue(self.snapshot('S')['active'])

    def worker_run(self, slug='remediate-x'):
        """A worker-run workstream with no tickets, its attempts registered by S: audits, reproductions and repairs."""
        def attempt(attempt, assignment, job, status=None):
            row = dict(live(attempt, assignment=f'{slug}/{assignment}', agent='reviewer'), job=job, session='S',
                       sessions=['S'])
            return dict(row, result={'status': status}) if status else row
        return attempts(self.scratch / slug / 'reports' / 'workers.sqlite', [
            attempt('p1/audit-1', 'p1', 'audit', 'completed'), attempt('p1/audit-2', 'p1', 'audit', 'completed'),
            attempt('p2/audit-1', 'p2', 'audit', 'completed'), attempt('p3/audit-1', 'p3', 'audit', 'failed'),
            attempt('p4/audit-1', 'p4', 'audit'), attempt('p5/audit-1', 'p5', 'audit', 'blocked'),
            attempt('r1/1', 'repro-p1', 'reproduce', 'completed'),
            attempt('x1/1', 'repair-p1', 'repair', 'cancelled')])

    def test_a_worker_run_without_tickets_counts_the_assignments_each_job_completed(self):
        # p1 completed twice (a re-audit) and counts once; p3 failed, p4 still runs, p5 is blocked and the repair was
        # cancelled.
        self.worker_run()
        self.assertEqual(['done: audit 2 · repair 0 · reproduce 1'], self.row_texts(self.snapshot('S'), 'counts'))

    def test_a_completed_row_without_a_string_assignment_counts_nothing(self):
        state = self.worker_run()
        db = sqlite3.connect(state)
        for number, assignment in enumerate((['p9'], {'p': 9}, None)):
            row = dict(live(f'odd/{number}'), job='audit', result={'status': 'completed'}, session='S', sessions=['S'])
            db.execute('INSERT INTO attempts VALUES (?, ?)',
                       (row['attempt'], json.dumps(dict(row, assignment=assignment))))
        db.commit()
        db.close()
        self.assertEqual(['done: audit 2 · repair 0 · reproduce 1'], self.row_texts(self.snapshot('S'), 'counts'))

    def test_a_workstream_with_tickets_keeps_its_tickets_tally(self):
        self.worker_run()
        ticket(self.scratch / 'remediate-x' / 'issues' / '01-a.md', 'remediate-x/01', status='ready')
        self.assertEqual(['1 ticket · 0 done · 0 blocked · 1 ready · 0 live'], self.row_texts(self.snapshot('S'), 'counts'))

    def plan(self, state, job, *assignments):
        """Record a job's plan the way an orchestrator does, through the lifecycle."""
        subprocess.run([sys.executable, '-B', str(support.script('worker_lifecycle.py')), 'plan', '--state', str(state),
                        '--job', job, *(arg for name in assignments for arg in ('--assignment', name))],
                       check=True, capture_output=True)

    def test_a_planned_job_counts_its_completed_planned_assignments_over_the_plan(self):
        # Done never exceeds the total: p2 completed outside the plan counts toward neither (104 done of 77 planned).
        state = self.worker_run()
        self.plan(state, 'audit', 'remediate-x/p1', 'remediate-x/p3', 'remediate-x/p6')
        self.assertEqual(['done: audit 1/3 · repair 0 · reproduce 1'], self.row_texts(self.snapshot('S'), 'counts'))

    def test_a_planned_job_with_no_attempts_yet_shows_none_of_its_total_done(self):
        state = self.worker_run()
        self.plan(state, 'diff-review', 'remediate-x/review')
        self.assertEqual(['done: audit 2 · diff-review 0/1 · repair 0 · reproduce 1'],
                         self.row_texts(self.snapshot('S'), 'counts'))

    def test_a_plan_recorded_before_any_worker_shows_its_total(self):
        # Remediate records the audit plan before it launches the first audit.
        card(self.scratch / 'remediate-x' / 'autopilot.md', session='S')
        self.plan(self.scratch / 'remediate-x' / 'reports' / 'workers.sqlite', 'audit',
                  'remediate-x/p1', 'remediate-x/p2')
        self.assertEqual(['done: audit 0/2'], self.row_texts(self.snapshot('S'), 'counts'))

    def test_two_sessions_each_see_their_own_workstream(self):
        for slug, session in (('alpha', 'S'), ('beta', 'T')):
            card(self.scratch / slug / 'autopilot.md', session='unknown')
            orchestrator(self.scratch / slug, [session])
        self.assertEqual('alpha', self.snapshot('S')['workstream'])
        self.assertEqual('beta', self.snapshot('T')['workstream'])


if __name__ == '__main__':
    unittest.main()
