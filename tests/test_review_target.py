#!/usr/bin/env python3
"""Target/options regressions with real temporary Git history and a local gh stub."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

import support

SCRIPT = support.script('review_target.py')
rt = support.load_script('review_target.py', 'review_target')
INTERRUPTED = '''import os, runpy, sys
def interrupted(*args): raise OSError('interrupted')
os.replace = interrupted
sys.argv = sys.argv[1:]
sys.path.insert(0, os.path.dirname(sys.argv[0]))
runpy.run_path(sys.argv[0], run_name='__main__')'''


class ReviewTarget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='zloop review ')
        self.addCleanup(self.tmp.cleanup)
        self.directory = str(Path(self.tmp.name).resolve())
        self.repo = Path(self.directory)
        self.git('init', '-b', 'trunk')
        self.git('config', 'user.name', 'Review Test')
        self.git('config', 'user.email', 'review@example.invalid')
        (self.repo / '.git/info/exclude').write_text('.scratch/\n')
        self.git('remote', 'add', 'origin', 'git@github.com:O/R.git')
        (self.repo / 'code.txt').write_text('base\n')
        self.git('add', '.')
        self.git('commit', '-m', 'base')
        self.base = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-b', 'feature')
        (self.repo / 'code.txt').write_text('changed\n')
        (self.repo / ' leading space.txt').write_text('file\n')
        self.git('add', '.')
        self.git('commit', '-m', 'feature')
        self.head = self.git('rev-parse', 'HEAD')
        self.git('branch', '123')
        (self.repo / 'unrelated.txt').write_text('preserve me\n')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', self.directory, *args], text=True).strip()

    def args(self, *extra):
        return rt.parser().parse_args(['--repo', self.directory, *extra])

    def launch(self, *extra, code=0, host='codex', target='feature', identified=True, session='review-session',
               interrupted=False, script=SCRIPT, **env_extra):
        env = {key: value for key, value in support.without_host_sessions(support.isolated_env(self.repo)).items()
               if not key.startswith(('ZLOOP_', 'HERDR_'))}
        env.update(HOME=str(self.repo), **env_extra)
        if identified:
            env.update(CODEX_SESSION_ID=session, HERDR_PANE_ID='w1:p9')
        # An interrupted start: every rename over a target fails, as when the process dies at that step.
        runner = [sys.executable, '-c', INTERRUPTED, str(script)] if interrupted else [sys.executable, str(script)]
        result = subprocess.run([*runner, '--repo', self.directory,
                                 *(['--host', host] if host else []),
                                 target, '--base', 'trunk', *extra], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout) if code == 0 else result.stderr

    def rejects(self, fn):
        with self.assertRaises((ValueError, SystemExit)):
            fn()

    def test_branch_target_resolves_head_base_and_changed_files(self):
        plain = rt.resolve(self.args('feature', '--base', 'trunk'))
        assert plain['head'] == self.head and plain['base'] == plain['diff_base'] == self.base
        assert 'mode' not in plain and not plain['autofix']
        assert plain['comments'] == 'draft'
        assert set(plain['files']) == {'code.txt', ' leading space.txt'}
        assert rt.resolve(self.args('refs/heads/123', '--base', 'trunk'))['head'] == self.head
        self.rejects(lambda: rt.resolve(self.args('missing', '--base', 'trunk')))

    def test_only_autofix_and_comment_options_are_accepted(self):
        self.rejects(lambda: self.args('feature', '--auto'))
        self.rejects(lambda: self.args('feature', '--interactive'))
        self.rejects(lambda: self.args('feature', '--unattended'))
        self.rejects(lambda: self.args('feature', '--comments', 'auto'))
        self.rejects(lambda: self.args('feature', '--comments'))
        for flags in ([], ['--autofix']):
            for comments in ('draft', 'confirm', 'post'):
                resolved = rt.resolve(self.args('feature', '--base', 'trunk', *flags,
                                                '--comments', comments))
                assert resolved['comments'] == comments
                assert resolved['autofix'] == bool(flags)
                assert 'mode' not in resolved

    def test_pull_request_targets_must_match_the_repository_and_head(self):
        for target in ['http://github.com/o/r/pull/1', 'https://u:p@github.com/o/r/pull/1',
                       'https://github.com/o/r/issues/1']:
            self.rejects(lambda: rt.pr_target(target))
        pr = dict(number=123, url='https://github.com/o/r/pull/123', title='Change', body='Goal',
                  baseRefName='trunk', baseRefOid=self.base, headRefName='feature', headRefOid=self.head)
        actual_run = rt.run
        def stub(cwd, *command):
            if command[:3] == ('gh', 'repo', 'view'):
                return json.dumps(dict(url='https://github.com/o/r', defaultBranchRef=dict(name='trunk')))
            if command[:3] == ('gh', 'pr', 'view'):
                return json.dumps(pr)
            if command[:3] == ('gh', 'pr', 'list'):
                return '[{"number":123}]'
            return actual_run(cwd, *command)
        with patch.object(rt, 'run', stub), patch.object(rt.shutil, 'which', return_value='/fake/gh'):
            for target in ['123', pr['url'], pr['url'] + '/files', 'feature']:
                resolved = rt.resolve(self.args(target, '--comments', 'post'))
                assert resolved['head'] == self.head and resolved['pr']['number'] == 123
                assert resolved['comments'] == 'post'
            self.rejects(lambda: rt.resolve(self.args('https://github.com/other/repo/pull/123')))
            self.git('remote', 'set-url', 'origin', 'https://github.com/other/repo.git')
            self.rejects(lambda: rt.resolve(self.args('123')))
            self.git('remote', 'set-url', 'origin', 'ssh://git@github.com/o/r.git')
            assert rt.resolve(self.args('123'))['head'] == self.head
            pr['headRefOid'] = self.base
            self.rejects(lambda: rt.resolve(self.args('feature')))

    def test_cli_resolution_preserves_the_checkout(self):
        original_status = self.git('status', '--porcelain')
        self.launch()
        assert self.git('symbolic-ref', '--short', 'HEAD') == 'feature'
        assert self.git('status', '--porcelain') == original_status
        assert (self.repo / 'unrelated.txt').read_text() == 'preserve me\n'

    def test_start_writes_the_resolved_report_binding_route_and_commands(self):
        result = self.launch('--autofix')
        report, state = Path(result['report']), Path(result['state'])
        self.assertEqual(report.parent.parent, self.repo / '.scratch/reviews')
        self.assertRegex(report.parent.name, r'^[a-zA-Z0-9_-]+$')
        self.assertEqual(report.name, 'review.md')
        self.assertEqual(state, report.with_name('workers.sqlite'))
        text = report.read_text()
        for line in (f'review_head: {self.head}', f'review_base: {self.base}', f'review_diff_base: {self.base}',
                     'review_status: incomplete', 'documentation_status: incomplete', 'review_by: none',
                     'autofix: true', 'blocker: none', '## Scope and priorities', '### Documentation gate',
                     '## Review round 1', '## Follow-ups', '## E2E', 'code.txt', ' leading space.txt'):
            self.assertIn(line, text)
        binding = json.loads(Path(result['binding']).read_text())
        self.assertEqual(binding['sessions'], ['review-session'])
        self.assertEqual(binding['pane'], 'w1:p9')
        self.assertEqual((binding['report'], binding['state']), (str(report), str(state)))
        self.assertEqual(binding['route'], result['route'])
        self.assertEqual(result['route']['status'], 'resolved')
        self.assertEqual(result['route']['result']['host'], 'codex')
        self.assertEqual(result['route']['result']['job'], 'diff-review')
        self.assertIn('--route --job diff-review', result['next_commands']['route'])
        for action, verb in (('register', 'record'), ('submit', 'submit'), ('collect', 'collect')):
            self.assertIn(f'worker_lifecycle.py {verb} --state', result['next_commands'][action])
            self.assertIn(str(state), result['next_commands'][action])

    def test_a_workstream_report_cannot_be_overwritten_for_another_target(self):
        result = self.launch('--workstream', 'fx')
        report = self.repo / '.scratch/fx/reports/spec-review.md'
        self.assertEqual(Path(result['report']), report)
        self.git('branch', 'other-target')
        before = {path.name: path.read_bytes() for path in report.parent.iterdir() if path.is_file()}
        refused = self.launch('--workstream', 'fx', target='other-target', code=2)
        self.assertIn('different target', refused)
        self.assertEqual(before, {path.name: path.read_bytes() for path in report.parent.iterdir() if path.is_file()})

    def test_restarting_the_same_review_preserves_its_report_and_stable_path(self):
        first = self.launch()
        report = Path(first['report'])
        text = report.read_text().replace('review_by: none', 'review_by: review/1') + '\nUser decision: retain scope.\n'
        report.write_text(text)
        second = self.launch()
        self.assertEqual(second['report'], first['report'])
        self.assertEqual(report.read_text(), text)

    def test_start_restores_a_missing_report_for_its_existing_binding(self):
        first = self.launch()
        report = Path(first['report'])
        report.unlink()
        second = self.launch()
        self.assertEqual(second['report'], first['report'])
        self.assertTrue(report.is_file())
        self.assertIn('review_by: none', report.read_text())

    def test_restarting_a_shared_review_keeps_every_session_that_started_it_bound(self):
        self.launch()
        second = self.launch(session='second-session')
        binding = json.loads(Path(second['binding']).read_text())
        self.assertEqual(binding['sessions'], ['review-session', 'second-session'])

    def test_an_interrupted_start_leaves_the_report_and_binding_whole(self):
        # Audit A1: the binding holds the unfinished review at stop; a write cut short must not truncate it or the report.
        first = self.launch()
        files = [Path(first['report']), Path(first['binding'])]
        before = [path.read_bytes() for path in files]
        self.git('commit', '--allow-empty', '-m', 'new head')
        refused = self.launch(code=2, interrupted=True)
        self.assertIn('interrupted', refused)
        self.assertEqual([path.read_bytes() for path in files], before)
        self.assertEqual(sorted(path.name for path in files[0].parent.iterdir() if path.name.startswith('.')), [])

    def test_concurrent_starts_by_different_sessions_all_stay_bound(self):
        # Audit A6: a start that succeeded holds its session at stop, whichever start wrote the binding last.
        sessions = [f'session-{number}' for number in range(4)]
        with ThreadPoolExecutor(len(sessions)) as pool:
            results = [future.result() for future in [pool.submit(self.launch, session=name) for name in sessions]]
        binding = json.loads(Path(results[0]['binding']).read_text())
        self.assertEqual(sorted(binding['sessions']), sessions)

    def test_rerunning_a_start_that_stopped_after_the_report_repairs_the_binding(self):
        # Audit A5: the stop hook advises the same start command, so it must finish a start that wrote only the report.
        for workstream in ((), ('--workstream', 'fx')):
            with self.subTest(workstream=workstream):
                first = self.launch(*workstream, session='first-session')
                report, binding = Path(first['report']), Path(first['binding'])
                text = report.read_text() + '\nUser decision: retain scope.\n'
                report.write_text(text)
                binding.unlink()
                again = self.launch(*workstream, session='second-session')
                self.assertEqual(again['report'], str(report))
                self.assertEqual(report.read_text(), text)
                self.assertEqual(json.loads(binding.read_text())['sessions'], ['second-session'])
                shutil.rmtree(report.parent)

    def test_a_report_left_by_another_target_is_not_adopted_without_its_binding(self):
        # Audit A5: repairing a missing binding must not hand one target's report to another.
        self.git('branch', 'other-target')
        report = Path(self.launch('--workstream', 'fx')['report'])
        report.with_name('review.json').unlink()
        before = report.read_bytes()
        refused = self.launch('--workstream', 'fx', target='other-target', code=2)
        self.assertIn('different target', refused)
        self.assertEqual(report.read_bytes(), before)
        self.assertFalse(report.with_name('review.json').exists())

    def test_a_start_interrupted_twice_still_leaves_the_report_resolution_at_the_current_head(self):
        # Audit A5 round 1: the report's resolution is regenerated from the current one, not found by the binding's
        # older copy, so a report ahead of its binding is not left naming a head the binding and front matter dropped.
        first = self.launch('--workstream', 'fx')
        report, binding = Path(first['report']), Path(first['binding'])
        at_first_head = binding.read_bytes()
        self.git('commit', '--allow-empty', '-m', 'second head')
        self.launch('--workstream', 'fx')
        binding.write_bytes(at_first_head)  # the second start died after the report, before the binding
        self.git('commit', '--allow-empty', '-m', 'third head')
        head = self.git('rev-parse', 'HEAD')
        self.launch('--workstream', 'fx')
        self.assertIn(f'review_head: {head}', report.read_text())
        self.assertEqual(json.loads(binding.read_text())['resolution']['head'], head)
        self.assertIn(f'"head": "{head}"', report.read_text())
        self.assertNotIn(first['head'], report.read_text().split('## Scope and priorities', 1)[1].split('\n## ', 1)[0])

    def test_a_truncated_binding_or_report_is_refused_naming_what_to_remove_and_the_rerun_repairs_it(self):
        # Audit A5: a file cut short is never a traceback; the refusal names the file, and the same start then repairs.
        for cut in ('review.json', 'report'):
            with self.subTest(cut=cut):
                first = self.launch()
                report, binding = Path(first['report']), Path(first['binding'])
                broken = binding if cut == 'review.json' else report
                broken.write_text(broken.read_text()[:30])
                refused = self.launch(code=2)
                self.assertIn(str(broken), refused)
                self.assertIn('remove it and rerun the same start command', refused)
                self.assertNotIn('Traceback', refused)
                broken.unlink()
                again = self.launch(session='second-session')
                self.assertEqual(Path(again['report']).read_text().count('## Review round 1'), 1)
                self.assertTrue(binding.is_file() and report.is_file())
                shutil.rmtree(report.parent)

    def test_a_binding_with_non_string_sessions_is_refused_without_changing_the_report(self):
        first = self.launch()
        report, binding = Path(first['report']), Path(first['binding'])
        before = report.read_bytes()
        saved = json.loads(binding.read_text())
        saved['sessions'] = [7]
        binding.write_text(json.dumps(saved))
        refused = self.launch(code=2)
        self.assertIn(f'{binding}: not a whole review binding; remove it and rerun the same start command', refused)
        self.assertEqual(report.read_bytes(), before)

    def test_a_truncated_report_without_its_binding_is_refused_naming_the_report(self):
        # Audit A5: the report alone cannot say which target it belongs to when it is cut short.
        first = self.launch()
        report = Path(first['report'])
        Path(first['binding']).unlink()
        report.write_text(report.read_text()[:30])
        refused = self.launch(code=2)
        self.assertIn(f'{report}: not a whole review report', refused)
        self.assertNotIn('Traceback', refused)

    def test_a_new_workstream_head_reopens_the_verdict_and_retains_human_decisions(self):
        result = self.launch('--workstream', 'fx')
        report = Path(result['report'])
        report.write_text(report.read_text().replace('review_by: none', 'review_by:\n  - review/1')
                          .replace('review_status: incomplete', 'review_status: clean')
                          + '\nUser decision: retain scope.\n')
        self.git('commit', '--allow-empty', '-m', 'new head')
        head = self.git('rev-parse', 'HEAD')
        restarted = self.launch('--workstream', 'fx')
        self.assertEqual(restarted['report'], str(report))
        text = report.read_text()
        self.assertIn(f'review_head: {head}', text)
        self.assertIn('review_status: incomplete', text)
        self.assertIn('review_by: none', text)
        self.assertIn('User decision: retain scope.', text)

    def test_a_new_standalone_head_refreshes_one_binding_and_keeps_decisions_and_follow_ups(self):
        first = self.launch()
        report = Path(first['report'])
        report.write_text(report.read_text().replace('review_status: incomplete', 'review_status: clean')
                          .replace('review_by: none', 'review_by: old-review/1')
                          .replace('documentation_status: incomplete', 'documentation_status: passed')
                          .replace('e2e_status: not-run', 'e2e_status: passed')
                          .replace('e2e_head: none', f'e2e_head: {self.head}')
                          .replace('e2e_by: none', 'e2e_by: old-e2e/1')
                          + '\nUser decision: retain scope.\nFollow-up: inspect the consumer.\n')
        self.git('commit', '--allow-empty', '-m', 'repair after review')
        head = self.git('rev-parse', 'HEAD')
        restarted = self.launch()
        self.assertEqual(restarted['report'], first['report'])
        self.assertEqual(restarted['binding'], first['binding'])
        self.assertEqual(list((self.repo / '.scratch/reviews').glob('*/review.json')), [Path(first['binding'])])
        for line in (f'review_head: {head}', 'review_status: incomplete', 'documentation_status: incomplete',
                     'review_by: none', 'e2e_status: not-run', 'e2e_head: none', 'e2e_by: none',
                     'User decision: retain scope.', 'Follow-up: inspect the consumer.'):
            self.assertIn(line, report.read_text())
        binding = json.loads(Path(restarted['binding']).read_text())
        self.assertEqual(binding['resolution']['head'], head)
        env = {key: value for key, value in os.environ.items() if not key.startswith(('ZLOOP_', 'HERDR_'))}
        checked = subprocess.run([sys.executable, str(support.ROOT / 'hooks/yield_gate.py'), 'check',
                                  str(self.repo), 'review-session'], capture_output=True, text=True, env=env)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        gaps = json.loads(checked.stdout)['missing']
        self.assertEqual(len(gaps), 1)
        self.assertIn(str(report), gaps[0])

    def test_no_reviewer_route_records_its_reason_without_allowing_an_unselected_pool(self):
        config = self.repo / '.zloop/executors.json'
        config.parent.mkdir()
        config.write_text(json.dumps({'models': {'local': {'description': 'Local reviewer',
                                                          'runtimes': {'pi': {'id': 'local/reviewer'}}}},
                                     'runtimes': {'pi': {'executors': {'pi': {'type': 'subagent'}}}},
                                     'agents': {'reviewer': {'model': 'local'}}}))
        result = self.launch('--workstream', 'fx')
        self.assertEqual(result['route']['status'], 'no_route')
        self.assertIn('No configured route', result['route']['reason'])
        binding = json.loads(Path(result['binding']).read_text())
        self.assertEqual(binding['route'], result['route'])
        # User (2026-11-07): delivery asks the router again for this host before local review counts.
        self.assertEqual(binding['host'], 'codex')
        config.write_text(json.dumps({'agents': {'reviewer': {'candidates': {
            'first': {'executor': {'type': 'subagent'}}, 'second': {'executor': {'type': 'subagent'}}}}}}))
        selected = self.launch('--workstream', 'fx')
        self.assertEqual(selected['route']['status'], 'selection_required')

    def test_the_router_exit_status_decides_no_route_whatever_its_refusal_says(self):
        old = "Model 'm': No configured route from 'codex' to model runtimes ['pi'] in this environment"
        for status, refusal, recorded in ((3, 'nowhere for the reviewer to run', True), (2, old, False), (1, 'boom', False)):
            with self.subTest(status=status):
                root = Path(self.tmp.name) / f'stub{status}'
                plugin = support.stub_router_plugin(root, status, refusal)
                result = self.launch('--workstream', f'w{status}', script=plugin / 'scripts/review_target.py',
                                     code=0 if recorded else 2)
                if recorded:
                    self.assertEqual(result['route'], {'status': 'no_route', 'reason': refusal})
                else:
                    self.assertIn(refusal, result)
                self.assertIn('agent_models.py', (root / 'stub.log').read_text())

    def test_reviewer_configuration_errors_stop_start_without_recording_no_route(self):
        config = self.repo / '.zloop'
        config.mkdir()
        for name, content, reason in (
                ('models.json', '{"agents":{"reviewer":{"model":"typo-model"}}}', 'expected host keys'),
                ('executors.json', '{broken', 'Expecting property name enclosed in double quotes'),
                ('executors.json', '{"agents":{"reviewer":{"model":"typo-model"}}}',
                 "Unknown model definition 'typo-model'")):
            with self.subTest(name=name, content=content):
                for path in config.iterdir():
                    path.unlink()
                (config / name).write_text(content)
                refused = self.launch(code=2)
                self.assertIn(reason, refused)
                self.assertFalse((self.repo / '.scratch').exists())

    def test_start_records_the_requested_review_level_and_defaults_to_high_on_restart(self):
        for level in ('low', 'medium', 'high', 'xhigh', 'max', None):
            with self.subTest(level=level):
                result = self.launch(*(['--effort', level] if level else []))
                self.assertIn(f'review_level: {level or "high"}\n', Path(result['report']).read_text())
                self.assertNotIn('effort', result['route']['result']['settings'])
        report, binding = Path(result['report']), Path(result['binding'])
        before = report.read_bytes(), binding.read_bytes()
        refused = self.launch('--effort', 'turbo', code=2)
        self.assertIn("invalid choice: 'turbo'", refused)
        self.assertEqual((report.read_bytes(), binding.read_bytes()), before)

    def test_a_review_cannot_start_without_a_session_or_pane_to_hold(self):
        refused = self.launch(identified=False, code=2)
        self.assertIn('no host session ID or Herdr pane; the review stop hold cannot bind this session', refused)
        self.assertFalse((self.repo / '.scratch/reviews').exists())

    def test_an_opencode_session_is_held_by_its_exported_id(self):
        # Gap 18: OpenCode exports OPENCODE_SESSION_ID to the reviewer's shell like the other hosts' variables.
        result = self.launch(identified=False, host='opencode', OPENCODE_SESSION_ID='opencode-1')
        self.assertEqual(json.loads(Path(result['binding']).read_text())['sessions'], ['opencode-1'])

    def test_a_review_started_in_a_pi_subagents_child_binds_the_sessions_registration_records(self):
        result = self.launch(identified=False, host='opencode', PI_SESSION_ID='pi-child', PI_SUBAGENT_CHILD='1',
                             PI_SUBAGENT_PARENT_SESSION='pi-parent')
        self.assertEqual(json.loads(Path(result['binding']).read_text())['sessions'], ['pi-child', 'pi-parent'])

    def test_resolve_for_walkthrough_needs_no_host_or_route_and_creates_no_hold(self):
        config = self.repo / '.zloop/executors.json'
        config.parent.mkdir()
        config.write_text('{broken')
        before = self.git('status', '--porcelain')
        resolved = self.launch('--resolve', host=None, identified=False)
        self.assertEqual(resolved['head'], self.head)
        self.assertEqual(resolved['base'], self.base)
        self.assertEqual(resolved['diff_base'], self.base)
        self.assertEqual(set(resolved['files']), {'code.txt', ' leading space.txt'})
        self.assertNotIn('report', resolved)
        self.assertNotIn('binding', resolved)
        self.assertFalse((self.repo / '.scratch').exists())
        self.assertEqual(self.git('status', '--porcelain'), before)
        self.assertEqual(config.read_text(), '{broken')
        env = {key: value for key, value in os.environ.items() if not key.startswith(('ZLOOP_', 'HERDR_'))}
        checked = subprocess.run([sys.executable, str(support.ROOT / 'hooks/yield_gate.py'), 'check',
                                  str(self.repo), 'review-session'], capture_output=True, text=True, env=env)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)['missing'], [])

    def test_workstream_slugs_cannot_escape_the_report_directory_or_be_empty(self):
        for slug in ('', '../escape', '/absolute', 'a/b', '..'):
            with self.subTest(slug=slug):
                refused = self.launch('--workstream', slug, code=2)
                self.assertIn('--workstream must be a filesystem-safe slug', refused)
        self.assertFalse((self.repo / '.scratch').exists())

    def test_each_host_starts_a_review_that_the_check_entry_holds_from_a_linked_checkout(self):
        linked = self.repo / 'linked'
        self.git('worktree', 'add', '--detach', str(linked), 'feature')
        self.directory = str(linked)
        env = {key: value for key, value in os.environ.items() if not key.startswith(('ZLOOP_', 'HERDR_'))}
        for host in ('claude', 'codex', 'pi', 'opencode'):
            with self.subTest(host=host):
                result = self.launch(host=host)
                self.assertEqual(Path(result['report']).parent.parent, self.repo / '.scratch/reviews')
                self.assertEqual(json.loads(Path(result['binding']).read_text())['host'], host)  # Review (Codex).
                self.assertEqual(result['route']['result']['host'], host)
                checked = subprocess.run([sys.executable, str(support.ROOT / 'hooks/yield_gate.py'), 'check',
                                          str(linked), 'review-session'], capture_output=True, text=True, env=env)
                self.assertEqual(checked.returncode, 0, checked.stderr)
                gaps = json.loads(checked.stdout)['missing']
                self.assertEqual(len(gaps), 1)
                self.assertIn('review_by: none names no collected worker attempt', gaps[0])
                self.assertIn(result['report'], gaps[0])


if __name__ == '__main__':
    unittest.main()
