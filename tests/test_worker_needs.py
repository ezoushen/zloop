#!/usr/bin/env python3
"""Collecting a completed ticket attempt requires its worker's own probe to show every need the ticket declares."""

import json
import unittest

import support
from lifecycle_case import WorkerLifecycleCase

# Where and how an assignment states its needs, as registration words a refusal.
SHAPE = ('needs must be one line of its own, before ticket context, holding a bracketed list of '
         'network, docker, setuid, repo-write, dev-fd')


def probe(worktree, **statuses):
    """A probe report as capability_probe.py prints it, each named capability with the given status."""
    return dict({name: {'status': status, 'evidence': f'{name} {status}'} for name, status in statuses.items()},
                worktree=str(worktree))


class CollectionNeeds(WorkerLifecycleCase):
    def ticket(self, needs=None, value=None, trailing='', assigned=()):
        """A local ticket file, with a `needs:` line when needs is given, and the packet that points a worker at it.

        value replaces the packet's `ticket:` value; trailing follows it on the same line; assigned is the packet's
        own `needs:` list."""
        path = self.root / 'ticket.md'
        path.write_text('---\nticket: fx/01\nstatus: ready\nblocked_by: []\n'
                        + (f'needs: [{", ".join(needs)}]\n' if needs is not None else '') + '---\n# 01: task\n')
        packet = self.root / 'packet.txt'
        lines = support.assignment_file(self.root, 'ticket').read_text().splitlines()
        shown = f'ticket: {path if value is None else value}{trailing}'
        own = f'needs: [{", ".join(assigned)}]'
        lines = [shown if line.startswith('ticket:') else own if line.startswith('needs:') else line for line in lines]
        packet.write_text('\n'.join(lines) + '\n')
        return packet

    def complete(self, packet, report=None, status='completed', code=0, after_register=None):
        """Register and start the attempt with no probe, then record its result with the probe file as evidence.

        after_register, when given, runs between registration and the result."""
        self.register(packet=str(packet))
        self.ready()
        if after_register:
            after_register(packet)
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

    def test_a_remote_ticket_needing_docker_by_its_assignment_is_not_collected_without_a_probe(self):
        refused = self.complete(self.ticket(value='https://tracker.example/issue/7', assigned=['docker']), code=2)
        self.assertIn('The ticket needs docker: no probe of this worktree in the result evidence', refused)

    def test_a_remote_ticket_needing_docker_by_its_assignment_is_collected_when_the_probe_shows_docker(self):
        self.complete(self.ticket(value='https://tracker.example/issue/7', assigned=['docker']),
                      probe(self.root, docker='available'))
        self.assertEqual(self.cli('status')['attempts'][0]['result']['status'], 'completed')

    def test_the_needs_registered_still_hold_when_the_assignment_changes_or_goes_before_collection(self):
        for change in (lambda packet: packet.write_text(packet.read_text().replace('needs: [docker]', 'needs: []')),
                       lambda packet: packet.unlink()):
            with self.subTest(change=change):
                self.setUp()
                refused = self.complete(self.ticket(value='https://tracker.example/issue/7', assigned=['docker']),
                                        code=2, after_register=change)
                self.assertIn('The ticket needs docker: no probe of this worktree in the result evidence', refused)

    def test_an_unknown_need_in_a_local_tickets_front_matter_is_not_met(self):
        refused = self.complete(self.ticket(['gpu']), code=2)
        self.assertIn('The ticket needs gpu: no probe of this worktree in the result evidence', refused)

    def test_lines_of_the_ticket_context_that_start_with_needs_are_not_the_assignments_needs(self):
        packet = self.ticket(value='https://tracker.example/issue/7', assigned=['docker'])
        packet.write_text(packet.read_text().replace('ticket context: /fixture/ticket-context', '\n'.join((
            'ticket context: # 7: build image', 'needs: a docker daemon', 'needs: []'))))
        refused = self.complete(packet, code=2)
        self.assertIn('The ticket needs docker: no probe of this worktree in the result evidence', refused)

    def test_a_local_tickets_needs_hold_when_the_assignment_goes_before_collection(self):
        refused = self.complete(self.ticket(['docker']), code=2, after_register=lambda packet: packet.unlink())
        self.assertIn('The ticket needs docker: no probe of this worktree in the result evidence', refused)

    def test_an_unknown_need_is_not_met_even_when_a_probe_claims_it(self):
        refused = self.complete(self.ticket(['gpu']), probe(self.root, gpu='available'), code=2)
        self.assertIn('The ticket needs gpu: it is not a capability the probe checks', refused)

    def test_a_local_tickets_needs_count_even_when_its_assignment_names_others(self):
        # Both lists count, named in the probe's vocabulary order.
        refused = self.complete(self.ticket(['docker'], assigned=['network']), probe(self.root, setuid='available'),
                                code=2)
        self.assertIn('The ticket needs network, docker: the probe reports network missing, docker missing', refused)

    def test_a_ticket_path_to_a_missing_file_is_refused(self):
        refused = self.complete(self.ticket(['docker'], value=str(self.root / 'gone.md')), code=2)
        self.assertIn('names a ticket file that does not exist', refused)


class RegistrationNeeds(WorkerLifecycleCase):
    def packet_with(self, needs_line):
        """A ticket assignment for a remote ticket whose `needs` line, where the template puts it, is needs_line, or
        none when it is None."""
        lines = [needs_line if line.startswith('needs:') else line
                 for line in support.assignment_file(self.root, 'ticket').read_text().splitlines()]
        packet = self.root / 'packet.txt'
        packet.write_text('\n'.join(line for line in lines if line is not None) + '\n')
        return packet

    def test_a_ticket_assignment_without_needs_is_refused_so_a_remote_ticket_cannot_skip_the_probe(self):
        refused = self.register(packet=str(self.packet_with(None)), code=2)
        self.assertIn('The ticket assignment lacks needs', refused)

    def test_a_ticket_assignment_needing_nothing_is_registered(self):
        self.register(packet=str(self.packet_with('needs: []')))
        self.assertEqual(len(self.cli('status')['attempts']), 1)

    def test_an_assignment_need_outside_the_probe_vocabulary_is_refused_naming_the_allowed_needs(self):
        refused = self.register(packet=str(self.packet_with('needs: [docker, gpu]')), code=2)
        self.assertIn("needs 'gpu' is not one of network, docker, setuid, repo-write, dev-fd", refused)

    def test_needs_quoted_inside_another_inputs_prose_is_not_the_assignments_needs(self):
        packet = self.packet_with('needs: []')
        packet.write_text(packet.read_text().replace(
            'ticket context: /fixture/ticket-context', 'ticket context: issue 7 says the runner needs: [GPU, 16GB]'))
        self.register(packet=str(packet))
        self.assertEqual(len(self.cli('status')['attempts']), 1)

    def test_a_needs_line_that_does_not_close_its_list_is_refused_even_after_a_well_formed_one(self):
        refused = self.register(packet=str(self.packet_with('needs: []\nneeds: [docker,\n  network]')), code=2)
        self.assertIn(SHAPE, refused)

    def test_a_needs_line_after_the_ticket_context_is_refused(self):
        packet = self.packet_with(None)
        packet.write_text(packet.read_text() + 'needs: [docker]\n')
        self.assertIn(SHAPE, self.register(packet=str(packet), code=2))

    def test_needs_sharing_a_line_with_another_input_is_refused_naming_where_it_goes(self):
        packet = self.packet_with(None)
        packet.write_text(packet.read_text().replace('ticket: fx/01', 'ticket: fx/01  needs: [docker]'))
        self.assertIn(SHAPE, self.register(packet=str(packet), code=2))

    def test_an_assignment_needs_value_that_is_not_a_bracketed_list_is_refused(self):
        refused = self.register(packet=str(self.packet_with('needs: docker')), code=2)
        self.assertIn(SHAPE, refused)


if __name__ == '__main__':
    unittest.main()
