#!/usr/bin/env python3
"""The repair plan's findings: each held by one repair assignment, each settled by a collected result."""
import json
import shlex
import unittest

from lifecycle_case import SCRIPT, WorkerLifecycleCase
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
        # What an attempt holds is read from its packet alone, and what became of it from the lifecycle's own records:
        # a register event that supplies them would fake totals, or a closure Herdr never confirmed.
        for supplied in ({'findings': ['P05-001']}, {'result': {'status': 'blocked', 'findings': {'P05-001': 'open'}}},
                         {'closed': {'evidence': ['forged']}}, {'retained': {'next_use': 'later', 'reason': 'kept'}},
                         {'accepted': {'commit': 'c1'}}, {'orchestrator': 'w9:p9', 'sessions': ['host-9']}):
            with self.subTest(supplied=sorted(supplied)):
                self.assertEqual(self.register('autofix/2', 'autofix', session='native-autofix-2',
                                               worker='pane-autofix-2', code=2, **supplied).strip(),
                                 f'Registration takes no {", ".join(sorted(supplied))}: the lifecycle records them '
                                 'itself, findings from the packet and the rest as the attempt goes on')
        self.cli('plan', '--job', 'repair', '--finding', 'P01-001', '--finding', 'P01-002', '--finding', 'P05-001')
        refused = self.repair('r1/1', "the CONFIRMED candidates in the listed audit report(s) (IDs and proposed "
                              "minimal repairs are in each report's Findings section)", code=2)
        plan = f'python3 {SCRIPT} plan --state {shlex.quote(str(self.state.resolve()))} --job repair --finding <ID>'
        self.assertEqual(refused.strip(), "The repair packet's findings: names what the repair plan does not hold: "
                         'the, CONFIRMED, candidates, in, listed and 12 more; list the IDs of the planned findings '
                         'this assignment repairs, separated by commas or spaces, after planning a confirmed one '
                         f'with `{plan}`')
        self.assertEqual(self.repair('r1/1', ', ', code=2).strip(), "The repair packet's findings: names no finding; "
                         'list the IDs of the planned findings this assignment repairs, separated by commas or spaces')
        self.repair('r1/1', 'P01-001, P01-002')
        refused = self.repair('r2/1', 'P01-002 P05-001', code=2)
        self.assertEqual(refused.strip(), 'Another repair assignment already holds P01-002 (r1); each planned finding '
                         'is repaired under one assignment: leave it out of this packet, or repair it in a new attempt '
                         'of the assignment that holds it')
        self.repair('r2/1', 'P05-001,')  # A list may end with its separator.
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

    def test_the_totals_put_each_planned_finding_in_one_bucket_from_the_recorded_results(self):
        # Pi's ledger for 558 findings went 551 + 6 + 2 = 559, then 453 repaired with P05-F18-001 wrongly open: here
        # the lifecycle counts, from the plan and the latest collected result naming each finding.
        self.cli('plan', '--job', 'repair', *[part for finding in ('P01-001', 'P01-002', 'P04-001', 'P05-001',
                                                                   'P05-002', 'P05-003') for part in ('--finding', finding)])
        self.repair('r1/9', 'P01-001 P01-002')
        self.repair('r2/1', 'P05-001')
        self.repair('r3/1', 'P05-002, P05-003')
        for attempt in ('r1/9', 'r3/1'):
            self.ready(attempt)
        self.settle('r1/9', findings={'P01-001': 'repaired', 'P01-002': 'open'})
        self.settle('r3/1', findings={'P05-002': 'runtime-bug', 'P05-003': 'repaired'})
        # A retry in flight leaves the last collected result standing; once collected, its result is the finding's,
        # by registration order (r1/10 after r1/9).
        self.repair('r1/10', 'P01-002')
        expected = {'planned': 6, 'counts': {'repaired': 2, 'open': 1, 'runtime-bug': 1, 'pending': 1, 'unassigned': 1},
                    'open': ['P01-002'], 'runtime-bug': ['P05-002'], 'pending': ['P05-001'], 'unassigned': ['P04-001']}
        self.assertEqual(self.cli('findings'), expected)
        self.ready('r1/10')
        self.settle('r1/10', findings={'P01-002': 'repaired'})
        expected.update(counts={'repaired': 3, 'open': 0, 'runtime-bug': 1, 'pending': 1, 'unassigned': 1}, open=[])
        self.assertEqual(self.cli('findings'), expected)
        # Attempts collected out of their registration order: the later registered one's result stands.
        self.repair('r3/2', 'P05-003')
        self.repair('r3/3', 'P05-003')
        for attempt in ('r3/2', 'r3/3'):
            self.ready(attempt)
        self.settle('r3/3', findings={'P05-003': 'open'})
        self.settle('r3/2', findings={'P05-003': 'repaired'})
        expected.update(counts={'repaired': 2, 'open': 1, 'runtime-bug': 1, 'pending': 1, 'unassigned': 1},
                        open=['P05-003'])
        self.assertEqual(self.cli('findings'), expected)
        # Narrowing the plan narrows the totals.
        self.cli('plan', '--job', 'repair', '--replace', '--finding', 'P01-001', '--finding', 'P04-001')
        self.assertEqual(self.cli('findings'), {
            'planned': 2, 'counts': {'repaired': 1, 'open': 0, 'runtime-bug': 0, 'pending': 0, 'unassigned': 1},
            'open': [], 'runtime-bug': [], 'pending': [], 'unassigned': ['P04-001']})

    def test_a_run_with_nothing_left_running_is_held_for_planned_findings_no_assignment_holds(self):
        # Live (stforestkit remediate-taut-534): with every repair worker done and P04, P08 and P15 never assigned,
        # Pi's stop passed and it reported the repair round complete.
        findings = [f'P{number:02}-001' for number in range(1, 8)]
        self.cli('plan', '--job', 'repair', *[part for finding in findings for part in ('--finding', finding)])
        command = (f'python3 {SCRIPT} plan --state {shlex.quote(str(self.state.resolve()))} --job repair --replace '
                   '[--finding <ID>...]')
        gap = ('No repair assignment holds {} of the 7 planned repair findings ({} and {} more): register a repair '
               "attempt whose packet's findings: names them, or, on the user's decision, narrow the plan with "
               f'`{command}`')
        # The run's own check names a plan no repair has started on; a session's stop is held only for its own repairs.
        self.assertEqual(self.cli('gate', '--stage', 'yield', code=1)['missing'],
                         [gap.format(7, ', '.join(findings[:5]), 2)])
        self.repair('r1/1', 'P01-001')
        self.ready('r1/1')
        # While a repair worker runs, the run is not done: its wait is the gap.
        self.assertEqual(self.cli('gate', '--stage', 'yield', code=1)['missing'], ['r1/1: no armed wait'])
        self.settle('r1/1', findings={'P01-001': 'repaired'})
        self.acknowledge()
        self.assertEqual(self.cli('gate', '--stage', 'yield', code=1)['missing'],
                         [gap.format(6, ', '.join(findings[1:6]), 1)])
        self.repair('r2/1', ' '.join(findings[1:]))
        self.ready('r2/1')
        self.settle('r2/1', findings={finding: 'open' for finding in findings[1:]})
        self.acknowledge()
        self.assertEqual(self.cli('gate', '--stage', 'yield'), {'complete': True, 'missing': []})


if __name__ == '__main__':
    unittest.main()
