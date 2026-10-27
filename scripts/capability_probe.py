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
import time

NEEDS = ('network', 'docker', 'setuid', 'repo-write', 'dev-fd')  # What a ticket's `needs:` may name.
TIMEOUT = 15  # seconds one probe command may run


def run(argv, stdin=''):
    """The finished command, or the reason it could not run."""
    path = shutil.which(argv[0])
    if not path:
        return None, f'{argv[0]} is not on PATH'
    try:
        return subprocess.run([path, *argv[1:]], input=stdin, capture_output=True, text=True, timeout=TIMEOUT), None
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
    """Write as a commit does: a file in the worktree's git dir (its index), then a commit object and a branch ref through
    git, so the repository's object and ref stores are judged wherever and however they are kept. The ref is deleted
    again; the unreferenced commit object stays until git prunes it."""
    process, why = run(['git', 'rev-parse', '--absolute-git-dir'])
    if why or process.returncode:
        return 'unknown', why or f'`git rev-parse --absolute-git-dir` exited {process.returncode}: {detail(process)}'
    git_dir = process.stdout.strip()
    try:
        with tempfile.NamedTemporaryFile(dir=git_dir):
            pass
    except OSError as error:
        return 'unavailable', f'cannot create a file in {git_dir}: {error}'
    ref = f'refs/heads/zloop-probe-{os.getpid()}-{os.urandom(4).hex()}'
    identity = ['-c', 'user.name=zloop probe', '-c', 'user.email=probe@zloop.invalid']
    tree, why = git_value(['git', 'mktree'])
    if why:
        return why
    commit, why = git_value(['git', *identity, 'commit-tree', tree, '-m', f'zloop capability probe {time.time()}'])
    if why:
        return why
    # An empty old value creates the ref only if it is absent, and deleting it names the probe's commit, so no other
    # branch is ever moved or removed.
    _, why = git_value(['git', 'update-ref', ref, commit, ''])
    if why:
        return why
    _, left = git_value(['git', 'update-ref', '-d', ref, commit])
    # A commit lands even where the deletion cannot lock packed-refs, so a ref left behind is reported, not a failure.
    kept = f'; could not delete {ref} again: {left[1]}' if left else ', then deleted the ref'
    return 'available', f'created a file in {git_dir}, and a commit and the branch ref {ref}{kept}'


def git_value(argv):
    """The command's printed value, or (status, evidence) for a step that could not run (unknown) or failed
    (unavailable). The commit identity stays out of the evidence."""
    process, why = run(argv)
    if why:
        return None, ('unknown', why)
    if process.returncode:
        shown = ' '.join(arg for arg in argv if arg != '-c' and not arg.startswith('user.'))
        return None, ('unavailable', f'`{shown}` exited {process.returncode}: {detail(process)}')
    return process.stdout.strip(), None


def ticket_needs(ticket):
    """The `needs:` a ticket declares, given the assignment's `ticket` value. A value that is not an absolute path
    (a remote ID or URL) has none; an absolute path to a file that is missing raises ValueError."""
    if not ticket or not Path(ticket).is_absolute():
        return []
    path = Path(ticket)
    if not path.is_file():
        raise ValueError(f'The assignment names a ticket file that does not exist: {ticket}')
    front = re.match(r'---\n(.*?)\n---\n', path.read_text(errors='replace'), re.S)
    declared = re.search(r'^needs:[ \t]*\[(.*)\]', front.group(1), re.M) if front else None
    return [need.strip() for need in declared.group(1).split(',') if need.strip()] if declared else []


def unmet(needs, reports, worktree):
    """Why the probe reports do not show every need available in worktree, else None. Only a report taken in
    worktree or inside it counts; when several did, the latest one is judged."""
    if not needs:
        return None
    names = ', '.join(needs)
    here = Path(worktree).resolve()
    mine = [report for report in reports if isinstance(report, dict) and text_path(report.get('worktree'))
            and (Path(report['worktree']).resolve() == here or here in Path(report['worktree']).resolve().parents)]
    if not mine:
        return f'The ticket needs {names}: no probe of this worktree in the result evidence (run capability_probe.py there)'
    unknown = [need for need in needs if need not in NEEDS]
    if unknown:
        return f'The ticket needs {", ".join(unknown)}: it is not a capability the probe checks'
    found = {need: (mine[-1].get(need) or {}).get('status', 'missing') for need in needs}
    short = {need: status for need, status in found.items() if status != 'available'}
    if not short:
        return None
    return (f'The ticket needs {", ".join(short)}: the probe reports '
            + ', '.join(f'{need} {status}' for need, status in short.items()))


def text_path(value):
    return isinstance(value, str) and Path(value).is_absolute()


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
