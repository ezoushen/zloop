#!/usr/bin/env python3
"""Exercise the supported merge commands against a fake GitHub CLI."""
import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

import support
from t3_intent_fixture import T3Read

REPO = 'fixture/zloop'
BASE = 'release/next'
SHA = 'a' * 40
PULL = f'repos/{REPO}/pulls/12'
BRANCH = f'repos/{REPO}/branches/release%2Fnext'
RULES = f'repos/{REPO}/rules/branches/release%2Fnext'


class PRSafety(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        subprocess.run(['git', 'remote', 'add', 'origin', f'git@github.com:{REPO}.git'],
                       cwd=self.root, check=True)
        self.fixture = self.root / 'responses.json'
        self.calls = self.root / 'calls.jsonl'
        tools = self.root / 'tools'
        tools.mkdir()
        (tools / 'git').symlink_to(shutil.which('git'))
        fixture = tools / 'gh_fixture.py'
        fixture.write_text("""\
import json
from pathlib import Path
import sys
args = sys.argv[1:]
calls_path = Path('calls.jsonl')
with calls_path.open('a') as calls:
    calls.write(json.dumps(args) + '\\n')
fixtures = json.loads(Path('responses.json').read_text())
reply = fixtures.get(' '.join(args))
if isinstance(reply, dict) and 'sequence' in reply:
    count = sum(json.loads(line) == args for line in calls_path.read_text().splitlines())
    reply = reply['sequence'][count - 1]
if reply is None:
    sys.exit('fixture denies this command')
print(json.dumps(reply))
""")
        gh = tools / 'gh'
        gh.write_text(f'#!/bin/sh\nexec {shlex.join([sys.executable, str(fixture)])} "$@"\n')
        gh.chmod(0o755)
        self.env = dict(support.without_host_sessions(), PATH=str(tools))
        self.t3 = None
        self.replies = {
            f'api --hostname github.com {PULL}':
                {'number': 12, 'state': 'open', 'base': {'ref': BASE, 'repo': {'full_name': REPO}},
                 'head': {'sha': SHA}},
            f'api --hostname github.com {BRANCH}':
                {'name': BASE, 'protected': False, 'protection': {'enabled': False}},
            f'api --hostname github.com --paginate --slurp {RULES}': [[]],
        }

    def run_cli(self, script, *args):
        self.fixture.write_text(json.dumps(self.replies))
        path = support.SCRIPTS / script
        command = self.t3.command(path, *args) if self.t3 else [sys.executable, str(path), *args]
        return subprocess.run(command,
                              cwd=self.root, env=self.env, text=True, capture_output=True)

    def approve_resume(self, card, revision=1, target=('--merge-pr', '12')):
        if self.t3 is None:
            self.t3 = T3Read(self, self.root, self.env)
        self.t3.answer = {}
        args = ('resume', str(card), '--expect', str(revision), '--request-id', f'merge-grant-{revision}',
                '--reason', 'user requested merge', *target)
        preview = self.run_cli('run_control.py', *args)
        self.t3.accept(preview, 'autopilot --take-over --auto-merge')
        return self.run_cli('run_control.py', *args)

    def grant(self, card, target=('--merge-pr', '12')):
        adopted = self.run_cli('run_control.py', 'adopt', str(card), '--source', 'existing confirmation')
        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        granted = self.approve_resume(card, target=target)
        self.assertEqual(granted.returncode, 0, granted.stderr)

    def test_preflight_refuses_without_required_checks(self):
        result = self.run_cli('pr_safety.py', 'grant', '--pr', '12')
        self.assertEqual((result.returncode, result.stderr.strip()),
                         (2, f'waiting-on-user: no required checks apply to {REPO}:{BASE}'))
        self.assertEqual([json.loads(row) for row in self.calls.read_text().splitlines()],
                         [['api', '--hostname', 'github.com', PULL],
                          ['api', '--hostname', 'github.com', BRANCH],
                          ['api', '--hostname', 'github.com', '--paginate', '--slurp', RULES]])

    def test_merge_submits_only_after_verified_classic_or_applicable_ruleset_checks(self):
        classic = {'name': BASE, 'protected': True, 'protection': {'enabled': True,
                   'required_status_checks': {'enforcement_level': 'non_admins', 'contexts': ['test']}}}
        active = {'type': 'required_status_checks', 'ruleset_id': 7, 'ruleset_source_type': 'Repository',
                  'ruleset_source': REPO, 'parameters': {'required_status_checks': [{'context': 'docs'}]}}
        no_classic = self.replies[f'api --hostname github.com {BRANCH}']
        for protection, pages, check in ((classic, [[]], 'test'), (no_classic, [[], [active]], 'docs')):
            with self.subTest(check=check):
                self.calls.write_text('')
                self.replies[f'api --hostname github.com {BRANCH}'] = protection
                self.replies[f'api --hostname github.com --paginate --slurp {RULES}'] = pages
                grant = self.run_cli('pr_safety.py', 'grant', '--pr', '12')
                self.assertEqual(grant.returncode, 0, grant.stderr)
                self.assertEqual(json.loads(grant.stdout), {'required_checks': [check]})
                card = support.run_card(self.root / check / 'autopilot.md', phase='pr', **{'intent-thread': 'host-1'})
                self.grant(card)
                self.t3.pr_readback(REPO, 12, SHA, BASE)
                result = self.run_cli('run_control.py', 'merge', str(card), '--merge-pr', '12', '--head', SHA,
                                      '--method', 'squash', '--expect', '2')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['outcome'], 'merged')
                reads = [['api', '--hostname', 'github.com', PULL],
                         ['api', '--hostname', 'github.com', BRANCH],
                         ['api', '--hostname', 'github.com', '--paginate', '--slurp', RULES]]
                self.assertEqual([json.loads(row) for row in self.calls.read_text().splitlines()],
                                 [*reads, reads[0], reads[0], *reads, reads[0], *reads, reads[0]])

    def test_preflight_refuses_malformed_unavailable_or_out_of_scope_github_responses(self):
        pull = f'api --hostname github.com {PULL}'
        classic = f'api --hostname github.com {BRANCH}'
        rules = f'api --hostname github.com --paginate --slurp {RULES}'
        misdirected = dict(self.replies[pull], base={'ref': BASE, 'repo': {'full_name': 'fork/zloop'}})
        unscoped = {'type': 'required_status_checks', 'ruleset_id': 7, 'ruleset_source_type': 'Repository',
                    'ruleset_source': 'fork/zloop', 'parameters': {'required_status_checks': [{'context': 'CI'}]}}
        for endpoint, fixture, reason in (
                (pull, misdirected, 'cannot verify open PR'),
                (classic, {'name': 'main', 'protected': True, 'protection': {'enabled': True}},
                 'cannot verify required checks'),
                (classic, None, 'cannot verify GitHub response'),
                (rules, {'wrong': 'shape'}, 'cannot verify required checks'),
                (rules, [[unscoped]], 'cannot verify required checks'),
                (rules, None, 'cannot verify GitHub response')):
            with self.subTest(endpoint=endpoint, fixture=fixture):
                original = self.replies[endpoint]
                self.replies[endpoint] = fixture
                self.calls.write_text('')
                result = self.run_cli('pr_safety.py', 'grant', '--pr', '12')
                self.replies[endpoint] = original
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertTrue(result.stderr.startswith(f'waiting-on-user: {reason}'), result.stderr)
                self.assertTrue(all(json.loads(row)[0] == 'api' for row in self.calls.read_text().splitlines()))

    def test_legacy_takeover_refuses_a_merge_request_before_github_access(self):
        card = support.run_card(self.root / 'autopilot.md', phase='pr')
        before = card.read_bytes()
        result = self.run_cli('autopilot_card.py', 'take-over', str(card), '--auto-merge', '--merge-base', BASE)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('requires a managed run', result.stderr)
        self.assertEqual(card.read_bytes(), before)
        self.assertFalse(self.calls.exists())

    def test_explicit_merge_request_during_relay_confirmation_is_refused(self):
        self.env['PI_SESSION_ID'] = 'host-2'
        card = support.run_card(self.root / 'autopilot.md', phase='pr')
        draft = card.read_text().replace('phase: pr',
                                        'phase: pr\nmode: full\ndispatch-effort: medium\nreview-effort: medium')
        card.write_text(draft)
        result = self.run_cli('autopilot_card.py', 'confirm', str(card), '--words', 'go', '--confirmed-by',
                              'relayed(Grok Bot)', '--auto-merge', '--merge-pr', '12')
        self.assertEqual((result.returncode, result.stderr.strip()),
                         (2, 'waiting-on-user: relayed confirmation cannot authorize auto-merge'))
        self.assertEqual(card.read_text(), draft)
        self.assertFalse(self.calls.exists())

    def test_existing_relayed_grant_cannot_submit_or_record_a_merge(self):
        self.replies[f'api --hostname github.com {BRANCH}'] = {
            'name': BASE, 'protected': True, 'protection': {'enabled': True,
                'required_status_checks': {'enforcement_level': 'everyone', 'contexts': ['docs']}}}
        card = support.run_card(self.root / 'autopilot.md', phase='pr',
                                **{'confirmed-by': 'human', 'intent-thread': 'host-1'})
        self.grant(card)
        record = card.with_name('autopilot-control.json')
        state = json.loads(record.read_text())
        state['card_text'] = state['card_text'].replace('confirmed-by: human', 'confirmed-by: relayed(Grok Bot)')
        card.write_text(state['card_text'])
        record.write_text(json.dumps(state))
        before = (card.read_bytes(), record.read_bytes())
        self.calls.write_text('')
        merge_args = ('merge', str(card), '--merge-pr', '12', '--head', SHA, '--method', 'squash', '--expect', '2')
        for args in (merge_args, ('end', str(card), '--source', 'delivery proof', '--terminal', 'merged',
                                  '--reason', 'merged', '--expect', '2', '--request-id', 'merged')):
            with self.subTest(command=args[0]):
                result = self.run_cli('run_control.py', *args)
                self.assertEqual((result.returncode, result.stderr.strip()),
                                 (2, 'waiting-on-user: relayed confirmation cannot authorize auto-merge'))
                self.assertEqual((card.read_bytes(), record.read_bytes()), before)
                self.assertEqual(self.calls.read_text(), '')

    def test_managed_exact_grant_refuses_another_pr_or_origin(self):
        self.replies[f'api --hostname github.com {BRANCH}'] = {
            'name': BASE, 'protected': True, 'protection': {'enabled': True,
                'required_status_checks': {'enforcement_level': 'everyone', 'contexts': ['docs']}}}
        card = support.run_card(self.root / 'autopilot.md', phase='pr', **{'intent-thread': 'host-1'})
        self.grant(card)
        merge = ('merge', str(card), '--head', SHA, '--method', 'squash', '--expect', '2', '--merge-pr')
        for repo, pr in ((REPO, 13), ('other/zloop', 12)):
            with self.subTest(repo=repo, pr=pr):
                subprocess.run(['git', 'remote', 'set-url', 'origin', f'git@github.com:{repo}.git'],
                               cwd=self.root, check=True)
                self.replies[f'api --hostname github.com repos/{repo}/pulls/{pr}'] = {
                    'number': pr, 'state': 'open', 'base': {'ref': BASE, 'repo': {'full_name': repo}},
                    'head': {'sha': SHA}}
                self.replies[f'api --hostname github.com repos/{repo}/branches/release%2Fnext'] = (
                    self.replies[f'api --hostname github.com {BRANCH}'])
                self.replies[f'api --hostname github.com --paginate --slurp repos/{repo}/rules/branches/release%2Fnext'] = [[]]
                self.replies[f'pr merge {pr} --repo github.com/{repo} --match-head-commit {SHA} --squash'] = 'submitted'
                self.calls.write_text('')
                result = self.run_cli('run_control.py', *merge, str(pr))
                self.assertEqual((result.returncode, result.stderr.strip()),
                                 (2, 'waiting-on-user: the PR does not match the verified merge grant'))
                self.assertEqual(self.calls.read_text(), '')

    def test_planned_grant_requires_the_approved_base_and_workstream_branch(self):
        branch = 'feat/workstream'
        subprocess.run(['git', 'symbolic-ref', 'HEAD', f'refs/heads/{branch}'], cwd=self.root, check=True)
        self.replies[f'api --hostname github.com {BRANCH}'] = {
            'name': BASE, 'protected': True, 'protection': {'enabled': True,
                'required_status_checks': {'enforcement_level': 'everyone', 'contexts': ['docs']}}}
        self.replies['api --hostname github.com repos/fixture/zloop/branches/main'] = dict(
            self.replies[f'api --hostname github.com {BRANCH}'], name='main')
        self.replies['api --hostname github.com --paginate --slurp repos/fixture/zloop/rules/branches/main'] = [[]]
        card = support.run_card(self.root / 'autopilot.md', phase='pr', **{'intent-thread': 'host-1'})
        self.grant(card, ('--merge-base', BASE))
        self.t3.pr_readback(REPO, 12, SHA, BASE)
        args = ('merge', str(card), '--merge-pr', '12', '--head', SHA, '--method', 'squash', '--expect', '2')
        for base, head_repo, head_ref, reason in (
                ('main', REPO, branch, 'the PR base does not match the verified merge grant'),
                (BASE, REPO, 'feat/other', 'the PR head does not match the verified workstream branch'),
                (BASE, 'fork/zloop', branch, 'the PR head does not match the verified workstream branch'),
                (BASE, REPO, branch, None)):
            with self.subTest(base=base, head_repo=head_repo, head_ref=head_ref):
                self.replies[f'api --hostname github.com {PULL}'] = {
                    'number': 12, 'state': 'open', 'base': {'ref': base, 'repo': {'full_name': REPO}},
                    'head': {'sha': SHA, 'ref': head_ref, 'repo': {'full_name': head_repo}}}
                self.calls.write_text('')
                result = self.run_cli('run_control.py', *args)
                calls = [json.loads(row) for row in self.calls.read_text().splitlines()]
                read = ['api', '--hostname', 'github.com', PULL]
                if reason:
                    self.assertEqual((result.returncode, result.stderr.strip()), (2, f'waiting-on-user: {reason}'))
                    self.assertEqual(calls, [read])
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)['outcome'], 'merged')
                    self.assertEqual(calls, [read, ['api', '--hostname', 'github.com', BRANCH],
                                             ['api', '--hostname', 'github.com', '--paginate', '--slurp', RULES],
                                             read])

    def test_unscoped_managed_record_requires_a_fresh_verified_grant(self):
        self.replies[f'api --hostname github.com {BRANCH}'] = {
            'name': BASE, 'protected': True, 'protection': {'enabled': True,
                'required_status_checks': {'enforcement_level': 'everyone', 'contexts': ['docs']}}}
        card = support.run_card(self.root / 'autopilot.md', phase='pr', **{'intent-thread': 'host-1'})
        self.grant(card)
        record = card.with_name('autopilot-control.json')
        state = json.loads(record.read_text())
        merge = ('merge', str(card), '--merge-pr', '12', '--head', SHA, '--method', 'squash', '--expect')
        ambiguous = dict(state, card_text=state['card_text'].replace('intent-thread: host-1\n', ''))
        card.write_text(ambiguous['card_text'])
        record.write_text(json.dumps(ambiguous))
        before = (card.read_bytes(), record.read_bytes())
        self.calls.write_text('')
        result = self.run_cli('run_control.py', *merge, '2')
        self.assertEqual((result.returncode, result.stderr.strip()), (
            2, 'waiting-on-user: obtain a new verified merge grant for this managed run'))
        self.assertEqual(self.calls.read_text(), '')
        self.assertEqual((card.read_bytes(), record.read_bytes()), before)

        card.write_text(state['card_text'])
        state.pop('merge_grant')
        record.write_text(json.dumps(state))
        before = (card.read_bytes(), record.read_bytes())
        result = self.run_cli('run_control.py', *merge, '2')
        self.assertEqual((result.returncode, result.stderr.strip()), (
            2, 'waiting-on-user: obtain a new verified merge grant for this managed run'))
        self.assertEqual(self.calls.read_text(), '')
        self.assertEqual((card.read_bytes(), record.read_bytes()), before)

        resumed = self.approve_resume(card, 2)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.t3.pr_readback(REPO, 12, SHA, BASE)
        result = self.run_cli('run_control.py', *merge, '3')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['outcome'], 'merged')

    def test_managed_submission_rechecks_required_checks_after_a_verified_grant(self):
        card = support.run_card(self.root / 'autopilot.md', phase='pr', **{'intent-thread': 'host-1'})
        classic = f'api --hostname github.com {BRANCH}'
        before = self.replies[classic]
        self.replies[classic] = {'name': BASE, 'protected': True, 'protection': {'enabled': True,
                                 'required_status_checks': {'enforcement_level': 'everyone', 'contexts': ['docs']}}}
        self.grant(card)
        self.replies[classic] = before
        self.calls.write_text('')
        result = self.run_cli('run_control.py', 'merge', str(card), '--merge-pr', '12', '--head', SHA,
                              '--method', 'squash', '--expect', '2')
        self.assertEqual((result.returncode, result.stderr.strip()),
                         (2, f'waiting-on-user: no required checks apply to {REPO}:{BASE}'))
        self.assertEqual([json.loads(row) for row in self.calls.read_text().splitlines()],
                         [['api', '--hostname', 'github.com', PULL],
                          ['api', '--hostname', 'github.com', BRANCH],
                          ['api', '--hostname', 'github.com', '--paginate', '--slurp', RULES]])
        self.assertIn('auto-merge: yes\n', card.read_text())
        status = self.run_cli('run_control.py', 'status', str(card))
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual((json.loads(status.stdout)['terminal'], json.loads(status.stdout)['revision']), ('', 2))

    def test_managed_takeover_does_not_upgrade_relayed_authority_to_a_merge_grant(self):
        self.replies[f'api --hostname github.com {BRANCH}'] = {
            'name': BASE, 'protected': True, 'protection': {'enabled': True,
                'required_status_checks': {'enforcement_level': 'everyone', 'contexts': ['docs']}}}
        card = support.run_card(self.root / 'autopilot.md', phase='pr', **{'confirmed-by': 'relayed(Grok Bot)'})
        adopted = self.run_cli('run_control.py', 'adopt', str(card), '--source', 'relayed confirmation')
        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        record = card.with_name('autopilot-control.json')
        before = (card.read_bytes(), record.read_bytes())
        native = self.root / 'native.jsonl'
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        native.write_text('\n'.join(map(json.dumps, [
            {'type': 'session_meta', 'payload': {'id': 'host-1'}},
            {'type': 'response_item', 'timestamp': stamp, 'payload': {
                'role': 'user', 'content': 'autopilot --take-over --auto-merge'}},
        ])))
        result = self.run_cli('run_control.py', 'resume', str(card), '--native', str(native), '--runtime', 'codex',
                              '--expect', '1', '--request-id', 'resume', '--reason', 'user decision',
                              '--merge-pr', '12')
        self.assertEqual((result.returncode, result.stderr.strip()),
                         (2, 'waiting-on-user: relayed confirmation cannot authorize auto-merge'))
        self.assertEqual((card.read_bytes(), record.read_bytes()), before)
        self.assertFalse(self.calls.exists())

    def test_managed_takeover_does_not_change_delivery_or_grant_without_required_checks(self):
        card = support.run_card(self.root / 'autopilot.md', phase='pr', **{'intent-thread': 'host-1'})
        self.assertEqual(self.run_cli('run_control.py', 'adopt', str(card), '--source', 'confirmed').returncode, 0)
        self.assertEqual(self.run_cli('run_control.py', 'end', str(card), '--source', 'delivery proof',
                                      '--terminal', 'delivered', '--reason', 'delivered',
                                      '--expect', '1', '--request-id', 'delivered').returncode, 0)
        before = card.read_bytes()
        result = self.approve_resume(card, 2)
        self.assertEqual((result.returncode, result.stderr.strip()),
                         (2, f'waiting-on-user: no required checks apply to {REPO}:{BASE}'))
        self.assertEqual(card.read_bytes(), before)
        status = self.run_cli('run_control.py', 'status', str(card))
        self.assertEqual(status.returncode, 0, status.stderr)
        view = json.loads(status.stdout)
        self.assertEqual((view['terminal'], view['revision']), ('delivered', 2))
        self.assertIn(b'auto-merge: no\n', before)
        self.assertEqual([json.loads(row) for row in self.calls.read_text().splitlines()],
                         [['api', '--hostname', 'github.com', PULL],
                          ['api', '--hostname', 'github.com', PULL],
                          ['api', '--hostname', 'github.com', BRANCH],
                          ['api', '--hostname', 'github.com', '--paginate', '--slurp', RULES]])


if __name__ == '__main__':
    unittest.main()
