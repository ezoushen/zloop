#!/usr/bin/env python3
"""Collecting a completed ticket attempt requires its worker's own probe to show every need the ticket declares."""

import json
import unittest

import support
from lifecycle_case import WorkerLifecycleCase


def probe(worktree, **statuses):
    """A probe report as capability_probe.py prints it, each named capability with the given status."""
    return dict({name: {'status': status, 'evidence': f'{name} {status}'} for name, status in statuses.items()},
                worktree=str(worktree))


class CollectionNeeds(WorkerLifecycleCase):
    def ticket(self, needs=None, value=None, trailing=''):
        """A local ticket file, with a `needs:` line when needs is given, and the packet that points a worker at it.

        value replaces the packet's `ticket:` value; trailing follows it on the same line."""
        path = self.root / 'ticket.md'
        path.write_text('---\nticket: fx/01\nstatus: ready\nblocked_by: []\n'
                        + (f'needs: [{", ".join(needs)}]\n' if needs is not None else '') + '---\n# 01: task\n')
        packet = self.root / 'packet.txt'
        lines = support.assignment_file(self.root, 'ticket').read_text().splitlines()
        shown = f'ticket: {path if value is None else value}{trailing}'
        packet.write_text('\n'.join(shown if line.startswith('ticket:') else line for line in lines) + '\n')
        return packet

    def complete(self, packet, report=None, status='completed', code=0):
        """Register and start the attempt with no probe, then record its result with the probe file as evidence."""
        self.register(packet=str(packet))
        self.ready()
        evidence = ['checks.log@head-1']
        if report is not None:
            file = self.root / 'probe.json'
            file.write_text(json.dumps(report))
            evidence.append(str(file))
        done = self.root / 'done.md'
        done.write_text('## Implemented\n\nVerified assignment report at head-1\n')
        return self.record('result', status=status, head='head-1', report=str(done), evidence=evidence, code=code)

    def test_a_ticket_needing_docker_is_not_collected_without_a_probe(self):
        refused = self.complete(self.ticket(['docker']), code=2)
        self.assertIn('The ticket needs docker: no probe of this worktree in the result evidence', refused)

    def test_a_ticket_needing_docker_is_not_collected_when_the_probe_shows_docker_unavailable(self):
        refused = self.complete(self.ticket(['docker']), probe(self.root, docker='unavailable', network='available'),
                                code=2)
        self.assertIn('The ticket needs docker: the probe reports docker unavailable', refused)

    def test_a_ticket_needing_docker_is_collected_when_its_workers_probe_shows_docker(self):
        self.complete(self.ticket(['docker']), probe(self.root, docker='available', network='unavailable'))
        self.assertEqual(self.cli('status')['attempts'][0]['result']['status'], 'completed')

    def test_a_probe_taken_in_another_worktree_does_not_count(self):
        refused = self.complete(self.ticket(['docker']), probe(self.root.parent / 'elsewhere', docker='available'), code=2)
        self.assertIn('The ticket needs docker: no probe of this worktree in the result evidence', refused)

    def test_a_need_the_probe_reports_unknown_or_omits_is_not_met(self):
        refused = self.complete(self.ticket(['docker', 'network', 'dev-fd']),
                                probe(self.root, docker='unknown', network='available'), code=2)
        self.assertIn('The ticket needs docker, dev-fd: the probe reports docker unknown, dev-fd missing', refused)

    def test_a_blocked_result_naming_the_unavailable_need_is_collected_without_a_probe(self):
        self.complete(self.ticket(['docker']), status='blocked')
        self.assertEqual(self.cli('status')['attempts'][0]['result']['status'], 'blocked')

    def test_a_ticket_without_needs_is_collected_without_a_probe(self):
        self.complete(self.ticket())
        self.assertEqual(self.cli('status')['attempts'][0]['result']['status'], 'completed')

    def test_needs_are_read_from_a_ticket_value_that_shares_its_line_with_another_input(self):
        refused = self.complete(self.ticket(['docker'], trailing=' brief: /abs/alignment.md'), code=2)
        self.assertIn('The ticket needs docker', refused)

    def test_a_ticket_that_is_not_a_path_has_no_needs(self):
        self.complete(self.ticket(['docker'], value='https://tracker.example/issue/7'))
        self.assertEqual(self.cli('status')['attempts'][0]['result']['status'], 'completed')

    def test_a_ticket_path_to_a_missing_file_is_refused(self):
        refused = self.complete(self.ticket(['docker'], value=str(self.root / 'gone.md')), code=2)
        self.assertIn('names a ticket file that does not exist', refused)


if __name__ == '__main__':
    unittest.main()
