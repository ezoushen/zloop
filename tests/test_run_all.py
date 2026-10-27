#!/usr/bin/env python3
"""The suite runner runs every regression script, reports each failure's output tail and exits 1 on any failure."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import support
import run_all

RUNNER = support.ROOT / 'tests/run_all.py'


class RunAll(unittest.TestCase):
    def outermost(self, extra):
        """The environment of a run that is no suite's script: a suite running this file must not hand its slot pool down."""
        env = {key: value for key, value in os.environ.items() if not key.startswith('ZLOOP_TEST_SLOT')}
        slots = tempfile.TemporaryDirectory(prefix='run all pool ')
        self.addCleanup(slots.cleanup)
        return {**env, 'ZLOOP_TEST_SLOT_DIR': slots.name, **extra}

    @staticmethod
    def stop_group(runner):
        """Stop the runner's process group, scripts included, as a hook's teardown would."""
        try:
            os.killpg(runner.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):  # The group is gone, or only its reaped leader remains.
            pass
        runner.wait()

    def suite(self, scripts, env=None):
        directory = tempfile.TemporaryDirectory(prefix='run all ')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / 'tests').mkdir()
        shutil.copy(RUNNER, root / 'tests/run_all.py')
        for name, body in scripts.items():
            (root / 'tests' / name).write_text(body)
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        # A Git hook exports GIT_DIR; the runner must not pass it to the scripts.
        env = self.outermost(dict(PYTHON=sys.executable, GIT_DIR=str(root / '.git'), **(env or {})))
        self.root = root
        return subprocess.run([sys.executable, str(root / 'tests/run_all.py')], env=env,
                              capture_output=True, text=True, timeout=60)

    def test_every_script_passing_exits_zero_and_counts_them(self):
        result = self.suite({'test_one.py': 'pass\n', 'test_two.py': 'pass\n', 'helper.py': 'raise SystemExit(1)\n'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'2 regression scripts passed ({sys.executable})', result.stdout)

    def test_a_failure_exits_one_and_prints_its_name_and_output_tail(self):
        result = self.suite({'test_good.py': 'pass\n',
                             'test_bad.py': 'print("first line")\nprint("why it broke")\nraise SystemExit(3)\n'})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('FAIL test_bad', result.stdout)
        self.assertIn('why it broke', result.stdout)
        self.assertNotIn('FAIL test_good', result.stdout)
        self.assertNotIn('passed', result.stdout)

    def test_without_python_set_a_worktree_uses_the_main_checkout_environment(self):
        main = Path(tempfile.mkdtemp(prefix='run all main '))
        self.addCleanup(shutil.rmtree, main)
        subprocess.run(['git', 'init', '-q', str(main)], check=True)
        subprocess.run(['git', '-C', str(main), '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false',
                        'commit', '-q', '--allow-empty', '-m', 'base'], check=True)
        (main / '.venv/bin').mkdir(parents=True)
        (main / '.venv/bin/python').symlink_to(sys.executable)
        tree = main.parent / f'{main.name} tree'
        subprocess.run(['git', '-C', str(main), 'worktree', 'add', '-q', '--detach', str(tree)], check=True)
        self.addCleanup(shutil.rmtree, tree, True)
        (tree / 'tests').mkdir()
        shutil.copy(RUNNER, tree / 'tests/run_all.py')
        (tree / 'tests/test_one.py').write_text('pass\n')
        env = {key: value for key, value in self.outermost({}).items() if key not in ('PYTHON', 'GIT_DIR')}
        result = subprocess.run([sys.executable, str(tree / 'tests/run_all.py')], env=env, cwd=main.parent,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'1 regression scripts passed ({main.resolve() / ".venv/bin/python"})', result.stdout)

    def test_a_failure_with_undecodable_output_is_still_reported(self):
        result = self.suite({'test_bytes.py': 'import sys\nsys.stdout.buffer.write(b"bad \\xff byte\\n")\nraise SystemExit(1)\n',
                             'test_other.py': 'print("second failure")\nraise SystemExit(1)\n'})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('FAIL test_bytes', result.stdout)
        self.assertIn('second failure', result.stdout)

    def test_a_terminated_run_removes_its_logs(self):
        root = Path(tempfile.mkdtemp(prefix='run all term '))
        self.addCleanup(shutil.rmtree, root)
        (root / 'tests').mkdir()
        (root / 'tmp').mkdir()
        shutil.copy(RUNNER, root / 'tests/run_all.py')
        started = root / 'started'
        (root / 'tests/test_slow.py').write_text(f'import time\nopen({str(started)!r}, "w").close()\ntime.sleep(30)\n')
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        runner = subprocess.Popen([sys.executable, str(root / 'tests/run_all.py')], start_new_session=True,
                                  env=self.outermost(dict(PYTHON=sys.executable, TMPDIR=str(root / 'tmp'))))
        self.addCleanup(self.stop_group, runner)
        deadline = time.monotonic() + 10
        # The runner opens a unit's log before starting it; wait for the script itself, which killpg then stops.
        while not (started.exists() and list((root / 'tmp').glob('*/*.log'))):
            self.assertLess(time.monotonic(), deadline, 'the run never started')
            time.sleep(0.05)
        runner.send_signal(signal.SIGTERM)
        self.assertEqual(runner.wait(timeout=10), 128 + signal.SIGTERM)
        self.stop_group(runner)  # the sleeping test, as a hook's teardown would
        self.assertEqual(list((root / 'tmp').iterdir()), [])

    def test_no_more_scripts_run_at_once_than_the_machine_has_cpus(self):
        # Rule: starting every script at once starves the timing-sensitive ones, so at most one per CPU runs.
        cpus = os.cpu_count()
        running = Path(tempfile.mkdtemp(prefix='run all running '))
        self.addCleanup(shutil.rmtree, running)
        script = ('import os,time\nfrom pathlib import Path\n'
                  f'running = Path({str(running)!r})\n'
                  '(running / str(os.getpid())).touch()\n'
                  'seen = sum(1 for path in running.iterdir() if not path.name.startswith("seen-"))\n'
                  '(running / f"seen-{os.getpid()}").write_text(str(seen))\n'
                  'time.sleep(0.5)\n'
                  '(running / str(os.getpid())).unlink()\n')
        result = self.suite({f'test_{index:02}.py': script for index in range(cpus + 4)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        seen = [int(path.read_text()) for path in running.glob('seen-*')]
        self.assertEqual(len(seen), cpus + 4)
        self.assertLessEqual(max(seen), cpus)

    def start(self, scripts, env):
        """Start a runner over `scripts` in its own copy of the tests directory, without waiting for it."""
        root = Path(tempfile.mkdtemp(prefix='run all start '))
        self.addCleanup(shutil.rmtree, root)
        (root / 'tests').mkdir()
        shutil.copy(RUNNER, root / 'tests/run_all.py')
        for name, body in scripts.items():
            (root / 'tests' / name).write_text(body)
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        process = subprocess.Popen([sys.executable, str(root / 'tests/run_all.py')], env=self.outermost(dict(env, PYTHON=sys.executable)),
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        # Its units too: a failed test must not leave one waiting for a file the cleanup removes.
        self.addCleanup(self.stop_group, process)
        return process

    def test_concurrent_runs_share_one_slot_budget_instead_of_one_each(self):
        # Rule: the per-CPU cap spans every run on the machine, so two runs together never exceed the slot count.
        slots = 2
        scratch = Path(tempfile.mkdtemp(prefix='run all shared '))
        self.addCleanup(shutil.rmtree, scratch)
        running = scratch / 'running'
        running.mkdir()
        script = ('import os,time\nfrom pathlib import Path\n'
                  f'running = Path({str(running)!r})\n'
                  '(running / str(os.getpid())).touch()\n'
                  'seen = sum(1 for path in running.iterdir() if not path.name.startswith("seen-"))\n'
                  '(running / f"seen-{os.getpid()}").write_text(str(seen))\n'
                  'time.sleep(0.3)\n'
                  '(running / str(os.getpid())).unlink()\n')
        env = {'ZLOOP_TEST_SLOTS': str(slots), 'ZLOOP_TEST_SLOT_DIR': str(scratch / 'slots')}
        scripts = {f'test_{index}.py': script for index in range(4)}
        runs = [self.start(scripts, env), self.start(scripts, env)]
        outputs = [run.communicate(timeout=60)[0] for run in runs]
        self.assertEqual([run.returncode for run in runs], [0, 0], outputs)
        seen = [int(path.read_text()) for path in running.glob('seen-*')]
        self.assertEqual(len(seen), 8)
        self.assertLessEqual(max(seen), slots)
        self.assertEqual(max(seen), slots, 'the runs never used the whole budget')

    def test_a_killed_run_does_not_keep_its_slots(self):
        # Rule: a slot is released when its run dies, so a later run is not blocked by a dead run's slots.
        scratch = Path(tempfile.mkdtemp(prefix='run all killed '))
        self.addCleanup(shutil.rmtree, scratch)
        env = {'ZLOOP_TEST_SLOTS': '1', 'ZLOOP_TEST_SLOT_DIR': str(scratch / 'slots')}
        started = scratch / 'started'
        holder = self.start({'test_hold.py': 'import os,time\nfrom pathlib import Path\n'
                             f'Path({str(started)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n'}, env)
        deadline = time.monotonic() + 10
        while not started.exists():
            self.assertLess(time.monotonic(), deadline, 'the holding run never started its script')
            time.sleep(0.05)
        holder.kill()
        holder.wait(timeout=10)
        self.addCleanup(os.kill, int(started.read_text()), signal.SIGKILL)
        waiter = self.start({'test_after.py': 'pass\n'}, env)
        output = waiter.communicate(timeout=10)[0]
        self.assertEqual(waiter.returncode, 0, output)

    def test_a_run_started_by_a_script_holding_a_slot_does_not_wait_for_the_pool(self):
        # Rule: a run nested in a suite script competes only for its own budget, so a full outer pool cannot starve it.
        scratch = Path(tempfile.mkdtemp(prefix='run all nested '))
        self.addCleanup(shutil.rmtree, scratch)
        env = {'ZLOOP_TEST_SLOTS': '1', 'ZLOOP_TEST_SLOT_DIR': str(scratch / 'slots')}
        started = scratch / 'started'
        self.start({'test_hold.py': 'import os,time\nfrom pathlib import Path\n'
                    f'Path({str(started)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n'}, env)
        deadline = time.monotonic() + 10
        while not started.exists():
            self.assertLess(time.monotonic(), deadline, 'the holding run never started its script')
            time.sleep(0.05)
        self.addCleanup(os.kill, int(started.read_text()), signal.SIGKILL)
        nested = self.start({'test_inner.py': 'pass\n'}, dict(env, ZLOOP_TEST_SLOT_HELD='1'))
        output = nested.communicate(timeout=10)[0]
        self.assertEqual(nested.returncode, 0, output)

    def test_scripts_do_not_inherit_the_git_hook_environment(self):
        result = self.suite({'test_env.py': 'import os\nraise SystemExit(1 if "GIT_DIR" in os.environ else 0)\n'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_classes_of_one_script_run_as_separate_units_in_the_shared_budget(self):
        # Rule: a script's test classes are scheduled separately, so a script with two classes keeps two slots busy.
        scratch = Path(tempfile.mkdtemp(prefix='run all classes '))
        self.addCleanup(shutil.rmtree, scratch)
        script = ('import time, unittest\nfrom pathlib import Path\n'
                  f'marks = Path({str(scratch)!r})\n'
                  'def meet(mine, other):\n'
                  '    (marks / mine).touch()\n'
                  '    deadline = time.monotonic() + 8\n'
                  '    while not (marks / other).exists():\n'
                  '        assert time.monotonic() < deadline, other + " never ran beside " + mine\n'
                  '        time.sleep(0.02)\n'
                  'class Left(unittest.TestCase):\n'
                  '    def test_meets_right(self):\n        meet("left", "right")\n'
                  'class Right(unittest.TestCase):\n'
                  '    def test_meets_left(self):\n        meet("right", "left")\n'
                  'if __name__ == "__main__":\n    unittest.main()\n')
        result = self.suite({'test_pair.py': script}, {'ZLOOP_TEST_SLOTS': '2'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'1 regression scripts passed ({sys.executable})', result.stdout)

    def test_every_test_of_a_split_script_runs_exactly_once(self):
        # Rule: splitting a script into units neither drops nor repeats a test, including inherited and many-test classes.
        scratch = Path(tempfile.mkdtemp(prefix='run all once '))
        self.addCleanup(shutil.rmtree, scratch)
        log = scratch / 'ran'
        script = ('import os, unittest\n'
                  f'def record(name):\n    open({str(log)!r}, "a").write(f"{{name}} {{os.getpid()}}\\n")\n'
                  'class Base(unittest.TestCase):\n'
                  '    def test_base(self):\n        record(self.id().split(".", 1)[1])\n'
                  'class Child(Base):\n'
                  '    def test_child(self):\n        record(self.id().split(".", 1)[1])\n'
                  'class Many(unittest.TestCase):\n'
                  + ''.join(f'    def test_n{index:02}(self):\n        record(self.id().split(".", 1)[1])\n' for index in range(40))
                  + 'if __name__ == "__main__":\n    unittest.main()\n')
        result = self.suite({'test_split.py': script}, {'ZLOOP_TEST_SLOTS': '4'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = [line.split() for line in log.read_text().splitlines()]
        expected = sorted(['Base.test_base', 'Child.test_base', 'Child.test_child']
                          + [f'Many.test_n{index:02}' for index in range(40)])
        self.assertEqual(sorted(name for name, _ in rows), expected)
        self.assertGreater(len({pid for name, pid in rows if name.startswith('Many.')}), 1,
                           'a 40-test class ran in one process')

    def test_a_failing_class_is_reported_with_its_script_and_class_name(self):
        script = ('import unittest\n'
                  'class Fine(unittest.TestCase):\n    def test_ok(self):\n        pass\n'
                  'class Broken(unittest.TestCase):\n    def test_bad(self):\n        self.fail("why it broke")\n'
                  'if __name__ == "__main__":\n    unittest.main()\n')
        result = self.suite({'test_mixed.py': script})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('FAIL test_mixed.Broken', result.stdout)
        self.assertIn('why it broke', result.stdout)
        self.assertNotIn('FAIL test_mixed.Fine', result.stdout)
        self.assertNotIn('passed', result.stdout)

    def test_a_finished_unit_frees_its_slot_while_a_longer_one_still_runs(self):
        # Rule: once every unit has started, each slot is released when its unit ends, so a concurrent run can use it
        # during the slow tail instead of waiting for the whole run.
        scratch = Path(tempfile.mkdtemp(prefix='run all tail '))
        self.addCleanup(shutil.rmtree, scratch)
        pool, release = scratch / 'slots', scratch / 'release'
        env = {'ZLOOP_TEST_SLOTS': '2', 'ZLOOP_TEST_SLOT_DIR': str(pool)}
        # The short unit ends only once the long one has started, so no later launch can be what frees its slot; a wait
        # that runs out fails its unit, so a runner stalled before the second launch cannot pass. Both waits end within
        # a minute, so a test killed before its cleanup leaves no unit running for good.
        wait = ('deadline = time.monotonic() + 60\nwhile not Path({0!r}).exists() and time.monotonic() < deadline: time.sleep(0.02)\n'
                'if not Path({0!r}).exists(): raise SystemExit("no " + {0!r} + " within a minute")\n')
        holder = self.start({'test_a_short.py': ('import time\nfrom pathlib import Path\n'
                                                 f'Path({str(scratch / "short")!r}).touch()\n' + wait.format(str(scratch / 'long'))),
                             'test_b_long.py': ('import time\nfrom pathlib import Path\n'
                                                f'Path({str(scratch / "long")!r}).touch()\n' + wait.format(str(release)))}, env)
        self.addCleanup(release.touch)
        support.wait_until(lambda: (scratch / 'short').exists() and (scratch / 'long').exists(), 'both units started')

        def free():
            """Whether a slot of the pool can be taken right now (the lock is dropped again at once)."""
            slot = run_all.try_slot(pool, 2)
            if slot:
                slot.close()
            return slot is not None

        try:
            support.wait_until(free, 'the finished unit freed its slot while the long one ran', limit=20)
            self.assertIsNone(holder.poll(), 'the run ended before the long unit did')
        finally:
            release.touch()
        output = holder.communicate(timeout=30)[0]
        self.assertEqual(holder.returncode, 0, output)


if __name__ == '__main__':
    unittest.main()
