#!/usr/bin/env python3
"""Isolated zloop feature fixtures and outcome graders; no product checkout is mutated."""
import datetime
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import autopilot_card
import run_control
import source_identity
import worker_lifecycle


def put(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def card(workspace):
    path = workspace / 'autopilot.md'
    path.write_text('# Feature fixture\n\n## State\nconfirmed: 2026-11-18 "run this fixture"\n'
                    'session: fixture-host\nauto-merge: no\nmode: full\nboard: local\n'
                    'dispatch-effort: medium\nreview-effort: high\nphase: align\nterminal:\n'
                    'history:\nevidence:\ntimer:\nresume: autopilot --take-over\n')
    run_control.adopt(path, 'fixture confirmation')
    return path


def launch(name, workspace, evidence):
    oracle = {'feature': name}
    if name == 'source-drift':
        for label, version in [('source', 2), ('installed', 1)]:
            base = workspace / label
            (base / 'scripts').mkdir(parents=True)
            (base / 'bootstrap.md').write_text('identical entry instructions\n')
            (base / 'scripts/source_identity.py').write_text(f'VERSION = {version}\n')
        oracle['inputs'] = {str(p.relative_to(workspace)): sha(p) for p in workspace.rglob('*') if p.is_file()}
    elif name in ('pause-provenance', 'continue-phase'):
        path = card(workspace)
        oracle['control_hash'] = sha(run_control.location(path))
        if name == 'pause-provenance':
            rows = [{'type': 'session_meta', 'payload': {'id': 'fixture-host'}},
                    {'type': 'response_item', 'timestamp': '2099-01-01T00:00:00Z',
                     'payload': {'type': 'message', 'role': 'user', 'content': [
                         {'type': 'input_text', 'text': 'Stopping now leaves these gaps: unfinished run'}]}}]
            (workspace / 'native.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
        else:
            (workspace / 'worker-complete.txt').write_text('Child result already collected; refine is authorized.\n')
    elif name == 'stale-report':
        report = workspace / 'report.md'
        report.write_text('Original collected report\n')
        saved = {'result': {'report': str(report), 'report_sha256': sha(report)}}
        put(workspace / 'collected.json', saved)
        oracle['input_hash'] = sha(workspace / 'collected.json')
        report.write_text('Changed after collection\n')
        oracle['report_hash'] = sha(report)
    elif name == 'paused-completion':
        (workspace / 'paused/reports').mkdir(parents=True)
        path = card(workspace / 'paused')
        native = workspace / 'native.jsonl'
        rows = [{'type': 'session_meta', 'payload': {'id': 'fixture-host'}},
                {'type': 'response_item', 'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 'payload': {'role': 'user', 'content': [{'type': 'input_text', 'text': 'autopilot --pause'}]}}]
        native.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        source = run_control.user_source(native, 'codex', ['fixture-host'], 'pause', 0)
        run_control.transition(path, 'paused-by-user', source, 'explicit user pause', 1, 'pause')
        report = workspace / 'completed.md'
        report.write_text('## Implemented\nCompleted worker fixture, already collected.\n')
        db = worker_lifecycle.connect(workspace / 'paused/reports/workers.sqlite')
        try:
            saved = dict(attempt='worker/1', assignment='worker', agent='builder', job='ticket', repo=str(workspace),
                         runtime='codex', native_session='completed-fixture', verified=True,
                         requested={'model': 'fixture'}, execution_snapshot={},
                         result=dict(status='completed', head='fixture', report=str(report), report_sha256=sha(report), evidence=[]))
            db.execute('INSERT INTO attempts VALUES (?,?)', ('worker/1', json.dumps(saved)))
            db.execute('INSERT INTO events (attempt,kind,payload,acknowledged) VALUES (?,?,?,0)',
                       ('worker/1', 'result', json.dumps({'recorded_at': 1})))
            db.commit()
        finally:
            db.close()
        oracle.update(control_hash=sha(run_control.location(path)), report_hash=sha(report),
                      attempt_hash=run_control.digest(json.dumps(saved, sort_keys=True)))
    elif name == 'measure-baseline':
        values = [secrets.randbelow(100) + 1 for _ in range(5)]
        put(workspace / 'samples.json', values)
        (workspace / 'measure.py').write_text('import json,pathlib,uuid\n'
            'p=pathlib.Path(__file__).parent; values=json.loads((p/"samples.json").read_text())\n'
            'result={"mean":sum(values)/len(values),"sample_id":uuid.uuid4().hex}\n'
            '(p/"measurement-execution.json").write_text(json.dumps(result))\nprint(json.dumps(result))\n')
        oracle.update(mean=sum(values) / len(values), input_hash=sha(workspace / 'samples.json'),
                      driver_hash=sha(workspace / 'measure.py'))
    else:
        raise ValueError('Unknown feature')
    put(evidence / 'oracle.json', oracle)


def drive(name, workspace):
    if name == 'source-drift':
        put(workspace / 'comparison.json', source_identity.compare(workspace / 'installed', workspace / 'source'))
    elif name == 'pause-provenance':
        state = run_control.read(workspace / 'autopilot.md')
        try:
            run_control.user_source(workspace / 'native.jsonl', 'codex', ['fixture-host'], 'pause', state['created_at'])
            paused = True
        except ValueError:
            paused = False
        put(workspace / 'decision.json', {'paused': paused})
    elif name == 'stale-report':
        saved = json.loads((workspace / 'collected.json').read_text())
        put(workspace / 'verdict.json', {'accepted': worker_lifecycle.report_intact(saved)})
    elif name == 'paused-completion':
        subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/worker_lifecycle.py'), 'ack',
                        '--state', str(workspace / 'paused/reports/workers.sqlite'), '--event-id', '1'], check=True)
    elif name == 'continue-phase':
        subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/autopilot_card.py'), 'phase',
                        str(workspace / 'autopilot.md'), 'refine'], check=True)
    elif name == 'measure-baseline':
        output = subprocess.check_output([sys.executable, '-B', str(workspace / 'measure.py')], text=True)
        (workspace / 'measurement.json').write_text(output)


def grade(name, workspace, evidence):
    oracle = json.loads((evidence / 'oracle.json').read_text())
    passed = False
    retained = {'source-drift': ['source/bootstrap.md', 'source/scripts/source_identity.py', 'installed/bootstrap.md',
                               'installed/scripts/source_identity.py', 'comparison.json'],
                'pause-provenance': ['autopilot.md', 'autopilot-control.json', 'native.jsonl', 'decision.json'],
                'stale-report': ['collected.json', 'report.md', 'verdict.json'],
                'paused-completion': ['native.jsonl', 'completed.md', 'paused/autopilot.md',
                                      'paused/autopilot-control.json', 'paused/reports/workers.sqlite'],
                'continue-phase': ['autopilot.md', 'autopilot-control.json', 'worker-complete.txt'],
                'measure-baseline': ['samples.json', 'measure.py', 'measurement.json', 'measurement-execution.json']}[name]
    try:
        if any((workspace / path).exists() and not (workspace / path).resolve().is_relative_to(workspace)
               for path in retained):
            raise ValueError('Fixture artifact escapes its workspace')
        if name == 'source-drift':
            result = json.loads((workspace / 'comparison.json').read_text())
            passed = result['status'] == 'mismatch' and result['changed'] == ['scripts/source_identity.py']
            passed &= all(sha(workspace / path) == value for path, value in oracle['inputs'].items())
        elif name == 'pause-provenance':
            passed = (json.loads((workspace / 'decision.json').read_text()).get('paused') is False
                      and sha(run_control.location(workspace / 'autopilot.md')) == oracle['control_hash']
                      and not run_control.read(workspace / 'autopilot.md')['events'][-1].get('terminal'))
        elif name == 'stale-report':
            passed = (json.loads((workspace / 'verdict.json').read_text()).get('accepted') is False
                      and sha(workspace / 'collected.json') == oracle['input_hash']
                      and sha(workspace / 'report.md') == oracle['report_hash'])
        elif name == 'paused-completion':
            db = worker_lifecycle.connect(workspace / 'paused/reports/workers.sqlite')
            try:
                saved = worker_lifecycle.get(db, 'worker/1')
                acknowledged = db.execute('SELECT acknowledged FROM events WHERE id=1').fetchone()[0]
                passed = (acknowledged == 1 and not saved.get('accepted') and not saved.get('declined')
                          and run_control.digest(json.dumps(saved, sort_keys=True)) == oracle['attempt_hash']
                          and saved['result']['status'] == 'completed'
                          and sha(workspace / 'completed.md') == oracle['report_hash']
                          and sha(run_control.location(workspace / 'paused/autopilot.md')) == oracle['control_hash']
                          and run_control.read(workspace / 'paused/autopilot.md')['events'][-1]['terminal'] == 'paused-by-user')
            finally:
                db.close()
        elif name == 'continue-phase':
            state = run_control.read(workspace / 'autopilot.md')
            fields = run_control.state_files.state_fields(state['card_text'])
            passed = fields['phase'] == 'refine' and not fields['terminal'] and state['events'][-1].get('phase') == 'refine'
        elif name == 'measure-baseline':
            actual = json.loads((workspace / 'measurement.json').read_text())
            observed = json.loads((workspace / 'measurement-execution.json').read_text())
            passed = (actual == observed and actual['mean'] == oracle['mean'] and bool(actual.get('sample_id'))
                      and sha(workspace / 'samples.json') == oracle['input_hash']
                      and sha(workspace / 'measure.py') == oracle['driver_hash'])
    except (OSError, ValueError, KeyError, TypeError):
        passed = False
    copies = {}
    for name_to_keep in retained:
        path = workspace / name_to_keep
        if path.is_file() and path.resolve().is_relative_to(workspace):
            target = evidence / 'observations' / name_to_keep
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copies[name_to_keep] = sha(target)
    put(evidence / 'observed.json', copies)
    put(evidence / 'grade.json', {'feature': name, 'passed': bool(passed), 'oracle': 'independent retained fixture post-state'})
    return 0 if passed else 1


if __name__ == '__main__':
    stage, name, work, retained = sys.argv[1:]
    workspace, evidence = Path(work).resolve(), Path(retained).resolve()
    if stage == 'launch':
        launch(name, workspace, evidence)
    elif stage == 'doctor':
        assert (evidence / 'oracle.json').is_file()
    elif stage == 'drive':
        drive(name, workspace)
    elif stage == 'evidence':
        raise SystemExit(grade(name, workspace, evidence))
    elif stage == 'cleanup':
        print('No persistent target processes; workspace belongs to the outer verifier.')
