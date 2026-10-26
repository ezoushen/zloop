#!/usr/bin/env python3
"""Probe the capabilities a ticket's checks may need, and print each as available, unavailable or unknown.

usage: capability_probe.py

Prints one JSON object: capability name to {"status", "evidence"}, and `worktree`, the directory it ran in. A step that cannot run reports
`unknown`; only a step that ran and showed the capability reports `available`. Exits 0 either way.
"""
import json
import os
from pathlib import Path
import re
import shutil
import stat
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


def setuid():
    """Set the setuid bit on a temporary file and read it back, as a setuid check does."""
    try:
        with tempfile.NamedTemporaryFile() as file:
            try:
                os.chmod(file.name, 0o4755)
            except OSError as error:
                return 'unavailable', f'cannot set the setuid bit on {file.name}: {error}'
            mode = os.stat(file.name).st_mode
    except OSError as error:
        return 'unknown', f'cannot create a temporary file: {error}'
    if mode & stat.S_ISUID:
        return 'available', f'the setuid bit set on a temporary file read back (mode {mode & 0o7777:o})'
    return 'unavailable', f'the setuid bit set on a temporary file read back without the setuid bit (mode {mode & 0o7777:o})'


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


def ticket_needs(assignment):
    """The `needs:` a ticket declares, for the local ticket file an assignment's `ticket:` line names; else []."""
    named = re.search(r'^ticket:[ \t]*(.*\S)', assignment, re.M)
    path = Path(named.group(1)) if named else None
    if not path or not path.is_absolute() or not path.is_file():
        return []
    front = re.match(r'---\n(.*?)\n---\n', path.read_text(errors='replace'), re.S)
    declared = re.search(r'^needs:[ \t]*\[(.*)\]', front.group(1), re.M) if front else None
    return [need.strip() for need in declared.group(1).split(',') if need.strip()] if declared else []


def unmet(needs, report):
    """Why a probe report (None when none was recorded) does not show every need available, else None."""
    if not needs:
        return None
    names = ', '.join(needs)
    if not isinstance(report, dict):
        return f'The ticket needs {names}: startup records no probe (run capability_probe.py and record its output)'
    found = {need: (report.get(need) or {}).get('status', 'missing') for need in needs}
    short = {need: status for need, status in found.items() if status != 'available'}
    if not short:
        return None
    return (f'The ticket needs {", ".join(short)}: the recorded probe reports '
            + ', '.join(f'{need} {status}' for need, status in short.items()))


def probe():
    curl = ['curl', '-sS', '-o', os.devnull, '--max-time', '10', 'https://registry.npmjs.org/']
    docker = ['docker', 'info']
    steps = {
        'network': lambda: command_probe(curl, exit_status(curl)),
        'docker': lambda: command_probe(docker, exit_status(docker)),
        'setuid': setuid,
        'repo-write': repo_write,
        'dev-fd': lambda: command_probe(['bash', '-c', 'cat <(echo ok)'], dev_fd),
    }
    report = {}
    for name, step in steps.items():
        status, evidence = step()
        report[name] = {'status': status, 'evidence': evidence}
    report['worktree'] = str(Path.cwd().resolve())
    return report


if __name__ == '__main__':
    print(json.dumps(probe(), indent=2))
