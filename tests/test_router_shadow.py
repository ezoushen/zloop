#!/usr/bin/env python3
"""Cost-aware shadow choices, endpoint policy and local backend capacity during routing."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import unittest
from unittest.mock import patch

from router_case import RouterCase


class ShadowAndCapacity(RouterCase):
    def test_training_endpoints_are_unavailable_unless_a_config_layer_allows_them(self):
        self.free_and_local()
        listed = self.resolve(list_candidates=True)
        self.assertIn('trains on prompts', listed['unavailable_candidates']['muse'])
        self.assertNotIn('muse', listed['candidates'])
        self.assertIn('glm', listed['candidates'])
        with self.assertRaisesRegex(ValueError, 'not eligible'):
            self.resolve(candidate='muse')
        self.project.write_text(json.dumps({'allow_training': True}))
        self.assertEqual(self.resolve(candidate='muse')['candidate'], 'muse')
        self.data['models']['muse']['cost'] = 'cheap'
        self.save()
        with self.assertRaises(ValueError):
            self.resolve(list_candidates=True)

    def test_each_route_reports_a_cost_aware_shadow_choice_without_changing_it(self):
        self.free_and_local()
        import outcomes
        for index in range(9):
            # Attempts registered without a candidate name are known by their model ID.
            outcomes.put(f'seed#sol-{index}', {'agent': 'builder', 'job': 'ticket', 'candidate': 'codex/sol' if index else 'sol',
                                               'status': 'succeeded', 'accepted': True})
        outcomes.put('seed#glm-0', {'agent': 'builder', 'job': 'ticket', 'candidate': 'glm', 'status': 'failed'})
        self.profile({'codex': {'candidate': 'sol'}})
        result = self.resolve(target_runtime='codex', assignment='Implement ticket 1.')
        self.assertEqual((result['candidate'], result['selection_source']), ('sol', 'profile'))
        shadow = result['shadow_selection']
        # Every reachable candidate competes, whatever runtime the route was pinned to; muse stays behind
        # the training boundary, and a host's reasoned exclusion still applies.
        self.assertEqual(set(shadow['candidates']), {'luna', 'sol', 'opus', 'glm'})
        narrowed = self.resolve(target_runtime='codex', assignment='Implement ticket 1.',
                                eligible_candidates=['sol', 'luna', 'opus'])['shadow_selection']
        self.assertEqual(set(narrowed['candidates']), {'luna', 'sol', 'opus'})
        sol, glm = shadow['candidates']['sol'], shadow['candidates']['glm']
        self.assertEqual((sol['cost'], sol['successes'], sol['failures'], sol['mean']), ('subscription', 9, 0, 0.909))
        self.assertEqual((glm['cost'], glm['successes'], glm['failures'], glm['mean']), ('local', 0, 1, 0.333))
        self.assertAlmostEqual(sol['utility'], sol['sample'] - shadow['cost_weight'], places=3)
        self.assertAlmostEqual(glm['utility'], glm['sample'], places=3)
        self.assertEqual(shadow['choice'], max(shadow['candidates'], key=lambda n: shadow['candidates'][n]['utility']))
        self.assertEqual(shadow['agrees'], shadow['choice'] == 'sol')
        # The same assignment draws the same samples; another assignment draws its own.
        self.assertEqual(self.resolve(target_runtime='codex', assignment='Implement ticket 1.')['shadow_selection'], shadow)
        samples = {self.resolve(target_runtime='codex', assignment=f'Implement ticket {n}.')['shadow_selection']['candidates']
                   ['glm']['sample'] for n in range(2, 8)}
        self.assertGreater(len(samples), 1)

    def test_shadow_cost_weight_follows_each_providers_allowance_pressure(self):
        self.free_and_local()
        codex = self.root / 'codex-home'
        rollout = codex / 'sessions/2026/10/15/rollout-a.jsonl'
        rollout.parent.mkdir(parents=True)
        now = __import__('time').time()
        rollout.write_text(json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count', 'rate_limits': {
            'limit_id': 'codex', 'primary': {'used_percent': 95.0, 'window_minutes': 300,
                                             'resets_at': now + 600}}}}) + '\n')
        claude = self.root / 'state/zloop/claude-allowance.json'
        claude.parent.mkdir(parents=True)
        claude.write_text(json.dumps({'recorded_at': now, 'rate_limits': {
            'five_hour': {'used_percentage': 10, 'resets_at': now + 600}}}))
        self.profile({'codex': {'candidate': 'sol'}})
        self.data['models']['opus']['cost'] = 'subscription'
        self.save()
        # Weights show only for candidates routing still reaches, so the critical provider is included.
        self.project.write_text(json.dumps({'include_critical_allowance': True}))
        with patch.dict(os.environ, {'CODEX_HOME': str(codex)}):
            shadow = self.resolve(target_runtime='codex', assignment='Implement ticket 1.')['shadow_selection']
        self.assertEqual(shadow['allowance'], {'claude': 'normal', 'codex': 'critical'})
        weights = {name: round(value['sample'] - value['utility'], 3) for name, value in shadow['candidates'].items()}
        self.assertEqual(weights, {'sol': 1.0, 'luna': 1.0, 'opus': 0.15, 'glm': 0.0})

    def test_a_full_local_backend_makes_its_candidates_unavailable_and_routes_carry_the_lease(self):
        self.free_and_local()
        self.data['models']['glm']['backend'] = 'dgx'
        self.data['capacity'] = {'dgx': {'slots': 1, 'health': 'http://127.0.0.1:9/health'}}
        self.save()
        self.assertIn('dgx is unhealthy', self.resolve(list_candidates=True)['unavailable_candidates']['glm'])
        del self.data['capacity']['dgx']['health']
        self.save()
        route = self.resolve(candidate='glm')
        self.assertEqual((route['backend'], route['capacity']['slots']), ('dgx', 1))
        import capacity
        state = self.root / 'effort/workers.sqlite'
        state.parent.mkdir()
        import sqlite3
        with sqlite3.connect(state) as db:
            db.execute('CREATE TABLE attempts (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('INSERT INTO attempts VALUES (?, ?)', ('t1/1', json.dumps({'attempt': 't1/1'})))
        db = capacity.connect()
        db.execute('INSERT INTO leases VALUES (?, ?, ?, ?)', (str(state), 't1/1', 'dgx', 0))
        db.close()
        listed = self.resolve(list_candidates=True)
        self.assertIn('dgx is at capacity (1/1)', listed['unavailable_candidates']['glm'])
        with self.assertRaisesRegex(ValueError, 'not eligible'):
            self.resolve(candidate='glm')
        # A role pinned to the model fails the same way.
        self.data['agents']['builder'] = {'model': 'glm'}
        self.save()
        with self.assertRaisesRegex(ValueError, 'dgx is at capacity'):
            self.resolve()
        self.data['capacity']['dgx']['slots'] = 2
        self.save()
        self.assertEqual(self.resolve()['backend'], 'dgx')
        self.data['capacity']['dgx']['slots'] = 0
        self.save()
        with self.assertRaises(ValueError):
            self.resolve(list_candidates=True)

    def test_each_backend_is_checked_once_per_route(self):
        self.free_and_local()
        hits = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        for name in ('glm', 'muse'):
            self.data['models'][name]['backend'] = 'lane'
        self.data['capacity'] = {'lane': {'slots': 4, 'health': f'http://127.0.0.1:{server.server_port}/health'}}
        self.project.write_text(json.dumps({'allow_training': True}))
        self.profile({'codex': {'candidate': 'sol'}})
        self.resolve(target_runtime='codex', assignment='Implement ticket 1.')
        self.assertEqual(hits, ['/health'])




if __name__ == '__main__':
    unittest.main()
