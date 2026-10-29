#!/usr/bin/env python3
"""A review runs at one effort level: levels.md states each level's rules, and the procedure, jobs and report apply them."""
import json
import re
import unittest

import support

ROOT = support.ROOT
REVIEW = ROOT / 'skills/review'
LEVELS = REVIEW / 'levels.md'
# The accepted review table: per level, its reviewers, which findings it reports and its cap, its verification and
# its extra pass. The columns are the module's contract; the rules are ticket 01's, in the alignment's own words.
COLUMNS = ('Level', 'Reviewers', 'Findings reported', 'Verification', 'Extra pass')
RULES = {
    'low': {'Reviewers': ('one reviewer pass',),
            'Findings reported': ('surest', 'at most 4'),
            'Verification': ('none', 'reproduces nothing'),
            'Extra pass': ('none',)},
    'medium': {'Reviewers': ('one reviewer pass',),
               'Findings reported': ('every finding',),
               'Verification': ('none', 'unverified'),
               'Extra pass': ('none',)},
    'high': {'Reviewers': ('one exhaustive pass',),
             'Findings reported': ('every finding',),
             'Verification': ('disputed or hard', 'reproduce'),
             'Extra pass': ('none',)},
    'xhigh': {'Reviewers': ('one reviewer per angle',),
              'Findings reported': ('every finding',),
              'Verification': ('every finding reproduced', 'CONFIRMED', 'PLAUSIBLE', 'REFUTED'),
              'Extra pass': ('one sweep reviewer',)},
    'max': {'Reviewers': ('one reviewer per angle and module',),
            'Findings reported': ('every finding',),
            'Verification': ('as xhigh',),
            'Extra pass': ('as xhigh',)},
}


def level_rows():
    """Levels.md's table: header first, then one row per level, each cell flattened."""
    header, rows = None, {}
    for line in LEVELS.read_text().splitlines():
        if not line.startswith('|'):
            continue
        cells = [' '.join(cell.split()) for cell in line.strip().strip('|').split('|')]
        if header is None:
            header = tuple(cells)
        elif not set(''.join(cells)) <= set('-: '):
            rows[cells[0]] = dict(zip(header, cells, strict=True))
    return header, rows


def inputs(path):
    """A job's declared assignment inputs."""
    match = re.search(r'^inputs: (\S.*)$', path.read_text().split('---', 2)[1], re.M)
    return json.loads(match.group(1)) if match else []


def section(path, name):
    """A doc's `## name` section, flattened."""
    match = re.search(rf'^## {re.escape(name)}\n(.*?)(?=^## |\Z)', path.read_text(), re.M | re.S)
    return ' '.join(match.group(1).split()) if match else ''


class ReviewLevels(unittest.TestCase):
    def test_each_level_states_its_reviewers_findings_verification_and_extra_pass(self):
        # Rule: the review level table. Five levels in order, each stating the reviewers, the findings it reports
        # and its cap, its verification and the extra pass, as accepted for ticket 01.
        header, rows = level_rows()
        self.assertEqual(header, COLUMNS)
        self.assertEqual([level.split()[0] for level in rows], list(RULES))
        for level, rules in RULES.items():
            with self.subTest(level=level):
                cells = rows[next(name for name in rows if name.split()[0] == level)]
                for column, phrases in rules.items():
                    for phrase in phrases:
                        self.assertIn(phrase, cells[column], f'{level} {column} must say {phrase!r}')

    def test_the_default_is_high_and_max_runs_only_when_the_user_asks(self):
        # Rule: with no level the review runs at high, today's review; a level is never remembered, and max runs
        # only on the user's request.
        text = LEVELS.read_text()
        self.assertIn('a missing level is `high`', ' '.join(text.split()))
        _, rows = level_rows()
        labels = {name.split()[0]: name for name in rows}
        self.assertEqual([level for level, label in labels.items() if 'default' in label], ['high'])
        self.assertIn('only when the user asks', labels['max'])
        self.assertEqual([level for level, label in labels.items() if 'when the user asks' in label], ['max'])

    def test_every_level_keeps_the_engineering_floor(self):
        # Rule: no level relaxes engineering judgment (ADR 0011): an independent reviewer gives a verdict per
        # finding, and the level's omissions are recorded rather than waived.
        text = ' '.join(LEVELS.read_text().split())
        self.assertIn('](../../standards/engineering.md)', text)
        self.assertIn('verdict per finding', text)
        self.assertIn('what a level leaves out', text)
        self.assertIn('](report.md)', text)

    def test_the_per_angle_split_uses_a_fixed_list_the_review_already_has(self):
        # Rule: the angles are countable: the method's two axes and the layers' four cross-cutting lenses, in
        # that order, so "one reviewer per angle" names exactly six reviewers.
        angles = section(LEVELS, 'Angles')
        names = ('Standards', 'Spec', 'Security', 'Testability', 'Integrity', 'Maintainability')
        positions = [angles.find(name) for name in names]
        self.assertNotIn(-1, positions, f'the angles name {names}')
        self.assertEqual(positions, sorted(positions), 'the angles keep the accepted order')
        self.assertIn('one reviewer each', angles)
        self.assertIn('](method.md)', angles)
        self.assertIn('](layers.md)', angles)
        self.assertIn('Six angles', angles)

    def test_the_review_command_takes_an_effort_level_and_a_missing_one_is_high(self):
        # Rule: `/zloop:review --effort low|medium|high|xhigh|max`; no level runs at high, never a remembered one;
        # max runs only when the user asks.
        text = (REVIEW / 'SKILL.md').read_text()
        hint = re.search(r'^argument-hint: (.+)$', text.split('---', 2)[1], re.M).group(1)
        self.assertIn('--effort', hint)
        for level in ('low', 'medium', 'high', 'xhigh', 'max'):
            self.assertIn(level, hint)
        body = ' '.join(text.split('---', 2)[2].split())
        self.assertIn('[effort level](levels.md)', body)
        self.assertIn('with no level', body)
        self.assertIn('never at a level remembered from an earlier run', body)
        self.assertIn('`max` runs only when the user asks for it', body)
        # Rule: the level stays with this procedure; the maintained target resolver takes no --effort.
        self.assertIn('`--effort` selects the [level](levels.md) here and is not forwarded', body)

    def test_the_two_layer_rules_name_the_levels_that_relax_them_and_only_those(self):
        # Rule: low relaxes the top-N cutoff and xhigh and max add the extra passes; no other level does.
        text = ' '.join((REVIEW / 'layers.md').read_text().split())
        self.assertIn('](levels.md)', text)
        self.assertIn('and only those', text)
        self.assertIn('`low` reports its surest findings only, at most 4', text)
        self.assertIn('`xhigh` and `max` add the per-angle passes', text)

    def test_xhigh_and_max_assign_a_reviewer_per_angle_from_the_orchestrator(self):
        # Rule: the split is the orchestrator's assignment, and a reviewer still creates no nested team.
        owner = section(REVIEW / 'layers.md', 'One owner of implementation review')
        self.assertIn('`xhigh`', owner)
        self.assertIn('`max`', owner)
        self.assertIn('per [angle](levels.md)', owner)
        self.assertIn('per angle and module', owner)
        self.assertIn('a worker does not create a nested review team', owner)

    def test_the_diff_review_assignment_carries_the_level_and_its_angle(self):
        # Rule: a diff-review assignment names the level, the angle at xhigh and max, and the module at max; each
        # level's rules reach the reviewer.
        self.assertIn('effort level', inputs(REVIEW / 'jobs/diff-review.md'))
        body = ' '.join(section(REVIEW / 'jobs/diff-review.md', 'Finish line').split())
        for phrase in ('report only your surest findings', 'at most 4', 'marked UNVERIFIED', 'one angle',
                       'its module', 'a verdict per finding'):
            self.assertIn(phrase, body)
        self.assertIn('](../levels.md)', body)

    def test_the_reproduce_job_verifies_every_finding_only_when_the_assignment_says_so(self):
        # Rule: reproduce takes no effort level, because remediation also assigns it and has none; the assignment
        # says when it verifies every finding, and then the review keeps CONFIRMED and PLAUSIBLE and drops REFUTED.
        self.assertNotIn('effort level', inputs(REVIEW / 'jobs/reproduce.md'))
        inputs_body = ' '.join(section(REVIEW / 'jobs/reproduce.md', 'Inputs').split())
        self.assertIn('whether it verifies every finding', inputs_body)
        body = ' '.join(section(REVIEW / 'jobs/reproduce.md', 'Return').split())
        self.assertIn('](../levels.md)', body)
        self.assertIn('CONFIRMED, PLAUSIBLE or REFUTED', body)
        self.assertIn('drops REFUTED', body)

    def test_the_review_report_records_the_level_and_what_it_left_out(self):
        # Rule: the review report names the effort level it ran at and what that level left out.
        text = (REVIEW / 'report.md').read_text()
        front = text.split('---', 2)[1]
        self.assertIn('review_level:', front)
        body = ' '.join(text.split('---', 2)[2].split())
        self.assertIn('what that level left out', body)
        self.assertIn('](levels.md)', body)

    def test_coverage_plans_reviewers_verification_and_the_sweep_by_the_level(self):
        # Rule: the coverage stage reads the level when it assigns reviewers, reproduction and the sweep.
        body = ' '.join((REVIEW / 'coverage.md').read_text().split())
        self.assertIn('](levels.md)', body)
        for phrase in ('`low`, `medium` and `high` keep that one [diff-review](jobs/diff-review.md) worker',
                       'per [angle](levels.md)', 'one reproduce job per finding', 'one sweep reviewer'):
            self.assertIn(phrase, body)


if __name__ == '__main__':
    unittest.main()
