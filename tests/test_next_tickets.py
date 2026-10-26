#!/usr/bin/env python3
"""The local board's scheduling rules and its command line."""
import os
import json
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import support

os.environ.pop("TICKET_PRIORITY", None)
os.environ.pop("MAX_LIVE", None)

SCRIPT = support.script("next_tickets.py")
nt = support.load_script("next_tickets.py", "next_tickets")
ready, can_verify, counts, report_problems, MAX_LIVE = (
    nt.ready, nt.can_verify, nt.counts, nt.report_problems, nt.MAX_LIVE)


def ticket(name, status, touches, devices=(), blocked=()):
    return {"ticket": name, "status": status, "touches": list(touches), "devices": list(devices),
            "blocked_by": list(blocked), "model": "sonnet", "path": "x"}


def names(tickets):
    return [fm["ticket"] for fm in ready(tickets)]


class Scheduling(unittest.TestCase):
    def test_a_device_is_held_only_while_its_holder_is_verifying(self):
        # An unrelated ticket may start work on a device it will not touch until its own E2E run...
        verifying_on_ios = {
            "apple-native-edge/01": ticket("apple-native-edge/01", "verifying", ["apple"], ["ios"]),
            "runtime-instance/02": ticket("runtime-instance/02", "ready", ["runtime"], ["android", "ios"]),
        }
        assert names(verifying_on_ios) == ["runtime-instance/02"], names(verifying_on_ios)
        # ...but it must not enter `verifying` while the holder is still there.
        assert can_verify(verifying_on_ios, "runtime-instance/02") is False
        verifying_on_ios["apple-native-edge/01"]["status"] = "done"
        assert can_verify(verifying_on_ios, "runtime-instance/02") is True

    def test_two_live_tickets_never_share_an_area(self):
        same_area = {"a/01": ticket("a/01", "implementing", ["runtime"]),
                     "b/01": ticket("b/01", "ready", ["runtime"])}
        assert names(same_area) == [], names(same_area)

    def test_blockers_must_be_done_and_the_live_cap_holds(self):
        blocked = {"a/01": ticket("a/01", "reviewing", ["apple"]),
                   "b/01": ticket("b/01", "ready", ["runtime"], blocked=["a/01"])}
        assert names(blocked) == [], names(blocked)
        assert len(ready({f"s{i}/01": ticket(f"s{i}/01", "ready", [f"area{i}"]) for i in range(MAX_LIVE + 2)})) == MAX_LIVE

    def test_tickets_of_one_spec_run_together_when_their_areas_differ(self):
        same_spec_parallel = {"s/01": ticket("s/01", "implementing", ["generator"]),
                              "s/02": ticket("s/02", "ready", ["docs"]),
                              "t/01": ticket("t/01", "ready", ["apple"])}
        assert names(same_spec_parallel) == ["s/02", "t/01"], names(same_spec_parallel)

    def test_a_full_board_admits_nothing(self):
        # The live cap is checked before admitting a candidate, not after.
        full = {f"s{i}/01": ticket(f"s{i}/01", "implementing", [f"area{i}"]) for i in range(MAX_LIVE)}
        full["spare/01"] = ticket("spare/01", "ready", ["spare"])
        assert names(full) == [], names(full)

    def test_a_spec_verifies_with_every_device_its_tickets_name(self):
        spec_union = {"u/01": ticket("u/01", "done", ["a"], ["android"]),
                      "u/02": ticket("u/02", "verifying", ["b"], []),
                      "v/01": ticket("v/01", "done", ["c"], ["android"])}
        assert can_verify(spec_union, "v/01") is False

    def test_the_count_line_names_every_bucket(self):
        # Zeros included, so the goal evaluator can read "0 ready".
        line = counts({"a/01": ticket("a/01", "done", ["x"])})
        assert line == "1 done · 0 blocked · 0 ready · 0 live", line

    def test_a_recorded_branch_name_follows_git_rules_and_is_never_head_or_an_option(self):
        for name in ('-h', 'HEAD', 'feat/\x00bad', 'main~1', '', None):
            assert not nt.valid_branch_name(name), name
        for name in ('main', 'feat/x-01', 'feat/x+01', '_effort', 'feat/v1.2'):
            assert nt.valid_branch_name(name), name

    def test_acceptance_requires_base_section_and_checkboxes(self):
        good = "base: `" + "a" * 40 + "`\n## Implemented\n- [x] done\n"
        assert report_problems(good) == [], report_problems(good)
        assert len(report_problems("# empty\n")) == 3


class BoardCLI(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop-board-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(self.root)], check=True)

    def write_ticket(self, name, status='ready', touches='runtime', blocked='', devices='', extra=''):
        # inline/01 of a measured effort keeps the start measured_ticket recorded, as --set would have.
        if name == 'inline/01' and getattr(self, 'inline_start', None) and 'start:' not in extra:
            extra += f'start: {self.inline_start}\n'
        slug, number = name.split('/')
        path = self.root / '.scratch' / slug / 'issues' / f'{number}-task.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'---\nticket: {name}\nstatus: {status}\n'
                        f'blocked_by: [{blocked}]\ntouches: [{touches}]\ndevices: [{devices}]\n{extra}---\n')
        return path

    def cli(self, *args, code=0, input=None):
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--repo', str(self.root), *args],
                                capture_output=True, text=True, input=input)
        assert result.returncode == code, result.stdout + result.stderr
        return result

    def test_board_reads_validates_and_repairs_ticket_state(self):
        write_ticket, cli = self.write_ticket, self.cli
        active = write_ticket('active/01')
        history = self.root / '.scratch/history/issues/old.md'
        history.parent.mkdir(parents=True)
        history.write_text('# Historical issue\nNo machine ticket state.\n')
        assert [item['ticket'] for item in json.loads(cli('active').stdout)] == ['active/01']
        history.write_text('---\nstatus: closed\n---\n# Historical tracker note\n')
        cli('active', '--check')

        write_ticket('active/01', 'typo')
        assert 'typo' in cli('active', '--board').stdout
        assert 'status' in cli('active', '--check', code=1).stderr
        cli('active', '--set', 'active/01', 'status=ready')
        cli('active', '--check')
        active.write_text(active.read_text().replace('status: ready\n', ''))
        assert '<missing>' in cli('active', '--board').stdout
        cli('active', '--set', 'active/01', 'status=ready')
        cli('active', '--check')

        write_ticket('other/01', 'implementing')
        assert json.loads(cli('active').stdout) == []
        write_ticket('other/01', 'implementing', touches='unrelated')
        assert json.loads(cli('active', '--max-live', '1').stdout) == []
        write_ticket('other/01', 'verifying', touches='unrelated', devices='ios')
        write_ticket('active/01', 'done', devices='ios')
        assert 'held by other/01' in cli('active', '--can-verify', 'active/01', code=1).stdout

        write_ticket('active/01', blocked='missing/01')
        assert 'does not exist' in cli('active', '--check', code=1).stderr
        write_ticket('active/01', blocked='other/01')
        write_ticket('other/01', blocked='active/01')
        assert 'dependency cycle' in cli('active', '--check', code=1).stderr
        duplicate = active.with_name('02-duplicate.md')
        duplicate.write_text(active.read_text())
        assert 'duplicate ticket' in cli('active', '--board', code=1).stderr
        duplicate.unlink()
        history.write_text('---\nstatus: implementing\ntouches: [runtime]\n---\n')
        assert 'missing ticket' in cli('active', '--board', code=1).stderr

    def test_blocking_a_ticket_needs_a_reason_recorded_on_the_ticket(self):
        # Rule (E4 pi): `status=blocked` is refused without `blocker=<reason>`, leaving the ticket as it was; with one,
        # the ticket's front matter keeps the status and the reason.
        path = self.write_ticket('active/01', 'implementing')
        before = path.read_text()
        refused = self.cli('active', '--set', 'active/01', 'status=blocked', code=1)
        assert refused.stderr == ('active/01: status=blocked needs the reason too: '
                                  '--set active/01 status=blocked blocker=<reason>\n'), refused.stderr
        assert path.read_text() == before
        self.cli('active', '--set', 'active/01', 'status=blocked', 'blocker=  ', code=1)
        assert path.read_text() == before
        self.cli('active', '--set', 'active/01', 'status=blocked', 'blocker=recall is 50, worse than baseline 100')
        fm = nt.parse_front_matter(path.read_text())
        assert (fm['status'], fm['blocker']) == ('blocked', 'recall is 50, worse than baseline 100'), fm

    def test_a_stored_blocker_is_cleared_on_unblocking_and_never_reused(self):
        # Rule: leaving `blocked` clears the stored reason; blocking again needs a new one, and the refusal says the
        # stored one is not reused.
        path = self.write_ticket('active/01', 'blocked', extra='blocker: device offline\n')
        refused = self.cli('active', '--set', 'active/01', 'status=blocked', code=1)
        assert refused.stderr == ('active/01: status=blocked needs the reason too, even with one stored '
                                  "('device offline'): --set active/01 status=blocked blocker=<reason>\n"), refused.stderr
        self.cli('active', '--set', 'active/01', 'status=ready')
        fm = nt.parse_front_matter(path.read_text())
        assert (fm['status'], 'blocker' in fm) == ('ready', False), fm

    def test_a_ticket_moved_into_implementing_records_the_effort_head_it_starts_from(self):
        # Rule (T12.2): the first move into implementing records `start:`, the effort branch head (main when the effort
        # has none); a recorded start never changes, and a ticket already live is not given one.
        start = lambda path: nt.parse_front_matter(path.read_text()).get('start')
        first = self.write_ticket('active/01')
        before = first.read_text()
        refused = self.cli('active', '--set', 'active/01', 'status=implementing', code=1).stderr
        assert refused == ('active/01: no effort branch (feat/active) or main to record the start of implementing '
                           'from\n'), refused
        assert first.read_text() == before
        on_main = self.commit('main head')
        self.cli('active', '--set', 'active/01', 'status=implementing')
        assert start(first) == on_main, first.read_text()
        self.git('switch', '-q', '-c', 'feat/active')
        effort_head = self.commit('effort head')
        self.git('switch', '-q', 'main')
        self.commit('main moves on')
        for status in ('ready', 'blocked'):
            path = self.write_ticket('active/02', status, touches='docs', extra='blocker: waits\n')
            self.cli('active', '--set', 'active/02', 'status=implementing')
            assert start(path) == effort_head, (status, path.read_text())
        self.cli('active', '--set', 'active/02', 'status=reviewing')
        self.cli('active', '--set', 'active/02', 'status=implementing')
        assert start(path) == effort_head, path.read_text()
        self.cli('active', '--set', 'active/02', 'status=ready')
        self.git('switch', '-q', 'feat/active')
        self.commit('the effort moves on')
        self.cli('active', '--set', 'active/02', 'status=implementing')
        assert start(path) == effort_head, path.read_text()
        for status in ('reviewing', 'fixing', 'implementing'):
            live = self.write_ticket('active/03', status, touches='tests')
            self.cli('active', '--set', 'active/03', 'status=implementing')
            assert start(live) is None, (status, live.read_text())

    def test_start_is_set_by_hand_only_while_absent_and_only_to_a_commit(self):
        # Rule (T12.2): `--set start=<sha>` records a missing start; changing a recorded one is refused, file unchanged.
        path = self.write_ticket('active/01', 'reviewing')
        first, second = self.commit('first'), self.commit('second')
        for value in ('main', first[:7], first.upper(), '-' + first[1:], 'f' * 40, ''):
            refused = self.cli('active', '--set', 'active/01', f'start={value}', code=1).stderr
            assert refused == f'active/01: start={value} is not a 40-hex commit of this repository\n', (value, refused)
        self.cli('active', '--set', 'active/01', f'start={first}')
        recorded = path.read_text()
        assert nt.parse_front_matter(recorded)['start'] == first, recorded
        for value in (second, first):
            refused = self.cli('active', '--set', 'active/01', f'start={value}', code=1).stderr
            assert refused == f'active/01: start is {first} already and never changes\n', (value, refused)
            assert path.read_text() == recorded

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        self.git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def write_report(self, base, branch=None, commits=(), body='', number='01', returned=None):
        """A ticket report; a delegated ticket's (one off the effort branch) is returned by a collected worker attempt
        unless returned=False."""
        report = self.root / f'.scratch/inline/reports/{number}.md'
        report.parent.mkdir(parents=True, exist_ok=True)
        branch_line = f'branch: {branch}\n' if branch else ''
        listed = ''.join(f'- Commit: {sha}\n' for sha in commits)
        report.write_text(f'base: `{base}`\n{branch_line}## Implemented\n{listed}- [x] done\n{body}')
        if returned if returned is not None else branch != 'feat/inline':
            self.attempts = getattr(self, 'attempts', 0) + 1
            at = subprocess.run(['git', '-C', str(self.root), 'rev-parse', '--verify', '--quiet',
                                 f'{branch or f"feat/inline-{number}"}^{{commit}}'], capture_output=True, text=True)
            support.collected_attempt(self.root, 'inline', f'inline/{number}/implementation-{self.attempts}', report,
                                      head=at.stdout.strip() or 'none')

    def test_acceptance_checks_the_branch_the_report_records(self):
        self.write_ticket('inline/01', 'implementing')
        base = self.commit('base')
        self.git('switch', '-q', '-c', 'feat/inline')
        self.write_report(base, 'feat/inline')
        assert 'no commit past base on feat/inline' in self.cli('inline', '--accept', 'inline/01', code=1).stderr
        work = self.commit('ticket work')
        self.write_report(base, 'feat/inline', [work])
        assert 'carry the evidence' in self.cli('inline', '--accept', 'inline/01').stdout
        self.write_report(base)
        assert 'feat/inline-01' in self.cli('inline', '--accept', 'inline/01', code=1).stderr
        self.write_report(base, body=f'Checked branch: feat/inline\nbranch: feat/inline\n')
        assert 'feat/inline-01' in self.cli('inline', '--accept', 'inline/01', code=1).stderr

    def test_a_builder_commit_in_its_coordinators_checkout_is_not_accepted(self):
        # E4 claude builder 01: a native builder ran in its coordinator's directory and committed on the effort's
        # branch, leaving its own ticket branch at base; acceptance refuses the ticket branch with no commit past base.
        self.write_ticket('inline/01', 'implementing')
        base = self.commit('base')
        self.git('branch', 'feat/inline-01')
        self.git('switch', '-q', '-c', 'feat/inline')
        stray = self.commit('ticket work, committed in the coordinator checkout')
        self.write_report(base, commits=[stray])
        refused = self.cli('inline', '--accept', 'inline/01', code=1)
        assert refused.stderr == 'inline/01: no commit past base on feat/inline-01\n', refused.stderr

    def test_a_shared_effort_branch_counts_only_the_commits_the_report_lists(self):
        self.write_ticket('inline/01', 'implementing')
        base = self.commit('base')
        self.git('switch', '-q', '-c', 'feat/inline')
        other = self.commit('another ticket merged here')
        self.write_report(base, 'feat/inline')
        assert 'no listed commit' in self.cli('inline', '--accept', 'inline/01', code=1).stderr
        self.write_report(base, 'feat/inline', [base])
        assert 'no listed commit' in self.cli('inline', '--accept', 'inline/01', code=1).stderr
        self.write_report(base, 'feat/inline', [other])
        self.cli('inline', '--accept', 'inline/01')
        self.write_report(base, 'feat/inline', body=f'- Evidence: reports/evidence/01/1/{other}/\n')
        assert 'no listed commit' in self.cli('inline', '--accept', 'inline/01', code=1).stderr
        self.git('switch', '-q', 'main')
        off_branch = self.commit('on main, not on the effort branch')
        self.git('switch', '-q', '-c', 'side', 'feat/inline')
        unmerged = self.commit('past base but never merged into the effort branch')
        self.git('switch', '-q', 'feat/inline')
        self.commit('later work on the effort branch')
        for sha in (off_branch, base, unmerged):
            self.write_report(other, 'feat/inline', [sha])
            assert 'no listed commit' in self.cli('inline', '--accept', 'inline/01', code=1).stderr, sha

    def test_a_ticket_is_done_only_once_a_passing_acceptance_is_in_the_effort_branch(self):
        # jose retro: ticket 07 was set done after --accept refused it ("--set doesn't enforce").
        self.write_ticket('inline/01', 'implementing', extra='worker:\n')
        base = self.commit('base')
        self.git('switch', '-q', '-c', 'feat/inline')
        refused = self.cli('inline', '--set', 'inline/01', 'status=done', code=1)
        assert 'inline/01: status=done needs a passing --accept first' in refused.stdout + refused.stderr, refused
        work = self.commit('ticket work')
        # Only a passing --accept records it, not --set, even through a line separator Python splits lines on.
        self.cli('inline', '--set', 'inline/01', f'accepted={work}', code=1)
        self.cli('inline', '--set', 'inline/01', f'worker=b\u2028accepted: {work}', code=1)
        self.cli('inline', '--set', 'inline/01', 'status=done', code=1)
        self.write_report(base, 'feat/inline', [work])
        self.cli('inline', '--accept', 'inline/01')
        assert f'accepted: {work}' in (self.root / '.scratch/inline/issues/01-task.md').read_text()
        # An acceptance that fails again withdraws the earlier one.
        self.write_report(base, 'feat/inline')
        self.cli('inline', '--accept', 'inline/01', code=1)
        self.cli('inline', '--set', 'inline/01', 'status=done', code=1)
        self.write_report(base, 'feat/inline', [work])
        self.cli('inline', '--accept', 'inline/01')
        # So does one whose report is gone.
        report = self.root / '.scratch/inline/reports/01.md'
        kept = report.read_text()
        report.unlink()
        self.cli('inline', '--accept', 'inline/01', code=1)
        self.cli('inline', '--set', 'inline/01', 'status=done', code=1)
        report.write_text(kept)
        self.cli('inline', '--accept', 'inline/01')
        # A report revised after its acceptance is not the one accepted, whether or not --accept ran again.
        report.write_text(kept + '- [x] added after acceptance\n')
        refused = self.cli('inline', '--set', 'inline/01', 'status=done', code=1)
        assert 'inline/01: the report changed after its --accept; accept it again' in refused.stdout + refused.stderr
        report.write_text(kept)
        self.cli('inline', '--set', 'inline/01', 'status=done')
        # A delegated ticket's accepted head must also be merged into the effort branch.
        self.write_ticket('inline/02', 'implementing', touches='docs')
        self.git('switch', '-q', '-c', 'feat/inline-02')
        delegated = self.commit('delegated ticket work')
        self.write_report(work, commits=[delegated], number='02')
        self.cli('inline', '--accept', 'inline/02')
        refused = self.cli('inline', '--set', 'inline/02', 'status=done', code=1)
        assert f'inline/02: accepted {delegated} is not in feat/inline' in refused.stdout + refused.stderr, refused
        self.git('switch', '-q', 'feat/inline')
        self.git('merge', '-q', '--ff-only', 'feat/inline-02')
        self.cli('inline', '--set', 'inline/02', 'status=done')

    def test_a_worker_result_counts_only_at_the_commit_it_names(self):
        # A symbolic head such as HEAD would be read in the coordinator's checkout, not the worker's.
        self.write_ticket('inline/01', 'implementing')
        base = self.commit('base')
        self.git('switch', '-q', '-c', 'feat/inline-01')
        work = self.commit('delegated ticket work')
        self.write_report(base, commits=[work], returned=False)
        report = self.root / '.scratch/inline/reports/01.md'
        support.collected_attempt(self.root, 'inline', 'inline/01/implementation-1', report, head='HEAD')
        refused = self.cli('inline', '--accept', 'inline/01', code=1)
        assert (f'inline/01: inline/01/implementation-1 returned its report at HEAD, not at feat/inline-01 head {work}'
                in refused.stderr), refused.stderr
        support.collected_attempt(self.root, 'inline', 'inline/01/implementation-2', report, head=work[:12])
        self.cli('inline', '--accept', 'inline/01')

    def test_a_collected_snapshot_vouches_for_the_canonical_report_with_its_content(self):
        # collect.md: collected reports stay immutable, so a worker's report may be collected as a snapshot.
        self.write_ticket('inline/01', 'implementing')
        base = self.commit('base')
        self.git('switch', '-q', '-c', 'feat/inline-01')
        work = self.commit('delegated ticket work')
        self.write_report(base, commits=[work], returned=False)
        report = self.root / '.scratch/inline/reports/01.md'
        snapshot = report.with_name('01-attempt-1.md')
        snapshot.write_bytes(report.read_bytes())
        support.collected_attempt(self.root, 'inline', 'inline/01/implementation-1', snapshot, head=work)
        self.cli('inline', '--accept', 'inline/01')
        report.write_text(report.read_text() + '- [x] ticked afterwards\n')
        refused = self.cli('inline', '--accept', 'inline/01', code=1)
        assert ('inline/01: no collected worker attempt returned .scratch/inline/reports/01.md'
                in refused.stderr), refused.stderr

    def test_a_done_ticket_lent_to_e2e_as_verifying_returns_to_done(self):
        # The E2E sentinel: a done ticket, accepted before acceptance was recorded, is set verifying and back.
        self.write_ticket('inline/01', 'done')
        self.write_ticket('inline/02', 'implementing', touches='docs')
        self.cli('inline', '--set', 'inline/01', 'status=verifying')
        self.cli('inline', '--set', 'inline/01', 'status=verifying')
        self.cli('inline', '--set', 'inline/01', 'status=done')
        # A ticket that was never done does not become done through the sentinel.
        self.cli('inline', '--set', 'inline/02', 'status=verifying')
        refused = self.cli('inline', '--set', 'inline/02', 'status=done', code=1)
        assert 'inline/02: status=done needs a passing --accept first' in refused.stdout + refused.stderr, refused

    def test_a_delegated_ticket_is_accepted_only_on_the_report_its_worker_returned(self):
        # jose retro: the orchestrator wrote base lines and ticked criteria into builders' reports to pass --accept.
        self.write_ticket('inline/01', 'implementing')
        base = self.commit('base')
        self.git('switch', '-q', '-c', 'feat/inline-01')
        work = self.commit('delegated ticket work')
        self.write_report(base, commits=[work], returned=False)
        report = self.root / '.scratch/inline/reports/01.md'
        refused = self.cli('inline', '--accept', 'inline/01', code=1)
        assert ('inline/01: no collected worker attempt returned .scratch/inline/reports/01.md'
                in refused.stderr), refused.stderr
        support.collected_attempt(self.root, 'inline', 'inline/01/implementation-1', report, head=work)
        self.cli('inline', '--accept', 'inline/01')
        # The report covers the head its worker returned it at, not a commit added to the branch afterwards.
        later = self.commit('a commit no worker reported')
        refused = self.cli('inline', '--accept', 'inline/01', code=1)
        assert (f'inline/01: inline/01/implementation-1 returned its report at {work}, not at feat/inline-01 head '
                f'{later}' in refused.stderr), refused.stderr
        self.git('reset', '-q', '--hard', work)
        report.write_text(report.read_text() + '- [x] a criterion the orchestrator ticked\n')
        refused = self.cli('inline', '--accept', 'inline/01', code=1)
        assert ('inline/01: .scratch/inline/reports/01.md changed after inline/01/implementation-1 returned it'
                in refused.stderr), refused.stderr
        # Relabelling it as inline work on the effort branch does not make a worker's report the orchestrator's.
        self.git('branch', 'feat/inline', work)
        report.write_text(report.read_text().replace('## Implemented', 'branch: feat/inline\n## Implemented'))
        refused = self.cli('inline', '--accept', 'inline/01', code=1)
        assert ('inline/01: .scratch/inline/reports/01.md changed after inline/01/implementation-1 returned it'
                in refused.stderr), refused.stderr
        # A worker that returned the ticket blocked did not finish it.
        support.collected_attempt(self.root, 'inline', 'inline/01/implementation-2', report, head=work,
                                  status='blocked')
        refused = self.cli('inline', '--accept', 'inline/01', code=1)
        assert ('inline/01: inline/01/implementation-2 returned blocked, not a completed ticket'
                in refused.stderr), refused.stderr

    def commit_gitignore(self):
        # The dispatch convention: `.scratch/` and `.worktrees/` are ignored; `build.log` stands for build output.
        (self.root / '.gitignore').write_text('.scratch/\n.worktrees/\nbuild.log\n')
        self.git('add', '.gitignore')

    def record_measurement(self, command, baseline=100, direction='higher', metric='recall', **extra):
        entry = {'name': 'recall tool', 'command': command, 'metric': metric,
                 'baseline': baseline, 'direction': direction, **extra}
        (self.root / '.scratch/inline/measurements.json').write_text(json.dumps([entry]))

    def measured_ticket(self, command, extra='', tracked=None, edits=None, **entry):
        """inline/01 is finished on feat/inline, checked out in the main checkout, and its effort records one measurement.

        `tracked` files are committed at the base; the ticket commit edits `edits`.
        """
        self.write_files(tracked)
        self.commit_gitignore()
        base = self.inline_start = self.commit('base')
        self.write_ticket('inline/01', 'implementing', extra=extra)
        self.git('switch', '-q', '-c', 'feat/inline')
        self.record_measurement(command, **entry)
        self.write_files(edits)
        self.write_report(base, 'feat/inline', [self.commit('ticket work')])
        return base

    def write_files(self, files):
        for name, text in (files or {}).items():
            if text is None:
                self.git('rm', '-q', name)
                continue
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)
            self.git('add', name)

    def test_a_metric_worse_than_its_baseline_refuses_acceptance_and_names_it(self):
        # A measured regression is a refusal even when the report and branch are structurally fine.
        self.measured_ticket('echo recall 83')
        refused = self.cli('inline', '--accept', 'inline/01', code=1).stderr
        assert refused == ("inline/01: measurement 'recall tool': recall is 83, worse than baseline 100 "
                           "(higher is better)\n"), refused
        for command in ('echo recall 100', 'echo recall 101.5'):
            self.record_measurement(command)
            assert 'carry the evidence' in self.cli('inline', '--accept', 'inline/01').stdout, command

    def test_a_metric_that_is_better_lower_is_worse_when_it_rises(self):
        self.measured_ticket('echo latency 120', baseline=100, direction='lower', metric='latency')
        refused = self.cli('inline', '--accept', 'inline/01', code=1).stderr
        assert refused == ("inline/01: measurement 'recall tool': latency is 120, worse than baseline 100 "
                           "(lower is better)\n"), refused
        for command in ('echo latency 100', 'echo latency 90'):
            self.record_measurement(command, direction='lower', metric='latency')
            assert 'carry the evidence' in self.cli('inline', '--accept', 'inline/01').stdout, command

    def test_a_metric_the_command_does_not_print_refuses_acceptance(self):
        # No `recall <number>` line means nothing was measured; a longer metric name or a comment is not that line.
        self.measured_ticket('true')
        for command in ('true', 'echo recall_t4 100', 'echo "# recall 100"'):
            self.record_measurement(command)
            refused = self.logless(self.refusal())
            assert refused == ("inline/01: measurement 'recall tool': recall has no value "
                               "(no `recall <number>` line in its output); baseline 100\n"), (command, refused)

    def test_a_metric_that_is_not_a_number_refuses_acceptance(self):
        # `nan` is the dangerous one: it compares false against every baseline, so it would read as no regression.
        self.measured_ticket('true')
        for text in ('high', 'nan', 'inf', '83%', '83 percent', ''):
            self.record_measurement(f'echo recall {text}')
            refused = self.logless(self.refusal())
            assert refused == (f"inline/01: measurement 'recall tool': recall is {text!r} (not a number); "
                               "baseline 100\n"), (text, refused)

    def test_a_measurement_command_that_fails_refuses_acceptance_even_after_printing_a_good_value(self):
        self.measured_ticket('true')
        for command, status in (('echo recall 100; exit 3', 3), ('no-such-measurement-tool-4f2a', 127)):
            self.record_measurement(command)
            refused = self.logless(self.refusal())
            assert refused == (f"inline/01: measurement 'recall tool': recall has no value "
                               f"(its command exited {status}); baseline 100\n"), (command, refused)

    def test_a_measurements_file_that_cannot_be_read_as_specified_refuses_acceptance(self):
        # An unreadable standing measurement is a gate that cannot run, never a pass.
        self.measured_ticket('true')
        path = self.root / '.scratch/inline/measurements.json'
        good = {'name': 'recall tool', 'command': 'echo recall 100', 'metric': 'recall',
                'baseline': 100, 'direction': 'higher'}
        reason = 'entry 1 needs a name, command, metric without spaces, numeric baseline and a direction of higher or lower'
        bad = [('not json', 'not valid JSON'), (json.dumps(good), 'not a JSON array of entries'),
               (json.dumps([1]), reason), (json.dumps([{k: v for k, v in good.items() if k != 'command'}]), reason)]
        bad += [(json.dumps([{**good, key: value}]), reason) for key, value in (
            ('baseline', '100'), ('baseline', True), ('baseline', float('nan')), ('direction', 'up'),
            ('metric', 'recall tool'), ('command', ''))]
        for text, why in bad:
            path.write_text(text)
            refused = self.cli('inline', '--accept', 'inline/01', code=1).stderr
            assert refused == f"inline/01: measurements.json is unusable: {why}\n", (text, refused)
        path.write_text(json.dumps([good]))
        self.cli('inline', '--accept', 'inline/01')

    def test_a_metric_name_with_a_comma_makes_the_measurements_file_unusable(self):
        # Review 1 of T12.3: `allows:` separates its pairs with commas, so a metric holding one could never be excused.
        self.measured_ticket('echo recall,x 100', metric='recall,x')
        for metric in ('recall,x', ',recall', 'recall,'):
            self.record_measurement(f'echo {metric} 100', metric=metric)
            assert self.refusal() == ("inline/01: measurements.json is unusable: entry 1 needs a metric without a "
                                      "comma, which separates the pairs of allows:\n"), (metric, self.refusal())
        self.record_measurement('echo recall.x 100', metric='recall.x')
        self.cli('inline', '--accept', 'inline/01')

    def write_ledger(self):
        ledger = self.root / '.scratch/inline/ledger.md'
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text('# inline: ledger\n\n| # | Decision | Hinge | Rejected |\n|---|---|---|---|\n'
                          '| A1 (agent) | Cut the fixture | the tool | none |\n'
                          '| U3 (user) | The SSN rule may lower recall to 80 | "go ahead" | keep 100 |\n'
                          '| U12 (user) | Keep the recall tool | quoted | drop it |\n')

    def test_a_cited_allowance_accepts_a_regression_up_to_its_bound_and_no_further(self):
        # `allows` is the worst value the ticket tolerates, inclusive, in the metric's own direction.
        self.write_ledger()
        self.measured_ticket('echo recall 83', extra='allows: recall 80\nallows_source: U3\n')
        accepted = self.cli('inline', '--accept', 'inline/01').stdout
        assert accepted == ("inline/01: measurement 'recall tool': recall is 83, worse than baseline 100; "
                            "allowed down to 80 by ledger row U3\n"
                            "inline/01: report and branch carry the evidence; judge the content now\n"), accepted
        self.record_measurement('echo recall 80')
        self.cli('inline', '--accept', 'inline/01')
        self.record_measurement('echo recall 79')
        refused = self.cli('inline', '--accept', 'inline/01', code=1).stderr
        assert refused == ("inline/01: measurement 'recall tool': recall is 79, worse than baseline 100 "
                           "and past the bound 80 that ledger row U3 allows\n"), refused
        self.write_ticket('inline/01', 'implementing', extra='allows: latency 130\nallows_source: U3\n')
        self.record_measurement('echo latency 130', direction='lower', metric='latency')
        assert 'allowed up to 130' in self.cli('inline', '--accept', 'inline/01').stdout
        self.record_measurement('echo latency 131', direction='lower', metric='latency')
        assert 'past the bound 130' in self.cli('inline', '--accept', 'inline/01', code=1).stderr

    def test_an_allowance_covers_only_the_metric_it_names(self):
        self.write_ledger()
        self.measured_ticket('echo recall 83')
        for allows in ('latency 80', 'recall_t4 80'):
            self.write_ticket('inline/01', 'implementing', extra=f'allows: {allows}\nallows_source: U3\n')
            refused = self.cli('inline', '--accept', 'inline/01', code=1).stderr
            assert refused == ("inline/01: measurement 'recall tool': recall is 83, worse than baseline 100 "
                               "(higher is better)\n"), (allows, refused)

    def test_an_allowance_may_excuse_several_metrics_each_pair_only_its_own(self):
        # Rule (T12.3): `allows:` takes comma-separated `<metric> <bound>` or `<metric> tool` pairs under one source.
        self.write_ledger()
        self.measured_ticket('echo recall 83', tracked={'measure.sh': 'echo recall 83\n'},
                             edits={'measure.sh': 'echo recall 83 # edited\n'})
        entries = [{'name': 'recall tool', 'command': 'echo recall 83', 'metric': 'recall', 'baseline': 100,
                    'direction': 'higher', 'paths': ['measure.sh']},
                   {'name': 'latency tool', 'command': 'echo latency 120', 'metric': 'latency', 'baseline': 100,
                    'direction': 'lower'}]
        (self.root / '.scratch/inline/measurements.json').write_text(json.dumps(entries))
        allow = lambda value: self.write_ticket('inline/01', 'implementing', extra=f'allows: {value}\nallows_source: U3\n')
        allow('recall tool, latency 130')
        refused = self.refusal()
        assert refused == ("inline/01: measurement 'recall tool': recall is 83, worse than baseline 100 "
                           "(higher is better)\n"), refused
        entries[0]['paths'] = []
        (self.root / '.scratch/inline/measurements.json').write_text(json.dumps(entries))
        allow('recall 80,latency 130')
        accepted = self.cli('inline', '--accept', 'inline/01').stdout
        assert accepted == ("inline/01: measurement 'recall tool': recall is 83, worse than baseline 100; "
                            "allowed down to 80 by ledger row U3\n"
                            "inline/01: measurement 'latency tool': latency is 120, worse than baseline 100; "
                            "allowed up to 130 by ledger row U3\n"
                            "inline/01: report and branch carry the evidence; judge the content now\n"), accepted
        allow('latency 130, recall 85')
        refused = self.refusal()
        assert refused == ("inline/01: measurement 'recall tool': recall is 83, worse than baseline 100 "
                           "and past the bound 85 that ledger row U3 allows\n"), refused
        allow('recall 80')
        refused = self.refusal()
        assert refused == ("inline/01: measurement 'latency tool': latency is 120, worse than baseline 100 "
                           "(lower is better)\n"), refused
        entries[0]['paths'] = ['measure.sh']
        entries[0]['command'] = 'echo recall 100'
        (self.root / '.scratch/inline/measurements.json').write_text(json.dumps(entries))
        allow('latency tool, recall 80')
        refused = self.refusal()
        assert refused == ("inline/01: measurement 'recall tool': the ticket changed what measures recall (measure.sh); "
                           "that needs allows: recall tool citing a ledger row\n"), refused
        allow('latency 130, recall tool')
        accepted = self.cli('inline', '--accept', 'inline/01').stdout
        assert accepted == ("inline/01: measurement 'recall tool': tool change (measure.sh) allowed by ledger row U3\n"
                            "inline/01: measurement 'latency tool': latency is 120, worse than baseline 100; "
                            "allowed up to 130 by ledger row U3\n"
                            "inline/01: report and branch carry the evidence; judge the content now\n"), accepted

    def test_an_allowance_naming_a_metric_twice_or_holding_an_empty_or_malformed_pair_is_refused(self):
        self.write_ledger()
        self.measured_ticket('echo recall 100')
        for value in ('recall 80,', ',recall 80', 'recall 80,,latency 130', 'recall 80, ', 'recall 80, latency',
                      'recall 80 latency 130', 'recall 80; latency 130', 'recall 80, latency high', ','):
            self.write_ticket('inline/01', 'implementing', extra=f'allows: {value}\nallows_source: U3\n')
            assert self.refusal() == (f"inline/01: allows {value.strip()!r} must read `<metric> <number>` or "
                                      "`<metric> tool`\n"), (value, self.refusal())
        for value in ('recall 80, recall tool', 'recall 80, latency 130, recall 90'):
            self.write_ticket('inline/01', 'implementing', extra=f'allows: {value}\nallows_source: U3\n')
            assert self.refusal() == f"inline/01: allows {value!r} names recall more than once\n", \
                (value, self.refusal())

    def refusal(self, ticket='inline/01'):
        return self.cli('inline', '--accept', ticket, code=1).stderr

    def logless(self, refused):
        """A refusal without its `; log <path>)` pointer, after checking that the log it points to exists."""
        pointer = re.search(r'; log (\S+)\)', refused)
        assert pointer and Path(pointer[1]).is_file(), refused
        return refused.replace(pointer[0], ')')

    def test_an_allowance_without_a_citation_is_refused(self):
        # The ticket author cannot excuse a regression alone; the authority is a recorded ledger row.
        self.write_ledger()
        expected = "inline/01: allows: recall 80 needs allows_source naming a row of .scratch/inline/ledger.md\n"
        self.measured_ticket('echo recall 83', extra='allows: recall 80\n')
        assert self.refusal() == expected, self.refusal()
        self.write_ticket('inline/01', 'implementing', extra='allows: recall 80\nallows_source:\n')
        assert self.refusal() == expected, self.refusal()
        self.record_measurement('echo recall 100')  # an uncited allowance is refused even when no regression needs it
        assert self.refusal() == expected, self.refusal()

    def test_an_allowance_citing_a_row_the_ledger_does_not_have_is_refused(self):
        # Only a row id (the first cell of a ledger table row) is a citation: not a prefix, a header or a missing file.
        self.measured_ticket('echo recall 83', extra='allows: recall 80\nallows_source: U3\n')
        expected = "inline/01: allows_source {} is not a row of .scratch/inline/ledger.md\n"
        assert self.refusal() == expected.format('U3'), self.refusal()  # no ledger file at all
        self.write_ledger()
        self.cli('inline', '--accept', 'inline/01')
        for cited in ('U9', 'U', 'U1', 'U30', 'u3', '#', '---', 'Decision', 'A1)'):
            self.write_ticket('inline/01', 'implementing', extra=f'allows: recall 80\nallows_source: {cited}\n')
            assert self.refusal() == expected.format(cited), (cited, self.refusal())
        self.write_ticket('inline/01', 'implementing', extra='allows: recall 80\nallows_source: A1\n')
        self.cli('inline', '--accept', 'inline/01')

    def test_an_allowance_that_does_not_read_metric_then_numeric_bound_is_refused(self):
        self.write_ledger()
        self.measured_ticket('echo recall 100')
        for value in ('recall', 'recall high', 'recall 80 90', 'recall nan', ''):
            self.write_ticket('inline/01', 'implementing', extra=f'allows: {value}\nallows_source: U3\n')
            assert self.refusal() == (f"inline/01: allows {value!r} must read `<metric> <number>` or `<metric> tool`\n"), \
                (value, self.refusal())

    def test_an_effort_without_a_measurements_file_accepts_exactly_as_before(self):
        # No file, no gate: another effort's file and this ticket's own `allows` line change nothing here.
        self.measured_ticket('echo recall 1', extra='allows: recall 80\n')
        (self.root / '.scratch/inline/measurements.json').rename(self.root / '.scratch/inline/measurements.old')
        other = self.root / '.scratch/other/measurements.json'
        other.parent.mkdir(parents=True)
        other.write_text(json.dumps([{'name': 'x', 'command': 'exit 1', 'metric': 'recall',
                                      'baseline': 100, 'direction': 'higher'}]))
        accepted = self.cli('inline', '--accept', 'inline/01').stdout
        assert accepted == "inline/01: report and branch carry the evidence; judge the content now\n", accepted

    def test_a_measurement_that_outruns_its_timeout_is_killed_with_its_children_and_refused(self):
        # The shell is not the only process to stop: a child it started must not outlive the refusal.
        scratch = Path(tempfile.mkdtemp(prefix='zloop-pid-'))
        self.addCleanup(shutil.rmtree, scratch, True)
        pidfile = scratch / 'pid'
        self.measured_ticket(f'sleep 15 >/dev/null 2>&1 & echo $! > {shlex.quote(str(pidfile))}; wait', timeout_seconds=1)
        started = time.monotonic()
        refused = self.logless(self.refusal())
        assert time.monotonic() - started < 10, 'acceptance waited for the command'
        assert refused == ("inline/01: measurement 'recall tool': recall has no value "
                           "(its command timed out after 1 s); baseline 100\n"), refused
        child = int(pidfile.read_text())
        for _ in range(50):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        os.kill(child, signal.SIGKILL)
        self.fail(f'the timed-out measurement left its child {child} running')

    def test_a_timeout_that_is_not_a_positive_number_makes_the_measurements_file_unusable(self):
        self.measured_ticket('echo recall 100')
        for value in (0, -1, '5', True, float('nan'), float('inf')):
            self.record_measurement('echo recall 100', timeout_seconds=value)
            assert self.refusal() == ("inline/01: measurements.json is unusable: "
                                      "entry 1 needs timeout_seconds to be a positive number\n"), (value, self.refusal())
        self.record_measurement('echo recall 100', timeout_seconds=0.5)
        self.cli('inline', '--accept', 'inline/01')

    def test_a_measurement_without_timeout_seconds_is_not_cut_short(self):
        self.measured_ticket('sleep 2; echo recall 100')
        self.cli('inline', '--accept', 'inline/01')

    def test_a_ticket_worktree_with_uncommitted_changes_is_refused_naming_them(self):
        # The committed head regresses (83); an uncommitted edit would hide that, so the measurement would not be of the head.
        self.ticket_worktree(83)
        worktree = self.root / '.worktrees/inline-01'
        dirty = "inline/01: branch feat/inline-01 has uncommitted changes ({}); a measurement of it would not be of the ticket head\n"
        (worktree / 'score.txt').write_text('100\n')
        assert self.refusal() == dirty.format('score.txt'), self.refusal()
        self.git('-C', str(worktree), 'add', 'score.txt')
        assert self.refusal() == dirty.format('score.txt'), self.refusal()
        (worktree / 'notes.txt').write_text('scratch\n')
        assert self.refusal() == dirty.format('notes.txt, score.txt'), self.refusal()
        self.git('-C', str(worktree), 'reset', '-q', '--hard')
        assert self.refusal() == dirty.format('notes.txt'), self.refusal()
        (worktree / 'notes.txt').unlink()
        (worktree / 'build.log').write_text('ignored build output\n')
        assert self.refusal() == ("inline/01: measurement 'recall tool': recall is 83, worse than baseline 100 "
                                  "(higher is better)\n"), self.refusal()

    def test_a_main_checkout_holding_the_ticket_branch_with_a_stray_file_is_refused(self):
        self.measured_ticket('echo recall 100')
        self.cli('inline', '--accept', 'inline/01')
        (self.root / 'stray.txt').write_text('left behind\n')
        assert self.refusal() == ("inline/01: branch feat/inline has uncommitted changes (stray.txt); "
                                  "a measurement of it would not be of the ticket head\n"), self.refusal()

    def test_a_worktree_whose_git_status_cannot_be_read_is_refused(self):
        self.ticket_worktree(100)
        self.cli('inline', '--accept', 'inline/01')
        (self.root / '.worktrees/inline-01/.git').write_text('gitdir: /nonexistent\n')
        refused = self.refusal()
        assert refused.startswith("inline/01: branch feat/inline-01 has a worktree whose git status cannot be read: "), refused

    def test_a_metric_printed_on_more_than_one_line_is_refused_naming_it(self):
        # A later line must not quietly override or hide an earlier one.
        self.measured_ticket('true')
        for command in ("printf 'recall 100\\nrecall 83\\n'", "printf 'recall 100\\nrecall 100\\n'"):
            self.record_measurement(command)
            refused = self.logless(self.refusal())
            assert refused == ("inline/01: measurement 'recall tool': recall has no single value "
                               "(printed on 2 lines); baseline 100\n"), (command, refused)
        self.record_measurement("printf 'latency 5\\nlatency 6\\nrecall 100\\n'")
        self.cli('inline', '--accept', 'inline/01')

    def test_a_refusal_points_to_a_retained_log_holding_both_streams_and_each_run_keeps_its_own(self):
        self.measured_ticket('echo recall 100; echo diagnostic >&2; exit 3')
        head = self.git('rev-parse', 'feat/inline')
        evidence = Path(self.root).resolve() / '.scratch/inline/reports/evidence/01'
        refused = self.refusal()
        assert refused == (f"inline/01: measurement 'recall tool': recall has no value (its command exited 3; "
                           f"log {evidence}/accept-1/{head}/1.log); baseline 100\n"), refused
        assert (evidence / f'accept-1/{head}/1.log').read_text() == 'recall 100\ndiagnostic\n'
        self.refusal()
        assert (evidence / f'accept-1/{head}/1.log').is_file() and (evidence / f'accept-2/{head}/1.log').is_file()

    def test_a_measurement_cannot_read_the_callers_input(self):
        # The command would read 83 from the caller's stdin and report a regression; it must see no input.
        self.measured_ticket('read x; echo recall ${x:-100}')
        accepted = self.cli('inline', '--accept', 'inline/01', input='83\n').stdout
        assert 'carry the evidence' in accepted, accepted

    def test_output_that_is_not_utf8_cannot_crash_acceptance(self):
        self.measured_ticket("printf '\\377\\376 noise\\nrecall 100\\n'")
        self.cli('inline', '--accept', 'inline/01')
        self.record_measurement("printf '\\377\\376 noise\\nrecall 83\\n'")
        assert 'recall is 83, worse than baseline 100' in self.refusal()

    def test_a_child_that_escapes_the_process_group_and_holds_stdout_cannot_stall_acceptance(self):
        scratch = Path(tempfile.mkdtemp(prefix='zloop-pid-'))
        self.addCleanup(shutil.rmtree, scratch, True)
        pidfile = scratch / 'pid'
        child = ('import subprocess, sys; from pathlib import Path; '
                 'p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], start_new_session=True); '
                 f'Path({str(pidfile)!r}).write_text(str(p.pid))')
        spawn = f'{shlex.quote(sys.executable)} -c {shlex.quote(child)}'

        def stop_child():
            if pidfile.exists():
                try:
                    os.kill(int(pidfile.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.addCleanup(stop_child)
        self.measured_ticket(f'{spawn}; sleep 30', timeout_seconds=1)
        # Waiting for the escaped child would take its 20 s; 15 s leaves a loaded machine room without hiding that.
        started = time.monotonic()
        refused = self.refusal()
        assert time.monotonic() - started < 15, 'acceptance waited for the escaped child'
        assert self.logless(refused) == ("inline/01: measurement 'recall tool': recall has no value "
                                         "(its command timed out after 1 s); baseline 100\n"), refused
        # On a loaded machine the escaped child may record itself only after the 1 s timeout has passed.
        deadline = time.monotonic() + 20
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        stop_child()
        pidfile.unlink()
        self.record_measurement(f'{spawn}; echo recall 100')  # exits at once, its child still holds stdout
        started = time.monotonic()
        self.cli('inline', '--accept', 'inline/01')
        assert time.monotonic() - started < 15, 'acceptance waited for the escaped child'

    def test_a_command_that_leaves_a_process_behind_is_refused(self):
        self.measured_ticket('sleep 30 >/dev/null 2>&1 & echo recall 100')
        started = time.monotonic()
        refused = self.logless(self.refusal())
        assert time.monotonic() - started < 15, 'acceptance waited for the leftover process'
        assert refused == ("inline/01: measurement 'recall tool': recall has no value "
                           "(its command exited 125); baseline 100\n"), refused

    def test_evidence_that_cannot_be_kept_refuses_acceptance(self):
        self.measured_ticket('echo recall 100')
        reports = self.root / '.scratch/inline/reports'
        (reports / 'evidence').write_text('a file where the evidence directory goes\n')
        refused = self.refusal()
        assert refused.startswith("inline/01: cannot keep the measurement evidence: "), refused

    def test_a_ticket_that_changes_the_paths_its_measurement_lists_is_refused_naming_them(self):
        # A builder must not edit measure.sh or its fixtures to print the baseline; only files in base..head count.
        others = ['tools/fixtures/a.txt', 'tools2/b.txt', 'src.txt']
        self.measured_ticket('sh measure.sh', paths=['measure.sh'],
                             tracked={'measure.sh': 'echo recall 100\n', 'untouched.txt': 'x\n', 'moved.sh': 'same\n',
                                      **{n: 'x\n' for n in others}},
                             edits={'measure.sh': 'echo recall 100 # edited\n', 'moved.sh': None, 'elsewhere.sh': 'same\n',
                                    **{n: 'edited\n' for n in others}})
        refused = ("inline/01: measurement 'recall tool': the ticket changed what measures recall ({}); "
                   "that needs allows: recall tool citing a ledger row\n")
        for paths, touched in ((['measure.sh'], 'measure.sh'), (['tools'], 'tools/fixtures/a.txt'),
                               (['tools/'], 'tools/fixtures/a.txt'), (['tools2/b.txt'], 'tools2/b.txt'),
                               (['tools/fixtures/a.txt', 'measure.sh'], 'measure.sh, tools/fixtures/a.txt'),
                               (['moved.sh'], 'moved.sh'),  # a rename is a change of the old path too
                               (['./measure.sh'], 'measure.sh'), (['measure.sh/.'], 'measure.sh'),
                               (['tools//fixtures'], 'tools/fixtures/a.txt'),
                               (['./tools/./fixtures/.'], 'tools/fixtures/a.txt')):  # git's spelling is what matches
            self.record_measurement('sh measure.sh', paths=paths)
            assert self.refusal() == refused.format(touched), (paths, self.refusal())
        for paths in ([], ['untouched.txt'], ['too'], ['tool'], ['src']):   # a prefix of a name is not the path
            self.record_measurement('sh measure.sh', paths=paths)
            self.cli('inline', '--accept', 'inline/01')

    def test_a_cited_tool_allowance_lets_the_ticket_change_the_paths_of_its_metric_only(self):
        self.write_ledger()
        edits = {'measure.sh': 'echo recall 100 # edited\n'}
        self.measured_ticket('sh measure.sh', paths=['measure.sh'], tracked={'measure.sh': 'echo recall 100\n'}, edits=edits,
                             extra='allows: recall tool\nallows_source: U3\n')
        accepted = self.cli('inline', '--accept', 'inline/01').stdout
        assert accepted == ("inline/01: measurement 'recall tool': tool change (measure.sh) allowed by ledger row U3\n"
                            "inline/01: report and branch carry the evidence; judge the content now\n"), accepted
        refused = ("inline/01: measurement 'recall tool': the ticket changed what measures recall (measure.sh); "
                   "that needs allows: recall tool citing a ledger row\n")
        for allows in ('latency tool', 'recall 80'):   # another metric, or a value bound: neither covers the tool
            self.write_ticket('inline/01', 'implementing', extra=f'allows: {allows}\nallows_source: U3\n')
            assert self.refusal() == refused, (allows, self.refusal())
        self.write_ticket('inline/01', 'implementing', extra='allows: recall tool\n')
        assert 'needs allows_source' in self.refusal()
        self.write_ticket('inline/01', 'implementing', extra='allows: recall tool\nallows_source: U9\n')
        assert 'U9 is not a row' in self.refusal()

    def test_a_paths_list_that_could_never_match_a_changed_file_makes_the_measurements_file_unusable(self):
        self.measured_ticket('echo recall 100')
        for value in ('measure.sh', [''], [1], ['/etc/passwd'], ['../x'], ['a/../b'], [['x']], ['.'], ['./'], ['./../x'], ['tools/..']):
            self.record_measurement('echo recall 100', paths=value)
            assert self.refusal() == ("inline/01: measurements.json is unusable: entry 1 needs paths to be a list "
                                      "of relative paths inside the project\n"), (value, self.refusal())
        self.record_measurement('echo recall 100', paths=['tools/', 'measure.sh'])
        self.cli('inline', '--accept', 'inline/01')

    def forked_ticket(self, effort=True):
        """inline/01 in `.worktrees/inline-01`; its builder commits an edit of a listed path, then names that commit as `base:`.

        With `effort`, feat/inline already holds a prior ticket's edit of measure.sh and the ticket forks from it.
        """
        self.write_ticket('inline/01', 'implementing')
        self.write_files({'measure.sh': 'echo recall 100\n', 'tools/fixture.txt': 'x\n'})
        self.commit_gitignore()
        self.commit('base')
        if effort:
            self.git('switch', '-q', '-c', 'feat/inline')
            self.write_files({'measure.sh': 'echo recall 100 # an earlier ticket\n'})
            self.commit('prior ticket on the effort branch')
            self.git('switch', '-q', 'main')
        worktree = self.root / '.worktrees/inline-01'
        self.git('worktree', 'add', '-q', '-b', 'feat/inline-01', str(worktree), 'feat/inline' if effort else 'main')
        commit = ['-C', str(worktree), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty']
        (worktree / 'tools/fixture.txt').write_text('edited so that the tool reads differently\n')
        self.git('-C', str(worktree), 'add', 'tools/fixture.txt')
        self.git(*commit, '-m', 'the edit')
        moved = self.git('-C', str(worktree), 'rev-parse', 'HEAD')
        self.git(*commit, '-m', 'later work')
        work = self.git('-C', str(worktree), 'rev-parse', 'HEAD')
        self.record_measurement('sh measure.sh', paths=['measure.sh', 'tools/fixture.txt'])
        self.write_report(moved, 'feat/inline-01', [work])

    def test_a_moved_base_cannot_hide_a_tool_edit_and_earlier_effort_work_is_not_the_tickets_change(self):
        self.forked_ticket()
        assert self.refusal() == ("inline/01: measurement 'recall tool': the ticket changed what measures recall "
                                  "(tools/fixture.txt); that needs allows: recall tool citing a ledger row\n"), self.refusal()

    def test_without_an_effort_branch_the_merge_base_with_main_catches_a_moved_base(self):
        self.forked_ticket(effort=False)
        assert self.refusal() == ("inline/01: measurement 'recall tool': the ticket changed what measures recall "
                                  "(tools/fixture.txt); that needs allows: recall tool citing a ledger row\n"), self.refusal()

    def test_a_ticket_with_no_effort_branch_or_main_to_compare_with_is_refused(self):
        self.forked_ticket(effort=False)
        self.git('branch', '-m', 'main', 'trunk')
        assert self.refusal() == ("inline/01: no effort branch (feat/inline) or main exists to compare "
                                  "feat/inline-01 with\n"), self.refusal()

    def test_a_delegated_ticket_is_not_blamed_for_an_integrated_tool_change_it_merged_in(self):
        self.write_ticket('inline/01', 'implementing')
        self.write_files({'measure.sh': 'echo recall 100\n', 'src.txt': 'x\n'})
        self.commit_gitignore()
        self.commit('base')
        self.git('switch', '-q', '-c', 'feat/inline')
        fork = self.git('rev-parse', 'HEAD')
        worktree = self.root / '.worktrees/inline-01'
        self.git('worktree', 'add', '-q', '-b', 'feat/inline-01', str(worktree), 'feat/inline')
        self.write_files({'measure.sh': 'echo recall 100 # an earlier ticket, allowed and integrated\n'})
        self.commit('earlier ticket lands on the effort branch')
        self.git('switch', '-q', 'main')
        git = ['-C', str(worktree), '-c', 'user.name=t', '-c', 'user.email=t@t']
        self.git(*git, 'merge', '-q', '--no-edit', 'feat/inline')   # the merge dispatch makes before a restart
        (worktree / 'src.txt').write_text('the ticket work\n')
        self.git(*git, 'commit', '-q', '-am', 'ticket work')
        self.record_measurement('sh measure.sh', paths=['measure.sh'])
        self.write_report(fork, 'feat/inline-01', [self.git('-C', str(worktree), 'rev-parse', 'HEAD')])
        assert 'carry the evidence' in self.cli('inline', '--accept', 'inline/01').stdout

    def test_an_inline_ticket_is_compared_from_its_recorded_start_not_from_the_reports_base(self):
        # Rule (T12.2): on the effort branch itself the diff runs from the `start:` recorded when the ticket moved into
        # implementing; a base: moved past a tool edit hides nothing, and an earlier ticket's edit is not this one's.
        self.write_ticket('inline/01')
        self.write_files({'measure.sh': 'echo recall 100\n', 'src.txt': 'x\n'})
        self.commit_gitignore()
        base = self.commit('base')
        self.git('switch', '-q', '-c', 'feat/inline')
        self.write_files({'measure.sh': 'echo recall 100 # an earlier ticket\n'})
        self.commit('an earlier ticket on the effort branch')
        self.cli('inline', '--set', 'inline/01', 'status=implementing')
        self.write_files({'src.txt': 'the ticket work\n'})
        work = self.commit('ticket work')
        self.record_measurement('sh measure.sh', paths=['measure.sh'])
        self.write_report(base, 'feat/inline', [work])
        accepted = self.cli('inline', '--accept', 'inline/01').stdout
        assert accepted == "inline/01: report and branch carry the evidence; judge the content now\n", accepted
        self.write_files({'measure.sh': 'echo recall 100 # this ticket\n'})
        moved = self.commit('the ticket edits the tool')
        self.write_report(moved, 'feat/inline', [self.commit('later work')])
        assert self.refusal() == ("inline/01: measurement 'recall tool': the ticket changed what measures recall "
                                  "(measure.sh); that needs allows: recall tool citing a ledger row\n"), self.refusal()

    def test_an_inline_ticket_without_a_start_on_its_branch_is_refused_naming_the_fix(self):
        # Rule (T12.2): no `start:`, or one the effort branch does not contain, leaves nothing to compare from.
        self.write_ticket('inline/01', 'implementing')
        self.commit_gitignore()
        base = self.commit('base')
        self.git('switch', '-q', '-c', 'side')
        aside = self.commit('never on the effort branch')
        self.git('switch', '-q', '-c', 'feat/inline', base)
        self.record_measurement('echo recall 100', paths=['measure.sh'])
        self.write_report(base, 'feat/inline', [self.commit('ticket work')])
        assert self.refusal() == ("inline/01: an inline ticket is compared from its start:, and it has none; record "
                                  "the effort branch commit it started from with --set inline/01 start=<sha>\n"), \
            self.refusal()
        for start in (aside, 'abc', '-h', 'f' * 40):
            self.write_ticket('inline/01', 'implementing', extra=f'start: {start}\n')
            assert self.refusal() == (f"inline/01: start: {start} is not an ancestor of feat/inline; delete that line "
                                      "from the ticket, then record the effort branch commit it started from with "
                                      "--set inline/01 start=<sha>\n"), (start, self.refusal())
        self.write_ticket('inline/01', 'implementing', extra=f'start: {base}\n')
        self.cli('inline', '--accept', 'inline/01')

    def test_an_inline_ticket_lists_a_commit_past_its_start(self):
        # Review 1 of T12.2: a start recorded after the ticket's own commits would hide them, so --accept needs a commit
        # listed under ## Implemented that is past start on the effort branch.
        self.write_ticket('inline/01')
        self.write_files({'measure.sh': 'echo recall 100\n'})
        self.commit_gitignore()
        base = self.commit('base')
        self.git('switch', '-q', '-c', 'feat/inline')
        self.cli('inline', '--set', 'inline/01', 'status=implementing')
        self.write_files({'measure.sh': 'echo recall 100 # this ticket\n'})
        work = self.commit('the ticket edits the tool')
        self.record_measurement('sh measure.sh', paths=['measure.sh'])
        self.write_report(base, 'feat/inline', [work])
        assert self.refusal() == ("inline/01: measurement 'recall tool': the ticket changed what measures recall "
                                  "(measure.sh); that needs allows: recall tool citing a ledger row\n"), self.refusal()
        path = self.root / '.scratch/inline/issues/01-task.md'
        path.write_text(re.sub(r'^start: .*$', f'start: {work}', path.read_text(), flags=re.M))
        hidden = (f"inline/01: no commit listed under ## Implemented is past start: {work} on feat/inline; a start "
                  "recorded after the ticket's commits would hide them\n")
        assert self.refusal() == hidden, self.refusal()
        later = self.commit('later work, not listed')
        assert self.refusal() == hidden, self.refusal()
        self.write_report(base, 'feat/inline', [work, later])
        self.cli('inline', '--accept', 'inline/01')

    def stacked_tickets(self):
        """inline/01 (A) edits measure.sh on its branch; inline/02 (B), blocked by A, forks from A's head and edits src.txt.

        Both forks start from feat/inline, which holds neither change. Returns (A's worktree, B's worktree).
        """
        self.write_ticket('inline/01', 'implementing')
        self.write_ticket('inline/02', 'implementing', touches='docs', blocked='inline/01')
        self.write_files({'measure.sh': 'echo recall 100\n', 'src.txt': 'x\n'})
        self.commit_gitignore()
        self.commit('base')
        self.git('branch', 'feat/inline')
        fork = self.git('rev-parse', 'HEAD')
        a, b = self.root / '.worktrees/inline-01', self.root / '.worktrees/inline-02'
        self.git('worktree', 'add', '-q', '-b', 'feat/inline-01', str(a), 'feat/inline')
        self.worktree_commit(a, {'measure.sh': 'echo recall 100 # A changes the tool\n'}, 'A: tool change')
        self.write_report(fork, 'feat/inline-01', [self.git('-C', str(a), 'rev-parse', 'HEAD')])
        self.git('worktree', 'add', '-q', '-b', 'feat/inline-02', str(b), 'feat/inline-01')
        a_head = self.git('-C', str(a), 'rev-parse', 'HEAD')
        self.worktree_commit(b, {'src.txt': 'B work\n'}, 'B: its own work')
        self.write_report(a_head, 'feat/inline-02', [self.git('-C', str(b), 'rev-parse', 'HEAD')], number='02')
        self.record_measurement('sh measure.sh', paths=['measure.sh'])
        return a, b

    def worktree_commit(self, worktree, files, message):
        for name, text in files.items():
            (worktree / name).write_text(text)
        self.git('-C', str(worktree), 'add', *files)
        self.git('-C', str(worktree), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', message)
        return self.git('-C', str(worktree), 'rev-parse', 'HEAD')

    def test_a_stacked_ticket_is_not_blamed_for_its_dependencys_tool_change_but_is_for_its_own(self):
        # Rule (T12.1): B's change is what differs at B's head from both its fork with the effort branch and the head of
        # each same-effort blocked_by ticket that B's head contains; A's tool change is judged at A's own --accept.
        a, b = self.stacked_tickets()
        blamed = ("inline/{}: measurement 'recall tool': the ticket changed what measures recall (measure.sh); "
                  "that needs allows: recall tool citing a ledger row\n")
        accepted = self.cli('inline', '--accept', 'inline/02').stdout
        assert accepted == "inline/02: report and branch carry the evidence; judge the content now\n", accepted
        assert self.refusal('inline/01') == blamed.format('01'), self.refusal('inline/01')
        # A's branch is the one A's report names; without that report it is feat/inline-01.
        self.git('branch', '-m', 'feat/inline-01', 'feat/stack-a')
        assert self.refusal('inline/02') == blamed.format('02'), self.refusal('inline/02')
        a_head = self.git('rev-parse', 'feat/stack-a')
        self.write_report(self.git('rev-parse', 'feat/inline'), 'feat/stack-a', [a_head])
        self.cli('inline', '--accept', 'inline/02')
        (self.root / '.scratch/inline/reports/01.md').unlink()
        assert self.refusal('inline/02') == blamed.format('02'), self.refusal('inline/02')
        self.git('branch', '-m', 'feat/stack-a', 'feat/inline-01')
        self.cli('inline', '--accept', 'inline/02')
        # A dependency head is a commit B's head strictly contains: a report naming B's own branch excuses nothing.
        self.write_report(self.git('rev-parse', 'feat/inline'), 'feat/inline-02', [a_head])
        assert self.refusal('inline/02') == blamed.format('02'), self.refusal('inline/02')
        self.write_report(self.git('rev-parse', 'feat/inline'), 'feat/inline-01', [a_head])
        # Only a blocked_by ticket of the same effort counts.
        self.write_ticket('other/01', 'done', touches='other')
        for blocked in ('', 'other/01'):
            self.write_ticket('inline/02', 'implementing', touches='docs', blocked=blocked)
            assert self.refusal('inline/02') == blamed.format('02'), (blocked, self.refusal('inline/02'))
        self.write_ticket('inline/02', 'implementing', touches='docs', blocked='inline/01')
        self.cli('inline', '--accept', 'inline/02')
        # A's head must be an ancestor of B's head: once A moves on, B is blamed again.
        self.worktree_commit(a, {'src.txt': 'A moves on\n'}, 'A: later work')
        assert self.refusal('inline/02') == blamed.format('02'), self.refusal('inline/02')
        self.git('-C', str(a), 'reset', '-q', '--hard', 'HEAD~1')
        self.cli('inline', '--accept', 'inline/02')
        # A file B changes again after A is still B's change (its builder returns its report at that head).
        self.worktree_commit(b, {'measure.sh': 'echo recall 100 # B changes it again\n'}, 'B: tool change')
        support.collected_attempt(self.root, 'inline', 'inline/02/implementation-final',
                                  self.root / '.scratch/inline/reports/02.md',
                                  head=self.git('rev-parse', 'feat/inline-02'))
        assert self.refusal('inline/02') == blamed.format('02'), self.refusal('inline/02')

    def test_a_dependency_head_the_fork_already_contains_excuses_no_revert(self):
        # Review 1 of T12.1: a blocked_by head the effort branch already holds (integrated and never deleted, or a
        # report naming main) is not a stacked base; B reverting the tool to that head's version is still B's change.
        self.write_ticket('inline/01', 'done')
        self.write_ticket('inline/02', 'implementing', touches='docs', blocked='inline/01')
        self.write_files({'measure.sh': 'echo recall 100 # v1\n'})
        self.commit_gitignore()
        base = self.commit('base')
        self.git('branch', 'feat/inline-01')   # A's branch, integrated long ago and never deleted
        self.git('switch', '-q', '-c', 'feat/inline')
        self.write_files({'measure.sh': 'echo recall 100 # v2, an approved tool change\n'})
        fork = self.commit('an approved tool change on the effort branch')
        self.git('switch', '-q', 'main')
        b = self.root / '.worktrees/inline-02'
        self.git('worktree', 'add', '-q', '-b', 'feat/inline-02', str(b), 'feat/inline')
        head = self.worktree_commit(b, {'measure.sh': 'echo recall 100 # v1\n'}, 'B: reverts the tool')
        self.write_report(fork, 'feat/inline-02', [head], number='02')
        self.record_measurement('sh measure.sh', paths=['measure.sh'])
        blamed = ("inline/02: measurement 'recall tool': the ticket changed what measures recall (measure.sh); "
                  "that needs allows: recall tool citing a ledger row\n")
        assert self.refusal('inline/02') == blamed, self.refusal('inline/02')
        self.git('branch', '-D', 'feat/inline-01')
        self.write_report(base, 'main', [base])   # A's report names main
        assert self.refusal('inline/02') == blamed, self.refusal('inline/02')

    def test_a_measurement_that_leaves_the_worktree_dirty_is_refused_naming_the_files(self):
        # The committed head scores 83; a command that rewrites score.txt to 100 measured a tree that is not the head.
        self.ticket_worktree(83)
        left = ("inline/01: a measurement left uncommitted changes in branch feat/inline-01 ({}); "
                "declare its outputs in .gitignore\n")
        for command, names in (('echo 100 > score.txt; echo recall $(cat score.txt)', 'score.txt'),
                               ('echo out > result.txt; echo recall 100', 'result.txt'),
                               ('echo 100 > score.txt; echo out > result.txt; echo recall 100', 'result.txt, score.txt')):
            self.record_measurement(command)
            refused = self.refusal()
            assert refused == left.format(names), (command, refused)
            self.git('-C', str(self.root / '.worktrees/inline-01'), 'checkout', '-q', '--', 'score.txt')
            (self.root / '.worktrees/inline-01/result.txt').unlink(missing_ok=True)
        self.record_measurement('echo out > build.log; echo recall 100')   # ignored output leaves the tree as it was
        self.cli('inline', '--accept', 'inline/01')
        ran = self.root / 'second-entry-ran'
        entries = [{'name': 'first', 'command': 'echo out > result.txt; echo recall 100', 'metric': 'recall',
                    'baseline': 100, 'direction': 'higher'},
                   {'name': 'second', 'command': f'touch {shlex.quote(str(ran))}; echo latency 1', 'metric': 'latency',
                    'baseline': 5, 'direction': 'lower'}]
        (self.root / '.scratch/inline/measurements.json').write_text(json.dumps(entries))
        refused = self.refusal()
        assert refused == left.format('result.txt'), refused
        assert not ran.exists(), 'a later measurement ran on the dirtied tree'

    def ticket_worktree(self, score):
        """inline/01 on feat/inline-01 in its own worktree; main keeps scoring 100 and the ticket branch scores `score`."""
        self.write_ticket('inline/01', 'implementing')
        (self.root / 'score.txt').write_text('100\n')
        self.git('add', 'score.txt')
        self.commit_gitignore()
        base = self.commit('base with the recall score')
        worktree = self.root / '.worktrees/inline-01'
        self.git('worktree', 'add', '-q', '-b', 'feat/inline-01', str(worktree), base)
        (worktree / 'score.txt').write_text(f'{score}\n')
        self.git('-C', str(worktree), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-am', 'ticket work')
        work = self.git('-C', str(worktree), 'rev-parse', 'HEAD')
        self.record_measurement('echo recall $(cat score.txt)')
        self.write_report(base, 'feat/inline-01', [work])

    def test_the_measurement_runs_in_the_worktree_that_holds_the_ticket_head(self):
        # Main still scores 100; only the ticket worktree, at the ticket head, scores 83.
        self.ticket_worktree(83)
        refused = self.cli('inline', '--accept', 'inline/01', code=1).stderr
        assert refused == ("inline/01: measurement 'recall tool': recall is 83, worse than baseline 100 "
                           "(higher is better)\n"), refused

    def test_a_ticket_branch_checked_out_nowhere_cannot_be_measured_and_is_refused(self):
        self.ticket_worktree(100)
        self.cli('inline', '--accept', 'inline/01')
        self.git('worktree', 'remove', '--force', '.worktrees/inline-01')
        refused = self.cli('inline', '--accept', 'inline/01', code=1).stderr
        assert refused == "inline/01: branch feat/inline-01 is checked out in no worktree; its head cannot be measured\n", refused

    def test_a_recorded_branch_must_name_an_existing_branch(self):
        self.write_ticket('inline/01', 'implementing')
        base = self.commit('base')
        self.git('switch', '-q', '-c', 'feat/inline')
        work = self.commit('ticket work')
        for value in ('HEAD', work, 'feat/inline~1', '-h', 'feat/missing'):
            self.write_report(base, value, [work])
            assert 'not an existing branch' in self.cli('inline', '--accept', 'inline/01', code=1).stderr, value
        for name in ('feat/x+01', '_effort', 'feat/v1.2'):
            self.git('branch', name)
            self.write_report(base, name, [work])
            self.cli('inline', '--accept', 'inline/01')

    def deliver(self, slug, branch, start, head, workers=True):
        """The effort's review report, clean and passed at head, as its reviewer and E2E worker returned it (two
        collected attempts that review_by and e2e_by name) unless workers=False."""
        review = self.root / f'.scratch/{slug}/reports/spec-review.md'
        review.parent.mkdir(parents=True, exist_ok=True)
        self.rounds = getattr(self, 'rounds', {})
        self.rounds[slug] = n = self.rounds.get(slug, 0) + 1
        evidence = (f'---\neffort_branch: {branch}\nreview_status: clean\ndocumentation_status: passed\n'
                    f'review_head: {head}\nreview_base: {start}\nreview_diff_base: {start}\n'
                    f'e2e_status: passed\ne2e_head: {head}\n'
                    f'review_by: review/{slug}/{n}\ne2e_by: e2e/{slug}/{n}\n---\n')
        review.write_text(evidence)
        if workers:
            support.collected_attempt(self.root, slug, f'review/{slug}/{n}', review, job='diff-review', head=head)
            support.collected_attempt(self.root, slug, f'e2e/{slug}/{n}', review, job='e2e-gate', head=head)
        return review, evidence

    def ready_names(self, slug):
        return [t['ticket'] for t in json.loads(self.cli(slug).stdout)]

    def test_an_effort_run_on_main_is_delivered_from_main_and_releases_its_dependents(self):
        self.write_ticket('onmain/01', 'done')
        self.write_ticket('later/01', blocked='onmain/01', touches='docs')
        self.write_ticket('later/02', blocked='onmain/01', touches='tests')
        start = self.commit('start')
        head = self.commit('ticket work on main')
        review, evidence = self.deliver('onmain', 'main', start, head)
        self.cli('onmain', '--delivery')
        assert self.ready_names('later') == ['later/01', 'later/02']
        self.git('branch', 'feat/later', start)
        self.deliver('later', 'main', start, start)
        self.commit('later/01 lands on main')
        self.cli('onmain', '--delivery')
        assert self.ready_names('later') == ['later/01', 'later/02']
        review.write_text(evidence.replace('effort_branch: main\n', ''))
        self.cli('onmain', '--delivery', code=1)
        assert self.ready_names('later') == []
        review.write_text(evidence.replace('effort_branch: main', 'effort_branch: main~1'))
        self.cli('onmain', '--delivery', code=1)
        review.write_text(evidence)
        later_review, later_evidence = self.deliver('later', 'main', start, start)
        later_review.write_text(later_evidence.replace('effort_branch: main', 'effort_branch: main~1'))
        assert self.ready_names('later') == []
        later_review.write_text(later_evidence)
        unreviewed = review.read_text().replace('effort_branch: main~1', 'effort_branch: main')
        review.write_text(unreviewed.replace(head, start))
        self.cli('onmain', '--delivery', code=1)

    def merged_on_remote(self, slug, on_remote=True):
        """An effort whose branch is deleted, reviewed and E2E passed at the head of feat/<slug>, which a bare remote's
        main holds (when on_remote) while local main stays at the start and has never fetched."""
        self.write_ticket(f'{slug}/01', 'done')
        start = self.commit('start')
        self.git('switch', '-q', '-c', f'feat/{slug}')
        head = self.commit('effort work')
        self.git('switch', '-q', 'main')
        remote = self.root.parent / f'{self.root.name}-remote.git'
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(remote)], check=True)
        self.addCleanup(shutil.rmtree, remote, ignore_errors=True)
        self.git('remote', 'add', 'origin', str(remote))
        self.git('push', '-q', 'origin', f'{head if on_remote else start}:refs/heads/main')
        self.deliver(slug, f'feat/{slug}', start, head)
        self.git('branch', '-D', f'feat/{slug}')
        return start, head

    def test_an_effort_merged_on_the_remote_is_finished_though_local_main_is_stale(self):
        # Rule: finished means the branch is gone and the reviewed and E2E heads are in the remote's main, even when
        # local main has not been updated from it.
        start, head = self.merged_on_remote('fx')
        assert self.git('rev-parse', 'main') == start
        with patch.object(nt, 'ROOT', self.root), patch.object(nt, 'ISSUES', self.root / '.scratch'):
            assert nt.finished('fx') is True

    def test_delivery_of_a_deleted_branch_whose_heads_are_in_no_main_says_to_update_local_main(self):
        # Rule: with the branch gone and the reviewed heads in neither local nor the remote's main, delivery says to
        # update local main from the remote, and never suggests making the branch again.
        self.merged_on_remote('fx', on_remote=False)
        refused = self.cli('fx', '--delivery', code=1).stderr
        assert refused == (f'fx: effort branch feat/fx is not an existing branch, and its review_head and e2e_head are '
                           f'in no main; update local main from the remote (`git -C {self.root.resolve()} fetch origin main`, '
                           f'then fast-forward main to origin/main)\n'), refused
        assert 'recreat' not in refused and 'git branch' not in refused, refused

    def test_delivery_names_each_field_that_fails(self):
        # Rule 2 (the Pi run): a repair committed after review leaves review_head stale against the effort head, and
        # E2E never ran; the gate names each failing field, not only the effort.
        self.write_ticket('fx/01', 'done')
        start = self.commit('start')
        self.git('switch', '-q', '-c', 'feat/fx')
        reviewed = self.commit('reviewed work')
        repaired = self.commit('repair after review')
        self.git('switch', '-q', 'main')
        review, evidence = self.deliver('fx', 'feat/fx', start, reviewed)
        review.write_text(evidence.replace('e2e_status: passed', 'e2e_status: not-run')
                          .replace(f'e2e_head: {reviewed}', 'e2e_head: none')
                          .replace('e2e_by: e2e/fx/1', 'e2e_by: none'))
        refused = self.cli('fx', '--delivery', code=1).stderr
        assert sorted(refused.splitlines()) == sorted([
            f'fx: review_head: {reviewed} is stale against feat/fx head {repaired}',
            'fx: e2e_status: not-run (needs passed)',
            f'fx: e2e_head: none is stale against feat/fx head {repaired}',
            'fx: e2e_by: none names no worker run']), refused

    def test_delivery_takes_each_status_from_the_worker_that_returned_it(self):
        # jose retro: the orchestrator rewrote the review report as clean, and recorded e2e_status passed crediting a
        # worker whose own report said failed.
        self.write_ticket('fx/01', 'done')
        start = self.commit('start')
        self.git('switch', '-q', '-c', 'feat/fx')
        head = self.commit('reviewed work')
        self.git('switch', '-q', 'main')
        review, evidence = self.deliver('fx', 'feat/fx', start, head, workers=False)
        refused = self.cli('fx', '--delivery', code=1).stderr
        assert 'fx: review_by: review/fx/1 names no collected worker attempt' in refused, refused
        assert 'fx: e2e_by: e2e/fx/1 names no collected worker attempt or check run' in refused, refused
        # The reviewer and the E2E worker each returned their own report, with their own verdicts.
        reviewer = review.with_name('review-1.md')
        reviewer.write_text(evidence.replace('review_status: clean', 'review_status: findings'))
        support.collected_attempt(self.root, 'fx', 'review/fx/1', reviewer, job='diff-review', head=head)
        verifier = review.with_name('e2e-1.md')
        verifier.write_text(evidence.replace('e2e_status: passed', 'e2e_status: failed'))
        support.collected_attempt(self.root, 'fx', 'e2e/fx/1', verifier, job='e2e-gate', head=head)
        refused = self.cli('fx', '--delivery', code=1).stderr
        assert sorted(refused.splitlines()) == sorted([
            'fx: review_by: review/fx/1 returned review_status: findings, not clean',
            'fx: e2e_by: e2e/fx/1 returned e2e_status: failed, not passed']), refused
        # Rewriting those reports afterwards does not change what their workers returned.
        reviewer.write_text(evidence)
        verifier.write_text(evidence)
        assert self.cli('fx', '--delivery', code=1).stderr == refused, refused
        # Their verdicts are taken as they returned them: a report edited afterwards does not count.
        reviewer.write_text(evidence)
        verifier.write_text(evidence)
        support.collected_attempt(self.root, 'fx', 'review/fx/2', reviewer, job='diff-review', head=head)
        support.collected_attempt(self.root, 'fx', 'e2e/fx/2', verifier, job='e2e-gate', head=head)
        review.write_text(evidence.replace('review/fx/1', 'review/fx/2').replace('e2e/fx/1', 'e2e/fx/2'))
        self.cli('fx', '--delivery')
        reviewer.write_text(evidence.replace(f'review_head: {head}', f'review_head: {start}'))
        refused = self.cli('fx', '--delivery', code=1).stderr
        assert refused == f'fx: review_by: review/fx/2 report now says review_head: {start}, not {head}\n', refused
        # Several workers may share a gate, a review split by axis or E2E by step: each must have returned it met.
        reviewer.write_text(evidence)
        split = review.with_name('review-axis.md')
        split.write_text(evidence.replace('review_status: clean', 'review_status: findings'))
        support.collected_attempt(self.root, 'fx', 'review/fx/axis', split, job='diff-review', head=head)
        review.write_text(evidence.replace('review_by: review/fx/1', 'review_by: [review/fx/2, review/fx/axis]')
                          .replace('e2e/fx/1', 'e2e/fx/2'))
        refused = self.cli('fx', '--delivery', code=1).stderr
        assert refused == 'fx: review_by: review/fx/axis returned review_status: findings, not clean\n', refused
        split.write_text(evidence)
        support.collected_attempt(self.root, 'fx', 'review/fx/axis-2', split, job='diff-review', head=head)
        review.write_text(evidence.replace('review_by: review/fx/1', 'review_by: [review/fx/2, review/fx/axis-2]')
                          .replace('e2e/fx/1', 'e2e/fx/2'))
        self.cli('fx', '--delivery')

    def test_e2e_the_orchestrator_ran_itself_counts_only_as_passing_checks_at_the_head(self):
        self.write_ticket('fx/01', 'done')
        self.commit_gitignore()
        start = self.commit('start')
        self.git('switch', '-q', '-c', 'feat/fx')
        head = self.commit('reviewed work')
        logs = Path(tempfile.mkdtemp(prefix='zloop-checks-'))
        self.addCleanup(shutil.rmtree, logs)

        def check(name, code):
            log = logs / f'{name}.log'
            subprocess.run([sys.executable, '-B', str(support.script('run_check.py')), '--timeout', '20', '--log',
                            str(log), '--', sys.executable, '-c', f'exit({code})'], cwd=self.root,
                           capture_output=True, check=False)
            return log
        suite, lint, failing = check('suite', 0), check('checks with spaces', 0), check('failing', 4)
        self.git('switch', '-q', 'main')
        earlier = check('earlier', 0)
        review, evidence = self.deliver('fx', 'feat/fx', start, head)
        run_by = evidence.replace('e2e_by: e2e/fx/1', f'e2e_by: [{suite}, {lint}]')
        review.write_text(evidence.replace('e2e_by: e2e/fx/1', f'e2e_by: {lint}'))
        self.cli('fx', '--delivery')
        review.write_text(run_by)
        self.cli('fx', '--delivery')
        review.write_text(run_by.replace(str(lint), str(failing)))
        assert self.cli('fx', '--delivery', code=1).stderr == f'fx: e2e_by: {failing} exited 4\n'
        review.write_text(run_by.replace(str(lint), str(earlier)))
        assert self.cli('fx', '--delivery', code=1).stderr == f'fx: e2e_by: {earlier} ran at {start}, not {head}\n'
        review.write_text(run_by.replace(str(lint), str(logs / 'missing.log')))
        refused = self.cli('fx', '--delivery', code=1).stderr
        missing = logs / 'missing.log'
        assert refused == f'fx: e2e_by: {missing} names no collected worker attempt or check run\n', refused

    def test_delivery_names_the_tickets_that_are_not_done(self):
        # Rule 2: with current review and E2E evidence, an unfinished ticket alone fails the gate, and it is named.
        self.write_ticket('fx/01', 'done')
        self.write_ticket('fx/02', 'implementing', touches='docs')
        self.write_ticket('fx/03', 'blocked', touches='tests')
        start = self.commit('start')
        self.git('switch', '-q', '-c', 'feat/fx')
        head = self.commit('reviewed work')
        self.git('switch', '-q', 'main')
        self.deliver('fx', 'feat/fx', start, head)
        refused = self.cli('fx', '--delivery', code=1).stderr
        assert refused == 'fx: tickets not done: fx/02 (implementing), fx/03 (blocked)\n', refused

if __name__ == '__main__':
    unittest.main()
