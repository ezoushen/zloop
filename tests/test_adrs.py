#!/usr/bin/env python3
"""A decision that affects others or passes the three tests is offered as an ADR, written on the user's yes, and binds later work."""
import unittest

import support
from support import prose

ROOT = support.ROOT
ADR = ROOT / 'runtime/domain/adr.md'


class Adrs(unittest.TestCase):
    def test_the_adr_doc_states_its_format_and_process(self):
        text = prose(ADR)
        for rule in ('`docs/adr/NNNN-slug.md`', 'the highest existing number plus one',
                     'Create `docs/adr/` lazily', 'a title and one to three sentences',
                     'Add Considered Options and Consequences sections only when they add something',
                     '**Hard to reverse:**', '**Surprising without context:**', '**A real trade-off:**',
                     'Offer one when a decision qualifies by either rule', '**It affects others:**',
                     'sets a rule every later change must follow', '**It passes the three tests**, all of them',
                     'A bare rule with no real trade-off is not an ADR',
                     'goes where the project keeps its rules',
                     'plus a one-line rule there that points to it',
                     'Offer the ADR to the user and write it only after their yes',
                     'a new ADR that names the ADR it replaces', '`superseded by ADR-NNNN`',
                     'Never rewrite an accepted ADR', "A project's existing decision-record convention wins"):
            self.assertIn(rule, text)

    def test_each_phase_offers_reads_or_holds_on_adrs(self):
        grilling = prose('runtime/interview/grilling.md')
        self.assertIn('Offer an [ADR](../domain/adr.md) for a settled decision that qualifies', grilling)
        self.assertIn("write either only after the user's yes", grilling)
        self.assertNotIn('passes the three tests', grilling)
        self.assertIn("or a line in the project's rules file for a settled rule with no real trade-off", grilling)
        self.assertIn('ADR [index](../../runtime/domain/adr-index.md) and the ADRs in the area', prose('skills/align/SKILL.md'))
        ticket = prose('skills/dispatch/jobs/ticket.md')
        self.assertIn('accepted [ADRs](../../../runtime/domain/adr.md) in the area', ticket)
        self.assertIn('An accepted ADR that looks outdated is flagged to the orchestrator, not silently followed',
                      ticket)
        self.assertIn('A change that contradicts an accepted ADR', prose('skills/review/method.md'))
        self.assertIn('a conflict with an accepted [ADR](../domain/adr.md)', prose('runtime/workers/design-hold.md'))


if __name__ == '__main__':
    unittest.main()
