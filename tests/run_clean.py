#!/usr/bin/env python3
"""Run the full suite with only a minimal tool path and the nested slot marker."""
import os
from pathlib import Path
import shutil
import sys


def main():
    """Replace this process with the default suite using a clean shell environment."""
    runner = Path(__file__).resolve().with_name('run_all.py')
    directories = []
    if git := shutil.which('git'):
        directories.append(str(Path(git).absolute().parent))
    directories.extend(os.defpath.split(os.pathsep))
    for name in ('claude', 'node'):
        if binary := shutil.which(name):
            directories.append(str(Path(binary).absolute().parent))
    environment = ['PATH=' + os.pathsep.join(dict.fromkeys(directories))]
    if os.environ.get('ZLOOP_TEST_SLOT_HELD'):
        environment.append('ZLOOP_TEST_SLOT_HELD=1')
    os.execv('/usr/bin/env', ['env', '-i', *environment, sys.executable, str(runner)])


if __name__ == '__main__':
    main()
