#!/usr/bin/env python3
"""Exercise check execution through its CLI using only temporary files/jobs."""
import json
import os
from pathlib import Path
import signal
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import support
import check_receipts


RUNNER = support.script('run_check.py')
LOCK = support.script('resource_lock.py')
# The claimed test alone: it fails at the base (value 1) and passes once the fix sets value 2.
CLAIMED_TEST = ('import unittest\nfrom product import value\n'
                'class ValueTest(unittest.TestCase):\n'
                '    def test_wanted(self): self.assertEqual(value, 2)\n')


class RunCheckTests(unittest.TestCase):
    def claim_fixture(self, test_file=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = root / 'repo'
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        (repo / 'product.py').write_text('value = 1\n')
        (repo / 'legacy.py').write_text('retired = True\n')
        (repo / 'stable.py').write_text('kept = True\n')
        subprocess.run(['git', '-C', str(repo), 'add', 'product.py', 'legacy.py', 'stable.py'], check=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t',
                        'commit', '-qm', 'base'], check=True)
        base = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
        state = root / 'workers.sqlite'
        claim = 'test_value.ValueTest.test_wanted'
        support.write_attempts(state, [support.attempt_row(
            repo, attempt='value-1', worktree=str(repo), head=base, job='ticket',
            dispatch_assignment={'assignment': 'ticket-1'},
            check_receipts={'claims': [claim], 'identity': 'value-run',
                            'test_files': {claim: test_file} if test_file else {}})])
        return repo, base, claim

    def claim_check(self, repo, claim, name, *phase, verbose='-v'):
        """Run the value test module as a check of claim with the phase options; its log is name.log beside repo."""
        log = repo.parent / f'{name}.log'
        result = subprocess.run(
            [sys.executable, str(RUNNER), '--timeout', '5', '--log', str(log), *phase,
             '--format', 'unittest', '--claim', claim, '--state', str(repo.parent / 'workers.sqlite'),
             '--attempt', 'value-1', '--', sys.executable, '-B', '-m', 'unittest', verbose, 'test_value'],
            cwd=repo, capture_output=True, text=True, timeout=10)
        return log, result

    def revert_check(self, repo, base, claim, name, **options):
        """Record a revert of the fix (product.py back at the base) at the checked-out head, then restore the head."""
        subprocess.run(['git', '-C', str(repo), 'checkout', base, '--', 'product.py'], check=True)
        log, result = self.claim_check(repo, claim, name, '--phase', 'revert', '--revert', 'product.py', **options)
        subprocess.run(['git', '-C', str(repo), 'checkout', 'HEAD', '--', 'product.py'], check=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        return log

    def acceptance(self, repo, base, claim, head, *logs):
        """What acceptance at head reports for the fixture attempt, whose ticket declares claim, given the summaries
        of logs collected now."""
        ticket = repo.parent / 'ticket.md'
        ticket.write_text('## Acceptance criteria\n- [ ] The product holds the wanted value.\n'
                          f'      Verify (code): run the value test.\n      Test claim: {claim}\n')
        assigned = dict(check_receipts.criteria(ticket), ticket=str(ticket), identity='value-run')
        result = dict(check_receipts=check_receipts.sealed_receipts([str(log) for log in logs]),
                      checks_collected_ns=time.time_ns())
        return check_receipts.problems(dict(attempt='value-1', head=base, check_receipts=assigned, result=result), head)

    def red_check(self, repo, claim, name, wanted):
        (repo / 'test_value.py').write_text(
            'import unittest\nfrom product import value\n'
            'class ValueTest(unittest.TestCase):\n'
            f'{wanted}'
            '    def test_other(self): self.fail("test_value.ValueTest.test_wanted")\n')
        return self.claim_check(repo, claim, name, '--phase', 'red')

    def committed_fix(self, repo):
        """Commit the claimed test, its fix (value 2) and two product changes the test does not depend on (helper.py
        added, legacy.py removed); return the new head."""
        (repo / 'test_value.py').write_text(CLAIMED_TEST)
        (repo / 'product.py').write_text('value = 2\n')
        (repo / 'helper.py').write_text('unused = True\n')
        (repo / 'legacy.py').unlink()
        subprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t',
                        'commit', '-qm', 'fix'], check=True)
        return subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()

    def test_red_records_the_claimed_unittest_failure_on_base_source(self):
        repo, base, claim = self.claim_fixture()
        log, result = self.red_check(repo, claim, 'red',
                                     '    def test_wanted(self): self.assertEqual(value, 2)\n')
        self.assertIn(f'FAIL: test_wanted ({claim})', log.read_text() if log.exists() else result.stderr)
        self.assertEqual(result.returncode, 1, result.stderr)
        proof = json.loads(result.stdout)['tdd']
        self.assertEqual((proof['phase'], proof['claim'], proof['base'], proof['attempt']),
                         ('red', claim, base, 'value-1'))
        tree = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
        self.assertEqual((proof['source']['head'], proof['source']['tree']), (base, tree))
        self.assertEqual(list(proof['source']['test_changes']), ['test_value.py'])
        self.assertLess(proof['started_ns'], proof['finished_ns'])

    def test_red_refuses_a_skipped_passing_or_erroring_claim_when_another_test_fails(self):
        repo, _, claim = self.claim_fixture()
        for name, wanted, outcome in (
            ('passes', '    def test_wanted(self): pass\n', 'ok'),
            ('skips', '    @unittest.skip("scenario unavailable")\n    def test_wanted(self): pass\n',
             "skipped 'scenario unavailable'"),
            ('errors', '    def test_wanted(self): raise RuntimeError("broken setup")\n', 'ERROR'),
        ):
            with self.subTest(outcome=name):
                log, result = self.red_check(repo, claim, name, wanted)
                self.assertEqual(result.returncode, 125, result.stdout + result.stderr)
                self.assertIn(f'test_wanted ({claim}) ... {outcome}', log.read_text())
                self.assertIn('FAIL: test_other (test_value.ValueTest.test_other)', log.read_text())
                summary = json.loads(result.stdout)
                self.assertNotIn('tdd', summary)
                self.assertIn(f'{claim} did not fail as a unittest result', summary['test_claim_error'])

    def test_red_refuses_staged_unstaged_and_untracked_product_changes(self):
        wanted = '    def test_wanted(self): self.assertEqual(value, 2)\n'
        for name in ('staged', 'unstaged', 'untracked'):
            with self.subTest(source=name):
                repo, _, claim = self.claim_fixture()
                source = 'extra.py' if name == 'untracked' else 'product.py'
                (repo / source).write_text('value = 3\n')
                if name == 'staged':
                    subprocess.run(['git', '-C', str(repo), 'add', source], check=True)
                    (repo / source).write_text('value = 1\n')
                log, result = self.red_check(repo, claim, name, wanted)
                self.assertEqual(result.returncode, 125, result.stdout + result.stderr)
                self.assertRegex(result.stderr, rf'red source differs from [0-9a-f]+: {source}')
                self.assertFalse(log.exists(), 'the check ran on changed product bytes')

    def test_red_marker_requires_an_exact_unambiguous_claimed_failure(self):
        repo, _, claim = self.claim_fixture(test_file='test_value.sh')
        script = repo / 'test_value.sh'
        cases = {
            'mention': f'FAIL: some other test\nreason: FAIL: {claim}',
            'error': f'FAIL: some other test\nERROR: {claim}',
            'contradiction': f'PASS: {claim}\nFAIL: {claim}',
        }
        for name, output in cases.items():
            with self.subTest(name=name):
                script.write_text(f"printf '%s\\n' {shlex.quote(output)}\nexit 1\n")
                log = repo.parent / f'{name}.log'
                result = subprocess.run(
                    [sys.executable, str(RUNNER), '--timeout', '5', '--log', str(log), '--phase', 'red',
                     '--claim', claim, '--state', str(repo.parent / 'workers.sqlite'), '--attempt', 'value-1',
                     '--', 'sh', 'test_value.sh'], cwd=repo, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 125, result.stdout + result.stderr)
                self.assertEqual(log.read_text(), output + '\n')
                summary = json.loads(result.stdout)
                self.assertNotIn('tdd', summary)
                self.assertIn(f'{claim} did not fail as a marker result', summary['test_claim_error'])

    def test_revert_records_a_receipt_only_when_restoring_the_fix_turns_the_claim_red(self):
        repo, base, claim = self.claim_fixture()
        head = self.committed_fix(repo)
        log = self.revert_check(repo, base, claim, 'revert')
        self.assertIn(f'FAIL: test_wanted ({claim})', log.read_text())
        proof = json.loads(Path(f'{log}.summary.json').read_text())['tdd']
        git = ['git', '-C', str(repo)]
        tree = subprocess.check_output([*git, 'rev-parse', 'HEAD^{tree}'], text=True).strip()
        self.assertEqual((proof['phase'], proof['claim'], proof['base'], proof['attempt']),
                         ('revert', claim, base, 'value-1'))
        self.assertEqual((proof['source']['head'], proof['source']['tree'], proof['source']['reverted']),
                         (head, tree, ['product.py']))

        def refused(name, paths, reason, code=125):
            with self.subTest(refusal=name):
                _, result = self.claim_check(repo, claim, name, '--phase', 'revert',
                                             *(['--revert', *paths] if paths else []))
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertIn(reason, result.stdout + result.stderr)
                self.assertNotIn('"tdd"', result.stdout)
            subprocess.run([*git, 'reset', '-q', '--hard', head], check=True)

        refused('no path', [], '--phase revert requires --revert', code=2)
        subprocess.run([*git, 'rm', '-q', 'helper.py'], check=True)
        refused('stays green', ['helper.py'], f'{claim} did not fail as a unittest result')
        refused('test path', ['test_value.py'], f'revert path test_value.py is not a product path changed since {base}')
        subprocess.run([*git, 'checkout', base, '--', 'product.py'], check=True)
        refused('unchanged path', ['product.py', 'stable.py'],
                f'revert path stable.py is not a product path changed since {base}')
        subprocess.run([*git, 'checkout', base, '--', 'product.py'], check=True)
        refused('removed path left removed', ['product.py', 'legacy.py'],
                f'revert source differs from {base}: legacy.py')
        (repo / 'product.py').write_text('value = 3\n')
        refused('other bytes', ['product.py'], f'revert source differs from {base}: product.py')

    def test_acceptance_requires_a_revert_receipt_after_green_at_the_accepted_head(self):
        repo, base, claim = self.claim_fixture()
        (repo / 'test_value.py').write_text(CLAIMED_TEST)
        red, _ = self.claim_check(repo, claim, 'red', '--phase', 'red')
        head = self.committed_fix(repo)
        green, _ = self.claim_check(repo, claim, 'green', '--phase', 'green')
        missing = f'{claim}: missing revert receipt after green at accepted head {head}'
        self.assertEqual(self.acceptance(repo, base, claim, head, red, green), [missing])
        revert = self.revert_check(repo, base, claim, 'revert')
        later_green, _ = self.claim_check(repo, claim, 'later-green', '--phase', 'green')
        self.assertEqual(self.acceptance(repo, base, claim, head, red, later_green, revert), [missing],
                         'a revert that ended before green')
        other_command = self.revert_check(repo, base, claim, 'other-command', verbose='--verbose')
        self.assertEqual(self.acceptance(repo, base, claim, head, red, later_green, other_command), [missing],
                         'a revert of another command')
        git = ['git', '-C', str(repo)]
        subprocess.run([*git, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty',
                        '-m', 'moved'], check=True)
        moved = subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip()
        moved_green, _ = self.claim_check(repo, claim, 'moved-green', '--phase', 'green')
        subprocess.run([*git, 'checkout', '-q', '--detach', head], check=True)
        earlier_head = self.revert_check(repo, base, claim, 'earlier-head')
        self.assertEqual(self.acceptance(repo, base, claim, moved, red, moved_green, earlier_head),
                         [f'{claim}: missing revert receipt after green at accepted head {moved}'],
                         'a revert at another head')
        self.assertEqual(self.acceptance(repo, base, claim, head, red, green, revert), [])

    def test_check_cannot_wait_on_the_host_input_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = subprocess.Popen(
                [sys.executable, str(RUNNER), '--timeout', '.3',
                 '--log', str(Path(tmp) / 'check.log'), '--',
                 'bash', '-c', 'cat >/dev/null; printf "prepared\\n"'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                runner.wait(timeout=3)
                out, err = runner.communicate()
                self.assertEqual(runner.returncode, 0, err)
                self.assertEqual(json.loads(out)['exit_source'], 'command')
                self.assertEqual((Path(tmp) / 'check.log').read_text(), 'prepared\n')
            finally:
                if runner.poll() is None:
                    runner.kill()
                runner.communicate()

    def test_normal_exit_reconciles_descendants_before_returning(self):
        for command_code in (0, 7):
            with self.subTest(command_code=command_code), tempfile.TemporaryDirectory() as tmp:
                log, ready = Path(tmp) / 'check.log', Path(tmp) / 'ready'
                env = dict(os.environ, RESOURCE_LOCK=str(Path(tmp) / 'resource.lock'), RESOURCE_LOCK_TIMEOUT='0')
                child = ('import os,signal,time; from pathlib import Path; '
                         'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                         f'Path({str(ready)!r}).write_text(str(os.getpgrp())); time.sleep(30)')
                parent = ('import subprocess,time; from pathlib import Path; '
                          f'subprocess.Popen({[sys.executable, str(LOCK), sys.executable, "-c", child]!r}); '
                          f'ready = Path({str(ready)!r})\n'
                          'while not ready.exists(): time.sleep(.01)\n'
                          f'print("scenario finished", flush=True); exit({command_code})')
                try:
                    result = subprocess.run(
                        [sys.executable, str(RUNNER), '--timeout', '5', '--log', str(log),
                         '--', sys.executable, '-c', parent], env=env,
                        capture_output=True, text=True, timeout=8)
                    self.assertEqual(result.returncode, command_code or 125, result.stderr)
                    report = json.loads(result.stdout)
                    self.assertEqual(report['command_exit_code'], command_code)
                    self.assertEqual(report['exit_source'], 'command' if command_code else 'cleanup')
                    self.assertTrue(report['cleanup_required'])
                    self.assertFalse(report['timed_out'])
                    self.assertIn('scenario finished', log.read_text())
                    contender = subprocess.run([sys.executable, str(LOCK), 'true'], env=env, capture_output=True)
                    self.assertEqual(contender.returncode, 0, contender.stderr)
                finally:
                    if ready.exists():
                        try:
                            os.killpg(int(ready.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_missing_script_pipeline_cannot_pass_required_execution_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'check.log'
            missing = Path(tmp) / 'missing script.sh'
            result = subprocess.run(
                [sys.executable, str(RUNNER), '--timeout', '3', '--log', str(log),
                 '--require-output', 'Executed 12 tests', '--',
                 'bash', '-c', f'cat {shlex.quote(str(missing))} | bash'],
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['command_exit_code'], 0)
            self.assertEqual(report['missing_output'], ['Executed 12 tests'])
            self.assertEqual(report['exit_source'], 'required-output')
            self.assertIn('No such file', log.read_text())

    def test_required_output_passes_only_when_each_marker_is_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(RUNNER), '--timeout', '3', '--log', str(Path(tmp) / 'check.log'),
                 '--require-output', 'started scenario', '--require-output', 'Executed 12 tests', '--',
                 sys.executable, '-c', 'print("started scenario"); print("Executed 12 tests")'],
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['command_exit_code'], 0)
            self.assertEqual(report['missing_output'], [])
            self.assertEqual(report['exit_source'], 'command')
            # One marker present and one missing still fails: every marker is required, not any one.
            result = subprocess.run(
                [sys.executable, str(RUNNER), '--timeout', '3', '--log', str(Path(tmp) / 'half.log'),
                 '--require-output', 'started scenario', '--require-output', 'Executed 12 tests', '--',
                 sys.executable, '-c', 'print("started scenario")'],
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(json.loads(result.stdout)['missing_output'], ['Executed 12 tests'])

    def test_required_output_does_not_replace_command_failure_or_timeout_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            for code in (7, 124):
                result = subprocess.run(
                    [sys.executable, str(RUNNER), '--timeout', '3', '--log', str(Path(tmp) / f'{code}.log'),
                     '--require-output', 'Executed 12 tests', '--', sys.executable, '-c', f'exit({code})'],
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, code, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report['command_exit_code'], code)
                self.assertFalse(report['timed_out'])
                self.assertEqual(report['exit_source'], 'command')

    def test_invalid_output_requirement_never_starts_a_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'check.log'
            for value in ('', ' ', 'a\nb'):
                result = subprocess.run(
                    [sys.executable, str(RUNNER), '--timeout', '3', '--log', str(log),
                     '--require-output', value, '--', 'true'], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(log.exists())

    def test_timing_out_a_queued_check_preserves_the_other_lock_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, RESOURCE_LOCK=str(Path(tmp) / 'resource.lock'), RESOURCE_LOCK_TIMEOUT='60')
            holder = subprocess.Popen(
                [sys.executable, str(LOCK), sys.executable, '-c',
                 'import time; print("locked", flush=True); time.sleep(30)'],
                env=env, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), 'locked')
                result = subprocess.run(
                    [sys.executable, str(RUNNER), '--timeout', '.2', '--log', str(Path(tmp) / 'queued.log'),
                     '--', sys.executable, str(LOCK), 'true'], env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 124, result.stderr)
                self.assertIsNone(holder.poll(), 'unrelated owner was stopped')
                contender = subprocess.run([sys.executable, str(LOCK), 'true'],
                                           env=dict(env, RESOURCE_LOCK_TIMEOUT='0'), capture_output=True)
                self.assertNotEqual(contender.returncode, 0)
            finally:
                holder.terminate()
                holder.wait(timeout=3)
                holder.stdout.close()

    def test_retained_log_survives_checkout_cleanup_and_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout, log, marker = Path(tmp) / 'checkout', Path(tmp) / 'retained.log', Path(tmp) / 'unexpected'
            checkout.mkdir()
            command = [sys.executable, str(RUNNER), '--timeout', '3', '--log', str(log), '--']
            first = subprocess.run(command + [sys.executable, '-c', 'print("verified at head abc")'],
                                   cwd=checkout, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            shutil.rmtree(checkout)
            self.assertEqual(log.read_text(), 'verified at head abc\n')
            retry = subprocess.run(command + [sys.executable, '-c',
                                   f'from pathlib import Path; Path({str(marker)!r}).touch()'],
                                   capture_output=True, text=True)
            self.assertEqual(retry.returncode, 125, retry.stderr)
            self.assertFalse(marker.exists())
            self.assertEqual(log.read_text(), 'verified at head abc\n')

    def test_invalid_deadline_never_starts_a_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            for value in ('0', '-1', 'nan', 'inf'):
                with self.subTest(timeout=value):
                    log = Path(tmp) / f'{value}.log'
                    result = subprocess.run(
                        [sys.executable, str(RUNNER), '--timeout', value, '--log', str(log), '--',
                         sys.executable, '-c', 'print("must not start")'],
                        capture_output=True, text=True, timeout=3)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(log.exists())

    def test_failed_gate_writes_a_journal_decision_and_keeps_its_exit_code_when_the_backend_is_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'home/.config/zloop/executors.json'
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'decisions': {'enabled': ['failure-cause'], 'backend': {
                'protocol': 'systemone', 'model': 'local/test', 'timeout_seconds': 1,
                'endpoint': 'http://127.0.0.1:9/v1/systemone'}}}))
            journal = root / 'decisions.jsonl'
            env = dict(os.environ, HOME=str(root / 'home'), XDG_STATE_HOME=str(root / 'state'))
            env.pop('ZLOOP_SESSION', None)

            def run(name, code):
                result = subprocess.run(
                    [sys.executable, str(RUNNER), '--timeout', '20', '--log', str(root / f'{name}.log'),
                     '--classify-failure', str(journal), '--assignment-id', f'gate/{name}', '--repo', str(root),
                     '--', sys.executable, '-c', f'print("FAILED test_resize"); exit({code})'],
                    env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, code, result.stderr)
                return json.loads(result.stdout)
            passed = run('passing', 0)
            self.assertNotIn('failure_cause', passed)
            self.assertFalse(journal.exists())
            failed = run('failing', 1)
            # The backend is unreachable here: the request is journaled and no label is shown.
            self.assertEqual(failed['failure_cause']['status'], 'unavailable')
            self.assertNotIn('choice', failed['failure_cause'])
            decision = json.loads(journal.read_text().splitlines()[0])
            self.assertEqual((decision['request']['family'], decision['assignment']), ('failure-cause', 'gate/failing'))
            self.assertIn('FAILED test_resize', decision['request']['state'])
            # Labeling never replaces the check's own result.
            (root / 'stale.log.summary.json').write_text('{}')
            stale = run('stale', 3)
            self.assertEqual(stale['failure_cause']['status'], 'unavailable')

    def test_the_summary_is_kept_beside_the_log_with_the_commit_the_check_ran_at(self):
        # The delivery gate reads it: an E2E run the orchestrator ran itself is evidence only at the head it ran.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q',
                            '--allow-empty', '-m', 'head'], check=True)
            head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True,
                                  text=True).stdout.strip()
            for name, code, cwd in (('passing', 0, repo), ('failing', 4, repo), ('outside', 0, Path(tmp))):
                log = Path(tmp) / f'{name}.log'
                result = subprocess.run([sys.executable, str(RUNNER), '--timeout', '10', '--log', str(log), '--',
                                         sys.executable, '-c', f'exit({code})'], cwd=cwd, capture_output=True,
                                        text=True, timeout=20)
                self.assertEqual(result.returncode, code, result.stderr)
                summary = json.loads(Path(f'{log}.summary.json').read_text())
                self.assertEqual(summary, json.loads(result.stdout))
                self.assertEqual((summary['exit_code'], summary.get('head')), (code, head if cwd == repo else None))
            # Uncommitted changes are not the commit: a run over them names no head.
            (repo / 'fix.txt').write_text('uncommitted\n')
            dirty = Path(tmp) / 'dirty.log'
            result = subprocess.run([sys.executable, str(RUNNER), '--timeout', '10', '--log', str(dirty), '--',
                                     sys.executable, '-c', 'exit(0)'], cwd=repo, capture_output=True, text=True,
                                    timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('head', json.loads(Path(f'{dirty}.summary.json').read_text()))
            (repo / 'fix.txt').unlink()
            # A commit made while the check runs is not the one it checked: the summary names no head.
            moving = Path(tmp) / 'moving.log'
            git = f"git -C {shlex.quote(str(repo))} -c user.name=t -c user.email=t@t commit -q --allow-empty -m moved"
            result = subprocess.run([sys.executable, str(RUNNER), '--timeout', '10', '--log', str(moving), '--',
                                     'sh', '-c', git], cwd=repo, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('head', json.loads(Path(f'{moving}.summary.json').read_text()))
            # Without git on PATH the check still runs and keeps its summary, with no head.
            bare = Path(tmp) / 'no-git.log'
            result = subprocess.run([sys.executable, str(RUNNER), '--timeout', '10', '--log', str(bare), '--',
                                     sys.executable, '-c', 'exit(0)'], cwd=repo, capture_output=True, text=True,
                                    timeout=20, env=dict(os.environ, PATH=str(Path(tmp) / 'empty')))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('head', json.loads(Path(f'{bare}.summary.json').read_text()))
            # A rerun under a reused log name that cannot even start leaves no passing summary behind.
            reused = Path(tmp) / 'passing.log'
            reused.unlink()
            subprocess.run([sys.executable, str(RUNNER), '--timeout', '10', '--log', str(reused), '--',
                            str(Path(tmp) / 'no-such-program')], cwd=repo, capture_output=True, timeout=20)
            kept = Path(f'{reused}.summary.json')
            self.assertFalse(kept.exists() and json.loads(kept.read_text())['exit_code'] == 0)

    def test_failed_check_retains_both_streams_and_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'check.log'
            result = subprocess.run(
                [sys.executable, str(RUNNER), '--timeout', '5', '--log', str(log), '--',
                 sys.executable, '-c',
                 'import sys; print("assertion failed"); print("diagnostic", file=sys.stderr); sys.exit(7)'],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(set(log.read_text().splitlines()), {'assertion failed', 'diagnostic'})
            self.assertEqual(json.loads(result.stdout)['exit_code'], 7)
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_deadline_stops_term_ignoring_descendant_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'check.log'
            env = dict(os.environ, RESOURCE_LOCK=str(Path(tmp) / 'resource.lock'), RESOURCE_LOCK_TIMEOUT='0')
            child = 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print("child ready", flush=True); time.sleep(30)'
            parent = ('import subprocess,time; subprocess.Popen(' +
                      repr([sys.executable, str(LOCK), sys.executable, '-c', child]) + '); time.sleep(30)')
            runner = subprocess.Popen(
                [sys.executable, str(RUNNER), '--timeout', '5', '--log', str(log), '--',
                 sys.executable, '-c', parent], env=env, start_new_session=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                # The deadline leaves a loaded machine time to start the descendant it must stop.
                out, err = runner.communicate(timeout=20)
                self.assertEqual(runner.returncode, 124, err)
                summary = json.loads(out)
                self.assertTrue(summary['timed_out'])
                self.assertEqual(summary['exit_source'], 'deadline')
                self.assertIn('child ready', log.read_text())
                contender = subprocess.run([sys.executable, str(LOCK), 'true'], env=env, capture_output=True)
                self.assertEqual(contender.returncode, 0, contender.stderr)
            finally:
                if runner.poll() is None:
                    os.killpg(runner.pid, signal.SIGKILL)
                    runner.communicate()

    def test_interruption_stops_the_check_before_releasing_its_lock(self):
        for interruption in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=interruption), tempfile.TemporaryDirectory() as tmp:
                log, ready = Path(tmp) / 'check.log', Path(tmp) / 'ready'
                env = dict(os.environ, RESOURCE_LOCK=str(Path(tmp) / 'resource.lock'), RESOURCE_LOCK_TIMEOUT='0')
                child = ('import os,signal,time; from pathlib import Path; '
                         'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                         f'Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(300)')
                runner = subprocess.Popen(
                    [sys.executable, str(RUNNER), '--timeout', '10', '--log', str(log), '--',
                     sys.executable, str(LOCK), sys.executable, '-c', child], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    support.wait_until(ready.exists, 'the check acquired the lock')
                    runner.send_signal(interruption)
                    try:
                        out, err = runner.communicate(timeout=30)
                    except subprocess.TimeoutExpired:
                        self.fail('the interrupted check kept running')
                    with self.assertRaises(ProcessLookupError, msg='the interrupted check\'s child survived'):
                        os.kill(int(ready.read_text()), 0)
                    self.assertEqual(runner.returncode, 128 + interruption, err)
                    summary = json.loads(out)
                    self.assertFalse(summary['timed_out'])
                    self.assertEqual(summary['exit_source'], 'signal')
                    self.assertEqual(summary['interrupted_signal'], interruption)
                    contender = subprocess.run([sys.executable, str(LOCK), 'true'], env=env, capture_output=True)
                    self.assertEqual(contender.returncode, 0, contender.stderr)
                finally:
                    if ready.exists():
                        try:
                            os.killpg(int(ready.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    if runner.poll() is None:
                        runner.kill()
                    runner.communicate()


if __name__ == '__main__':
    unittest.main()
