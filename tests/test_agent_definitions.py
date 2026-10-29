#!/usr/bin/env python3
"""Agent and job definitions: each structural rule on a small fixture tree, then the real plugin."""
import json
from pathlib import Path
import tempfile
import unittest

import support
import agent_defs

AGENT = '''---
name: checker
description: Read-only checker for assigned jobs.
writes: none
core_skills: ["skills/craft/SKILL.md", "host-skills:diagnose"]
tools: Read, Grep, Bash, Edit
disallowedTools: Write
---

## Permissions

Never edits source; see [shared rules](../runtime/rules.md).

## Principles

- A verdict cites its evidence — shown by: every finding names file:line or a command.

## Core skills

Apply [craft](../skills/craft/SKILL.md) to every job.
'''

JOB = '''---
name: inspect
agent: checker
inputs: ["worktree", "head", "report"]
returns: ["## Coverage"]
independent_of: []
acceptance: false
---

# inspect

## Inputs

The pinned head.

## Finish line

Every file covered.

## Return

The coverage section.
'''


class Definitions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.write('agents/checker.md', AGENT)
        self.write('skills/craft/SKILL.md', '---\nname: craft\ndescription: Craft.\n---\n')
        self.write('skills/craft/jobs/inspect.md', JOB)
        self.write('runtime/rules.md', '# Rules\n')
        self.write('scripts/tool.py', "JOB = 'inspect'\n")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def edit(self, name, old, new):
        path = self.root / name
        self.assertIn(old, path.read_text())
        path.write_text(path.read_text().replace(old, new, 1))

    def problems(self):
        return agent_defs.problems(self.root)

    def assertProblem(self, fragment):
        problems = self.problems()
        self.assertTrue(any(fragment in problem for problem in problems), problems)

    def test_valid_fixture_loads_agents_and_jobs(self):
        self.assertEqual(self.problems(), [])
        loaded = agent_defs.load(self.root)
        self.assertEqual(loaded['agents']['checker']['writes'], 'none')
        self.assertEqual(loaded['agents']['checker']['core_skills'], ['skills/craft/SKILL.md', 'host-skills:diagnose'])
        self.assertEqual(loaded['jobs']['inspect'], {
            'name': 'inspect', 'agent': 'checker', 'inputs': ['worktree', 'head', 'report'],
            'returns': ['## Coverage'], 'independent_of': [], 'acceptance': False,
            'path': str(self.root / 'skills/craft/jobs/inspect.md')})

    def test_an_agent_holds_only_its_three_sections(self):
        self.edit('agents/checker.md', '## Core skills', '## Diff boundary\n\nOnly the changed range.\n\n## Core skills')
        self.assertProblem('sections must be Permissions, Principles, Core skills')

    def test_every_principle_names_its_evidence(self):
        self.edit('agents/checker.md', '## Core skills', '- Be rigorous.\n\n## Core skills')
        self.assertProblem('principle without "shown by:"')

    def test_identity_phrasing_is_rejected(self):
        self.edit('agents/checker.md', 'Never edits source', 'You are an expert reviewer. Never edits source')
        self.assertProblem('identity phrasing')

    def test_agent_links_only_to_core_skills_or_host_docs(self):
        self.write('skills/review/layers.md', '# Layers\n')
        self.edit('agents/checker.md', 'Apply [craft]', 'Follow [layers](../skills/review/layers.md). Apply [craft]')
        self.assertProblem('links outside core skills')

    def test_agent_may_link_a_standard(self):
        self.write('standards/craft.md', '# Craft\n')
        self.edit('agents/checker.md', 'Apply [craft]', 'Follow [craft standard](../standards/craft.md). Apply [craft]')
        self.assertEqual(self.problems(), [])

    def test_agent_size_is_capped(self):
        self.edit('agents/checker.md', '## Core skills', 'Filler. ' * 1000 + '\n\n## Core skills')
        self.assertProblem('bytes, over')

    def test_read_only_agent_cannot_be_given_write(self):
        self.edit('agents/checker.md', 'disallowedTools: Write\n', '')
        self.edit('agents/checker.md', 'tools: Read, Grep, Bash, Edit', 'tools: Read, Grep, Bash, Edit, Write')
        self.assertProblem('writes: none but Write is allowed')

    def test_writing_agent_needs_write(self):
        self.edit('agents/checker.md', 'writes: none', 'writes: worktree')
        self.assertProblem('writes: worktree but Write is not allowed')

    def test_core_skill_paths_must_exist(self):
        self.edit('agents/checker.md', 'skills/craft/SKILL.md"', 'skills/missing/SKILL.md"')
        self.assertProblem('core skill skills/missing/SKILL.md does not exist')

    def test_agent_frontmatter_fields_are_checked(self):
        cases = [('name: checker', 'name: other', 'name other does not match file checker'),
                 ('description: Read-only checker for assigned jobs.\n', '', 'description is required'),
                 ('writes: none', 'writes: everything', 'writes must be one of none, worktree'),
                 ('core_skills: ["skills/craft/SKILL.md", "host-skills:diagnose"]', 'core_skills: craft',
                  'core_skills must be a list')]
        for old, new, fragment in cases:
            with self.subTest(fragment=fragment):
                self.write('agents/checker.md', AGENT)
                self.edit('agents/checker.md', old, new)
                self.assertProblem(fragment)

    def test_principles_cannot_be_empty(self):
        self.edit('agents/checker.md', '- A verdict cites its evidence — shown by: every finding names file:line or a command.',
                  'Rules follow.')
        self.assertProblem('Principles needs at least one rule')

    def test_job_frontmatter_fields_are_checked(self):
        cases = [('acceptance: false', 'acceptance: maybe', 'acceptance must be true or false'),
                 ('inputs: ["worktree", "head", "report"]', 'inputs: worktree', 'inputs must be a list of strings')]
        for old, new, fragment in cases:
            with self.subTest(fragment=fragment):
                self.write('skills/craft/jobs/inspect.md', JOB)
                self.edit('skills/craft/jobs/inspect.md', old, new)
                self.assertProblem(fragment)

    def test_job_names_an_existing_agent(self):
        self.edit('skills/craft/jobs/inspect.md', 'agent: checker', 'agent: auditor')
        self.assertProblem('unknown agent auditor')

    def test_job_holds_inputs_finish_line_and_return(self):
        self.edit('skills/craft/jobs/inspect.md', '## Finish line', '## Steps')
        self.assertProblem('sections must be Inputs, Finish line, Return')

    def test_job_returns_are_report_headings(self):
        self.edit('skills/craft/jobs/inspect.md', '["## Coverage"]', '["Coverage"]')
        self.assertProblem('returns must be Markdown headings')

    def test_job_independence_names_existing_jobs(self):
        self.edit('skills/craft/jobs/inspect.md', 'independent_of: []', 'independent_of: ["review"]')
        self.assertProblem('independent_of names unknown job review')

    def test_job_names_are_unique_across_flows(self):
        self.write('skills/other/jobs/inspect.md', JOB)
        self.assertProblem('job inspect is defined twice')

    def test_a_job_under_a_flow_loads_and_shares_the_unique_name_space(self):
        self.write('skills/craft/jobs/survey.md', JOB.replace('name: inspect', 'name: survey'))
        self.assertEqual(self.problems(), [])
        self.assertEqual(agent_defs.load(self.root)['jobs']['survey']['path'],
                         str(self.root / 'skills/craft/jobs/survey.md'))
        self.write('skills/other/jobs/inspect.md', JOB)
        self.assertProblem('job inspect is defined twice')

    def test_job_name_matches_its_file(self):
        self.edit('skills/craft/jobs/inspect.md', 'name: inspect', 'name: survey')
        self.assertProblem('name survey does not match file inspect')

    def test_scripts_never_name_an_agent(self):
        self.write('scripts/tool.py', "if saved['agent'] == 'checker':\n    pass\n")
        self.assertProblem('scripts/tool.py names agent checker')
        self.write('scripts/tool.py', "if saved['agent'] in ('maker', 'checker'):\n    pass\n")
        self.assertProblem('scripts/tool.py names agent checker')
        self.write('scripts/tool.py', "trial = {'checker': 'pane-2'}\nfor key in ('maker', 'checker'):\n    pass\n")
        self.assertEqual(self.problems(), [])

    def test_an_assignment_names_the_common_and_job_inputs(self):
        job = agent_defs.load(self.root)['jobs']['inspect']
        complete = ('adapter: /a.md  agent_file: /agents/checker.md  job_file: /jobs/inspect.md\n'
                    'plugin_root: /p  repo: /r\nworktree: /w  head: abc123\nreport: /r.md\n')
        self.assertEqual(agent_defs.missing_inputs(job, complete), [])
        self.assertEqual(agent_defs.missing_inputs(job, complete.replace('head: abc123', 'head:')), ['head'])
        self.assertEqual(agent_defs.missing_inputs(job, complete.replace('job_file: /jobs/inspect.md', '')),
                         ['job_file'])
        # A value that merely mentions an input is not that input.
        self.assertEqual(agent_defs.missing_inputs(job, complete.replace('report: /r.md', 'see the report')),
                         ['report'])

    def test_the_reviewer_returns_its_worktree_as_registered_untracked_files_included(self):
        # The collect check holds a read-only agent to its registered state, so the contract says the same.
        text = ' '.join((support.ROOT / 'agents/reviewer.md').read_text().split())
        self.assertIn('restore the tracked files you changed and remove the temporary untracked files you made', text)
        self.assertIn('the worktree must match its state at registration, untracked files included', text)

    def test_every_agent_addresses_its_assigned_worktree_by_absolute_path(self):
        # E4: a native worker starts in its orchestrator's directory and its shell may reset between commands, so a
        # `cd` does not hold: each agent names the worktree in every command and confirms it once.
        for agent in ('builder', 'reviewer', 'verifier'):
            text = ' '.join((support.ROOT / f'agents/{agent}.md').read_text().split())
            self.assertIn('A shell may start each command in another directory, so use absolute paths and '
                          '`git -C <worktree>` in every command, and confirm the assigned worktree once with '
                          '`git -C <worktree> rev-parse --show-toplevel`.', text, agent)
            self.assertNotIn('Your first action is `cd <worktree>`', text, agent)

    def test_the_builder_writes_the_report_file_acceptance_reads(self):
        # E4 D5: `--accept` reads `.scratch/<slug>/reports/<NN>.md`, so the builder writes it, whatever a host says
        # about returning reports as a message.
        text = ' '.join((support.ROOT / 'agents/builder.md').read_text().split())
        self.assertIn('Write the job\'s report to the assigned `report` file (`.scratch/<slug>/reports/<NN>.md`), '
                      'which `--accept` reads; a host instruction to return reports only as a message does not '
                      'replace it: write the file, then return the status the job asks for.', text)

    def test_the_plugin_definitions_are_valid(self):
        self.assertEqual(agent_defs.problems(support.ROOT), [])
        loaded = agent_defs.load(support.ROOT)
        self.assertEqual(set(loaded['agents']), {'builder', 'reviewer', 'verifier'})
        for name, agent in loaded['agents'].items():
            for item in agent['core_skills']:
                self.assertTrue(':' in item or item.startswith(('standards/', 'runtime/')), (name, item))
        self.assertEqual(set(loaded['jobs']), {'ticket', 'merge-conflict', 'diff-review', 'repair', 'reproduce',
                                               'e2e-gate', 'audit', 'prototype'})
        # Configuration is validated against this enum, so an agent missing there cannot be configured.
        schema = json.loads((support.ROOT / 'schemas/executors.schema.json').read_text())
        self.assertEqual(schema['$defs']['agentName']['enum'], sorted(loaded['agents']))
        self.assertEqual(schema['$defs']['jobName']['enum'], sorted(loaded['jobs']))


if __name__ == '__main__':
    unittest.main()
