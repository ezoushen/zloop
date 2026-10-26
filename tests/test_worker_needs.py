#!/usr/bin/env python3
"""An attempt's startup is refused when its ticket needs a capability the recorded probe does not show."""

import json
import sys
import unittest

import support
from lifecycle_case import WorkerLifecycleCase


def probe(**statuses):
    """A probe report as capability_probe.py prints it, each named capability with the given status."""
    return {name: {'status': status, 'evidence': f'{name} {status}'} for name, status in statuses.items()}


class StartupNeeds(WorkerLifecycleCase):
    def ticket(self, needs=None):
        """A local ticket file, with a `needs:` line when needs is given, and the packet that points a worker at it."""
        path = self.root / 'ticket.md'
        path.write_text('---\nticket: fx/01\nstatus: ready\nblocked_by: []\n'
                        + (f'needs: [{", ".join(needs)}]\n' if needs is not None else '') + '---\n# 01: task\n')
        packet = self.root / 'packet.txt'
        lines = support.assignment_file(self.root, 'ticket').read_text().splitlines()
        packet.write_text('\n'.join(f'ticket: {path}' if line.startswith('ticket:') else line for line in lines) + '\n')
        return packet

    def start(self, packet, code=0, **fields):
        self.register(packet=str(packet))
        return self.record('startup', observed={'runtime': 'claude', 'model': 'sonnet', 'effort': 'high'},
                           evidence=['native.jsonl:1'], code=code, **fields)

    def test_a_ticket_needing_docker_is_refused_naming_docker_when_the_probe_reports_none(self):
        refused = self.start(self.ticket(['docker']), code=2, probe=probe(docker='unavailable', network='available'))
        self.assertIn('The ticket needs docker: the recorded probe reports docker unavailable', refused)
        self.assertFalse(self.cli('status')['attempts'][0].get('verified'))

    def test_a_ticket_needing_docker_starts_when_the_probe_shows_docker(self):
        self.start(self.ticket(['docker']), probe=probe(docker='available', network='unavailable'))
        self.assertTrue(self.cli('status')['attempts'][0]['verified'])

    def test_a_ticket_needing_docker_is_refused_without_a_probe(self):
        self.assertIn('The ticket needs docker: startup records no probe', self.start(self.ticket(['docker']), code=2))

    def test_a_need_the_probe_reports_unknown_or_omits_is_not_met(self):
        refused = self.start(self.ticket(['docker', 'network', 'dev-fd']), code=2,
                             probe=probe(docker='unknown', network='available'))
        self.assertIn('The ticket needs docker, dev-fd: the recorded probe reports docker unknown, dev-fd missing', refused)

    def submit(self, packet, code=0, **fields):
        """Submit through the lifecycle CLI, where the startup record is taken from the native session."""
        self.register(packet=str(packet))
        native = self.root / 'native.jsonl'
        native.write_text(json.dumps({'type': 'assistant', 'sessionId': 'native-1', 'isSidechain': False,
                                     'cwd': str(self.root), 'effort': 'high', 'perTurnEffort': 'high',
                                     'message': {'model': 'sonnet'}}) + '\n')
        request = self.root / 'submit.json'
        request.write_text(json.dumps(dict(native=str(native), timeout=2, argv=[sys.executable, '-c', 'pass'], **fields)))
        return self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=code)

    def test_a_submitted_attempt_is_held_to_its_tickets_needs_by_the_probe_in_the_request(self):
        refused = self.submit(self.ticket(['docker']), code=2)
        self.assertIn('The ticket needs docker: startup records no probe', refused)

    def test_a_submitted_attempt_whose_request_carries_a_probe_showing_the_need_runs(self):
        self.assertEqual(self.submit(self.ticket(['docker']), probe=probe(docker='available'))['exit_code'], 0)

    def test_a_ticket_without_needs_is_unaffected_by_the_probe(self):
        self.start(self.ticket(), probe=probe(docker='unavailable'))
        self.assertTrue(self.cli('status')['attempts'][0]['verified'])


if __name__ == '__main__':
    unittest.main()
