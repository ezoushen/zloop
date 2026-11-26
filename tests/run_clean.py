#!/usr/bin/env python3
"""Run the suite runner with the arguments given here, under only a minimal tool path and the nested slot marker.

`--parity` runs it as a busy hosted runner would, on the Python that launched it: Python processes cannot look up
network hosts (loopback still resolves), the slot budget is twice the CPU count unless ZLOOP_TEST_SLOTS sets one, and
the run first prints its tool versions and refuses a git older than GIT_FLOOR."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

GIT_FLOOR = (2, 40)  # `git log --attr-source` in scripts/next_tickets.py.


def tool_versions(path):
    """The parity lane's version line for the tools on `path`; exits when git is older than GIT_FLOOR."""
    def version(name):
        try:
            return subprocess.run([name, '--version'], capture_output=True, text=True, env={'PATH': path}).stdout.strip()
        except OSError:
            return ''
    git = version('git')
    found = re.search(r'(\d+)\.(\d+)', git)
    if not found or tuple(map(int, found.groups())) < GIT_FLOOR:
        sys.exit(f'parity lane: {git or "no git"} is older than git {".".join(map(str, GIT_FLOOR))}, '
                 'which the scripts need')
    return f'parity lane: {git}, Python {sys.version.split()[0]}, Node {version("node") or "absent"}'


def main():
    """Replace this process with the suite runner, given this command line, using a clean shell environment."""
    runner = Path(__file__).resolve().with_name('run_all.py')
    selected = {str(Path(binary).absolute().parent) for name in ('git', 'claude', 'node')
                if (binary := shutil.which(name))}
    directories = [directory for item in os.get_exec_path()
                   if (directory := str(Path(item).absolute())) in selected]
    directories.extend(os.defpath.split(os.pathsep))
    path = os.pathsep.join(dict.fromkeys(directories))
    environment = ['PATH=' + path]
    if os.environ.get('ZLOOP_TEST_SLOT_HELD'):
        environment.append('ZLOOP_TEST_SLOT_HELD=1')
    args = sys.argv[1:]
    if '--parity' in args:
        args.remove('--parity')
        print(tool_versions(path), flush=True)
        environment += [f'PYTHON={sys.executable}', f'PYTHONPATH={Path(__file__).resolve().with_name("parity_site")}',
                        f'ZLOOP_TEST_SLOTS={os.environ.get("ZLOOP_TEST_SLOTS") or 2 * (os.cpu_count() or 1)}']
    os.execv('/usr/bin/env', ['env', '-i', *environment, sys.executable, str(runner), *args])


if __name__ == '__main__':
    main()
