#!/usr/bin/env python3
"""The shared builders other test files write their fixtures with produce what the real readers read."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support
import front_matter

LIFECYCLE = support.load_script('worker_lifecycle.py')


class Builders(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_a_ticket_file_is_the_board_front_matter_and_a_done_one_carries_the_acceptance_fields(self):
        # Rule: a fixture ticket reads as the board reads one: the template's fields, and `--accept`'s two for a done one.
        path = support.ticket_file(self.root, 'fx/07', 'done', blocked_by='fx/01, fx/02', touches='area')
        self.assertEqual(path, self.root / '.scratch/fx/issues/07-task.md')
        fields = front_matter.parse(path.read_text())
        self.assertEqual((fields['ticket'], fields['status'], fields['blocked_by'], fields['touches'], fields['devices']),
                         ('fx/07', 'done', ['fx/01', 'fx/02'], ['area'], []))
        self.assertEqual((fields['accepted'], fields['accepted_report']), ('a' * 40, 'b' * 64))
        pending = front_matter.parse(support.ticket_file(self.root, 'fx/08', 'ready', accepted=False).read_text())
        self.assertNotIn('accepted', pending)
        self.assertNotIn('accepted', front_matter.parse(support.ticket_file(self.root, 'fx/09', 'done',
                                                                            accepted=False).read_text()))

    def test_a_run_card_is_a_state_section_the_lifecycle_reads_back_field_by_field(self):
        # Rule: a fixture run card has the State section the panel, the stop hook and reap read.
        path = support.run_card(self.root / 'ws/autopilot.md', session='host-1', terminal='merged', board='local')
        state = LIFECYCLE.card_state(path)
        self.assertEqual((state['session'], state['terminal'], state['board'], state['confirmed'], state['phase']),
                         ('host-1', 'merged', 'local', '2026-10-27 "go"', 'dispatch'))
        self.assertNotIn('board', LIFECYCLE.card_state(support.run_card(self.root / 'b/autopilot.md', board=None)))

    def test_attempt_rows_are_written_as_the_lifecycle_state_holds_them(self):
        # Rule: a fixture state is read back by the lifecycle's own status reader, one row per attempt.
        state = self.root / 'ws/reports/workers.sqlite'
        row = support.attempt_row(self.root, attempt='t/2', worker='w-2')
        self.assertEqual((row['runtime'], row['repo'], row['owned'], row['worker']), ('claude', str(self.root), True, 'w-2'))
        support.write_attempts(state, [support.attempt_row(self.root), row])
        with sqlite3.connect(state) as db:
            self.assertEqual(sorted(json.loads(data)['attempt'] for _, data in db.execute('SELECT * FROM attempts')),
                             ['t/2', 'ticket-1/1'])
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone(), (2,))
        support.write_attempts(state, [row], events=False)
        with sqlite3.connect(state) as db:
            self.assertEqual([name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")],
                             ['attempts'])

    def test_the_host_session_variables_are_dropped_from_an_environment(self):
        # Rule: a test shell exports no host session unless the test says so.
        with patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 'a', 'OPENCODE_SESSION_ID': 'b', 'KEEP': 'c'}):
            env = support.without_host_sessions()
        self.assertEqual((env.get('KEEP'), 'CLAUDE_CODE_SESSION_ID' in env, 'OPENCODE_SESSION_ID' in env), ('c', False, False))
        self.assertEqual(support.without_host_sessions({'PI_SESSION_ID': 'x', 'A': '1'}), {'A': '1'})


if __name__ == '__main__':
    unittest.main()
