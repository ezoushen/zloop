#!/usr/bin/env python3
"""Candidate selection and session isolation, without personal configuration or model calls."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import support


models = support.load_script('agent_models.py', 'models')


def worker(model, default='medium', allowed=('medium', 'high'), executor=None):
    return {'executor': executor or {'type': 'subagent'},
            'models': {'mode': 'override', 'model': model,
                       'thinking': {'default': default, 'allowed': list(allowed)}}}


class Pools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='zloop pools ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'ZLOOP_PROFILE': '', 'HERDR_ENV': '',
                                          'ZLOOP_SESSION': '',
                                          'XDG_STATE_HOME': str(self.root / 'state'),
                                     'CODEX_HOME': str(self.root / 'codex-home')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.user = self.root / 'user.json'
        self.project = self.root / '.zloop/executors.json'
        self.project.parent.mkdir()
        self.pool = {'candidates': {
            'luna': worker('luna', 'xhigh', ('xhigh', 'max')),
            'sol': worker('sol'),
            'local': {'description': 'Local model through an installed Pi delegate.',
                      'executor': {'type': 'subagent', 'config': {'agent': 'local-pi'}},
                      'models': {'mode': 'inherit'}}}}
        self.project.write_text(json.dumps({'agents': {'builder': self.pool}}))

    def resolve(self, task=None, **kwargs):
        return models.resolve_execution(self.root, self.root / 'absent-models.json', self.user,
                                        'codex', 'builder', task or {}, **kwargs)

    def test_joint_choice_and_native_inheritance(self):
        result = self.resolve(candidate='luna')
        self.assertEqual(result['candidate'], 'luna')
        self.assertEqual(result['settings'], {'model': 'luna', 'effort': 'xhigh'})
        result = self.resolve(selection={'candidate': 'sol', 'thinking': 'high',
                                         'reason': 'Comparable task evidence.'})
        self.assertEqual(result['settings'], {'model': 'sol', 'effort': 'high'})
        self.assertEqual(result['selection_source'], 'selector')
        result = self.resolve(candidate='local')
        self.assertEqual(result['executor_config'], {'agent': 'local-pi'})
        self.assertEqual(result['settings'], {})
        self.assertEqual(result['host'], 'codex')
        self.assertFalse(result['native_dispatch'])
        self.assertNotIn(support.RETIRED_HOST_WORD, result)

    def test_invalid_recommendations_respect_eligibility(self):
        for selection in ({'candidate': 'outside', 'reason': 'Guess'},
                          {'candidate': 'sol', 'thinking': 'max', 'reason': 'Guess'},
                          {'candidate': 'local', 'thinking': 'high', 'reason': 'Guess'},
                          {'candidate': 'sol', 'reason': ''}, ['sol'], '{malformed'):
            with self.subTest(selection=selection):
                result = self.resolve(selection=selection)
                self.assertEqual(result['status'], 'selection_required')
                self.assertTrue(result['selection_error'])
                fallback = self.resolve(selection=selection, eligible_candidates=['sol'])
                self.assertEqual(fallback['status'], 'selection_required')
                self.assertNotIn('settings', fallback)
        with self.assertRaises(ValueError):
            self.resolve(selection={'candidate': None, 'reason': 'No suitable runtime.'})
        with self.assertRaises(ValueError):
            self.resolve(eligible_candidates=[])
        with self.assertRaises(ValueError):
            self.resolve(eligible_candidates=['invented'])
        with self.assertRaises(ValueError):
            self.resolve(candidate='local', eligible_candidates=['luna'])

    def test_remaining_candidates_need_selection_and_respect_fallback_policy(self):
        first = self.resolve(eligible_candidates=['sol', 'local'])
        self.assertEqual(first['status'], 'selection_required')
        self.assertEqual(self.resolve(eligible_candidates=['local'])['candidate'], 'local')
        with self.assertRaises(ValueError):
            self.resolve(eligible_candidates=[])
        self.pool['fallback'] = False
        self.project.write_text(json.dumps({'agents': {'builder': self.pool}}))
        with self.assertRaises(ValueError):
            self.resolve(selection={'candidate': 'luna', 'reason': 'Chosen before outage.'}, eligible_candidates=['sol'])

    def test_explicit_pins_and_bounds(self):
        result = self.resolve(candidate='sol', task={'effort': 'medium'},
                              selection={'candidate': 'outside', 'thinking': 'max', 'reason': 'Guess'})
        self.assertEqual(result['settings'], {'model': 'sol', 'effort': 'medium'})
        for options in ({'task': {'model': 'outside'}}, {'task': {'effort': 'low'}},
                        {'task': {'effort': None}}, {'adaptive_thinking': 'low'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.resolve(candidate='luna', **options)
        self.assertEqual(self.resolve(candidate='luna', adaptive_thinking='max')['settings']['effort'], 'max')
        self.assertEqual(self.resolve(choice=worker('explicit'))['settings']['model'], 'explicit')
        with self.assertRaises(ValueError):
            self.resolve(choice=worker('explicit'), task={'model': 'different'})

    def test_fixed_labels_and_model_only_candidates(self):
        self.pool['candidates']['fixed'] = {'executor': {'type': 'subagent'},
                                           'models': {'mode': 'override', 'thinking': 'high'}}
        self.pool['candidates']['model-only'] = {'executor': {'type': 'skill', 'config': {
            'skill': 'local-runner', 'instruction': 'Use the registered local worker.'}},
            'models': {'mode': 'override', 'model': 'local/model'}}
        self.project.write_text(json.dumps({'agents': {'builder': self.pool}}))
        result = self.resolve(selection={'candidate': 'fixed', 'thinking': 'high', 'reason': 'Pinned setting fits.'})
        self.assertEqual(result['settings'], {'effort': 'high'})
        self.assertEqual(self.resolve(candidate='model-only')['settings'], {'model': 'local/model'})
        result = self.resolve(selection={'candidate': 'model-only', 'thinking': 'max', 'reason': 'Guess'})
        self.assertIn('selection_error', result)

    def test_profile_pool_and_session_null_mask(self):
        self.project.write_text(json.dumps({
            'agents': {'builder': self.pool},
            'profiles': {'balanced': {'builder': {'codex': {'candidate': 'luna'}}}}}))
        self.assertEqual(self.resolve()['status'], 'selection_required')
        models.save_session(self.root, 'run', {'agents': {'builder': None}})
        result = self.resolve(session_id='run', config_profile='balanced')
        self.assertEqual(result['model_mode'], 'existing')
        models.reset_session(self.root, 'run')
        result = self.resolve(session_id='run', config_profile='balanced', selection={
            'candidate': 'sol', 'reason': 'The assignment needs a native Codex worker.'})
        self.assertEqual(result['candidate'], 'luna')

    def test_frozen_bash_assignment_keeps_settings_after_session_edit(self):
        script = self.root / 'worker.py'
        script.write_text('''import json, sys
request = json.load(sys.stdin)
print(json.dumps({'assignment_id': request['assignment']['assignment_id'],
                  'status': 'completed', 'report': json.dumps(request['models'])}))
''')
        executor = {'type': 'bash', 'config': {'argv': [sys.executable, str(script)]}}
        models.save_session(self.root, 'run', {'agents': {'builder': worker('before', executor=executor)}})
        frozen = self.root / 'resolved.json'
        frozen.write_text(json.dumps(self.resolve(session_id='run')))
        models.save_session(self.root, 'run', {'agents': {'builder': worker('after', executor=executor)}})
        assignment = self.root / 'assignment.json'
        assignment.write_text(json.dumps({'assignment_id': 'frozen-1', 'worktree': str(self.root),
                                          'agent_file': str(models.ROOT / 'agents/builder.md'),
                                          'job_file': str(models.ROOT / 'skills/dispatch/jobs/ticket.md'),
                                          'head': 'fixture-head', 'prompt': 'Return received settings.'}))
        result = subprocess.run([sys.executable, '-B', str(models.ROOT / 'scripts/bash_executor.py'),
                                 '--execution', str(frozen), '--assignment', str(assignment)],
                                text=True, capture_output=True, check=True)
        report = json.loads(json.loads(result.stdout)['report'])
        self.assertEqual(report, {'mode': 'override', 'model': 'before', 'thinking': 'medium'})
        self.assertEqual(self.resolve(session_id='run')['settings']['model'], 'after')

    def test_validation_and_whole_entry_precedence(self):
        bad = [dict(self.pool, default='missing'), dict(self.pool, candidates={}),
               dict(self.pool, executor={'type': 'subagent'})]
        for thinking in ({'default': 'low', 'allowed': ['high']},
                         {'default': 'high', 'allowed': []},
                         {'default': 'high', 'allowed': ['high', 'high']}):
            value = worker('model')
            value['models']['thinking'] = thinking
            bad.append(value)
        value = worker('model')
        value['models']['allow_thinking_override'] = True
        bad.append(value)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                models.validate_execution_config({'agents': {'builder': value}}, 'test')
        self.user.write_text(json.dumps({'agents': {'builder': self.pool}}))
        self.project.write_text(json.dumps({'agents': {'builder': worker('project')}}))
        self.assertEqual(self.resolve()['settings']['model'], 'project')
        with self.assertRaises(ValueError):
            self.resolve(candidate='sol')

    def test_session_resume_isolation_and_child_host(self):
        project_before = self.project.read_bytes()
        config = {'agents': {'builder': worker('session-model')}}
        missing = self.resolve(session_id='first')
        self.assertFalse(Path(missing['session']['path']).exists())
        models.save_session(self.root, 'first', config)
        frozen = self.resolve(session_id='first')
        self.assertEqual(frozen['settings']['model'], 'session-model')
        child = models.resolve_execution(self.root, self.root / 'absent.json', self.user,
                                         'claude', 'builder', {}, session_id='first')
        self.assertEqual(child['settings']['model'], 'session-model')
        self.assertEqual(self.resolve(session_id='second')['status'], 'selection_required')
        other = self.root / 'another-repo'
        other.mkdir()
        self.assertNotEqual(models.session_path(other, 'first'), models.session_path(self.root, 'first'))
        with patch.dict(os.environ, {'ZLOOP_SESSION': 'first'}):
            self.assertEqual(self.resolve()['settings']['model'], 'session-model')
            self.assertEqual(self.resolve(session_id='second')['status'], 'selection_required')
        self.assertEqual(self.resolve(session_id='first', choice=worker('pin'))['settings']['model'], 'pin')
        models.save_session(self.root, 'first', {'agents': {'builder': worker('updated')}})
        self.assertEqual(self.resolve(session_id='first')['settings']['model'], 'updated')
        self.assertEqual(frozen['settings']['model'], 'session-model')
        models.reset_session(self.root, 'first')
        self.assertEqual(self.resolve(session_id='first')['status'], 'selection_required')
        self.assertEqual(self.project.read_bytes(), project_before)
        self.assertFalse(self.user.exists())

    def test_session_validation_preserves_existing_state(self):
        path = models.save_session(self.root, 'run', {'agents': {'builder': self.pool}})
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            models.save_session(self.root, 'run', {'agents': {'builder': {'default': 'unknown'}}})
        self.assertEqual(path.read_bytes(), before)
        record = json.loads(before)
        record['repo'] = str(self.root / 'wrong')
        path.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            self.resolve(session_id='run')
        with self.assertRaises(ValueError):
            models.reset_session(self.root, 'run')
        self.assertTrue(path.exists())
        with patch.dict(os.environ, {'XDG_STATE_HOME': 'relative'}), self.assertRaises(ValueError):
            models.session_path(self.root, 'run')

    def test_cli_inspection_and_session_write(self):
        args = [sys.executable, '-B', str(models.ROOT / 'scripts/agent_models.py'),
                '--repo', str(self.root), '--host', 'codex', '--job', 'ticket',
                '--user-executors', str(self.user), '--route']
        def cli(*extra, ok=True):
            result = subprocess.run(args + list(extra), capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, ok, result.stderr)
            return json.loads(result.stdout) if ok else result
        listing = cli('--list-candidates')
        self.assertEqual(set(listing['candidates']), {'luna', 'sol', 'local'})
        self.assertNotIn('default', listing)
        cli('--candidate', 'outside', ok=False)
        cli('--eligible-candidates', '', ok=False)
        config = self.root / 'session.json'
        config.write_text(json.dumps({'agents': {'builder': worker('session-model')}}))
        cli('--session', 'run', '--save-session', str(config))
        self.assertEqual(cli('--session', 'run')['settings']['model'], 'session-model')
        cli('--session', 'run', '--reset-session')
        self.assertEqual(cli('--session', 'run')['status'], 'selection_required')
        cli('--save-session', str(config), ok=False)
        cli('--list-candidates', '--profile', ok=False)
        selected = cli('--selection', json.dumps({'candidate': 'sol', 'thinking': 'high', 'reason': 'Task fit.'}))
        self.assertEqual(selected['settings'], {'model': 'sol', 'effort': 'high'})
        self.assertEqual(cli('--candidate', 'sol', '--selection', 'malformed')['candidate'], 'sol')
        self.assertTrue(cli('--selection', '{malformed')['selection_error'])


if __name__ == '__main__':
    unittest.main()
