#!/usr/bin/env python3
"""Reusable model definitions, context-specific executors and allowed fallback paths."""

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


class Catalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='zloop catalog ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.user = self.root / 'user.json'
        self.project = self.root / '.zloop/executors.json'
        self.project.parent.mkdir()
        env = patch.dict(os.environ, {'HERDR_ENV': '', 'ZLOOP_PROFILE': '',
                                     'ZLOOP_SESSION': '', 'XDG_STATE_HOME': str(self.root / 'state'),
                                     'CODEX_HOME': str(self.root / 'codex-home')})
        env.start()
        self.addCleanup(env.stop)
        self.data = {'runtimes': {
            'codex': {'executors': {'codex': {'type': 'subagent'}, 'herdr': {
                'type': 'skill', 'config': {'skill': 'herdr', 'instruction': 'Run a Codex worker.'}}}},
            'pi': {'executors': {'pi': {'type': 'subagent'}, 'herdr': {
                'type': 'skill', 'config': {'skill': 'herdr', 'instruction': 'Run a Pi worker.'}}}}},
            'models': {
                'sol': {'description': 'Implementation and repair.', 'runtimes': {'codex': {
                    'id': 'gpt-6.1-sol', 'thinking': {'default': 'medium', 'allowed': ['medium', 'high']}}}},
                'local': {'description': 'Heavy local tasks.', 'runtimes': {'pi': {'id': 'local/glm'}}}},
            'agents': {'builder': {'candidates': ['sol', 'local']},
                       'reviewer': {'model': 'sol'}}}
        self.save()

    def save(self):
        self.user.write_text(json.dumps(self.data))

    def resolve(self, host='codex', role='builder', **kwargs):
        return models.resolve_execution(self.root, self.root / 'absent.json', self.user,
                                        host, role, {}, **kwargs)

    def test_native_same_host_and_herdr_cross_host(self):
        result = self.resolve()
        self.assertTrue(result['native_dispatch'])
        self.assertEqual(result['settings'], {'model': 'gpt-6.1-sol', 'effort': 'medium'})
        self.assertEqual(result['executor_context'], 'codex')
        self.assertIn('local', result['unavailable_candidates'])
        with patch.dict(os.environ, {'HERDR_ENV': '1'}):
            self.assertTrue(self.resolve(candidate='sol')['native_dispatch'])
            result = self.resolve(host='claude', candidate='sol')
            self.assertEqual(result['executor_type'], 'skill')
            self.assertEqual(result['executor_context'], 'herdr')
            self.assertEqual(result['executor_config']['skill'], 'herdr')
            self.assertEqual(self.resolve(eligible_candidates=['local'])['settings']['model'], 'local/glm')
        with self.assertRaises(ValueError):
            self.resolve(host='claude')

    def test_missing_route_excluded_without_losing_inspection(self):
        self.assertEqual(self.resolve()['candidate'], 'sol')
        listing = self.resolve(host='claude', list_candidates=True)
        self.assertEqual(listing['candidates'], {})
        self.assertEqual(set(listing['unavailable_candidates']), {'sol', 'local'})
        with self.assertRaises(ValueError):
            self.resolve(candidate='local')

    def test_profile_name_does_not_supply_a_runtime(self):
        self.data['profiles'] = {'herdr': {}}
        self.save()
        result = self.resolve(host='claude', config_profile='herdr', list_candidates=True)
        self.assertEqual(result['candidates'], {})
        with self.assertRaises(ValueError):
            self.resolve(host='claude', config_profile='herdr')
        with self.assertRaises(ValueError):
            self.resolve(candidate='local')

    def test_definition_replacement_and_source_across_layers(self):
        replacement = {'description': 'Project serving configuration.',
                       'runtimes': {'codex': {'id': 'project-sol'}}}
        self.project.write_text(json.dumps({'models': {'sol': replacement}}))
        result = self.resolve(role='reviewer')
        self.assertEqual(result['settings'], {'model': 'project-sol'})
        self.assertEqual(result['model_definition_source'], f'{self.project}:models.sol')
        self.assertEqual(result['sources']['model'], f'{self.project}:models.sol.runtimes.codex.id')
        models.save_session(self.root, 'one', {'models': {'sol': dict(replacement, runtimes={'codex': {'id': 'session-sol'}})}})
        self.assertEqual(self.resolve(session_id='one')['settings'], {'model': 'session-sol'})
        self.assertEqual(self.resolve()['settings'], {'model': 'project-sol'})
        self.project.write_text(json.dumps({'models': {'sol': replacement},
            'runtimes': {'codex': {'executors': {'codex': {'type': 'subagent'}}}}}))
        # The project's codex routes replace the user's whole, so its herdr route goes; Herdr is still built in.
        with patch.dict(os.environ, {'HERDR_ENV': '1'}):
            result = self.resolve(host='claude', candidate='sol')
            self.assertEqual((result['executor_source'], result['settings']['model']), ('built-in', 'project-sol'))

    def test_thinking_recommendation_after_catalog_expansion(self):
        result = self.resolve(selection={'candidate': 'sol', 'thinking': 'high', 'reason': 'Task fits.'})
        self.assertEqual(result['settings']['effort'], 'high')
        self.assertEqual(result['candidate_pool']['candidates']['sol']['description'], 'Implementation and repair.')
        result = self.resolve(selection={'candidate': 'sol', 'thinking': 'max', 'reason': 'Guess.'})
        self.assertEqual(result['status'], 'selection_required')
        self.assertIn('selection_error', result)

    def test_bad_catalog_references_are_configuration_errors(self):
        self.project.write_text(json.dumps({'agents': {'builder': {
            'candidates': ['typo', 'sol']}}}))
        with self.assertRaisesRegex(ValueError, 'Unknown model'):
            self.resolve()
        for entry in ({'default': 'sol', 'candidates': ['sol', 'sol']},
                      {'default': 'other', 'candidates': ['sol']}):
            with self.assertRaises(ValueError):
                models.validate_execution_config({'agents': {'builder': entry}}, 'test')
        for definition in ({'description': 'Task fit.', 'runtimes': {}},
                           {'description': 'Task fit.', 'runtimes': {'unknown': {'id': 'id'}}},
                           {'description': 'Task fit.', 'runtimes': {'pi': {
                               'id': 'id', 'thinking': {'default': 'low', 'allowed': ['high']}}}}):
            with self.assertRaises(ValueError):
                models.validate_execution_config({'models': {'sol': definition}}, 'test')
        with self.assertRaisesRegex(ValueError, 'runtime bindings'):
            models.validate_execution_config({'models': {'old': {'model': 'old',
                'description': 'Old layout.', 'executors': {'pi': {'type': 'subagent'}}}}}, 'test')

    def test_herdr_is_the_built_in_provider_for_every_runtime(self):
        # The user (2026-10-26): "Own the contract and have herdr be our built in provider for this." In Herdr, every
        # runtime is reachable through it with no route or executor type configured; outside Herdr, it is not.
        del self.data['runtimes']['codex']['executors']['herdr']
        self.save()
        with self.assertRaises(ValueError):
            self.resolve(host='claude', candidate='sol')
        with patch.dict(os.environ, {'HERDR_ENV': '1'}):
            result = self.resolve(host='claude', candidate='sol')
        self.assertEqual((result['executor_type'], result['executor_context'], result['native_dispatch']),
                         ('herdr', 'herdr', False))
        self.assertEqual(result['executor_source'], 'built-in')
        self.assertEqual(result['executor_guide'], str(models.ROOT / 'hosts/herdr.md'))
        self.assertIn('launch', result)
        # A route of type herdr is the built-in provider, whatever an executor type of that name says.
        self.data['runtimes']['codex']['executors']['herdr'] = {'type': 'herdr'}
        self.data['executor_types'] = {'herdr': {'type': 'skill', 'config': {
            'skill': 'herdr', 'instruction': 'Launch through the herdr skill.'}}}
        self.save()
        with patch.dict(os.environ, {'HERDR_ENV': '1'}):
            result = self.resolve(host='claude', candidate='sol')
        self.assertEqual((result['executor_type'], result['executor_config']), ('herdr', {}))

    def test_a_pi_host_in_herdr_runs_its_workers_through_herdr(self):
        # Pi's own delegation is a third-party extension; in Herdr the built-in provider serves Pi workers, and the
        # extension route serves only outside Herdr.
        del self.data['runtimes']['pi']['executors']['herdr']
        self.save()
        result = self.resolve(host='pi', candidate='local')
        self.assertEqual((result['executor_context'], result['native_dispatch']), ('pi', True))
        with patch.dict(os.environ, {'HERDR_ENV': '1'}):
            result = self.resolve(host='pi', candidate='local')
        self.assertEqual((result['executor_type'], result['executor_context'], result['native_dispatch']),
                         ('herdr', 'herdr', False))
        self.assertEqual(result['settings'], {'model': 'local/glm'})

    def test_skill_routes_use_each_callers_interface_and_destination_settings(self):
        routes = self.data['runtimes']['pi']['executors']
        for host in ('claude', 'codex'):
            routes[host] = {'type': 'skill', 'config': {
                'skill': f'{host}-pi-bridge', 'instruction': f'Delegate to Pi from {host}.'}}
        self.data['models']['local']['runtimes']['pi']['thinking'] = {
            'default': 'high', 'allowed': ['high']}
        self.save()
        for managed in ('', '1'):
            for host in ('claude', 'codex'):
                with self.subTest(host=host, herdr=managed), patch.dict(os.environ, {'HERDR_ENV': managed}):
                    result = self.resolve(host=host, candidate='local')
                    self.assertEqual(result['executor_type'], 'skill')
                    self.assertFalse(result['native_dispatch'])
                    self.assertEqual(result['executor_config'], routes[host]['config'])
                    self.assertEqual(result['executor_context'], host)
                    self.assertEqual(result['target_runtime'], 'pi')
                    self.assertEqual(result['settings'], {'model': 'local/glm', 'effort': 'high'})
                    self.assertEqual(result['executor_source'], f'{self.user}:runtimes.pi.executors.{host}')
                    self.assertEqual(result['candidate_routes']['local']['executor_context'], host)

    def test_missing_caller_route_never_borrows_another_hosts_skill(self):
        for host, other in (('claude', 'codex'), ('codex', 'claude')):
            with self.subTest(host=host):
                self.data['runtimes']['pi']['executors'] = {
                    other: {'type': 'skill', 'config': {
                        'skill': f'{other}-pi-bridge', 'instruction': 'Delegate to Pi.'}},
                    'herdr': {'type': 'skill', 'config': {
                        'skill': 'herdr', 'instruction': 'Launch Pi through Herdr.'}}}
                self.data['profiles'] = {other: {}}
                self.save()
                listing = self.resolve(host=host, config_profile=other, list_candidates=True)
                self.assertNotIn('local', listing['candidates'])
                self.assertIn('No configured route', listing['unavailable_candidates']['local'])
                with self.assertRaisesRegex(ValueError, "candidate 'local' is not eligible"):
                    self.resolve(host=host, config_profile=other, candidate='local')
                with patch.dict(os.environ, {'HERDR_ENV': '1'}):
                    result = self.resolve(host=host, config_profile=other, candidate='local')
                    self.assertEqual(result['executor_context'], 'herdr')
                    self.assertEqual(result['executor_config']['skill'], 'herdr')

    def test_binding_ids_and_thinking_are_runtime_specific(self):
        self.data['models']['local']['runtimes'] = {
            'codex': {'id': 'codex-local', 'thinking': {'default': 'medium', 'allowed': ['medium', 'high']}},
            'pi': {'id': 'pi-local', 'thinking': {'default': 'xhigh', 'allowed': ['xhigh']}}}
        self.save()
        result = self.resolve(host='pi', candidate='local')
        self.assertEqual(result['settings'], {'model': 'pi-local', 'effort': 'xhigh'})
        self.assertEqual(result['thinking_allowed'], ['xhigh'])
        result = self.resolve(candidate='local')
        self.assertEqual(result['settings'], {'model': 'codex-local', 'effort': 'medium'})
        with self.assertRaises(ValueError):
            self.resolve(host='pi', candidate='local', adaptive_thinking='high')
        # A model's bindings are tried in declaration order through whatever route reaches them, Herdr included.
        self.data['runtimes']['codex']['executors'] = {'codex': {'type': 'subagent'}}
        self.save()
        with patch.dict(os.environ, {'HERDR_ENV': '1'}):
            result = self.resolve(host='claude', candidate='local')
            self.assertEqual((result['target_runtime'], result['executor_source']), ('codex', 'built-in'))

    def test_shared_routes_replace_whole_and_do_not_invent_bridges(self):
        self.data['runtimes']['codex']['executors']['claude'] = {
            'type': 'subagent', 'config': {'agent': 'codex-delegate'}}
        self.save()
        result = self.resolve(host='claude')
        self.assertFalse(result['native_dispatch'])
        self.assertEqual(result['target_runtime'], 'codex')
        self.assertEqual(result['executor_config'], {'agent': 'codex-delegate'})
        self.assertEqual(result['executor_source'], f'{self.user}:runtimes.codex.executors.claude')
        self.project.write_text(json.dumps({'runtimes': {'codex': {'executors': {
            'codex': {'type': 'subagent'}}}}}))
        with self.assertRaises(ValueError):
            self.resolve(host='claude')
        self.project.unlink()
        models.save_session(self.root, 'route-one', {'runtimes': {'codex': {'executors': {
            'claude': {'type': 'subagent', 'config': {'agent': 'session-delegate'}}}}}})
        result = self.resolve(host='claude', session_id='route-one')
        self.assertEqual(result['executor_config'], {'agent': 'session-delegate'})
        self.assertIn('runtimes.codex.executors.claude', result['executor_source'])
        self.assertTrue(self.resolve(session_id='another')['native_dispatch'])
        with self.assertRaises(ValueError):
            self.resolve(session_id='route-one')
        del self.data['runtimes']['pi']
        self.save()
        with self.assertRaisesRegex(ValueError, 'Unknown runtime'):
            self.resolve()
        with self.assertRaises(ValueError):
            models.validate_execution_config({'runtimes': {'pi': {'executors': {
                'codex': {'type': 'subagent'}}}}}, 'test')

    def test_catalog_cli_is_read_only_and_explains_exclusions(self):
        before = self.user.read_bytes()
        args = [sys.executable, '-B', str(models.ROOT / 'scripts/agent_models.py'),
                '--repo', str(self.root), '--harness', 'codex', '--job', 'ticket',
                '--user-executors', str(self.user), '--route']
        result = subprocess.run(args, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['executor_context'], 'codex')
        result = subprocess.run(args + ['--list-candidates'], capture_output=True, text=True, check=True)
        listing = json.loads(result.stdout)
        self.assertEqual(listing['candidates']['sol']['description'], 'Implementation and repair.')
        self.assertIn('local', listing['unavailable_candidates'])
        self.assertEqual(before, self.user.read_bytes())
        self.assertFalse((self.root / 'state').exists())

    def test_user_skill_type_expands_without_changing_caller_or_model_policy(self):
        definition = {'type': 'skill', 'config': {
            'skill': 'local-worker', 'instruction': 'Use its single-assignment entrypoint.'}}
        self.data['executor_types'] = {'local-direct': definition}
        self.data['runtimes']['pi']['executors']['claude'] = {'type': 'local-direct'}
        self.save()
        before = self.user.read_bytes()
        for managed in ('', '1'):
            with patch.dict(os.environ, {'HERDR_ENV': managed}):
                result = self.resolve(host='claude', candidate='local')
                self.assertEqual(result['executor_type'], 'skill')
                self.assertEqual(result['configured_executor_type'], 'local-direct')
                self.assertEqual(result['executor_config'], definition['config'])
                self.assertEqual(result['executor_context'], 'claude')
                self.assertEqual(result['target_runtime'], 'pi')
                self.assertEqual(result['settings'], {'model': 'local/glm'})
                self.assertEqual(result['executor_definition_source'], f'{self.user}:executor_types.local-direct')
                self.assertTrue(result['executor_guide'].endswith('/skill.md'))
                self.assertFalse(result['native_dispatch'])
                listing = self.resolve(host='claude', list_candidates=True)
                self.assertEqual(listing['candidates']['local']['executor'], definition)
                self.assertEqual(listing['candidate_routes']['local']['configured_executor_type'], 'local-direct')
        self.assertIn('local', self.resolve(list_candidates=True)['unavailable_candidates'])
        self.assertTrue(self.resolve(host='pi', candidate='local')['native_dispatch'])
        self.assertEqual(before, self.user.read_bytes())

    def test_skill_type_layering_applies_to_instances_pools_and_task_choices(self):
        def definition(skill):
            return {'type': 'skill', 'config': {'skill': skill, 'instruction': 'Delegate one task.'}}

        instance = {'executor': {'type': 'bridge'}, 'models': {
            'mode': 'override', 'model': 'chosen-model',
            'thinking': {'default': 'medium', 'allowed': ['medium', 'high']}}}
        self.data['executor_types'] = {'bridge': definition('user-bridge')}
        self.data['agents']['reviewer'] = instance
        self.save()
        self.assertEqual(self.resolve(role='reviewer')['executor_config']['skill'], 'user-bridge')
        self.project.write_text(json.dumps({'executor_types': {'bridge': definition('project-bridge')},
            'agents': {'builder': {'candidates': {'named': instance}}}}))
        result = self.resolve(adaptive_thinking='high')
        self.assertEqual(result['executor_config']['skill'], 'project-bridge')
        self.assertEqual(result['settings'], {'model': 'chosen-model', 'effort': 'high'})
        self.assertEqual(result['executor_definition_source'], f'{self.project}:executor_types.bridge')
        listing = self.resolve(list_candidates=True)
        self.assertEqual(listing['candidates']['named']['executor'], definition('project-bridge'))
        models.save_session(self.root, 'types', {'executor_types': {'bridge': definition('session-bridge')}})
        result = self.resolve(session_id='types', choice=instance)
        self.assertEqual(result['executor_config']['skill'], 'session-bridge')
        self.assertIn(':executor_types.bridge', result['executor_definition_source'])
        self.assertEqual(self.resolve(choice=instance)['executor_config']['skill'], 'project-bridge')
        self.assertEqual(self.resolve(choice={'executor': {'type': 'bridge'}})['settings'], {})

    def test_unknown_skill_type_is_an_error_not_an_availability_fallback(self):
        self.data['runtimes']['pi']['executors']['claude'] = {'type': 'missing'}
        self.save()
        with self.assertRaisesRegex(ValueError, 'Unknown executor type'):
            self.resolve(host='claude', list_candidates=True)
        with self.assertRaisesRegex(ValueError, 'Unknown executor type'):
            self.resolve(choice={'executor': {'type': 'missing'}})

    def test_skill_type_definitions_cannot_override_builtins_or_nest_types(self):
        definition = {'type': 'skill', 'config': {'skill': 'worker', 'instruction': 'Delegate.'}}
        for name in ('subagent', 'skill', 'bash', 'bad name'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                models.validate_execution_config({'executor_types': {name: definition}}, 'test')
        for value in ({'type': 'loop'}, {'type': 'subagent'},
                      {'type': 'bash', 'config': {'argv': ['worker']}},
                      {'type': 'skill', 'config': {'skill': 'worker'}},
                      dict(definition, schema={})):
            with self.subTest(value=value), self.assertRaises(ValueError):
                models.validate_execution_config({'executor_types': {'loop': value}}, 'test')
        self.data['executor_types'] = {'bridge': definition}
        self.save()
        with self.assertRaises(ValueError):
            self.resolve(choice={'executor': {'type': 'bridge', 'config': {'instruction': 'Replace.'}}})


if __name__ == '__main__':
    unittest.main()
