#!/usr/bin/env python3
"""pre-push: signals stop the checks promptly; runs killed outright are reclaimed."""
import os
from pathlib import Path
import signal
import subprocess
import time
import unittest

from hook_case import HookRepo
from test_doc_checker import GUIDE, git


class PrePushSignals(HookRepo):
    """Signals, grace period and reclaiming temporary worktrees."""

    def test_the_checks_run_in_their_own_process_group_with_default_signals(self):
        # Their own session: no terminal signals reach them directly, and they can be stopped as a group.
        hook = self.start_hook()
        leader = self.suite_pids()[0]
        self.assertNotEqual(self.suite_group(), os.getpgid(hook.pid))
        self.assertEqual(os.getsid(leader), self.suite_group(), 'a session of their own, without a terminal')
        self.assertEqual(self.pipe.read_text().strip(), '141', 'SIGPIPE has its default action')
        hook.send_signal(signal.SIGTERM)
        hook.wait(timeout=10)

    def test_a_signal_reaches_the_checks_once_as_that_signal_and_stops_all_of_them(self):
        cases = (('TERM', signal.SIGTERM, 143, False), ('INT', signal.SIGINT, 130, False),
                 ('HUP', signal.SIGHUP, 129, False), ('QUIT', signal.SIGQUIT, 131, False),
                 ('INT', signal.SIGINT, 130, True), ('HUP', signal.SIGHUP, 129, True))
        for name, number, status, whole_group in cases:
            with self.subTest(signal=name, whole_group=whole_group):
                self.signals.unlink(missing_ok=True)
                hook = self.start_hook()
                [tree] = self.temporary_worktrees()
                started = time.monotonic()
                if whole_group:  # Ctrl-C or a closing terminal: the hook's whole foreground group
                    os.killpg(hook.pid, number)
                else:
                    hook.send_signal(number)
                hook.wait(timeout=10)
                self.assertLess(time.monotonic() - started, 4.5, 'the hook does not wait for the suite to finish')
                self.assertEqual(hook.returncode, status)
                self.assertEqual(self.signals.read_text().split(), [name], 'the checks get that signal, once')
                pids = self.suite_pids()
                self.assertEqual(len(pids), 3)
                self.assertFalse([pid for pid in pids if self.alive(pid)], 'the suite and every descendant stop')
                self.assertEqual(self.temporary_worktrees(), [])
                self.assertFalse(Path(tree).parent.exists())

    def test_checks_that_ignore_signals_are_killed_after_a_grace_period(self):
        self.stubborn.write_text('1')
        hook = self.start_hook()
        started = time.monotonic()
        hook.send_signal(signal.SIGTERM)
        hook.wait(timeout=15)
        self.assertLess(time.monotonic() - started, 9)
        self.assertEqual(hook.returncode, 143)
        self.assertFalse([pid for pid in self.suite_pids() if self.alive(pid)], 'escalated to KILL')
        self.assertEqual(self.temporary_worktrees(), [])

    def test_a_worktree_left_by_a_killed_hook_is_reclaimed_once_its_checks_are_gone(self):
        hook = self.start_hook()
        [tree] = self.temporary_worktrees()
        hook.kill()
        hook.wait(timeout=10)
        self.slow.unlink()
        self.assertEqual(self.temporary_worktrees(), [tree], 'kill -9 cannot be trapped')
        self.assertTrue(any(self.alive(pid) for pid in self.suite_pids()), 'its checks are still running')
        early = self.push('main')
        self.assertEqual(early.returncode, 0, early.stdout + early.stderr)
        self.assertIn(tree, self.temporary_worktrees(), 'not removed from under running checks')
        self.stop_stragglers()
        git(self.root, 'checkout', '-q', '-b', 'later')
        self.assertEqual(self.commit('--allow-empty').returncode, 0)
        later = self.push('later')
        self.assertEqual(later.returncode, 0, later.stdout + later.stderr)
        self.assertEqual(self.temporary_worktrees(), [])
        self.assertFalse(Path(tree).parent.exists())

    def test_a_running_hooks_worktree_is_never_reclaimed(self):
        hook = self.start_hook()
        [tree] = self.temporary_worktrees()
        self.slow.unlink()
        git(self.root, 'checkout', '-q', '-b', 'topic')
        (self.root / 'shared/guide.md').write_text(GUIDE + '\nMore text.\n')
        self.assertEqual(self.commit().returncode, 0)
        other = self.push('topic')
        self.assertEqual(other.returncode, 0, other.stdout + other.stderr)
        self.assertIn(tree, self.temporary_worktrees())
        hook.send_signal(signal.SIGTERM)
        hook.wait(timeout=10)

    def test_reclaim_touches_only_its_own_dead_runs(self):
        self.assertEqual(self.commit().returncode, 0)
        finished = subprocess.Popen(['true'])
        finished.wait()
        dead = str(finished.pid)
        own = self.tmp / 'zloop-pre-push.Ab12Cd'
        own.mkdir()
        (own / 'pid').write_text(dead)
        (own / 'group').write_text(dead)
        fresh = self.tmp / 'zloop-pre-push.Fr35hX'  # killed before recording its checks: may still run
        fresh.mkdir()
        (fresh / 'pid').write_text(dead)
        others = {'zloop-pre-push.Ab12C': dead, 'zloop-pre-push.Ab12Cde': dead,
                  'zloop-pre-push.Xy34Zw': 'abc', 'zloop-pre-push.Qr56St': ''}
        for name, owner in others.items():  # wrong name length or no numeric owner: not provably the hook's
            (self.tmp / name).mkdir()
            (self.tmp / name / 'pid').write_text(owner)
        mine = self.base / 'zloop-pre-push.mine'  # a user's directory that happens to share the prefix
        nopid = self.base / 'zloop-pre-push.Zz99Zz'  # no pid file: not provably the hook's
        for directory in (mine, nopid):
            git(self.root, 'worktree', 'add', '-q', '--detach', str(directory / 'tree'))
            (directory / 'keep.txt').write_text('user data\n')
        (mine / 'pid').write_text(dead)  # a dead owner, but not the hook's name
        moved = self.base / 'external'
        git(self.root, 'worktree', 'add', '-q', '--detach', str(moved))
        moved.rename(self.base / 'external.away')  # an unmounted volume looks like this
        result = self.push('main')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(own.exists(), 'an unregistered leftover of a dead run is swept')
        for name in others:
            self.assertTrue((self.tmp / name).exists(), name)
        self.assertTrue(fresh.exists(), 'without a recorded group, kept for an hour')
        listed = git(self.root, 'worktree', 'list', '--porcelain')
        for directory in (mine, nopid):
            self.assertTrue((directory / 'keep.txt').exists())
            self.assertIn(str((directory / 'tree').resolve()), listed)
        self.assertIn(str(moved.resolve()), listed, 'another worktree is not pruned')


if __name__ == '__main__':
    unittest.main()
