"""Test authorization repairs at the run card and managed-control entry points."""
from pathlib import Path
import json
import select
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import quote

import support
import test_pr_safety
from t3_intent_fixture import T3Read


class AuthorityRepair(unittest.TestCase):
    def test_caller_cannot_select_the_t3_listener(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            card = support.run_card(root / 'autopilot.md', confirmed='no', session='bound-thread', mode='full',
                                    **{'dispatch-effort': 'medium', 'review-effort': 'medium'})
            env = {'T3CODE_HOME': str(root / '.t3')}
            host = T3Read(self, root, env, 'bound-thread')
            script = support.SCRIPTS / 'autopilot_card.py'
            args = ('confirm', str(card), '--words', 'Confirm this card', '--confirmed-by', 'human')
            trusted = host.command(script, *args)
            preview = subprocess.run(trusted, env=env, capture_output=True, text=True, timeout=10)
            host.accept(preview, 'Confirm this card')
            original = card.read_bytes()
            reads = list(host.reads)
            result = subprocess.run([sys.executable, '-B', str(script), *args], env=env,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('waiting-on-user:', result.stderr)
            self.assertEqual(card.read_bytes(), original)
            self.assertEqual(host.reads, reads)

            runtime = host.runtime.read_bytes()
            host.runtime.write_text(json.dumps(dict(json.loads(runtime), pid=host.pid + 1)))
            result = subprocess.run(trusted, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('listener does not belong to the T3 runtime', result.stderr)
            host.runtime.write_bytes(runtime)
            host.runtime.parent.chmod(0o775)
            result = subprocess.run(trusted, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('runtime directory is not trusted', result.stderr)
            host.runtime.parent.chmod(0o755)
            self.assertEqual(card.read_bytes(), original)
            self.assertEqual(host.reads, reads)

            accepted = subprocess.run(trusted, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertIn('confirmation-item: fixture-item-1\n', card.read_text())

    def test_paginated_intent_is_complete_or_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            card = support.run_card(root / 'autopilot.md', confirmed='no', session='bound-thread', mode='full',
                                    **{'dispatch-effort': 'medium', 'review-effort': 'medium'})
            env = {}
            host = T3Read(self, root, env, 'bound-thread')
            script = support.SCRIPTS / 'autopilot_card.py'
            args = ('confirm', str(card), '--words', 'Confirm this card', '--confirmed-by', 'human')

            def confirm():
                return subprocess.run(host.command(script, *args), env=env, capture_output=True, text=True, timeout=10)

            host.accept(confirm(), 'Confirm this card')
            proof = dict(host.answer['items'][0], position=210)
            newer = [dict(host.item('history', item=f'newer-{pos}', run='older-run'), position=pos)
                     for pos in range(209, 110, -1)]
            older = [dict(host.item('history', run='older-run', item=f'older-{pos}'), position=pos)
                     for pos in range(110, 10, -1)]
            page = dict(host.answer, thread=dict(host.answer['thread'], itemCount=201))
            first = dict(page, items=[proof, *newer], hasMore=True, earlierPosition=111)
            second = dict(page, items=older, hasMore=True, earlierPosition=11)
            third = dict(page, items=[dict(host.item('history', run='older-run', item='start'), position=2)])
            original = card.read_bytes()
            host.answer = first
            host.pages = {111: dict(second, items=[dict(proof, position=110, itemId='other-user-item'), *older[1:]]),
                          11: third}
            refused = confirm()
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
            self.assertIn('ambiguous', refused.stderr)
            self.assertEqual(card.read_bytes(), original)
            for page, detail in (
                (dict(second, earlierPosition=110), 'pagination'),
                (dict(second, items=[]), 'pagination'),
                (dict(second, thread=dict(second['thread'], activeRunId='new-run')), 'bound active run'),
                (dict(second, recentRuns=[dict(second['recentRuns'][0], requestedAt='2026-01-01T00:00:00Z')]),
                 'changed during pagination'),
            ):
                with self.subTest(detail=detail):
                    host.pages = {111: page, 11: third}
                    refused = confirm()
                    self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
                    self.assertIn(detail, refused.stderr)
                    self.assertEqual(card.read_bytes(), original)

            host.malformed = True
            refused = confirm()
            self.assertEqual(refused.returncode, 2, refused.stderr)
            self.assertIn('waiting-on-user: human intent', refused.stderr)
            self.assertEqual(card.read_bytes(), original)
            host.malformed = False
            thread = env.pop('T3CODE_THREAD_ID')
            for command in (args, ('take-over', str(card))):
                refused = subprocess.run(host.command(script, *command), env=env,
                                         capture_output=True, text=True, timeout=10)
                self.assertEqual(refused.returncode, 2, refused.stderr)
                self.assertIn('no current T3 thread is bound', refused.stderr)
                self.assertEqual(card.read_bytes(), original)
            env['T3CODE_THREAD_ID'] = thread

            host.answer = dict(first, items=[dict(proof, runId='older-run', itemId='previous-user-item'), *newer])
            host.pages = {111: second, 11: dict(third, items=[dict(proof, position=2)])}
            host.reads.clear()
            accepted = confirm()
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertIn('confirmation-item: fixture-item-1\n', card.read_text())
            self.assertEqual(list(dict.fromkeys(call['arguments'].get('beforePosition') for call in host.reads)),
                             [None, 111, 11])

    def test_existing_pr_destination_is_bound(self):
        github = test_pr_safety.PRSafety()
        github.setUp()
        self.addCleanup(github.doCleanups)
        card = support.run_card(github.root / 'autopilot.md', phase='pr')
        github.t3 = T3Read(self, github.root, github.env)
        self.assertEqual(github.run_cli('run_control.py', 'adopt', str(card), '--source', 'confirmed').returncode, 0)
        record = card.with_name('autopilot-control.json')
        before = card.read_bytes(), record.read_bytes()
        pull = f'api --hostname github.com {test_pr_safety.PULL}'
        original = github.replies[pull]
        other = dict(original, base=dict(original['base'], ref='release-two'))
        for name in (test_pr_safety.BASE, 'release-two'):
            slug = quote(name, safe='')
            github.replies[f'api --hostname github.com repos/{test_pr_safety.REPO}/branches/{slug}'] = {
                'name': name, 'protected': True, 'protection': {'enabled': True,
                    'required_status_checks': {'enforcement_level': 'everyone', 'contexts': ['test']}}}
            github.replies[f'api --hostname github.com --paginate --slurp repos/{test_pr_safety.REPO}/rules/branches/{slug}'] = [[]]

        def resume():
            return github.run_cli('run_control.py', 'resume', str(card), '--expect', '1', '--request-id', 'scoped',
                                  '--reason', 'user decision', '--merge-pr', '12')

        preview = resume()
        github.t3.accept(preview, 'autopilot --take-over --auto-merge')
        github.replies[pull] = other
        refused = resume()
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn('not a fresh matching human response', refused.stderr)
        self.assertEqual((card.read_bytes(), record.read_bytes()), before)
        self.assertIn(f'"base": "{test_pr_safety.BASE}"', preview.stderr)

        github.calls.write_text('')
        github.replies[pull] = {'sequence': [original, other]}
        refused = resume()
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn('changed base during verification', refused.stderr)
        self.assertEqual((card.read_bytes(), record.read_bytes()), before)

        github.replies[pull] = original
        accepted = resume()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        state = json.loads(record.read_bytes())
        self.assertEqual(state['merge_grant'], {'repo': test_pr_safety.REPO, 'pr': 12,
                                                'base': test_pr_safety.BASE, 'authorization': 'scoped'})
        self.assertTrue(all(json.loads(line)[0] == 'api' for line in github.calls.read_text().splitlines()))

    def test_legacy_phase_writes_use_the_card_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            card = support.run_card(root / 'autopilot.md', confirmed='no', session='bound-thread', mode='full',
                                    phase='align', **{'dispatch-effort': 'medium', 'review-effort': 'medium'})
            env = {}
            host = T3Read(self, root, env, 'bound-thread')
            script = support.SCRIPTS / 'autopilot_card.py'
            confirm = host.command(script, 'confirm', card, '--words', 'Confirm this card', '--confirmed-by', 'human')
            host.accept(subprocess.run(confirm, env=env, capture_output=True, text=True, timeout=10), 'Confirm this card')
            reading, release = threading.Event(), threading.Event()

            def hold_read():
                reading.set()
                release.wait(6)

            host.on_read = hold_read
            phase_probe = '''
import fcntl
import runpy
import sys
flock = fcntl.flock
def observe_lock(file, operation):
    print('waiting for card lock', flush=True)
    return flock(file, operation)
fcntl.flock = observe_lock
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
'''
            with subprocess.Popen(confirm, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as writer:
                try:
                    self.assertTrue(reading.wait(5), 'confirmation never reached the host read')
                    with subprocess.Popen([sys.executable, '-B', '-c', phase_probe, str(script), 'phase', str(card), 'refine'],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as phase:
                        self.assertTrue(select.select([phase.stdout], [], [], 5)[0], 'phase did not attempt the write')
                        phase.stdout.readline()
                        release.set()
                        _, phase_error = phase.communicate(timeout=5)
                        self.assertEqual(phase.returncode, 0, phase_error)
                finally:
                    release.set()
                _, writer_error = writer.communicate(timeout=5)
                self.assertEqual(writer.returncode, 0, writer_error)

            view = card.read_text()
            self.assertIn('phase: refine\n', view)
            self.assertIn('confirmation-item: fixture-item-1\n', view)
            control = support.SCRIPTS / 'run_control.py'
            adopted = subprocess.run([sys.executable, str(control), 'adopt', str(card), '--source', 'confirmed'],
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(adopted.returncode, 0, adopted.stderr)
            self.assertEqual(json.loads(adopted.stdout)['revision'], 1)
            updated = subprocess.run([sys.executable, str(script), 'phase', str(card), 'align'],
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(updated.returncode, 0, updated.stderr)
            state = json.loads(card.with_name('autopilot-control.json').read_bytes())
            self.assertEqual(state['revision'], 2)
            self.assertEqual(state['card_text'], card.read_text())
            self.assertIn('confirmation-item: fixture-item-1\n', state['card_text'])
            self.assertIn('phase: align\n', state['card_text'])


if __name__ == '__main__':
    unittest.main()
