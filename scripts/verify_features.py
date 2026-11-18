#!/usr/bin/env python3
"""Run a feature's launch/doctor/drive/evidence/cleanup contract with retained proof."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_identity  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STEPS = ('launch', 'doctor', 'drive', 'evidence', 'cleanup')


def expand(argv, values):
    if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and arg for arg in argv):
        raise ValueError('Each verification step requires a nonempty literal argv')
    result = []
    for arg in argv:
        for key, value in values.items():
            arg = arg.replace('{' + key + '}', str(value))
        result.append(arg)
    return result


def verify(feature, evidence, driver=None):
    """Grade actual post-state; setup gaps and invalid retained evidence cannot pass."""
    evidence = Path(evidence).resolve()
    timeout = feature.get('timeout', 60)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Feature timeout must be finite and positive')
    if not isinstance(feature.get('name'), str) or not re.fullmatch(r'[a-z0-9_-]+', feature['name']):
        raise ValueError('Feature name must be a safe path segment')
    evidence.mkdir(parents=True, exist_ok=False)
    result = dict(feature=feature['name'], status='blocked', steps={}, started_at=time.time(),
                  evidence=str(evidence), mode='agent' if driver else 'scripted')
    with tempfile.TemporaryDirectory(prefix='zloop-feature-') as tmp:
        result['workspace'] = tmp
        values = dict(workspace=tmp, evidence=evidence, plugin_root=ROOT, python=sys.executable,
                      feature=feature['name'])
        task = feature.get('task', '')
        for key, value in values.items():
            task = task.replace('{' + key + '}', str(value))
        values['task'] = task
        if driver and not task.strip():
            raise ValueError('Agent verification needs an explicit feature task')
        commands = {step: expand(driver if step == 'drive' and driver else feature[step], values) for step in STEPS}
        result['task'] = task
        cleanup_inputs = {arg: hashlib.sha256(Path(arg).read_bytes()).hexdigest() for arg in commands['cleanup']
                          if arg.startswith('/') and Path(arg).is_file()}
        cleanup_runtime = source_identity.snapshot()['digest']
        frozen = {}
        try:
            for step in STEPS[:-1]:
                if step == 'drive':
                    inputs = [Path(arg) for command in commands.values() for arg in command
                              if arg.startswith('/') and Path(arg).suffix in ('.py', '.json') and Path(arg).is_file()]
                    inputs += [Path(expand([path], values)[0]) for path in feature.get('protected_inputs', [])]
                    frozen = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
                    result['input_hashes'] = frozen
                    result['runtime_identity'] = source_identity.snapshot()['digest']
                command = [sys.executable, '-B', str(ROOT / 'scripts/run_check.py'), '--timeout', str(timeout),
                           '--log', str(evidence / f'{step}.log'), '--', *commands[step]]
                process = subprocess.run(command, cwd=tmp, capture_output=True, text=True)
                try:
                    summary = json.loads(process.stdout)
                except ValueError:
                    summary = {'exit_code': process.returncode or 125, 'runner_error': True}
                result['steps'][step] = summary
                if summary.get('exit_code') != 0:
                    result['status'] = 'failed' if step == 'evidence' and summary.get('exit_code') != 125 else 'blocked'
                    break
                if step == 'drive':
                    scope_changed = (any(not Path(path).is_file() or hashlib.sha256(Path(path).read_bytes()).hexdigest() != value
                                         for path, value in frozen.items())
                                     or source_identity.snapshot()['digest'] != result['runtime_identity'])
                    if scope_changed:
                        result.update(status='blocked', reason='Protected verification input changed during drive')
                        break
            else:
                result['status'] = 'passed'
        finally:
            cleanup_changed = (any(not Path(path).is_file() or hashlib.sha256(Path(path).read_bytes()).hexdigest() != value
                                   for path, value in cleanup_inputs.items())
                               or source_identity.snapshot()['digest'] != cleanup_runtime)
            if cleanup_changed:
                result['steps']['cleanup'] = {'exit_code': 125, 'reason': 'Changed cleanup inputs require operator reconciliation'}
            else:
                command = [sys.executable, '-B', str(ROOT / 'scripts/run_check.py'), '--timeout', str(timeout),
                           '--log', str(evidence / 'cleanup.log'), '--', *commands['cleanup']]
                process = subprocess.run(command, cwd=tmp, capture_output=True, text=True)
                try:
                    result['steps']['cleanup'] = json.loads(process.stdout)
                except ValueError:
                    result['steps']['cleanup'] = {'exit_code': process.returncode or 125, 'runner_error': True}
            if result['steps']['cleanup'].get('exit_code') != 0 and result['status'] == 'passed':
                result['status'] = 'blocked'
        missing = []
        for name in feature.get('required_artifacts', []):
            path = evidence / name
            if not path.resolve().is_relative_to(evidence) or not path.is_file():
                missing.append(name)
        if missing:
            result['missing_artifacts'] = missing
            if result['status'] == 'passed':
                result['status'] = 'blocked'
        if any(not Path(path).is_file() or hashlib.sha256(Path(path).read_bytes()).hexdigest() != value
               for path, value in frozen.items()):
            result.update(status='blocked', reason='Protected input did not survive verification and cleanup')
        result['artifact_hashes'] = {name: hashlib.sha256((evidence / name).read_bytes()).hexdigest()
                                    for name in feature.get('required_artifacts', []) if name not in missing}
    result['finished_at'] = time.time()
    (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--feature', action='append')
    parser.add_argument('--driver', type=Path, help='JSON argv for an agent driver; uses {workspace} and {task}')
    parser.add_argument('--trials', type=int, default=1)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text())
        features = manifest['features']
        if args.trials < 1 or not isinstance(features, list):
            raise ValueError('Require a feature list and at least one trial')
        selected = [f for f in features if not args.feature or f['name'] in args.feature]
        if not selected or args.feature and set(args.feature) - {f['name'] for f in selected}:
            raise ValueError('Unknown or empty feature selection')
        driver = json.loads(args.driver.read_text()) if args.driver else None
        results = [verify(feature, args.evidence / feature['name'] / str(trial), driver)
                   for feature in selected for trial in range(1, args.trials + 1)]
        report = {'manifest': str(args.manifest.resolve()),
                  'manifest_sha256': hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                  'driver': str(args.driver.resolve()) if args.driver else None,
                  'trials': args.trials, 'results': results}
        (args.evidence / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({f'{r["feature"]}:{index}': r['status'] for index, r in enumerate(results, 1)}, indent=2))
        return int(any(r['status'] != 'passed' for r in results))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    sys.exit(main())
