#!/usr/bin/env python3
"""Every builder works test-first from our own TDD standard, whose tests protect business rules, not coverage."""
import unittest

import support

ROOT = support.ROOT
TDD = ROOT / 'standards/tdd.md'
KIND = ROOT / 'standards/kinds/tautological-tests.md'


def prose(path):
    return ' '.join(path.read_text().split())


class TddStandard(unittest.TestCase):
    def test_the_loop_is_one_failing_test_then_only_enough_code(self):
        text = prose(TDD)
        self.assertIn('**Red before green.** Write one failing test, run it and see it fail for the expected reason, '
                      'then write only enough code to make it pass', text)
        self.assertIn('**One slice at a time.** One rule, one test, one minimal implementation per cycle', text)
        self.assertIn('Never write all the tests first', text)

    def test_the_ticket_code_is_refactored_after_green_within_its_scope(self):
        self.assertIn("**Refactor after green.** Once the test passes, refactor the ticket's own code within the "
                      "ticket's scope, keeping the tests green; a wider refactor goes to review", prose(TDD))

    def test_a_failing_run_is_recorded_before_the_passing_one(self):
        self.assertIn('**Record the failing run before the passing one:**', prose(TDD))

    def test_good_tests_check_behavior_through_the_public_interface(self):
        text = prose(TDD)
        self.assertIn("A test checks behavior through the module's public interface, the seam its callers use, "
                      'and survives a refactor of the implementation', text)
        self.assertIn('**Mock only at system boundaries:** external services, time and randomness, and storage or the '
                      'file system when no local stand-in exists', text)
        self.assertIn('At a boundary, prefer one specific operation per external action over one generic call, so each '
                      'mock returns one shape and needs no conditional logic', text)
        self.assertIn("Never mock the project's own modules or internal collaborators", text)

    def test_seams_come_from_the_ticket(self):
        text = prose(TDD)
        self.assertIn('**Seams come from the ticket.**', text)
        self.assertIn('Prefer the highest existing seam that exposes the rule, and the fewest seams', text)
        self.assertIn('A seam the ticket does not name goes back to the orchestrator before any test is written there',
                      text)
        self.assertIn('[module design](design.md)', text)

    def test_internal_seams_stay_out_of_the_interface(self):
        self.assertIn('Do not expose an internal seam through the interface only because a test uses it', prose(TDD))

    def test_each_test_protects_a_stated_business_rule(self):
        self.assertIn('**Each test states the business rule it protects**', prose(TDD))

    def test_expected_values_come_from_an_independent_source(self):
        text = prose(TDD)
        self.assertIn('**Expected values come from an independent source:**', text)
        self.assertIn('never from the code under test, a copy of its logic or a value the test configured on a mock',
                      text)

    def test_a_test_asserts_the_whole_result_and_the_reason_for_a_failure(self):
        self.assertIn('**Assert the whole claimed result**, and for an expected failure the reason the rule gives',
                      prose(TDD))

    def test_each_test_is_shown_to_fail_when_its_rule_is_broken(self):
        text = prose(TDD)
        self.assertIn('**Break the rule on purpose.** Each new or changed test is shown to fail when its rule is '
                      'deliberately broken in the implementation', text)
        self.assertIn('then the break is undone and the test passes again', text)

    def test_coverage_means_every_rule_has_a_test_not_a_line_figure(self):
        text = prose(TDD)
        self.assertIn('**Coverage means rules, not lines.** Every business rule and acceptance criterion of the ticket '
                      'has a test that fails when that rule breaks, including its boundary cases from the spec', text)
        self.assertIn('A line-coverage figure is neither the goal nor proof', text)
        self.assertIn('Tests of trivia, such as pass-through accessors or framework behavior, are not written to raise it',
                      text)

    def test_a_rule_no_honest_test_can_reach_is_reported_untested_not_weakly_tested(self):
        self.assertIn('**No honest test, no weak one.** When neither the ticket\'s seams nor one the orchestrator '
                      'approves can hold an honest test of a rule, write none: report the rule untested and name what '
                      'verifies it instead', prose(TDD))

    def test_the_anti_patterns_are_named(self):
        text = prose(TDD)
        for name in ('**Implementation-coupled:**', '**Tautological:**', '**Horizontal slicing:**'):
            with self.subTest(name=name):
                self.assertIn(name, text)

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

    def test_builders_ticket_and_repair_jobs_and_the_pipeline_use_it(self):
        import agent_defs
        builder = agent_defs.load(ROOT)['agents']['builder']
        self.assertIn('standards/tdd.md', builder['core_skills'])
        for name, link in (('agents/builder.md', '[TDD standard](../standards/tdd.md)'),
                           ('skills/dispatch/jobs/ticket.md', '[TDD standard](../../../standards/tdd.md)'),
                           ('skills/review/jobs/repair.md', '[TDD standard](../../../standards/tdd.md)'),
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
