#!/usr/bin/env python3
"""Exercise the workstream spend report through its CLI over recorded lifecycle state and transcripts."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import support
from native_records import assistant_turn, session_record


SCRIPT = support.script('workstream_spend.py')
LIFECYCLE = support.script('worker_lifecycle.py')


class WorkstreamSpend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='zloop spend ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Outcomes go to a ledger under the state home; keep the user's ledger out of tests.
        self.enterContext(patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'state')}))
        self.state = self.root / 'workers.sqlite'
        self.claude = self.root / 'claude'
        self.codex = self.root / 'codex'
        self.pi = self.root / 'pi'
        for path in (self.claude, self.codex, self.pi):
            path.mkdir()

    def test_the_script_that_named_a_workstream_effort_is_gone(self):
        # Rule: one name, no alias beside it.
        self.assertFalse((SCRIPT.parent / 'effort_spend.py').exists())

    def lifecycle(self, action, *args):
        process = subprocess.run([sys.executable, '-B', str(LIFECYCLE), action, '--state', str(self.state), *args],
                                 capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def record(self, kind, attempt, **fields):
        path = self.root / 'event.json'
        path.write_text(json.dumps(dict(kind=kind, attempt=attempt, **fields)))
        self.lifecycle('record', '--input', str(path))

    def attempt(self, attempt, assignment, session, runtime='claude', job='ticket',
                status='completed', accepted=True):
        execution = self.root / f'{session}.execution.json'
        execution.write_text(json.dumps({'host': runtime, 'native_dispatch': True, 'agent': 'builder', 'job': job,
                                         'settings': {'model': 'fixture/m' if runtime in ('pi', 'opencode') else 'm', 'effort': 'high'}}))
        self.record('register', attempt, assignment=assignment, repo=str(self.root), worktree=str(self.root),
                    head='head-1', execution=str(execution), runtime=runtime, worker=f'pane-{session}',
                    native_session=session, owned=True, packet=str(support.assignment_file(self.root, job)))
        native = session_record(self.root / f'{session}.native.jsonl', runtime, session, self.root,
                                model='fixture/m' if runtime in ('pi', 'opencode') else 'm')
        self.record('startup', attempt, native=str(native))
        self.record('submitted', attempt, evidence=['native:2'])
        report = self.root / f'{session}.md'
        report.write_text('## Implemented\n\n## Fixed round 1\n\nreport\n')
        assistant_turn(native, runtime)
        self.record('result', attempt, status=status, head='head-1', report=str(report), native=str(native), evidence=['checks'])
        if accepted:
            self.record('accepted', attempt, commit='merge-1', evidence=['git log merge-1'])

    def claude_transcript(self, session, responses, directory='-tmp-worktree'):
        """Write a Claude transcript; each response is (id, usage, [tool blocks]) and streams as two blocks."""
        path = self.claude / directory / f'{session}.jsonl'
        path.parent.mkdir(exist_ok=True)
        lines = []
        for identity, usage, tools in responses:
            for index, block in enumerate([{'type': 'text', 'text': 'x'}, *(tools or [{'type': 'text', 'text': 'y'}])]):
                lines.append({'type': 'assistant', 'timestamp': '2026-10-14T01:00:00Z', 'sessionId': session,
                              'message': {'id': identity, 'model': 'claude-sonnet-5-5', 'content': [block],
                                          'usage': dict(usage, output_tokens=usage['output_tokens'] - (index == 0))}})
        path.write_text(''.join(json.dumps(line) + '\n' for line in lines))
        return path

    def codex_transcript(self, session, responses):
        """Write a Codex rollout; each response is (input, cached, output, [commands]) and its count repeats."""
        path = self.codex / '2026' / '10' / '14' / f'rollout-2026-10-14T01-00-00-{session}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [{'timestamp': '2026-10-14T01:00:00Z', 'type': 'session_meta', 'payload': {'id': session}}]
        totals = dict.fromkeys(('input_tokens', 'cached_input_tokens', 'output_tokens'), 0)
        for fresh, cached, output, commands in responses:
            for command in commands:
                lines.append({'timestamp': '2026-10-14T01:00:01Z', 'type': 'response_item', 'payload': {
                    'type': 'function_call', 'name': 'exec_command', 'arguments': json.dumps({'cmd': command})}})
            last = {'input_tokens': fresh + cached, 'cached_input_tokens': cached, 'output_tokens': output}
            totals = {key: totals[key] + last[key] for key in totals}
            count = {'timestamp': '2026-10-14T01:00:02Z', 'type': 'event_msg', 'payload': {
                'type': 'token_count', 'info': {'total_token_usage': totals, 'last_token_usage': last}}}
            lines += [count, count]
        path.write_text(''.join(json.dumps(line) + '\n' for line in lines))
        return path

    def pi_transcript(self, session, entries, directory='--tmp-worktree--'):
        """Write a Pi session; entries are user texts, custom messages or assistant (usage, [tool calls])."""
        path = self.pi / directory / f'2026-10-14T01-00-00-000Z_{session}.jsonl'
        path.parent.mkdir(exist_ok=True)
        lines = [{'type': 'session', 'id': session, 'timestamp': '2026-10-14T01:00:00Z', 'cwd': '/tmp'}]
        for index, entry in enumerate(entries):
            base = {'id': f'e{index}', 'timestamp': '2026-10-14T01:00:01Z'}
            if isinstance(entry, str):
                lines.append(dict(base, type='message', message={
                    'role': 'user', 'content': [{'type': 'text', 'text': entry}]}))
            elif isinstance(entry, dict):
                lines.append(dict(base, type='custom_message', **entry))
            else:
                usage, calls = entry
                lines.append(dict(base, type='message', message={
                    'role': 'assistant', 'model': 'glm', 'usage': usage,
                    'content': [{'type': 'toolCall', 'name': name, 'arguments': arguments}
                                for name, arguments in calls]}))
        path.write_text(''.join(json.dumps(line) + '\n' for line in lines))
        return path

    def claude_host(self, entries):
        """Write a host transcript; entries are user texts or (time, id, output, [tool blocks])."""
        path = self.claude / '-repo' / 'host.jsonl'
        path.parent.mkdir(exist_ok=True)
        lines = []
        for entry in entries:
            if isinstance(entry, str):
                lines.append({'type': 'user', 'timestamp': '2026-10-14T02:00:00Z', 'message': {
                    'role': 'user', 'content': entry}})
                continue
            stamp, identity, output, tools = entry
            for block in tools or [{'type': 'text', 'text': 'reply'}]:
                lines.append({'type': 'assistant', 'timestamp': stamp, 'message': {
                    'id': identity, 'model': 'claude-opus-5-5', 'content': [block],
                    'usage': {'input_tokens': 1, 'cache_read_input_tokens': 100, 'output_tokens': output}}})
                lines.append({'type': 'user', 'timestamp': stamp, 'message': {'role': 'user', 'content': [
                    {'type': 'tool_result', 'tool_use_id': 't', 'content': 'output'}]}})
        path.write_text(''.join(json.dumps(line) + '\n' for line in lines))
        return path

    def report(self, *args, code=0):
        process = subprocess.run([sys.executable, '-B', str(SCRIPT), '--state', str(self.state),
                                  '--claude-root', str(self.claude), '--codex-root', str(self.codex),
                                  '--pi-root', str(self.pi), *args], capture_output=True, text=True)
        self.assertEqual(process.returncode, code, process.stdout + process.stderr)
        return json.loads(process.stdout) if code == 0 else process.stderr

    def test_claude_worker_spend_is_divided_by_accepted_tickets(self):
        self.attempt('t1/1', 't1', 'sess-a')
        self.claude_transcript('sess-a', [
            ('msg-1', {'input_tokens': 10, 'cache_creation_input_tokens': 100,
                       'cache_read_input_tokens': 1000, 'output_tokens': 50}, []),
            ('msg-2', {'input_tokens': 30, 'cache_creation_input_tokens': 0,
                       'cache_read_input_tokens': 3000, 'output_tokens': 70}, [])])
        result = self.report()
        expected = {'responses': 2, 'input': 40, 'cache_write': 100, 'cache_read': 4000, 'output': 120}
        self.assertEqual(result['workers']['total'], expected)
        self.assertEqual(result['accepted_tickets'], ['t1'])
        self.assertEqual(result['per_accepted_ticket'], expected)

    def test_codex_repair_counts_toward_its_ticket_and_unaccepted_work_still_costs(self):
        self.attempt('t1/1', 't1', 'sess-a')
        self.attempt('t1/repair-1', 't1', 'sess-b', runtime='codex', job='repair')
        self.attempt('t2/1', 't2', 'sess-c', status='failed', accepted=False)
        self.claude_transcript('sess-a', [('msg-1', {'input_tokens': 10, 'cache_creation_input_tokens': 0,
                                                     'cache_read_input_tokens': 0, 'output_tokens': 5}, [])])
        self.codex_transcript('sess-b', [(100, 900, 20, ['ls']), (50, 1000, 30, [])])
        self.claude_transcript('sess-c', [('msg-2', {'input_tokens': 7, 'cache_creation_input_tokens': 0,
                                                     'cache_read_input_tokens': 0, 'output_tokens': 3}, [])])
        result = self.report()
        self.assertEqual(result['workers']['by_job']['repair'],
                         {'responses': 2, 'input': 150, 'cache_write': 0, 'cache_read': 1900, 'output': 50})
        self.assertEqual(result['workers']['by_assignment']['t2']['output'], 3)
        self.assertEqual(result['accepted_tickets'], ['t1'])
        self.assertEqual(result['per_accepted_ticket']['output'], 58)

    def test_pi_worker_usage_counts_each_assistant_message(self):
        self.attempt('t1/1', 't1', 'sess-p', runtime='pi')
        self.pi_transcript('sess-p', ['do the ticket',
                                      ({'input': 40, 'output': 6, 'cacheRead': 400, 'cacheWrite': 4}, []),
                                      ({'input': 60, 'output': 4, 'cacheRead': 600, 'cacheWrite': 0}, [])])
        self.assertEqual(self.report()['workers']['total'],
                         {'responses': 2, 'input': 100, 'cache_write': 4, 'cache_read': 1000, 'output': 10})

    def test_missing_or_unreadable_transcripts_are_coverage_gaps_and_reverts_are_not_accepted(self):
        self.attempt('t1/1', 't1', 'sess-gone')
        self.attempt('t2/1', 't2', 'sess-o', runtime='opencode')
        self.lifecycle('attribute', '--attempt', 't1/1', '--kind', 'revert', '--evidence', 'revert of merge-1')
        result = self.report()
        self.assertEqual(result['coverage']['missing'], [
            {'attempt': 't1/1', 'runtime': 'claude', 'native_session': 'sess-gone', 'reason': 'no transcript found'},
            {'attempt': 't2/1', 'runtime': 'opencode', 'native_session': 'sess-o', 'reason': 'unsupported runtime'}])
        self.assertEqual(result['accepted_tickets'], ['t2'])
        self.lifecycle('attribute', '--attempt', 't2/1', '--kind', 'revert', '--evidence', 'revert of merge-1')
        result = self.report()
        self.assertEqual((result['accepted_tickets'], result['per_accepted_ticket']), ([], None))

    def test_host_responses_are_traced_by_trigger_and_first_tool_within_the_window(self):
        self.attempt('t1/1', 't1', 'sess-a')
        bash = lambda command: {'type': 'tool_use', 'name': 'Bash', 'input': {'command': command}}
        receipt = json.dumps({'event_id': 3, 'kind': 'wait-finished', 'attempt': 't1/1', 'receipt': {},
                              'instruction': 'Inspect this receipt and the native worker; acknowledge it.'})
        host = self.claude_host([
            ('2026-10-14T01:59:00Z', 'before', 1000, []),
            'Dispatch the ready tickets.',
            ('2026-10-14T02:00:01Z', 'h1', 10, [bash('cd /repo && python3 /p/scripts/worker_lifecycle.py watch '
                                                     '--state s --attempt t1/1 --input w.json')]),
            ('2026-10-14T02:00:02Z', 'h2', 20, [bash('FOO=1 herdr agent prompt w1 "go"')]),
            receipt,
            ('2026-10-14T02:00:03Z', 'h3', 30, [{'type': 'tool_use', 'name': 'Read', 'input': {'file_path': '/r.md'}}]),
            ('2026-10-14T02:00:04Z', 'h4', 40, [bash('git -C /repo merge --ff-only t1')]),
            ('2026-10-14T02:00:05Z', 'h5', 50, []),
            'Stop hook feedback:\nThese lifecycle workers could finish without waking you:\n- t1/1',
            ('2026-10-14T02:00:06Z', 'h6', 60, [bash('python3 /p/scripts/run_check.py --label unit -- make test')]),
            ('2026-10-14T02:00:07Z', 'h7', 70, [bash('# find the device\ncd /repo\nadb -s emulator devices')]),
            ('2026-10-14T03:00:00Z', 'after', 1000, [])])
        result = self.report('--host', str(host), '--since', '2026-10-14T02:00:00Z',
                             '--until', '2026-10-14T03:00:00Z')
        output = lambda group: {key: value['output'] for key, value in result['host'][group].items()}
        self.assertEqual(output('by_trigger'), {'user': 30, 'wake': 120, 'yield-hook': 130})
        self.assertEqual(output('by_activity'), {'lifecycle:watch': 10, 'herdr:agent prompt': 20, 'read': 30,
                                                 'git:merge': 40, 'reply': 50, 'check': 60, 'command:adb': 70})
        self.assertEqual(result['host']['total']['responses'], 7)
        self.assertEqual(result['per_accepted_ticket']['output'], 280)

    def test_each_yield_gate_header_traces_its_turn_to_the_yield_hook(self):
        # Decision 5: the Stop reason's header changed twice; a transcript holding any of them is a yield-hook turn.
        self.attempt('t1/1', 't1', 'sess-a')
        host = self.claude_host([
            'Stop hook feedback:\nThese lifecycle workers could finish without waking you:\n- t1/1',
            ('2026-10-14T02:00:01Z', 'h1', 10, []),
            'Stop hook feedback:\nThese workers could finish or run without waking you:\n- t1/1',
            ('2026-10-14T02:00:02Z', 'h2', 20, []),
            'Stop hook feedback:\nStopping now leaves these gaps:\n- fix: delivery incomplete under autofix',
            ('2026-10-14T02:00:03Z', 'h3', 40, [])])
        result = self.report('--host', str(host), '--since', '2026-10-14T02:00:00Z', '--until', '2026-10-14T03:00:00Z')
        self.assertEqual({key: value['output'] for key, value in result['host']['by_trigger'].items()},
                         {'yield-hook': 70})

    def test_pi_and_codex_hosts_are_traced_the_same_way(self):
        self.attempt('t1/1', 't1', 'sess-a')
        receipt = json.dumps({'event_id': 3, 'kind': 'hold', 'attempt': 't1/1'})
        usage = {'input': 1, 'output': 7, 'cacheRead': 0, 'cacheWrite': 0}
        pi = self.pi_transcript('host-p', [
            receipt, (usage, [('bash', {'command': 'python3 /p/scripts/worker_lifecycle.py ack --event-id 3'})]),
            {'customType': 'zloop_yield_gate', 'content': 'x'}, (usage, [('read', {'path': '/r.md'})]),
            {'customType': 'background-task-notification', 'content': '<background-task-notification>x'},
            (usage, [])])
        codex = self.codex_transcript('host-c', [(10, 0, 5, ['git diff --stat']), (10, 0, 6, [])])
        window = ('--since', '2026-10-14T00:00:00Z', '--until', '2026-10-15T00:00:00Z')
        result = self.report('--host', str(pi), '--host', str(codex), *window)
        output = lambda group: {key: value['output'] for key, value in result['host'][group].items()}
        self.assertEqual(output('by_trigger'), {'wake': 14, 'user': 11, 'yield-hook': 7})
        self.assertEqual(output('by_activity'), {'reply': 13, 'lifecycle:ack': 7, 'read': 7, 'git:diff': 5})
        self.assertIn('--since', self.report('--host', str(pi), code=2))


    def test_damaged_lines_split_records_and_subagent_files_do_not_break_or_shrink_the_count(self):
        self.attempt('t1/1', 't1', 'sess-a')
        usage = {'input_tokens': 1, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0, 'output_tokens': 5}
        path = self.claude_transcript('sess-a', [('msg-1', usage, [])])
        lines = path.read_text().splitlines(keepends=True)
        user = {'type': 'user', 'message': {'role': 'user', 'content': 'line\u2028separator'}}
        path.write_text(lines[0] + json.dumps(user, ensure_ascii=False) + '\n' + ''.join(lines[1:])
                        + '{"type":"assistant","message":{"id":"x')
        child = self.claude / '-tmp-worktree' / 'sess-a' / 'subagents' / 'agent-1.jsonl'
        child.parent.mkdir(parents=True)
        child.write_text(json.dumps({'type': 'assistant', 'timestamp': '2026-10-14T01:00:00Z', 'message': {
            'id': 'sub-1', 'model': 'claude-haiku-5-5', 'content': [], 'usage': dict(usage, output_tokens=9)}}) + '\n')
        result = self.report()
        self.assertEqual((result['workers']['total']['responses'], result['workers']['total']['output']), (2, 14))
        self.assertEqual(result['coverage']['unreadable_lines'], [{'path': str(path), 'line': 4}])

    def test_codex_pages_are_one_stream_and_compressed_pages_are_a_coverage_gap(self):
        self.attempt('t1/1', 't1', 'sess-b', runtime='codex')
        first = self.codex_transcript('sess-b', [(10, 0, 5, [])])
        lines = first.read_text().splitlines(keepends=True)
        later = {'timestamp': '2026-10-14T01:00:09Z', 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
            'total_token_usage': {'input_tokens': 30, 'cached_input_tokens': 0, 'output_tokens': 12},
            'last_token_usage': {'input_tokens': 20, 'cached_input_tokens': 0, 'output_tokens': 7}}}}
        first.rename(first.with_name(first.stem + '_page-1.jsonl'))
        first.with_name(first.stem + '_page-2.jsonl').write_text(lines[-1] + json.dumps(later) + '\n')
        first.with_name(first.stem + '_page-3.jsonl.zst').write_bytes(b'\x28\xb5\x2f\xfd')
        result = self.report()
        self.assertEqual((result['workers']['total']['responses'], result['workers']['total']['output']), (2, 12))
        self.assertEqual([gap['reason'] for gap in result['coverage']['missing']], ['compressed pages not read'])

    def test_host_commands_are_named_by_the_program_that_runs(self):
        self.attempt('t1/1', 't1', 'sess-a')
        bash = lambda command: {'type': 'tool_use', 'name': 'Bash', 'input': {'command': command}}
        host = self.claude_host(['go'] + [(f'2026-10-14T02:00:0{index}Z', f'h{index}', index + 1, [bash(command)])
                                          for index, command in enumerate([
            "# it's 'x'; the end\nls -la",
            'TOK=$(sim-mirror token create) && curl -s a',
            "echo 'a;b' | grep a",
            'cd /repo  # enter the checkout\ngit -C /repo diff'])])
        codex = self.codex_transcript('host-c', [])
        exec_call = {'timestamp': '2026-10-14T02:00:05Z', 'type': 'response_item', 'payload': {
            'type': 'custom_tool_call', 'name': 'exec',
            'input': 'text(await tools.exec_command({cmd:"git -C /r status --short","max_output_tokens":100}));'}}
        count = {'timestamp': '2026-10-14T02:00:06Z', 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
            'total_token_usage': {'input_tokens': 1, 'output_tokens': 4},
            'last_token_usage': {'input_tokens': 1, 'output_tokens': 4}}}}
        poll = json.loads(json.dumps(exec_call))
        poll['payload']['input'] = 'const id=load("s"); const r=await tools.write_stdin({session_id:id});'
        polled = json.loads(json.dumps(count))
        polled['payload']['info']['total_token_usage']['output_tokens'] = 10
        polled['payload']['info']['last_token_usage']['output_tokens'] = 6
        codex.write_text(codex.read_text() + ''.join(json.dumps(line) + '\n'
                                                     for line in (exec_call, count, poll, polled)))
        result = self.report('--host', str(host), '--host', str(codex),
                             '--since', '2026-10-14T02:00:00Z', '--until', '2026-10-14T03:00:00Z')
        self.assertEqual({key: value['output'] for key, value in result['host']['by_activity'].items()},
                         {'tool:write_stdin': 6, 'git:status': 4, 'git:diff': 4, 'command:echo': 3,
                          'command:sim-mirror': 2, 'read': 1})

    def test_lifecycle_calls_through_aliases_are_named_and_shell_keywords_are_not_programs(self):
        self.attempt('t1/1', 't1', 'sess-a')
        bash = lambda command: {'type': 'tool_use', 'name': 'Bash', 'input': {'command': command}}
        lifecycle = '/plugin/scripts/worker_lifecycle.py'
        commands = [
            f'P=/r; L={lifecycle}; PY=/v/bin/python; $PY -B $L ack --state $P/w.sqlite --event-id 3',
            f'L="/v/bin/python -B {lifecycle}"; $L status --state /r/w.sqlite',
            f'cd /r; L() {{ /v/bin/python -B {lifecycle} --state $PWD/w.sqlite "$@"; }}\nA=t1/1\n'
            'L record --input /r/rec.json',
            f'python3 {lifecycle} --state /r/w.sqlite gate --stage yield',
            f'grep -n "def watch" {lifecycle} | head -5',
            'for t in 1.3.1 1.3.2; do gh release view $t; done',
            'set -e; cd /x; git clone -q git@host:o/r.git r2',
            'export DEVELOPER_DIR=/Xcode HF_HUB_OFFLINE=1; cd /y; make install',
            f'f() {{ {lifecycle} watch "$@"; }}; git fetch -q']
        host = self.claude_host(['go'] + [(f'2026-10-14T02:00:0{index}Z', f'h{index}', 2 ** index, [bash(command)])
                                          for index, command in enumerate(commands)])
        result = self.report('--host', str(host), '--since', '2026-10-14T02:00:00Z', '--until', '2026-10-14T03:00:00Z')
        self.assertEqual({key: value['output'] for key, value in result['host']['by_activity'].items()},
                         {'lifecycle:ack': 1, 'lifecycle:status': 2, 'lifecycle:record': 4, 'lifecycle:gate': 8,
                          'read': 16, 'command:gh': 32, 'git:clone': 64, 'command:make': 128, 'git:fetch': 256})

    def test_wrappers_heredocs_and_continuations_do_not_hide_the_command(self):
        self.attempt('t1/1', 't1', 'sess-a')
        bash = lambda command: {'type': 'tool_use', 'name': 'Bash', 'input': {'command': command}}
        lifecycle = '/a/scripts/worker_lifecycle.py'
        commands = [
            f'nohup /v/bin/python {lifecycle} submit --state /s/w.sqlite --input /t/w.json > /t/o 2>&1 &',
            f'timeout 60 python3 {lifecycle} status --state /s/w.sqlite',
            f"cat > /t/n.md <<'EOF'\nthe worker's report\nEOF\npython3 {lifecycle} record --input /t/r.json",
            f"python3 - <<'EOF'\nprint(x.decode().strip())\nEOF\npython3 {lifecycle} retire --input /t/r.json",
            "cat > /t/a.txt <<'EOF'\nport the fork's cache\nEOF\n"
            '.venv/bin/python -B scripts/agent_models.py --job ticket --route --assignment /t/a.txt',
            f'python3 {lifecycle} --help 2>&1 | head -5',
            'case "$x" in\n  a) echo one ;;\n  *) gh pr list ;;\nesac',
            'for f in a.proto \\\n  b.proto; do curl -sO "$f"; done',
            f'echo $((1 << n))\npython3 {lifecycle} watch --input /t/w.json',
            f'python3 -c "print(1 << bits)"\npython3 {lifecycle} ack --event-id 2']
        host = self.claude_host(['go'] + [(f'2026-10-14T02:00:{index:02}Z', f'h{index}', 2 ** index, [bash(command)])
                                          for index, command in enumerate(commands)])
        result = self.report('--host', str(host), '--since', '2026-10-14T02:00:00Z', '--until', '2026-10-14T03:00:00Z')
        self.assertEqual({key: value['output'] for key, value in result['host']['by_activity'].items()},
                         {'lifecycle:submit': 1, 'lifecycle:status': 2, 'lifecycle:record': 4, 'lifecycle:retire': 8,
                          'routing': 16, 'lifecycle:?': 32, 'command:echo': 64, 'command:curl': 128,
                          'lifecycle:watch': 256, 'lifecycle:ack': 512})

    def test_transcripts_from_before_the_underscore_rename_keep_their_activities(self):
        self.attempt('t1/1', 't1', 'sess-a')
        bash = lambda command: {'type': 'tool_use', 'name': 'Bash', 'input': {'command': command}}
        commands = ['L=/p/scripts/worker-lifecycle.py; python3 $L ack --event-id 3',
                    'python3 /p/scripts/worker-lifecycle.py --state /s/w.sqlite collect',
                    'python3 /p/scripts/run-check.py --label unit -- make test',
                    'python3 /p/scripts/agent-models.py --job ticket --route']
        host = self.claude_host(['go'] + [(f'2026-10-14T02:00:0{index}Z', f'h{index}', 2 ** index, [bash(command)])
                                          for index, command in enumerate(commands)])
        result = self.report('--host', str(host), '--since', '2026-10-14T02:00:00Z', '--until', '2026-10-14T03:00:00Z')
        self.assertEqual({key: value['output'] for key, value in result['host']['by_activity'].items()},
                         {'lifecycle:ack': 1, 'lifecycle:collect': 2, 'check': 4, 'routing': 8})


if __name__ == '__main__':
    unittest.main()
