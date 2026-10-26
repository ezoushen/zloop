#!/usr/bin/env python3
"""Run every tests/test_*.py regression script in parallel, at most one per CPU at a time across all runs.

usage: tests/run_all.py

Uses the plugin environment's .venv (the main checkout's, from a worktree) unless
PYTHON names another interpreter. Concurrent runs on the machine share one budget of
ZLOOP_TEST_SLOTS scripts (default: the CPU count), held as file locks in ZLOOP_TEST_SLOT_DIR
(default: /tmp/zloop-test-slots-<uid>); a lock vanishes with its run, so a killed run frees its slots. Prints each failing script's output tail and
exits 1 when any script fails. Native checks (ZLOOP_NATIVE=1) stay opt-in; see docs/development.md.
"""
import fcntl
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
# A Git hook exports these; the tests' temporary repositories must not inherit them.
GIT_VARIABLES = ('GIT_DIR', 'GIT_INDEX_FILE', 'GIT_WORK_TREE', 'GIT_PREFIX', 'GIT_OBJECT_DIRECTORY', 'GIT_COMMON_DIR')


def interpreter(env):
    if os.environ.get('PYTHON'):
        return os.environ['PYTHON']
    common = subprocess.run(['git', 'rev-parse', '--git-common-dir'], cwd=ROOT, env=env,
                            capture_output=True, text=True).stdout.strip()
    main = (ROOT / common).resolve().parent if common else ROOT
    for candidate in (ROOT / '.venv/bin/python', main / '.venv/bin/python'):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which('python3') or sys.executable


def try_slot(directory, count):
    """Return an open file holding a slot's lock, or None when all `count` slots are taken."""
    for index in range(count):
        slot = open(directory / f'slot-{index}', 'w')
        try:
            fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return slot
        except OSError:
            slot.close()
    return None


def main():
    # Exit through the context manager on a hook's teardown, so the log directory is removed.
    for number in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, lambda number, frame: sys.exit(128 + number))
    env = {key: value for key, value in os.environ.items() if key not in GIT_VARIABLES}
    python = interpreter(env)
    with tempfile.TemporaryDirectory() as logs:
        slots = int(os.environ.get('ZLOOP_TEST_SLOTS') or os.cpu_count() or 1)
        directory = Path(os.environ.get('ZLOOP_TEST_SLOT_DIR') or f'/tmp/zloop-test-slots-{os.getuid()}')
        directory.mkdir(parents=True, exist_ok=True)
        jobs = []
        held = []
        for test in sorted((ROOT / 'tests').glob('test_*.py')):
            # One script per slot: starting all of them at once starves those that assert a deadline.
            while True:
                for pair in [pair for pair in held if pair[1].poll() is not None]:
                    held.remove(pair)
                    pair[0].close()
                slot = try_slot(directory, slots)
                if slot:
                    break
                time.sleep(0.05)
            log = open(Path(logs) / f'{test.stem}.log', 'w+', errors='replace')
            process = subprocess.Popen([python, '-B', str(test)], stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, env=env)
            jobs.append((test.stem, log, process))
            held.append((slot, process))
        failed = False
        for name, log, process in jobs:
            if process.wait():
                failed = True
                log.seek(0)
                print(f'FAIL {name}')
                print(''.join(log.readlines()[-20:]), end='')
            log.close()
    for slot, _ in held:
        slot.close()
    if not failed:
        print(f'{len(jobs)} regression scripts passed ({python})')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
