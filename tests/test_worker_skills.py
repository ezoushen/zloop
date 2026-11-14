#!/usr/bin/env python3
"""Registering a ticket attempt requires its packet's skills line to match the skills a local ticket declares, and,
under an autofix binding, a registered readiness verifier."""

import json
import unittest

import support
from lifecycle_case import WorkerLifecycleCase


class RegistrationSkills(WorkerLifecycleCase):
    def register_with(self, declared, assigned, code=0):
        """Register a packet whose `skills:` line lists `assigned`, for a local ticket declaring `declared` (None: no
        `skills:` line in the ticket)."""
        path = support.ticket_file(self.root, 'fx/01', path=self.root / 'ticket.md', title='task',
                                   extra=f'skills: [{", ".join(declared)}]\n' if declared is not None else '')
        lines = support.assignment_file(self.root, 'ticket').read_text().splitlines()
        lines = [f'ticket: {path}' if line.startswith('ticket:') else f'skills: [{", ".join(assigned)}]'
                 if line.startswith('skills:') else line for line in lines]
        packet = self.root / 'packet.txt'
        packet.write_text('\n'.join(lines) + '\n')
        return self.register(packet=str(packet), code=code)

    def test_a_packet_listing_the_tickets_skills_registers(self):
        self.register_with(['a', 'b'], ['a', 'b'])
        self.assertEqual(len(self.cli('status')['attempts']), 1)

    def test_a_packet_missing_a_ticket_skill_is_refused_naming_it(self):
        refused = self.register_with(['a', 'b'], ['a'], code=2)
        self.assertIn('skills differ from the ticket: missing b', refused)

    def test_a_packet_adding_a_skill_is_refused_naming_it(self):
        refused = self.register_with(['a', 'b'], ['a', 'b', 'c'], code=2)
        self.assertIn('skills differ from the ticket: added c', refused)

    def test_a_packet_both_dropping_and_adding_names_both(self):
        refused = self.register_with(['a', 'b'], ['a', 'c'], code=2)
        self.assertIn('skills differ from the ticket: missing b; added c', refused)

    def test_a_ticket_declaring_no_skills_refuses_a_packet_that_adds_one(self):
        self.assertIn('added c', self.register_with([], ['c'], code=2))

    def test_a_skills_value_that_is_not_a_bracketed_list_is_refused(self):
        packet = self.root / 'packet.txt'
        packet.write_text(support.assignment_file(self.root, 'ticket').read_text().replace('skills: []', 'skills: a, b'))
        refused = self.register(packet=str(packet), code=2)
        self.assertIn('skills must be one line of its own, before ticket context, holding a bracketed list', refused)


class ReadinessFirst(WorkerLifecycleCase):
    # Live check (claude, 1896dae): the host skipped the readiness launch its dispatch setup names; the delivery gate
    # then forced it after review, when it could no longer check anything the builders depended on.
    def bind(self, autofix):
        (self.root / 'orchestrator.json').write_text(json.dumps({'pane': 'p', 'sessions': [], 'autofix': autofix}))

    def test_under_autofix_a_builder_registers_only_after_the_readiness_verifier(self):
        self.bind(True)
        refused = self.register(code=2)
        self.assertIn('readiness', refused)
        self.assertIn('dispatch Setup', refused)
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': 'verifier',
                                              'job': 'readiness', 'settings': {'model': 'sonnet', 'effort': 'high'}}))
        self.register('readiness/1', assignment='readiness', session='native-r', worker='pane-r')
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': 'builder', 'job': 'ticket',
                                              'settings': {'model': 'sonnet', 'effort': 'high'}}))
        self.register()

    def test_without_autofix_a_builder_needs_no_readiness_verifier(self):
        self.bind(False)
        self.register()
        self.assertEqual(len(self.cli('status')['attempts']), 1)


if __name__ == '__main__':
    unittest.main()
