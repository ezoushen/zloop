#!/usr/bin/env python3
"""The suite runner runs every regression script, reports each failure's output tail and exits 1 on any failure."""
import json
import os
from pathlib import Path
import re
import shlex
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
CLEAN = support.ROOT / 'tests/run_clean.py'
PARITY_SITE = support.ROOT / 'tests/parity_site'


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

    def suite(self, scripts, env=None, args=(), *, clean=False):
        directory = tempfile.TemporaryDirectory(prefix='run all ')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / 'tests').mkdir()
        shutil.copy(RUNNER, root / 'tests/run_all.py')
        launcher = Path('tests/run_all.py')
        if clean:
            launcher = Path('tests/run_clean.py')
            shutil.copy(CLEAN, root / launcher)
            if PARITY_SITE.is_dir():
                shutil.copytree(PARITY_SITE, root / 'tests' / PARITY_SITE.name)
        for name, body in scripts.items():
            (root / 'tests' / name).write_text(body)
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        # A Git hook exports GIT_DIR; the runner must not pass it to the scripts.
        env = self.outermost(dict(PYTHON=sys.executable, GIT_DIR=str(root / '.git'), **(env or {})))
        if clean:
            env['ZLOOP_TEST_SLOT_HELD'] = '1'
        self.root = root
        return subprocess.run([sys.executable, str(root / launcher), *args], env=env, cwd=root,
                              capture_output=True, text=True, timeout=60)

    def test_every_script_passing_exits_zero_and_counts_them(self):
        result = self.suite({'test_one.py': 'pass\n', 'test_two.py': 'pass\n', 'helper.py': 'raise SystemExit(1)\n'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'2 regression scripts passed ({sys.executable})', result.stdout)

    def test_clean_shell_runs_the_full_suite_without_caller_opt_ins(self):
        caller_only = 'zloop-caller-path'
        env = {'ZLOOP_NATIVE': '1', 'PROBE_CALLER_ONLY': 'keep out',
               'PATH': os.pathsep.join((caller_only, os.environ['PATH']))}
        script = ('import os\n'
                  'from pathlib import Path\n'
                  'assert "PROBE_CALLER_ONLY" not in os.environ, "caller environment leaked"\n'
                  'assert "ZLOOP_NATIVE" not in os.environ, "caller native opt-in leaked"\n'
                  'assert "zloop-caller-path" not in os.environ["PATH"], "caller PATH leaked"\n'
                  'pool = Path(os.environ["TMPDIR"]).parents[1] / "slots"\n'
                  'assert pool.is_dir(), "clean fixture used the outer slot pool"\n')
        result = self.suite({'test_first.py': script, 'test_second.py': script}, env, clean=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('2 scripts passed, 0 failed; 2 scheduled units passed, 0 failed', result.stdout)

    def test_the_clean_shell_run_passes_its_arguments_to_the_suite_runner(self):
        # Rule: a hosted shard or lane gets the clean environment and still names its shard and scripts; sharding two
        # one-unit scripts leaves the failing first one to shard 1 and the passing second one to shard 2.
        result = self.suite({'test_first.py': 'raise SystemExit(1)\n', 'test_second.py': 'pass\n',
                             'test_third.py': 'raise SystemExit(1)\n'},
                            args=['--shard', '2/2', 'tests/test_first.py', 'tests/test_second.py'], clean=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('1 scripts passed, 0 failed; 1 scheduled units passed, 0 failed', result.stdout)
        self.assertIn('shard 2/2: 1 units', result.stdout)

    def test_the_parity_lane_refuses_network_lookups_and_oversubscribes_the_slots(self):
        script = ('import os, socket\n'
                  'try:\n'
                  '    socket.getaddrinfo("zloop-parity.invalid", 443)\n'
                  'except socket.gaierror as error:\n'
                  '    assert "parity lane" in str(error), error\n'
                  'else:\n'
                  '    raise AssertionError("a network lookup was not refused")\n'
                  'assert socket.getaddrinfo("localhost", 80)\n'
                  'assert os.environ["ZLOOP_TEST_SLOTS"] == str(2 * os.cpu_count()), os.environ["ZLOOP_TEST_SLOTS"]\n')
        result = self.suite({'test_network.py': script}, args=['--parity'], clean=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout.splitlines()[0], r'^parity lane: git version \d+\.\d+.*, Python 3\.\d+.*, Node ')

    def test_the_parity_lane_refuses_a_git_older_than_its_floor(self):
        tools = tempfile.TemporaryDirectory(prefix='parity tools ')
        self.addCleanup(tools.cleanup)
        old = Path(tools.name) / 'git'
        old.write_text('#!/bin/sh\n'
                       'if [ "$1" = --version ]; then printf "%s\\n" "git version 2.39.5 (Apple Git-154)"; exit 0; fi\n'
                       f'exec {shlex.quote(shutil.which("git"))} "$@"\n')
        old.chmod(0o755)
        result = self.suite({'test_one.py': 'pass\n'}, {'PATH': os.pathsep.join((tools.name, os.environ['PATH']))},
                            args=['--parity'], clean=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('git version 2.39.5 (Apple Git-154) is older than git 2.40', result.stderr)
        self.assertNotIn('scripts passed', result.stdout)

    def test_clean_shell_keeps_the_callers_git_and_node(self):
        git = shutil.which('git')
        tools = tempfile.TemporaryDirectory(prefix='suite tools ')
        self.addCleanup(tools.cleanup)
        git_dir = Path(tools.name) / 'git tools'
        node_dir = Path(tools.name) / 'node tools'
        git_dir.mkdir()
        node_dir.mkdir()
        executable = git_dir / 'git'
        executable.write_text('#!/bin/sh\n'
                              'if [ "$1" = --version ]; then printf "%s\\n" "fixture git"; exit 0; fi\n'
                              f'exec {shlex.quote(git)} "$@"\n')
        executable.chmod(0o755)
        for directory, version in ((git_dir, 'shadow node'), (node_dir, 'selected node')):
            node = directory / 'node'
            node.write_text(f'#!/bin/sh\nprintf "%s\\n" "{version}"\n')
            node.chmod(0o755)
        env = {'PATH': os.pathsep.join((str(node_dir), str(git_dir), os.environ['PATH']))}
        script = ('import subprocess\n'
                  'git = subprocess.check_output(["git", "--version"], text=True).strip()\n'
                  'node = subprocess.check_output(["node", "--version"], text=True).strip()\n'
                  'assert git == "fixture git", git\n'
                  'assert node == "selected node", node\n')
        result = self.suite({'test_tools.py': script}, env, clean=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_named_scripts_run_alone_and_are_the_ones_counted(self):
        # Rule: a focused run names its scripts; the others, a failing one among them, do not run.
        result = self.suite({'test_one.py': 'pass\n', 'test_two.py': 'pass\n', 'test_bad.py': 'raise SystemExit(1)\n'},
                            args=['tests/test_one.py', str(Path('tests/test_two.py'))])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.endswith(f'2 regression scripts passed ({sys.executable})\n'
                                               '2 scripts passed, 0 failed; 2 scheduled units passed, 0 failed\n'), result.stdout)
        self.assertNotIn('test_bad', result.stdout)

    def test_a_path_that_is_no_test_script_here_is_refused_and_nothing_runs(self):
        # Rule: a mistyped or foreign path must not pass as a green run of fewer scripts.
        ran = Path(tempfile.mkdtemp(prefix='run all ran '))
        self.addCleanup(shutil.rmtree, ran)
        script = f'open({str(ran / "one")!r}, "w").close()\n'
        outside = Path(tempfile.mkdtemp(prefix='run all outside '))
        self.addCleanup(shutil.rmtree, outside)
        (outside / 'test_far.py').write_text('pass\n')
        for path in ('tests/test_onw.py', 'tests/helper.py', 'tests', str(outside / 'test_far.py')):
            with self.subTest(path=path):
                result = self.suite({'test_one.py': script, 'helper.py': 'pass\n'}, args=['tests/test_one.py', path])
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertEqual(result.stderr, f'run_all: not a tests/test_*.py script of this checkout: {path}\n')
                self.assertEqual(list(ran.iterdir()), [])

    SHARD_FIXTURE = {
        'test_alpha': (('A', 3), ('B', 12)),
        'test_beta': (('C', 5), ('D', 1)),
    }

    def recording_scripts(self, log):
        """Fixture scripts whose every test, and the plain script `test_plain`, append its name to `log`; the names
        they must produce are listed by `recorded_names`."""
        scripts = {}
        for stem, classes in self.SHARD_FIXTURE.items():
            body = ('import unittest\n'
                    f'def record(name):\n    open({str(log)!r}, "a").write(name + "\\n")\n')
            for name, count in classes:
                body += f'class {name}(unittest.TestCase):\n'
                body += ''.join(f'    def test_n{index:02}(self):\n        record("{stem}.{name}.test_n{index:02}")\n'
                                for index in range(count))
            scripts[f'{stem}.py'] = body + 'if __name__ == "__main__":\n    unittest.main()\n'
        scripts['test_plain.py'] = f'open({str(log)!r}, "a").write("test_plain\\n")\n'
        return scripts

    def recorded_names(self):
        return sorted(['test_plain'] + [f'{stem}.{name}.test_n{index:02}'
                                         for stem, classes in self.SHARD_FIXTURE.items()
                                         for name, count in classes for index in range(count)])

    def test_the_shards_together_run_every_test_exactly_once(self):
        # Rule: `--shard K/N` for K = 1..N schedules each unit of the unsharded run in exactly one shard, so the
        # shards neither drop nor repeat a test.
        scratch = Path(tempfile.mkdtemp(prefix='run all shards '))
        self.addCleanup(shutil.rmtree, scratch)
        for count in (1, 3, 4, 40):
            with self.subTest(shards=count):
                ran, units = [], 0
                for number in range(1, count + 1):
                    log = scratch / f'{count}-{number}'
                    result = self.suite(self.recording_scripts(log), {'ZLOOP_TEST_SLOTS': '4'},
                                        ['--shard', f'{number}/{count}'])
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    ran += log.read_text().split() if log.exists() else []
                    units += int(re.search(r'(\d+) scheduled units passed', result.stdout)[1])
                self.assertEqual(sorted(ran), self.recorded_names())
                self.assertEqual(units, 6, 'the shards scheduled other units than the five of the fixture and its plain script')
        # Five fixture units split among forty shards leave most shards empty; an empty shard passes.
        empty = self.suite(self.recording_scripts(scratch / 'empty'), args=['--shard', '40/40'])
        self.assertEqual(empty.returncode, 0, empty.stdout + empty.stderr)

    def test_a_shard_spec_that_is_not_k_of_n_is_refused_and_nothing_runs(self):
        # Rule: a mistyped shard must not pass as a green run of fewer tests.
        scratch = Path(tempfile.mkdtemp(prefix='run all bad shard '))
        self.addCleanup(shutil.rmtree, scratch)
        log = scratch / 'ran'
        for spec in ('0/3', '4/3', '1/0', '-1/3', '1.5/3', 'a/b', '2', '1/2/3', ''):
            with self.subTest(spec=spec):
                result = self.suite(self.recording_scripts(log), args=['--shard', spec])
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertEqual(result.stderr, f'run_all: --shard needs K/N with 1 <= K <= N, got: {spec!r}\n')
                self.assertFalse(log.exists())
        result = self.suite(self.recording_scripts(log), args=['--shard'])
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "run_all: --shard needs K/N with 1 <= K <= N, got: None\n")
        self.assertFalse(log.exists())

    def test_the_run_ends_with_its_slowest_units_and_their_seconds(self):
        # Rule: a hosted log profiles the suite, so it names the slowest units, slowest first and no more than ten.
        slow = 'import time\ntime.sleep(1.5)\n'
        scripts = {f'test_quick{index:02}.py': 'pass\n' for index in range(11)}
        result = self.suite({**scripts, 'test_slow.py': slow})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = re.findall(r'(?m)^ +(\d+\.\d)s (\S+)$', result.stdout)
        self.assertEqual(len(rows), 10, result.stdout)
        self.assertEqual(rows[0][1], 'test_slow')
        self.assertGreaterEqual(float(rows[0][0]), 1.5)
        self.assertEqual([float(seconds) for seconds, _ in rows], sorted((float(seconds) for seconds, _ in rows), reverse=True))
        self.assertLess(float(rows[1][0]), 1.5)
        self.assertLess(result.stdout.index('test_slow'), result.stdout.index('scripts passed, 0 failed'))

    def test_a_failure_exits_one_and_prints_its_name_and_output_tail(self):
        result = self.suite({'test_good.py': 'pass\n',
                             'test_bad.py': 'print("first line")\nprint("why it broke")\nraise SystemExit(3)\n'})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('FAIL test_bad', result.stdout)
        self.assertIn('why it broke', result.stdout)
        self.assertNotIn('FAIL test_good', result.stdout)
        self.assertNotIn('regression scripts passed (', result.stdout)
        self.assertIn('1 scripts passed, 1 failed; 1 scheduled units passed, 1 failed', result.stdout)

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

    def test_the_default_slot_directory_is_under_the_temporary_directory(self):
        # Rule: a sandbox that writes only $TMPDIR must still get its slot locks, so they default beside the logs.
        root = Path(tempfile.mkdtemp(prefix='run all tmpdir '))
        self.addCleanup(shutil.rmtree, root)
        (root / 'tests').mkdir()
        tmp = tempfile.TemporaryDirectory(prefix='run all temp ')
        self.addCleanup(tmp.cleanup)
        temp = Path(tmp.name)
        shutil.copy(RUNNER, root / 'tests/run_all.py')
        (root / 'tests/test_one.py').write_text('pass\n')
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        env = {key: value for key, value in os.environ.items() if not key.startswith('ZLOOP_TEST_SLOT')}
        result = subprocess.run([sys.executable, str(root / 'tests/run_all.py')], cwd=root, capture_output=True,
                                text=True, timeout=60, env={**env, 'PYTHON': sys.executable, 'TMPDIR': str(temp)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((temp / f'zloop-test-slots-{os.getuid()}' / 'slot-0').exists())


    def test_tmpdir_in_another_worktree_is_refused_even_through_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp)
            other = outside / 'another-repo'
            subprocess.run(['git', 'init', '-q', str(other)], check=True)
            inside = other / 'temp'
            inside.mkdir()
            link = outside / 'symlink'
            link.symlink_to(inside, target_is_directory=True)
            ran = outside / 'ran'
            for path in (inside, link):
                with self.subTest(path=path):
                    result = self.suite({'test_probe.py': f'from pathlib import Path\nPath({str(ran)!r}).touch()\n'},
                                        {'TMPDIR': str(path)})
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn(f'TMPDIR resolves inside a Git worktree: {inside.resolve()}', result.stderr)
                    self.assertFalse(ran.exists())
                    self.assertEqual(list(inside.iterdir()), [])

    def test_a_terminated_run_removes_its_logs(self):
        root = Path(tempfile.mkdtemp(prefix='run all term '))
        self.addCleanup(shutil.rmtree, root)
        (root / 'tests').mkdir()
        tmp = tempfile.TemporaryDirectory(prefix='run all temp ')
        self.addCleanup(tmp.cleanup)
        temp = Path(tmp.name)
        shutil.copy(RUNNER, root / 'tests/run_all.py')
        started = root / 'started'
        (root / 'tests/test_slow.py').write_text(f'import time\nopen({str(started)!r}, "w").close()\ntime.sleep(30)\n')
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        runner = subprocess.Popen([sys.executable, str(root / 'tests/run_all.py')], start_new_session=True,
                                  env=self.outermost(dict(PYTHON=sys.executable, TMPDIR=str(temp))))
        self.addCleanup(self.stop_group, runner)
        deadline = time.monotonic() + 10
        # The runner opens a unit's log before starting it; wait for the script itself, which killpg then stops.
        while not (started.exists() and list(temp.glob('*/*.log'))):
            self.assertLess(time.monotonic(), deadline, 'the run never started')
            time.sleep(0.05)
        runner.send_signal(signal.SIGTERM)
        self.assertEqual(runner.wait(timeout=10), 128 + signal.SIGTERM)
        self.stop_group(runner)  # the sleeping test, as a hook's teardown would
        self.assertEqual(list(temp.iterdir()), [])

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

    def test_units_start_with_the_checkouts_python_already_compiled(self):
        # Rule: units run with -B, which writes no bytecode, so in a fresh checkout (a worktree, the pre-push gate)
        # every process a test starts would compile what it imports; the runner compiles the checkout's Python first.
        unit = ('import importlib.util, os\n'
                'spec = importlib.util.find_spec("helper_module")\n'
                'raise SystemExit(0 if os.path.exists(spec.cached) else "no bytecode for " + spec.origin)\n')
        result = self.suite({'test_compiled.py': unit, 'helper_module.py': 'VALUE = 1\n'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'1 regression scripts passed ({sys.executable})', result.stdout)

    def test_scripts_do_not_inherit_the_git_hook_environment(self):
        result = self.suite({'test_env.py': 'import os\nraise SystemExit(1 if "GIT_DIR" in os.environ else 0)\n'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_collection_and_each_test_unit_get_a_fresh_environment(self):
        with tempfile.TemporaryDirectory() as scratch:
            markers = Path(scratch)
            body = f"MARKERS = {str(markers)!r}\n" + '''import json, os, subprocess, tempfile, unittest
from pathlib import Path

names = ('HOME', 'CODEX_HOME', 'XDG_STATE_HOME', 'TMPDIR', 'GIT_CEILING_DIRECTORIES',
         'ZLOOP_NATIVE', 'CODEX_SESSION_ID', 'OTHER_SESSION_ID', 'HERDR_ENV', 'T3_SAMPLE',
         'ZLOOP_SESSION', 'CODEX_THREAD_ID')
private = Path(os.environ['TMPDIR']).parent
if Path(os.environ['HOME']) == private / 'home':
    subprocess.run(['git', 'init', '-q', private], check=True)
with tempfile.TemporaryDirectory() as tmp:
    ok = subprocess.run(['git', '-C', tmp, 'rev-parse', '--show-toplevel'], capture_output=True).returncode
    subprocess.run(['git', 'init', '-q', tmp], check=True)
    root = subprocess.check_output(['git', '-C', tmp, 'rev-parse', '--show-toplevel'], text=True).strip()
    assert Path(root) == Path(tmp).resolve(), root
(Path(MARKERS) / str(os.getpid())).write_text(json.dumps({
    'values': {name: os.environ.get(name) for name in names},
    'empty': [name for name in names[:3] if os.environ.get(name) and Path(os.environ[name]).is_dir()
              and not any(Path(os.environ[name]).iterdir())],
    'temporary': tmp, 'outside_git': ok != 0,
}))

class Left(unittest.TestCase):
    def test_left(self):
        pass

class Right(unittest.TestCase):
    def test_right(self):
        pass

if __name__ == '__main__':
    unittest.main()
'''
            host = {key: scratch for key in ('HOME', 'CODEX_HOME', 'XDG_STATE_HOME', 'TMPDIR',
                                             'CODEX_SESSION_ID', 'OTHER_SESSION_ID', 'HERDR_ENV', 'T3_SAMPLE',
                                             'ZLOOP_SESSION', 'CODEX_THREAD_ID')}
            result = self.suite({'test_isolation.py': body}, dict(host, ZLOOP_NATIVE='1'))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rows = [json.loads(path.read_text()) for path in markers.iterdir()]
            self.assertEqual(len(rows), 3, result.stdout + result.stderr)
            for row in rows:
                with self.subTest(row=row):
                    self.assertEqual(row['empty'], ['HOME', 'CODEX_HOME', 'XDG_STATE_HOME'])
                    self.assertTrue(row['outside_git'], 'Git discovered a repository beyond the unit ceiling')
                    values = row['values']
                    tmp = Path(values['TMPDIR'])
                    self.assertEqual(Path(row['temporary']).parent, tmp)
                    self.assertEqual(values['GIT_CEILING_DIRECTORIES'], str(tmp.parent))
                    self.assertEqual(values['ZLOOP_NATIVE'], '1')
                    for key in ('CODEX_SESSION_ID', 'OTHER_SESSION_ID', 'HERDR_ENV', 'T3_SAMPLE',
                                'ZLOOP_SESSION', 'CODEX_THREAD_ID'):
                        self.assertIsNone(values[key], key)
                    self.assertFalse(tmp.parent.exists())
            for key in ('HOME', 'CODEX_HOME', 'XDG_STATE_HOME', 'TMPDIR'):
                self.assertEqual(len({row['values'][key] for row in rows}), 3, key)

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
        self.assertNotIn('regression scripts passed (', result.stdout)
        self.assertIn('0 scripts passed, 1 failed; 1 scheduled units passed, 1 failed', result.stdout)

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
