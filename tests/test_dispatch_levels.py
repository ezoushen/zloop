#!/usr/bin/env python3
"""A dispatch verifies at one effort level: accept.md states each level's checks, and dispatch plans, accepts and hands off by it."""
import re
import unittest

import support
import agent_defs

ROOT = support.ROOT
DISPATCH = ROOT / 'skills/dispatch'
ACCEPT = ROOT / 'runtime/checks/accept.md'
COLUMNS = ('Level', 'Beyond the required checks')
# The accepted dispatch table: one exact cell per level, so any change to what a level adds fails here.
CELLS = {
    'low': "each ticket's focused tests only",
    'medium': "+ each ticket's affected suites before it returns",
    'high': "+ the full gate after every ticket is integrated, merged or committed inline, and one verifier per "
            "ticket re-runs that ticket's acceptance on the integrated head",
    'xhigh': '+ E2E for every acceptance criterion with an observable path, even where CI covers it',
    'max': '+ each E2E check and flaky-prone suite run three times',
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


def per_merge_gate_level():
    """The level at which accept.md's table starts the full gate after every ticket."""
    _, rows = level_rows()
    found = [name.split()[0] for name, cells in rows.items()
             if 'the full gate after every ticket' in cells['Beyond the required checks']]
    assert len(found) == 1, found
    return found[0]


class DispatchLevels(unittest.TestCase):
    def test_each_level_states_the_checks_it_adds_beyond_the_required_ones(self):
        # Rule: the dispatch verification table. Five levels in order, medium by default, each stating only the
        # checks it adds on top of the project's required ones.
        header, rows = level_rows()
        self.assertEqual(header, COLUMNS)
        self.assertEqual([level.split()[0] for level in rows], list(CELLS))
        labels = {name.split()[0]: name for name in rows}
        self.assertEqual([level for level, label in labels.items() if 'default' in label], ['medium'])
        self.assertEqual([level for level, label in labels.items() if 'when the user asks' in label], ['max'])
        for level, expected in CELLS.items():
            with self.subTest(level=level):
                self.assertEqual(rows[labels[level]]['Beyond the required checks'], expected)

    def test_every_level_runs_the_required_checks_and_keeps_the_engineering_floor(self):
        # Rule: a level scales only checks beyond the ones the project requires (the full gate once on the
        # integrated head and required E2E); no level relaxes engineering judgment or hides what it skipped, and
        # every criterion maps to passing evidence the acceptance cites, as the engineering standard says.
        text = prose(ACCEPT)
        for phrase in ("level runs the project's required checks",
                       'scales only the checks beyond the ones the project requires',
                       "engineering standard's floor: every criterion maps to passing evidence the acceptance "
                       'cites (command, exit status, identity), and the dispatch report says what the level '
                       'left out'):
            self.assertIn(phrase, text)
        self.assertNotIn('accepting verifier', text)

    def test_the_tier_rules_name_the_levels_that_relax_them_and_only_those(self):
        # Rule: the full gate runs once on the integrated head at every level, and high and higher add the
        # per-ticket runs; low skips the affected suites; E2E adds only what CI and the gate miss, and xhigh and
        # higher add every criterion; max runs each E2E check and flaky-prone suite three times on request.
        text = prose(ACCEPT)
        for phrase in ('names the effort levels that relax it, and only those',
                       'run it after every ticket is integrated, merged or committed inline, too',
                       'E2E adds only what CI and the full gate do not cover',
                       '`xhigh` and higher add E2E for every criterion with an observable path',
                       'rather than adding another batch for confidence alone',
                       '`max` runs each E2E check and flaky-prone suite three times when the user asked for it'):
            self.assertIn(phrase, text)
        affected = re.search(r'- \*\*affected:\*\*(.*?)(?=\n- \*\*)', ACCEPT.read_text(), re.S).group(1)
        self.assertIn('`low` skips them', ' '.join(affected.split()))
        for other in ('`low` and higher', '`medium` and higher', '`low` or `medium`'):
            self.assertNotIn(other, text)

    def test_the_per_ticket_gate_starts_at_the_same_level_in_the_table_the_tier_and_the_iteration(self):
        # Rule: the level that adds the per-ticket full gate is one level; the table, the tier rule and the
        # iteration's launch step all name it.
        level = per_merge_gate_level()
        self.assertEqual(level, 'high')
        self.assertIn(f'`{level}` and higher run it after every ticket is integrated', prose(ACCEPT))
        self.assertIn(f'`{level}` and higher also name the full gate', prose(DISPATCH / 'iteration.md'))

    def test_the_xhigh_e2e_belongs_to_the_review_stage_and_needs_autofix(self):
        # Rule: the E2E that xhigh and max add runs in the review's autofix E2E stage, which widens to every
        # criterion with an observable path; with no --autofix no level runs E2E and the dispatch report says so.
        accept = prose(ACCEPT)
        self.assertIn("E2E, required or added by a level, runs in the review's autofix E2E stage, so with no "
                      '`--autofix` no level runs E2E and the dispatch report says so', accept)
        stage = prose(ROOT / 'skills/review/e2e.md')
        self.assertIn("When the dispatch's [verification level](../../runtime/checks/accept.md#verification-levels) "
                      'is `xhigh` or `max`, the applicable gates widen to every acceptance criterion with an '
                      'observable path, even where CI covers it, and `max` runs each E2E check and each '
                      'flaky-prone suite three times.', stage)
        handoff = prose(DISPATCH / 'handoff.md')
        self.assertIn("With `--autofix`, also pass the dispatch's verification level: it widens the E2E stage.",
                      handoff)

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
                       'verification runs at `medium` and the review at `high`',
                       'never at a level remembered from an earlier run', '`max` runs only when the user asks',
                       'Reject an unknown flag or level value',
                       '](../../runtime/checks/accept.md)'):
            self.assertIn(phrase, body)
        self.assertNotIn("today's dispatch", body)

    def test_an_invocation_keeps_its_levels_across_a_resume_and_a_new_one_never_inherits_them(self):
        # Rule: the levels an invocation's setup recorded hold for the same invocation after compaction or when
        # another session resumes it; a new invocation takes its flags, else the defaults.
        body = prose(DISPATCH / 'SKILL.md')
        self.assertIn('Record the invocation\'s effort levels in the dispatch report before the first launch: both '
                      'levels, and what the verification level left out, which with no `--autofix` includes every '
                      'E2E.', body)
        self.assertIn('Resuming this invocation, after compaction or in another session that takes over this '
                      'dispatch, keeps the levels its setup recorded; a new invocation never inherits them.', body)
        run = prose(ROOT / 'skills/autopilot/SKILL.md').split('## Run', 1)[1]
        self.assertIn("with `--autofix` and the card's current levels, `--dispatch-effort <level>` and "
                      '`--review-effort <level>`', run)

    def test_the_iteration_applies_the_level_when_it_launches_and_accepts(self):
        # Rule: the level decides which tiers a ticket's `checks:` line carries, the per-ticket full gate and the
        # acceptance re-run at accept, and the E2E and repetitions xhigh and max add in the review stage; the
        # required gate still runs once on the integrated head at every level.
        body = prose(DISPATCH / 'iteration.md')
        for phrase in ('](../../runtime/checks/accept.md)', 'verification level', '`checks:` line',
                       "`low` names each ticket's focused tests only", '`medium` adds the affected suites',
                       '`high` and higher also name the full gate after each ticket is integrated',
                       '`xhigh` and higher the E2E of every criterion with an observable path',
                       '`max` the three-run repeats',
                       'run the full gate after every ticket is integrated, merged or committed inline',
                       '`xhigh` and higher add E2E for every criterion with an observable path, and `max` runs '
                       'each E2E check and flaky-prone suite three times, both in the review\'s '
                       '[autofix E2E stage](../review/e2e.md)',
                       'required full gate once on the integrated head'):
            self.assertIn(phrase, body)

    def test_a_verifier_re_runs_each_tickets_acceptance_under_its_own_job_at_high(self):
        # Rule: at high and higher one verifier attempt per ticket re-runs that ticket's `Verify (code)` commands
        # on the integrated head, under a job whose report is neither the review report nor an `e2e_by` entry.
        body = prose(DISPATCH / 'iteration.md')
        self.assertIn('register one verifier attempt per ticket with the [acceptance-rerun](jobs/acceptance-rerun.md) '
                      "job, which re-runs that ticket's `Verify (code)` commands on the integrated head", body)
        job = agent_defs.load(ROOT)['jobs']['acceptance-rerun']
        self.assertEqual(job['agent'], 'verifier')
        self.assertEqual(job['returns'], ['## Acceptance rerun'])
        self.assertTrue({'worktree', 'head', 'report', 'ticket', 'checks'} <= set(job['inputs']), job['inputs'])
        text = prose(DISPATCH / 'jobs/acceptance-rerun.md')
        self.assertIn('Its `report` is a file of its own, never the review report: it holds no `e2e_status` or '
                      '`e2e_head`, and the review report does not list it in `e2e_by`.', text)
        packet = '\n'.join(f'{name}: x' for name in (*agent_defs.COMMON_INPUTS, *job['inputs']))
        self.assertEqual(agent_defs.missing_inputs(job, packet), [])
        self.assertEqual(agent_defs.missing_inputs(job, packet.replace('ticket: x', '')), ['ticket'])

    def test_the_handoff_passes_the_review_level_to_the_shared_review_procedure(self):
        # Rule: dispatch supplies the review level to the shared review procedure, which records it in
        # `review_level:`; the precedence is `--review-effort`, else `--effort`, else the review's default high,
        # and the verification flag never sets it.
        handoff = prose(DISPATCH / 'handoff.md')
        for phrase in ('](../review/SKILL.md)', 'review effort level',
                       "`--review-effort`, else `--effort`, else the review's default `high`",
                       'records it in `review_level:`'):
            self.assertIn(phrase, handoff)
        precedence = handoff.split('review effort level', 1)[1].split('records it', 1)[0]
        self.assertNotIn('--dispatch-effort', precedence)
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

    def test_the_glossary_full_gate_and_e2e_name_the_levels_that_widen_them(self):
        # Rule: the glossary's Full gate and E2E definitions hold at every level: high and above also run the gate
        # after each ticket is integrated, and xhigh and above add E2E where CI covers the criterion.
        text = (ROOT / 'GLOSSARY.md').read_text()
        entries = dict(re.findall(r'^\*\*([^*\n]+)\*\*:\n(.*?)(?=^\*\*|^#|\Z)', text, re.M | re.S))
        gate = ' '.join(entries['Full gate'].partition('_Avoid_:')[0].split())
        e2e = ' '.join(entries['E2E'].partition('_Avoid_:')[0].split())
        self.assertIn('at `high` and above it also runs after each ticket is integrated', gate)
        self.assertIn('at `xhigh` and above it also covers criteria that CI covers', e2e)


if __name__ == '__main__':
    unittest.main()
