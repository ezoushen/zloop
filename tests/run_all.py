#!/usr/bin/env python3
"""Run every tests/test_*.py regression script in parallel, split into units, at most one unit per CPU at a time across all runs.

usage: tests/run_all.py [tests/test_<name>.py ...]

Named scripts run alone, a focused run while editing; with none, every script runs, the full gate.

Uses the plugin environment's .venv (the main checkout's, from a worktree) unless
PYTHON names another interpreter. Concurrent runs on the machine share one budget of
ZLOOP_TEST_SLOTS units (default: the CPU count), held as file locks in ZLOOP_TEST_SLOT_DIR
(default: /tmp/zloop-test-slots-<uid>); a lock vanishes with its run, so a killed run frees its slots.
A run started by one of those scripts (ZLOOP_TEST_SLOT_HELD is set) counts its own units against a private
budget, so it never waits on the slot its parent script already holds. A unittest script is split into one unit per
test class, and a class of more than MAX_UNIT_TESTS tests into balanced shards of at most that many, so a script's
work spreads over the cores; any other script is one unit. Prints each failing unit's output tail, named
<script>.<class> for a split script, and exits 1 when any unit fails. Native checks (ZLOOP_NATIVE=1) stay opt-in; see docs/development.md.
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
# A class with more tests than this runs as several units; a smaller one runs whole.
MAX_UNIT_TESTS = 8
# Prints the `Class.method` id of each test the script's module defines, one per line, without running any.
COLLECT = """
import importlib.util, sys, unittest
sys.path.insert(0, sys.argv[1].rpartition('/')[0])
spec = importlib.util.spec_from_file_location('collected', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
def walk(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from walk(test)
        else:
            yield type(test).__qualname__ + '.' + test._testMethodName
print(*walk(unittest.defaultTestLoader.loadTestsFromModule(module)), sep='\\n')
"""
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


def units(stem, ids):
    """The (name, test names, test count) units of a script whose tests are `ids`, largest first."""
    classes = {}
    for test in ids:
        classes.setdefault(test.partition('.')[0], []).append(test)
    result = []
    for name, tests in classes.items():
        count = -(-len(tests) // MAX_UNIT_TESTS)
        if count == 1:
            result.append((f'{stem}.{name}', [name], len(tests)))
            continue
        size = -(-len(tests) // count)
        for number in range(count):
            shard = tests[number * size:(number + 1) * size]
            result.append((f'{stem}.{name}[{number + 1}/{count}]', shard, len(shard)))
    return sorted(result, key=lambda unit: -unit[2])


def chosen(paths):
    """The scripts `paths` name, or None after saying which path is no test script of this checkout."""
    tests = (ROOT / 'tests').resolve()
    scripts = set()
    for path in paths:
        script = Path(path).resolve()
        if not (script.is_file() and script.parent == tests and script.match('test_*.py')):
            print(f'run_all: not a tests/test_*.py script of this checkout: {path}', file=sys.stderr)
            return None
        scripts.add(script)
    return scripts


def main(paths=()):
    named = chosen(paths)
    if named is None:
        return 2
    # Exit through the context manager on a hook's teardown, so the log directory is removed.
    for number in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, lambda number, frame: sys.exit(128 + number))
    env = {key: value for key, value in os.environ.items() if key not in GIT_VARIABLES}
    python = interpreter(env)
    with tempfile.TemporaryDirectory() as logs:
        slots = int(os.environ.get('ZLOOP_TEST_SLOTS') or os.cpu_count() or 1)
        if os.environ.get('ZLOOP_TEST_SLOT_HELD'):
            directory = Path(logs) / 'slots'
        else:
            directory = Path(os.environ.get('ZLOOP_TEST_SLOT_DIR') or f'/tmp/zloop-test-slots-{os.getuid()}')
        directory.mkdir(parents=True, exist_ok=True)
        env['ZLOOP_TEST_SLOT_HELD'] = '1'
        held = []

        def release():
            """Drop the slot of every unit that has ended."""
            for pair in [pair for pair in held if pair[1].poll() is not None]:
                held.remove(pair)
                pair[0].close()

        def launch(argv, log):
            """Start argv once a slot is free, holding the slot until it exits."""
            while True:
                release()
                slot = try_slot(directory, slots)
                if slot:
                    break
                time.sleep(0.05)
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env)
            held.append((slot, process))
            return process

        scripts = sorted(named or (ROOT / 'tests').glob('test_*.py'))
        # One unit per slot: starting all of them at once starves those that assert a deadline.
        collectors = []
        for test in scripts:
            if 'unittest.main(' in test.read_text(errors='replace'):
                out = open(Path(logs) / f'{test.stem}.collect', 'w+', errors='replace')
                collectors.append((test, out, launch([python, '-B', '-c', COLLECT, str(test)], out)))
        planned = {}
        for test, out, process in collectors:
            ok = process.wait() == 0
            out.seek(0)
            ids = out.read().split() if ok else []
            out.close()
            planned[test] = units(test.stem, ids)
        pending = []
        for test in scripts:
            pending += planned.get(test) or [(test.stem, [], 1)]
        jobs = []
        for index, (name, names, _) in enumerate(sorted(pending, key=lambda unit: -unit[2])):
            log = open(Path(logs) / f'{index}.log', 'w+', errors='replace')
            test = ROOT / 'tests' / f"{name.partition('.')[0]}.py"
            jobs.append((name, log, launch([python, '-B', str(test), *names], log)))
        while held:
            release()
            time.sleep(0.05)
        failed = False
        for name, log, process in jobs:
            if process.wait():
                failed = True
                log.seek(0)
                print(f'FAIL {name}')
                print(''.join(log.readlines()[-20:]), end='')
            log.close()
    if not failed:
        print(f'{len(scripts)} regression scripts passed ({python})')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
