#!/usr/bin/env python3
"""Domain terms settle into the repository's GLOSSARY.md, and every phase uses them."""
import re
import subprocess
import unittest

import support

ROOT = support.ROOT
GLOSSARY = ROOT / 'runtime/domain/glossary.md'


def prose(path):
    return ' '.join((ROOT / path).read_text().split())


class ZloopGlossary(unittest.TestCase):
    def test_each_domain_concept_has_one_definition_and_avoid_list(self):
        text = (ROOT / 'GLOSSARY.md').read_text()
        entries = re.findall(r'^\*\*([^*\n]+)\*\*:\n(.*?)(?=^\*\*|^#|\Z)', text, re.M | re.S)
        terms = [term for term, _ in entries]
        required = {
            'Workstream', 'Ticket', 'Assignment', 'Attempt', 'Job', 'Agent',
            'Builder', 'Reviewer', 'Verifier', 'Worker', 'Orchestrator', 'Host',
            'Runtime', 'Executor', 'Route', 'Candidate', 'Pool', 'Profile',
            'Router', 'Thinking', 'Effort', 'Mandate', 'Ledger', 'Alignment',
            'Run card', 'Run', 'Phase', 'Checkpoint', 'Decision family', 'Report',
            'Evidence', 'Check tier', 'Full gate', 'E2E', 'Board', 'Tracker',
            'Slug', 'Panel', 'Allowance', 'Kind', 'Standard', 'ADR',
            'Goal', 'Principle', 'Constraint', 'Decision', 'Hinge', 'Veto',
            'Interview', 'Skill', 'Command', 'Session', 'Hook', 'Model',
            'Runtime binding', 'Executor type', 'Provider', 'Backend', 'Capacity',
            'Configuration scope', 'Calibration', 'Journal', 'Case', 'Review',
            'Finding', 'Repair', 'Review target', 'Base', 'Head', 'Worktree',
            'Branch', 'PR', 'Project', 'Milestone', 'Issue', 'Focus', 'Depth',
        }
        self.assertFalse(required - set(terms), f'missing terms: {sorted(required - set(terms))}')
        self.assertEqual(len(terms), len(set(terms)), 'each concept has one canonical entry')
        for term, body in entries:
            with self.subTest(term=term):
                definition, marker, avoided = body.partition('_Avoid_:')
                self.assertTrue(definition.strip(), 'the term needs a definition')
                self.assertEqual(marker, '_Avoid_:', 'the term needs an Avoid list')
                self.assertTrue(avoided.strip(), 'use none when there are no avoided words')


class Glossary(unittest.TestCase):
    def test_the_glossary_doc_states_the_format_and_upkeep(self):
        text = prose(GLOSSARY)
        for rule in ('`GLOSSARY.md` at the repository root',
                     '`GLOSSARY-MAP.md` at the root lists the contexts, where each glossary lives and how the contexts relate',
                     '`_Avoid_:`', 'one or two sentences saying what the term is, not what it does',
                     'Only terms specific to this domain', 'no implementation detail',
                     'Create it lazily, when the first term settles',
                     'update it the moment a term settles, never in a batch',
                     "A project's existing glossary convention wins"):
            self.assertIn(rule, text)

    def test_each_phase_writes_or_reads_the_glossary_for_its_part(self):
        link = '](../domain/glossary.md)'
        self.assertIn('add the term to the [glossary' + link + ' the moment it settles',
                      prose('runtime/interview/grilling.md'))
        self.assertIn('[glossary' + link, prose('runtime/interview/authority.md'))
        self.assertIn('[glossary' + link, prose('runtime/interview/record.md'))
        self.assertIn("Use the glossary's terms and avoid its `_Avoid_` words", prose('skills/explain/SKILL.md'))
        ask = prose('runtime/ask/ask.md')
        self.assertIn("the glossary's term in place of an `_Avoid_` word", ask[ask.index('## Re-pitch'):])
        self.assertIn('domain', prose('README.md')[prose('README.md').index('interview, board'):][:200])
        jobs = '](../../../runtime/domain/glossary.md)'
        for job in ('skills/dispatch/jobs/ticket.md', 'skills/review/jobs/repair.md'):
            with self.subTest(job=job):
                text = prose(job)
                self.assertIn(jobs, text)
                self.assertIn('in the same commit that introduces or changes it', text)
        self.assertIn('a term used against its glossary definition, or one of its `_Avoid_` words',
                      prose('skills/review/method.md'))
        for skill in ('skills/align/SKILL.md', 'skills/refine/SKILL.md'):
            self.assertIn('](../../runtime/domain/glossary.md)', prose(skill))

    def test_refine_tells_the_reader_to_take_ticket_words_from_the_glossary(self):
        self.assertIn("Use the project's [glossary](../../runtime/domain/glossary.md) for the words.",
                      prose('skills/refine/SKILL.md'))

    def test_no_doc_names_the_old_glossary_file(self):
        found = subprocess.run(
            ['git', 'grep', '-n', '-I', '-E', r'CONTEXT\.md',
             '--', '.', ':!journals', ':!tests/test_glossary.py'],
            cwd=ROOT, capture_output=True, text=True)
        self.assertIn(found.returncode, (0, 1), found.stderr)
        self.assertEqual('', found.stdout)


if __name__ == '__main__':
    unittest.main()
