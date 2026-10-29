#!/usr/bin/env python3
"""This repository records its own binding decisions as ADRs with a checked index."""
import json
import re
import subprocess
import unittest

import support

ROOT = support.ROOT


class RepoAdrs(unittest.TestCase):
    def test_the_repositorys_adrs_pass_the_check_with_an_index(self):
        result = subprocess.run(['python3', str(support.script('adr_numbers.py')), '--repo', str(ROOT),
                                 '--index'], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual('0016', out['next'])
        index = (ROOT / 'docs/adr/README.md').read_text()
        accepted = index[index.index('## Accepted'):index.index('## Superseded')]
        for name in ('0001-adr-numbers-are-checked-on-main.md', '0002-grilling-asks-principles-only.md',
                     '0003-adrs-record-what-others-follow.md', '0004-adrs-are-superseded-never-rewritten.md',
                     '0005-the-adr-index-is-the-current-state.md', '0006-adr-statuses-have-no-proposed.md',
                     '0007-builders-work-test-first-from-our-own-standard.md',
                     '0008-builders-refactor-after-green-within-the-ticket.md',
                     '0009-the-framework-depends-on-no-external-skill-package.md',
                     '0010-autopilot-merges-only-through-the-normal-path.md',
                     '0011-the-engineering-standard-is-extended-never-overridden.md',
                     '0012-an-autopilot-resume-timer-is-the-users-own-take-over.md',
                     '0013-no-backward-compatibility-before-a-stable-release.md',
                     '0014-the-doc-checker-enforces-the-glossary.md',
                     '0015-effort-levels-scale-thoroughness-above-a-fixed-floor.md'):
            self.assertIn(f']({name})', accepted)

    def test_adr_0004_states_the_repair_exception(self):
        text = ' '.join((ROOT / 'docs/adr/0004-adrs-are-superseded-never-rewritten.md').read_text().split())
        self.assertIn('apart from repairing a broken status or supersede link', text)

    def test_each_standalone_framework_adr_states_its_rule(self):
        rules = {
            '0007-builders-work-test-first-from-our-own-standard.md':
                'Each test names the business rule it protects, takes its expected value from an independent source '
                'and each new or changed test is shown to fail when that rule is deliberately broken; coverage means '
                'every rule and acceptance criterion has such a test, not a line-coverage figure.',
            '0008-builders-refactor-after-green-within-the-ticket.md':
                "Once a test passes, the builder refactors the ticket's own code within the ticket's scope, keeping "
                'the tests green; a wider refactor goes to review.',
            '0009-the-framework-depends-on-no-external-skill-package.md':
                "A repository-wide test rejects the package's name and its distinctive skill names outside the "
                'journals, the ADR folder and the test itself',
        }
        rules_first = {
            '0007-builders-work-test-first-from-our-own-standard.md':
                'Every builder works test-first from the [TDD standard](../../standards/tdd.md): one failing test '
                'seen to fail, then only enough code to pass it, one rule at a time.',
            '0009-the-framework-depends-on-no-external-skill-package.md':
                'The framework depends on no external skill package: its manifests declare no skill dependency, and '
                'its procedures, standards and jobs are its own, under its own names.',
        }
        for name, rule in list(rules.items()) + list(rules_first.items()):
            with self.subTest(adr=name, rule=rule[:40]):
                self.assertIn(rule, ' '.join((ROOT / 'docs/adr' / name).read_text().split()))

    def test_each_accepted_index_entry_starts_with_its_adr_title(self):
        index = (ROOT / 'docs/adr/README.md').read_text()
        accepted = index[index.index('## Accepted'):index.index('## Superseded')]
        entries = re.findall(r'^- \[\d{4}\]\((\d{4}-[^)]+\.md)\) (.+)$', accepted, re.M)
        self.assertEqual(15, len(entries))
        for name, text in entries:
            title = (ROOT / 'docs/adr' / name).read_text().splitlines()[0].removeprefix('# ')
            with self.subTest(adr=name):
                self.assertTrue(text.startswith(title + ':'), text)

    def test_every_accepted_adr_has_a_one_line_rule_in_the_development_doc(self):
        text = (ROOT / 'docs/development.md').read_text()
        rules = {link: ' '.join(rule.split()) for rule, link in
                 re.findall(r'(?m)^- ((?:[^\n]|\n  )*?)\(\[ADR \d{4}\]\(adr/([^)]+)\)\)\.$', text)}
        index = (ROOT / 'docs/adr/README.md').read_text()
        accepted = index[index.index('## Accepted'):index.index('## Superseded')]
        for name in re.findall(r'^- \[\d{4}\]\((\d{4}-[^)]+\.md)\)', accepted, re.M):
            with self.subTest(adr=name):
                self.assertGreaterEqual(len(rules.get(name, '').split()), 4, rules.get(name))

    def test_the_resume_timer_adr_states_its_authority_its_guard_and_where_it_applies(self):
        adr = ' '.join((ROOT / 'docs/adr/0012-an-autopilot-resume-timer-is-the-users-own-take-over.md').read_text().split())
        self.assertIn('schedules its own resume where the host has a scheduler', adr)
        self.assertIn("recorded on the run card, is its authorization", adr)
        self.assertIn('runs `--take-over --timed`, which resumes only a card that still ended `paused-allowance`', adr)
        index = ' '.join((ROOT / 'docs/adr/README.md').read_text().split())
        development = ' '.join((ROOT / 'docs/development.md').read_text().split())
        for text in (index, development):
            self.assertRegex(text, r"resume timer is the user's own take-over[^\n]*authorized on the run card[^\n]*`--take-over --timed`")

    def test_the_development_rules_point_to_the_standalone_framework_adrs(self):
        text = ' '.join((ROOT / 'docs/development.md').read_text().split())
        for rule in (
                'Builders work test-first from `standards/tdd.md` and each test protects a named business rule '
                '([ADR 0007](adr/0007-builders-work-test-first-from-our-own-standard.md)).',
                "Builders refactor the ticket's own code after green, within the ticket's scope "
                '([ADR 0008](adr/0008-builders-refactor-after-green-within-the-ticket.md)).',
                'The plugin declares no external skill package as a dependency '
                '([ADR 0009](adr/0009-the-framework-depends-on-no-external-skill-package.md)).'):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_contributors_are_told_to_read_the_index_first(self):
        text = ' '.join((ROOT / 'docs/development.md').read_text().split())
        self.assertIn('[decision index](adr/README.md) before an architectural change', text)
        self.assertIn('only accepted ADRs bind', text)


if __name__ == '__main__':
    unittest.main()
