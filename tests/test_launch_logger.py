#!/usr/bin/env python3
"""The launch logger records each `herdr agent start` a session runs, in the main checkout, without blocking the command."""
import json
import shutil
import time
import unittest

import support
from gate_case import GateRepo

ROOT = support.ROOT


class LaunchLogger(GateRepo):
    def setUp(self):
        super().setUp()
        self.worktree = self.repo / '.worktrees/ticket-1'
        self.worktree.mkdir(parents=True)
        self.serve(panes={'default': {'w1:p5': {'pane_id': 'w1:p5', 'cwd': str(self.worktree)}}})

    def test_each_herdr_agent_start_is_logged_in_the_main_checkout_for_its_orchestrator(self):
        # Rule 1: time, agent name, pane, the Herdr server session it ran in (None for the default one), orchestrator
        # (its Herdr pane and host session, each when known) and the pane's cwd as the named Herdr server reports it; a
        # command that only mentions a launch records nothing.
        self.serve(panes={'s1': {'w1:p5': {'pane_id': 'w1:p5', 'cwd': str(self.worktree)}},
                          'default': {'w1:p5': {'pane_id': 'w1:p5', 'cwd': '/elsewhere'}}})
        before = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        self.launch('herdr --session s1 agent start builder-1 --kind codex --pane w1:p5 -- -m x', cwd=self.worktree)
        self.launch('git commit -qm "herdr agent start x --pane w1:p5" && echo herdr agent start y --pane w1:p5\n'
                    'FOO=1 herdr agent start reviewer-2 --kind claude --pane w1:p6', pane=None)
        after = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        logged = self.logged()
        for record in logged:
            self.assertTrue(before <= record.pop('time') <= after)
        self.assertEqual(logged, [
            {'agent': 'builder-1', 'pane': 'w1:p5', 'herdr_session': 's1',
             'orchestrator': {'pane': 'w1:p1', 'session': 'host-1'}, 'cwd': str(self.worktree)},
            {'agent': 'reviewer-2', 'pane': 'w1:p6', 'herdr_session': 'default',
             'orchestrator': {'pane': None, 'session': 'host-1'}}])

    def test_one_command_with_many_launches_stays_inside_the_hook_timeout(self):
        # Review 7: the Herdr lookups of one hook run share a budget inside the hosts' hook timeout, so a hung
        # Herdr still leaves every launch logged (cwd unknown) before the host kills the hook.
        limit = min(hook['timeout'] for path in ('hooks/hooks.json', 'hosts/codex/hooks.json')
                    for entry in json.loads((ROOT / path).read_text())['hooks']['PreToolUse']
                    for hook in entry['hooks'] if hook['command'].endswith('hooks/herdr_launch.py"'))
        self.serve(hang=True)
        started = time.monotonic()
        self.launch(' && '.join(f'herdr agent start b-{index} --kind codex --pane w1:p{index}' for index in range(1, 6)))
        self.assertLess(time.monotonic() - started, limit - 2)
        self.assertEqual([{key: value for key, value in record.items() if key not in ('time', 'orchestrator', 'herdr_session')}
                          for record in self.logged()],
                         [{'agent': f'b-{index}', 'pane': f'w1:p{index}'} for index in range(1, 6)])

    def test_launches_behind_wrappers_and_shell_strings_are_logged(self):
        # Review 5: `timeout`, `env` with its options, `--session=S` and a shell's `-c` string still run a launch.
        self.serve(panes={'s1': {pane: {'pane_id': pane, 'cwd': str(self.worktree)} for pane in ('w1:p14', 'w1:p16')}})
        for command in ('timeout 60 herdr agent start a-1 --kind codex --pane w1:p11',
                        'timeout -s KILL -k 5 60 herdr agent start a-2 --kind codex --pane w1:p12',
                        'env -u HERDR_PANE_ID -i FOO=1 herdr agent start a-3 --kind codex --pane w1:p13',
                        'herdr --session=s1 agent start a-4 --kind codex --pane w1:p14',
                        'sh -c "herdr agent start a-5 --kind codex --pane w1:p15"',
                        "bash -lc 'cd /tmp && herdr --session s1 agent start a-6 --kind codex --pane w1:p16'",
                        'bash -c "timeout 30 herdr agent start a-7 --kind codex --pane w1:p17" && echo done'):
            self.launch(command, pane=None)
        self.assertEqual([(record['agent'], record['pane'], record.get('cwd')) for record in self.logged()], [
            ('a-1', 'w1:p11', None), ('a-2', 'w1:p12', None), ('a-3', 'w1:p13', None),
            ('a-4', 'w1:p14', str(self.worktree)), ('a-5', 'w1:p15', None), ('a-6', 'w1:p16', str(self.worktree)),
            ('a-7', 'w1:p17', None)])

    def test_a_start_herdr_would_refuse_or_only_explain_is_not_logged(self):
        # Rule: `herdr agent start` needs a name and a pane, and with --help only prints its usage, so none of these
        # starts an agent; logged, each would hold its orchestrator for a worker nothing can register or dismiss.
        for command in ('herdr agent start a-1 --kind codex --pane w1:p11 --help',
                        'herdr agent start a-4 -h --kind codex --pane w1:p14',
                        'herdr agent start --kind codex --pane w1:p12', 'herdr agent start a-2 --kind codex',
                        'herdr agent start a-3 --kind codex --pane w1:p13'):
            self.launch(command, pane=None)
        self.assertEqual([(record['agent'], record['pane']) for record in self.logged()], [('a-3', 'w1:p13')])

    def test_logging_never_blocks_the_command_even_when_herdr_or_the_log_fails(self):
        # Rule 1: a hung Herdr still leaves a record with the cwd unknown; an unwritable log only loses the record.
        self.serve(hang=True)
        started = time.monotonic()
        self.launch('herdr agent start builder-1 --kind codex --pane w1:p5')
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual([{key: value for key, value in record.items() if key != 'time'} for record in self.logged()],
                         [{'agent': 'builder-1', 'pane': 'w1:p5', 'herdr_session': 'default',
                           'orchestrator': {'pane': 'w1:p1', 'session': 'host-1'}}])
        (self.repo / '.scratch/herdr-launches.jsonl').unlink()
        (self.repo / '.scratch/herdr-launches.jsonl').mkdir()
        self.serve()
        self.launch('herdr agent start builder-2 --kind codex --pane w1:p5')

    @unittest.skipUnless(shutil.which('node'), 'Node.js runs the Pi extension')

    def test_the_launch_logger_asks_the_callers_own_server_through_its_socket_without_a_session_option(self):
        # Rule (one Herdr transport): a server the caller's own HERDR_SOCKET_PATH is the socket of is asked through it,
        # as the lifecycle and the stop check ask it, so an isolated config that holds no `s1` name still gives the cwd.
        self.serve(panes={'default': {'w1:p5': {'pane_id': 'w1:p5', 'cwd': str(self.worktree)}}})
        self.launch('herdr --session s1 agent start builder-1 --kind codex --pane w1:p5', pane=None,
                    env={'HERDR_SOCKET_PATH': '/home/u/.config/herdr/sessions/s1/herdr.sock'})
        self.assertEqual((self.bin / 'argv').read_text().splitlines(), ['pane get w1:p5'])
        self.assertEqual(self.logged()[0]['cwd'], str(self.worktree))

    def test_a_command_that_names_the_socket_variable_anywhere_logs_no_launch(self):
        # Rule: the logger reads no shell. A command that sets, unsets, exports or prefixes HERDR_SOCKET_PATH may run its
        # launch on a server the text cannot settle, so nothing is logged; the stop check's live-agent scan names it.
        sock = lambda name: f'/home/u/.config/herdr/sessions/{name}/herdr.sock'
        for command in (f"HERDR_SOCKET_PATH={sock('s2')} herdr agent start a --pane w1:p5",
                        f"env HERDR_SOCKET_PATH={sock('s2')} herdr agent start a --pane w1:p5",
                        f"HERDR_SOCKET_PATH={sock('s2')} herdr --session s3 agent start a --pane w1:p5",
                        f"HERDR_SOCKET_PATH={sock('s2')} env -u HERDR_SOCKET_PATH herdr agent start a --pane w1:p5",
                        f"HERDR_SOCKET_PATH={sock('s2')} env -i herdr agent start a --pane w1:p5",
                        f"HERDR_SOCKET_PATH={sock('s2')} bash -c 'herdr agent start a --pane w1:p5'",
                        f"HERDR_SOCKET_PATH={sock('s2')} bash -c 'unset HERDR_SOCKET_PATH; herdr agent start a --pane w1:p5'",
                        f"bash -c 'export HERDR_SOCKET_PATH={sock('s3')}; herdr agent start a --pane w1:p5'",
                        f"HERDR_SOCKET_PATH={sock('s2')}; herdr agent start a --pane w1:p5",
                        f"HERDR_SOCKET_PATH={sock('s2')} bash -c '(\n  unset HERDR_SOCKET_PATH\n)\nherdr agent start a --pane w1:p5'",
                        f"HERDR_SOCKET_PATH={sock('s2')} bash -c 'while false; do unset HERDR_SOCKET_PATH; done\n"
                        "herdr agent start a --pane w1:p5'",
                        f"env -u HERDR_SOCKET_PATH bash -c 'HERDR_SOCKET_PATH={sock('s3')}\nherdr agent start a --pane w1:p5'",
                        f"HERDR_SOCKET_PATH={sock('s2')} bash -c 'env -u HERDR_SOCKET_PATH\nherdr agent start a --pane w1:p5'",
                        # Review 6: a name the shell expands still names the variable.
                        f"HERDR_SOCKET_PATH={sock('s2')} bash -c 'unset HERDR_SOCKET_{{PATH,UNUSED}}; herdr agent start a --pane w1:p5'"):
            with self.subTest(command=command):
                before = len(self.logged())
                self.launch(command, pane=None, env={'HERDR_SOCKET_PATH': sock('s4')})
                self.assertEqual(before, len(self.logged()))


if __name__ == '__main__':
    unittest.main()
