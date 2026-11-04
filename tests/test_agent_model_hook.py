#!/usr/bin/env python3
"""The Agent PreToolUse hook requires an explicit model unless the subagent definition pins one."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import support

HOOK = support.HOOKS / 'agent_model.py'
TICKET = support.ROOT / 'skills/dispatch/jobs/ticket.md'


def assignment(job_file=TICKET, drop=()):
    """A launch prompt naming a job file and every input that job lists, less any dropped ones."""
    text = support.assignment_file(tempfile.mkdtemp(), Path(job_file).stem).read_text()
    lines = [line for line in text.splitlines() if line.split(':', 1)[0] not in drop]
    return '\n'.join(line if not line.startswith('job_file:') else f'job_file: {job_file}' for line in lines)


class AgentModelHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.cwd = self.home / 'project'
        (self.cwd / '.claude/agents').mkdir(parents=True)
        (self.home / '.claude/agents').mkdir(parents=True)

    def agent(self, directory, name, model=None):
        front = f'model: {model}\n' if model else ''
        (directory / f'{name}.md').write_text(f'---\nname: {name}\n{front}---\nBody\n')

    def run_hook(self, tool_input, tool='Agent', env=None):
        payload = {'hook_event_name': 'PreToolUse', 'tool_name': tool, 'tool_input': tool_input,
                   'cwd': str(self.cwd)}
        result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True,
                                text=True, env=dict(os.environ, HOME=str(self.home), **(env or {})), timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['hookSpecificOutput'] if result.stdout.strip() else None

    def test_explicit_model_is_allowed(self):
        self.assertIsNone(self.run_hook({'subagent_type': 'general-purpose', 'model': 'sonnet', 'prompt': 'x'}))

    def test_helper_without_model_is_denied_with_guidance(self):
        for subagent in ('general-purpose', 'Explore', 'Plan', None):
            with self.subTest(subagent=subagent):
                tool_input = {'prompt': 'x'} if subagent is None else {'subagent_type': subagent, 'prompt': 'x'}
                decision = self.run_hook(tool_input)
                self.assertEqual(decision['permissionDecision'], 'deny')
                for word in ('model', 'haiku', 'sonnet', 'opus'):
                    self.assertIn(word, decision['permissionDecisionReason'])

    def test_legacy_task_tool_name_is_checked_too(self):
        self.assertEqual(self.run_hook({'subagent_type': 'Explore'}, tool='Task')['permissionDecision'], 'deny')
        self.assertIsNone(self.run_hook({'command': 'ls'}, tool='Bash'))

    def test_fork_and_definitions_that_pin_a_model_are_allowed(self):
        self.agent(self.home / '.claude/agents', 'zloop-builder-0123456789ab', 'sonnet')
        self.agent(self.cwd / '.claude/agents', 'project-reviewer', 'opus')
        for subagent in ('fork', 'zloop-builder-0123456789ab', 'project-reviewer'):
            with self.subTest(subagent=subagent):
                self.assertIsNone(self.run_hook({'subagent_type': subagent, 'prompt': assignment()}))

    def test_inherit_or_unpinned_definition_is_denied(self):
        self.agent(self.home / '.claude/agents', 'inheriting', 'inherit')
        self.agent(self.home / '.claude/agents', 'unpinned')
        for subagent in ('inheriting', 'unpinned'):
            with self.subTest(subagent=subagent):
                self.assertEqual(self.run_hook({'subagent_type': subagent})['permissionDecision'], 'deny')

    def test_plugin_agent_definitions_are_resolved_from_the_plugin_cache(self):
        agents = self.home / '.claude/plugins/cache/market/toolkit/1.0.0/agents'
        agents.mkdir(parents=True)
        self.agent(agents, 'code-reviewer', 'sonnet')
        self.agent(agents, 'silent', None)
        self.assertIsNone(self.run_hook({'subagent_type': 'toolkit:code-reviewer'}))
        self.assertEqual(self.run_hook({'subagent_type': 'toolkit:silent'})['permissionDecision'], 'deny')

    def test_bundled_zloop_agent_without_model_is_denied(self):
        decision = self.run_hook({'subagent_type': 'zloop:builder', 'prompt': assignment()})
        self.assertEqual(decision['permissionDecision'], 'deny')

    def test_zloop_launch_needs_its_job_file_and_inputs(self):
        self.agent(self.home / '.claude/agents', 'zloop-builder-0123456789ab', 'sonnet')
        review = support.ROOT / 'skills/review/jobs/diff-review.md'
        for subagent, model in (('zloop:builder', 'sonnet'), ('zloop-builder-0123456789ab', None)):
            with self.subTest(subagent=subagent):
                launch = {'subagent_type': subagent, **({'model': model} if model else {})}
                for prompt, reason in (('Implement ticket 3.', 'No `job_file:` found'),
                                       ('job_file: skills/dispatch/jobs/ticket.md', 'is not an absolute path'),
                                       (assignment(drop=('worktree', 'brief')), 'Missing: brief, worktree'),
                                       (assignment(review), 'Job diff-review is not done by builder')):
                    decision = self.run_hook(dict(launch, prompt=prompt))
                    self.assertEqual(decision['permissionDecision'], 'deny', prompt)
                    self.assertIn(reason, decision['permissionDecisionReason'])
                self.assertIsNone(self.run_hook(dict(launch, prompt=assignment())))

    def test_the_denial_offers_only_the_job_location_that_resolves(self):
        decision = self.run_hook({'subagent_type': 'zloop:builder', 'model': 'sonnet',
                                  'prompt': 'job_file: skills/dispatch/jobs/ticket.md'})
        reason = decision['permissionDecisionReason']
        self.assertIn('skills/<skill>/jobs/<job>.md', reason)
        self.assertNotIn('flows/', reason)

    def test_a_job_under_a_skill_folder_of_another_plugin_root_is_resolved(self):
        self.agent(self.home / '.claude/agents', 'zloop-builder-0123456789ab', 'sonnet')
        job = self.home.joinpath('plugin', 'skills', 'review', 'jobs', 'ticket.md')
        job.parent.mkdir(parents=True)
        job.write_text(TICKET.read_text())
        launch = {'subagent_type': 'zloop-builder-0123456789ab'}
        self.assertIsNone(self.run_hook(dict(launch, prompt=assignment(job))))
        decision = self.run_hook(dict(launch, prompt=assignment(job, drop=('worktree',))))
        self.assertEqual(decision['permissionDecision'], 'deny')
        self.assertIn('Missing: worktree', decision['permissionDecisionReason'])

    def test_a_job_without_an_inputs_list_is_denied_not_skipped(self):
        job = self.home / 'plugin/skills/dispatch/jobs/ticket.md'
        job.parent.mkdir(parents=True)
        job.write_text('---\nname: ticket\nagent: builder\n---\n')
        decision = self.run_hook({'subagent_type': 'zloop:builder', 'model': 'sonnet',
                                  'prompt': f'job_file: {job}'})
        self.assertEqual(decision['permissionDecision'], 'deny')
        self.assertIn('declares no inputs list', decision['permissionDecisionReason'])

    def test_generated_profile_whose_tools_drifted_from_its_agent_is_denied(self):
        current = 'Read, Edit, Write, Bash, Grep, Glob, Skill, SendMessage, Monitor, TaskStop'
        for name, tools in (('zloop-builder-0123456789ab', 'Read, Edit, Write, Bash, Grep, Glob, Skill'),
                            ('zloop-builder-ba9876543210', current)):
            (self.home / '.claude/agents' / f'{name}.md').write_text(
                f'---\nname: {name}\ndescription: "x"\ntools: "{tools}"\nmodel: "sonnet"\n---\nBody\n')
        launch = {'subagent_type': 'zloop-builder-0123456789ab', 'prompt': assignment()}
        decision = self.run_hook(launch)
        self.assertEqual(decision['permissionDecision'], 'deny')
        for word in ('Monitor', 'SendMessage', 'TaskStop', 'agent_models.py'):
            self.assertIn(word, decision['permissionDecisionReason'])
        self.assertIsNone(self.run_hook(dict(launch, subagent_type='zloop-builder-ba9876543210')))

    def test_a_profile_listing_its_tools_as_a_block_list_is_compared_by_those_tools(self):
        # Rule: `tools:` followed by `- item` lines is the same tool set as the comma form, so a matching profile
        # launches and a drifted one is denied for the tools it lacks.
        current = 'Read, Edit, Write, Bash, Grep, Glob, Skill, SendMessage, Monitor, TaskStop'.split(', ')
        for name, tools in (('zloop-builder-0123456789ab', current[:-3]), ('zloop-builder-ba9876543210', current)):
            items = ''.join(f'  - {tool}\n' for tool in tools)
            (self.home / '.claude/agents' / f'{name}.md').write_text(
                f'---\nname: {name}\ndescription: "x"\ntools:\n{items}model: "sonnet"\n---\nBody\n')
        launch = {'subagent_type': 'zloop-builder-0123456789ab', 'prompt': assignment()}
        decision = self.run_hook(launch)
        self.assertEqual(decision['permissionDecision'], 'deny')
        self.assertIn('missing: Monitor, SendMessage, TaskStop; extra: none', decision['permissionDecisionReason'])
        self.assertIsNone(self.run_hook(dict(launch, subagent_type='zloop-builder-ba9876543210')))

    def test_opt_out_and_malformed_input_never_block(self):
        self.assertIsNone(self.run_hook({'subagent_type': 'Explore'}, env={'ZLOOP_AGENT_MODEL_HOOK': 'off'}))
        result = subprocess.run([sys.executable, str(HOOK)], input='not json', capture_output=True, text=True,
                                timeout=10)
        self.assertEqual((result.returncode, result.stdout), (0, ''))


if __name__ == '__main__':
    unittest.main()
