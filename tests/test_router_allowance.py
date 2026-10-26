#!/usr/bin/env python3
"""A provider at critical allowance stops receiving workers unless a configuration layer includes it."""

import json
import time
import unittest

from router_case import RouterCase


class CriticalAllowance(RouterCase):
    def codex_critical(self):
        """Inject the readings the way the shadow-weight test does: codex at 95%, claude at 10%."""
        now = time.time()
        rollout = self.root / 'codex-home/sessions/2026/10/15/rollout-a.jsonl'
        rollout.parent.mkdir(parents=True)
        rollout.write_text(json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count', 'rate_limits': {
            'limit_id': 'codex', 'primary': {'used_percent': 95.0, 'window_minutes': 300,
                                             'resets_at': now + 600}}}}) + '\n')
        claude = self.root / 'state/zloop/claude-allowance.json'
        claude.parent.mkdir(parents=True)
        claude.write_text(json.dumps({'recorded_at': now, 'rate_limits': {
            'five_hour': {'used_percentage': 10, 'resets_at': now + 600}}}))

    def test_a_critical_provider_is_unavailable_by_default_and_others_stay_eligible(self):
        self.codex_critical()
        listed = self.resolve(list_candidates=True)
        for name in ('luna', 'sol'):
            self.assertIn('allowance', listed['unavailable_candidates'][name])
            self.assertNotIn(name, listed['candidates'])
        self.assertEqual(set(listed['candidates']), {'opus'})
        self.assertNotIn('opus', listed['unavailable_candidates'])
        # An excluded candidate does not compete in the shadow choice either.
        self.profile({'claude': {'candidate': 'opus'}})
        route = self.resolve(assignment='Implement ticket 1.')
        self.assertEqual(set(route['shadow_selection']['candidates']), {'opus'})

    def test_a_layer_that_includes_critical_providers_makes_them_eligible_and_weighted_again(self):
        self.codex_critical()
        self.data['models']['sol']['cost'] = 'subscription'
        self.save()
        self.project.write_text(json.dumps({'include_critical_allowance': True}))
        listed = self.resolve(list_candidates=True)
        self.assertEqual(set(listed['candidates']), {'luna', 'sol', 'opus'})
        self.assertEqual(listed['unavailable_candidates'], {})
        self.profile({'codex': {'candidate': 'sol'}})
        route = self.resolve(target_runtime='codex', assignment='Implement ticket 1.')
        self.assertEqual(route['shadow_selection']['allowance']['codex'], 'critical')
        shadow = route['shadow_selection']['candidates']['sol']
        self.assertEqual(round(shadow['sample'] - shadow['utility'], 3), 1.0)

    def test_a_job_whose_candidates_are_all_critical_is_refused_naming_the_allowance_and_the_setting(self):
        self.codex_critical()
        self.data['agents']['builder']['candidates'] = ['luna', 'sol']
        self.save()
        with self.assertRaisesRegex(ValueError, r'(?s)allowance.*include_critical_allowance'):
            self.resolve()
        self.project.write_text(json.dumps({'include_critical_allowance': True}))
        self.assertEqual(self.resolve(candidate='sol')['candidate'], 'sol')


if __name__ == '__main__':
    unittest.main()
