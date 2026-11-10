#!/usr/bin/env python3
"""Diagnosis builds a failing loop before any theory; the user types it or the agent loads it on a defect."""
import re
import unittest

import support
from support import prose

ROOT = support.ROOT
DOC = 'skills/diagnose/SKILL.md'


class Diagnose(unittest.TestCase):
    def test_the_diagnose_doc_states_each_rule(self):
        text = prose(DOC).lower()
        for rule in ('build one fast, repeatable command that fails on this bug, before any theory',
                     'shrink it to the smallest case that still fails',
                     'rank 3–5 falsifiable hypotheses before testing any',
                     'the failing case is kept as the regression test',
                     'redact every secret before showing a command, its output or a captured artifact',
                     "ask the user before touching their devices",
                     'diagnose only, never repair',
                     'one reproducer per finding'):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_the_diagnose_doc_names_no_language_specific_tooling(self):
        source = (ROOT / DOC).read_text()
        self.assertNotIn('```', source)
        # The only code spans are the redaction marker, the bootstrap path every command first reads and the tdd skill.
        self.assertEqual(set(re.findall(r'`([^`]+)`', source)), {'<REDACTED>', '../../bootstrap.md', '/zloop:tdd'})
        text = prose(DOC).lower()
        for tool in ('pytest', 'unittest', 'python', 'jest', 'node', 'npm', 'yarn', 'playwright', 'puppeteer', 'curl',
                     'cargo', 'go test', 'gradle', 'maven', 'dotnet', 'rspec', 'xcodebuild', 'swift test',
                     'git bisect', 'performance.now'):
            with self.subTest(tool=tool):
                self.assertNotIn(tool, text)

    def test_the_reproduce_job_and_the_blocked_ticket_step_use_it(self):
        for path, link in (('skills/review/jobs/reproduce.md', '../../diagnose/SKILL.md'),
                           ('skills/dispatch/iteration.md', '../diagnose/SKILL.md')):
            with self.subTest(path=path):
                self.assertIn(f']({link})', prose(path))
        self.assertIn('diagnose defects under [diagnosis](../diagnose/SKILL.md)',
                      prose('skills/dispatch/iteration.md'))

    def test_invoked_directly_the_fix_follows_the_diagnosis_test_first(self):
        # Diagnosis itself still never repairs; a user who asked for the fix gets it only after the cause is
        # confirmed, starting from the reproducer as a failing test.
        section = prose(DOC).split('## Invoked directly', 1)[1]
        for rule in ('only after the confirmed cause',
                     'the shrunk case becomes the failing test at the seam where the defect occurs',
                     '](../../standards/tdd.md)',
                     'with the user in place of the ticket, as `/zloop:tdd` works it',
                     'confirm the seam with the user before the test'):
            with self.subTest(rule=rule):
                self.assertIn(rule, section)
        self.assertIn('runtime bootstrap', prose(DOC))
        self.assertFalse((ROOT / 'runtime/diagnose').exists())

    def test_the_agent_loads_it_on_what_the_user_reports(self):
        # Its direct path offers a fix only the user asked for, so it fires on the user's report, not on the agent's
        # own failing check mid-task.
        description = re.search(r'^description: (.+)$', (ROOT / DOC).read_text(), re.M).group(1)
        self.assertIn('Use when the user reports', description)


if __name__ == '__main__':
    unittest.main()
