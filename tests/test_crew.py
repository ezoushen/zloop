#!/usr/bin/env python3
"""A workstream's crew: align names the experts the work needs from the project's crew profile, and delivery
updates that profile from what workers loaded or left unused."""
import re
import unittest

import support
from support import prose

PROFILE = '.scratch/crew.md'


def alignment_template():
    text = (support.ROOT / 'skills/align/SKILL.md').read_text()
    return re.search(r'```markdown\n(# <slug>.*?)```', text, re.S).group(1)


class AlignNamesTheCrew(unittest.TestCase):
    def test_the_alignment_template_has_a_crew_section_with_one_row_per_expert(self):
        # Rule: a reader looking for the crew finds a `## Crew` section whose rows carry the four columns.
        section = alignment_template().partition('## Crew')[2].partition('\n## ')[0]
        self.assertTrue(section, 'the template has no ## Crew section')
        flat = ' '.join(section.split())
        for column in ('agent', 'skills, each with the fact that selects it', 'area', 'whether it starts before a diff exists'):
            with self.subTest(column=column):
                self.assertIn(column, flat)

    def test_establishing_the_design_takes_crew_rows_from_the_profile_and_cites_it(self):
        # Rule: where `.scratch/crew.md` covers an area, align takes that area's rows from it and cites it.
        text = prose('skills/align/SKILL.md')
        establish = text.partition('## Establish the design')[2].partition('## Save the current alignment')[0]
        self.assertIn(f"Where the crew profile (`{PROFILE}`) covers an area, take that area's crew rows from it and cite it", establish)


class DeliveryUpdatesTheProfile(unittest.TestCase):
    def test_the_handoff_records_each_areas_expert_and_skills_in_the_profile(self):
        handoff = prose('skills/dispatch/handoff.md')
        self.assertIn(f'update the crew profile (`{PROFILE}`) with each area\'s expert and the skills workers loaded on demand '
                      'or left unused', handoff)

    def test_a_learning_moves_into_tracked_instructions_only_with_the_users_approval(self):
        skills = prose('runtime/workers/skills.md')
        learn = skills.partition('## Record and learn')[2]
        self.assertIn(f'record them in the crew profile (`{PROFILE}`)', learn)
        self.assertIn("moving one into the project's tracked instructions needs the user's approval", learn)
        self.assertNotIn('Propose either change to the project', learn)


if __name__ == '__main__':
    unittest.main()
