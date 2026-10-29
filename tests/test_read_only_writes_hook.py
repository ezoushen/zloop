#!/usr/bin/env python3
"""A Claude PreToolUse hook keeps a zloop `writes: none` subagent's writes inside the reports."""
import json
import os
from pathlib import Path
import resource
from shlex import quote
import subprocess
import sys
import tempfile
import time
import unittest

import support

HOOK = support.HOOKS / 'read_only_writes.py'
# What the hook may spend deciding a call in a session without records, whatever the call's size.
CPU_SECONDS = 1.5


class Checkouts(unittest.TestCase):
    """A main checkout with a ticket worktree and a workstream's reports directory."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop read-only ')
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.main = self.tmp / 'project'
        self.main.mkdir()
        self.git = ['git', '-C', str(self.main), '-c', 'user.name=t', '-c', 'user.email=t@t',
                    '-c', 'commit.gpgsign=false']
        subprocess.run(['git', 'init', '-q', str(self.main)], check=True)
        subprocess.run(self.git + ['commit', '-q', '--allow-empty', '-m', 'init'], check=True)
        self.worktree = self.main / '.worktrees/review-1'
        subprocess.run(self.git + ['worktree', 'add', '-q', '--detach', str(self.worktree)], check=True)
        self.reports = self.main / '.scratch/workstream/reports'
        self.reports.mkdir(parents=True)

    def assert_denied(self, decision, path, agent='zloop:reviewer'):
        self.assertIsNotNone(decision, path)
        self.assertEqual(decision['permissionDecision'], 'deny')
        reason = decision['permissionDecisionReason']
        for words in (agent, 'writes: none', str(path), f'{self.main}/.scratch/<slug>/reports/'):
            self.assertIn(words, reason)


class ReadOnlyWrites(Checkouts):
    """A Claude subagent; the orchestrator's cwd is the worktree, as a subagent's payload shows."""

    def setUp(self):
        super().setUp()
        # The orchestrator's transcript; a subagent's own lies beside it, in <session>/subagents/agent-<agentId>.jsonl.
        self.transcript = self.tmp / 'session.jsonl'

    def assign(self, agent_id, text):
        """Write a subagent's transcript whose first user message is its assignment, as Claude Code records it."""
        path = self.tmp / 'session/subagents' / f'agent-{agent_id}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'type': 'user', 'isSidechain': True, 'agentId': agent_id,
                                    'message': {'role': 'user', 'content': text}}) + '\n')
        return path

    def run_hook(self, tool, tool_input, agent='zloop:reviewer', cwd=None, agent_id='a1'):
        """The hook's decision for one tool call a subagent of type `agent` (None: the main session) makes."""
        payload = {'hook_event_name': 'PreToolUse', 'tool_name': tool, 'tool_input': tool_input,
                   'cwd': str(cwd or self.worktree), 'session_id': 'host-1', 'transcript_path': str(self.transcript),
                   **({'agent_type': agent, 'agent_id': agent_id} if agent else {})}
        result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True,
                                text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['hookSpecificOutput'] if result.stdout.strip() else None

    def test_a_read_only_agent_without_an_assignment_writes_in_the_checkouts_only_under_the_reports(self):
        # Rule (T11): Edit, Write and NotebookEdit from a `writes: none` agent (reviewer, verifier, under its plugin
        # name or a generated profile) are denied inside the main checkout and its worktrees, wherever they lie,
        # except under <main>/.scratch/<slug>/reports/; outside that family (/tmp, an isolated copy) they are allowed.
        subprocess.run(self.git + ['worktree', 'add', '-q', '--detach', str(self.tmp / 'elsewhere-wt')], check=True)
        inside = self.worktree / 'src/app.py'
        report = self.reports / '03.md'
        notebook = self.worktree / 'n.ipynb'
        family = self.tmp / 'elsewhere-wt/src/app.py'
        edit = {'old_string': 'a', 'new_string': 'b'}
        for agent in ('zloop:reviewer', 'zloop:verifier', 'zloop-reviewer-0123456789ab'):
            with self.subTest(agent=agent):
                self.assert_denied(self.run_hook('Edit', {'file_path': str(inside), **edit}, agent), inside, agent)
                self.assert_denied(self.run_hook('Write', {'file_path': str(family), 'content': 'x'}, agent),
                                   family, agent)
                self.assert_denied(self.run_hook('NotebookEdit', {'notebook_path': str(notebook), 'new_source': 'x'},
                                                 agent), notebook, agent)
                # The reports directory itself, and a look-alike path that only starts with it, are not a report.
                lookalike = self.main / '.scratch/workstream/reports-old/03.md'
                self.assert_denied(self.run_hook('Write', {'file_path': str(lookalike), 'content': 'x'}, agent),
                                   lookalike, agent)
                escape = f'{self.reports}/../../../src/app.py'
                self.assert_denied(self.run_hook('Edit', {'file_path': escape, **edit}, agent),
                                   self.main / 'src/app.py', agent)
                self.assertIsNone(self.run_hook('Edit', {'file_path': str(report), **edit}, agent))
                self.assertIsNone(self.run_hook('Write', {'file_path': str(self.reports / 'notes/run.log'),
                                                          'content': 'x'}, agent))
                for outside in (self.tmp / 'copy/src/app.py', Path('/private/var/tmp/zloop-probe.txt')):
                    self.assertIsNone(self.run_hook('Write', {'file_path': str(outside), 'content': 'x'}, agent))

    def test_a_read_only_agent_also_writes_in_the_worktree_its_assignment_names(self):
        # Rule (T11 review): a reviewer keeps its temporary experiment edits, which collection's restored-worktree
        # check guards, in the `worktree:` its assignment (the first user message of its own transcript) names, before
        # registration too; another worktree and the main checkout stay denied, and so does an assignment naming the
        # main checkout; an agent with no readable assignment gets the reports only.
        other = self.main / '.worktrees/review-2'
        subprocess.run(self.git + ['worktree', 'add', '-q', '--detach', str(other)], check=True)
        self.assign('a1', f'job_file: /plugin/skills/review/jobs/reproduce.md\nworktree: {self.worktree}\n'
                          f'report: {self.reports}/03.md\n')
        self.assign('a4', f'worktree: {self.main}\n')
        mine, theirs, tracked = self.worktree / 'src/app.py', other / 'src/app.py', self.main / 'src/app.py'
        edit = {'old_string': 'a', 'new_string': 'b'}
        self.assertIsNone(self.run_hook('Edit', {'file_path': str(mine), **edit}))
        self.assertIsNone(self.run_hook('Bash', {'command': 'echo x > src/app.py'}))
        self.assertIsNone(self.run_hook('Edit', {'file_path': str(self.reports / '03.md'), **edit}))
        for path in (tracked, theirs):
            decision = self.run_hook('Edit', {'file_path': str(path), **edit})
            self.assert_denied(decision, path)
            self.assertIn(f'its assigned worktree {self.worktree}', decision['permissionDecisionReason'])
        self.assert_denied(self.run_hook('Bash', {'command': f'echo x > {quote(str(tracked))}'}), tracked)
        self.assert_denied(self.run_hook('Edit', {'file_path': str(tracked), **edit}, agent_id='a4'), tracked)
        # Rule (T11 re-review): `cd` with flags, `pushd` and `builtin cd` move the command like `cd`, so a relative
        # write after them leaves the assigned worktree.
        main = quote(str(self.main))
        for command in (f'cd -P {main}; echo x > src/app.py', f'cd -- {main}; echo x > src/app.py',
                        f'cd -L -P -- {main} && echo x > src/app.py', f'pushd {main} && echo x > src/app.py',
                        f'builtin cd {main} && echo x > src/app.py'):
            with self.subTest(command=command):
                self.assert_denied(self.run_hook('Bash', {'command': command}), tracked)
        unassigned = self.run_hook('Edit', {'file_path': str(mine), **edit}, agent_id='a3')
        self.assert_denied(unassigned, mine)
        self.assertNotIn('assigned worktree', unassigned['permissionDecisionReason'])

    def test_a_read_only_agent_never_rewrites_its_sessions_transcripts(self):
        # Rule (T11 re-review): the assignment that widens the allowance is read from the session's transcript
        # directory (`transcript_path` without `.jsonl`), so a read-only agent may not write anything under it, from
        # inside a checkout or outside any.
        own = self.assign('a1', f'worktree: {self.worktree}\n')
        sibling = self.tmp / 'session/subagents/agent-a2.jsonl'
        for path in (own, sibling):
            with self.subTest(path=path):
                self.assert_denied(self.run_hook('Write', {'file_path': str(path), 'content': 'x'}), path)
                self.assert_denied(self.run_hook('Bash', {'command': f'echo x >> {quote(str(path))}'}), path)
                decision = self.run_hook('Edit', {'file_path': str(path), 'old_string': 'a', 'new_string': 'b'},
                                         cwd=Path('/'))
                self.assertEqual((decision or {}).get('permissionDecision'), 'deny', path)
                self.assertIn(str(path), decision['permissionDecisionReason'])

    def test_writes_to_the_reports_or_outside_the_checkouts_never_fail_on_an_unrelated_lookup(self):
        # Rule (T11 review): the reports and paths outside the checkout family are decided before the assignment is
        # read, so a corrupt transcript denies neither; a session outside any checkout protects none.
        self.assign('a5', 'not json').write_text('{not json\n')
        report, scratch = self.reports / '03.md', self.tmp / 'scratch/notes.txt'
        for agent_id, cwd in (('a5', None), ('a1', Path('/'))):
            with self.subTest(agent_id=agent_id, cwd=cwd):
                self.assertIsNone(self.run_hook('Write', {'file_path': str(report), 'content': 'x'},
                                                cwd=cwd, agent_id=agent_id))
                self.assertIsNone(self.run_hook('Write', {'file_path': str(scratch), 'content': 'x'},
                                                cwd=cwd, agent_id=agent_id))
                self.assertIsNone(self.run_hook('Bash', {'command': f'echo x > {quote(str(scratch))}'},
                                                cwd=cwd, agent_id=agent_id))
        # A corrupt assignment is no assignment: its worktree stays denied.
        self.assert_denied(self.run_hook('Edit', {'file_path': str(self.worktree / 'a.py'), 'old_string': 'a',
                                                  'new_string': 'b'}, agent_id='a5'), self.worktree / 'a.py')

    def test_a_read_only_agents_shell_redirects_and_tee_only_into_the_reports(self):
        # Rule (T11): a Bash command from a `writes: none` agent that clearly writes a literal path by `>`, `>>`,
        # `&>` or `tee` outside the reports is denied; relative paths follow the command's own `cd`.
        app, log = self.worktree / 'src/app.py', self.worktree / 'out.log'
        denied = {
            'echo x > src/app.py': app,
            f'echo x >> {quote(str(app))}': app,
            'make test 2>/dev/null | tee out.log': log,
            'tee -a out.log < /dev/null': log,
            f'cd {quote(str(self.worktree / "src"))} && echo x >app.py': app,
            'npm test &> out.log': log,
            'cd -- -x && echo x > a.py': self.worktree / '-x/a.py',  # After `--`, `-x` is the directory.
            f'cat <<EOF > {quote(str(app))}\nx\nEOF': app,
            f'git -C {quote(str(self.worktree))} status; echo x > "{app}"': app,
            f'echo x > {quote("/dev/.." + str(app))}': app,  # Resolved before any device is exempt.
            # Rule (T15 review G7, G8): the open-quote scan follows shlex's comment rule, `#` outside quotes, inside a
            # word too, so a quote after it opens nothing and the lines around it are still lexed.
            "# the 'old path\necho x > src/app.py": app,
            "echo x >> a#b | cat \"\n) x\n'\" > f": self.worktree / 'a',
        }
        for command, path in denied.items():
            with self.subTest(command=command):
                self.assert_denied(self.run_hook('Bash', {'command': command}), path)

    def test_a_shell_command_that_writes_no_clear_path_outside_the_reports_runs(self):
        # Rule (T11), the conservative side: devices, fd copies, the reports, quoted or heredoc `>`, comparisons
        # and paths only known at run time are not flagged (hosts/claude/workers.md lists what stays uncaught).
        allowed = [
            'make test > /dev/null 2>&1',
            'ls missing 2>/dev/null >&2',
            f'echo x > {quote(str(self.reports / "03.md"))}',
            f'make test | tee -a {quote(str(self.reports / "run.log"))}',
            f'cd {quote(str(self.reports))} && echo x > 03.md',
            'grep -n ">" src/app.py',
            "awk '{ print > \"out\" }' src/app.py",
            f'cat <<\'EOF\' > {quote(str(self.reports / "03.md"))}\nexpected a > b\nEOF',
            '(( count > 3 )) && echo many',
            '[[ b > a ]] && echo later',
            'echo x > "$OUT"',
            'echo x > $(mktemp)',
            "python3 -c \"open('src/app.py', 'w')\"",
            'git status --short',
        ]
        for command in allowed:
            with self.subTest(command=command):
                self.assertIsNone(self.run_hook('Bash', {'command': command}))

    def test_writers_other_agents_and_the_main_session_are_not_checked(self):
        # Rule (T11): only `writes: none` zloop agents are held to their reports.
        inside = {'file_path': str(self.worktree / 'src/app.py'), 'content': 'x'}
        for agent in ('zloop:builder', 'zloop-builder-0123456789ab', 'general-purpose', 'reviewer', None):
            with self.subTest(agent=agent):
                self.assertIsNone(self.run_hook('Write', inside, agent))
                self.assertIsNone(self.run_hook('Bash', {'command': 'echo x > src/app.py'}, agent))

    def test_claude_runs_the_check_before_every_file_and_shell_tool(self):
        # Rule (T11): hooks/hooks.json registers the check as a PreToolUse hook for Edit, Write, NotebookEdit and Bash;
        # the registered command itself denies a reviewer's write into its worktree.
        config = json.loads((support.HOOKS / 'hooks.json').read_text())['hooks']['PreToolUse']
        commands = {tool: [hook['command'] for entry in config if tool in entry['matcher'].split('|')
                           for hook in entry['hooks'] if hook['command'].endswith('hooks/read_only_writes.py"')]
                    for tool in ('Edit', 'Write', 'NotebookEdit', 'Bash')}
        for tool, found in commands.items():
            self.assertEqual(len(found), 1, (tool, commands))
        tool_inputs = {'Edit': {'file_path': 'src/app.py', 'old_string': 'a', 'new_string': 'b'},
                       'Write': {'file_path': 'src/app.py', 'content': 'x'},
                       'NotebookEdit': {'notebook_path': 'n.ipynb', 'new_source': 'x'},
                       'Bash': {'command': 'echo x > src/app.py'}}
        for tool, [command] in commands.items():
            payload = {'hook_event_name': 'PreToolUse', 'tool_name': tool, 'tool_input': tool_inputs[tool],
                       'cwd': str(self.worktree), 'agent_type': 'zloop:reviewer', 'agent_id': 'a1'}
            result = subprocess.run(command.replace('${CLAUDE_PLUGIN_ROOT}', str(support.ROOT)), shell=True,
                                    input=json.dumps(payload), capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'deny', tool)


    def test_a_long_multi_line_command_is_judged_well_within_the_hook_timeout(self):
        # Rule (T15 review G3): the shell parser is linear, so a write beside a 64 KB multi-line quoted commit
        # message is still denied well within the 10 s hook timeout (hooks/hooks.json), not let through by it.
        body = ('x' * 79 + '\n') * 820
        started = time.monotonic()
        decision = self.run_hook('Bash', {'command': f"echo x > src/app.py; git commit -m '{body}'"})
        self.assertLess(time.monotonic() - started, 8)
        self.assert_denied(decision, self.worktree / 'src/app.py')

    def test_a_read_only_agent_never_writes_a_git_path_in_its_assigned_worktree(self):
        # Rule (T15 review G6): a `.git` file or directory is never a reviewer's experiment edit, and a junk one can
        # mislead whoever reads the checkout's layout, so the assigned worktree allows no path through `.git`.
        self.assign('a1', f'worktree: {self.worktree}\n')
        for path in (self.worktree / '.git', self.worktree / 'sub/.git/config'):
            with self.subTest(path=path):
                self.assert_denied(self.run_hook('Write', {'file_path': str(path), 'content': 'x'}), path)
        self.assertIsNone(self.run_hook('Write', {'file_path': str(self.worktree / 'a.git.py'), 'content': 'x'}))

class CodexChildren(Checkouts):
    """A Codex parent session s1 whose native children run in the worktree; Codex names a child only by `agent_id`,
    its rollout (`transcript_path`) and the parent's `session_id`, so the parent records each assignment first."""

    def setUp(self):
        super().setUp()
        self.records = self.main / '.scratch/codex-children/s1'
        self.agents = support.ROOT / 'agents'
        # The zloop state directory, where codex-assign also indexes each session's recording checkouts.
        self.env = dict({key: value for key, value in os.environ.items() if not key.startswith('CODEX_')},
                        XDG_STATE_HOME=str(self.tmp / 'state'))

    def record(self, task, agent_file, worktree=None, session='s1', thread='s1', cwd=None):
        """Run the parent's record command as Codex runs a shell command: CODEX_SESSION_ID is the root session,
        CODEX_THREAD_ID the thread running it."""
        env = dict(self.env)
        env.update({key: value for key, value in (('CODEX_SESSION_ID', session), ('CODEX_THREAD_ID', thread))
                    if value})
        command = [sys.executable, str(HOOK), 'codex-assign', '--task', task, '--agent-file', str(agent_file)]
        return subprocess.run(command + (['--worktree', str(worktree)] if worktree else []), env=env,
                              cwd=str(cwd or self.worktree), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=20)

    def test_the_parent_records_a_childs_agent_and_worktree_before_spawning_it(self):
        # Rule (T15 R1'): the Codex parent records the assignment at
        # <main>/.scratch/codex-children/<its session id>/<task_name>.json: the agent its agent_file names and the
        # worktree, real path. A child (CODEX_THREAD_ID is not the session), a shell outside Codex, an unknown agent,
        # a task name Codex would refuse, or a worktree outside the checkout's family records nothing.
        result = self.record('review_1', self.agents / 'reviewer.md', self.worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.records / 'review_1.json').read_text()),
                         {'agent': 'reviewer', 'worktree': str(self.worktree)})
        refused = {
            'child': self.record('review_2', self.agents / 'reviewer.md', self.worktree, thread='c1'),
            'outside Codex': self.record('review_3', self.agents / 'reviewer.md', self.worktree, session=None,
                                         thread=None),
            'unknown agent': self.record('review_4', self.tmp / 'agents' / 'janitor.md', self.worktree),
            'task name': self.record('Review-5', self.agents / 'reviewer.md', self.worktree),
            'worktree': self.record('review_6', self.agents / 'reviewer.md', self.tmp),
        }
        for case, result in refused.items():
            with self.subTest(case=case):
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertTrue(result.stderr.strip(), case)
        self.assertEqual(sorted(path.name for path in self.records.iterdir()), ['review_1.json'])


    def run_codex(self, tool, tool_input, agent_id='c1', task='review_1', session='s1', cwd=None, meta_id=None):
        """The guard's decision for one Codex PreToolUse call, shaped as Codex 0.163 sends it: a child's payload
        carries `agent_id` and its own rollout, whose session_meta names its task path; agent_id None is the parent.
        meta_id: the rollout's own id, when it is not the payload's."""
        rollout = self.tmp / 'codex' / f'rollout-{agent_id or session}.jsonl'
        rollout.parent.mkdir(exist_ok=True)
        meta = {'id': meta_id or agent_id or session, 'agent_path': f'/root/{task}' if agent_id else None}
        rollout.write_text(json.dumps({'type': 'session_meta', 'payload': meta}) + '\n')
        payload = {'session_id': session, 'turn_id': 't1', 'transcript_path': str(rollout),
                   'cwd': str(cwd or self.worktree), 'hook_event_name': 'PreToolUse', 'model': 'm',
                   'permission_mode': 'bypassPermissions', 'tool_name': tool, 'tool_input': tool_input,
                   'tool_use_id': 'u1', **({'agent_id': agent_id, 'agent_type': 'default'} if agent_id else {})}
        result = subprocess.run([sys.executable, str(HOOK), 'codex'], input=json.dumps(payload), capture_output=True,
                                text=True, timeout=30, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['hookSpecificOutput'] if result.stdout.strip() else None

    def test_a_recorded_read_only_child_writes_only_its_reports_and_its_worktree(self):
        # Rule (T15 R2'): a child whose record names a `writes: none` agent gets the Claude guard's decisions:
        # the reports and its recorded worktree allowed, the rest of the checkout family denied, outside allowed.
        other = self.main / '.worktrees/review-2'
        subprocess.run(self.git + ['worktree', 'add', '-q', '--detach', str(other)], check=True)
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        for path in (other / 'a.py', self.main / 'src/app.py'):
            with self.subTest(path=path):
                decision = self.run_codex('Bash', {'command': f'echo x > {quote(str(path))}'})
                self.assert_denied(decision, path, 'Codex child review_1')
                self.assertIn(f'its assigned worktree {self.worktree}', decision['permissionDecisionReason'])
        for path in (self.worktree / 'a.py', self.reports / '03.md', self.tmp / 'copy/a.py'):
            with self.subTest(path=path):
                self.assertIsNone(self.run_codex('Bash', {'command': f'echo x > {quote(str(path))}'}))

    def test_a_child_without_a_record_in_a_recording_session_is_held_read_only(self):
        # Rule (T15 R2'): in a session that has records, a child with none (or whose rollout cannot be read) may
        # write only the reports inside the checkout family: fail closed.
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        mine = self.worktree / 'a.py'
        decision = self.run_codex('Bash', {'command': f'echo x > {quote(str(mine))}'}, task='review_9')
        self.assert_denied(decision, mine, 'Codex child review_9')
        self.assertIn(f'no assignment record in {self.records}', decision['permissionDecisionReason'])
        self.assertIsNone(self.run_codex('Bash', {'command': f'echo x > {quote(str(self.reports / "03.md"))}'},
                                         task='review_9'))

    def test_sessions_without_records_and_the_parent_itself_are_not_checked(self):
        # Rule (T15 R2'): a session with no record directory (Codex used without zloop dispatch) and every call
        # of the parent (no agent_id) are allowed, wherever they write.
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        tracked = {'command': f'echo x > {quote(str(self.main / "src/app.py"))}'}
        self.assertIsNone(self.run_codex('Bash', tracked, session='s2'))
        self.assertIsNone(self.run_codex('Bash', tracked, agent_id=None))
        self.assertIsNone(self.run_codex('Bash', {'command': f'echo x > {quote(str(self.records / "x.json"))}'},
                                         agent_id=None))

    def test_no_child_writes_the_records_and_no_record_is_rewritten(self):
        # Rule (T15 R2', review F4): the record directory widens what a child may write, so every child, a recorded
        # writer included, is denied writing under it; a writer is otherwise unchecked. A record is never
        # overwritten, so a child cannot rewrite its own once its parent recorded it before spawning; merely naming
        # codex-assign (grep, a patch to a doc) is no write.
        self.assertEqual(self.record('build_1', self.agents / 'builder.md', self.worktree).returncode, 0)
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        self.assertIsNone(self.run_codex('Bash', {'command': f'echo x > {quote(str(self.main / "src/app.py"))}'},
                                         task='build_1'))
        forged = self.records / 'build_1.json'
        # The records directory has the shape of <main>/.scratch/<slug>/reports/ too; it is never a report.
        lookalike = self.main / '.scratch/codex-children/reports/x.json'
        # The index naming the session's recording checkouts (codex-assign keeps it in the state directory).
        [index] = (self.tmp / 'state/zloop/codex-children/s1').iterdir()
        for task in ('build_1', 'review_9'):
            for path in (forged, lookalike, index):
                with self.subTest(task=task, path=path):
                    decision = self.run_codex('Bash', {'command': f'echo x > {quote(str(path))}'}, task=task)
                    self.assertEqual((decision or {}).get('permissionDecision'), 'deny', path)
                    self.assertIn(str(path), decision['permissionDecisionReason'])
        rewrite = self.record('review_1', self.agents / 'builder.md', self.worktree, thread=None)
        self.assertNotEqual(rewrite.returncode, 0, rewrite.stdout)
        self.assertIn('already recorded', rewrite.stderr)
        self.assertEqual(json.loads((self.records / 'review_1.json').read_text()),
                         {'agent': 'reviewer', 'worktree': str(self.worktree)})
        doc = self.worktree / 'hosts/codex/workers.md'
        mention = self.patch(f'*** Update File: {doc}', '@@', '-old', '+run codex-assign first')
        self.assertIsNone(self.run_codex('apply_patch', {'command': mention}, task='build_1'))
        for task in ('build_1', 'review_1'):
            with self.subTest(task=task):
                self.assertIsNone(self.run_codex('Bash', {'command': 'git diff | grep -n codex-assign'}, task=task))

    def test_a_read_only_childs_relative_target_is_denied_since_codex_hides_the_workdir(self):
        # Rule (T15 R3'): Codex's Bash payload omits exec_command's `workdir`, so a read-only child's relative
        # redirect target cannot be resolved; inside the checkout family it is denied, the reason saying to write
        # an absolute path. A target after an absolute `cd` is resolved as usual; a writer child is not checked.
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        self.assertEqual(self.record('build_1', self.agents / 'builder.md', self.worktree).returncode, 0)
        for command in ('echo x > a.py', 'make test | tee -a out.log', 'cd src && echo x > a.py'):
            with self.subTest(command=command):
                decision = self.run_codex('Bash', {'command': command})
                self.assertEqual((decision or {}).get('permissionDecision'), 'deny', command)
                reason = decision['permissionDecisionReason']
                for words in ('Codex child review_1', 'relative', 'workdir', 'absolute path'):
                    self.assertIn(words, reason)
                self.assertIsNone(self.run_codex('Bash', {'command': command}, task='build_1'))
        mine, theirs = quote(str(self.worktree)), quote(str(self.main))
        self.assertIsNone(self.run_codex('Bash', {'command': f'cd {mine} && echo x > a.py'}))
        self.assert_denied(self.run_codex('Bash', {'command': f'cd {theirs} && echo x > a.py'}), self.main / 'a.py',
                           'Codex child review_1')


    @staticmethod
    def patch(*hunks):
        """A patch in the grammar of Codex's apply_patch tool (codex-rs/apply-patch/src/parser.rs)."""
        return '\n'.join(['*** Begin Patch', *hunks, '*** End Patch'])

    def test_a_read_only_childs_patch_gets_the_same_decisions(self):
        # Rule (T15 R4'): Codex fires PreToolUse for its patch tool as `apply_patch` with the patch text as
        # `command`; every Add, Update, Delete and Move-to path (relative ones from the call's cwd, where the tool
        # resolves them) gets the shell write's decision.
        other = self.main / '.worktrees/review-2'
        subprocess.run(self.git + ['worktree', 'add', '-q', '--detach', str(other)], check=True)
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        self.assertEqual(self.record('build_1', self.agents / 'builder.md', self.worktree).returncode, 0)
        update = ['*** Update File: src/app.py', '@@', '-a', '+b']
        denied = {
            self.patch(f'*** Add File: {other}/new.py', '+x'): other / 'new.py',
            self.patch(f'  *** Delete File: {self.main}/src/app.py'): self.main / 'src/app.py',
            self.patch(f'*** Update File: {self.worktree}/a.py', f'*** Move to: {self.main}/moved.py', '@@', '-a',
                       '+b'): self.main / 'moved.py',
            self.patch(*update, f'*** Add File: {self.reports}/../../../x.py', '+x'): self.main / 'x.py',
        }
        for patch, path in denied.items():
            with self.subTest(patch=patch):
                self.assert_denied(self.run_codex('apply_patch', {'command': patch}), path, 'Codex child review_1')
        for patch in (self.patch(*update), self.patch(f'*** Add File: {self.reports}/03.md', '+x'),
                      self.patch(f'*** Add File: {self.tmp}/copy/a.py', '+x')):
            with self.subTest(patch=patch):
                self.assertIsNone(self.run_codex('apply_patch', {'command': patch}))
        # Without a record the child's own worktree is denied too; a writer and the parent are not checked.
        self.assert_denied(self.run_codex('apply_patch', {'command': self.patch(*update)}, task='review_9'),
                           self.worktree / 'src/app.py', 'Codex child review_9')
        tracked = self.patch(f'*** Update File: {self.main}/src/app.py', '@@', '-a', '+b')
        self.assertIsNone(self.run_codex('apply_patch', {'command': tracked}, task='build_1'))
        self.assertIsNone(self.run_codex('apply_patch', {'command': tracked}, agent_id=None))
        forged = self.patch(f'*** Add File: {self.records}/build_2.json', '+{}')
        self.assertEqual(self.run_codex('apply_patch', {'command': forged}, task='build_1')['permissionDecision'],
                         'deny')

    def test_a_read_only_childs_shell_apply_patch_gets_the_same_decisions(self):
        # Rule (T15 R4'): `apply_patch <<'EOF'` through the shell, which Codex intercepts and applies itself, arrives
        # as Bash: its patch paths are writes, relative ones unresolved like any relative shell target.
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        tracked = self.main / 'src/app.py'
        body = self.patch(f'*** Update File: {tracked}', '@@', '-a', '+b')
        self.assert_denied(self.run_codex('Bash', {'command': f"apply_patch <<'EOF'\n{body}\nEOF\n"}), tracked,
                           'Codex child review_1')
        relative = self.patch('*** Update File: src/app.py', '@@', '-a', '+b')
        decision = self.run_codex('Bash', {'command': f"cd src && apply_patch <<'EOF'\n{relative}\nEOF\n"})
        self.assertIn('src/src/app.py is relative', decision['permissionDecisionReason'])
        mine = self.patch(f'*** Update File: {self.worktree}/a.py', '@@', '-a', '+b')
        self.assertIsNone(self.run_codex('Bash', {'command': f"applypatch <<'EOF'\n{mine}\nEOF\n"}))

    def test_codex_runs_the_check_before_every_shell_and_patch_call(self):
        # Rule (T15 R4'): hosts/codex/hooks.json registers `read_only_writes.py codex` as a PreToolUse hook whose
        # matcher (exact names joined by `|`, as Codex reads them) selects both Bash and apply_patch, after the
        # trusted launch hook, whose position keeps its trust; the registered command denies a reviewer's patch.
        config = json.loads((support.ROOT / 'hosts/codex/hooks.json').read_text())['hooks']['PreToolUse']
        self.assertTrue(config[0]['hooks'][0]['command'].endswith('hooks/herdr_launch.py"'), config[0])
        found = [(entry['matcher'].split('|'), hook['command']) for entry in config for hook in entry['hooks']
                 if hook['command'].endswith('hooks/read_only_writes.py" codex')]
        self.assertEqual(len(found), 1, config)
        [(names, command)] = found
        self.assertLessEqual({'Bash', 'apply_patch'}, set(names))
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        rollout = self.tmp / 'codex/rollout-c1.jsonl'
        rollout.parent.mkdir(exist_ok=True)
        meta = {'id': 'c1', 'agent_path': '/root/review_1'}
        rollout.write_text(json.dumps({'type': 'session_meta', 'payload': meta}))
        payload = {'session_id': 's1', 'agent_id': 'c1', 'transcript_path': str(rollout), 'cwd': str(self.worktree),
                   'hook_event_name': 'PreToolUse', 'tool_name': 'apply_patch',
                   'tool_input': {'command': self.patch(f'*** Add File: {self.main}/x.py', '+x')}}
        result = subprocess.run(command.replace('${PLUGIN_ROOT}', str(support.ROOT)), shell=True,
                                input=json.dumps(payload), capture_output=True, text=True, timeout=20, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')


    def test_a_failed_checkout_lookup_before_any_record_is_found_allows_the_call(self):
        # Rule (T15 review F1): the guard fails closed only once the session's record directory is known to exist. A
        # call that writes nothing is allowed, and so is one whose checkout lookup fails first (a removed turn cwd, a
        # repository git cannot read), so Codex sessions without records are never blocked.
        broken = self.tmp / 'broken'
        subprocess.run(['git', 'init', '-q', str(broken)], check=True)
        (broken / '.git/config').write_text('[core\nbroken')
        outside = {'command': f'echo x > {quote(str(self.tmp / "copy/a.py"))}'}
        for cwd in (self.tmp / 'gone', broken):
            for tool_input in ({'command': 'ls'}, outside):
                with self.subTest(cwd=cwd, command=tool_input['command']):
                    self.assertIsNone(self.run_codex('Bash', tool_input, cwd=cwd))


    def test_no_child_rewrites_its_own_rollout(self):
        # Rule (T15 review F3): a child's task name, which picks its record, comes from its own rollout
        # (transcript_path), so in a session with records no child, a writer included, writes that file: else a
        # reviewer could claim a sibling builder's task.
        self.assertEqual(self.record('build_1', self.agents / 'builder.md', self.worktree).returncode, 0)
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        meta = json.dumps({'type': 'session_meta', 'payload': {'id': 'c1', 'agent_path': '/root/build_1'}})
        for task in ('review_1', 'build_1'):
            rollout = self.tmp / 'codex/rollout-c1.jsonl'
            for tool, tool_input in (('Bash', {'command': f'echo {quote(meta)} > {quote(str(rollout))}'}),
                                     ('apply_patch', {'command': self.patch(f'*** Update File: {rollout}', '@@',
                                                                            '-a', '+b')})):
                with self.subTest(task=task, tool=tool):
                    decision = self.run_codex(tool, tool_input, task=task)
                    self.assertEqual((decision or {}).get('permissionDecision'), 'deny', tool)
                    self.assertIn(str(rollout), decision['permissionDecisionReason'])


    def test_records_are_found_from_a_written_path_when_the_turn_cwd_is_outside_the_checkout(self):
        # Rule (T15 review F5): the payload has no workdir and the turn cwd may lie outside any checkout, so the
        # session's records are also looked up from the checkout of each absolute path the call writes.
        other = self.main / '.worktrees/review-2'
        subprocess.run(self.git + ['worktree', 'add', '-q', '--detach', str(other)], check=True)
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        outside = self.tmp / 'elsewhere'
        outside.mkdir()
        tracked = self.main / 'src/app.py'
        self.assert_denied(self.run_codex('Bash', {'command': f'echo x > {quote(str(tracked))}'}, cwd=outside),
                           tracked, 'Codex child review_1')
        self.assert_denied(self.run_codex('apply_patch', {'command': self.patch(f'*** Add File: {other}/x.py', '+x')},
                                          cwd=outside), other / 'x.py', 'Codex child review_1')
        self.assertIsNone(self.run_codex('Bash', {'command': f'echo x > {quote(str(self.worktree / "a.py"))}'},
                                         cwd=outside))


    def test_a_record_or_rollout_that_cannot_be_trusted_holds_the_child_read_only(self):
        # Rule (T15 R2', review F6): in a session with records, a corrupt or unreadable record, or a rollout whose
        # own id is not the payload's agent_id, gives no assignment: the child may write only the reports.
        for task, agent in (('build_1', 'builder'), ('build_2', 'builder'), ('review_1', 'reviewer')):
            self.assertEqual(self.record(task, self.agents / f'{agent}.md', self.worktree).returncode, 0)
        (self.records / 'build_1.json').write_text('{not json\n')
        (self.records / 'build_2.json').chmod(0)
        self.addCleanup((self.records / 'build_2.json').chmod, 0o644)
        tracked, mine = self.main / 'src/app.py', self.worktree / 'a.py'
        for task, meta_id, path in (('build_1', None, tracked), ('build_2', None, tracked),
                                    ('review_1', 'c8', mine)):
            with self.subTest(task=task, meta_id=meta_id):
                decision = self.run_codex('Bash', {'command': f'echo x > {quote(str(path))}'}, task=task,
                                          meta_id=meta_id)
                self.assert_denied(decision, path, 'Codex child')
                self.assertIn(f'no assignment record in {self.records}', decision['permissionDecisionReason'])

    def test_a_session_id_codex_would_not_issue_names_no_records(self):
        # Rule (T15 R1', R2', review F6): a session id is a path segment of the records, so one with characters
        # outside Codex's ids ([0-9A-Za-z-]) is never looked up (the call is unchecked) and never recorded.
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        tracked = {'command': f'echo x > {quote(str(self.main / "src/app.py"))}'}
        for session in ('.', 's1/'):
            with self.subTest(session=session):
                self.assertIsNone(self.run_codex('Bash', tracked, session=session))
        result = self.record('review_2', self.agents / 'reviewer.md', self.worktree, session='../s1', thread=None)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('session id', result.stderr)
        self.assertFalse((self.main / '.scratch/s1').exists())


    def test_a_session_without_records_is_decided_without_parsing_the_call(self):
        # Rule (T15 review G1): a call in a session with no records costs near-constant time, whatever its size: a
        # 64 KB multi-line commit message, a patch updating 400 files in 400 directories.
        body = ('x' * 79 + '\n') * 820
        for index in range(400):
            (self.main / f'src/pkg{index}').mkdir(parents=True)
        patch = self.patch(*(line for index in range(400)
                             for line in (f'*** Update File: src/pkg{index}/m.py', '@@', '-a', '+b')))
        for tool, tool_input in (('Bash', {'command': f"git commit -m '{body}'"}), ('apply_patch', {'command': patch})):
            with self.subTest(tool=tool):
                # CPU time, not wall time: a loaded machine delays the hook without making it do more work.
                before = resource.getrusage(resource.RUSAGE_CHILDREN)
                self.assertIsNone(self.run_codex(tool, tool_input, session='s9', cwd=self.main))
                after = resource.getrusage(resource.RUSAGE_CHILDREN)
                self.assertLess(after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime, CPU_SECONDS)


    def test_the_codex_guard_never_exits_non_zero(self):
        # Rule (T15 review G2): Codex treats a hook that exits non-zero as not blocking, so the guard always exits 0:
        # once the session's records are known any error denies (a path too long to stat beside a write into another
        # worktree, a record whose worktree is not a path), before that it allows (an index entry that cannot be
        # read), and a payload that is not a JSON object is no call to check.
        other = self.main / '.worktrees/review-2'
        subprocess.run(self.git + ['worktree', 'add', '-q', '--detach', str(other)], check=True)
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        self.assertEqual(self.record('review_2', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        (self.records / 'review_2.json').write_text(json.dumps({'agent': 'reviewer', 'worktree': 5}))
        long = '/tmp/' + 'a' * 300
        decision = self.run_codex('Bash', {'command': f'echo x > {quote(str(other / "a.py"))}; echo y > {long}'})
        self.assert_denied(decision, other / 'a.py', 'Codex child review_1')
        mine = {'command': f'echo x > {quote(str(self.worktree / "a.py"))}'}
        self.assertEqual(self.run_codex('Bash', mine, task='review_2')['permissionDecision'], 'deny')
        (self.tmp / 'state/zloop/codex-children/s3/broken').mkdir(parents=True)
        self.assertIsNone(self.run_codex('Bash', mine, session='s3', cwd=self.tmp))
        for payload in ('[1]', '"x"', json.dumps({'tool_name': 'Bash', 'agent_id': 'c1', 'session_id': 's1',
                                                  'tool_input': 'echo', 'cwd': str(self.main)})):
            with self.subTest(payload=payload):
                result = subprocess.run([sys.executable, str(HOOK), 'codex'], input=payload, capture_output=True,
                                        text=True, timeout=20, env=self.env)
                self.assertEqual((result.returncode, result.stdout), (0, ''), result.stderr)


    def test_a_recorded_childs_long_multi_line_command_is_judged_within_the_hook_timeout(self):
        # Rule (T15 review G3): a recorded reviewer's write into another worktree beside a 64 KB multi-line quoted
        # commit message is denied well within Codex's 10 s hook timeout, past which the call would run unchecked.
        other = self.main / '.worktrees/review-2'
        subprocess.run(self.git + ['worktree', 'add', '-q', '--detach', str(other)], check=True)
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        body = ('x' * 79 + '\n') * 820
        started = time.monotonic()
        decision = self.run_codex('Bash', {'command': f"echo x > {quote(str(other / 'a.py'))}; git commit -m '{body}'"})
        self.assertLess(time.monotonic() - started, 8)
        self.assert_denied(decision, other / 'a.py', 'Codex child review_1')


    def test_a_command_too_large_to_judge_in_time_is_denied_before_the_hook_timeout(self):
        # Rule (T15 review G3 backstop): judging a call is bounded well under the 10 s hook timeouts; a recorded
        # child's or a Claude read-only agent's command that cannot be judged in that time (one 1 MB quoted word,
        # which shlex lexes in quadratic time) is denied, not left to the timeout that would let it run.
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        command = {'command': "git commit -m '" + ('x' * 79 + '\n') * 13108 + "'"}
        claude = ReadOnlyWrites(methodName='run_hook')
        claude.tmp, claude.worktree, claude.transcript = self.tmp, self.worktree, self.tmp / 'session.jsonl'
        for name, run in (('Codex child review_1', lambda: self.run_codex('Bash', command)),
                          ('zloop:reviewer', lambda: claude.run_hook('Bash', command))):
            with self.subTest(name=name):
                started = time.monotonic()
                decision = run()
                self.assertLess(time.monotonic() - started, 9)
                self.assertEqual((decision or {}).get('permissionDecision'), 'deny', name)
                self.assertIn(f'{name} is', decision['permissionDecisionReason'])
                self.assertIn('took over 5 s', decision['permissionDecisionReason'])


    def test_each_written_path_is_judged_by_the_records_of_the_checkout_it_lands_in(self):
        # Rule (T15 review G5): a session may record children in several checkouts; each path a child writes is
        # judged against the records of the checkout it lands in, whatever its turn cwd: review_b, recorded in B as
        # a reviewer of B's worktree, is held there and, having no record in A, to A's reports.
        other = self.tmp / 'other'
        subprocess.run(['git', 'init', '-q', str(other)], check=True)
        subprocess.run(['git', '-C', str(other), '-c', 'user.name=t', '-c', 'user.email=t@t', '-c',
                        'commit.gpgsign=false', 'commit', '-q', '--allow-empty', '-m', 'init'], check=True)
        theirs = other / '.worktrees/review-b'
        subprocess.run(['git', '-C', str(other), 'worktree', 'add', '-q', '--detach', str(theirs)], check=True)
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        self.assertEqual(self.record('review_b', self.agents / 'reviewer.md', theirs, cwd=other).returncode, 0)
        for path in (other / 'src.py', self.main / 'src.py', self.worktree / 'a.py'):
            with self.subTest(path=path):
                decision = self.run_codex('Bash', {'command': f'echo x > {quote(str(path))}'}, task='review_b')
                self.assertEqual((decision or {}).get('permissionDecision'), 'deny', path)
                self.assertIn(str(path), decision['permissionDecisionReason'])
        for path in (theirs / 'a.py', self.reports / '03.md'):
            with self.subTest(path=path):
                self.assertIsNone(self.run_codex('Bash', {'command': f'echo x > {quote(str(path))}'}, task='review_b'))
        # A relative target is a writer's only in the checkout of its turn cwd, where the record makes it one.
        self.assertEqual(self.record('build_b', self.agents / 'builder.md', theirs, cwd=other).returncode, 0)
        self.assertIsNone(self.run_codex('Bash', {'command': 'echo x > a.py'}, task='build_b', cwd=theirs))
        decision = self.run_codex('Bash', {'command': 'echo x > a.py'}, task='build_b')
        self.assertIn('a.py is relative', (decision or {}).get('permissionDecisionReason', ''))


    def test_records_are_found_from_the_turn_cwds_checkout_when_the_index_is_elsewhere(self):
        # Rule (T15 review G1): the index may sit in another state directory than the hook reads (another
        # XDG_STATE_HOME), so the turn cwd's checkout, found from its `.git` without git, is searched too.
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        self.env['XDG_STATE_HOME'] = str(self.tmp / 'elsewhere')
        tracked = self.main / 'src/app.py'
        self.assert_denied(self.run_codex('Bash', {'command': f'echo x > {quote(str(tracked))}'}), tracked,
                           'Codex child review_1')


    def test_one_bad_source_does_not_hide_the_records_the_other_names(self):
        # Rule (T15 review G6): the index entries and the turn cwd's `.git` are read independently, a bad one skipped
        # and the walk up continuing past a `.git` that names no git directory, so a forbidden write stays denied with
        # a directory or non-UTF-8 index entry, or a junk `.git` in the child's own worktree or a directory under it,
        # with the index present or in another state directory.
        other = self.main / '.worktrees/review-2'
        subprocess.run(self.git + ['worktree', 'add', '-q', '--detach', str(other)], check=True)
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        forbidden = {'command': f'echo x > {quote(str(other / "a.py"))}'}
        index = self.tmp / 'state/zloop/codex-children/s1'
        (index / 'x').mkdir()
        (index / 'y').write_bytes(b'\xff\xfe')
        self.assert_denied(self.run_codex('Bash', forbidden), other / 'a.py', 'Codex child review_1')
        (self.worktree / 'sub').mkdir()
        (self.worktree / 'sub/.git').write_text('\n')
        dot = self.worktree / '.git'
        saved = dot.read_text()
        self.addCleanup(dot.write_text, saved)
        dot.write_text('junk\n')
        for state in ('state', 'elsewhere'):
            self.env['XDG_STATE_HOME'] = str(self.tmp / state)
            for cwd in (self.worktree, self.worktree / 'sub'):
                with self.subTest(state=state, cwd=cwd):
                    decision = self.run_codex('Bash', forbidden, cwd=cwd)
                    self.assert_denied(decision, other / 'a.py', 'Codex child review_1')


    def test_a_recorded_read_only_child_never_writes_a_git_path_in_its_worktree(self):
        # Rule (T15 review G6): the turn cwd's `.git` is one way the hook finds the records, and a `.git` is never a
        # reviewer's edit, so a read-only child writes no path through `.git` even in its recorded worktree.
        self.assertEqual(self.record('review_1', self.agents / 'reviewer.md', self.worktree).returncode, 0)
        for path in (self.worktree / '.git', self.worktree / 'sub/.git'):
            with self.subTest(path=path):
                self.assert_denied(self.run_codex('Bash', {'command': f'echo junk > {quote(str(path))}'}), path,
                                   'Codex child review_1')
        self.assertIsNone(self.run_codex('Bash', {'command': f'echo x > {quote(str(self.worktree / "a.py"))}'}))


if __name__ == '__main__':
    unittest.main()
