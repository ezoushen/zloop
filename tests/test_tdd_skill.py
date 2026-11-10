#!/usr/bin/env python3
"""The tdd skill works the TDD standard in this session, typed by the user or loaded by the agent; with no ticket, the
user stands in for it, and the standard stays where the builder reads it."""
import re
import unittest

import support
from support import prose

ROOT = support.ROOT
SKILL = ROOT / 'skills/tdd/SKILL.md'
STANDARD_LINK = '](../../standards/tdd.md)'


class TddSkill(unittest.TestCase):
    def test_it_reads_the_standard_it_works_before_proposing_the_seams(self):
        declaration = re.search(r'^First read:.*?(?:\n\n|\Z)', SKILL.read_text(), re.M | re.S).group(0)
        self.assertEqual(' '.join(declaration.split()),
                         'First read: the runtime bootstrap (`../../bootstrap.md`), the host session part.')
        # The agent proposes the seams itself here, so the standard's seam rules must be read before that.
        self.assertIn(f'[test-first development{STANDARD_LINK} in full before proposing the seams', prose(SKILL))
        # The builder and ADR 0007 still name the standard, so it stays a standard.
        self.assertIn('](../standards/tdd.md)', prose(ROOT / 'agents/builder.md'))

    def test_the_user_agrees_the_seams_before_any_test(self):
        text = prose(SKILL).lower()
        for rule in ('before any test, propose the public interfaces the tests will reach',
                     'what it catches and what it misses',
                     'a seam the user did not confirm gets no test'):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_the_users_rules_and_their_independent_expected_values_come_first(self):
        text = prose(SKILL)
        for rule in ('each business rule the change must satisfy',
                     'where its expected values come from',
                     'pre-existing failures'):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_where_the_standard_names_the_ticket_the_user_stands_in(self):
        section = prose(SKILL).split('## Where the standard names a ticket', 1)[1].split(' ## ', 1)[0]
        for rule in ('the seams agreed with the user', 'goes back to the user', 'offered to the user'):
            with self.subTest(rule=rule):
                self.assertIn(rule, section)

    def test_it_finishes_with_each_rule_and_its_recorded_runs(self):
        section = prose(SKILL).split('## Finish', 1)[1]
        for rule in ('each rule with the test that protects it', 'failing, passing and break runs',
                     'no coverage figure'):
            with self.subTest(rule=rule):
                self.assertIn(rule, section)

    def test_it_works_the_standard_rather_than_restating_it(self):
        text = prose(SKILL)
        for sentence in ('Write one failing test, run it and see it fail for the expected reason',
                         'Mock only at system boundaries', 'Expected values come from an independent source'):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, prose(ROOT / 'standards/tdd.md'))
                self.assertNotIn(sentence, text)


if __name__ == '__main__':
    unittest.main()
