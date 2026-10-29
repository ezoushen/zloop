#!/usr/bin/env python3
"""Worker submission verifies native model and effort before productive work."""

import json
import subprocess
import sys
import unittest

from lifecycle_case import WorkerLifecycleCase
from native_records import session_record


class Submission(WorkerLifecycleCase):
    def test_submit_reads_native_settings_before_running_the_productive_command(self):
        self.register()
        native = self.root / 'native.jsonl'
        native.write_text(json.dumps({'type': 'assistant', 'sessionId': 'native-1',
                                     'isSidechain': False, 'cwd': str(self.root),
                                     'effort': 'high', 'perTurnEffort': 'high',
                                     'message': {'model': 'sonnet'}}) + '\n')
        marker = self.root / 'productive-work'
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
                                      'argv': [sys.executable, '-c',
                                               'from pathlib import Path; Path("productive-work").write_text("ran")']}))
        result = self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual(result['exit_code'], 0)
        self.assertTrue(marker.is_file())
        attempt = self.cli('status')['attempts'][0]
        self.assertEqual(attempt['observed'], {'runtime': 'claude', 'model': 'sonnet', 'effort': 'high'})
        self.assertEqual(self.cli('status')['pending'][0]['kind'], 'wait-finished')

    def test_submit_allows_claude_to_work_in_a_checkout_subdirectory(self):
        self.register()
        child = self.root / '.scratch' / 'reports'
        child.mkdir(parents=True)
        native = self.root / 'native.jsonl'
        native.write_text(json.dumps({'type': 'assistant', 'sessionId': 'native-1',
            'cwd': str(child), 'perTurnEffort': 'high', 'message': {'model': 'sonnet'}}) + '\n')
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
            'argv': [sys.executable, '-c', 'from pathlib import Path; Path("productive-work").touch()']}))
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertTrue((self.root / 'productive-work').exists())

    def test_submit_rejects_nested_claude_repository_boundaries(self):
        self.register()
        nested = self.root / 'other-project'
        child = nested / 'src'
        child.mkdir(parents=True)
        native = self.root / 'native.jsonl'
        native.write_text(json.dumps({'type': 'assistant', 'sessionId': 'native-1',
            'cwd': str(child), 'perTurnEffort': 'high', 'message': {'model': 'sonnet'}}) + '\n')
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
            'argv': [sys.executable, '-c', 'from pathlib import Path; Path("productive-work").touch()']}))
        boundary = nested / '.git'
        for kind in ('directory', 'worktree-file'):
            with self.subTest(kind=kind):
                if kind == 'directory':
                    boundary.mkdir()
                else:
                    boundary.write_text('gitdir: /another/worktree\n')
                self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
                self.assertFalse((self.root / 'productive-work').exists())
                if kind == 'directory':
                    boundary.rmdir()
                else:
                    boundary.unlink()

    def test_registration_refuses_a_top_level_route_without_launch_controls(self):
        # Rule: a worker started as its own runtime process carries the resolver's launch controls, or its write
        # scope could never be verified; a native child route is exempt.
        for runtime in ('claude', 'codex', 'opencode', 'pi'):
            with self.subTest(runtime=runtime):
                self.execution.write_text(json.dumps({'host': 'claude', 'target_runtime': runtime, 'native_dispatch': False,
                                                      'agent': 'builder', 'job': 'ticket', 'settings': {}}))
                error = self.register(f'ticket-1/{runtime}', session=f'native-{runtime}', worker=f'pane-{runtime}',
                                      runtime=runtime, code=2)
                self.assertIn(f'A route to a {runtime} worker started as its own process requires its launch '
                              'controls (launch.verify)', error)
        self.assertEqual(self.cli('status')['attempts'], [])
        self.execution.write_text(json.dumps({'host': 'codex', 'target_runtime': 'codex', 'native_dispatch': True,
                                              'agent': 'builder', 'job': 'ticket', 'settings': {}}))
        self.register(runtime='codex')
        self.assertEqual(len(self.cli('status')['attempts']), 1)

    def test_registration_refuses_an_untargeted_top_level_route_by_its_registered_runtime(self):
        # Rule (review reproducer): a route that names no target runtime, such as a --route-choice Herdr route, is
        # judged by the runtime it registers as; without launch controls a danger-full-access worker would verify.
        self.execution.write_text(json.dumps({'host': 'claude', 'target_runtime': None, 'native_dispatch': False,
            'executor': 'skill', 'agent': 'reviewer', 'job': 'diff-review',
            'settings': {'model': 'gpt-6.1-sol', 'effort': 'high'}}))
        error = self.register(runtime='codex', code=2)
        self.assertIn('A route to a codex worker started as its own process requires its launch controls', error)
        self.assertEqual(self.cli('status')['attempts'], [])

    def test_registration_refuses_a_native_route_under_another_runtime_than_its_host(self):
        # Rule: a native child runs in its host's runtime, so a worker registered under another runtime is refused.
        self.execution.write_text(json.dumps({'host': 'codex', 'native_dispatch': True,
                                              'agent': 'builder', 'job': 'ticket', 'settings': {}}))
        self.assertIn('Runtime conflicts with resolved execution', self.register(runtime='claude', code=2))
        self.assertEqual(self.cli('status')['attempts'], [])

    def test_submit_reads_codex_session_and_latest_turn_controls(self):
        self.execution.write_text(json.dumps({'host': 'codex', 'native_dispatch': True, 'agent': 'builder', 'job': 'ticket',
                                             'settings': {'model': 'gpt-6.1-sol', 'effort': 'high'}}))
        self.register(runtime='codex')
        native = self.root / 'native.jsonl'
        rows = [{'type': 'session_meta', 'payload': {'id': 'native-1', 'cwd': str(self.root)}},
                {'type': 'turn_context', 'payload': {'model': 'gpt-6.1-sol', 'effort': 'medium', 'cwd': str(self.root)}},
                {'type': 'turn_context', 'payload': {'model': 'gpt-6.1-sol', 'effort': 'high', 'cwd': str(self.root)}}]
        native.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
                                      'argv': [sys.executable, '-c', 'print("accepted")']}))
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual(self.cli('status')['attempts'][0]['observed'],
                         {'runtime': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'high'})

    def submit_scoped(self, request_code=0):
        """Submit the productive-work marker command with the native record at native.jsonl."""
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(self.root / 'native.jsonl'), 'timeout': 2,
            'argv': [sys.executable, '-c', 'from pathlib import Path; Path("productive-work").touch()']}))
        return self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=request_code)

    def assert_scope_refused(self):
        """A record whose write scope differs from the launch, or lacks it, blocks work unverified."""
        self.assertIn('verified', self.submit_scoped(2))
        self.assertFalse(self.cli('status')['attempts'][0]['verified'])
        self.assertFalse((self.root / 'productive-work').exists())

    def test_submit_refuses_codex_work_until_the_rollout_shows_the_launch_sandbox_and_approval(self):
        # Rule: submit verifies the write scope from the native record; a different or missing control blocks work.
        self.execution.write_text(json.dumps({'target_runtime': 'codex', 'agent': 'builder', 'job': 'ticket',
            'settings': {'model': 'gpt-6.1-sol', 'effort': 'high'},
            'launch': {'writes': 'worktree', 'verify': {'sandbox': 'workspace-write', 'approval': 'never'}}}))
        self.register(runtime='codex')
        turn = {'model': 'gpt-6.1-sol', 'effort': 'high', 'cwd': str(self.root), 'approval_policy': 'never',
                'sandbox_policy': {'type': 'workspace-write', 'writable_roots': [str(self.root)]}}

        def rollout(**changes):
            context = {key: value for key, value in dict(turn, **changes).items() if value is not None}
            (self.root / 'native.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in (
                {'type': 'session_meta', 'payload': {'id': 'native-1', 'cwd': str(self.root)}},
                {'type': 'turn_context', 'payload': context})))
        for case, changes in (('user default', {'sandbox_policy': {'type': 'danger-full-access'}}),
                              ('other scope', {'sandbox_policy': {'type': 'read-only'}}),
                              ('asks for approval', {'approval_policy': 'on-request'}),
                              ('no sandbox recorded', {'sandbox_policy': None}),
                              ('no approval recorded', {'approval_policy': None})):
            with self.subTest(case=case):
                rollout(**changes)
                self.assert_scope_refused()
        rollout()
        self.submit_scoped()
        self.assertTrue((self.root / 'productive-work').exists())
        self.assertEqual(self.cli('status')['attempts'][0]['observed'], {
            'runtime': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'high', 'sandbox': 'workspace-write',
            'approval': 'never'})

    def test_submit_refuses_read_only_codex_work_unless_its_permission_profile_keeps_the_worktree_read_only(self):
        # Rule: for `writes: none` the rollout's permission profile must be exactly the launch's: everything readable,
        # only $TMPDIR and the reports directory writable. A workspace-write sandbox on the worktree, the user
        # default, another sandbox or a record without the profile blocks work.
        reports = str(self.root / 'reports')
        self.execution.write_text(json.dumps({'target_runtime': 'codex', 'agent': 'reviewer', 'job': 'diff-review',
            'settings': {'model': 'gpt-6.1-sol', 'effort': 'high'}, 'launch': {'writes': 'none', 'verify': {
                'sandbox': 'workspace-write', 'approval': 'never',
                'filesystem': ['read :root', f'write {reports}', 'write :tmpdir']}}}))
        self.register(runtime='codex')

        def special(kind, access):
            return {'path': {'type': 'special', 'value': {'kind': kind}}, 'access': access}

        def path(name, access, **extra):
            return dict({'path': {'type': 'path', 'path': name}, 'access': access}, **extra)
        profile = {'type': 'managed', 'network': 'restricted', 'file_system': {'type': 'restricted', 'entries': [
            path(reports, 'write'), special('root', 'read'), special('tmpdir', 'write')]}}
        on_worktree = {'type': 'managed', 'network': 'restricted', 'file_system': {'type': 'restricted', 'entries': [
            special('root', 'read'), path(str(self.root), 'write'), special('slash_tmp', 'write'),
            special('tmpdir', 'write'), path(str(self.root / '.git'), 'read', missing_path_behavior='skip')]}}

        def rollout(sandbox, permission_profile):
            context = {'model': 'gpt-6.1-sol', 'effort': 'high', 'cwd': str(self.root), 'approval_policy': 'never',
                       'sandbox_policy': {'type': sandbox}}
            if permission_profile is not None:
                context['permission_profile'] = permission_profile
            (self.root / 'native.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in (
                {'type': 'session_meta', 'payload': {'id': 'native-1', 'cwd': str(self.root)}},
                {'type': 'turn_context', 'payload': context})))
        for case, sandbox, permission_profile in (
                ('worktree writable', 'workspace-write', on_worktree),
                ('user default', 'danger-full-access', {'type': 'disabled'}),
                ('read-only', 'read-only', {'type': 'managed', 'network': 'restricted', 'file_system': {
                    'type': 'restricted', 'entries': [special('root', 'read')]}}),
                ('no profile recorded', 'workspace-write', None)):
            with self.subTest(case=case):
                rollout(sandbox, permission_profile)
                self.assert_scope_refused()
        rollout('workspace-write', profile)
        self.submit_scoped()
        self.assertTrue((self.root / 'productive-work').exists())
        self.assertEqual(self.cli('status')['attempts'][0]['observed']['filesystem'],
                         ['read :root', f'write {reports}', 'write :tmpdir'])

    def test_submit_refuses_claude_work_until_the_transcript_shows_dont_ask_mode(self):
        # Rule: a TUI transcript has no tool list, so its latest permission-mode row is the write-scope evidence.
        execution = json.loads(self.execution.read_text())
        execution['launch'] = {'writes': 'worktree', 'verify': {'permission_mode': 'dontAsk'}}
        self.execution.write_text(json.dumps(execution))
        self.register()
        assistant = {'type': 'assistant', 'sessionId': 'native-1', 'isSidechain': False, 'cwd': str(self.root),
                     'perTurnEffort': 'high', 'message': {'model': 'sonnet'}}

        def transcript(*modes):
            rows = [{'type': 'permission-mode', 'permissionMode': mode, 'sessionId': 'native-1'} for mode in modes]
            (self.root / 'native.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in [*rows, assistant]))
        for case, modes in (('user default', ('auto',)), ('changed after launch', ('dontAsk', 'bypassPermissions')),
                            ('no mode recorded', ())):
            with self.subTest(case=case):
                transcript(*modes)
                self.assert_scope_refused()
        transcript('dontAsk', 'dontAsk')
        self.submit_scoped()
        self.assertTrue((self.root / 'productive-work').exists())
        self.assertEqual(self.cli('status')['attempts'][0]['observed'], {
            'runtime': 'claude', 'model': 'sonnet', 'effort': 'high', 'permission_mode': 'dontAsk'})

    def pi_session(self, *tool_changes, checkpoint=None):
        """A Pi session file whose active branch carries one system message per (added, removed) tool-name change,
        then, when given, a compaction whose system message checkpoints these tool names."""
        rows = [{'type': 'session', 'id': 'native-1', 'cwd': str(self.root)},
                {'type': 'model_change', 'id': 'model', 'parentId': None, 'provider': 'dgx', 'modelId': 'glm-5.3-flash'},
                {'type': 'thinking_level_change', 'id': 'e0', 'parentId': 'model', 'thinkingLevel': 'high'}]
        for index, (added, removed) in enumerate(tool_changes, 1):
            message = {'role': 'system', 'content': '', 'sections': {}}
            message.update({'toolsAdded': [{'name': name, 'description': name, 'parameters': {}} for name in added]}
                           if added else {}, **({'toolsRemoved': [{'name': name} for name in removed]} if removed else {}))
            rows.append({'type': 'message', 'id': f'e{index}', 'parentId': f'e{index - 1}', 'message': message})
        if checkpoint is not None:
            rows.append({'type': 'compaction', 'id': 'compacted', 'parentId': rows[-1]['id'], 'summary': 'Earlier work.',
                         'firstKeptEntryId': 'compacted', 'tokensBefore': 50000, 'systemMessage': {
                             'role': 'system', 'content': '', 'toolsAdded': [{'name': name} for name in checkpoint]}})
        rows.append({'type': 'message', 'id': 'reply', 'parentId': rows[-1]['id'],
                     'message': {'role': 'assistant', 'provider': 'dgx', 'model': 'glm-5.3-flash', 'stopReason': 'stop'}})
        (self.root / 'native.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def test_submit_refuses_pi_work_until_the_session_offers_exactly_the_launch_tools(self):
        # Rule: the tools a Pi session offers replay from its system messages (toolsAdded, then toolsRemoved) and
        # must be exactly the launch's allowlist; an extra tool, such as an extension shell, blocks work.
        self.execution.write_text(json.dumps({'target_runtime': 'pi', 'agent': 'reviewer', 'job': 'diff-review',
            'settings': {'model': 'dgx/glm-5.3-flash', 'effort': 'high'},
            'launch': {'writes': 'none', 'verify': {'tools': ['bash', 'find', 'grep', 'ls', 'read']}}}))
        self.register(runtime='pi')
        read_only = ['read', 'grep', 'find', 'ls', 'bash']  # `none` keeps the shell, never write or edit.
        for case, changes in (('user default', [(['read', 'bash', 'edit', 'write', 'bg_run', 'subagent'], [])]),
                              ('tool added later', [(read_only, []), (['bg_run'], [])]),
                              ('no tool record', [])):
            with self.subTest(case=case):
                self.pi_session(*changes)
                self.assert_scope_refused()
        self.pi_session((read_only + ['bg_run'], []), ([], ['bg_run']))
        self.submit_scoped()
        self.assertTrue((self.root / 'productive-work').exists())
        self.assertEqual(self.cli('status')['attempts'][0]['observed'], {
            'runtime': 'pi', 'model': 'dgx/glm-5.3-flash', 'effort': 'high', 'tools': ['bash', 'find', 'grep', 'ls', 'read']})

    def test_a_refused_submit_names_each_mismatched_control_with_its_requested_and_observed_value(self):
        # Rule: an unverified worker's refusal says which requested control the native record contradicts and what
        # each side was, and names nothing that matched; a control the record lacks is observed as none.
        self.execution.write_text(json.dumps({'target_runtime': 'codex', 'agent': 'builder', 'job': 'ticket',
            'settings': {'model': 'gpt-6.1-sol', 'effort': 'high'},
            'launch': {'writes': 'worktree', 'verify': {'sandbox': 'workspace-write', 'approval': 'never'}}}))
        self.register(runtime='codex')
        (self.root / 'native.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in (
            {'type': 'session_meta', 'payload': {'id': 'native-1', 'cwd': str(self.root)}},
            {'type': 'turn_context', 'payload': {'model': 'gpt-6.1-sol', 'effort': 'medium', 'cwd': str(self.root),
                                                 'sandbox_policy': {'type': 'read-only'}}})))
        error = self.submit_scoped(2)
        self.assertIn('Wait requires a verified, unfinished worker: the native record differs from the launch: '
                      'sandbox requested workspace-write, observed read-only; approval requested never, observed none; '
                      'effort requested high, observed medium', error)
        self.assertNotIn('model requested', error)
        self.assertFalse((self.root / 'productive-work').exists())

    def test_a_refused_pi_submit_names_the_requested_and_offered_tools(self):
        # Rule: a tool allowlist mismatch names both tool lists.
        self.execution.write_text(json.dumps({'target_runtime': 'pi', 'agent': 'reviewer', 'job': 'diff-review',
            'settings': {'model': 'dgx/glm-5.3-flash', 'effort': 'high'},
            'launch': {'writes': 'none', 'verify': {'tools': ['bash', 'find', 'grep', 'ls', 'read']}}}))
        self.register(runtime='pi')
        self.pi_session((['read', 'grep', 'find', 'ls', 'bash', 'bg_run'], []))
        error = self.submit_scoped(2)
        self.assertIn("tools requested ['bash', 'find', 'grep', 'ls', 'read'], "
                      "observed ['bash', 'bg_run', 'find', 'grep', 'ls', 'read']", error)

    def test_submit_reads_pi_tools_from_the_latest_compaction_checkpoint(self):
        # Rule (boundary): a compaction stores a complete tool checkpoint, so the tools offered after it replace
        # whatever earlier system messages declared.
        self.execution.write_text(json.dumps({'target_runtime': 'pi', 'agent': 'reviewer', 'job': 'diff-review',
            'settings': {'model': 'dgx/glm-5.3-flash', 'effort': 'high'},
            'launch': {'writes': 'none', 'verify': {'tools': ['bash', 'find', 'grep', 'ls', 'read']}}}))
        self.register(runtime='pi')
        read_only = ['read', 'grep', 'find', 'ls', 'bash']  # `none` keeps the shell, never write or edit.
        self.pi_session((read_only, []), checkpoint=read_only + ['write'])
        self.assert_scope_refused()
        self.pi_session((read_only + ['edit', 'write'], []), checkpoint=read_only)
        self.submit_scoped()
        self.assertTrue((self.root / 'productive-work').exists())
        self.assertEqual(self.cli('status')['attempts'][0]['observed']['tools'], ['bash', 'find', 'grep', 'ls', 'read'])

    def test_submit_reads_pi_controls_from_active_branch(self):
        self.execution.write_text(json.dumps({'host': 'pi', 'native_dispatch': True, 'agent': 'builder', 'job': 'ticket',
                                             'settings': {'model': 'dgx/glm-5.3-flash', 'effort': 'high'}}))
        self.register(runtime='pi')
        native = self.root / 'native.jsonl'
        rows = [{'type': 'session', 'id': 'native-1', 'cwd': str(self.root)},
                {'type': 'model_change', 'id': 'model', 'parentId': None,
                 'provider': 'dgx', 'modelId': 'glm-5.3-flash'},
                {'type': 'thinking_level_change', 'id': 'high', 'parentId': 'model', 'thinkingLevel': 'high'},
                {'type': 'thinking_level_change', 'id': 'abandoned', 'parentId': 'high', 'thinkingLevel': 'low'},
                {'type': 'message', 'id': 'active', 'parentId': 'high',
                 'message': {'role': 'assistant', 'provider': 'dgx', 'model': 'glm-5.3-flash'}}]
        native.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
                                      'argv': [sys.executable, '-c', 'print("accepted")']}))
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual(self.cli('status')['attempts'][0]['observed'],
                         {'runtime': 'pi', 'model': 'dgx/glm-5.3-flash', 'effort': 'high'})

    def test_submit_refuses_pi_provider_error_until_a_successful_probe(self):
        self.execution.write_text(json.dumps({'host': 'pi', 'native_dispatch': True, 'agent': 'builder', 'job': 'ticket',
            'settings': {'model': 'dgx/glm-5.3-flash', 'effort': 'high'}}))
        self.register(runtime='pi')
        native = self.root / 'native.jsonl'
        rows = [{'type': 'session', 'id': 'native-1', 'cwd': str(self.root)},
                {'type': 'model_change', 'id': 'model', 'parentId': None,
                 'provider': 'dgx', 'modelId': 'glm-5.3-flash'},
                {'type': 'thinking_level_change', 'id': 'high', 'parentId': 'model',
                 'thinkingLevel': 'high'},
                {'type': 'message', 'id': 'failed', 'parentId': 'high', 'message': {
                    'role': 'assistant', 'provider': 'dgx', 'model': 'glm-5.3-flash',
                    'stopReason': 'error', 'errorMessage': 'Connection error.'}}]
        native.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
            'argv': [sys.executable, '-c', 'from pathlib import Path; Path("productive-work").touch()']}))
        error = self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertIn('failed native response', error)
        self.assertFalse((self.root / 'productive-work').exists())
        rows.append({'type': 'message', 'id': 'ready', 'parentId': 'failed', 'message': {
            'role': 'assistant', 'provider': 'dgx', 'model': 'glm-5.3-flash', 'stopReason': 'stop'}})
        native.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertTrue((self.root / 'productive-work').exists())

    def test_submit_reads_opencode_assistant_controls_instead_of_requested_defaults(self):
        self.execution.write_text(json.dumps({'host': 'opencode', 'native_dispatch': True, 'agent': 'builder', 'job': 'ticket',
                                             'settings': {'model': 'opencode/muse-spark-1.3-contributor-free',
                                                          'effort': 'high'}}))
        self.register(runtime='opencode')
        native = self.root / 'native.json'
        native.write_text(json.dumps({'info': {'id': 'native-1', 'directory': str(self.root),
                                               'model': {'modelID': 'different-default'}},
            'messages': [{'info': {'role': 'assistant', 'sessionID': 'native-1',
                                  'providerID': 'opencode', 'modelID': 'muse-spark-1.3-contributor-free',
                                  'variant': 'high'}}]}))
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
                                      'argv': [sys.executable, '-c', 'print("accepted")']}))
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertEqual(self.cli('status')['attempts'][0]['observed'],
                         {'runtime': 'opencode', 'model': 'opencode/muse-spark-1.3-contributor-free',
                          'effort': 'high'})

    def test_submit_refuses_opencode_tool_error_until_a_clean_new_probe(self):
        self.execution.write_text(json.dumps({'host': 'opencode', 'native_dispatch': True, 'agent': 'builder', 'job': 'ticket',
            'settings': {'model': 'opencode/muse-spark-1.3-contributor-free'}}))
        self.register(runtime='opencode')
        native = self.root / 'native-export.json'
        assistant = {'role': 'assistant', 'sessionID': 'native-1',
            'providerID': 'opencode', 'modelID': 'muse-spark-1.3-contributor-free'}
        data = {'info': {'id': 'native-1', 'directory': str(self.root)}, 'messages': [
            {'info': {'role': 'user', 'sessionID': 'native-1'}},
            {'info': assistant, 'parts': [{'type': 'tool', 'state': {'status': 'error',
                'error': 'The user rejected permission to use this specific tool call.'}}]},
            {'info': assistant, 'parts': [{'type': 'text', 'text': 'Required read was denied.'}]}]}
        native.write_text(json.dumps(data))
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
            'argv': [sys.executable, '-c', 'from pathlib import Path; Path("productive-work").touch()']}))
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertFalse((self.root / 'productive-work').exists())
        # A later successful startup probe does not inherit the earlier tool failure.
        data['messages'].extend([{'info': {'role': 'user', 'sessionID': 'native-1'}},
            {'info': assistant, 'parts': [{'type': 'tool', 'state': {'status': 'completed'}}]}])
        native.write_text(json.dumps(data))
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertTrue((self.root / 'productive-work').exists())

    def test_submit_reads_opencode_2_export_controls(self):
        self.execution.write_text(json.dumps({'host': 'opencode', 'native_dispatch': True, 'agent': 'builder', 'job': 'ticket',
            'settings': {'model': 'opencode/deepseek-v4.1-flash-free', 'effort': 'high'}}))
        self.register(runtime='opencode')
        self.submit_opencode_v2(self.opencode_v2_export([{'type': 'text', 'text': 'ready'}]))
        self.assertEqual(self.cli('status')['attempts'][0]['observed'],
                         {'runtime': 'opencode', 'model': 'opencode/deepseek-v4.1-flash-free', 'effort': 'high'})
        self.assertTrue((self.root / 'productive-work').exists())

    def test_submit_refuses_opencode_2_tool_error_and_foreign_session(self):
        self.execution.write_text(json.dumps({'host': 'opencode', 'native_dispatch': True, 'agent': 'builder', 'job': 'ticket',
            'settings': {'model': 'opencode/deepseek-v4.1-flash-free'}}))
        self.register(runtime='opencode')
        failed = {'type': 'tool', 'name': 'read', 'state': {'status': 'error', 'error': {'message': 'denied'}}}
        ready = [{'type': 'text', 'text': 'ready'}]
        for content, options, reason in ((([failed], [{'type': 'text', 'text': 'Read was denied.'}]), {},
                                          'failed tool call'),
                                         ((ready,), {'session': 'other'}, 'registered session'),
                                         ((ready,), {'directory': '/elsewhere'}, 'worktree')):
            native = self.opencode_v2_export(*content, **options)
            self.assertIn(reason, self.submit_opencode_v2(native, code=2))
            self.assertFalse((self.root / 'productive-work').exists())

    def test_submit_refuses_opencode_work_until_the_export_shows_the_launch_deny_rules(self):
        # Rule: the session's permissions in its export must be exactly the launch's rules; with the last matching
        # rule winning, a missing deny or a later allow widens the scope.
        def deny(*actions):
            return [{'action': action, 'resource': '*', 'effect': 'deny'} for action in actions]
        rules = deny('edit', 'subagent')  # `none` keeps the shell for checks.
        self.execution.write_text(json.dumps({'target_runtime': 'opencode', 'agent': 'reviewer', 'job': 'diff-review',
            'settings': {'model': 'opencode/deepseek-v4.1-flash-free', 'effort': 'high'},
            'launch': {'writes': 'none', 'verify': {'permissions': rules}}}))
        self.register(runtime='opencode')
        ready = [{'type': 'text', 'text': 'ready'}]
        for case, permissions in (('none recorded', None), ('subagent allowed', deny('edit')),
                                  ('allowed again later', rules + [{'action': 'edit', 'resource': '*', 'effect': 'allow'}])):
            with self.subTest(case=case):
                self.assertIn('verified', self.submit_opencode_v2(self.opencode_v2_export(ready, permissions=permissions),
                                                                  code=2))
                self.assertFalse(self.cli('status')['attempts'][0]['verified'])
                self.assertFalse((self.root / 'productive-work').exists())
        self.submit_opencode_v2(self.opencode_v2_export(ready, permissions=rules))
        self.assertTrue((self.root / 'productive-work').exists())
        self.assertEqual(self.cli('status')['attempts'][0]['observed'], {
            'runtime': 'opencode', 'model': 'opencode/deepseek-v4.1-flash-free', 'effort': 'high',
            'permissions': rules})

    def test_submit_records_submitted_when_the_activity_gated_prompt_succeeds(self):
        self.register()
        native = self.root / 'native.jsonl'
        native.write_text(json.dumps({'type': 'assistant', 'sessionId': 'native-1', 'cwd': str(self.root),
                                      'message': {'model': 'sonnet'}, 'effort': 'high'}) + '\n')
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
                                      'argv': [sys.executable, '-c', 'print("prompted")']}))
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request))
        self.acknowledge()
        self.record('reconciled', evidence=['native.jsonl:3:idle after prompt'])
        # Completion requires an accepted prompt; the host recorded nothing by hand.
        self.collect()
        self.assertEqual(self.cli('status')['attempts'][0]['result']['status'], 'completed')

    def test_submit_leaves_a_failed_prompt_unsubmitted_for_inspection(self):
        self.register()
        native = self.root / 'native.jsonl'
        native.write_text(json.dumps({'type': 'assistant', 'sessionId': 'native-1', 'cwd': str(self.root),
                                      'message': {'model': 'sonnet'}, 'effort': 'high'}) + '\n')
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
                                      'argv': [sys.executable, '-c', 'raise SystemExit(1)']}))
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=1)
        self.assertNotIn('submitted', self.cli('status')['attempts'][0])
        # The host inspects the worker and records acceptance itself.
        self.record('submitted', evidence=['native.jsonl:2:accepted prompt'])

    def herdr_prompt_request(self, *flags, options=('--session', 's1')):
        """A submit request whose argv is Herdr's prompt, run by a stand-in herdr that records its call."""
        native = self.root / 'native.jsonl'
        native.write_text(json.dumps({'type': 'assistant', 'sessionId': 'native-1', 'cwd': str(self.root),
                                      'message': {'model': 'sonnet'}, 'effort': 'high'}) + '\n')
        directory = self.root / 'bin'
        directory.mkdir(exist_ok=True)
        herdr = directory / 'herdr'
        herdr.write_text('#!' + sys.executable + '\n'
                         f'from pathlib import Path; Path({str(self.root / "prompted")!r}).write_text("ran")\n')
        herdr.chmod(0o700)
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
                                       'argv': [str(herdr), *options, 'agent', 'prompt', 'builder-1',
                                                'assignment', *flags]}))
        return request

    def test_submit_refuses_a_herdr_prompt_that_would_wait_for_the_whole_turn(self):
        # Rule: a Herdr submit returns once the worker is working; plain --wait holds until its turn settles.
        self.register()
        for flags in (['--wait', '--timeout', '3600000'], ['--until', 'working', '--timeout', '45000'],
                      ['--wait', '--until', 'blocked', '--timeout', '45000']):
            with self.subTest(flags=flags):
                request = self.herdr_prompt_request(*flags)
                error = self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
                self.assertIn('--wait --until working', error)
                self.assertIn('herdr agent wait', error)
                self.assertFalse((self.root / 'prompted').exists())
                self.assertNotIn('submitted', self.cli('status')['attempts'][0])
        # Global options before the command, such as another machine's, do not exempt the prompt.
        for options in (['--machine', 'remote1'], ['--remote', 'host', '--session', 's1']):
            with self.subTest(options=options):
                request = self.herdr_prompt_request('--wait', '--timeout', '3600000', options=options)
                error = self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
                self.assertIn('--wait --until working', error)
                self.assertFalse((self.root / 'prompted').exists())

    def test_submit_reports_a_malformed_herdr_argv_as_invalid_input(self):
        # Rule: an argv that is not literal text gets the usual validation error, not a crash.
        self.register()
        request = self.herdr_prompt_request('--wait')
        data = json.loads(request.read_text())
        data['argv'][1] = None
        request.write_text(json.dumps(data))
        self.assertIn('Wait requires literal argv',
                      self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=2))

    def test_submit_runs_a_herdr_prompt_that_returns_once_the_worker_is_working(self):
        # Rule: --wait --until working is the accepted Herdr form, and its success records the submission.
        self.register()
        request = self.herdr_prompt_request('--wait', '--until', 'working', '--until', 'blocked',
                                            '--timeout', '45000')
        self.assertEqual(self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request))['exit_code'], 0)
        self.assertEqual((self.root / 'prompted').read_text(), 'ran')
        # The documented handoff: reconcile the submit receipt, then arm the completion wait.
        self.acknowledge()
        self.record('reconciled', evidence=['submit log: worker working'])
        wait = self.root / 'wait.json'
        wait.write_text(json.dumps({'timeout': 2, 'argv': [json.loads(request.read_text())['argv'][0], '--session',
                                                           's1', 'agent', 'wait', 'builder-1', '--timeout', '1000']}))
        self.assertEqual(self.cli('watch', '--attempt', 'ticket-1/1', '--input', str(wait))['exit_code'], 0)
        self.acknowledge()
        self.record('reconciled', evidence=['native.jsonl:3:idle after prompt'])
        # Collection completes only for a recorded submission; nothing was recorded by hand.
        self.collect()
        self.assertEqual(self.cli('status')['attempts'][0]['result']['status'], 'completed')

    def test_submit_refuses_opencode_provider_error_even_when_settings_match(self):
        self.execution.write_text(json.dumps({'host': 'opencode', 'native_dispatch': True, 'agent': 'builder', 'job': 'ticket',
                                             'settings': {'model': 'opencode/muse-spark-1.3-contributor-free'}}))
        self.register(runtime='opencode')
        native = self.root / 'native.json'
        native.write_text(json.dumps({'info': {'id': 'native-1', 'directory': str(self.root)},
            'messages': [{'info': {'role': 'assistant', 'sessionID': 'native-1',
                                  'providerID': 'opencode', 'modelID': 'muse-spark-1.3-contributor-free',
                                  'error': {'name': 'APIError', 'data': {'statusCode': 403}}}}]}))
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
                                      'argv': [sys.executable, '-c',
                                               'from pathlib import Path; Path("productive-work").touch()']}))
        error = self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertIn('failed native response', error)
        self.assertFalse((self.root / 'productive-work').exists())

    def test_submit_refuses_wrong_unknown_and_unbound_native_controls(self):
        self.register()
        native = self.root / 'native.jsonl'
        marker = self.root / 'productive-work'
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
                                      'argv': [sys.executable, '-c',
                                               'from pathlib import Path; Path("productive-work").touch()']}))
        good = {'type': 'assistant', 'sessionId': 'native-1', 'cwd': str(self.root),
                'message': {'model': 'sonnet'}, 'effort': 'high'}
        invalid = [dict(good, message={'model': 'opus'}), dict(good, perTurnEffort='medium'),
                   dict(good, effort=None), dict(good, sessionId='different-session'),
                   dict(good, cwd=str(self.root / 'different-worktree')),
                   dict(good, isSidechain=True), dict(good, message='unsupported schema')]
        for row in invalid:
            with self.subTest(row=row):
                native.write_text(json.dumps(row) + '\n')
                self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
                self.assertFalse(marker.exists())
        native.write_text(json.dumps(good))
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertFalse(marker.exists())

    def test_submit_accepts_only_explicit_model_alias_evidence(self):
        self.register()
        native = self.root / 'native.jsonl'
        native.write_text(json.dumps({'type': 'assistant', 'sessionId': 'native-1', 'cwd': str(self.root),
                                     'message': {'model': 'claude-sonnet-native'}, 'effort': 'high'}) + '\n')
        request = self.root / 'submit.json'
        data = {'native': str(native), 'timeout': 2,
                'argv': [sys.executable, '-c', 'print("accepted")']}
        request.write_text(json.dumps(data))
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        data['model_alias_evidence'] = ['installed binding: sonnet = claude-sonnet-native']
        request.write_text(json.dumps(data))
        native.write_text(json.dumps({'type': 'assistant', 'sessionId': 'native-1', 'cwd': str(self.root),
                                     'message': {'model': 'claude-sonnet-native'}, 'effort': 'medium'}) + '\n')
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        native.write_text(json.dumps({'type': 'assistant', 'sessionId': 'native-1', 'cwd': str(self.root),
                                     'message': {'model': 'claude-sonnet-native'}, 'effort': 'high'}) + '\n')
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request))
        self.assertTrue(self.cli('status')['attempts'][0]['verified'])

    def test_submit_leaves_unrequested_native_controls_inherited(self):
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': 'builder', 'job': 'ticket', 'settings': {}}))
        self.register()
        native = self.root / 'native.jsonl'
        native.write_text(json.dumps({'type': 'assistant', 'sessionId': 'native-1', 'cwd': str(self.root),
                                     'message': {'model': 'claude-opus-native'}}) + '\n')
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
                                      'argv': [sys.executable, '-c', 'print("accepted")']}))
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request))
        attempt = self.cli('status')['attempts'][0]
        self.assertEqual(attempt['requested'], {'runtime': 'claude'})
        self.assertEqual(attempt['observed'], {'runtime': 'claude', 'model': 'claude-opus-native'})

    def test_submit_cannot_duplicate_productive_work_after_reconciliation(self):
        self.register()
        native = self.root / 'native.jsonl'
        native.write_text(json.dumps({'type': 'assistant', 'sessionId': 'native-1', 'cwd': str(self.root),
                                     'message': {'model': 'sonnet'}, 'effort': 'high'}) + '\n')
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
                                      'argv': [sys.executable, '-c',
                                               'from pathlib import Path; p=Path("count"); p.write_text(p.read_text()+"x" if p.exists() else "x")']}))
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request))
        self.record('reconciled', evidence=['native state inspected; wait finished'])
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertEqual((self.root / 'count').read_text(), 'x')

    def test_submit_blocks_an_active_design_hold(self):
        self.register()
        native = self.root / 'native.jsonl'
        native.write_text(json.dumps({'type': 'assistant', 'sessionId': 'native-1', 'cwd': str(self.root),
                                     'message': {'model': 'sonnet'}, 'effort': 'high'}) + '\n')
        self.record('hold', reason='Possible agreement breach', brief=str(self.root / 'brief.md'),
                    evidence=['worker uncertainty signal'])
        request = self.root / 'submit.json'
        request.write_text(json.dumps({'native': str(native), 'timeout': 2,
                                      'argv': [sys.executable, '-c',
                                               'from pathlib import Path; Path("productive-work").touch()']}))
        self.cli('submit', '--attempt', 'ticket-1/1', '--input', str(request), code=2)
        self.assertFalse((self.root / 'productive-work').exists())

    def test_model_or_effort_mismatch_blocks_work_and_acceptance(self):
        for observed in ({'runtime': 'claude', 'model': 'opus', 'effort': 'high'},
                         {'runtime': 'claude', 'model': 'sonnet', 'effort': 'medium'},
                         {'runtime': 'claude', 'model': 'sonnet'}):
            if not self.state.exists():
                self.register()
            result = self.record('startup', native=str(self.native(model=observed.get('model'),
                                                                   effort=observed.get('effort'))))
            self.assertFalse(result['verified'])
            gate = self.cli('gate', '--attempt', 'ticket-1/1', '--stage', 'work', code=1)
            self.assertIn('runtime settings verification', gate['missing'])
            self.record('submitted', code=2, evidence=['accepted prompt'])
            self.watch(code=2)
        self.ready()
        self.collect()
        self.assertTrue(self.cli('gate', '--attempt', 'ticket-1/1', '--stage', 'accept',
                                 '--revision', 'head-1')['complete'])
        self.cli('gate', '--attempt', 'ticket-1/1', '--stage', 'accept', '--revision', 'head-2', code=1)

    def test_native_alias_requires_binding_evidence_and_inherited_controls_stay_omitted(self):
        self.register()
        native = self.native(model='claude-sonnet-native')
        self.assertFalse(self.record('startup', native=str(native))['verified'])
        self.assertTrue(self.record('startup', native=str(native),
                                   model_alias_evidence=['installed catalog binds sonnet to claude-sonnet-native'])['verified'])
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': 'builder', 'job': 'ticket', 'settings': {}}))
        self.register('ticket-2/1', 'ticket-2', 'native-2', 'pane-2')
        self.record('startup', 'ticket-2/1', native=str(self.native('ticket-2/1', model=None, effort=None)))
        self.assertEqual(self.cli('status')['attempts'][1]['requested'], {'runtime': 'claude'})

    def test_new_ticket_or_role_rejects_reused_native_session(self):
        self.register()
        self.ready()
        self.collect()
        self.record('retain', code=2, reason='Pending repair', next_use='same-ticket repair recheck', evidence=['phase report'])
        self.acknowledge()
        self.record('retain', reason='Pending repair', next_use='same-ticket repair recheck', evidence=['phase report'])
        self.register('ticket-2/1', 'ticket-2', 'native-1', 'pane-2', code=2)
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': 'reviewer', 'job': 'diff-review', 'settings': {}}))
        self.register('ticket-1/review', 'ticket-1', 'native-1', 'pane-2', code=2)
        self.execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': 'builder', 'job': 'ticket',
                                             'settings': {'model': 'sonnet', 'effort': 'high'}}))
        self.register('ticket-1/2')
        self.assertFalse(self.cli('status')['attempts'][1]['verified'])




class NativeWorkingDirectory(WorkerLifecycleCase):
    """A native child (Claude subagent, Codex or Pi native child) skips `submit`; its own native record says where it
    works. Claude and Codex children carry their orchestrator's directory, so a directory outside the registered
    worktree is a warning, and for an agent that writes nothing that checkout is watched as well (E4: a Codex reviewer
    registered to the review worktree wrote in the workstream worktree)."""

    SESSIONS = {'claude': 'a1b2c3', 'codex': '/root/review', 'pi': 'pi-child-1'}

    def setUp(self):
        super().setUp()
        self.worktree = (self.root / '.worktrees' / 'ticket-1').resolve()
        self.worktree.mkdir(parents=True)

    @staticmethod
    def native_rows(runtime, cwd, session):
        """A native record placing `session` in `cwd`: a Claude subagent transcript, whose rows carry the parent's
        sessionId and the child's agentId; a Codex native child's rollout; a Pi child's session header."""
        return {'claude': [{'type': 'user', 'sessionId': 'parent-1', 'agentId': session, 'isSidechain': True,
                            'cwd': str(cwd)},
                           {'type': 'assistant', 'sessionId': 'parent-1', 'agentId': session, 'isSidechain': True,
                            'cwd': str(cwd), 'perTurnEffort': 'high', 'message': {'model': 'm'}}],
                'codex': [{'type': 'session_meta', 'payload': {'id': 'thread-1', 'agent_path': session,
                                                               'cwd': str(cwd)}},
                          {'type': 'turn_context', 'payload': {'model': 'm', 'effort': 'high', 'cwd': str(cwd)}}],
                'pi': [{'type': 'session', 'id': session, 'cwd': str(cwd)},
                       {'type': 'thinking_level_change', 'id': 'thinking', 'parentId': None, 'thinkingLevel': 'high'},
                       {'type': 'message', 'id': 'probe', 'parentId': 'thinking', 'message': {
                           'role': 'assistant', 'provider': 'fixture', 'model': 'm', 'stopReason': 'stop'}}]}[runtime]

    def native_child(self, runtime, agent='builder', job='ticket', worktree=None):
        """Register a native child of `runtime` for the worktree, as its orchestrator's resolved route says."""
        self.execution.write_text(json.dumps({'host': runtime, 'target_runtime': runtime, 'native_dispatch': True,
                                              'agent': agent, 'job': job,
                                              'settings': {'model': 'fixture/m' if runtime == 'pi' else 'm', 'effort': 'high'}}))
        attempt = f'{runtime}-1/1'
        self.record('register', attempt, assignment=f'{runtime}-1', repo=str(self.root),
                    worktree=str(worktree or self.worktree), head='head-1', execution=str(self.execution),
                    runtime=runtime, worker=self.SESSIONS[runtime], native_session=self.SESSIONS[runtime], owned=True,
                    packet=str(self.packet()))
        return attempt

    def startup(self, attempt, runtime, rows=None, code=0):
        """Record startup from a native child session record."""
        native = self.root / f'{runtime}.jsonl'
        if rows is not None:
            native.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        return self.record('startup', attempt, code=code,
                           **({'native': str(native)} if rows is not None else {}))

    def attempt(self, attempt):
        return next(row for row in self.cli('status')['attempts'] if row['attempt'] == attempt)

    def test_a_native_child_outside_its_worktree_is_a_warning_naming_both_paths(self):
        # Rule: the working directory comes from the native record (Claude: the subagent transcript's cwd; Codex:
        # turn_context.cwd; Pi: the session header's cwd). Outside the registered worktree it is recorded as
        # observed_cwd and `status` warns, naming both paths; it does not block. Inside the worktree, no warning.
        orchestrator = self.root.resolve()
        for runtime, session in self.SESSIONS.items():
            with self.subTest(runtime=runtime):
                attempt = self.native_child(runtime)
                self.assertTrue(self.startup(attempt, runtime, self.native_rows(runtime, orchestrator, session))['verified'])
                saved = self.attempt(attempt)
                self.assertEqual((saved['observed_cwd'], saved.get('warnings')), (str(orchestrator), [
                    f'the native record puts the worker in {orchestrator}, outside its registered worktree '
                    f'{self.worktree}']))
                self.startup(attempt, runtime, self.native_rows(runtime, self.worktree, session))
                saved = self.attempt(attempt)
                self.assertEqual((saved['observed_cwd'], saved.get('warnings')), (str(self.worktree), None))

    def test_a_claude_subdirectory_is_inside_its_worktree_and_a_nested_repository_is_not(self):
        # Rule: as `submit` allows, a Claude cwd inside the worktree is in it; one inside a nested repository is not.
        attempt, session = self.native_child('claude'), self.SESSIONS['claude']
        nested = self.worktree / 'vendor' / 'other'
        (nested / '.git').mkdir(parents=True)
        self.startup(attempt, 'claude', self.native_rows('claude', nested, session))
        self.assertEqual(self.attempt(attempt).get('warnings'), [
            f'the native record puts the worker in {nested}, outside its registered worktree {self.worktree}'])
        child = self.worktree / 'src'
        child.mkdir()
        self.startup(attempt, 'claude', self.native_rows('claude', child, session))
        self.assertNotIn('warnings', self.attempt(attempt))

    def test_a_native_child_requires_its_own_session_record(self):
        attempt, session = self.native_child('claude'), self.SESSIONS['claude']
        self.assertIn('requires native', self.startup(attempt, 'claude', code=2))
        self.assertNotIn('observed_cwd', self.attempt(attempt))
        refused = self.startup(attempt, 'claude', self.native_rows('claude', self.worktree, 'other-child'), code=2)
        self.assertIn('Native evidence does not belong to the registered session a1b2c3: the record '
                      'is session other-child', refused)

    def test_a_pi_child_is_registered_by_its_session_record_which_names_its_session(self):
        # Live E2E 2026-10-26: a pi-subagents run id is not the child's session id, and a Pi orchestrator kept
        # registering the wrong one. Given the record (pi-subagents' status sessionFile), registration reads the id.
        self.execution.write_text(json.dumps({'host': 'pi', 'target_runtime': 'pi', 'native_dispatch': True,
                                              'agent': 'builder', 'job': 'ticket',
                                              'settings': {'model': 'fixture/m', 'effort': 'high'}}))
        native = self.root / 'child.jsonl'
        native.write_text(json.dumps({'type': 'session', 'id': 'pi-child-7', 'cwd': str(self.worktree)}) + '\n')
        fields = dict(assignment='pi-1', repo=str(self.root), worktree=str(self.worktree), head='head-1',
                      execution=str(self.execution), runtime='pi', worker='run-42', owned=True, packet=str(self.packet()))
        refused = self.record('register', 'pi-1/1', native=str(native), native_session='run-42', code=2, **fields)
        self.assertEqual(refused, 'native_session run-42 is not the session pi-child-7 the native record names\n')
        self.record('register', 'pi-1/1', native=str(native), **fields)
        self.assertEqual(self.attempt('pi-1/1')['native_session'], 'pi-child-7')
        session_record(native, 'pi', 'pi-child-7', self.worktree, model='fixture/m')
        self.assertTrue(self.record('startup', 'pi-1/1', native=str(native))['verified'])
        # A record of another session at startup names both.
        other = self.root / 'other.jsonl'
        other.write_text(json.dumps({'type': 'session', 'id': 'pi-child-8', 'cwd': str(self.worktree)}) + '\n')
        refused = self.record('startup', 'pi-1/1', native=str(other), code=2)
        self.assertIn('Native evidence does not belong to the registered session pi-child-7: the record '
                      'is session pi-child-8', refused)

    def git(self, checkout, *args):
        return subprocess.run(['git', '-C', str(checkout), '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    def two_checkouts(self):
        """A repository with a workstream worktree (feat/e4) and a detached review worktree at its base: E4's shape."""
        repo = (self.root / 'repo').resolve()
        repo.mkdir()
        self.git(repo, 'init', '-q', '-b', 'main')
        (repo / 'code.py').write_text('x = 1\n')
        self.git(repo, 'add', '-A')
        self.git(repo, 'commit', '-qm', 'base')
        head = self.git(repo, 'rev-parse', 'HEAD')
        effort, review = repo / '.worktrees' / 'e4', repo / '.worktrees' / 'e4-review'
        self.git(repo, 'worktree', 'add', '-q', '-b', 'feat/e4', str(effort))
        self.git(repo, 'worktree', 'add', '-q', '--detach', str(review), head)
        return head, effort, review

    def collect_request(self, head, attempt):
        report = self.root / 'review.md'
        report.write_text('## Coverage\n\n## Review round 1\n')
        request = self.root / 'collect.json'
        request.write_text(json.dumps({
            'status': 'completed', 'head': head, 'report': str(report), 'evidence': ['review.md'],
            **self.completion_evidence(attempt),
            'disposition': {'kind': 'retain', 'reason': 'recheck', 'next_use': 'recheck', 'evidence': ['review.md']}}))
        return str(request)

    def test_a_read_only_pi_child_working_in_another_checkout_is_held_to_both(self):
        # Rule: a Pi child's session header is its own directory (`cwd:` at launch). A reviewer registered to the
        # review worktree whose header names the workstream worktree has that checkout snapshotted at startup too;
        # collection refuses a change in either one, and collects once both are as they were.
        head, effort, review = self.two_checkouts()
        attempt = self.native_child('pi', 'reviewer', 'diff-review', worktree=review)
        self.startup(attempt, 'pi', self.native_rows('pi', effort, self.SESSIONS['pi']))
        self.record('submitted', attempt, evidence=['native child started'])
        request = self.collect_request(head, attempt)
        (effort / 'notes.md').write_text('reviewer scratch\n')
        refused = self.cli('collect', '--attempt', attempt, '--input', request, code=2)
        self.assertEqual(refused, f'Collection requires an unchanged worktree for a read-only agent (in {effort}, '
                                  'where its native record put it: an untracked file appeared: notes.md); restore it '
                                  'and collect again\n')
        (effort / 'notes.md').unlink()
        (review / 'code.py').write_text('x = 2\n')
        refused = self.cli('collect', '--attempt', attempt, '--input', request, code=2)
        self.assertIn('(tracked files differ from their state at registration)', refused)
        self.git(review, 'checkout', '-q', '--', 'code.py')
        self.cli('collect', '--attempt', attempt, '--input', request)

    def test_a_claude_or_codex_read_only_child_is_held_to_its_registered_worktree_alone(self):
        # Rule (review probe R1): a Claude or Codex child's record names its orchestrator's directory, not its own, so
        # changes there (the orchestrator integrating a ticket into the workstream worktree, as dispatch has it do while a
        # review runs) are not the child's; collection checks the registered worktree only, and status still warns.
        head, effort, review = self.two_checkouts()
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                attempt = self.native_child(runtime, 'reviewer', 'diff-review', worktree=review)
                self.startup(attempt, runtime, self.native_rows(runtime, effort, self.SESSIONS[runtime]))
                self.record('submitted', attempt, evidence=['native child started'])
                (effort / f'{runtime}.py').write_text('y = 1\n')
                self.git(effort, 'add', '-A')
                self.git(effort, 'commit', '-qm', f'integrate a ticket while the {runtime} review runs')
                self.assertEqual(self.attempt(attempt).get('warnings'), [
                    f'the native record puts the worker in {effort}, outside its registered worktree {review}'])
                self.cli('collect', '--attempt', attempt, '--input', self.collect_request(head, attempt))


if __name__ == '__main__':
    unittest.main()
