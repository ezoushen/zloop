#!/usr/bin/env python3
"""An overruled decision is superseded, never rewritten, and the index shows what holds now."""
import unittest

import support
from support import prose


class AdrSupersession(unittest.TestCase):
    def test_the_adr_doc_states_statuses_supersession_and_the_index(self):
        text = prose('runtime/domain/adr.md')
        for rule in ('`accepted` (the default), `superseded by ADR-NNNN` or `deprecated`',
                     'There is no `proposed` status',
                     'names the ADR it replaces in `supersedes: ADR-NNNN`',
                     "the old ADR's only edit is its status line",
                     "Repairing a broken status or supersede link in an ADR's front matter is the one other edit; its text never changes",
                     'A partial overrule supersedes the whole ADR and restates what still holds',
                     '--index [--dir', 'Before offering, check the [index](adr-index.md)'):
            self.assertIn(rule, text)
        index = prose('runtime/domain/adr-index.md')
        for rule in ('`docs/adr/README.md` is the current-state index',
                     'one line per accepted ADR: its number, title and rule in one line',
                     'updated in the same commit as the ADRs it lists',
                     'Read the index first, then only the ADRs that bear on the work',
                     'when run with `--index`, which also makes a missing index a failure',
                     'a list line that starts with a plain link to the ADR\'s file', 'Prose and headings are not entries', 'A list line that names an ADR file but is not an entry is reported, never skipped'):
            self.assertIn(rule, index)
        self.assertNotIn('`proposed`, `accepted`', text)

    def test_consumers_read_the_index_first(self):
        for path, link in (('skills/align/SKILL.md', '../../runtime/domain/adr-index.md'),
                           ('skills/dispatch/jobs/ticket.md', '../../../runtime/domain/adr-index.md')):
            with self.subTest(path=path):
                self.assertIn(f'index]({link})', prose(path))


if __name__ == '__main__':
    unittest.main()
