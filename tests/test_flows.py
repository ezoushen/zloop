#!/usr/bin/env python3
"""Each command's procedure is its SKILL.md, in a folder that also holds its other docs and jobs; the
procedures no command owns (interview, board) are runtime modules below their users. Direction, size and
shape are the doc checker's rules; this checks what lives where."""
import re
import unittest

import support

ROOT = support.ROOT
RUNTIME = ROOT / 'runtime'
SHARED = ('interview', 'board', 'ask')
SKILLS = ROOT / 'skills'
LINK = re.compile(r'\]\(([^)\s#]+)(?:#[^)\s]*)?\)')
COMMANDS = ('align', 'refine', 'dispatch', 'review', 'pr', 'autopilot', 'remediate', 'setup', 'brief')
# huh is a short trigger with no sections, a sibling of brief the pipeline map names without a link (tests/test_huh.py).
# a procedure each merged or moved command must still carry, by the doc that now holds it
HOLDS = {
    'skills/align/SKILL.md': ('## Settle the mandate', '## Establish the design', '## Save the current alignment'),
    'runtime/interview/authority.md': ('A decision the user reserved is always theirs.',),
    'runtime/interview/question-filter.md': ('**Material fork:** ask only if plausible answers change the goal',),
    'runtime/interview/record.md': ('`## Standing directives` section in the ledger',),
    'skills/refine/SKILL.md': ("`/zloop:refine` cuts work into tickets by the board's [slicing rules](../../runtime/board/slicing.md)", '## Explicit phase composition'),
    'skills/refine/ticket-template.md': ('# NN: <title, a behaviour not a component>',),
    'skills/dispatch/SKILL.md': ('# dispatch',),
    'skills/dispatch/iteration.md': ('## One iteration',),
    'skills/dispatch/handoff.md': ('## Review handoff',),
    'skills/housekeeping/SKILL.md': ('## Remove disposable residue',),
    'skills/pr/SKILL.md': ('## Repository approval path',),
    'skills/review/SKILL.md': ('# Shared review procedure',),
    'skills/review/walkthrough.md': ('## Continue into review when requested',),
    'skills/remediate/SKILL.md': ('## Arguments',),
    'skills/setup/SKILL.md': ('## Direct changes', 'No arguments means read-only inspection'),
    'skills/setup/tour.md': ('## Explain the actual setup', '## Discuss one useful topic at a time'),
    'skills/brief/SKILL.md': ('## Brief the human', '## Show the pipeline map'),
    'skills/brief/pipeline.md': ('## The outer loop', '## Failure modes and where the loop catches them'),
    'runtime/board/board.md': ('## Local Markdown board',),
}
JOBS = {'diff-review': ('reviewer', 'review'), 'repair': ('builder', 'review'), 'reproduce': ('reviewer', 'review'),
        'e2e-gate': ('verifier', 'review'), 'audit': ('reviewer', 'remediate'), 'ticket': ('builder', 'dispatch'),
        'merge-conflict': ('builder', 'dispatch'), 'acceptance-rerun': ('verifier', 'dispatch'),
        'prototype': ('builder', 'align')}


def targets(path):
    for target in LINK.findall(path.read_text()):
        if not re.match(r'[a-z]+:', target):
            yield (path.parent / target).resolve().relative_to(ROOT)


class Flows(unittest.TestCase):
    def test_every_command_holds_its_procedure_and_first_reads_no_wrapped_doc(self):
        for command in COMMANDS:
            with self.subTest(command=command):
                text = (SKILLS / command / 'SKILL.md').read_text()
                declaration = re.search(r'^First read:.*?(?:\n\n|\Z)', text, re.M | re.S).group(0)
                self.assertNotIn(f'{command}.md', declaration)
                self.assertIn('\n## ', text)
                self.assertFalse((RUNTIME / command).exists())

    def test_the_shared_procedures_are_runtime_modules_and_flows_is_gone(self):
        self.assertFalse((ROOT / 'flows').exists())
        for name in SHARED:
            self.assertTrue((RUNTIME / name).is_dir(), name)

    def test_review_reads_the_walkthrough_only_when_asked_for_one(self):
        text = (SKILLS / 'review/SKILL.md').read_text()
        declaration = re.search(r'^First read:.*?(?:\n\n|\Z)', text, re.M | re.S).group(0)
        self.assertNotIn('walkthrough', declaration)
        argument = ' '.join(text.split('\n## ', 1)[0].split())
        self.assertIn('A first argument `walkthrough` selects the [walkthrough procedure](walkthrough.md)', argument)

    def test_each_procedure_is_in_its_new_home(self):
        for name, markers in HOLDS.items():
            text = ' '.join((ROOT / name).read_text().split())
            for marker in markers:
                with self.subTest(doc=name, marker=marker):
                    self.assertIn(marker, text)

    def test_every_job_loads_from_its_owning_flow_with_its_agent(self):
        import agent_defs
        jobs = agent_defs.load(ROOT)['jobs']
        self.assertEqual(sorted(jobs), sorted(JOBS))
        for name, (agent, owner) in JOBS.items():
            with self.subTest(job=name):
                self.assertEqual(jobs[name]['agent'], agent)
                self.assertEqual(jobs[name]['path'], str(SKILLS / owner / 'jobs' / f'{name}.md'))
        self.assertEqual(agent_defs.problems(ROOT), [])

    def test_the_builder_packet_names_the_ticket_job_where_it_lives(self):
        self.assertIn('job_file: <plugin_root>/skills/dispatch/jobs/ticket.md', (SKILLS / 'dispatch/iteration.md').read_text())

    def test_the_board_does_not_link_the_review_flow_or_carry_the_split_rule(self):
        for path in (RUNTIME / 'board').rglob('*.md'):
            self.assertNotIn('too large for one worker', path.read_text(), path)
            for target in targets(path):
                self.assertNotEqual(target.parts[:2], ('skills', 'review'), path)

    def test_the_pipeline_map_links_every_command_and_every_shared_flow(self):
        linked = {target.parts[:2] for target in targets(SKILLS / 'brief/pipeline.md') if target.parts[0] in ('runtime', 'skills')}
        expected = {('runtime', name) for name in SHARED}
        expected |= {('skills', command) for command in COMMANDS if command != 'brief'}
        expected |= {('skills', skill) for skill in ('diagnose', 'research', 'prototype', 'explain', 'tdd')}
        self.assertLessEqual(expected, linked)

if __name__ == '__main__':
    unittest.main()
