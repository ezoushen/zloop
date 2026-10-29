#!/usr/bin/env python3
"""A dispatch verifies at one effort level: accept.md states each level's checks, and dispatch plans, accepts and hands off by it."""
import re
import unittest

import support

ROOT = support.ROOT
DISPATCH = ROOT / 'skills/dispatch'
ACCEPT = ROOT / 'runtime/checks/accept.md'
COLUMNS = ('Level', 'Beyond the required checks')
# The accepted dispatch table: what each level adds beyond the project's required checks, in the alignment's own words.
RULES = {
    'low': ('focused',),
    'medium': ('affected suites', 'before it returns'),
    'high': ('full gate after every ticket merge', "verifier re-runs each ticket's acceptance on the integrated head"),
    'xhigh': ('every acceptance criterion with an observable path', 'even where CI covers it'),
    'max': ('repeated E2E', 'flaky-prone'),
}


def level_rows():
    """accept.md's table: header first, then one row per level, each cell flattened."""
    header, rows = None, {}
    for line in ACCEPT.read_text().splitlines():
        if not line.startswith('|'):
            continue
        cells = [' '.join(cell.split()) for cell in line.strip().strip('|').split('|')]
        if header is None:
            header = tuple(cells)
        elif not set(''.join(cells)) <= set('-: '):
            rows[cells[0]] = dict(zip(header, cells, strict=True))
    return header, rows


def prose(path):
    """A doc's text with runs of whitespace flattened."""
    return ' '.join(path.read_text().split())


class DispatchLevels(unittest.TestCase):
    def test_each_level_states_the_checks_it_adds_beyond_the_required_ones(self):
        # Rule: the dispatch verification table. Five levels in order, medium by default, each stating only the
        # checks it adds on top of the project's required ones, as accepted for ticket 02.
        header, rows = level_rows()
        self.assertEqual(header, COLUMNS)
        self.assertEqual([level.split()[0] for level in rows], list(RULES))
        labels = {name.split()[0]: name for name in rows}
        self.assertEqual([level for level, label in labels.items() if 'default' in label], ['medium'])
        self.assertEqual([level for level, label in labels.items() if 'when the user asks' in label], ['max'])
        for level, phrases in RULES.items():
            with self.subTest(level=level):
                cell = rows[labels[level]]['Beyond the required checks']
                for phrase in phrases:
                    self.assertIn(phrase, cell, f'{level} must say {phrase!r}')
    def test_every_level_runs_the_required_checks_and_keeps_the_engineering_floor(self):
        # Rule: a level scales only checks beyond the ones the project requires (the full gate once on the
        # integrated head and required E2E); no level relaxes engineering judgment or hides what it skipped.
        text = prose(ACCEPT)
        for phrase in ("level runs the project's required checks",
                       'scales only the checks beyond the ones the project requires',
                       "engineering standard's floor",
                       'what the level left out'):
            self.assertIn(phrase, text)

    def test_the_tier_rules_name_the_levels_that_relax_them_and_only_those(self):
        # Rule: the full gate runs once on the integrated head (not per ticket merge) at every level, and high
        # and higher add the per-merge runs; E2E adds only what CI and the gate miss, and xhigh and higher add
        # every criterion; repetitions answer a validity gap, and max adds them on request. No other level does.
        text = prose(ACCEPT)
        for phrase in ('names the effort levels that relax it, and only those',
                       '`high` and higher run it after every ticket merge',
                       'E2E adds only what CI and the full gate do not cover',
                       '`xhigh` and higher add E2E for every criterion with an observable path',
                       'rather than adding another batch for confidence alone',
                       '`max` repeats E2E and flaky-prone suites when the user asked for it'):
            self.assertIn(phrase, text)
        for other in ('`low` and higher', '`medium` and higher', '`low` or `medium`'):
            self.assertNotIn(other, text)
    def test_the_dispatch_command_takes_three_flags_and_defaults_to_medium_and_high(self):
        # Rule: `--effort` sets both steps, `--dispatch-effort` and `--review-effort` override it for theirs, no
        # level means verification at medium and review at high (never a level remembered from an earlier run),
        # and max runs only when the user asks.
        text = (DISPATCH / 'SKILL.md').read_text()
        hint = re.search(r'^argument-hint: (.+)$', text.split('---', 2)[1], re.M)
        assert hint, 'dispatch declares its argument hint'
        hint = hint.group(1)
        for flag in ('--effort', '--dispatch-effort', '--review-effort'):
            self.assertIn(flag, hint)
        body = ' '.join(text.split('---', 2)[2].split())
        for phrase in ('`--effort <level>` sets both', '`--dispatch-effort` and `--review-effort` override it',
                       'verification runs at `medium`', 'review at `high`',
                       'never at a level remembered from an earlier run', '`max` runs only when the user asks',
                       'Reject an unknown flag or level value',
                       '](../../runtime/checks/accept.md)'):
            self.assertIn(phrase, body)

    def test_the_dispatch_report_records_both_levels_at_setup(self):
        # Rule: the dispatch report records the verification and review levels at setup, with what the
        # verification level left out, so a resumed dispatch keeps them rather than defaulting again.
        body = prose(DISPATCH / 'SKILL.md')
        for phrase in ('both levels', 'dispatch report', 'what the verification level left out',
                       'resumed dispatch'):
            self.assertIn(phrase, body)
    def test_the_iteration_applies_the_level_when_it_launches_and_accepts(self):
        # Rule: the level decides which tiers a ticket's `checks:` line carries, the per-merge full gate and the
        # verifier acceptance re-run at accept, and the E2E and repetitions xhigh and max add; the required gate
        # still runs once on the integrated head at every level.
        body = prose(DISPATCH / 'iteration.md')
        for phrase in ('](../../runtime/checks/accept.md)', 'verification level', '`checks:` line',
                       "`low` names each ticket's focused tests only", '`medium` adds the affected suites',
                       'full gate after every ticket merge',
                       "re-runs each ticket's acceptance on the integrated head",
                       '`xhigh` and higher', 'every criterion with an observable path', '`max` repeats',
                       'required full gate once on the integrated head'):
            self.assertIn(phrase, body)
    def test_the_handoff_passes_the_review_level_to_the_shared_review_procedure(self):
        # Rule: dispatch supplies the review level to the shared review procedure, which records it in
        # `review_level:`; with no level the review runs at its own default high.
        handoff = prose(DISPATCH / 'handoff.md')
        for phrase in ('](../review/SKILL.md)', 'review effort level', '`--review-effort`', 'else `--effort`',
                       'default `high`', 'records it in `review_level:`'):
            self.assertIn(phrase, handoff)
        self.assertIn('dispatch supplies its review level', prose(ROOT / 'skills/review/SKILL.md'))
        self.assertIn('review_level:', prose(ROOT / 'skills/review/report.md'))
    def test_one_effort_level_definition_covers_dispatch_and_review(self):
        # Rule: GLOSSARY.md's one Effort level term covers dispatch's checks as well as a review's reviewers, and
        # the README's dispatch row names the three flags.
        text = (ROOT / 'GLOSSARY.md').read_text()
        entries = dict(re.findall(r'^\*\*([^*\n]+)\*\*:\n(.*?)(?=^\*\*|^#|\Z)', text, re.M | re.S))
        definition = ' '.join(entries['Effort level'].partition('_Avoid_:')[0].split())
        for meaning in ('thoroughness', 'review', 'dispatch', "project's required checks",
                        "model's reasoning effort"):
            self.assertIn(meaning, definition)
        row = next(line for line in (ROOT / 'README.md').read_text().splitlines() if line.startswith('| `dispatch`'))
        for flag in ('--effort', '--dispatch-effort', '--review-effort'):
            self.assertIn(flag, row)


if __name__ == '__main__':
    unittest.main()
