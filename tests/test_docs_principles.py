#!/usr/bin/env python3
"""The worker and autopilot docs hold principles and commands; a script's own refusal or reminder is not narrated."""
import re
import unittest

import support

ROOT = support.ROOT


def words(path):
    return ' '.join((ROOT / path).read_text().split())


class DocsHoldPrinciples(unittest.TestCase):
    def test_the_adr_index_calls_a_body_of_work_a_workstream_not_an_effort(self):
        # Rule: the glossary's Workstream avoids "effort" for a body of work (its bare word also names the model's
        # reasoning effort, so the checker skips it); only an "effort level" is the Effort level term.
        index = re.sub(r'\]\([^)]*\)', ']', (ROOT / 'docs/adr/README.md').read_text())
        self.assertEqual([], re.findall(r'(?i)\beffort\b(?! level)', index))

    def test_no_doc_narrates_what_its_script_refuses_or_prints(self):
        # Rule: a refusal or reminder is the script's text, carrying its own next step; the doc names the
        # principle and the command. Each phrase below narrates one such message or a removed mechanism.
        narrated = {
            'runtime/workers/prepare.md': ('heredoc or backticks', '`--keep "<why>"`',
                                           'registration refuses it when it lacks',
                                           'is rejected even if its pane changed',
                                           'also cannot take a job whose', 'A `timeout` over 60 needs `notify`'),
            'runtime/resources/retire.md': ('within a few seconds in all', 'keeps no record', 'a stop reminds you',
                                            'A pane gone with its tab alive'),
            'runtime/workers/panel.md': ('(+N more)', 'by precedence', 'since the scheduler then starts nothing',
                                         'a problem names one of its tickets'),
            'skills/autopilot/run-card.md': ('an unknown flag or level value is refused',
                                             'refuses a card whose State lacks a line below',
                                             'pass `next_tickets.py --check`'),
            'skills/remediate/SKILL.md': ('registration refuses one outside the plan',),
            'runtime/workers/collect.md': ('each is `open`',),
        }
        for path, phrases in narrated.items():
            text = words(path)
            for phrase in phrases:
                with self.subTest(doc=path, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_no_doc_describes_a_removed_mechanism(self):
        # Rule: the ownerless fallback, the launch index, the closed-pane reminder, the retirement intent, the panel's
        # text mode and ended phase and the lent ticket are gone from the code, so from every doc.
        removed = re.compile(r'ownerless|launch index|closed-pane|retirement interrupted|retirement intent|'
                             r'text mode|ended phase|\blent\b|an attempt recorded closed whose', re.I)
        for path in list((ROOT / 'runtime').rglob('*.md')) + list((ROOT / 'skills').rglob('*.md')) \
                + list((ROOT / 'hosts').rglob('*.md')):
            with self.subTest(doc=str(path.relative_to(ROOT))):
                self.assertIsNone(removed.search(' '.join(path.read_text().split())))

    def test_the_docs_keep_the_rules_no_script_enforces(self):
        # Rule: what no script refuses stays, so an agent still learns it: the worker marker, the record-only
        # notify, the repair plan command, retire's evidence, the run card's two commands and its State lines.
        kept = {
            'runtime/workers/prepare.md': ('ZLOOP_WORKER=<slug>/<assignment>', '["echo", "{receipt}"]',
                                           'herdr_launch.py dismiss'),
            'runtime/workers/collect.md': ('disposition',),
            'runtime/resources/retire.md': ('`record closed`', 'reap'),
            'skills/remediate/SKILL.md': ('worker_lifecycle.py" plan --state <reports>/workers.sqlite --job repair',),
            'skills/autopilot/run-card.md': ('autopilot_card.py" confirm', 'autopilot_card.py phase',
                                             'dispatch-effort:', 'review-effort:'),
            'runtime/workers/panel.md': ('scripts/panel_state.py', 'Hosts render', 'never re-derive a fact'),
        }
        for path, phrases in kept.items():
            text = words(path)
            for phrase in phrases:
                with self.subTest(doc=path, phrase=phrase):
                    self.assertIn(phrase, text)


if __name__ == '__main__':
    unittest.main()
