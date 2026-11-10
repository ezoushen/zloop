#!/usr/bin/env python3
"""Every question to the user carries the context needed to answer it, as /huh's re-pitch does."""
import unittest

import support
from support import prose


class QuestionContext(unittest.TestCase):
    def test_the_runtime_ask_doc_owns_asking_with_context_and_the_re_pitch(self):
        section = prose('runtime/ask/ask.md')
        for rule in ('Every question to the user, and every decision listed for them',
                     'start at the first fact they may not know',
                     'state an earlier decision by its content',
                     'define an internal name where it first appears',
                     'never a ticket or decision ID in place of what it means',
                     'answer without asking what the question refers to'):
            self.assertIn(rule, section)
        self.assertIn('[ask with context](../../runtime/ask/ask.md)', prose('skills/explain/SKILL.md'))
        self.assertIn('](#re-pitch-what-lost-the-reader)', section)

    def test_every_place_that_asks_the_user_applies_it(self):
        for path, link in (('runtime/interview/question-filter.md', '../ask/ask.md'),
                           ('runtime/interview/record.md', '../ask/ask.md'),
                           ('runtime/workers/design-hold.md', '../ask/ask.md'),
                           ('skills/dispatch/iteration.md', '../../runtime/ask/ask.md'),
                           ('skills/dispatch/handoff.md', '../../runtime/ask/ask.md'),
                           ('skills/review/coverage.md', '../../runtime/ask/ask.md')):
            with self.subTest(path=path):
                self.assertIn(f']({link})', prose(path))
        iteration = prose('skills/dispatch/iteration.md')
        self.assertIn('send the user one request covering all of them, [with context](../../runtime/ask/ask.md)', iteration)


if __name__ == '__main__':
    unittest.main()
