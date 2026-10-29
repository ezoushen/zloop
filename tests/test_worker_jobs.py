#!/usr/bin/env python3
"""A registered attempt carries its agent and job: independence, required report sections, read-only work."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import unittest

from lifecycle_case import WorkerLifecycleCase
import support


class Jobs(WorkerLifecycleCase):
    def use(self, agent, job):
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': agent, 'job': job,
                                             'settings': {'model': 'sonnet', 'effort': 'high'}}))

    def checkout(self, files=None):
        """A Git checkout of one commit, kept apart from the files the fixture writes under the root."""
        self.worktree = self.root / 'checkout'
        self.worktree.mkdir()
        self.git('init', '-q')
        return self.commit('base', files or {'code.py': 'x = 1\n'})

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.worktree), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message, files):
        for name, content in files.items():
            (self.worktree / name).write_text(content)
        self.git('add', '-A')
        self.git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', message)
        return self.git('rev-parse', 'HEAD')

    def review(self, head, attempt='review-1/1', session='native-1'):
        """Register and ready a read-only reviewer attempt in the checkout, and write its report."""
        self.use('reviewer', 'diff-review')
        self.record('register', attempt, assignment=attempt.split('/')[0], repo=str(self.worktree),
                    worktree=str(self.worktree), head=head, execution=str(self.execution), runtime='claude',
                    worker='pane-' + session, native_session=session, owned=True, packet=str(self.packet(attempt)))
        self.ready(attempt)
        report = self.root / 'review.md'
        report.write_text('## Coverage\n\n## Review round 1\n')
        return report

    def test_an_untracked_file_a_read_only_agent_edits_is_a_change(self):
        head = self.checkout()
        (self.worktree / 'before.txt').write_text('there before the agent started\n')
        report = self.review(head)
        (self.worktree / 'before.txt').write_text('rewritten by the agent\n')
        self.completed('review-1/1', status='completed', head=head, report=str(report), evidence=['review.md'])
        gate = self.cli('gate', '--attempt', 'review-1/1', '--stage', 'accept', '--revision', head, code=1)
        self.assertEqual(gate['missing'],
                         ['unchanged worktree for a read-only agent (an untracked file changed: before.txt)'])

    def test_an_untracked_file_a_read_only_agent_deletes_is_a_change(self):
        head = self.checkout()
        (self.worktree / 'before.txt').write_text('there before the agent started\n')
        report = self.review(head)
        (self.worktree / 'before.txt').unlink()
        self.completed('review-1/1', status='completed', head=head, report=str(report), evidence=['review.md'])
        gate = self.cli('gate', '--attempt', 'review-1/1', '--stage', 'accept', '--revision', head, code=1)
        self.assertEqual(gate['missing'],
                         ['unchanged worktree for a read-only agent (an untracked file was deleted: before.txt)'])

    def test_untracked_symlinks_and_a_nested_repository_are_held_to_their_registered_state(self):
        head = self.checkout()
        (self.worktree / 'target').mkdir()
        (self.worktree / 'link').symlink_to('target')
        (self.worktree / 'dangling').symlink_to('absent')
        self.git('init', '-q', 'nested')
        report = self.review(head)
        self.completed('review-1/1', status='completed', head=head, report=str(report), evidence=['review.md'])
        self.assertTrue(self.cli('gate', '--attempt', 'review-1/1', '--stage', 'accept', '--revision', head)['complete'])

    def forget_untracked(self, attempt):
        """Make a registration read as one made before the snapshot listed untracked files."""
        with sqlite3.connect(self.state) as db:
            data = json.loads(db.execute('SELECT data FROM attempts WHERE id = ?', (attempt,)).fetchone()[0])
            del data['worktree_baseline']['untracked']
            db.execute('UPDATE attempts SET data = ? WHERE id = ?', (json.dumps(data), attempt))

    def fake_git(self, vanish=None, extra=None, probe_lock=False):
        """A `git` ahead of the real one for the CLI: it logs each call, and after `ls-files --others` deletes
        `vanish` (a file gone between its listing and its hashing) and adds the raw name `extra` to the listing.
        With `probe_lock`, each `ls-files` log line says whether the state database's write lock was free."""
        directory = self.root / 'bin'
        directory.mkdir(exist_ok=True)
        self.git_log = self.root / 'git.log'
        executable = directory / 'git'
        executable.write_text('#!' + sys.executable + '\n' + f'''
import os, sqlite3, subprocess, sys
vanish, extra, probe = {str(vanish) if vanish else None!r}, {extra!r}, {str(self.state) if probe_lock else None!r}
lock = ''
if probe and 'ls-files' in sys.argv:
    try:
        db = sqlite3.connect(probe, timeout=0, isolation_level=None)
        db.execute('BEGIN IMMEDIATE')
        db.execute('ROLLBACK')
        lock = ' [write lock free]'
    except sqlite3.OperationalError:
        lock = ' [write lock held]'
with open({str(self.git_log)!r}, 'a') as log:
    log.write(' '.join(sys.argv[1:]) + lock + chr(10))
process = subprocess.run([{shutil.which('git')!r}, *sys.argv[1:]], capture_output=True)
if 'ls-files' in sys.argv and '--others' in sys.argv:
    if vanish and os.path.lexists(vanish):
        os.unlink(vanish)
    if extra is not None:
        process.stdout += extra + bytes(1)
sys.stdout.buffer.write(process.stdout)
sys.stderr.buffer.write(process.stderr)
sys.exit(process.returncode)
''')
        executable.chmod(0o700)
        self.native_env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'])

    def collect_request(self, head, report, attempt='review-1/1'):
        request = self.root / 'collect.json'
        request.write_text(json.dumps({
            'status': 'completed', 'head': head, 'report': str(report), 'evidence': ['review.md'],
            **self.completion_evidence(attempt),
            'disposition': {'kind': 'retain', 'reason': 'recheck', 'next_use': 'recheck', 'evidence': ['review.md']}}))
        return str(request)

    def test_registration_needs_a_known_job_done_by_its_agent(self):
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': 'builder',
                                             'settings': {'model': 'sonnet', 'effort': 'high'}}))
        self.assertIn('known job', self.register(code=2))
        self.use('reviewer', 'ticket')
        self.assertIn('Job ticket is done by builder', self.register(code=2))
        self.use('builder', 'ticket')
        self.register()

    def test_registration_checks_the_assignment_sent_to_the_worker(self):
        self.assertIn('requires packet', self.register(packet=str(self.root / 'absent.txt'), code=2))
        packet = self.root / 'partial.txt'
        packet.write_text('\n'.join(line for line in self.packet().read_text().splitlines()
                                    if not line.startswith(('brief:', 'agent_file:'))))
        self.assertIn('The ticket assignment lacks agent_file, brief', self.register(packet=str(packet), code=2))
        self.register()

    def test_a_kept_worker_cannot_decide_a_finding_it_raised(self):
        self.use('reviewer', 'diff-review')
        self.register('review-1/1', 'review-1')
        self.ready('review-1/1')
        report = self.root / 'review.md'
        report.write_text('## Coverage\n\n## Review round 1\n')
        self.completed('review-1/1', status='completed', head='head-1', report=str(report),
                    evidence=['review.md'])
        self.acknowledge()
        self.record('retain', 'review-1/1', reason='Disputed finding', next_use='recheck', evidence=['review.md'])
        self.use('reviewer', 'reproduce')
        self.assertIn('independent of diff-review', self.register('review-1/reproduce', 'review-1', code=2))
        # The same job again, as when the reviewer rechecks a repair, keeps its worker.
        self.use('reviewer', 'diff-review')
        self.register('review-1/2', 'review-1')
        # A fresh worker may decide it.
        self.use('reviewer', 'reproduce')
        self.register('review-1/reproduce', 'review-1', session='native-2', worker='pane-2')

    def test_acceptance_waits_for_the_job_report_sections(self):
        self.register()
        self.ready()
        report = self.root / 'report.md'
        report.write_text('Done; see the commits.\n')
        self.collect(report=report)
        gate = self.cli('gate', '--attempt', 'ticket-1/1', '--stage', 'accept', '--revision', 'head-1', code=1)
        self.assertEqual(gate['missing'], ['job report sections ## Implemented'])
        self.assertIn('requires the job report sections ## Implemented',
                      self.record('accepted', commit='merge-1', evidence=['git log'], code=2))
        self.acknowledge()
        closing = {'decision': 'accepted', 'commit': 'merge-1', 'evidence': ['git log']}
        self.assertIn('requires the job report sections', self.record('closed', evidence=['pane closed'],
                                                                      acceptance=closing, code=2))
        self.use('builder', 'ticket')
        self.register('ticket-2/1', 'ticket-2', 'native-2', 'pane-2')
        self.ready('ticket-2/1')
        self.collect('ticket-2/1')  # The helper's report has its Implemented section.
        self.assertTrue(self.cli('gate', '--attempt', 'ticket-2/1', '--stage', 'accept',
                                 '--revision', 'head-1')['complete'])

    def test_a_section_is_a_real_heading_outside_code(self):
        for number, (text, ok) in enumerate((('## Implementedness\n', False),
                                              ('```\n## Implemented\n```\n', False),
                                              ('````md\n```\n## Implemented\n```\n````\n', False),
                                              ('```\n```python\n## Implemented\n```\n', False),
                                              ('```\nexample\n```\n## Implemented\n', True),
                                              ('Text\r\n## Implemented\r\nDone\r\n', True)), 1):
            with self.subTest(text=text):
                attempt = f'ticket-{number}/1'
                self.use('builder', 'ticket')
                self.register(attempt, f'ticket-{number}', f'native-{number}', f'pane-{number}')
                self.ready(attempt)
                report = self.root / f'report-{number}.md'
                report.write_bytes(text.encode())
                self.collect(attempt, report=report)
                self.cli('gate', '--attempt', attempt, '--stage', 'accept', '--revision', 'head-1', code=0 if ok else 1)

    def test_a_read_only_agent_must_leave_tracked_files_and_head_unchanged(self):
        head = self.checkout()
        for attempt, session, edit in (('review-1/1', 'native-1', 'edit'), ('review-2/1', 'native-2', 'commit'),
                                       ('review-3/1', 'native-3', None), ('review-4/1', 'native-4', 'dirty'),
                                       ('review-5/1', 'native-5', 'dirty-edit')):
            if edit in ('dirty', 'dirty-edit'):
                # A snapshot check can start from a dirty tree; only the worker's own changes count.
                (self.worktree / 'code.py').write_text('x = 3\n')
            report = self.review(head, attempt, session)
            if edit in ('edit', 'commit', 'dirty-edit'):
                (self.worktree / 'code.py').write_text('x = 2\n')
            if edit == 'commit':
                self.git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qam', 'reviewer edit')
            self.completed(attempt, status='completed', head=head, report=str(report), evidence=['review.md'])
            changed = edit in ('edit', 'commit', 'dirty-edit')
            gate = self.cli('gate', '--attempt', attempt, '--stage', 'accept', '--revision', head, code=1 if changed else 0)
            if changed:
                reason = (f'HEAD moved from {head} to {self.git("rev-parse", "HEAD")}' if edit == 'commit'
                          else 'tracked files differ from their state at registration')
                self.assertEqual(gate['missing'], [f'unchanged worktree for a read-only agent ({reason})'])
                self.git('reset', '-q', '--hard', head)
            else:
                self.assertTrue(gate['complete'])

    def test_an_untracked_file_a_read_only_agent_adds_fails_the_accept_gate(self):
        head = self.checkout()
        report = self.review(head)
        (self.worktree / 'notes.txt').write_text('scratch\n')
        self.completed('review-1/1', status='completed', head=head, report=str(report), evidence=['review.md'])
        gate = self.cli('gate', '--attempt', 'review-1/1', '--stage', 'accept', '--revision', head, code=1)
        self.assertEqual(gate['missing'],
                         ['unchanged worktree for a read-only agent (an untracked file appeared: notes.txt)'])

    def test_a_gitignored_file_created_after_registration_is_not_a_change(self):
        head = self.checkout({'code.py': 'x = 1\n', '.gitignore': '*.log\n'})
        report = self.review(head)
        (self.worktree / 'run.log').write_text('output\n')
        self.completed('review-1/1', status='completed', head=head, report=str(report), evidence=['review.md'])
        gate = self.cli('gate', '--attempt', 'review-1/1', '--stage', 'accept', '--revision', head)
        self.assertTrue(gate['complete'])

    def test_untracked_files_present_at_registration_are_not_a_change(self):
        head = self.checkout()
        (self.worktree / 'before.txt').write_text('there before the agent started\n')
        for attempt, session, added in (('review-1/1', 'native-1', None), ('review-2/1', 'native-2', 'notes.txt')):
            report = self.review(head, attempt, session)
            if added:
                (self.worktree / added).write_text('scratch\n')
            self.completed(attempt, status='completed', head=head, report=str(report), evidence=['review.md'])
            gate = self.cli('gate', '--attempt', attempt, '--stage', 'accept', '--revision', head,
                            code=1 if added else 0)
            if added:
                self.assertEqual(gate['missing'],
                                 [f'unchanged worktree for a read-only agent (an untracked file appeared: {added})'])
            else:
                self.assertTrue(gate['complete'])

    def test_collect_refuses_a_read_only_attempt_whose_worktree_changed(self):
        head = self.checkout()
        for number, change in enumerate(('commit', 'edit', 'untracked'), 1):
            with self.subTest(change=change):
                attempt = f'review-{number}/1'
                report = self.review(head, attempt, f'native-{number}')
                if change == 'untracked':
                    (self.worktree / 'notes.txt').write_text('scratch\n')
                else:
                    (self.worktree / 'code.py').write_text('x = 2\n')
                if change == 'commit':
                    self.git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qam', 'reviewer edit')
                reason = {'commit': f'HEAD moved from {head} to {self.git("rev-parse", "HEAD")}',
                          'edit': 'tracked files differ from their state at registration',
                          'untracked': 'an untracked file appeared: notes.txt'}[change]
                request = self.collect_request(head, report, attempt)
                error = self.cli('collect', '--attempt', attempt, '--input', request, code=2)
                self.assertIn(f'Collection requires an unchanged worktree for a read-only agent ({reason}); '
                              'restore it and collect again', error)
                self.assertNotIn('result', self.cli('status', '--attempt', attempt)['attempts'][0])
                # Once the worktree is as registered, the same result collects.
                self.git('reset', '-q', '--hard', head)
                self.git('clean', '-fdq')
                self.cli('collect', '--attempt', attempt, '--input', request)
                self.assertTrue(self.cli('gate', '--attempt', attempt, '--stage', 'accept', '--revision', head)['complete'])

    def test_collect_accepts_a_read_only_attempt_whose_worktree_is_as_registered(self):
        head = self.checkout({'code.py': 'x = 1\n', '.gitignore': '*.log\n'})
        # Dirty and untracked at registration, and an ignored file made later: none of it is the agent's change.
        (self.worktree / 'code.py').write_text('x = 3\n')
        (self.worktree / 'before.txt').write_text('there before the agent started\n')
        report = self.review(head)
        (self.worktree / 'run.log').write_text('output\n')
        self.cli('collect', '--attempt', 'review-1/1', '--input', self.collect_request(head, report))
        self.assertTrue(self.cli('gate', '--attempt', 'review-1/1', '--stage', 'accept', '--revision', head)['complete'])

    def test_collect_refuses_a_read_only_attempt_it_cannot_verify(self):
        self.use('reviewer', 'diff-review')
        self.register('review-1/1', 'review-1')
        self.ready('review-1/1')
        report = self.root / 'review.md'
        report.write_text('## Coverage\n\n## Review round 1\n')
        error = self.cli('collect', '--attempt', 'review-1/1', '--input', self.collect_request('head-1', report), code=2)
        self.assertIn('Collection requires an unchanged worktree for a read-only agent (not a readable Git checkout '
                      'at registration; read-only work cannot be verified); record the result with its gap', error)
        self.assertNotIn('restore it', error)

    def test_a_registration_without_untracked_files_does_not_count_a_new_untracked_file(self):
        head = self.checkout()
        report = self.review(head)
        self.forget_untracked('review-1/1')
        (self.worktree / 'notes.txt').write_text('scratch\n')
        self.cli('collect', '--attempt', 'review-1/1', '--input', self.collect_request(head, report))
        self.assertTrue(self.cli('gate', '--attempt', 'review-1/1', '--stage', 'accept', '--revision', head)['complete'])

    def test_a_registration_without_untracked_files_still_catches_head_and_tracked_changes_and_says_so(self):
        head = self.checkout()
        for number, change in enumerate(('commit', 'edit'), 1):
            with self.subTest(change=change):
                attempt = f'review-{number}/1'
                report = self.review(head, attempt, f'native-{number}')
                self.forget_untracked(attempt)
                (self.worktree / 'code.py').write_text('x = 2\n')
                if change == 'commit':
                    self.git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qam', 'reviewer edit')
                reason = (f'HEAD moved from {head} to {self.git("rev-parse", "HEAD")}' if change == 'commit'
                          else 'tracked files differ from their state at registration')
                error = self.cli('collect', '--attempt', attempt, '--input', self.collect_request(head, report, attempt), code=2)
                self.assertIn('Collection requires an unchanged worktree for a read-only agent '
                              f'({reason}; untracked files were not recorded at registration)', error)
                self.git('reset', '-q', '--hard', head)

    @unittest.skipIf(os.geteuid() == 0, 'root reads any file')
    def test_an_untracked_file_the_agent_cannot_read_is_held_to_a_stable_marker(self):
        head = self.checkout()
        secret = self.worktree / 'secret.txt'
        secret.write_text('private\n')
        secret.chmod(0)
        self.addCleanup(secret.chmod, 0o600)
        for attempt, session, readable in (('review-1/1', 'native-1', False), ('review-2/1', 'native-2', True)):
            report = self.review(head, attempt, session)
            if readable:
                secret.chmod(0o600)
                secret.write_text('rewritten by the agent\n')
            self.completed(attempt, status='completed', head=head, report=str(report), evidence=['review.md'])
            gate = self.cli('gate', '--attempt', attempt, '--stage', 'accept', '--revision', head,
                            code=1 if readable else 0)
            if readable:
                self.assertEqual(gate['missing'],
                                 ['unchanged worktree for a read-only agent (an untracked file changed: secret.txt)'])
            else:
                self.assertTrue(gate['complete'])

    def test_an_untracked_file_that_vanishes_before_it_is_hashed_counts_as_absent(self):
        head = self.checkout()
        (self.worktree / 'before.txt').write_text('listed, then gone\n')
        self.fake_git(vanish=self.worktree / 'before.txt')
        report = self.review(head)
        self.assertFalse((self.worktree / 'before.txt').exists())
        self.completed('review-1/1', status='completed', head=head, report=str(report), evidence=['review.md'])
        self.assertTrue(self.cli('gate', '--attempt', 'review-1/1', '--stage', 'accept', '--revision', head)['complete'])

    def test_an_untracked_name_that_is_not_utf8_does_not_break_the_snapshot(self):
        head = self.checkout()
        # APFS refuses such a name, so Git is made to list one; the file itself is not there to hash.
        self.fake_git(extra=b'caf\xe9.txt')
        report = self.review(head)
        self.completed('review-1/1', status='completed', head=head, report=str(report), evidence=['review.md'])
        self.assertTrue(self.cli('gate', '--attempt', 'review-1/1', '--stage', 'accept', '--revision', head)['complete'])

    def test_retargeting_an_untracked_symlink_is_a_change(self):
        head = self.checkout()
        for name in ('one', 'two'):
            (self.worktree / name).mkdir()
        (self.worktree / 'link').symlink_to('one')
        report = self.review(head)
        (self.worktree / 'link').unlink()
        (self.worktree / 'link').symlink_to('two')
        self.completed('review-1/1', status='completed', head=head, report=str(report), evidence=['review.md'])
        gate = self.cli('gate', '--attempt', 'review-1/1', '--stage', 'accept', '--revision', head, code=1)
        self.assertEqual(gate['missing'],
                         ['unchanged worktree for a read-only agent (an untracked file changed: link)'])

    def test_a_read_only_attempt_whose_worktree_state_is_unreadable_at_collection_is_refused(self):
        head = self.checkout()
        report = self.review(head)
        shutil.rmtree(self.worktree / '.git')
        error = self.cli('collect', '--attempt', 'review-1/1', '--input', self.collect_request(head, report), code=2)
        self.assertIn('Collection requires an unchanged worktree for a read-only agent '
                      '(worktree state unreadable at collection); record the result with its gap', error)
        self.assertNotIn('restore it', error)
        self.assertNotIn('result', self.cli('status', '--attempt', 'review-1/1')['attempts'][0])
        # Recording the result keeps the gap on it, so acceptance stays refused.
        self.completed('review-1/1', status='completed', head=head, report=str(report), evidence=['review.md'])
        gate = self.cli('gate', '--attempt', 'review-1/1', '--stage', 'accept', '--revision', head, code=1)
        self.assertEqual(gate['missing'],
                         ['unchanged worktree for a read-only agent (worktree state unreadable at collection)'])

    def test_a_refusal_for_an_untracked_file_that_cannot_be_restored_names_a_way_out_that_works(self):
        head = self.checkout()
        (self.worktree / 'before.txt').write_text('there before the agent started\n')
        for number, change in enumerate(('edited', 'deleted'), 1):
            with self.subTest(change=change):
                attempt = f'review-{number}/1'
                report = self.review(head, attempt, f'native-{number}')
                if change == 'edited':
                    (self.worktree / 'before.txt').write_text('rewritten by the agent\n')
                else:
                    (self.worktree / 'before.txt').unlink()
                request = self.collect_request(head, report, attempt)
                error = self.cli('collect', '--attempt', attempt, '--input', request, code=2)
                self.assertIn('record the result with its gap (`record`, kind `result`), then collect again', error)
                self.assertNotIn('restore it', error)
                # The way out: the result keeps its gap, the collection resumes, and acceptance stays refused.
                self.completed(attempt, status='completed', head=head, report=str(report), evidence=['review.md'])
                self.assertTrue(self.cli('collect', '--attempt', attempt, '--input', request)['result']['already_collected'])
                gate = self.cli('gate', '--attempt', attempt, '--stage', 'accept', '--revision', head, code=1)
                self.assertEqual(gate['missing'], [f'unchanged worktree for a read-only agent '
                                                   f'(an untracked file {"changed" if change == "edited" else "was deleted"}: before.txt)'])

    def test_the_registration_event_leaves_the_worktree_snapshot_to_the_attempt_row(self):
        head = self.checkout()
        (self.worktree / 'before.txt').write_text('there before the agent started\n')
        self.review(head)
        with sqlite3.connect(self.state) as db:
            event = json.loads(db.execute("SELECT payload FROM events WHERE kind = 'register'").fetchone()[0])
            attempt = json.loads(db.execute('SELECT data FROM attempts').fetchone()[0])
        self.assertNotIn('worktree_baseline', event)
        self.assertEqual(attempt['worktree_baseline']['head'], head)
        self.assertEqual(list(attempt['worktree_baseline']['untracked']), ['before.txt'])

    def test_collect_reads_a_read_only_worktree_once(self):
        head = self.checkout()
        report = self.review(head)
        self.fake_git()
        self.cli('collect', '--attempt', 'review-1/1', '--input', self.collect_request(head, report))
        listings = [call for call in self.git_log.read_text().splitlines() if 'ls-files --others' in call]
        self.assertEqual(len(listings), 1, listings)

    def test_collect_reads_a_read_only_worktree_before_it_takes_the_write_lock(self):
        head = self.checkout()
        report = self.review(head)
        self.fake_git(probe_lock=True)
        self.cli('collect', '--attempt', 'review-1/1', '--input', self.collect_request(head, report))
        listings = [call for call in self.git_log.read_text().splitlines() if 'ls-files --others' in call]
        self.assertEqual([call.rsplit(' [', 1)[1] for call in listings], ['write lock free]'])

    def test_a_read_only_agent_outside_a_git_checkout_cannot_be_verified(self):
        self.use('reviewer', 'diff-review')
        self.register('review-1/1', 'review-1')
        self.ready('review-1/1')
        report = self.root / 'review.md'
        report.write_text('## Coverage\n\n## Review round 1\n')
        self.completed('review-1/1', status='completed', head='head-1', report=str(report), evidence=['r'])
        gate = self.cli('gate', '--attempt', 'review-1/1', '--stage', 'accept', '--revision', 'head-1', code=1)
        self.assertEqual(gate['missing'], ['unchanged worktree for a read-only agent (not a readable Git checkout '
                                           'at registration; read-only work cannot be verified)'])

    def with_submodule(self):
        """A checkout whose `sub` is a submodule of one commit; returns the checkout's and the submodule's HEAD."""
        source = self.root / 'source'
        source.mkdir()
        run = lambda path, *args: subprocess.run(['git', '-C', str(path), '-c', 'user.name=t', '-c', 'user.email=t@t',
                                                  '-c', 'protocol.file.allow=always', *args],
                                                 check=True, capture_output=True, text=True).stdout.strip()
        run(source, 'init', '-q')
        (source / 'a.txt').write_text('a\n')
        (source / 'b.txt').write_text('b\n')
        run(source, 'add', '-A')
        run(source, 'commit', '-qm', 'sub base')
        self.checkout()
        run(self.worktree, 'submodule', 'add', '-q', str(source), 'sub')
        run(self.worktree, 'commit', '-qm', 'add sub')
        self.sub_git = lambda *args: run(self.worktree / 'sub', *args)
        return self.git('rev-parse', 'HEAD'), self.sub_git('rev-parse', 'HEAD')

    def test_a_change_inside_a_submodule_is_refused_naming_the_submodule(self):
        head, sub_head = self.with_submodule()
        sub = self.worktree / 'sub'
        for number, change in enumerate(('edit', 'commit', 'untracked', 'dirty-edit'), 1):
            with self.subTest(change=change):
                attempt = f'review-{number}/1'
                if change == 'dirty-edit':
                    # Dirty at registration: a further edit leaves the superproject's view (`-dirty`) as it was.
                    (sub / 'a.txt').write_text('dirty before the agent started\n')
                report = self.review(head, attempt, f'native-{number}')
                if change == 'untracked':
                    (sub / 'notes.txt').write_text('scratch\n')
                elif change == 'dirty-edit':
                    (sub / 'b.txt').write_text('edited by the agent\n')
                else:
                    (sub / 'a.txt').write_text('edited by the agent\n')
                if change == 'commit':
                    self.sub_git('commit', '-qam', 'reviewer edit')
                reason = {'edit': 'tracked files differ from their state at registration',
                          'commit': f'HEAD moved from {sub_head} to {self.sub_git("rev-parse", "HEAD")}',
                          'untracked': 'an untracked file appeared: notes.txt',
                          'dirty-edit': 'tracked files differ from their state at registration'}[change]
                request = self.collect_request(head, report, attempt)
                error = self.cli('collect', '--attempt', attempt, '--input', request, code=2)
                self.assertIn('Collection requires an unchanged worktree for a read-only agent '
                              f'(in submodule sub: {reason}); restore it and collect again', error)
                # Once the submodule is as registered, the same result collects.
                self.sub_git('reset', '-q', '--hard', sub_head)
                self.sub_git('clean', '-fdq')
                if change == 'dirty-edit':
                    (sub / 'a.txt').write_text('dirty before the agent started\n')
                self.cli('collect', '--attempt', attempt, '--input', request)
                self.sub_git('reset', '-q', '--hard', sub_head)

    def test_a_change_inside_a_nested_repository_is_refused_naming_it(self):
        head = self.checkout()
        nested = self.worktree / 'nested'
        nested_git = lambda *args: subprocess.run(['git', '-C', str(nested), '-c', 'user.name=t', '-c',
                                                   'user.email=t@t', *args],
                                                  check=True, capture_output=True, text=True).stdout.strip()
        nested.mkdir()
        nested_git('init', '-q')
        (nested / 'a.txt').write_text('a\n')
        nested_git('add', '-A')
        nested_git('commit', '-qm', 'nested base')
        nested_head = nested_git('rev-parse', 'HEAD')
        (self.worktree / 'empty').mkdir()
        empty_git = lambda *args: subprocess.run(['git', '-C', str(self.worktree / 'empty'), *args], check=True)
        empty_git('init', '-q')  # A repository with no commit yet.
        for number, change in enumerate(('edit', 'commit', 'untracked', 'unborn-untracked', 'unborn-added'), 1):
            with self.subTest(change=change):
                attempt = f'review-{number}/1'
                report = self.review(head, attempt, f'native-{number}')
                if change in ('edit', 'commit'):
                    (nested / 'a.txt').write_text('edited by the agent\n')
                elif change == 'untracked':
                    (nested / 'notes.txt').write_text('scratch\n')
                else:
                    (self.worktree / 'empty' / 'notes.txt').write_text('scratch\n')
                if change == 'commit':
                    nested_git('commit', '-qam', 'reviewer edit')
                if change == 'unborn-added':
                    empty_git('add', 'notes.txt')
                reason = {'edit': 'in nested repository nested/: tracked files differ from their state at registration',
                          'commit': f'in nested repository nested/: HEAD moved from {nested_head} to '
                                    f'{nested_git("rev-parse", "HEAD")}',
                          'untracked': 'in nested repository nested/: an untracked file appeared: notes.txt',
                          'unborn-untracked': 'in nested repository empty/: an untracked file appeared: notes.txt',
                          'unborn-added': 'in nested repository empty/: tracked files differ from their state at '
                                          'registration'}[change]
                request = self.collect_request(head, report, attempt)
                error = self.cli('collect', '--attempt', attempt, '--input', request, code=2)
                self.assertIn(f'Collection requires an unchanged worktree for a read-only agent ({reason}); '
                              'restore it and collect again', error)
                # Once the nested repository is as registered, the same result collects.
                nested_git('reset', '-q', '--hard', nested_head)
                nested_git('clean', '-fdq')
                empty_git('rm', '-q', '--cached', '--ignore-unmatch', 'notes.txt')
                (self.worktree / 'empty' / 'notes.txt').unlink(missing_ok=True)
                self.cli('collect', '--attempt', attempt, '--input', request)

    def test_an_uninitialized_submodule_is_named_partly_checked_and_held_to_its_directory(self):
        head, _ = self.with_submodule()
        self.git('submodule', 'deinit', '-f', 'sub')
        for number, change in enumerate((None, 'written'), 1):
            with self.subTest(change=change):
                attempt = f'review-{number}/1'
                report = self.review(head, attempt, f'native-{number}')
                request = self.collect_request(head, report, attempt)
                if change is None:
                    collected = self.cli('collect', '--attempt', attempt, '--input', request)
                    self.assertEqual(collected['result']['partly_checked'], ['sub/'])
                    continue
                # Git sees no file in a submodule directory it has no checkout for.
                (self.worktree / 'sub' / 'notes.txt').write_text('scratch\n')
                error = self.cli('collect', '--attempt', attempt, '--input', request, code=2)
                self.assertIn('Collection requires an unchanged worktree for a read-only agent (submodule sub is not '
                              'the checkout it was at registration); record the result with its gap', error)

    def test_a_submodule_replaced_by_a_symlink_is_not_followed(self):
        head, _ = self.with_submodule()
        # Git reads no submodule it is told to ignore, so it lets a symlink stand where the submodule was.
        self.git('config', 'submodule.sub.ignore', 'all')
        report = self.review(head)
        # A link back to the checkout itself: followed, the snapshot would never end.
        shutil.rmtree(self.worktree / 'sub')
        (self.worktree / 'sub').symlink_to('.')
        error = self.cli('collect', '--attempt', 'review-1/1', '--input', self.collect_request(head, report), code=2)
        self.assertIn('Collection requires an unchanged worktree for a read-only agent (submodule sub is not '
                      'the checkout it was at registration); record the result with its gap', error)

    def test_submodules_reached_through_a_symlinked_parent_are_not_followed(self):
        head, _ = self.with_submodule()
        source = self.root / 'source'
        for parent in ('a', 'b'):
            self.git('-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(source), f'{parent}/checkout')
        self.git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'two more')
        head = self.git('rev-parse', 'HEAD')
        report = self.review(head)
        # Each parent becomes a link to the checkout's parent: followed, each link doubles the snapshot, endlessly.
        for parent in ('a', 'b'):
            shutil.rmtree(self.worktree / parent)
            (self.worktree / parent).symlink_to('..')
        command = [sys.executable, '-B', str(support.script('worker_lifecycle.py')), 'collect', '--state',
                   str(self.state), '--attempt', 'review-1/1', '--input', self.collect_request(head, report)]
        process = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 2, process.stdout)
        self.assertIn('Collection requires an unchanged worktree for a read-only agent (submodule a/checkout is not '
                      'the checkout it was at registration); record the result with its gap', process.stderr)

    @unittest.skipIf(os.geteuid() == 0, 'root reads any directory')
    def test_a_registration_from_before_nested_checkouts_were_recorded_is_compared_as_it_was_recorded(self):
        head = self.checkout()
        self.lock(self.worktree / 'locked')
        subprocess.run(['git', 'init', '-q', str(self.worktree / 'nested')], check=True)
        for number, added in enumerate((None, 'notes.txt', 'locked nested'), 1):
            with self.subTest(added=added):
                attempt = f'review-{number}/1'
                report = self.review(head, attempt, f'native-{number}')
                # Then: no submodules, no record of a directory Git could not open, a nested repository as a marker.
                with sqlite3.connect(self.state) as db:
                    data = json.loads(db.execute('SELECT data FROM attempts WHERE id = ?', (attempt,)).fetchone()[0])
                    baseline = data['worktree_baseline']
                    del baseline['submodules'], baseline['untracked']['locked/']
                    baseline['untracked']['nested/'] = 'unreadable:EISDIR'
                    db.execute('UPDATE attempts SET data = ? WHERE id = ?', (json.dumps(data), attempt))
                if added == 'notes.txt':
                    (self.worktree / added).write_text('scratch\n')
                elif added:
                    # Git cannot open it now, so it leaves the listing, as it did for the older code.
                    (self.worktree / 'nested').chmod(0)
                    self.addCleanup((self.worktree / 'nested').chmod, 0o700)
                request = self.collect_request(head, report, attempt)
                if not added:
                    self.cli('collect', '--attempt', attempt, '--input', request)
                    continue
                error = self.cli('collect', '--attempt', attempt, '--input', request, code=2)
                reason = {'notes.txt': 'an untracked file appeared: notes.txt); restore it and collect again',
                          'locked nested': 'an untracked file was deleted: nested/); record the result with its gap'}
                self.assertIn(f'Collection requires an unchanged worktree for a read-only agent ({reason[added]}', error)

    @unittest.skipIf(os.geteuid() == 0, 'root reads any directory')
    def test_a_checkout_whose_top_cannot_be_listed_is_named_partly_checked_and_held_to_its_status(self):
        head, _ = self.with_submodule()
        nested = self.worktree / 'nested'
        nested.mkdir()
        subprocess.run(['git', 'init', '-q', str(nested)], check=True)
        for number, (place, name, partly) in enumerate(((nested, 'nested repository nested/', 'nested/'),
                                                        (self.worktree / 'sub', 'submodule sub', 'sub/'),
                                                        (self.worktree, None, './')), 1):
            with self.subTest(place=name):
                try:
                    attempt = f'review-{number}/1'
                    # Searchable, not listable: Git reads its tracked files but sees none of its untracked ones.
                    place.chmod(0o300)
                    report = self.review(head, attempt, f'native-{number}')
                    request = self.collect_request(head, report, attempt)
                    place.chmod(0o700)
                    (place / 'added.txt').write_text('scratch\n')
                    place.chmod(0o300)
                    reason = (f'in {name}: ' if name else '') + 'an unreadable directory changed: ./'
                    error = self.cli('collect', '--attempt', attempt, '--input', request, code=2)
                    self.assertIn(f'Collection requires an unchanged worktree for a read-only agent ({reason}); '
                                  'record the result with its gap', error)
                    # The way out records the gap; the next attempt, with nothing changed, names the directory.
                    self.completed(attempt, status='completed', head=head, report=str(report), evidence=['r'])
                    attempt = f'review-{number}/2'
                    report = self.review(head, attempt, f'native-{number}-2')
                    collected = self.cli('collect', '--attempt', attempt, '--input', self.collect_request(head, report, attempt))
                    self.assertEqual(collected['result']['partly_checked'], [partly])
                finally:
                    place.chmod(0o700)  # A later subtest needs it listable.

    def lock(self, directory):
        """Make an untracked directory, with a file in it, that Git cannot open."""
        directory.mkdir(parents=True)
        (directory / 'inside.txt').write_text('unseen\n')
        directory.chmod(0)
        self.addCleanup(directory.chmod, 0o700)

    @unittest.skipIf(os.geteuid() == 0, 'root reads any directory')
    def test_an_unreadable_directory_is_named_partly_checked_and_held_to_what_can_be_read(self):
        head = self.checkout()
        self.lock(self.worktree / 'locked')
        self.lock(self.worktree / 'plain' / 'deep')
        subprocess.run(['git', 'init', '-q', str(self.worktree / 'nested')], check=True)
        self.lock(self.worktree / 'nested' / 'locked')
        for number, change in enumerate((None, 'entered', 'appeared'), 1):
            with self.subTest(change=change):
                attempt = f'review-{number}/1'
                report = self.review(head, attempt, f'native-{number}')
                if change == 'entered':
                    # Opened, written and locked again: only its record shows it.
                    (self.worktree / 'locked').chmod(0o700)
                    (self.worktree / 'locked' / 'added.txt').write_text('scratch\n')
                    (self.worktree / 'locked').chmod(0)
                elif change == 'appeared':
                    self.lock(self.worktree / 'late')
                request = self.collect_request(head, report, attempt)
                if change is None:
                    collected = self.cli('collect', '--attempt', attempt, '--input', request)
                    self.assertEqual(collected['result']['partly_checked'],
                                     ['locked/', 'nested/locked/', 'plain/deep/'])
                    continue
                error = self.cli('collect', '--attempt', attempt, '--input', request, code=2)
                reason = {'entered': 'an unreadable directory changed: locked/); record the result with its gap',
                          'appeared': 'an untracked file appeared: late/); restore it'}[change]
                self.assertIn(f'Collection requires an unchanged worktree for a read-only agent ({reason}', error)

    def test_the_collect_doc_says_what_the_check_covers_and_how_to_get_past_a_refusal(self):
        doc = ' '.join((support.ROOT / 'runtime/workers/collect.md').read_text().split())
        self.assertIn('by the same rules, in each submodule and each nested repository (an untracked directory '
                      'that is a Git checkout of its own)', doc)
        self.assertIn('A directory Git cannot open (a checkout\'s top too, named `./` in it), and a submodule with no '
                      'checkout, is held to its own status', doc)
        self.assertIn('`collect` names it in `partly_checked` even when nothing changed', doc)
        self.assertNotIn('are not checked', doc)
        self.assertIn('Restore a moved HEAD, a tracked change or a new untracked file, then collect again', doc)
        self.assertIn('record the result with `record` (kind `result`), which keeps the gap on it, then collect again', doc)


if __name__ == '__main__':
    unittest.main()
