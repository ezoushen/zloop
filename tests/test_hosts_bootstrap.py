#!/usr/bin/env python3
"""Every session starts from the bootstrap and the host's session part; hosts keep a fixed shape.

The expected triggers and owners come from the ticket's acceptance list (rules that must fire
outside their owner), not from the bootstrap's own text.
"""
import json
import re
import sys
import unittest

import support

ROOT = support.ROOT
CHECKER = ROOT / '.agents/skills/writing-agent-docs'
sys.path.insert(0, str(CHECKER))
import check_docs  # noqa: E402
from architecture import Architecture  # noqa: E402

HOSTS = ('claude', 'codex', 'pi', 'opencode')
# trigger wording in the bootstrap table -> the owner document its row must name
TRIGGERS = {
    r'creat\w+ a PR': 'standards/pr.md',
    r'mandate/alignment questions': 'runtime/interview/question-filter.md',
    r'explicit phase composition': 'skills/refine/SKILL.md',
    r'before .*worker launch': 'runtime/workers/launch.md',
    r'worker blockers': 'runtime/workers/escalation.md',
    r'decisions\.enabled': 'runtime/decisions/checkpoint.md#phase-checkpoints',
    r'HERDR_ENV=1': 'hosts/herdr.md',
    r'tracker': 'runtime/board/conventions.md',
    r'compaction': 'runtime/workers/long-sessions.md',
    r'autopilot run card': 'skills/autopilot/SKILL.md',
    r'correction': 'runtime/interview/record.md',
    r'source comment': 'standards/code-comments.md',
}
PATH = re.compile(r'(?:[\w.-]+/)*[\w.-]+\.md(?:#[\w-]+)?')


def rows():
    """(trigger cell, owner cell) of each table row in the bootstrap."""
    table = [line for line in (ROOT / 'bootstrap.md').read_text().splitlines() if line.startswith('|')]
    return [tuple(cell.strip() for cell in line.strip('|').split('|', 1)) for line in table[2:]]


def has_heading(path, anchor):
    slugs = {re.sub(r'[^\w\- ]', '', line.lstrip('#').strip().lower()).replace(' ', '-')
             for line in path.read_text().splitlines() if line.startswith('#')}
    return anchor in slugs


class Bootstrap(unittest.TestCase):
    def test_each_listed_trigger_has_a_row_whose_owner_document_exists(self):
        found = rows()
        for wording, owner in TRIGGERS.items():
            with self.subTest(trigger=wording):
                matching = [cell for trigger, cell in found if re.search(wording, trigger, re.I)]
                self.assertEqual(len(matching), 1, f'one row for {wording}')
                named = PATH.findall(matching[0])
                self.assertIn(owner, named if '#' in owner else [item.split('#')[0] for item in named])
                path, _, anchor = owner.partition('#')
                self.assertTrue((ROOT / path).is_file(), path)
                if anchor:
                    self.assertTrue(has_heading(ROOT / path, anchor), owner)

    def test_the_hint_names_the_host_and_an_explicit_host_selects_its_session_part_when_the_hint_is_absent(self):
        text = ' '.join((ROOT / 'bootstrap.md').read_text().split())
        self.assertIn('supplies `zloop host: claude|codex|pi`', text)
        fallback = text[text.index('If the hint is absent'):].split('. Do not guess', 1)[0]
        self.assertEqual(re.findall(r'`host=(\w+)`', fallback), list(HOSTS))
        self.assertIn('selects `hosts/<host>/session.md`', fallback)

    def test_the_pr_row_names_the_delivery_checkpoint_before_the_check_watch(self):
        [owner] = [cell for trigger, cell in rows() if re.search(r'creat\w+ a PR', trigger)]
        self.assertRegex(owner, r'run the `delivery` checkpoint or record an evidenced skip, then the \[required-check watch\]')

    def test_the_worker_launch_row_links_the_lifecycle_docs_directly(self):
        # A Pi orchestrator that never followed launch.md's links launched ten workers it never registered.
        [owner] = [cell for trigger, cell in rows() if re.search(r'before .*worker launch', trigger, re.I)]
        linked = [link for _, link in check_docs.links(owner)]
        for doc in ('runtime/workers/prepare.md', 'runtime/workers/wait.md'):
            self.assertIn(doc, linked)

    def test_every_path_a_row_names_exists(self):
        for trigger, cell in rows():
            for named in PATH.findall(cell):
                path, _, anchor = named.partition('#')
                self.assertTrue((ROOT / path).is_file(), (trigger, named))
                if anchor:
                    self.assertTrue(has_heading(ROOT / path, anchor), (trigger, named))

    def test_no_lower_document_links_the_bootstrap(self):
        for folder in ('hosts', 'agents', 'standards', 'runtime'):
            for path in (ROOT / folder).rglob('*.md'):
                for line in path.read_text().splitlines():
                    for _, link in check_docs.links(line):
                        self.assertNotIn('bootstrap.md', link.split('#')[0], path)

    def test_the_brief_skill_reads_at_most_twelve_kib_before_acting(self):
        data = json.loads((CHECKER / 'architecture.json').read_text())
        data['first_read']['skills'] = ['skills/brief/SKILL.md']
        data['first_read']['budget'] = 12 * 1024
        data['debt'] = {}
        findings = check_docs.first_read(ROOT, Architecture(data), lambda path: str(path.relative_to(ROOT)))
        self.assertEqual([(f['path'], f['message']) for f in findings], [])

    def test_the_bootstrap_and_hosts_carry_no_recorded_debt(self):
        debt = json.loads((CHECKER / 'architecture.json').read_text())['debt']
        owned = ('bootstrap.md', 'hosts/')
        self.assertEqual([link for link in debt['links'] if any(part.startswith(owned) for part in link.split(' -> '))], [])


class Hosts(unittest.TestCase):
    def test_each_host_folder_holds_a_session_part_and_a_worker_part(self):
        for host in HOSTS:
            with self.subTest(host=host):
                self.assertEqual(sorted(path.name for path in (ROOT / 'hosts' / host).glob('*.md')),
                                 ['session.md', 'workers.md'])

    def test_the_repository_check_reports_no_host_or_bootstrap_finding(self):
        config = json.loads((CHECKER / 'config.json').read_text())
        architecture = Architecture.load(CHECKER / config['architecture'])
        findings = check_docs.check(ROOT, config, None, architecture)
        mine = [(f['path'], f['code'], f['message']) for f in findings if f['severity'] == 'error'
                or (f['path'].startswith(('hosts/', 'bootstrap.md')) and f['code'] != 'shared-section')]
        self.assertEqual(mine, [])

    def test_a_session_part_names_its_worker_part(self):
        for host in HOSTS:
            text = (ROOT / 'hosts' / host / 'session.md').read_text()
            self.assertIn('workers.md', text, host)


if __name__ == '__main__':
    unittest.main()
