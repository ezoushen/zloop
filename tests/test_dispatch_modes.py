#!/usr/bin/env python3
"""Dispatch runs each ticket inline or delegated, and both follow one ticket contract."""
import subprocess
import unittest

import support

ROOT = support.ROOT
DISPATCH = ROOT / 'skills/dispatch'
MODES = DISPATCH / 'modes.md'


def prose(path):
    return ' '.join(path.read_text().split())


class TicketModes(unittest.TestCase):
    def test_the_orchestrator_chooses_how_each_ticket_runs_and_records_why(self):
        text = prose(MODES)
        for rule in ('**Inline:** the orchestrator implements the ticket itself in the workstream worktree, on the workstream branch',
                     '**Delegated:** a builder implements it in its own worktree and branch',
                     'Default to inline for sequential, well-understood tickets whose context the session already holds',
                     'Delegate tickets that can run in parallel, need a clean context, carry high risk or would crowd',
                     "The user's instruction on how a ticket runs wins",
                     'Record the mode and its reason in the run report'):
            self.assertIn(rule, text)
        self.assertIn('[how a ticket runs](modes.md)', prose(DISPATCH / 'iteration.md'))

    def test_an_inline_ticket_keeps_the_ticket_contract_and_its_report_names_the_branch(self):
        text = prose(MODES)
        for kept in ('follows the [ticket job](jobs/ticket.md) itself', '`branch: <workstream branch>`',
                     '`worker=inline`', 'the integrated review is its independent check'):
            self.assertIn(kept, text)
        job = prose(DISPATCH / 'jobs/ticket.md')
        self.assertIn('For an inline ticket the orchestrator follows this job itself', job)
        self.assertIn('the branch the report records', prose(ROOT / 'runtime/board/board.md'))
        self.assertIn('one `Commit: <sha>` line per commit', job)

    def test_an_inline_ticket_skips_only_the_builder_setup(self):
        iteration = prose(DISPATCH / 'iteration.md')
        self.assertIn('An inline ticket skips only the ticket branch and worktree, their preparation, the builder '
                      'launch with its launch decision record, and the merge', iteration)
        self.assertIn('everything else in steps 4 and 5 applies to both modes', iteration)
        text = prose(MODES)
        for rule in ('Commit inline work before merging a delegated ticket',
                     'A blocked inline ticket follows the same escalation and two-attempt rules'):
            self.assertIn(rule, text)

    def test_the_builder_still_never_works_on_main_or_outside_its_worktree(self):
        builder = prose(ROOT / 'agents/builder.md')
        self.assertIn('Change code only in the assigned worktree, on the assigned branch', builder)
        self.assertIn('Never work on `main`', builder)

    def test_a_workstream_gets_its_own_branch_unless_the_user_says_main(self):
        text = prose(MODES)
        for rule in ('Every workstream gets a dedicated branch by default',
                     'Only an explicit user instruction to work directly on main makes `main` the workstream branch',
                     'record that instruction and the starting commit in the run report',
                     'delegated tickets merge into `main` under that instruction', 'Never push'):
            self.assertIn(rule, text)
        self.assertIn('[directly on main](modes.md#directly-on-main)', prose(DISPATCH / 'iteration.md'))
        self.assertIn('ensure the workstream branch', prose(DISPATCH / 'iteration.md'))
        self.assertIn('Apart from working directly on main, never commit to the user', prose(DISPATCH / 'iteration.md'))
        board = prose(ROOT / 'runtime/board/board.md')
        self.assertIn('With `workstream_branch: main` the reviewed head must instead be an ancestor of the current `main`', board)
        self.assertIn('later commits on `main` are not covered by that evidence', board)
        for rule in ('A stale or diverged local `main` is reported and the user asked before the first commit',
                     'On `main` the orchestrator resolves an integration conflict itself',
                     'pass `--base <starting commit>`'):
            self.assertIn(rule, prose(MODES))

    def test_a_workstream_on_main_is_reviewed_from_its_starting_commit_and_has_no_pr(self):
        self.assertIn('review covers the starting commit to the current `main`', prose(MODES))
        self.assertIn('`workstream_branch: main`', prose(MODES))
        self.assertIn('`workstream_branch: main`', prose(ROOT / 'skills/review/report.md'))
        self.assertIn('A workstream the user ran directly on `main` has no branch to publish', prose(ROOT / 'skills/pr/SKILL.md'))

    def test_the_map_and_readme_name_both_modes(self):
        self.assertIn('inline by the orchestrator or by a fresh builder in its own worktree',
                      prose(ROOT / 'skills/brief/pipeline.md'))
        self.assertIn('inline or in builder worktrees', prose(ROOT / 'README.md'))


if __name__ == '__main__':
    unittest.main()
