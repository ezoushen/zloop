#!/usr/bin/env python3
"""A review runs at one effort level: levels.md states each level's rules, and the procedure, jobs and report apply them."""
import json
import re
import unittest

import support
import agent_defs

ROOT = support.ROOT
REVIEW = ROOT / 'skills/review'
LEVELS = REVIEW / 'levels.md'
# The accepted review table: one exact cell per level and column, so any change to a level's rule fails here.
COLUMNS = ('Level', 'Reviewers', 'Findings reported', 'Verification', 'Extra pass')
CELLS = {
    'low': ('one reviewer pass, both axes',
            'only findings the reviewer proved itself, at most 4, surest first, and how many candidates it left out',
            "the reviewer's own executed reproduction or traced failure path; assigns no reproduce job",
            'none'),
    'medium': ('one reviewer pass, both axes',
               "every finding, each with the reviewer's own verdict: CONFIRMED with its evidence, else UNVERIFIED",
               "the reviewer's own; assigns no reproduce job",
               'none'),
    'high': ('one exhaustive pass, both axes',
             'every finding',
             'disputed or hard findings go to a reproduce job',
             'none'),
    'xhigh': ('one reviewer per angle',
              'every finding',
              'every finding reproduced, one finding per job: CONFIRMED and UNVERIFIED kept, FALSE-POSITIVE '
              'dropped with its verdict recorded',
              'one sweep reviewer given the verified list; its new findings are reproduced too'),
    'max': ('one reviewer per angle and module',
            'every finding',
            'as xhigh',
            'as xhigh'),
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


def prose(path):
    """A doc's text with runs of whitespace flattened."""
    return ' '.join(path.read_text().split())


def section(path, name):
    """A doc's `## name` section, flattened."""
    match = re.search(rf'^## {re.escape(name)}\n(.*?)(?=^## |\Z)', path.read_text(), re.M | re.S)
    return ' '.join(match.group(1).split()) if match else ''


class ReviewLevels(unittest.TestCase):
    def test_each_level_states_its_reviewers_findings_verification_and_extra_pass(self):
        # Rule: the review level table. Five levels in order, each cell exactly as accepted, so a changed cap,
        # verification or extra pass fails here.
        header, rows = level_rows()
        self.assertEqual(header, COLUMNS)
        self.assertEqual([level.split()[0] for level in rows], list(CELLS))
        for level, expected in CELLS.items():
            with self.subTest(level=level):
                cells = rows[next(name for name in rows if name.split()[0] == level)]
                self.assertEqual(tuple(cells[column] for column in COLUMNS[1:]), expected)

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

    def test_one_verdict_vocabulary_serves_every_level(self):
        # Rule: a finding's verdict is CONFIRMED, FALSE-POSITIVE or UNVERIFIED at every level; no level adds a
        # second vocabulary, so the report template and the reviewer agent stay as they are.
        for folder in ('skills', 'agents', 'runtime', 'standards'):
            for path in (ROOT / folder).rglob('*.md'):
                text = path.read_text()
                self.assertNotIn('PLAUSIBLE', text, path)
                self.assertNotIn('REFUTED', text, path)
        body = section(REVIEW / 'jobs/reproduce.md', 'Return')
        self.assertIn('CONFIRMED with the repro or trace, FALSE-POSITIVE with the disproof, or UNVERIFIED', body)

    def test_low_and_medium_leave_nothing_unrepairable_and_clean_means_the_reported_findings_are_closed(self):
        # Rule: only CONFIRMED findings are repaired, so a level that reports a finding gives it the reviewer's
        # own verdict; at low a clean status closes the reported findings and the report names what was left out.
        levels = prose(LEVELS)
        self.assertIn('`review_status: clean` at `low` means its reported findings are closed, and the report '
                      'names what the level left out', levels)
        job = ' '.join(section(REVIEW / 'jobs/diff-review.md', 'Finish line').split())
        self.assertIn('At `low`, report only findings you proved yourself, with an executed reproduction or a '
                      'traced failure path, at most 4, surest first, and state how many candidates you left out.',
                      job)
        self.assertIn('At `medium`, report every finding with your own verdict, CONFIRMED with its evidence '
                      'else UNVERIFIED.', job)
        self.assertIn('At `low` and `medium`, request no reproduce job, not even for a disputed flag.', job)
        self.assertNotIn('marked UNVERIFIED', job)

    def test_every_level_keeps_the_engineering_floor(self):
        # Rule: no level relaxes engineering judgment: an independent reviewer gives a verdict per finding, and the
        # level's omissions are recorded rather than waived.
        text = prose(LEVELS)
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

    def test_an_angle_reviewer_applies_the_method_and_owns_the_gates_the_angles_define(self):
        # Rule: Standards and Spec are the method's two axes; a lens angle applies both axes through its lens; the
        # documentation gate and the test and comment checks belong to Standards, which the sweep re-checks.
        angles = section(LEVELS, 'Angles')
        for sentence in ('A lens angle (Security, Testability, Integrity, Maintainability) applies both axes '
                         'through that lens.',
                         'The documentation gate and the tautological-test and comment checks belong to the '
                         'Standards angle, and the sweep re-checks them with the verified list.',
                         'The sweep looks for what the angles missed.'):
            self.assertIn(sentence, angles)

    def test_a_module_is_defined_once_as_an_area_the_changed_files_fall_in(self):
        # Rule: max's reviewer count is knowable: a module is one of the project's areas the changed files fall in,
        # by the project's area or `touches` conventions, else top-level directories.
        angles = section(LEVELS, 'Angles')
        self.assertIn("A module is one of the project's areas the changed files fall in: its area or `touches` "
                      'convention, else top-level directories.', angles)
        for path in REVIEW.rglob('*.md'):
            if path != LEVELS:
                self.assertNotIn('A module is', path.read_text(), path)

    def test_the_review_command_takes_an_effort_level_and_a_missing_one_is_high(self):
        # Rule: `/zloop:review --effort low|medium|high|xhigh|max`; no level runs at high, never a remembered one;
        # max runs only when the user asks; an unknown level value is refused.
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
        self.assertIn('Reject conflicting or unknown flags and an unknown level value', body)
        # Rule: the level stays with this procedure; the maintained target resolver takes no --effort.
        self.assertIn('`--effort` selects the [level](levels.md) here and is not forwarded', body)

    def test_a_walkthrough_carries_its_effort_into_the_review_it_continues_into(self):
        # Rule: `review walkthrough <target> --effort L` continues into a review at L, as it carries --autofix.
        body = prose(REVIEW / 'walkthrough.md').split('## Establish the change')[0]
        self.assertIn('`--autofix`, `--comments` or `--effort` given with it are authorization or settings for '
                      'the review it continues into', body)

    def test_the_layer_rules_name_the_three_levels_that_relax_them_and_only_those(self):
        # Rule: low relaxes the top-N cutoff and xhigh and max add the extra passes; no other level does; at
        # xhigh and max each lens is an angle with its own reviewer.
        text = prose(REVIEW / 'layers.md')
        self.assertIn('](levels.md)', text)
        self.assertIn('Three [levels](levels.md) relax these, and only those', text)
        self.assertIn('`low` reports only findings it proved itself, at most 4', text)
        self.assertIn('`xhigh` and `max` add the per-angle passes', text)
        self.assertNotIn('Two [levels]', text)
        self.assertIn('are cross-cutting lenses on each relevant layer, not additional whole-PR passes, except '
                      'that at `xhigh` and `max` each lens is an angle with its own reviewer', text)

    def test_xhigh_and_max_assign_a_reviewer_per_angle_from_the_orchestrator(self):
        # Rule: the split is the orchestrator's assignment, and a reviewer still creates no nested team.
        owner = section(REVIEW / 'layers.md', 'One owner of implementation review')
        self.assertIn('`xhigh`', owner)
        self.assertIn('`max`', owner)
        self.assertIn('per [angle](levels.md)', owner)
        self.assertIn('per angle and module', owner)
        self.assertIn('a worker does not create a nested review team', owner)

    def test_the_diff_review_job_declares_the_level_and_the_angle_it_is_assigned(self):
        # Rule: a diff-review packet without its effort level or angle is refused at registration; the angle is
        # `all` at low, medium and high, one of the six angles or `sweep` at xhigh, and names its module at max.
        job = agent_defs.load(ROOT)['jobs']['diff-review']
        self.assertIn('effort level', job['inputs'])
        self.assertIn('angle', job['inputs'])
        self.assertNotIn('module', job['inputs'])
        full = {name: 'x' for name in (*agent_defs.COMMON_INPUTS, *job['inputs'])}
        packet = '\n'.join(f'{name}: {value}' for name, value in full.items())
        self.assertEqual(agent_defs.missing_inputs(job, packet), [])
        for name in ('effort level', 'angle'):
            without = '\n'.join(f'{key}: {value}' for key, value in full.items() if key != name)
            self.assertEqual(agent_defs.missing_inputs(job, without), [name])
        inputs_body = ' '.join(section(REVIEW / 'jobs/diff-review.md', 'Inputs').split())
        self.assertIn('The `angle` is `all` at `low`, `medium` and `high`; at `xhigh` one of the six '
                      '[angles](../levels.md) or `sweep`; at `max` an angle in its module', inputs_body)
        self.assertIn('a `sweep` assignment also carries the verified findings', inputs_body)

    def test_the_diff_review_finish_line_applies_each_level_and_the_angle(self):
        # Rule: each level's rules reach the reviewer; an angle reviewer applies the method through its angle and
        # the sweep looks for what the angles missed, with its new findings reproduced too.
        body = ' '.join(section(REVIEW / 'jobs/diff-review.md', 'Finish line').split())
        for phrase in ('As an angle reviewer, apply the [method](../method.md) through your angle',
                       'As the `sweep`, look for what the angles missed, given the verified findings, '
                       'and re-check the documentation gate and the test and comment checks with them',
                       'Every level returns a verdict per finding.'):
            self.assertIn(phrase, body)
        self.assertIn('](../levels.md)', body)

    def test_the_reproduce_job_holds_one_finding_when_the_review_verifies_every_finding(self):
        # Rule: reproduce takes no effort level, because remediation also assigns it and has none; at xhigh and
        # max the review assigns it every finding, one per job, and its verdicts are the usual three.
        self.assertNotIn('effort level', inputs(REVIEW / 'jobs/reproduce.md'))
        inputs_body = ' '.join(section(REVIEW / 'jobs/reproduce.md', 'Inputs').split())
        self.assertIn('one finding, or a batch that shares one kind and proof procedure; a review at `xhigh` or '
                      '`max` ([levels](../levels.md)) assigns every finding, one per job', inputs_body)

    def test_the_review_report_records_the_level_and_what_it_left_out(self):
        # Rule: the review report names the effort level it ran at and what that level left out.
        text = (REVIEW / 'report.md').read_text()
        front = text.split('---', 2)[1]
        self.assertIn('review_level:', front)
        body = ' '.join(text.split('---', 2)[2].split())
        self.assertIn('what that level left out', body)
        self.assertIn('](levels.md)', body)

    def test_coverage_plans_reviewers_verification_and_the_sweep_by_the_level(self):
        # Rule: the coverage stage reads the level when it assigns reviewers, reproduction and the sweep, and
        # names the level and angle among the diff-review packet's contents.
        body = prose(REVIEW / 'coverage.md')
        self.assertIn('](levels.md)', body)
        for phrase in ('`low`, `medium` and `high` keep that one [diff-review](jobs/diff-review.md) worker',
                       '`low` and `medium` assign no reproduce job',
                       'At `xhigh` and `max`, assign one reproduce job per finding, then a diff-review with the '
                       'angle `sweep`, the verified list and the full coverage, and reproduce its new findings too.',
                       'pinned base, diff_base, head, the full changed-file list, allowed behavior boundary, '
                       'priority sources, the review\'s `effort level` and the `angle`'):
            self.assertIn(phrase, body)


if __name__ == '__main__':
    unittest.main()
