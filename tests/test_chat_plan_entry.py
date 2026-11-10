#!/usr/bin/env python3
"""A plan agreed in chat enters refine or dispatch directly; new tickets wait for the user's check."""
import unittest

import support
from support import prose

ROOT = support.ROOT
REFINE = ROOT / 'skills/refine'
DISPATCH = ROOT / 'skills/dispatch'
CHAT_PLAN = REFINE / 'chat-plan.md'


class ChatPlanEntry(unittest.TestCase):
    def test_a_chat_plan_becomes_a_minimal_alignment_without_running_align(self):
        text = prose(CHAT_PLAN)
        for rule in ('the plan agreed in the conversation', 'write `.scratch/<slug>/alignment.md`',
                     "the user's words quoted verbatim", 'the plan as agreed as WHAT and HOW', 'open questions',
                     'Do not run align or re-ask what the conversation settled',
                     'ask only a material gap that blocks executable tickets',
                     '`/zloop:align` stays available for a plan that needs it'):
            self.assertIn(rule, text)
        self.assertIn('[a plan agreed in chat](chat-plan.md)', prose(REFINE / 'SKILL.md'))
        self.assertIn('[a plan agreed in chat](../refine/chat-plan.md)', prose(DISPATCH / 'SKILL.md'))

    def test_new_tickets_always_wait_for_the_users_check(self):
        rule = "Newly cut tickets wait for the user's check before any implementation"
        self.assertIn(rule, prose(REFINE / 'SKILL.md'))
        self.assertIn('approval continues this dispatch', prose(CHAT_PLAN))
        self.assertIn("show the list and wait for the user's check", prose(CHAT_PLAN))
        dispatch = prose(DISPATCH / 'SKILL.md')
        self.assertIn('Tickets the user selected for dispatch are already checked', dispatch)
        self.assertIn('the iteration starts once the user has checked them', dispatch)
        self.assertIn("the new tickets wait for the user's check", prose(DISPATCH / 'iteration.md'))
        self.assertIn("wait for the user's check like any new cut", prose(DISPATCH / 'split-work.md'))

    def test_the_commands_and_map_advertise_the_chat_plan(self):
        for path in (REFINE / 'SKILL.md', DISPATCH / 'SKILL.md'):
            hint = next(line for line in path.read_text().splitlines() if line.startswith('argument-hint:'))
            self.assertIn('plan', hint)
        self.assertIn('or a plan agreed in chat', prose(ROOT / 'skills/brief/pipeline.md'))
        for command in ('refine', 'dispatch'):
            self.assertIn('a plan agreed in chat', prose(ROOT / 'skills' / command / 'agents/openai.yaml'))


if __name__ == '__main__':
    unittest.main()
