#!/usr/bin/env python3
"""Check the stable CI contract: one run per change, a fast job, a sharded suite, a macOS lane and the `ci-ok` aggregate."""
import re
import subprocess
import unittest

import support

# A test script that branches on the platform or drives the macOS sandbox. The character class keeps this file's own
# source from matching, so the scan below does not need to skip it.
MACOS_ONLY = re.compile(r'''(?i)['"]darwin['"]|sandbox[-]exec''')


class CIWorkflow(unittest.TestCase):
    def setUp(self):
        self.workflow = (support.ROOT / '.github/workflows/ci.yml').read_text()

    def section(self, name):
        """The body of the top-level `name:` mapping."""
        found = re.search(rf'(?ms)^{name}:\n(.*?)(?=^\S|\Z)', self.workflow)
        self.assertIsNotNone(found, f'CI needs a top-level {name}')
        return found[1].rstrip('\n') + '\n'

    def jobs(self):
        """Job id -> body, in file order."""
        bodies = re.split(r'(?m)^  ([\w-]+):\n', self.section('jobs'))
        return dict(zip(bodies[1::2], bodies[2::2]))

    def job(self, name):
        self.assertIn(name, self.jobs(), f'CI needs a {name} job')
        return self.jobs()[name]

    def steps(self, job):
        """The steps of a job as their text without the leading dash, split off the job's keys."""
        keys, marker, steps = job.partition('    steps:\n')
        self.assertEqual(marker, '    steps:\n')
        return [step.strip('\n') for step in re.split(r'(?m)^      - ', steps)[1:]]

    def step(self, entries, name):
        matches = [entry for entry in entries if entry.splitlines()[0] == f'name: {name}']
        self.assertEqual(len(matches), 1, name)
        return matches[0]

    def commands(self, job):
        """The `run:` lines of a job's steps."""
        return [line.strip()[len('run: '):] for step in self.steps(job)
                for line in step.splitlines() if line.strip().startswith('run: ')]

    def test_a_change_runs_ci_once_and_a_newer_push_cancels_the_older_run(self):
        # Rule: pull_request covers a PR's commits and push only main, so no commit runs twice; one group per PR (or
        # ref) cancels what a newer push supersedes.
        self.assertEqual(self.section('on'), '  push:\n    branches: [main]\n  pull_request:\n')
        self.assertEqual([line for line in self.section('concurrency').splitlines()],
                         ['  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}',
                          '  cancel-in-progress: true'])

    def test_ci_is_read_only(self):
        # Rule: the workflow token reads the repository and does nothing else.
        self.assertEqual([line for line in self.section('permissions').splitlines()
                          if line.strip() and not line.lstrip().startswith('#')], ['  contents: read'])
        for name, body in self.jobs().items():
            self.assertNotRegex(body, r'(?m)^    permissions:', name)

    def test_every_job_sets_up_its_tools_unconditionally_and_caches_pip(self):
        # Rule: a skipped setup step would let a later step pass or fail for the wrong reason; pip's downloads are
        # cached by the pinned requirements.
        for name in ('fast', 'suite', 'macos'):
            with self.subTest(job=name):
                entries = self.steps(self.job(name))
                self.assertNotRegex(self.job(name), r'(?m)^    if:')
                checkout = [step for step in entries if step.startswith('uses: actions/checkout@')]
                python = [step for step in entries if step.startswith('uses: actions/setup-python@')]
                self.assertEqual((len(checkout), len(python)), (1, 1))
                self.assertIn('        with:\n          persist-credentials: false', checkout[0])
                self.assertIn("python-version: '3.13'", python[0])
                self.assertIn('cache: pip', python[0])
                self.assertIn('cache-dependency-path: requirements.txt', python[0])
                prepare = self.step(entries, 'Prepare test tools')
                self.assertNotRegex(prepare, r'(?m)^        if:')
                self.assertIn('python -m venv .venv', prepare)
                self.assertIn('.venv/bin/python -m pip install -r requirements.txt', prepare)
                for step in entries:
                    self.assertNotRegex(step, r'(?m)^        if:\s*\$\{\{\s*!cancelled', name)

    def test_the_fast_job_checks_the_whole_repository_docs_and_this_contract(self):
        # Rule: the quick checks report on their own, without the suite's minutes.
        fast = self.job('fast')
        self.assertIn('    name: Docs and workflow contract\n', fast)
        self.assertIn('    runs-on: ubuntu-latest\n', fast)
        self.assertEqual(self.commands(fast)[-2:],
                         ['.venv/bin/python .agents/skills/writing-agent-docs/check_docs.py',
                          '.venv/bin/python tests/test_ci_workflow.py'])
        for step in self.steps(fast):
            self.assertNotRegex(step, r'(?m)^        if:')

    def test_the_suite_runs_as_ubuntu_shards_that_each_report_and_together_cover_the_units(self):
        # Rule: shard K/N of every K = 1..N, each on a fresh ubuntu runner, so the shards cover the suite exactly once
        # and one failure does not hide another.
        suite = self.job('suite')
        self.assertIn('    runs-on: ubuntu-latest\n', suite)
        strategy = re.search(r'(?ms)^    strategy:\n(.*?)(?=^    \S)', suite)
        self.assertIsNotNone(strategy, 'the suite needs a strategy')
        self.assertIn('      fail-fast: false\n', strategy[1])
        shards = re.search(r"      matrix:\n        shard: \[(.*)\]\n", strategy[1])
        self.assertIsNotNone(shards, 'the matrix is a list of K/N shards')
        listed = [item.strip().strip("'") for item in shards[1].split(',')]
        count = len(listed)
        self.assertGreater(count, 1)
        self.assertEqual(listed, [f'{number}/{count}' for number in range(1, count + 1)])
        self.assertEqual(self.commands(suite)[-1], '.venv/bin/python tests/run_clean.py --shard ${{ matrix.shard }}')
        self.assertIn('    name: Suite ${{ matrix.shard }}\n', suite)

    def test_the_suite_caches_the_pinned_claude_code_install(self):
        # Rule: the npm install is keyed by the version the install pins, so a version bump refreshes the cache.
        suite = self.job('suite')
        pinned = re.search(r'(?m)^  CLAUDE_CODE_VERSION: (\S+)$', self.section('env'))
        self.assertIsNotNone(pinned, 'the workflow pins the installed Claude Code version once')
        entries = self.steps(suite)
        cache = [step for step in entries if step.startswith('uses: actions/cache@')]
        self.assertEqual(len(cache), 1)
        self.assertIn('          path: ~/.npm\n', cache[0])
        self.assertIn('${{ env.CLAUDE_CODE_VERSION }}', cache[0])
        self.assertIn('npm install --global @anthropic-ai/claude-code@${{ env.CLAUDE_CODE_VERSION }}',
                      self.step(entries, 'Prepare test tools'))
        self.assertTrue(entries.index(cache[0]) < entries.index(self.step(entries, 'Prepare test tools')))

    def test_the_macos_lane_runs_exactly_the_scripts_that_need_macos(self):
        # Rule: a script that branches on darwin or drives the macOS sandbox runs on macOS, and the lane runs nothing
        # else, so the slow runner stays small.
        macos = self.job('macos')
        self.assertIn('    runs-on: macos-15\n', macos)
        command = self.commands(macos)[-1]
        prefix = '.venv/bin/python tests/run_clean.py '
        self.assertTrue(command.startswith(prefix), command)
        in_lane = sorted(command[len(prefix):].split())
        needing = sorted(f'tests/{path.name}' for path in (support.ROOT / 'tests').glob('test_*.py')
                         if MACOS_ONLY.search(path.read_text()))
        self.assertEqual(in_lane, needing)

    def test_ci_ok_passes_only_when_every_other_job_succeeded(self):
        # Rule: one stable check name that is red when any job failed, was cancelled or was skipped, and green only
        # when all succeeded.
        others = [name for name in self.jobs() if name != 'ci-ok']
        self.assertEqual(sorted(others), ['fast', 'macos', 'suite'])
        gate = self.job('ci-ok')
        self.assertIn('    name: ci-ok\n', gate)
        self.assertIn('    if: ${{ always() }}\n', gate)
        self.assertIn(f"    needs: [{', '.join(others)}]\n", gate)
        check = self.step(self.steps(gate), 'Require every needed job to have succeeded')
        self.assertIn("        env:\n          RESULTS: ${{ join(needs.*.result, ' ') }}\n", check)
        script = re.search(r'(?ms)^        run: \|\n(.*)\Z', check)[1]
        script = '\n'.join(line[10:] for line in script.splitlines())
        for results, passes in (('success success success', True), ('success failure success', False),
                                ('cancelled success success', False), ('success success skipped', False),
                                ('', False)):
            with self.subTest(results=results):
                ran = subprocess.run(['bash', '-c', script], env={'RESULTS': results, 'PATH': '/usr/bin:/bin'},
                                     capture_output=True, text=True)
                self.assertEqual(ran.returncode == 0, passes, ran.stdout + ran.stderr)


if __name__ == '__main__':
    unittest.main()
