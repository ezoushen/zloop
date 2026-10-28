#!/usr/bin/env python3
"""Research reads primary sources and measures the real consumers; the user types it or the agent loads it."""
import unittest

import support

ROOT = support.ROOT
DOC = 'skills/research/SKILL.md'


def prose(path):
    return ' '.join((ROOT / path).read_text().split())


class Research(unittest.TestCase):
    def test_the_research_doc_states_each_rule(self):
        text = prose(DOC).lower()
        for rule in ('read primary sources',
                     'cite every claim to the source that owns it',
                     'save the findings where the project already keeps such notes',
                     'run it as a background worker',
                     'measure every real consumer, not one',
                     'settle every available fact before asking the user to choose',
                     'no separate fact-checker is mandatory'):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_align_review_and_the_pipeline_map_use_it(self):
        for path, link in (('skills/align/SKILL.md', '../research/SKILL.md'),
                           ('skills/review/SKILL.md', '../research/SKILL.md'),
                           ('skills/brief/pipeline.md', '../research/SKILL.md'),
                           ('skills/dispatch/iteration.md', '../research/SKILL.md')):
            with self.subTest(path=path):
                text = prose(path)
                self.assertIn(f']({link})', text)
                self.assertNotIn('use `research`', text.lower())

    def test_research_runs_as_a_host_helper_not_a_worker_job(self):
        text = prose(DOC)
        self.assertIn("a helper agent of the host, not a job of the builder, reviewer or verifier", text)
        self.assertIn('](../../runtime/resources/capacity.md)', text)
        self.assertIn('record it in the workstream\'s run report when one exists', text)
        self.assertNotIn('](../../runtime/workers/launch.md)', text)

    def test_invoked_directly_the_users_question_is_the_question(self):
        section = prose(DOC).split('## Invoked directly', 1)[1]
        for rule in ("the user's question is the question",
                     '`.scratch/<topic>/`',
                     'name the helper in the answer',
                     'answer in the conversation'):
            with self.subTest(rule=rule):
                self.assertIn(rule, section)
        self.assertIn('First read: the runtime bootstrap (`../../bootstrap.md`), the host session part.', prose(DOC))
        self.assertFalse((ROOT / 'runtime/research').exists())

    def test_the_pipeline_map_sends_a_defect_to_diagnosis(self):
        text = prose('skills/brief/pipeline.md')
        self.assertIn('](../diagnose/SKILL.md)', text)


if __name__ == '__main__':
    unittest.main()
