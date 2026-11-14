#!/usr/bin/env python3
"""Refine writes a ticket's skills from its expert's Crew row; dispatch copies them into the assignment's skills line."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT / path).read_text()


class TicketSkillsDocs(unittest.TestCase):
    def test_refine_writes_each_tickets_skills_from_its_experts_crew_row(self):
        # Rule: the crew align planned is the crew that works, so refine copies the Crew row into `skills`.
        step = re.search(r'^5\. \*\*Execution inputs\.\*\*.*?(?=^6\. )', text('skills/refine/SKILL.md'), re.M | re.S).group()
        self.assertRegex(step, r"`skills`[^.]*expert's row in the alignment's Crew section[^.]*`\[\]` when none")

    def test_dispatch_copies_the_tickets_skills_into_the_assignments_skills_line_before_ticket_context(self):
        template = re.search(r'```text\n(\s*adapter:.*?)```', text('skills/dispatch/iteration.md'), re.S).group(1)
        lines = [line.strip() for line in template.splitlines()]
        listed = [i for i, line in enumerate(lines) if line.startswith('skills:')]
        self.assertEqual(len(listed), 1, lines)
        self.assertIn("the ticket's skills list", lines[listed[0]])
        self.assertLess(listed[0], next(i for i, line in enumerate(lines) if line.startswith('ticket context:')))

    def test_the_situational_tier_names_the_bracketed_skills_line(self):
        tier = re.search(r'2\. \*\*Situational:\*\*.*?(?=^3\. )', text('runtime/workers/skills.md'), re.M | re.S).group()
        self.assertIn('one `skills:` line holding a bracketed list', tier)


if __name__ == '__main__':
    unittest.main()
