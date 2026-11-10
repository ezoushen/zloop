#!/usr/bin/env python3
"""A throwaway prototype answers one design question and records its verdict: the user types the skill or the agent
loads it, and align assigns it to a builder through its own job."""
import re
import unittest

import support
from support import prose
import agent_defs

ROOT = support.ROOT
SKILL = 'skills/prototype/SKILL.md'
JOB = 'skills/align/jobs/prototype.md'


class Prototype(unittest.TestCase):
    def test_the_job_names_its_agent_inputs_finish_line_and_return(self):
        job = agent_defs.load(ROOT)['jobs']['prototype']
        self.assertEqual(job['agent'], 'builder')
        self.assertEqual(job['inputs'], ['question', 'worktree', 'branch', 'report'])
        self.assertEqual(job['returns'], ['## Verdict'])
        text = prose(JOB)
        for heading in ('## Inputs', '## Finish line', '## Return'):
            self.assertIn(heading, text)
        self.assertIn('](../../prototype/SKILL.md)', text)

    def test_the_skill_states_each_rule(self):
        text = prose(SKILL).lower()
        for rule in ('throwaway code that answers one question',
                     'does the logic hold, or what should it look like',
                     'clearly marked as a prototype',
                     'trivial to run',
                     'no persistence',
                     'commit it only to the throwaway branch'):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_align_records_the_verdict_from_its_job(self):
        text = prose(JOB).lower()
        for rule in ('the verdict and the question it settled go to the ledger', 'when it qualifies, an adr'):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_invoked_directly_it_builds_in_its_own_worktree_and_answers_the_user(self):
        section = prose(SKILL).split('## Invoked directly', 1)[1]
        for rule in ('throwaway branch in its own worktree', 'the verdict in the conversation', 'offer',
                     "the worktree's path", '](../../runtime/interview/record.md)'):
            with self.subTest(rule=rule):
                self.assertIn(rule, section)
        self.assertIn('runtime bootstrap', prose(SKILL))

    def test_the_agent_loads_it_on_what_the_user_wants(self):
        # Loading it creates a branch, a worktree and a commit, so it fires on the user's request only.
        description = re.search(r'^description: (.+)$', (ROOT / SKILL).read_text(), re.M).group(1)
        self.assertIn('Use when the user wants', description)

    def test_neither_keeps_less_than_the_builders_core_gates(self):
        for path in (SKILL, JOB):
            with self.subTest(path=path):
                self.assertNotRegex(prose(path).lower(), r'test-first|mutation|failing test|waive|exempt|gates? (do|does) not')

    def test_both_are_language_neutral(self):
        spans = {SKILL: {'../../bootstrap.md', 'main'}, JOB: {'question', 'branch', 'worktree', 'report', '## Verdict'}}
        for path, allowed in spans.items():
            with self.subTest(path=path):
                body = (ROOT / path).read_text().split('---', 2)[2]
                self.assertNotIn('```', body)
                self.assertEqual(set(re.findall(r'`([^`]+)`', body)), allowed)
                text = prose(path).lower()
                for tool in ('pnpm', 'npm', 'yarn', 'bun ', 'node', 'python', 'cargo', 'go run', 'gradle', 'dotnet',
                             'swift run', 'html file', 'url search param'):
                    self.assertNotIn(tool, text)

    def test_align_uses_the_job(self):
        self.assertIn('](jobs/prototype.md)', prose('skills/align/SKILL.md'))


if __name__ == '__main__':
    unittest.main()
