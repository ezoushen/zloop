#!/usr/bin/env python3
"""A session that works on zloop's own repository is told, and stopped before dispatch or pr, while the plugin it
loaded is behind the repository's `origin/main`."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support

URL = 'https://linear.app/team/project/x'


class PluginLag(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop-lag-')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.repo, self.plugin = support.plugin_repository(self.base, 'work', ahead=2)
        self.behind = support.git(self.plugin, 'rev-parse', '--short', 'HEAD')
        self.main = support.git(self.repo, 'rev-parse', '--short', 'HEAD')
        self.set_main('HEAD')

    def set_main(self, revision, repo=None):
        repo = repo or self.repo
        support.git(repo, 'update-ref', 'refs/remotes/origin/main', support.git(repo, 'rev-parse', revision))

    def lag(self):
        """What the worktree plugin root, two commits behind origin/main, is reported as."""
        return (f'the plugin root {self.plugin} is at {self.behind}, 2 behind origin/main at {self.main}, and this '
                f'session works in that repository. Refresh it with `git -C {self.plugin} pull --ff-only`, then start a '
                'new session (on Claude, /reload-plugins also works).')

    def session_start(self, plugin, cwd):
        """The lines `plugin`'s session-start hook adds to its context for a session in `cwd`."""
        result = subprocess.run([sys.executable, '-B', str(plugin / 'hooks' / 'session_start.py'), 'claude'],
                                cwd=cwd, capture_output=True, text=True, timeout=30,
                                env=support.without_host_sessions(dict(os.environ, HERDR_ENV='0')))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['hookSpecificOutput']['additionalContext'].splitlines()[1:]

    def test_session_start_warns_only_when_a_dogfooded_plugin_root_lags_main(self):
        # Rule: a session on zloop's own repository learns, from local refs alone, that its plugin root lags main.
        warned = self.session_start(self.plugin, self.repo)
        support.git(self.repo, 'update-ref', '-d', 'refs/remotes/origin/main')
        no_main = self.session_start(self.plugin, self.repo)
        self.set_main(self.behind)
        at_main = self.session_start(self.plugin, self.repo)
        self.set_main('HEAD')
        other, other_plugin = support.plugin_repository(self.base, 'other', ahead=2)
        self.set_main('HEAD', other)
        elsewhere = self.base / 'elsewhere'
        elsewhere.mkdir()
        copy = self.base / 'copy'
        for folder in ('hooks', 'scripts'):
            shutil.copytree(self.repo / folder, copy / folder)
        got = {'lagging': warned, 'no origin/main': no_main, 'at origin/main': at_main,
               'plugin root in another repository': self.session_start(other_plugin, self.repo),
               'session outside any repository': self.session_start(self.plugin, elsewhere),
               'plugin root not a checkout': self.session_start(copy, self.repo)}
        self.assertEqual(got, {'lagging': [f'zloop plugin lag: {self.lag()}'], 'no origin/main': [], 'at origin/main': [],
                               'plugin root in another repository': [], 'session outside any repository': [],
                               'plugin root not a checkout': []})

    def phase(self, phase, plugin=None):
        """(exit status, stderr, card before, card after) of `phase` on a fresh card of the working repository, run by
        the copy of autopilot_card.py in `plugin`."""
        extra = {'publish-probe': 'ok 2026-11-27T09:00:00Z acme/widgets'}
        card = support.run_card(self.repo / '.scratch' / 'run' / 'autopilot.md', phase='refine', board=URL, **extra)
        before = card.read_text()
        result = subprocess.run([sys.executable, '-B', str((plugin or self.plugin) / 'scripts' / 'autopilot_card.py'),
                                 'phase', str(card), phase], capture_output=True, text=True, timeout=30)
        return result.returncode, result.stderr.strip(), before, card.read_text()

    def test_dispatch_and_pr_phases_refuse_a_lagging_dogfooded_plugin_root(self):
        # Rule: a run never enters dispatch or pr on hooks older than the repository's main; the card stays as it was.
        got, expected = {}, {}
        for phase in ('dispatch', 'pr'):
            status, error, before, after = self.phase(phase)
            got[f'{phase} lagging'] = (status, error, after == before)
            expected[f'{phase} lagging'] = (1, f'{self.lag()} Then set the phase.', True)
        self.set_main(self.behind)
        for phase in ('dispatch', 'pr'):
            status, error, before, after = self.phase(phase)
            got[f'{phase} at origin/main'] = (status, error, after)
            expected[f'{phase} at origin/main'] = (0, '', before.replace('phase: refine\n', f'phase: {phase}\n'))
        self.assertEqual(got, expected)


if __name__ == '__main__':
    unittest.main()
