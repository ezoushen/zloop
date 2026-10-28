#!/usr/bin/env python3
"""A resolved route to a worker runtime carries that runtime's native launch controls for the agent's write scope.

Expected flags and native values are the verified ones in the retro's startup-controls notes, never the resolver's.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import support


SCRIPT = support.script('agent_models.py')


class LaunchControls(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop launch ')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.user = self.root / 'executors.json'
        herdr = {'type': 'skill', 'config': {'skill': 'herdr', 'instruction': 'Run one worker.'}}
        self.data = {'runtimes': {runtime: {'executors': {'herdr': herdr, runtime: {'type': 'subagent'}}}
                                  for runtime in ('claude', 'codex', 'opencode', 'pi')},
                     'models': {}, 'agents': {}}
        for name, runtime, model in (('opus', 'claude', 'claude-opus-5-5'), ('sol', 'codex', 'gpt-6.1-sol'),
                                     ('flash', 'opencode', 'opencode/deepseek-v4.1-flash-free'),
                                     ('glm', 'pi', 'dgx/glm-5.3-flash')):
            self.data['models'][name] = {'description': name, 'runtimes': {runtime: {
                'id': model, 'thinking': {'default': 'high', 'allowed': ['high']}}}}

    def route(self, model, job, host='claude', *options, code=0):
        """The --route output for a job whose agent uses this model, from a host Herdr bridges to it (stderr when
        the resolver refuses)."""
        self.data['agents'] = {'builder': {'model': model}, 'reviewer': {'model': model}}
        self.user.write_text(json.dumps(self.data))
        process = subprocess.run([sys.executable, '-B', str(SCRIPT), '--repo', str(self.root), '--host', host,
                                  '--job', job, '--route', '--user-executors', str(self.user),
                                  '--user-config', str(self.root / 'absent.json'), *options],
                                 capture_output=True, text=True,
                                 env=support.isolated_env(self.root, HERDR_ENV='1', ZLOOP_PROFILE='',
                                                          ZLOOP_SESSION=''))
        self.assertEqual(process.returncode, code, process.stderr)
        return json.loads(process.stdout) if code == 0 else process.stderr

    def places(self):
        """--worktree and --reports as a host passes them (unresolved), and the resolved paths a launch must name,
        as Codex records them (/private/var for /var)."""
        worktree, reports = self.root / 'wt', self.root / 'reports'
        return (['--worktree', str(worktree), '--reports', str(reports)],
                str(worktree.resolve()), str(reports.resolve()), str(self.root.resolve() / '.git'))

    def test_codex_route_sets_a_permissions_profile_for_the_write_scope_and_never_asks(self):
        # Rule: a profile reads everything; `none` writes only $TMPDIR and the reports directory, so checks still run;
        # `worktree` also writes the worktree, the main checkout's .git, where a linked worktree commits, and the
        # worktree's own git directory. Approval never, model and effort explicit.
        options, worktree, reports, git = self.places()
        for args in (['init', '-q', str(self.root)],
                     ['-C', str(self.root), 'commit', '-q', '--allow-empty', '-m', 'init'],
                     ['-C', str(self.root), 'worktree', 'add', '-q', worktree]):
            subprocess.run(['git', *args], check=True, capture_output=True)
        tail = ['-a', 'never', '-m', 'gpt-6.1-sol', '-c', 'model_reasoning_effort="high"']
        self.assertEqual(self.route('sol', 'diff-review', 'claude', *options)['launch'], {
            'writes': 'none',
            'argv': ['-c', 'default_permissions="zloop_none"',
                     '-c', 'permissions.zloop_none.filesystem={":root"="read",":tmpdir"="write",'
                     f'"{reports}"="write"}}', *tail],
            'verify': {'sandbox': 'workspace-write', 'approval': 'never',
                       'filesystem': sorted(['read :root', 'write :tmpdir', f'write {reports}'])}})
        self.assertEqual(self.route('sol', 'ticket', 'claude', *options)['launch'], {
            'writes': 'worktree',
            'argv': ['-c', 'default_permissions="zloop_worktree"',
                     '-c', 'permissions.zloop_worktree.filesystem={":root"="read",":tmpdir"="write",'
                     f'"{reports}"="write","{worktree}"="write","{git}"="write","{git}/worktrees/wt"="write"}}',
                     *tail],
            'verify': {'sandbox': 'workspace-write', 'approval': 'never', 'filesystem': sorted([
                'read :root', 'write :tmpdir', f'write {reports}', f'write {worktree}', f'write {git}',
                f'write {git}/worktrees/wt'])}})
        # A native child of the same runtime takes no launch flags; its agent profile restricts it.
        self.assertNotIn('launch', self.route('sol', 'diff-review', host='codex'))

    def test_claude_route_lets_a_read_only_agent_edit_its_reports_but_never_the_worktree(self):
        # Rule: dontAsk with the agent's tools as the allowlist; for `writes: none` absolute settings rules deny edits
        # (tools, shell redirects) under the worktree and allow them under the reports directory.
        options, worktree, reports, _ = self.places()
        settings = json.dumps({'permissions': {'deny': [f'Edit(/{worktree}/**)'], 'allow': [f'Edit(/{reports}/**)']}},
                              separators=(',', ':'))
        self.assertEqual(self.route('opus', 'diff-review', 'codex', *options)['launch'], {
            'writes': 'none',
            'argv': ['--permission-mode', 'dontAsk',
                     '--allowedTools', 'Read,Grep,Glob,Bash,Edit,Skill,SendMessage,Monitor,TaskStop',
                     '--settings', settings, '--model', 'claude-opus-5-5', '--effort', 'high'],
            'verify': {'permission_mode': 'dontAsk'}})
        self.assertIn('"deny":["Edit(//', settings)  # The absolute form: `//` and an absolute path.
        self.assertEqual(self.route('opus', 'ticket', 'codex', *options)['launch'], {
            'writes': 'worktree',
            'argv': ['--permission-mode', 'dontAsk',
                     '--allowedTools', 'Read,Edit,Write,Bash,Grep,Glob,Skill,SendMessage,Monitor,TaskStop',
                     '--model', 'claude-opus-5-5', '--effort', 'high'],
            'verify': {'permission_mode': 'dontAsk'}})

    def test_claude_refuses_a_read_only_worker_whose_reports_sit_inside_its_worktree(self):
        # Rule: the worktree deny beats the reports allow, so such a worker could never write its report.
        worktree = self.root / 'wt'
        error = self.route('opus', 'diff-review', 'pi', '--worktree', str(worktree),
                           '--reports', str(worktree / '.scratch' / 'reports'), code=2)
        self.assertIn('the reports directory is inside the worktree, whose edit deny beats the reports allow', error)
        self.assertIn('review from a separate worktree, such as .worktrees/review-<slug>', error)

    def test_codex_builder_writes_the_shared_git_directory_of_a_linked_worktree_repo(self):
        # Rule: a linked worktree's commits land in the main checkout's git directory, which git reports as the
        # common dir; a --repo that is itself a linked worktree must still name it.
        main, linked = self.root / 'main', self.root / 'linked'
        for args in (['init', '-q', str(main)], ['-C', str(main), 'commit', '-q', '--allow-empty', '-m', 'init'],
                     ['-C', str(main), 'worktree', 'add', '-q', str(linked)]):
            subprocess.run(['git', *args], check=True, capture_output=True)
        launch = self.route('sol', 'ticket', 'claude', '--repo', str(linked), '--worktree', str(linked),
                            '--reports', str(self.root / 'reports'))['launch']
        self.assertIn(f'write {main.resolve() / ".git"}', launch['verify']['filesystem'])
        self.assertNotIn(f'write {linked.resolve() / ".git"}', launch['verify']['filesystem'])

    def test_codex_builder_writes_its_own_worktree_git_directory(self):
        # Rule: Codex keeps a linked worktree's own git directory (.git/worktrees/<name>, where its index.lock goes)
        # read-only under a write on the common dir, so the builder needs an explicit write entry for it.
        main, worktree = self.root / 'main', self.root / 'wt'
        for args in (['init', '-q', str(main)], ['-C', str(main), 'commit', '-q', '--allow-empty', '-m', 'init'],
                     ['-C', str(main), 'worktree', 'add', '-q', str(worktree)]):
            subprocess.run(['git', *args], check=True, capture_output=True)
        options, _, _, _ = self.places()
        launch = self.route('sol', 'ticket', 'claude', '--repo', str(main), *options)['launch']
        own = main.resolve() / '.git' / 'worktrees' / 'wt'
        self.assertIn(f'write {own}', launch['verify']['filesystem'])
        self.assertIn(f'"{own}"="write"', launch['argv'][3])

    def test_a_route_whose_launch_needs_its_worktree_or_reports_is_refused_without_them(self):
        # Rule: Codex needs --reports for every scope and --worktree for `worktree`; Claude `none` needs both.
        for model, job, missing in (('sol', 'diff-review', '--reports'), ('sol', 'ticket', '--worktree, --reports'),
                                    ('opus', 'diff-review', '--worktree, --reports')):
            with self.subTest(model=model, job=job):
                self.assertIn(f'its launch controls need {missing}', self.route(model, job, 'pi', code=2))
        self.assertIn('launch', self.route('opus', 'ticket', 'pi'))  # A Claude builder needs neither.

    def test_pi_route_offers_an_explicit_tool_allowlist_for_the_write_scope(self):
        # Rule: `--tools` is an allowlist (an exclude list leaks bg_run); `none` keeps the shell for checks but no
        # write or edit tool, `worktree` gets the full built-in set; `--tools` keeps MCP tools, so `--no-mcp`;
        # provider, model and thinking explicit.
        for job, writes, tools, offered in (
                ('diff-review', 'none', 'read,grep,find,ls,bash', ['bash', 'find', 'grep', 'ls', 'read']),
                ('ticket', 'worktree', 'read,bash,edit,write,grep,find,ls',
                 ['bash', 'edit', 'find', 'grep', 'ls', 'read', 'write'])):
            with self.subTest(job=job):
                self.assertEqual(self.route('glm', job)['launch'], {
                    'writes': writes,
                    'argv': ['--tools', tools, '--no-mcp', '--provider', 'dgx', '--model', 'glm-5.3-flash',
                             '--thinking', 'high'],
                    'verify': {'tools': offered}})

    def test_opencode_route_creates_its_session_with_deny_rules_for_the_write_scope(self):
        # Rule: the TUI has no --agent or -m, so the session is created through the API with its model, variant and
        # permissions; `none` denies edit and subagent but keeps the shell for checks, `worktree` denies subagent
        # (a denied agent still writes through the general subagent).
        def deny(*actions):
            return [{'action': action, 'resource': '*', 'effect': 'deny'} for action in actions]
        for job, writes, rules in (('diff-review', 'none', deny('edit', 'subagent')),
                                   ('ticket', 'worktree', deny('subagent'))):
            with self.subTest(job=job):
                self.assertEqual(self.route('flash', job)['launch'], {
                    'writes': writes,
                    'api': {'method': 'POST', 'path': '/api/session', 'body': {
                        'agent': 'build',
                        'model': {'providerID': 'opencode', 'id': 'deepseek-v4.1-flash-free', 'variant': 'high'},
                        'permissions': rules}},
                    'verify': {'permissions': rules}})


if __name__ == '__main__':
    unittest.main()
