#!/usr/bin/env python3
"""Exercise the supported merge commands against a fake GitHub CLI."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import support

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
        gh = self.root / 'gh'
        gh.write_text(f'#!{sys.executable}\n' + """\
import json
from pathlib import Path
import sys
args = sys.argv[1:]
with Path('calls.jsonl').open('a') as calls:
    calls.write(json.dumps(args) + '\\n')
fixtures = json.loads(Path('responses.json').read_text())
reply = fixtures.get(' '.join(args))
if reply is None:
    sys.exit('fixture denies this command')
print(json.dumps(reply))
""")
        gh.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{self.root}{os.pathsep}{os.environ["PATH"]}')
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
        return subprocess.run([sys.executable, str(support.SCRIPTS / script), *args],
                              cwd=self.root, env=self.env, text=True, capture_output=True)

    def test_pr_refuses_to_submit_without_required_checks(self):
        result = self.run_cli('pr_safety.py', 'merge', '--pr', '12', '--head', SHA, '--method', 'squash')
        self.assertEqual((result.returncode, result.stderr.strip()),
                         (2, f'waiting-on-user: no required checks apply to {REPO}:{BASE}'))
        self.assertEqual([json.loads(row) for row in self.calls.read_text().splitlines()],
                         [['api', '--hostname', 'github.com', PULL],
                          ['api', '--hostname', 'github.com', BRANCH],
                          ['api', '--hostname', 'github.com', '--paginate', '--slurp', RULES]])

    def test_merge_submits_only_after_verified_classic_or_applicable_ruleset_checks(self):
        merge = ['pr', 'merge', '12', '--repo', 'github.com/fixture/zloop', '--match-head-commit', SHA, '--squash']
        self.replies[' '.join(merge)] = 'submitted to GitHub'
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
                result = self.run_cli('pr_safety.py', 'merge', '--pr', '12', '--head', SHA, '--method', 'squash')
                self.assertEqual((result.returncode, result.stdout.strip()), (0, '"submitted to GitHub"'), result.stderr)
                reads = [['api', '--hostname', 'github.com', PULL],
                         ['api', '--hostname', 'github.com', BRANCH],
                         ['api', '--hostname', 'github.com', '--paginate', '--slurp', RULES]]
                self.assertEqual([json.loads(row) for row in self.calls.read_text().splitlines()],
                                 [*reads, *reads, reads[0], merge])

    def test_malformed_unavailable_or_out_of_scope_github_responses_never_submit(self):
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
                result = self.run_cli('pr_safety.py', 'merge', '--pr', '12', '--head', SHA, '--method', 'merge')
                self.replies[endpoint] = original
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertTrue(result.stderr.startswith(f'waiting-on-user: {reason}'), result.stderr)
                self.assertTrue(all(json.loads(row)[0] == 'api' for row in self.calls.read_text().splitlines()))

    def test_autopilot_takeover_refuses_an_unverified_explicit_base(self):
        card = support.run_card(self.root / 'autopilot.md', phase='pr')
        card.write_text(card.read_text().replace('phase: pr',
                        'phase: pr\ndispatch-effort: medium\nreview-effort: medium\nhistory:'))
        before = card.read_bytes()
        self.env['PI_SESSION_ID'] = 'host-2'
        result = self.run_cli('autopilot_card.py', 'take-over', str(card), '--auto-merge', '--merge-base', BASE)
        self.assertEqual((result.returncode, result.stderr.strip()),
                         (2, f'waiting-on-user: no required checks apply to {REPO}:{BASE}'))
        self.assertEqual(card.read_bytes(), before)
        self.assertEqual([json.loads(row) for row in self.calls.read_text().splitlines()],
                         [['api', '--hostname', 'github.com', BRANCH],
                          ['api', '--hostname', 'github.com', '--paginate', '--slurp', RULES]])

    def test_relayed_autopilot_grants_are_refused_before_github_access(self):
        self.replies[f'api --hostname github.com {BRANCH}'] = {
            'name': BASE, 'protected': True, 'protection': {'enabled': True,
                'required_status_checks': {'enforcement_level': 'everyone', 'contexts': ['docs']}}}
        self.env['PI_SESSION_ID'] = 'host-2'
        card = support.run_card(self.root / 'autopilot.md', phase='pr')
        draft = card.read_text().replace('phase: pr', 'phase: pr\nmode: full\n'
                     'dispatch-effort: medium\nreview-effort: medium')
        for command, args, source in (
                ('autopilot_card.py', ('confirm', str(card), '--words', 'go',
                                     '--confirmed-by', 'relayed(Grok Bot)'), 'human'),
                ('autopilot_card.py', ('take-over', str(card), '--auto-merge'), 'relayed(Grok Bot)'),
                ('pr_safety.py', ('grant', '--pr', '12', '--card', str(card)), 'relayed(Grok Bot)'),
                ('run_control.py', ('adopt', str(card), '--source', 'relayed confirmation'), 'relayed(Grok Bot)')):
            with self.subTest(command=command, action=args[0]):
                before = draft.replace('auto-merge: no', f'auto-merge: yes\nconfirmed-by: {source}')
                card.write_text(before)
                self.calls.write_text('')
                target = () if command == 'pr_safety.py' else ('--merge-pr', '12')
                result = self.run_cli(command, *args, *target)
                self.assertEqual((result.returncode, result.stderr.strip()),
                                 (2, 'waiting-on-user: relayed confirmation cannot authorize auto-merge'))
                self.assertEqual(card.read_text(), before)
                self.assertEqual(self.calls.read_text(), '')
                self.assertFalse(card.with_name('autopilot-control.json').exists())

        card = self.root / 'human' / 'autopilot.md'
        card.parent.mkdir()
        self.calls.write_text('')
        card.write_text(draft + 'confirmed-by: human\n')
        grant = self.run_cli('autopilot_card.py', 'take-over', str(card), '--auto-merge', '--merge-pr', '12')
        self.assertEqual(grant.returncode, 0, grant.stderr)
        self.assertIn('auto-merge: yes\n', card.read_text())
        self.assertIn('confirmed-by: human\n', card.read_text())
        self.assertEqual(len(self.calls.read_text().splitlines()), 3)

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
        merge = 'pr merge 12 --repo github.com/fixture/zloop --match-head-commit ' + SHA + ' --squash'
        self.replies[merge] = 'submitted to GitHub'
        card = support.run_card(self.root / 'autopilot.md', phase='pr', **{'confirmed-by': 'human'})
        card.write_text(card.read_text().replace('auto-merge: no', 'auto-merge: yes'))
        human = card.read_text()
        safety_args = ('merge', '--card', str(card), '--pr', '12', '--head', SHA, '--method', 'squash')
        for source, reason in (
                ('relayed(Grok Bot)', 'relayed confirmation cannot authorize auto-merge'),
                ('relayed()', 'Invalid confirmed-by: expected human or relayed(agent)')):
            with self.subTest(source=source):
                card.write_text(human.replace('confirmed-by: human', f'confirmed-by: {source}'))
                self.calls.write_text('')
                result = self.run_cli('pr_safety.py', *safety_args)
                self.assertEqual((result.returncode, result.stderr.strip()), (2, f'waiting-on-user: {reason}'))
                self.assertEqual(self.calls.read_text(), '')

        card.write_text(human)
        result = self.run_cli('pr_safety.py', *safety_args)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, '"submitted to GitHub"'), result.stderr)
        adopted = self.run_cli('run_control.py', 'adopt', str(card), '--source', 'existing confirmation',
                               '--merge-pr', '12')
        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        merge_args = ('merge', str(card), '--merge-pr', '12', '--head', SHA, '--method', 'squash', '--expect', '1')
        result = self.run_cli('run_control.py', *merge_args)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, '"submitted to GitHub"'), result.stderr)

        # Seed a canonical record with a relay grant that predates the guard.
        record = card.with_name('autopilot-control.json')
        state = json.loads(record.read_text())
        state['card_text'] = state['card_text'].replace('confirmed-by: human', 'confirmed-by: relayed(Grok Bot)')
        card.write_text(state['card_text'])
        record.write_text(json.dumps(state))
        before = (card.read_bytes(), record.read_bytes())
        self.calls.write_text('')
        for args in (merge_args, ('end', str(card), '--source', 'delivery proof', '--terminal', 'merged',
                                  '--reason', 'merged', '--expect', '1', '--request-id', 'merged')):
            with self.subTest(command=args[0]):
                result = self.run_cli('run_control.py', *args)
                self.assertEqual((result.returncode, result.stderr.strip()),
                                 (2, 'waiting-on-user: relayed confirmation cannot authorize auto-merge'))
                self.assertEqual((card.read_bytes(), record.read_bytes()), before)
                self.assertEqual(self.calls.read_text(), '')

    def test_managed_submission_rechecks_required_checks_after_a_verified_grant(self):
        card = support.run_card(self.root / 'autopilot.md', phase='pr')
        card.write_text(card.read_text().replace('auto-merge: no', 'auto-merge: yes'))
        classic = f'api --hostname github.com {BRANCH}'
        before = self.replies[classic]
        self.replies[classic] = {'name': BASE, 'protected': True, 'protection': {'enabled': True,
                                 'required_status_checks': {'enforcement_level': 'everyone', 'contexts': ['docs']}}}
        adopted = self.run_cli('run_control.py', 'adopt', str(card), '--source', 'confirmed grant',
                               '--merge-pr', '12')
        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        self.replies[classic] = before
        self.calls.write_text('')
        result = self.run_cli('run_control.py', 'merge', str(card), '--merge-pr', '12', '--head', SHA,
                              '--method', 'squash', '--expect', '1')
        self.assertEqual((result.returncode, result.stderr.strip()),
                         (2, f'waiting-on-user: no required checks apply to {REPO}:{BASE}'))
        self.assertEqual([json.loads(row) for row in self.calls.read_text().splitlines()],
                         [['api', '--hostname', 'github.com', PULL],
                          ['api', '--hostname', 'github.com', BRANCH],
                          ['api', '--hostname', 'github.com', '--paginate', '--slurp', RULES]])
        self.assertIn('auto-merge: yes\n', card.read_text())
        status = self.run_cli('run_control.py', 'status', str(card))
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual((json.loads(status.stdout)['terminal'], json.loads(status.stdout)['revision']), ('', 1))

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
        card = support.run_card(self.root / 'autopilot.md', phase='pr')
        self.assertEqual(self.run_cli('run_control.py', 'adopt', str(card), '--source', 'confirmed').returncode, 0)
        self.assertEqual(self.run_cli('run_control.py', 'end', str(card), '--source', 'delivery proof',
                                      '--terminal', 'delivered', '--reason', 'delivered',
                                      '--expect', '1', '--request-id', 'delivered').returncode, 0)
        before = card.read_bytes()
        native = self.root / 'native.jsonl'
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        native.write_text('\n'.join(map(json.dumps, [
            {'type': 'session_meta', 'payload': {'id': 'host-1'}},
            {'type': 'response_item', 'timestamp': stamp, 'payload': {
                'role': 'user', 'content': 'autopilot --take-over --auto-merge'}},
        ])))
        result = self.run_cli('run_control.py', 'resume', str(card), '--native', str(native), '--runtime', 'codex',
                              '--expect', '2', '--request-id', 'resume', '--reason', 'user decision',
                              '--merge-pr', '12')
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
                          ['api', '--hostname', 'github.com', BRANCH],
                          ['api', '--hostname', 'github.com', '--paginate', '--slurp', RULES]])


if __name__ == '__main__':
    unittest.main()
