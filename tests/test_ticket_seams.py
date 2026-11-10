#!/usr/bin/env python3
"""Each ticket names the seams its tests reach, so the user's ticket check approves them."""
import unittest

import support
from support import prose

ROOT = support.ROOT
REFINE = ROOT / 'skills/refine/SKILL.md'
TEMPLATE = ROOT / 'skills/refine/ticket-template.md'


class TicketSeams(unittest.TestCase):
    def test_the_template_carries_a_seams_line_with_the_rule_for_choosing_them(self):
        self.assertIn('**Seams:** <the public interfaces its tests reach: existing and higher ones first, '
                      'as few as will do>', prose(TEMPLATE))

    def test_refine_prepares_each_ticket_with_its_seams_for_the_users_check(self):
        self.assertIn('**Seams:** the public interfaces its tests reach, existing and higher ones first, '
                      'as few as will do; the ticket check approves them', prose(REFINE))

    def test_refine_stays_under_the_size_cap(self):
        self.assertLessEqual(len(REFINE.read_bytes()), 12 * 1024)

    def test_the_tdd_standard_takes_its_seams_from_the_tickets_seams_line(self):
        self.assertIn("**Seams come from the ticket.** Its **Seams** line names the public interfaces its tests "
                      "reach, approved with the ticket", prose(ROOT / 'standards/tdd.md'))


if __name__ == '__main__':
    unittest.main()
