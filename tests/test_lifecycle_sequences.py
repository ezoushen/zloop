#!/usr/bin/env python3
"""Generated traces compare real control/acceptance operations with a small outcome oracle."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import support
import run_control
import worker_lifecycle as life
from t3_intent_fixture import T3Read


def exercise(operations):
    fixture = unittest.TestCase()
    with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, XDG_STATE_HOME=tmp):
        root = Path(tmp)
        state = root / '.scratch/trace/reports/workers.sqlite'
        state.parent.mkdir(parents=True)
        card = support.run_card(state.parent.parent / 'autopilot.md', phase='align', **{'intent-thread': 'host-1'})
        control = run_control.adopt(card, 'trace grant')
        host = None
        native = root / 'native.jsonl'
        native.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'host-1'}}) + '\n')
        report = root / 'report.md'
        original = '## Implemented\nOriginal evidence\n'
        report.write_text(original)
        db = life.connect(state)
        try:
            saved = dict(attempt='t/1', assignment='t', agent='builder', job='ticket', repo=str(root),
                         runtime='codex', native_session='child', verified=True, requested={'model': 'fixture'},
                         execution_snapshot={}, result={'status': 'completed', 'head': 'head',
                         'report': str(report), 'report_sha256': hashlib.sha256(original.encode()).hexdigest(),
                         'evidence': []})
            db.execute('INSERT INTO attempts VALUES (?,?)', ('t/1', json.dumps(saved)))
            db.execute('INSERT INTO events (attempt,kind,payload,acknowledged) VALUES (?,?,?,0)',
                       ('t/1', 'result', json.dumps({'recorded_at': 1})))
            db.commit()
            active, intact, accepted, declined = True, True, False, False
            for number, operation in enumerate(operations):
                if operation == 'pause':
                    text = 'autopilot --pause'
                    row = {'type': 'response_item', 'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                           'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}}
                    with native.open('a') as out:
                        out.write(json.dumps(row) + '\n')
                    source = run_control.user_source(native, 'codex', ['host-1'], operation, control['created_at'])
                    run_control.transition(card, 'paused-by-user', source,
                                           operation, run_control.read(card)['revision'], f'{operation}-{number}')
                    active = False
                elif operation == 'resume':
                    if host is None:
                        env = support.without_host_sessions()
                        host = T3Read(fixture, root, env)
                    revision = run_control.read(card)['revision']
                    command = host.command(support.script('run_control.py'), 'resume', card,
                                           '--expect', revision, '--request-id', f'resume-{number}', '--reason', 'trace')
                    preview = subprocess.run(command, capture_output=True, text=True, env=env)
                    host.accept(preview, 'autopilot --take-over')
                    result = subprocess.run(command, capture_output=True, text=True, env=env)
                    assert result.returncode == 0, result.stderr
                    active = True
                elif operation in ('damage', 'restore'):
                    intact = operation == 'restore'
                    report.write_text(original if intact else 'Changed after collection\n')
                elif operation == 'ack':
                    result = subprocess.run([sys.executable, '-B', str(support.SCRIPTS / 'worker_lifecycle.py'),
                        'ack', '--state', str(state), '--event-id', '1'], capture_output=True, text=True)
                    assert result.returncode == 0, result.stderr
                elif operation in ('accept', 'decline'):
                    allowed = not (accepted or declined) and (operation == 'decline' or active and intact)
                    data = dict(kind='accepted' if operation == 'accept' else 'declined', attempt='t/1',
                                commit='integrated', reason='trace verdict', evidence=['retained evidence'])
                    try:
                        life.record(db, data)
                        succeeded = True
                    except ValueError:
                        succeeded = False
                    assert succeeded == allowed, (operations[:number + 1], 'allowed', allowed, 'observed', succeeded)
                    if succeeded:
                        accepted, declined = operation == 'accept', operation == 'decline'
                actual = life.get(db, 't/1')
                assert bool(actual.get('accepted')) == accepted, operations[:number + 1]
                assert bool(actual.get('declined')) == declined, operations[:number + 1]
        finally:
            db.close()
            fixture.doCleanups()


class LifecycleSequences(unittest.TestCase):
    def test_generated_control_receipt_and_acceptance_traces(self):
        choices = ['pause', 'resume', 'damage', 'restore', 'ack', 'accept', 'decline']
        for seed in range(12):
            rng = random.Random(seed)
            trace = [rng.choice(choices) for _ in range(24)]
            try:
                exercise(trace)
            except AssertionError:
                minimal = trace[:]
                for index in range(len(minimal) - 1, -1, -1):
                    candidate = minimal[:index] + minimal[index + 1:]
                    try:
                        exercise(candidate)
                    except AssertionError:
                        minimal = candidate
                self.fail(f'seed={seed}; reduced trace={minimal}')

    def test_oracle_rejects_a_broken_report_integrity_guard(self):
        exercise(['damage', 'accept'])
        with patch.object(life, 'report_intact', return_value=True):
            with self.assertRaises(AssertionError):
                exercise(['damage', 'accept'])

    def test_oracle_rejects_acceptance_while_explicitly_paused(self):
        exercise(['pause', 'accept'])
        with patch.object(run_control, 'require_active', return_value=None):
            with self.assertRaises(AssertionError):
                exercise(['pause', 'accept'])


if __name__ == '__main__':
    unittest.main()
