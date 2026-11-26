#!/usr/bin/env python3
"""Taking over an autopilot run card ends the old run's merge grant and keeps its terminal state as history."""
import datetime
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support
from t3_intent_fixture import T3Read

COMMAND = support.script('autopilot_card.py')
card = support.load_script('autopilot_card.py', 'autopilot_card')
life = support.load_script('worker_lifecycle.py', 'worker_lifecycle')
CARD = """# run — autopilot run card

## Goal
Ship it.

## State
confirmed: 2026-10-25 "go"
session: host-1
intent-thread: host-1
auto-merge: yes
dispatch-effort: low
review-effort: xhigh
phase: pr
terminal: merged
history: waiting-on-user 2026-10-24
evidence: abc123
resume: `autopilot --take-over`
"""


class TakeOver(unittest.TestCase):
    def test_a_repeated_state_key_reads_the_first_line_that_the_writer_edits(self):
        text = '## State\nterminal: delivered\nterminal: blocked\n'
        self.assertEqual(card.field(text, 'terminal'), 'delivered')
        cleared = card.set_field(text, 'terminal', '')
        self.assertEqual(cleared, '## State\nterminal:\nterminal: blocked\n')
        self.assertEqual(card.field(cleared, 'terminal'), '')

    def take_over(self, *flags, card=CARD, sessions=None, code=0, error=None):
        """Take over a copy of `card` from only `sessions` (default: Pi host-2), expecting `code` and optional `error`."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'autopilot.md'
        path.write_text(card)
        env = support.without_host_sessions()
        env.update({'PI_SESSION_ID': 'host-2'} if sessions is None else sessions)
        for name in ('T3CODE_THREAD_ID', 'T3_MCP_URL', 'T3_MCP_BEARER_TOKEN'):
            env.pop(name, None)
        args = ('take-over', str(path), *flags)
        host = T3Read(self, Path(tmp.name), env) if code == 0 else None

        def run():
            command = host.command(COMMAND, *args) if host else [sys.executable, '-B', str(COMMAND), *args]
            return subprocess.run(command, capture_output=True, text=True, timeout=20, env=env)

        if host:
            host.accept(run(), 'autopilot --take-over')
        result = run()
        self.assertEqual(result.returncode, code, result.stderr)
        if error is not None:
            self.assertIn(error, result.stderr)
        return '\n'.join(line for line in path.read_text().split('\n') if not line.startswith('take-over-item:'))

    def test_legacy_take_over_refuses_a_new_merge_grant_and_preserves_history(self):
        self.assertEqual(self.take_over('--auto-merge', code=2, error='requires a managed run'), CARD)
        today = datetime.date.today().isoformat()
        self.assertEqual(self.take_over(), CARD.replace('auto-merge: yes', 'auto-merge: no')
                         .replace('session: host-1', 'session: host-2, host-1')
                         .replace('terminal: merged', 'terminal:')
                         .replace('history: waiting-on-user 2026-10-24',
                                  f'history: waiting-on-user 2026-10-24; merged {today}'))

    def test_an_interrupted_take_over_leaves_the_card_whole(self):
        # Audit A1: the card holds the unfinished run at stop; a write cut short must not leave it empty.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'autopilot.md'
        path.write_text(CARD)
        env = support.without_host_sessions()
        env['PI_SESSION_ID'] = 'host-2'
        host = T3Read(self, Path(tmp.name), env)
        argv = ['autopilot_card.py', 'take-over', str(path)]
        preview = subprocess.run(host.command(COMMAND, *argv[1:]), env=env,
                                 capture_output=True, text=True, timeout=20)
        host.accept(preview, 'autopilot --take-over')
        with (host.trust(), patch.object(sys, 'argv', argv), patch.dict(os.environ, env, clear=True),
              patch('os.replace', side_effect=OSError('interrupted')), self.assertRaises(OSError)):
            card.main()
        self.assertEqual(path.read_text(), CARD)
        self.assertFalse(list(path.parent.glob('*.tmp')))

    def test_a_goal_that_quotes_state_lines_is_left_alone(self):
        # Audit A10: the State section is the card's state; the same words in the Goal are prose, never edited.
        example = ('Ship it. A take-over sets:\n```\nterminal: merged\nhistory: none\nauto-merge: yes\n'
                   'dispatch-effort: low\nreview-effort: high\n```\n')
        goal = CARD.replace('Ship it.\n', example)
        text = self.take_over(card=goal)
        self.assertEqual(text.split('## State')[0], goal.split('## State')[0])
        self.assertIn('\nauto-merge: no\n', text.split('## State')[1])

    def test_a_card_with_state_lines_only_outside_the_state_section_is_refused(self):
        # Audit A10: a line the State section lacks is not found in the Goal either.
        without = CARD.replace('## State', '## Notes')
        self.assertEqual(self.take_over(card=without, code=1, error='## State'), without)

    def test_a_split_flag_changes_the_level_of_its_own_step(self):
        # Rule: a take-over may set one step's level from its split flag.
        text = self.take_over('--review-effort', 'max')
        self.assertIn('dispatch-effort: low\n', text)
        self.assertIn('review-effort: max\n', text)

    def test_effort_sets_both_levels(self):
        # Rule: `--effort` sets the dispatch and the review levels together.
        text = self.take_over('--effort', 'high')
        self.assertIn('dispatch-effort: high\n', text)
        self.assertIn('review-effort: high\n', text)

    def test_a_split_flag_overrides_effort_for_its_step(self):
        # Rule: the split flag wins over `--effort` for its own step and leaves the other at it.
        text = self.take_over('--effort', 'xhigh', '--dispatch-effort', 'low')
        self.assertIn('dispatch-effort: low\n', text)
        self.assertIn('review-effort: xhigh\n', text)

    def test_a_card_level_line_that_is_not_a_level_is_refused(self):
        # Rule: both card level lines must hold one of the five levels, blank or misspelled alike.
        lines = {'dispatch-effort': ('dispatch-effort: low', 'dispatch-effort:'),
                 'review-effort': ('review-effort: xhigh', 'review-effort: hgih')}
        for key, (current, broken) in lines.items():
            with self.subTest(line=broken):
                card = CARD.replace(current, broken)
                self.assertEqual(self.take_over(card=card, code=1, error=key), card)

    def test_a_level_outside_the_five_is_refused_and_changes_nothing(self):
        # Rule: only low, medium, high, xhigh and max are levels; anything else fails without touching the card.
        self.assertEqual(self.take_over('--effort', 'ultra', code=2, error='ultra'), CARD)

    def test_a_card_without_the_level_lines_is_refused(self):
        # Rule: a card always records both levels, so one without those lines has no reader here.
        without = CARD.replace('dispatch-effort: low\n', '').replace('review-effort: xhigh\n', '')
        self.assertEqual(self.take_over(card=without, code=1, error='dispatch-effort'), without)

    def test_a_card_that_never_ended_keeps_its_history_and_clears_its_merge_grant(self):
        open_card = CARD.replace('terminal: merged', 'terminal:').replace('history: waiting-on-user 2026-10-24', 'history:')
        text = self.take_over(card=open_card)
        self.assertEqual(text, open_card.replace('auto-merge: yes', 'auto-merge: no').replace('session: host-1',
                                                                                             'session: host-2, host-1'))

    def test_a_first_ended_state_starts_the_history(self):
        today = datetime.date.today().isoformat()
        first = CARD.replace('history: waiting-on-user 2026-10-24', 'history: <earlier terminal states>')
        self.assertIn(f'history: merged {today}\n', self.take_over(card=first))

    def test_a_take_over_from_a_shell_with_no_host_session_is_refused(self):
        # Rule (card-confirm): a run nobody can tie to a session has no panel and no stop hold.
        self.assertEqual(self.take_over(sessions={}, code=2, error='no current T3 thread is bound'), CARD)

    def test_a_take_over_names_each_host_session_and_refuses_a_card_without_a_session_line(self):
        # Review (Sonnet, card-confirm): a take-over wrote no session on a card that had no `session:` line, so the
        # resumed run stayed untied; and a host started from another host's shell is both sessions'.
        text = self.take_over(sessions={'CLAUDE_CODE_SESSION_ID': 'claude-1', 'CODEX_SESSION_ID': 'codex-2'})
        self.assertIn('\nsession: claude-1, codex-2, host-1\n', text)
        without = CARD.replace('session: host-1\n', '')
        self.assertEqual(self.take_over(card=without, code=1), without)


DRAFT = """# run — autopilot run card

## Goal
Ship it. The user wrote:
session: theirs, quoted, not this card's

## State
confirmed: no
session: <each host session ID this shell exports, comma-separated; `confirm` and `take-over` rewrite it>
auto-merge: no
mode: full
board: local
dispatch-effort: medium
review-effort: high
phase: refine
terminal:
history:
evidence:
timer:
resume: `autopilot --take-over`
"""
UNNAMED = 'session: <each host session ID this shell exports, comma-separated; `confirm` and `take-over` rewrite it>'
OK_PROBE = 'publish-probe: ok 2026-11-27T09:00:00Z acme/widgets'



class Confirm(unittest.TestCase):
    def confirm(self, *args, card=DRAFT, sessions=None, code=0, scratch=None):
        """Run `confirm` on a copy of `card` at `.scratch/run/autopilot.md`, beside the files `scratch` maps from their
        path under `.scratch` to their text, from a shell exporting only `sessions` (a SESSIONS name to its value); the
        card's text after it, and what it printed to stderr."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / '.scratch/run/autopilot.md'
        path.parent.mkdir(parents=True)
        path.write_text(card)
        for name, text in (scratch or {}).items():
            (Path(tmp.name) / '.scratch' / name).write_text(text)
        env = support.without_host_sessions()
        env.update(sessions or {})
        for name in ('T3CODE_THREAD_ID', 'T3_MCP_URL', 'T3_MCP_BEARER_TOKEN'):
            env.pop(name, None)
        host = None
        args_with_card = ('confirm', str(path), *args)

        def run():
            command = host.command(COMMAND, *args_with_card) if host else [sys.executable, '-B', str(COMMAND), *args_with_card]
            return subprocess.run(command, capture_output=True, text=True, timeout=20, env=env)

        if code == 0 and 'mode: full' in card and 'relayed(' not in (card + ' '.join(args)):
            thread = next(iter(sessions.values()))
            host = T3Read(self, Path(tmp.name), env, thread)
            host.accept(run(), ' '.join(args[args.index('--words') + 1].split()))
        result = run()
        self.assertEqual(result.returncode, code, result.stderr)
        return path.read_text(), result.stderr

    def test_full_confirmation_records_the_verified_provenance_words_and_host_sessions(self):
        # Live (jose, Pi): a hand-written card named no session, so neither the panel nor the stop hold found its run.
        today = datetime.date.today().isoformat()
        confirmed = DRAFT.replace('confirmed: no', f'confirmed: {today} "go ahead, inline"\nconfirmed-by: human')
        text, _ = self.confirm('--words', 'go ahead, inline', sessions={'PI_SESSION_ID': 'pi-1'})
        self.assertEqual(text, confirmed.replace(UNNAMED, 'session: pi-1')
                         + 'confirmation-item: fixture-item-1\nintent-thread: pi-1\n')
        # A host started from another host's shell exports both, and the stop hook counts each.
        text, _ = self.confirm('--words', 'go ahead, inline',
                               sessions={'CODEX_SESSION_ID': 'codex-2', 'CLAUDE_CODE_SESSION_ID': 'claude-1'})
        self.assertEqual(text, confirmed.replace(UNNAMED, 'session: claude-1, codex-2')
                         + 'confirmation-item: fixture-item-1\nintent-thread: codex-2\n')
        # Review (Sonnet): words on several lines wrote their own `session:` line into the State; they are one line.
        text, _ = self.confirm('--words', 'go ahead,\n\nsession: mine\u2028  inline', sessions={'PI_SESSION_ID': 'pi-1'})
        self.assertEqual(text, DRAFT.replace('confirmed: no', f'confirmed: {today} "go ahead, session: mine inline"\nconfirmed-by: human')
                         .replace(UNNAMED, 'session: pi-1') + 'confirmation-item: fixture-item-1\nintent-thread: pi-1\n')

    def test_confirmation_records_explicit_human_or_named_relay_provenance(self):
        today = datetime.date.today().isoformat()
        session = {'PI_SESSION_ID': 'pi-1'}
        confirmed = DRAFT.replace('confirmed: no', f'confirmed: {today} "go"').replace(UNNAMED, 'session: pi-1')
        for source in ('human', 'relayed(Grok Bot)'):
            with self.subTest(source=source):
                text, _ = self.confirm('--words', 'go', '--confirmed-by', source, sessions=session)
                expected = confirmed.replace('session: pi-1', f'confirmed-by: {source}\nsession: pi-1')
                recorded = ('confirmation-item: fixture-item-1\nintent-thread: pi-1'
                            if source == 'human' else 'delivery: pr-only')
                self.assertEqual(text, expected + recorded + '\n')
        relay = DRAFT.replace('confirmed: no', 'confirmed: no\nconfirmed-by: relayed(Grok Bot)')
        text, _ = self.confirm('--words', 'go', card=relay, sessions=session)
        self.assertEqual(text, confirmed.replace('session: pi-1', 'confirmed-by: relayed(Grok Bot)\nsession: pi-1')
                         + 'delivery: pr-only\n')

    def test_confirmation_rejects_malformed_present_authority_fields_without_rewriting(self):
        session = {'PI_SESSION_ID': 'pi-1'}
        for field, reason in (
                ('confirmed-by:', 'Invalid confirmed-by: expected human or relayed(agent)'),
                ('confirmed-by: relayed()', 'Invalid confirmed-by: expected human or relayed(agent)'),
                ('confirmed-by: <source>', 'Invalid confirmed-by: expected human or relayed(agent)'),
                ('confirmed-by: human\nconfirmed-by: relayed(Grok Bot)', 'Duplicate confirmed-by in card State'),
                ('delivery:', 'Invalid delivery: expected pr-only'),
                ('delivery: local', 'Invalid delivery: expected pr-only'),
                ('delivery: pr-only\ndelivery: pr-only', 'Duplicate delivery in card State')):
            with self.subTest(field=field):
                card = DRAFT.replace('confirmed: no', f'confirmed: no\n{field}')
                self.assertEqual(self.confirm('--words', 'go', card=card, sessions=session, code=1),
                                 (card, reason + '\n'))
        self.assertEqual(self.confirm('--words', 'go', '--confirmed-by', 'relayed()', sessions=session, code=1),
                         (DRAFT, 'Invalid confirmed-by: expected human or relayed(agent)\n'))

    def test_a_direct_card_is_confirmed_by_rule_from_the_goal_and_a_card_without_a_mode_line_is_refused(self):
        # Rule (outcome B): a direct run asks nothing, so its confirmation is the rule applied to the goal the user typed.
        today = datetime.date.today().isoformat()
        direct = DRAFT.replace('mode: full', 'mode: direct')
        text, _ = self.confirm('--words', 'fix the typo', card=direct, sessions={'PI_SESSION_ID': 'pi-1'})
        self.assertEqual(text, direct.replace('confirmed: no', f'confirmed: {today} by rule "fix the typo"')
                         .replace(UNNAMED, 'session: pi-1'))
        # The stop hook and the panel read a confirmation by rule as confirmed.
        self.assertTrue(life.confirmed(life.state_fields(text)))
        # A full card keeps the user's own words.
        self.assertIn(f'confirmed: {today} "ok"', self.confirm('--words', 'ok', sessions={'PI_SESSION_ID': 'pi-1'})[0])
        lacking = DRAFT.replace('mode: full\n', '')
        text, error = self.confirm('--words', 'go', card=lacking, sessions={'PI_SESSION_ID': 'pi-1'}, code=1)
        self.assertEqual((text, error.split(':', 1)[0]), (lacking, "the card's State lacks mode"))
        odd = DRAFT.replace('mode: full', 'mode: both')
        self.assertIn('`mode:`', self.confirm('--words', 'go', card=odd, sessions={'PI_SESSION_ID': 'pi-1'}, code=1)[1])

    def test_confirming_refuses_a_card_missing_a_state_line_empty_words_or_no_host_session(self):
        # Live (jose, Pi): its State held only phase, terminal and history. The lines expected are the run-card format's.
        run_card = COMMAND.resolve().parents[1] / 'skills/autopilot/run-card.md'
        hand_written = '# maven-warp-closeout\n\n## Goal\nx\n\n## State\nphase: refine\nterminal:\nhistory:\n- 10:04 HOLD\n'
        text, error = self.confirm('--words', 'go', card=hand_written, sessions={'PI_SESSION_ID': 'pi-1'}, code=1)
        self.assertEqual((text, error.strip()), (hand_written, "the card's State lacks confirmed, session, auto-merge, "
                         'mode, board, dispatch-effort, review-effort, evidence, timer, resume: write each line of the run '
                         "card's "
                         f'State ({run_card}), then confirm'))
        # Every line of the format's State, as run-card.md writes it.
        template = run_card.read_text().split('\n## State\n', 1)[1].split('```', 1)[0]
        keys = [line.split(':', 1)[0] for line in template.splitlines() if ':' in line]
        self.assertEqual(len(keys), 13)
        for key in keys:
            with self.subTest(missing=key):
                head, state = DRAFT.split('## State\n')  # The Goal's quoted `session:` line stays.
                lacking = head + '## State\n' + '\n'.join(line for line in state.split('\n') if not line.startswith(f'{key}:'))
                text, error = self.confirm('--words', 'go', card=lacking, sessions={'PI_SESSION_ID': 'pi-1'}, code=1)
                self.assertEqual((text, error.split(':', 1)[0]), (lacking, f"the card's State lacks {key}"))
        self.assertEqual(self.confirm('--words', ' ', sessions={'PI_SESSION_ID': 'pi-1'}, code=1),
                         (DRAFT, "confirm takes --words: the user's words that confirmed this card\n"))
        text, error = self.confirm('--words', 'go', sessions={}, code=2)
        self.assertEqual(text, DRAFT)
        self.assertIn('waiting-on-user: human intent', error)
        self.assertIn('no current T3 thread is bound', error)
        self.assertIn('confirm a fresh run card in T3', error)
        self.assertNotIn('Continue PR-only work', error)

    def test_confirming_refuses_a_brief_that_does_not_name_the_crew_profile(self):
        # Rule: once the project has `.scratch/crew.md`, a card is confirmed only when its brief's Crew section names it.
        profile = {'crew.md': '# Crew profile\n\n## first\n| calc | builder |\n'}
        session = {'PI_SESSION_ID': 'pi-1'}
        brief = '# run\n\n## HOW\nx\n\n## Crew\n| builder | none | calc | no |\n'
        text, error = self.confirm('--words', 'go', sessions=session, code=1,
                                   scratch=dict(profile, **{'run/alignment.md': brief}))
        self.assertEqual(text, DRAFT)
        self.assertIn("`## Crew` section does not name", error)
        self.assertIn('crew.md', error)
        # Naming the profile elsewhere in the brief is not starting the crew from it.
        elsewhere = brief.replace('## HOW\nx', '## HOW\nsee .scratch/crew.md')
        self.confirm('--words', 'go', sessions=session, code=1, scratch=dict(profile, **{'run/alignment.md': elsewhere}))
        cited = brief + 'From `.scratch/crew.md`: calc.\n'
        self.assertIn('confirmed: ', self.confirm('--words', 'go', sessions=session,
                                                  scratch=dict(profile, **{'run/alignment.md': cited}))[0])
        # A project with no profile yet has nothing to start from.
        self.confirm('--words', 'go', sessions=session, scratch={'run/alignment.md': brief})


class PublishProbe(unittest.TestCase):
    """`publish_probe.py` records on the run card whether the run can push its branch and open a pull request."""
    LINE = r'publish-probe: (ok|failed) \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ (.+)'

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop-probe-')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.repo, self.remote, self.bin = root / 'repo', root / 'remote.git', root / 'bin'
        self.env = dict(support.without_host_sessions(), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                        GH_LOG=str(root / 'gh.log'), PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}')
        self.git('init', '-q', '-b', 'main', str(self.repo), cwd=root)
        self.git('init', '-q', '--bare', str(self.remote), cwd=root)
        self.git('-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-q', '--allow-empty', '-m', 'one')
        # origin names a GitHub repository, while pushes go to a local bare repository: no network is reachable.
        self.git('remote', 'add', 'origin', 'https://github.com/acme/widgets.git')
        self.git('config', 'remote.origin.pushurl', str(self.remote))
        self.bin.mkdir()
        gh = self.bin / 'gh'
        gh.write_text('#!/bin/sh\necho "$@" >> "$GH_LOG"\nprintf \'%s\' "$GH_BODY"\n')
        gh.chmod(0o755)
        self.card = support.run_card(self.repo / '.scratch' / 'run' / 'autopilot.md', phase='refine')

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.repo, env=self.env, check=True, capture_output=True,
                              text=True, timeout=30).stdout

    def probe(self, permission=True, code=0):
        """Run the probe on the card against a `gh` that grants write when `permission`; the card's probe line."""
        env = dict(self.env, GH_BODY=json.dumps({'permissions': {'push': permission}}))
        result = subprocess.run([sys.executable, '-B', str(support.script('publish_probe.py')), str(self.card)],
                                env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, code, result.stderr)
        lines = [line for line in self.card.read_text().splitlines() if line.startswith('publish-probe:')]
        self.assertEqual(len(lines), 1, self.card.read_text())
        self.assertIn(lines[0], result.stdout)
        return re.fullmatch(self.LINE, lines[0])

    def test_the_probe_records_ok_or_the_failing_reason_without_network(self):
        # Rule: a run learns at its start whether it can push its branch and open a PR; the card says which, and why not.
        before = self.card.read_text()
        found = self.probe()
        self.assertEqual((found[1], found[2]), ('ok', 'acme/widgets'))
        self.assertEqual(self.card.read_text().replace(f'{found[0]}\n', ''), before)
        # A dry run pushes nothing, and gh is only asked to read.
        self.assertEqual(self.git('for-each-ref', cwd=self.remote), '')
        self.assertEqual(Path(self.env['GH_LOG']).read_text().split(), ['api', '--hostname', 'github.com', 'repos/acme/widgets'])

        found = self.probe(permission=False, code=1)
        self.assertEqual((found[1], found[2]), ('failed', 'gh reports no write permission on acme/widgets'))

        self.git('config', 'remote.origin.pushurl', str(self.remote.with_name('missing.git')))
        found = self.probe(code=1)
        self.assertEqual(found[1], 'failed')
        self.assertTrue(found[2].startswith('git push --dry-run of HEAD to origin failed: '), found[2])

        self.git('remote', 'set-url', 'origin', str(self.remote))
        self.git('config', '--unset', 'remote.origin.pushurl')
        found = self.probe(code=1)
        self.assertEqual((found[1], found[2]), ('failed', 'origin is not a GitHub repository'))

    def test_the_probe_skips_push_hooks_and_keeps_a_configured_ssh_command(self):
        # Rule: the probe changes nothing locally or remotely and reaches the remote the way the user's pushes do.
        marker = self.bin.parent / 'marks'
        hook = self.repo / '.git' / 'hooks' / 'pre-push'
        hook.write_text(f'#!/bin/sh\necho hook >> "{marker}"\nexit 1\n')
        hook.chmod(0o755)
        ssh = self.bin / 'configured-ssh'
        # Runs the remote command on this machine, ignoring the host, and records that it was the transport.
        ssh.write_text(f'#!/bin/sh\necho ssh >> "{marker}"\nshift\nPATH="$PATH:$(git --exec-path)" exec sh -c "$@"\n')
        ssh.chmod(0o755)
        self.git('config', 'remote.origin.pushurl', f'ssh://remote.invalid{self.remote}')
        self.git('config', 'core.sshCommand', str(ssh))
        for name in ('GIT_SSH_COMMAND', 'GIT_SSH'):
            self.env.pop(name, None)
        env = dict(self.env, GH_BODY=json.dumps({'permissions': {'push': True}}))
        result = subprocess.run([sys.executable, '-B', str(support.script('publish_probe.py')), str(self.card)],
                                env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(set(marker.read_text().split()) if marker.exists() else set(), {'ssh'}, result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_probe_records_on_a_managed_card_through_its_control_record(self):
        # Rule: a managed card's State changes only through run_control, so the probe line must survive its views.
        control = support.load_script('run_control.py')
        control.adopt(self.card, 'confirmed run')
        found = self.probe()
        self.assertEqual(found[1], 'ok')
        state = control.read(self.card)
        self.assertEqual(state['revision'], 2)
        self.assertEqual(state['card_text'], self.card.read_text())
        self.assertIn(f'{found[0]}\n', state['card_text'])


class Phase(unittest.TestCase):
    """`phase` sets the run's phase; dispatch and pr need the tickets on the board the card names."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop-phase-')
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(self.repo)], check=True)
        self.card = self.repo / '.scratch' / 'run' / 'autopilot.md'
        self.card.parent.mkdir(parents=True)

    def phase(self, phase, board='local', code=0, phase_line=True):
        """Set `phase` on a card naming `board` (None: no board line), with or without a `phase:` line; the card
        after, and stderr."""
        text = DRAFT.replace('confirmed: no', 'confirmed: 2026-11-06 "go"').replace(UNNAMED, 'session: host-1')
        text = text.replace('board: local\n', f'board: {board}\n' if board is not None else '')
        # A card that lost its phase line has no probe line either; dispatch and pr need a probe that says ok.
        text = text.replace('phase: refine\n', f'phase: refine\n{OK_PROBE}\n' if phase_line else '')
        self.card.write_text(text)
        result = subprocess.run([sys.executable, '-B', str(COMMAND), 'phase', str(self.card), phase],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stderr)
        return self.card.read_text(), result.stderr

    def ticket(self, number, blocked_by=''):
        support.ticket_file(self.repo, f'run/{number}', blocked_by=blocked_by, touches='')

    def test_a_phase_is_one_of_the_four_and_dispatch_needs_the_local_boards_tickets(self):
        # Live (jose, Pi): the run set `phase: dispatch (inline)` by hand with no ticket on any board, so the panel
        # had nothing to count. A phase is set by this command, and dispatch and pr only with tickets to work.
        text, _ = self.phase('align')
        self.assertIn('\nphase: align\n', text)
        self.assertEqual(self.phase('dispatch (inline)', code=2)[0], text.replace('phase: align', 'phase: refine'))
        new = shlex.join(['python3', str(COMMAND.resolve().parent / 'next_tickets.py'), '--repo', str(self.repo), 'run',
                          '--new', '<title>'])
        # Review (Sonnet): a note in the board's folder is no ticket.
        (self.repo / '.scratch' / 'run' / 'issues').mkdir()
        (self.repo / '.scratch' / 'run' / 'issues' / 'README.md').write_text('# notes\nticket: none yet\n')
        # Review (Codex): a front matter that never closes is no ticket either.
        (self.repo / '.scratch' / 'run' / 'issues' / '00-draft.md').write_text('---\nticket: run/00\n')
        for phase in ('dispatch', 'pr'):
            with self.subTest(phase=phase):
                text, error = self.phase(phase, code=1)
                self.assertEqual(error.strip(), f'the local board of run has no tickets: write each with `{new}`, then '
                                 f'set the phase')
                self.assertIn('\nphase: refine\n', text)
        # Review (Codex, Sonnet): a ticket with a blank id is the board's fault to name, not a missing ticket.
        (self.repo / '.scratch' / 'run' / 'issues' / '00-blank.md').write_text('---\nticket:\nstatus: ready\n---\n')
        self.assertIn("00-blank.md: invalid ticket id ''", self.phase('dispatch', code=1)[1])
        for draft in ('00-draft.md', '00-blank.md'):
            (self.repo / '.scratch' / 'run' / 'issues' / draft).unlink()
        self.ticket('01', blocked_by='run/09')
        for phase in ('dispatch', 'pr'):
            with self.subTest(phase=phase):
                text, error = self.phase(phase, code=1)
                self.assertIn('the local board of run fails its check: run/01: blocked_by run/09 does not exist', error)
        self.ticket('01')
        self.assertIn('\nphase: dispatch\n', self.phase('dispatch')[0])
        # Review (Sonnet): the stop hook names this command for a card with no `phase:` line too, so it writes one.
        text, _ = self.phase('align', phase_line=False)
        self.assertIn('\nreview-effort: high\nterminal:\nhistory:\nevidence:\ntimer:\nresume: `autopilot --take-over`\nphase: align\n', text)

    def test_dispatch_on_a_tracker_board_needs_no_local_tickets_and_a_card_names_its_board(self):
        # Question tool (board-tools, "Card names its board"): a tracker holds its own tickets; the card says which.
        self.assertIn('\nphase: dispatch\n', self.phase('dispatch', board='https://linear.app/team/project/x')[0])
        for board, refusal in ((None, 'the card has no `board:` line'),
                               ("<local | the tracker's URL>", "the card's `board:` is neither `local` nor a tracker's URL: ''"),
                               ('jira', "the card's `board:` is neither `local` nor a tracker's URL: 'jira'"),
                               ('https://', "the card's `board:` is neither `local` nor a tracker's URL: 'https://'"),
                               ('https://?project=1', "the card's `board:` is neither `local` nor a tracker's URL: "
                                                      "'https://?project=1'"),
                               ('https://:443', "the card's `board:` is neither `local` nor a tracker's URL: 'https://:443'"),
                               *((bad, f"the card's `board:` is neither `local` nor a tracker's URL: {bad!r}")
                                 for bad in ('https://host:abc/x', 'https://<tracker>', 'https://linear.app/a b',
                                             'https://[bad/x'))):
            with self.subTest(board=board):
                text, error = self.phase('dispatch', board=board, code=1)
                self.assertEqual(error.strip(), refusal)
                self.assertIn('\nphase: refine\n', text)

    def test_dispatch_refuses_a_missing_or_failed_publish_probe(self):
        # Rule: a run that cannot publish enters neither dispatch nor pr; the refusal names the probe and leaves the card
        # unchanged. A run probes before its work, so the dispatch phase is where the probe is first demanded.
        self.ticket('01')
        probe = shlex.join(['python3', str(COMMAND.resolve().parent / 'publish_probe.py'), str(self.card)])
        refusal = (f'the publish probe of run is missing or failed: run `{probe}` and fix what it reports, then set the '
                   'phase')

        def card_with(line):
            text = DRAFT.replace('confirmed: no', 'confirmed: 2026-11-06 "go"').replace(UNNAMED, 'session: host-1')
            return text.replace('phase: refine\n', f'phase: refine\n{line}\n' if line else 'phase: refine\n')

        run_control = support.load_script('run_control.py')

        def phase(value, text, managed=False):
            run_control.location(self.card).unlink(missing_ok=True)
            self.card.write_text(text)
            if managed:
                run_control.adopt(self.card, 'confirmed run')
            before = self.card.read_text()
            result = subprocess.run([sys.executable, '-B', str(COMMAND), 'phase', str(self.card), value],
                                    capture_output=True, text=True, timeout=30)
            return result, before

        for value in ('dispatch', 'pr'):
            for managed in (False, True):
                for line in ('', 'publish-probe: failed 2026-11-27T09:00:00Z gh reports no write permission on acme/widgets',
                             'publish-probe:'):
                    result, before = phase(value, card_with(line), managed)
                    self.assertEqual((result.returncode, result.stderr.strip()), (1, refusal))
                    self.assertEqual(self.card.read_text(), before)
                result, before = phase(value, card_with(OK_PROBE), managed)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.card.read_text(), before.replace('phase: refine\n', f'phase: {value}\n'))

    def test_a_direct_card_moves_to_dispatch_with_two_local_tickets_and_is_refused_with_three(self):
        # Rule (outcome B): above two tickets the work is the full path, with the user's confirmation.
        self.card.parent.mkdir(exist_ok=True)

        def direct(phase):
            text = DRAFT.replace('mode: full', 'mode: direct').replace('confirmed: no', 'confirmed: 2026-11-06 by rule "go"')
            self.card.write_text(text.replace(UNNAMED, f'session: host-1\n{OK_PROBE}'))
            return subprocess.run([sys.executable, '-B', str(COMMAND), 'phase', str(self.card), phase],
                                  capture_output=True, text=True, timeout=30)
        for number in ('01', '02'):
            self.ticket(number)
        self.assertEqual(direct('dispatch').returncode, 0)
        self.ticket('03')
        result = direct('dispatch')
        self.assertEqual(result.returncode, 1)
        self.assertIn('full path', result.stderr)
        self.assertIn('\nphase: refine\n', self.card.read_text())
        # A full card takes three tickets.
        self.card.write_text(DRAFT.replace('confirmed: no', 'confirmed: 2026-11-06 "go"').replace('resume:', f'{OK_PROBE}\nresume:'))
        self.assertEqual(subprocess.run([sys.executable, '-B', str(COMMAND), 'phase', str(self.card), 'dispatch'],
                                        capture_output=True, text=True, timeout=30).returncode, 0)

    def human_confirm(self, words):
        env = dict(support.without_host_sessions(), PI_SESSION_ID='host-1')
        host = T3Read(self, self.card.parent, env)
        command = host.command(COMMAND, 'confirm', self.card, '--words', words)
        host.accept(subprocess.run(command, env=env, capture_output=True, text=True, timeout=30), words)
        subprocess.run(command, env=env, check=True, capture_output=True, text=True, timeout=30)

    def test_a_full_card_confirmed_by_rule_is_refused_dispatch_and_pr_until_the_user_confirms_it(self):
        # Rule: a full card confirmed by rule, as a direct card switched by hand would be, enters neither dispatch nor
        # pr until the user's own confirmation is recorded.
        self.card.parent.mkdir(exist_ok=True)
        self.ticket('01')
        by_rule = DRAFT.replace('mode: full', 'mode: direct').replace('confirmed: no', 'confirmed: 2026-11-06 by rule "go"')
        self.card.write_text(by_rule.replace(UNNAMED, 'session: host-1\nintent-thread: host-1\n' + OK_PROBE))

        def run(command, *args):
            return subprocess.run([sys.executable, '-B', str(COMMAND), command, str(self.card), *args],
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(run('phase', 'dispatch').returncode, 0)
        self.card.write_text(self.card.read_text().replace('mode: direct', 'mode: full'))
        for phase in ('dispatch', 'pr'):
            with self.subTest(phase=phase):
                result = run('phase', phase)
                self.assertEqual(result.returncode, 1)
                self.assertIn('confirm --words', result.stderr)
        self.assertIn('\nphase: dispatch\n', self.card.read_text())
        # The user's own confirmation, recorded by the command, clears it.
        self.human_confirm('ok')
        self.assertEqual(run('phase', 'pr').returncode, 0)

    def test_a_full_card_the_user_confirmed_in_words_that_say_by_rule_is_not_taken_for_a_confirmation_by_rule(self):
        # Rule: only the form `confirm` writes for a direct card is a confirmation by rule; the user's own words
        # are recorded verbatim and may contain the phrase.
        self.card.parent.mkdir(exist_ok=True)
        self.ticket('01')
        self.card.write_text(DRAFT.replace(UNNAMED, f'session: host-1\n{OK_PROBE}'))
        self.human_confirm('yes, go by rule of thumb')
        result = subprocess.run([sys.executable, '-B', str(COMMAND), 'phase', str(self.card), 'dispatch'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
