#!/usr/bin/env python3
"""Routing profiles, user pins, runtime bindings and configuration validation."""

import json
import os
import unittest
from unittest.mock import patch

from router_case import RouterCase, models


class Profiles(RouterCase):
    def test_missing_selection_returns_options_without_launch_settings(self):
        result = self.resolve()
        self.assertEqual(result['status'], 'selection_required')
        self.assertEqual(result['router'], {'type': 'host'})
        self.assertEqual(set(result['candidates']), {'luna', 'sol', 'opus'})
        self.assertNotIn('settings', result)
        self.assertNotIn('executor', result)
        self.assertNotIn('default', result['candidate_pool'])

    def test_profile_uses_worker_runtime_not_caller(self):
        self.profile({'codex': {'candidate': 'sol', 'thinking': 'high'},
                      'claude': {'candidate': 'opus'}})
        result = self.resolve(target_runtime='codex')
        self.assertEqual(result['candidate'], 'sol')
        self.assertEqual(result['target_runtime'], 'codex')
        self.assertEqual(result['executor_context'], 'herdr')
        self.assertEqual(result['settings']['effort'], 'high')
        self.assertEqual(result['selection_source'], 'profile')
        result = self.resolve(host='codex', target_runtime='claude')
        self.assertEqual(result['candidate'], 'opus')

    def test_runtime_can_come_from_selection_without_extra_router_call(self):
        self.profile({'codex': {'candidate': 'sol'}})
        result = self.resolve(selection=self.choose('luna'))
        self.assertEqual(result['candidate'], 'sol')
        self.assertEqual(result['target_runtime'], 'codex')
        self.assertEqual(result['settings']['effort'], 'medium')

    def test_sparse_profiles_preserve_unspecified_selection_fields(self):
        self.profile({'codex': {'thinking': 'high'}})
        result = self.resolve(selection=self.choose('sol'))
        self.assertEqual(result['candidate'], 'sol')
        self.assertEqual(result['settings']['effort'], 'high')
        self.assertEqual(self.resolve(target_runtime='codex')['status'], 'selection_required')
        self.profile({'codex': {'candidate': 'sol'}})
        self.assertEqual(self.resolve(selection=self.choose('sol', thinking='high'))['settings']['effort'], 'high')
        self.profile({'codex': {}})
        self.assertEqual(self.resolve(selection=self.choose('luna', thinking='max'))['settings']['effort'], 'max')
        result = self.resolve(selection=self.choose('opus', thinking='high'))
        self.assertEqual(result['settings'], {'model': 'claude/opus', 'effort': 'high'})

    def test_profile_does_not_guess_runtime_or_create_routes(self):
        self.profile({'codex': {'candidate': 'sol'}, 'claude': {'candidate': 'opus'}})
        self.assertEqual(self.resolve()['status'], 'selection_required')
        with patch.dict(os.environ, {'HERDR_ENV': ''}):
            with self.assertRaisesRegex(ValueError, 'eligible'):
                self.resolve(target_runtime='codex')
            self.assertEqual(self.resolve()['candidate'], 'opus')

    def test_explicit_user_pins_win_over_profile(self):
        self.profile({'codex': {'candidate': 'sol', 'thinking': 'high'}})
        result = self.resolve(candidate='luna', task={'effort': 'max'}, selection='invalid')
        self.assertEqual(result['settings'], {'model': 'codex/luna', 'effort': 'max'})
        self.assertEqual(result['selection_source'], 'user')
        self.assertEqual(self.resolve(candidate='luna')['settings']['effort'], 'xhigh')

    def test_invalid_selection_never_launches_hidden_default(self):
        for decision in ('invalid', self.choose('outside'), self.choose('sol', thinking='max')):
            with self.subTest(decision=decision):
                result = self.resolve(selection=decision)
                self.assertEqual(result['status'], 'selection_required')
                self.assertIn('selection_error', result)
                self.assertNotIn('settings', result)
        with self.assertRaisesRegex(ValueError, 'No suitable'):
            self.resolve(selection={'candidate': None, 'reason': 'No candidate meets task needs.'})

    def test_profile_availability_recovery_does_not_repeat_unavailable_model(self):
        self.profile({'codex': {'candidate': 'sol'}})
        result = self.resolve(target_runtime='codex', eligible_candidates=['luna'])
        self.assertEqual(result['status'], 'selection_required')
        self.assertEqual(result['fallback_from'], 'sol')
        result = self.resolve(target_runtime='codex', eligible_candidates=['luna'], selection=self.choose('luna'))
        self.assertEqual(result['candidate'], 'luna')
        self.assertEqual(result['fallback_from'], 'sol')
        self.data['agents']['builder']['fallback'] = False
        self.save()
        with self.assertRaisesRegex(ValueError, 'fallback'):
            self.resolve(target_runtime='codex', eligible_candidates=['luna'], selection=self.choose('luna'))

    def test_pins_no_candidates_and_unknown_eligibility_block(self):
        with self.assertRaisesRegex(ValueError, 'not eligible'):
            self.resolve(candidate='sol', eligible_candidates=['luna'])
        with self.assertRaisesRegex(ValueError, 'eligible'):
            self.resolve(eligible_candidates=[])
        with self.assertRaises(ValueError):
            self.resolve(eligible_candidates=['outside'])
        with self.assertRaises(ValueError):
            self.resolve(candidate='sol', target_runtime='claude')

    def test_profile_reference_and_runtime_mismatch_are_errors(self):
        for value in ({'codex': {'candidate': 'opus'}}, {'codex': {'candidate': 'outside'}},
                      {'codex': {'candidate': 'sol', 'thinking': 'max'}}):
            with self.subTest(value=value):
                self.profile(value)
                with self.assertRaises(ValueError):
                    self.resolve(list_candidates=True)
        self.data['router']['profile'] = 'missing'
        self.save()
        with self.assertRaisesRegex(ValueError, 'Unknown.*profile'):
            self.resolve()

    def test_profile_thinking_must_fit_final_candidate(self):
        self.profile({'codex': {'thinking': 'high'}})
        result = self.resolve(selection=self.choose('luna'))
        self.assertEqual(result['status'], 'selection_required')
        self.assertIn('selection_error', result)
        self.assertNotIn('settings', result)

    def test_profile_selection_is_explicit_not_host_or_herdr_name(self):
        self.data['profiles'] = {'herdr': {'builder': {'codex': {'candidate': 'sol'}}}}
        self.save()
        self.assertEqual(self.resolve()['router']['type'], 'host')
        self.assertEqual(self.resolve(config_profile='herdr', target_runtime='codex')['candidate'], 'sol')
        with patch.dict(os.environ, {'ZLOOP_PROFILE': 'herdr'}):
            self.assertEqual(self.resolve(target_runtime='codex')['candidate'], 'sol')

    def test_router_and_profile_layering_and_session_resume(self):
        self.profile({'codex': {'candidate': 'sol'}})
        self.project.write_text(json.dumps({'profiles': {'balanced': {'builder': {
            'codex': {'candidate': 'luna'}}}}}))
        self.assertEqual(self.resolve(target_runtime='codex')['candidate'], 'luna')
        models.save_session(self.root, 'one', {'router': {'type': 'skill', 'skill': 'my:router'}})
        self.assertEqual(self.resolve(session_id='one')['router']['skill'], 'my:router')
        self.assertEqual(self.resolve(session_id='two', target_runtime='codex')['candidate'], 'luna')
        models.reset_session(self.root, 'one')
        self.assertEqual(self.resolve(session_id='one', target_runtime='codex')['candidate'], 'luna')

    def test_old_profiles_and_pool_defaults_have_migration_diagnostics(self):
        for config in ({'agents': {'builder': {'default': 'sol', 'candidates': ['sol']}}},
                       {'profiles': {'codex': {'agents': {}}}}):
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, 'migrat'):
                models.validate_execution_config(config, 'fixture')

    def test_schema_rejects_unknown_router_fields_and_profile_wrappers(self):
        for router in ({'type': 'fixed'}, {'type': 'skill'}, {'type': 'profile'},
                       {'type': 'skill', 'skill': 'route', 'profile': 'balanced'}, {'type': 'host', 'skill': 'route'}):
            with self.subTest(router=router), self.assertRaises(ValueError):
                models.validate_execution_config({'router': router}, 'fixture')
        with self.assertRaises(ValueError):
            models.validate_execution_config({'profiles': {'balanced': {'choices': {}}}}, 'fixture')

    def test_schema_accepts_the_host_router_with_an_optional_instruction(self):
        for router in ({'type': 'host'}, {'type': 'host', 'instruction': 'Prefer native work.'}):
            with self.subTest(router=router):
                models.validate_execution_config({'router': router}, 'fixture')

    def test_malformed_config_sections_report_validation_errors(self):
        for key in ('profiles', 'agents'):
            for value in (None, [], 'invalid'):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'fixture'):
                    models.validate_execution_config({key: value}, 'fixture')

    def test_profile_preserves_target_when_candidate_has_multiple_bindings(self):
        self.data['models']['opus']['runtimes']['codex'] = {'id': 'codex/opus'}
        self.profile({'codex': {'candidate': 'opus'}})
        result = self.resolve(selection=self.choose('sol'))
        self.assertEqual(result['candidate'], 'opus')
        self.assertEqual(result['target_runtime'], 'codex')
        self.assertEqual(result['settings'], {'model': 'codex/opus'})

    def test_target_runtime_applies_to_fixed_catalog_references(self):
        self.data['models']['opus']['runtimes']['codex'] = {'id': 'codex/opus'}
        self.data['agents']['builder'] = {'model': 'opus'}
        self.save()
        self.assertEqual(self.resolve(target_runtime='codex')['settings'], {'model': 'codex/opus'})

    def test_runtime_pin_requires_declared_catalog_bindings(self):
        native = {'executor': {'type': 'subagent'}}
        external = {'executor': {'type': 'skill', 'config': {
            'skill': 'custom:worker', 'instruction': 'Run the assignment.'}}}
        for entry in (None, native, {'candidates': {'native': native}},
                      {'candidates': {'external': external}}):
            with self.subTest(entry=entry):
                self.data['agents']['builder'] = entry
                self.save()
                with self.assertRaisesRegex(ValueError, 'target runtime requires'):
                    self.resolve(target_runtime='codex')

    def test_invalid_structured_router_output_is_recoverable(self):
        for name in ([], {}, 123):
            with self.subTest(name=name):
                self.assertEqual(self.resolve(selection=self.choose(name))['status'], 'selection_required')

    def test_profile_recovery_releases_unavailable_candidates_thinking(self):
        self.profile({'codex': {'candidate': 'sol', 'thinking': 'high'}})
        result = self.resolve(target_runtime='codex', eligible_candidates=['luna'],
                              selection=self.choose('luna', thinking='max'))
        self.assertEqual(result['settings'], {'model': 'codex/luna', 'effort': 'max'})

    def test_pending_selection_cannot_render_native_launch_profile(self):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, '-B', str(models.ROOT / 'scripts/agent_models.py'),
                                 '--repo', str(self.root), '--user-executors', str(self.user),
                                 '--host', 'claude', '--job', 'ticket', '--route', '--profile'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Select a candidate', result.stderr)

    def test_inspection_filter_survives_profile_runtime_selection(self):
        self.profile({'codex': {'candidate': 'sol'}})
        listing = self.resolve(list_candidates=True)
        result = self.resolve(eligible_candidates=list(listing['candidates']), selection=self.choose('sol'))
        self.assertEqual(result['candidate'], 'sol')
        self.assertEqual(result['target_runtime'], 'codex')




if __name__ == '__main__':
    unittest.main()
