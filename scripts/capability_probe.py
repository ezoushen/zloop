#!/usr/bin/env python3
"""Probe the capabilities a ticket's checks may need, and print each as available, unavailable or unknown.

usage: capability_probe.py

Prints one JSON object: capability name to {"status", "evidence"}. A step that cannot run reports
`unknown`; only a step that ran and showed the capability reports `available`. Exits 0 either way.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

NEEDS = ('network', 'docker', 'setuid', 'repo-write', 'dev-fd')  # What a ticket's `needs:` may name.
TIMEOUT = 15  # seconds one probe command may run


def run(argv):
    """The finished command, or the reason it could not run."""
    path = shutil.which(argv[0])
    if not path:
        return None, f'{argv[0]} is not on PATH'
    try:
        return subprocess.run([path, *argv[1:]], capture_output=True, text=True, timeout=TIMEOUT), None
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f'{" ".join(argv)} could not run: {error}'


def command_probe(argv, judge):
    """Run argv and let judge(process) return (status, evidence) for a command that finished."""
    process, why = run(argv)
    return ('unknown', why) if why else judge(process)


def detail(process):
    return (process.stderr.strip() or process.stdout.strip() or 'no output').splitlines()[0]


def exit_status(argv):
    def judge(process):
        shown = f'`{" ".join(argv)}` exited {process.returncode}'
        return ('available', shown) if process.returncode == 0 else ('unavailable', f'{shown}: {detail(process)}')
    return judge


def setuid(process):
    value = process.stdout.partition(':')[2].strip()
    shown = f'/proc/self/status NoNewPrivs is {value!r}'
    return {'0': ('available', shown), '1': ('unavailable', shown)}.get(value, ('unknown', shown))


def dev_fd(process):
    shown = f'`bash -c "cat <(echo ok)"` exited {process.returncode} printing {process.stdout.strip()!r}'
    return ('available', shown) if process.returncode == 0 and process.stdout.strip() == 'ok' else ('unavailable', shown)


def repo_write():
    process, why = run(['git', 'rev-parse', '--absolute-git-dir'])
    if why or process.returncode:
        return 'unknown', why or f'`git rev-parse --absolute-git-dir` exited {process.returncode}: {detail(process)}'
    git_dir = process.stdout.strip()
    try:
        with tempfile.NamedTemporaryFile(dir=git_dir):
            pass
    except OSError as error:
        return 'unavailable', f'cannot create a file in {git_dir}: {error}'
    return 'available', f'created and removed a file in {git_dir}'


def probe():
    curl = ['curl', '-sS', '-o', os.devnull, '--max-time', '10', 'https://registry.npmjs.org/']
    docker = ['docker', 'info']
    steps = {
        'network': lambda: command_probe(curl, exit_status(curl)),
        'docker': lambda: command_probe(docker, exit_status(docker)),
        'setuid': lambda: command_probe(['grep', '-s', '^NoNewPrivs', '/proc/self/status'], setuid),
        'repo-write': repo_write,
        'dev-fd': lambda: command_probe(['bash', '-c', 'cat <(echo ok)'], dev_fd),
    }
    report = {}
    for name, step in steps.items():
        status, evidence = step()
        report[name] = {'status': status, 'evidence': evidence}
    return report


if __name__ == '__main__':
    print(json.dumps(probe(), indent=2))
