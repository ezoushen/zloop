#!/usr/bin/env python3
"""huh re-pitches what lost the user; the explain skill replaces the show-me addon, and the re-pitch it uses lives
with asking in runtime, where every question to the user applies it."""
import subprocess
import unittest

import support
from support import prose

ROOT = support.ROOT
HUH = ROOT / 'skills/huh/SKILL.md'
EXPLAIN = ROOT / 'skills/explain/SKILL.md'
ASK = ROOT / 'runtime/ask/ask.md'


class Huh(unittest.TestCase):
    def test_huh_is_a_short_manual_command_over_the_explain_skill(self):
        text = prose(HUH)
        self.assertIn('disable-model-invocation: true', text)
        first_read = next(line for line in HUH.read_text().splitlines() if line.startswith('First read:'))
        self.assertIn('](../explain/SKILL.md)', first_read)
        self.assertIn('](../../runtime/ask/ask.md#re-pitch-what-lost-the-reader)', text)
        # A re-pitch launches nothing, so it overrides the session hook's read-the-bootstrap hint, and a short
        # trigger keeps the answer short.
        self.assertIn('Skip the runtime bootstrap and the host session part', text)
        self.assertLess(len(text.encode()), 1500)

    def test_the_re_pitch_starts_from_what_the_reader_missed(self):
        text = prose(ASK)
        for phrase in ('missing context first', 'no longer than the message it replaces', 'ticket or decision ID'):
            self.assertIn(phrase, text)
        self.assertIn('only when the user asks', prose(EXPLAIN))

    def test_the_explain_doc_keeps_every_view_and_draws_text_where_diagrams_do_not_render(self):
        text = prose(EXPLAIN)
        for view in ('pseudocode', 'call tree', 'component tree', 'file tree', 'sequence', '`diff`', 'whole block',
                     'hide ownership or order', 'real labels and data', 'desktop and mobile', 'preview'):
            self.assertIn(view, text)
        self.assertIn('Draw with text pictures by default', text)
        self.assertIn("only where the reader's surface renders it", text)

    def test_the_explain_doc_owns_telling_in_layers(self):
        section = prose(EXPLAIN).split('## Tell it in layers', 1)[1].split(' ## ', 1)[0]
        for rule in ('headline in plain language', 'one representative flow', 'responsibility becomes relevant',
                     'only when they help the user steer', 'may not know to ask about', 'one topic at a time',
                     "follow the user's question rather than a questionnaire", 'match an explicitly requested depth or focus',
                     'leave room for questions', 'before any decision', 'an artifact link alone cannot establish it',
                     'what is settled, what remains uncertain', 'return to the remaining decision'):
            self.assertIn(rule, section)
        repitch = prose(ASK).split('## Re-pitch what lost the reader', 1)[1]
        self.assertIn('replaces telling in layers', repitch)
        self.assertIn('](../../runtime/ask/ask.md#re-pitch-what-lost-the-reader)', prose(EXPLAIN))

    def test_the_callers_tell_by_the_explain_doc_instead_of_restating_it(self):
        restated = {'skills/brief/SKILL.md': 'Lead with the goal',
                    'skills/review/walkthrough.md': 'Introduce technical names as their responsibilities',
                    'skills/setup/tour.md': 'Follow the user\'s question rather than a questionnaire'}
        for name, phrase in restated.items():
            text = prose(ROOT / name)
            with self.subTest(doc=name):
                self.assertIn('in layers, as [explain](../explain/SKILL.md) says', text)
                self.assertNotIn(phrase, text)
        # what each caller still covers itself
        kept = {'skills/brief/SKILL.md': ('What is complete, with its meaningful result', 'no invented percentages',
                                          'Match an explicitly requested depth or focus'),
                'skills/review/walkthrough.md': ('WHY, WHAT, then HOW', 'Explain material tradeoffs, alternatives and scope limits',
                                                 'documented author rationale'),
                'skills/setup/tour.md': ('Explain responsibilities before field names', 'Give the next useful topic its tradeoff')}
        for name, phrases in kept.items():
            for phrase in phrases:
                self.assertIn(phrase, prose(ROOT / name), name)

    def test_the_pipeline_map_names_huh(self):
        self.assertIn('`/zloop:huh`', (ROOT / 'skills/brief/pipeline.md').read_text())

    def test_invoked_directly_explain_reads_what_the_question_concerns_first(self):
        section = prose(EXPLAIN).split('## Invoked directly', 1)[1]
        for rule in ("the user's question sets the scope", 'read-only', 'mark a claim you have not checked as unchecked'):
            with self.subTest(rule=rule):
                self.assertIn(rule, section)
        self.assertIn('Skip the runtime bootstrap and the host session part', prose(EXPLAIN))
        self.assertFalse((ROOT / 'runtime/explain').exists())
        # Progress is a briefing, which reads live worker and check evidence that explaining skips.
        # explain skips the bootstrap that says how each host types a skill, so it names all three forms.
        self.assertIn('(`/zloop:brief` in Claude Code, `$zloop:brief` in Codex, `/skill:brief` in Pi)', section)
        readme_row = next(line for line in (ROOT / 'README.md').read_text().splitlines() if line.startswith('| `explain` |'))
        for where, text in (('frontmatter', EXPLAIN.read_text().split('---', 2)[1]), ('README row', readme_row),
                            ('openai.yaml', (ROOT / 'skills/explain/agents/openai.yaml').read_text())):
            with self.subTest(where=where):
                self.assertNotIn('current state', text)

    def test_no_show_me_dependency_remains(self):
        self.assertFalse((ROOT / 'addons').exists())
        scan = subprocess.run(['git', 'grep', '-l', '-i', '-e', 'show-me', '-e', 'addons/', '--', '.',
                               ':!tests/test_huh.py', ':!tests/test_pi_adapter.mjs'],
                              cwd=ROOT, capture_output=True, text=True)
        # git grep exits 1 for no match; any other code is a failed scan, not a clean one.
        self.assertEqual((scan.returncode, scan.stdout), (1, ''), scan.stderr)


if __name__ == '__main__':
    unittest.main()
