#!/usr/bin/env python3
"""Run an explicitly selected Bash executor with a structured assignment on stdin."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import agent_models as models


def run(execution, assignment):
    if execution.get('executor_type') != 'bash':
        raise ValueError('Expected a resolved bash executor')
    policy = {'mode': execution['model_mode']}
    for source, target in (('model', 'model'), ('effort', 'thinking')):
        if source in execution['settings']:
            policy[target] = execution['settings'][source]
    models.validate_execution_config({'agents': {execution['agent']: {
        'executor': {'type': 'bash', 'config': execution['executor_config']},
        'models': policy}}}, 'execution')
    for field in ('assignment_id', 'worktree', 'agent_file', 'job_file', 'head', 'prompt'):
        if not isinstance(assignment.get(field), str) or not assignment[field].strip():
            raise ValueError(f'assignment.{field} must be a nonempty string')
    worktree = Path(assignment['worktree'])
    if not worktree.is_absolute() or not worktree.is_dir():
        raise ValueError('assignment.worktree must be an existing absolute directory')
    for field in ('agent_file', 'job_file'):
        if not Path(assignment[field]).is_absolute() or not Path(assignment[field]).is_file():
            raise ValueError(f'assignment.{field} must be an existing absolute file')
    request = {'assignment': assignment, 'models': policy}
    # argv remains literal: prompts and model selections travel through stdin, never shell expansion.
    argv = execution['executor_config']['argv']
    scope = models.DEFINITIONS['agents'][execution['agent']]['writes']
    if sys.platform == 'darwin':
        # macOS Seatbelt bounds the worker's writes to its scope (the bash executor is zloop's own
        # subprocess, so the profile wraps here); an unavailable sandbox records its fallback reason, never silent.
        import seatbelt
        # The worker interpreter's tree must stay readable under read bounding: the plugin's own venv, the real
        # interpreter behind its symlinks, and the plugin root.
        plugin_root = Path(__file__).resolve().parents[1]
        real_python = Path(os.path.realpath(sys.executable))
        venv = Path(sys.executable).resolve().parents[1]
        extra = [str(plugin_root), str(venv), str(real_python.parents[1])]
        wrapped = seatbelt.wrap(argv, scope, worktree=str(worktree), reports=str(execution.get('reports') or worktree),
                                tag=execution.get('attempt', 'worker').replace('/', '-'), extra_reads=extra)
        argv, fallback = wrapped['argv'], wrapped.get('fallback_reason')
    else:
        fallback = None
    import os as _os
    env = dict(_os.environ, ZLOOP_TEST_REPORTS=str(execution.get('reports') or worktree))
    process = subprocess.run(argv, cwd=worktree, env=env,
                             input=json.dumps(request), text=True, stdout=subprocess.PIPE)
    if process.returncode:
        raise ValueError(f'Executor exited with status {process.returncode}'
                         + (f' (sandbox fallback: {fallback})' if fallback else '') + '; reconcile before retrying')
    try:
        outcome = json.loads(process.stdout)
    except ValueError as error:
        raise ValueError('Executor returned invalid JSON; reconcile before retrying') from error
    if not isinstance(outcome, dict) or outcome.get('assignment_id') != assignment['assignment_id']:
        raise ValueError('Executor returned a missing or mismatched assignment ID')
    if outcome.get('status') not in ('completed', 'blocked', 'failed', 'unknown'):
        raise ValueError('Executor did not return a terminal or uncertain execution status')
    if not isinstance(outcome.get('report'), str) or not outcome['report'].strip():
        raise ValueError('Executor returned no report or explanation')
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execution', type=Path, required=True, help='saved resolver JSON')
    parser.add_argument('--assignment', type=Path, required=True, help='assignment JSON')
    args = parser.parse_args()
    try:
        outcome = run(json.loads(args.execution.read_text()), json.loads(args.assignment.read_text()))
        print(json.dumps(outcome, indent=2))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    main()
