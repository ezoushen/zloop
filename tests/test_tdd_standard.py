#!/usr/bin/env python3
"""Every builder works test-first from our own TDD standard, whose tests protect business rules, not coverage."""
import unittest

import support
from support import prose

ROOT = support.ROOT
TDD = ROOT / 'standards/tdd.md'
KIND = ROOT / 'standards/kinds/tautological-tests.md'


class TddStandard(unittest.TestCase):
    def test_the_standard_keeps_each_required_rule(self):
        text = prose(TDD)
        for rule in ('**Red before green.** Write one failing test, run it and see it fail for the expected reason, '
                     'then write only enough code to make it pass',
                     '**One slice at a time.** One rule, one test, one minimal implementation per cycle',
                     'Never write all the tests first',
                     "**Refactor after green.** Once the test passes, refactor the ticket's own code within the "
                     "ticket's scope, keeping the tests green; a wider refactor goes to review",
                     '**Record the failing run before the passing one:**',
                     "A test checks behavior through the module's public interface, the seam its callers use, "
                     'and survives a refactor of the implementation',
                     '**Mock only at system boundaries:** external services, time and randomness, and storage or the '
                     'file system when no local stand-in exists',
                     'At a boundary, prefer one specific operation per external action over one generic call, so each '
                     'mock returns one shape and needs no conditional logic',
                     "Never mock the project's own modules or internal collaborators",
                     '**Seams come from the ticket.**',
                     'Prefer the highest existing seam that exposes the rule, and the fewest seams',
                     'A seam the ticket does not name goes back to the orchestrator before any test is written there',
                     '[module design](design.md)',
                     'Do not expose an internal seam through the interface only because a test uses it',
                     '**Each test states the business rule it protects**',
                     '**Expected values come from an independent source:**',
                     '**Assert the whole claimed result**, and for an expected failure the reason the rule gives',
                     '**Break the rule on purpose.** Each new or changed test is shown to fail when its rule is '
                     'deliberately broken in the implementation',
                     'then the break is undone and the test passes again',
                     '**Coverage means rules, not lines.** Every business rule and acceptance criterion of the ticket '
                     'has a test that fails when that rule breaks, including its boundary cases from the spec',
                     'A line-coverage figure is neither the goal nor proof',
                     'Tests of trivia, such as pass-through accessors or framework behavior, are not written to raise it',
                     '**No honest test, no weak one.** When neither the ticket\'s seams nor one the orchestrator '
                     'approves can hold an honest test of a rule, write none: report the rule untested and name what '
                     'verifies it instead',
                     '**Implementation-coupled:**',
                     '**Horizontal slicing:**'):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_the_standard_agrees_with_the_tautological_tests_kind(self):
        tdd, kind = prose(TDD), prose(KIND)
        self.assertIn('[tautological-tests](kinds/tautological-tests.md)', tdd)
        self.assertIn('Expected values come from an independent source:** a specified literal, a worked example from '
                      'the ticket or spec, or a separate trusted oracle; never from the code under test, a copy of its '
                      'logic or a value the test configured on a mock', tdd)
        self.assertIn('Rewrite the test so it checks the claim with an expected result from an independent source: a '
                      'specified literal, a worked example, or a separate trusted oracle', kind)
        self.assertIn('- an expected value computed by the code under test or a copy of its logic;', kind)
        self.assertIn('- assertions on values the test configured on a mock;', kind)
        self.assertIn('A test is tautological for the behavior it claims when a plausible defect in that behavior leaves '
                      'it passing', kind)
        self.assertIn('**Tautological:** a plausible defect in the claimed rule leaves it passing', tdd)

    def test_the_standard_has_no_code_in_any_language(self):
        self.assertEqual([], support.code_samples(TDD.read_text()))

    def test_the_builder_core_skills_the_repair_job_and_the_pipeline_use_it(self):
        import agent_defs
        builder = agent_defs.load(ROOT)['agents']['builder']
        self.assertIn('standards/tdd.md', builder['core_skills'])
        for name, link in (('skills/review/jobs/repair.md', '[TDD standard](../../../standards/tdd.md)'),
                           ('skills/brief/pipeline.md', '[TDD standard](../../standards/tdd.md)')):
            with self.subTest(doc=name):
                self.assertIn(link, prose(ROOT / name))

    def test_a_builder_reads_the_standard_and_records_each_slice(self):
        builder = prose(ROOT / 'agents/builder.md')
        self.assertIn('[TDD standard](../standards/tdd.md): read it in full before the first test', builder)
        self.assertIn('Change behavior test-first at the ticket\'s seams, one slice at a time — shown by: the report '
                      'names the TDD standard as read and records, for each slice, a failing run before the passing '
                      'one and the run that breaks its rule on purpose', builder)
        job = prose(ROOT / 'skills/dispatch/jobs/ticket.md')
        self.assertIn('for each slice, its failing run, its passing run and the run that breaks its rule on purpose', job)
        self.assertIn('Read the [TDD standard](../../../standards/tdd.md) in full before the first test', job)

    def test_the_pipeline_map_names_no_tdd_skill(self):
        self.assertNotIn('`tdd`', prose(ROOT / 'skills/brief/pipeline.md'))


if __name__ == '__main__':
    unittest.main()
