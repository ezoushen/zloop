#!/usr/bin/env python3
"""Our grilling asks the user about principles; the agent decides implementation detail."""
import subprocess
import unittest

import support

ROOT = support.ROOT
GRILLING = ROOT / 'runtime/interview/grilling.md'


def prose(path):
    return ' '.join(path.read_text().split())


class Grilling(unittest.TestCase):
    def test_the_tree_is_worked_principles_first_in_small_rounds(self):
        text = prose(GRILLING)
        for rule in ('Map the plan as a design tree', 'A question waits until the decisions it depends on are settled',
                     'goal and principles, then what may change or must keep working, then the behavior the user would notice',
                     '[question filter](question-filter.md)', 'one or two questions per round',
                     'a recommendation that "yes" accepts',
                     'Finding facts is the agent\'s job', 'without blocking the questions that do not depend on them',
                     'done only when the user confirms a shared understanding'):
            self.assertIn(rule, text)

    def test_implementation_detail_is_decided_by_the_agent_at_every_depth(self):
        text = prose(GRILLING)
        for rule in ('Never ask the user about implementation detail',
                     'decide it within the principles; a material agent choice is recorded in the ledger',
                     "listed among the agent's decisions the user may veto",
                     'Exhaustive depth, on an explicit request, only raises the number of questions per round',
                     'a material agent choice', 'A plan the user calls ready, or an accepted mandate that already settles the next step, is that confirmation'):
            self.assertIn(rule, text)
        align = prose(ROOT / 'skills/align/SKILL.md')
        self.assertIn('showing material ones for veto', align)
        self.assertNotIn('shows it for veto', align)
        self.assertIn('lists material ones for veto', prose(ROOT / 'docs/rehearsals/planning.md'))
        self.assertNotIn('relax the round', align)
        self.assertNotIn('question-count/detail limit', align)

    def test_every_entry_point_uses_our_grilling(self):
        self.assertIn('[grilling](../../runtime/interview/grilling.md)', prose(ROOT / 'skills/align/SKILL.md'))
        self.assertIn('[grilling](runtime/interview/grilling.md)', prose(ROOT / 'bootstrap.md'))
        self.assertIn('[grilling](../../runtime/interview/grilling.md)', prose(ROOT / 'skills/brief/pipeline.md'))

    def test_no_doc_keeps_the_retired_grilling_wording(self):
        found = subprocess.run(
            ['git', 'grep', '-n', '-I', '-E', r'exhaustive interview|exhaustive frontier|'
             r"grilling's internal|relax the round|detail limit", '--', '.', ':!journals',
             ':!tests/test_grilling.py'], cwd=ROOT, capture_output=True, text=True)
        self.assertIn(found.returncode, (0, 1), found.stderr)
        self.assertEqual('', found.stdout)

    def test_the_question_filter_defers_to_grilling_for_exhaustive_depth_and_glossary_terms(self):
        text = prose(ROOT / 'runtime/interview/question-filter.md')
        self.assertIn('exhaustive [grilling](grilling.md) raises this count', text)
        self.assertIn("the project's glossary defines", text)
        self.assertIn('restate an `_Avoid_` word with its canonical term', text)


if __name__ == '__main__':
    unittest.main()
