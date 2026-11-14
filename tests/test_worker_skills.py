#!/usr/bin/env python3
"""Registering a ticket attempt requires its packet's skills line to match the skills a local ticket declares; under
autofix, a builder's completed result waits for a registered readiness verifier."""

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


class ReadinessWithTheFirstBuilders(WorkerLifecycleCase):
    # Rule: under autofix the readiness verifier starts with the first builders: it may register before or after a
    # builder, and a builder's completed result waits for it. The review report's own `autofix` overrides the
    # orchestrator binding's.
    def bind(self, autofix, report=None):
        (self.root / 'orchestrator.json').write_text(json.dumps({'pane': 'p', 'sessions': [], 'autofix': autofix}))
        if report is not None:
            (self.root / 'spec-review.md').write_text(f'---\nautofix: {report}\n---\n')

    def readiness(self):
        builder = self.execution.read_text()
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': 'verifier',
                                              'job': 'readiness', 'settings': {'model': 'sonnet', 'effort': 'high'}}))
        self.register('readiness/1', assignment='readiness', session='native-r', worker='pane-r')
        self.execution.write_text(builder)

    def finish(self, code=0):
        report = self.root / 'ticket-1-1.md'
        report.write_text('## Implemented\n\nVerified assignment report at head-1\n')
        return self.completed(status='completed', head='head-1', report=str(report), evidence=['checks.log@head-1'],
                              code=code)

    def test_under_autofix_a_builder_result_waits_for_the_readiness_verifier_in_either_launch_order(self):
        self.bind(True)
        self.register()
        self.ready()
        refused = self.finish(code=2)
        self.assertIn('readiness', refused)
        self.assertIn('dispatch Setup', refused)
        self.readiness()
        self.finish()

    def test_without_autofix_a_builder_completes_with_no_readiness_verifier(self):
        self.bind(False)
        self.register()
        self.ready()
        self.finish()

    def test_the_review_reports_autofix_false_overrides_a_binding_that_authorized_it(self):
        self.bind(True, report='false')
        self.register()
        self.ready()
        self.finish()

    def test_the_review_reports_autofix_true_overrides_a_binding_that_did_not(self):
        self.bind(False, report='true')
        self.register()
        self.ready()
        self.assertIn('readiness', self.finish(code=2))


if __name__ == '__main__':
    unittest.main()
