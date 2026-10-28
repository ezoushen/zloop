#!/usr/bin/env python3
"""Tickets are cut by one slicing doc of our own, which refine and dispatch's split step both read."""
import re
import unittest

import support

ROOT = support.ROOT
SLICING = ROOT / 'runtime/board/slicing.md'


def prose(path):
    return ' '.join(path.read_text().split())


class Slicing(unittest.TestCase):
    def test_the_slicing_doc_states_each_rule(self):
        text = re.sub(r'<!--.*?-->', '', prose(SLICING))
        for rule in (
                'The cutting procedure owns stories, criteria and publication; this doc owns where the cuts go.',
                'Cut thin end-to-end slices: each delivers one observable behaviour through every layer it needs, '
                'each verifiable on its own, each sized for one fresh context window.',
                'A slice that only builds one layer for a later slice to use is not a ticket.',
                'Preparatory refactors and wide mechanical changes are the exceptions to end-to-end slices.',
                'Preparatory refactors come first: when a slice is easier after a change that keeps behaviour the '
                'same, that change is its own earlier ticket, and the slice is blocked by it.',
                'A wide mechanical change, such as a rename or an interface replaced across many callers, runs as '
                'expand → migrate in batches → contract: add the new form beside the old, move callers over in '
                'batches, then remove the old form once nothing uses it.',
                'Expand, each migrate batch and contract are tickets: each batch is blocked by expand and contract is '
                'blocked by every batch, with batches sized by blast radius.',
                'Batches that cannot stay green alone share the effort branch and end in the spec E2E: write them as '
                'ordinary tickets in a chain, with no separate integration branch or integrate-and-verify ticket.',
                'Each ticket lists what blocks it.',
                "Draw an edge only where one ticket needs another's result; independent slices stay parallel.",
                'Ticket cycles and cycles between efforts are invalid.',
                'A cross-effort edge waits for the prerequisite effort to finish and merge into main.',
                'Work that must integrate before then belongs in the same effort.',
                'Weigh handoff cost before splitting adjacent outcomes.',
                'When they share the same small implementation, fixtures and verification, keep them together as one '
                'coherent slice; split where independent delivery, risk, dependencies or context size earns the extra '
                'worker setup and integration.',
                'Individual settings or acceptance criteria need not each become a ticket.',
                'Tickets sharing a device, serving lane or mutable fixture share a reservation, even when their files '
                'differ; account for that resource during implementation, not only E2E.',
                'Separate the experiment that decides value from the automation that depends on it, and run it first.',
                'For optimization or operational work, distinguish research hypotheses from measurements on the '
                'actual targets.',
                'When local applicability or benefit decides the implementation, put a baseline using existing tools '
                'first, then a representative trial before wider application.',
                'Define the metric, comparable workload conditions and success criterion, and keep a supported '
                'no-change outcome when the change adds no value.',
                'Evidence from another environment suggests what to investigate, not what to build.',
                'When the result can make later work unnecessary, the later work is blocked by it and stays deferred '
                'scope until the result is accepted.'):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)
        self.assertIn('[Slicing tickets](slicing.md)', prose(ROOT / 'runtime/board/board.md'))

    def test_refine_split_work_and_the_map_read_the_slicing_doc(self):
        readers = {'skills/refine/SKILL.md': '](../../runtime/board/slicing.md)',
                   'skills/dispatch/split-work.md': '](../../runtime/board/slicing.md)',
                   'skills/brief/pipeline.md': '](../../runtime/board/slicing.md)'}
        for name, link in readers.items():
            text = prose(ROOT / name)
            with self.subTest(doc=name):
                self.assertIn(link, text)
                self.assertNotIn('spec.md', text)
        self.assertNotIn('](../refine/', prose(ROOT / 'skills/dispatch/split-work.md'))
        refine = prose(ROOT / 'skills/refine/SKILL.md')
        self.assertIn("Dispatch's split step cuts already-selected work by the same slicing rules", refine)
        self.assertNotIn('Dispatch also uses it to split', refine)
        self.assertLess(len((ROOT / 'skills/refine/SKILL.md').read_bytes()), 12 * 1024)

    def test_a_split_child_meets_the_bar_a_refined_ticket_meets(self):
        text = re.sub(r'<!--.*?-->', '', prose(ROOT / 'skills/dispatch/split-work.md'))
        for rule in (
                "Selected work too large for one worker is split by the board's [slicing rules]"
                "(../../runtime/board/slicing.md), within native permissions and existing tracker-write "
                "authorization, in the project's ticket convention.",
                'Each child meets the bar a refined ticket meets: a one-sentence story; observable Given/When/Then '
                'criteria, each with a `Verify` line naming the command or test and who observes it (`code`, `agent` '
                'or `user`), and a check that cannot pass while the criterion is broken and that normal variation '
                'cannot swamp.',
                "An edge the parent's brief does not settle is a ledger row with a hinge.",
                'Run the configured [`refinement` checkpoint](../../runtime/decisions/checkpoint-command.md) on the cut.',
                'A child is ready when a builder with an empty context can build it from these, the parent\'s brief and '
                'the project instructions.',
                'On a local Markdown board each child carries the front matter a refined ticket carries — `ticket`, '
                '`status`, `blocked_by` (always present, `[]` when nothing gates it), `touches`, `devices`, `model` and '
                '`worker` — and the split ends with `python3 "<plugin_root>/scripts/next_tickets.py" --repo "<repo>" '
                '<slug> --check`.'):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_a_split_reuses_an_existing_breakdown_instead_of_refining_it_again(self):
        text = re.sub(r'<!--.*?-->', '', prose(ROOT / 'skills/dispatch/split-work.md'))
        self.assertIn('Reuse existing child issues and dependencies; dispatch ready children rather than refining an '
                      'already executable breakdown again.', text)

if __name__ == '__main__':
    unittest.main()
