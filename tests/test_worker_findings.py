#!/usr/bin/env python3
"""The repair plan's findings: each held by one repair assignment, each settled by a collected result."""
import json
import unittest

from lifecycle_case import WorkerLifecycleCase
import support


class RepairFindings(WorkerLifecycleCase):
    def setUp(self):
        super().setUp()
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': 'builder',
                                             'job': 'repair', 'settings': {'model': 'sonnet', 'effort': 'high'}}))

    def repair(self, attempt, findings, code=0):
        """Register a repair attempt of the assignment its attempt names, whose packet's findings: line is findings."""
        assignment, number = attempt.split('/')
        packet = support.assignment_file(self.root, 'repair', attempt=attempt)
        packet = packet.rename(self.root / f'{assignment}-{number}.txt')
        packet.write_text(packet.read_text().replace('findings: /fixture/findings', f'findings: {findings}'))
        session = f'native-{assignment}-{number}'
        return self.register(attempt, assignment, session=session, worker=f'pane-{session}', packet=str(packet),
                             code=code)

    def test_a_repair_attempt_names_planned_findings_no_other_assignment_holds(self):
        # Live (stforestkit remediate-taut-534): every repair packet's findings: read "the CONFIRMED candidates in the
        # listed audit report(s)", so R03 and R08 both repaired P01, R04 and R06 both P05, and none took P04.
        self.repair('autofix/1', '/fixture/findings')  # With no findings planned, a repair names them as it likes.
        # What an attempt holds is read from its packet alone.
        self.assertEqual(self.register('autofix/2', 'autofix', session='native-autofix-2', worker='pane-autofix-2',
                                       findings=['P05-001'], code=2).strip(),
                         'Registration reads findings from the packet; an event cannot supply them')
        self.cli('plan', '--job', 'repair', '--finding', 'P01-001', '--finding', 'P01-002', '--finding', 'P05-001')
        refused = self.repair('r1/1', "the CONFIRMED candidates in the listed audit report(s) (IDs and proposed "
                              "minimal repairs are in each report's Findings section)", code=2)
        self.assertEqual(refused.strip(), "The repair packet's findings: names what the repair plan does not hold: "
                         'the, CONFIRMED, candidates, in, listed and 12 more; list the IDs of the planned findings '
                         'this assignment repairs, separated by commas or spaces')
        self.repair('r1/1', 'P01-001, P01-002')
        refused = self.repair('r2/1', 'P01-002 P05-001', code=2)
        self.assertEqual(refused.strip(), 'Another repair assignment already holds P01-002 (r1); each planned finding '
                         'is repaired under one assignment')
        self.repair('r2/1', 'P05-001')
        # A later attempt of the same assignment names its findings again.
        self.repair('r1/2', 'P01-002')
        held = {row['attempt']: row.get('findings') for row in self.cli('status')['attempts']}
        self.assertEqual(held, {'autofix/1': None, 'r1/1': ['P01-001', 'P01-002'], 'r2/1': ['P05-001'],
                                'r1/2': ['P01-002']})

    def settle(self, attempt, code=0, status='completed', **fields):
        """Record attempt's result with its report and evidence, plus fields."""
        report = self.root / (attempt.replace('/', '-') + '.md')
        report.write_text('## Fixed round 1\n\nRepaired at head-1\n')
        return self.completed(attempt, code=code, status=status, head='head-1', report=str(report),
                              evidence=['checks.log@head-1'], **fields)

    def test_a_result_settles_each_finding_its_attempt_holds(self):
        # The totals come from these results, not from a number someone writes (Pi's ledger said 551, then 453).
        self.cli('plan', '--job', 'repair', '--finding', 'P01-001', '--finding', 'P01-002', '--finding', 'P05-001')
        self.repair('r1/1', 'P01-001 P01-002')
        self.ready('r1/1')
        held = ("This attempt's result settles each finding it holds (P01-001, P01-002) as repaired, open or "
                'runtime-bug in findings: ')
        for given, problem in ((None, 'missing P01-001, P01-002'),
                               ({'P01-001': 'repaired'}, 'missing P01-002'),
                               ({'P01-001': 'repaired', 'P01-002': 'open', 'P05-001': 'open'},
                                'P05-001 not held by this attempt'),
                               ({'P01-001': 'done', 'P01-002': 'open'},
                                "P01-001: 'done' is not repaired, open or runtime-bug")):
            with self.subTest(given=given):
                refused = self.settle('r1/1', code=2, **({} if given is None else {'findings': given}))
                self.assertEqual(refused.strip(), held + problem)
        self.settle('r1/1', findings={'P01-001': 'repaired', 'P01-002': 'runtime-bug'})
        self.assertEqual(self.cli('status', '--attempt', 'r1/1')['attempts'][0]['result']['findings'],
                         {'P01-001': 'repaired', 'P01-002': 'runtime-bug'})
        # A blocked or failed attempt settles them too: what it did not repair is open.
        self.repair('r2/1', 'P05-001')
        self.ready('r2/1')
        self.assertIn('missing P05-001', self.settle('r2/1', code=2, status='blocked'))
        self.settle('r2/1', status='blocked', findings={'P05-001': 'open'})


if __name__ == '__main__':
    unittest.main()
