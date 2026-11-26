#!/usr/bin/env python3
"""Routing network boundaries through the agent-models CLI."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest

from router_case import RouterCase, models
import support


class RouteNetwork(RouterCase):
    def setUp(self):
        super().setUp()
        self.hits = []
        self.responses = {}
        hits, responses = self.hits, self.responses

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                status = responses.get(self.path, 200)
                self.send_response(status if isinstance(status, int) else 302)
                if isinstance(status, str):
                    self.send_header('Location', status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.data['capacity'] = {}
        for name in ('luna', 'sol', 'opus'):
            self.data['models'][name]['backend'] = name
            self.data['capacity'][name] = {'slots': 2, 'health': f'{self.base}/{name}'}
        self.save()

    def cli(self, *args, listing=False, env=None):
        return subprocess.run(
            [sys.executable, str(Path(models.__file__)), '--repo', str(self.root),
             '--host', 'codex', '--user-config', str(self.root / 'absent.json'),
             '--user-executors', str(self.user), '--route',
             *(['--agent', 'builder', '--list-candidates'] if listing else ['--job', 'ticket']), *args],
            check=False, capture_output=True, text=True,
            env={**os.environ, 'HERDR_ENV': '', 'ZLOOP_NO_NETWORK': '', 'no_proxy': '*', **(env or {})})

    def test_explicit_pin_probes_only_pinned_candidate_and_never_falls_back(self):
        result = self.cli('--candidate', 'sol')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['candidate'], 'sol')
        self.assertEqual(self.hits, ['/sol'])

        self.hits.clear()
        self.responses['/sol'] = 503
        result = self.cli('--candidate', 'sol')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("candidate 'sol' is not eligible", result.stderr)
        self.assertIn('Backend sol is unhealthy', result.stderr)
        self.assertEqual(self.hits, ['/sol'])

    def managed(self, denied):
        card = support.run_card(self.root / '.scratch/run/autopilot.md', **{'deny-hosts': denied})
        support.load_script('run_control.py').adopt(card, 'approved test routes', allow_resume=True)
        packet = self.root / 'packet.txt'
        packet.write_text('attempt: probe-1\nCheck a local fixture.\n')
        return card, ('--dispatch-card', str(card), '--assignment-id', 'probe-1',
                      '--assignment', str(packet), '--worktree', str(self.root))

    def guard_network(self):
        probe = self.root / 'network-attempts'
        (self.root / 'sitecustomize.py').write_text(
            'import os, sys\n'
            'def guard(event, args):\n'
            '    host = args[0] if event == "socket.getaddrinfo" else None\n'
            '    if host and host != "127.0.0.1":\n'
            '        with open(os.environ["NETWORK_ATTEMPTS"], "a") as output:\n'
            '            output.write(host + "\\n")\n'
            '        raise OSError("test refuses non-fixture DNS")\n'
            'sys.addaudithook(guard)\n')
        return probe, {'PYTHONPATH': str(self.root), 'NETWORK_ATTEMPTS': str(probe)}

    def test_managed_card_excludes_normalized_hosts_before_dns_and_preserves_paths(self):
        port = self.server.server_port
        self.data['capacity']['luna']['health'] = f'http://LOCALHOST.:{port}/luna'
        self.data['capacity']['opus']['health'] = 'http://remote.invalid/opus'
        self.data['models']['opus']['runtimes'] = self.data['models']['sol']['runtimes']
        self.save()
        card, managed = self.managed(f'[LOCALHOST.:{port}, ReMote.Invalid.:80, 127.0.0.1:1]')
        probe, env = self.guard_network()
        result = self.cli('--dispatch-card', str(card), listing=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        listing = json.loads(result.stdout)
        self.assertEqual(listing['availability'], 'unchecked')
        self.assertEqual(set(listing['candidates']), {'sol'})
        self.assertEqual(listing['unavailable_candidates'],
                         {'luna': 'excluded-by-mandate', 'opus': 'excluded-by-mandate'})
        self.assertFalse(card.with_name('dispatch-state.json').exists())
        self.assertEqual(self.hits, [])

        for name in ('luna', 'opus'):
            result = self.cli(*managed, '--candidate', name, env=env)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn(f"candidate '{name}' is not eligible; excluded-by-mandate", result.stderr)
        self.assertEqual(self.hits, [])
        self.assertFalse(probe.exists())

        alias = self.root / 'workspace-alias'
        alias.symlink_to(self.root, target_is_directory=True)
        result = self.cli(*managed, '--repo', str(alias), '--worktree', str(alias),
                          '--assignment', str(alias / 'packet.txt'), env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        route = json.loads(result.stdout)
        self.assertEqual(route['candidate'], 'sol')
        self.assertEqual(route['dispatch']['repo'], str(self.root.resolve()))
        self.assertEqual(route['dispatch']['worktree'], str(self.root.resolve()))
        self.assertEqual(route['dispatch']['packet'], str((self.root / 'packet.txt').resolve()))
        self.assertEqual(self.hits, ['/sol'])
        self.assertFalse(probe.exists())

    def test_pool_probes_only_locally_eligible_candidates(self):
        result = self.cli('--eligible-candidates', '["sol", "opus"]')
        self.assertEqual(result.returncode, 0, result.stderr)
        route = json.loads(result.stdout)
        self.assertEqual(route['candidate'], 'sol')
        self.assertIn('No configured route', route['unavailable_candidates']['opus'])
        self.assertEqual(self.hits, ['/sol'])

        self.hits.clear()
        result = self.cli('--eligible-thinking', '{"luna":[]}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['candidate'], 'sol')
        self.assertEqual(self.hits, ['/sol'])

        self.hits.clear()
        result = self.cli('--eligible-thinking', '{"sol":["unknown"]}')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('exceeds configured thinking choices', result.stderr)
        self.assertEqual(self.hits, [])

    def test_non_decision_pool_probes_only_matching_model_and_runtime(self):
        self.data['runtimes']['claude']['executors']['codex'] = {
            'type': 'skill', 'config': {'skill': 'claude-bridge', 'instruction': 'Run one worker.'}}
        self.save()
        for args, candidates, hits in (
            (('--model', 'codex/sol'), {'luna', 'sol', 'opus'}, ['/sol']),
            (('--target-runtime', 'codex'), {'luna', 'sol'}, ['/luna', '/sol']),
            (('--model', 'codex/sol', '--target-runtime', 'codex'), {'luna', 'sol'}, ['/sol']),
        ):
            with self.subTest(args=args):
                self.hits.clear()
                result = self.cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                route = json.loads(result.stdout)
                self.assertEqual(route['status'], 'selection_required')
                self.assertEqual(self.hits, hits)
                self.assertEqual(set(route['eligible_candidates']), candidates)

    def test_exact_routes_validate_locally_before_health_checks(self):
        self.data['capacity']['luna']['health'] = 'http://remote.invalid/luna'
        self.data['capacity']['opus']['health'] = 'http://remote.invalid/opus'
        self.save()
        probe, env = self.guard_network()
        result = self.cli('--route-choice', '{"executor":{"type":"subagent"}}', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['native_dispatch'])
        self.assertEqual(self.hits, [])
        self.assertFalse(probe.exists())

        self.data['agents']['builder'] = {'model': 'sol'}
        self.save()
        result = self.cli('--thinking', 'unknown', env=env)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("thinking 'unknown' is outside allowed ['medium', 'high']", result.stderr)
        self.assertEqual(self.hits, [])
        result = self.cli(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['settings']['model'], 'codex/sol')
        self.assertEqual(self.hits, ['/sol'])
        self.assertFalse(probe.exists())

    def test_managed_health_redirect_never_reaches_denied_host(self):
        self.responses['/sol'] = 'http://remote.invalid/redirected'
        card, managed = self.managed('[remote.invalid]')
        probe, env = self.guard_network()
        result = self.cli(*managed, '--candidate', 'sol', env=env)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.hits, ['/sol'])
        self.assertFalse(probe.exists())
        self.assertIn('excluded-by-mandate', result.stderr)
        self.assertFalse(card.with_name('dispatch-state.json').exists())

        self.hits.clear()
        self.responses['/sol'] = '/redirected'
        result = self.cli(*managed, '--candidate', 'sol', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['candidate'], 'sol')
        self.assertEqual(self.hits, ['/sol', '/redirected'])
        self.assertFalse(probe.exists())

    def test_offline_decision_uses_safe_fallback_without_inference(self):
        self.fallback_router()
        packet = self.root / 'decision.txt'
        packet.write_text('Choose a worker for this test.')
        result = self.cli('--offline', '--target-runtime', 'codex', '--assignment', str(packet))
        self.assertEqual(result.returncode, 0, result.stderr)
        route = json.loads(result.stdout)
        self.assertEqual(self.hits, [])
        self.assertEqual(self.requests, [])
        self.assertEqual((route['candidate'], route['selection_source']), ('sol', 'fallback-profile'))
        self.assertIn('unchecked in offline routing', route['selection_error'])

    def test_decision_checks_only_candidates_matching_task_model_and_effort(self):
        self.decision_router()
        packet = self.root / 'decision.txt'
        packet.write_text('Choose a worker for this test.')
        for pin in (('--model', 'codex/sol'), ('--thinking', 'high')):
            with self.subTest(pin=pin):
                self.hits.clear()
                result = self.cli(*pin, '--assignment', str(packet))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['settings']['model'], 'codex/sol')
                self.assertEqual(self.hits, ['/sol'])

    def test_free_memory_refusal_precedes_health(self):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        memory_pressure = bin_dir / 'memory_pressure'
        memory_pressure.write_text("#!/bin/sh\nprintf 'System-wide memory free percentage: 50%%\\n'\n")
        memory_pressure.chmod(0o700)
        self.data['capacity']['luna']['min_free_memory_percent'] = 90
        self.save()

        result = self.cli(env={'PATH': str(bin_dir) + os.pathsep + os.environ['PATH']})
        self.assertEqual(result.returncode, 0, result.stderr)
        route = json.loads(result.stdout)
        self.assertEqual(route['candidate'], 'sol')
        self.assertIn('needs 90% free memory', route['unavailable_candidates']['luna'])
        self.assertEqual(self.hits, ['/sol'])

    def test_offline_flag_environment_and_listing_leave_health_unchecked(self):
        self.responses['/sol'] = 503
        for args, kwargs in (
            (('--candidate', 'sol', '--offline'), {}),
            (('--candidate', 'sol'), {'env': {'ZLOOP_NO_NETWORK': '1'}}),
            ((), {'listing': True}),
        ):
            with self.subTest(args=args, kwargs=kwargs):
                result = self.cli(*args, **kwargs)
                self.assertEqual(result.returncode, 0, result.stderr)
                route = json.loads(result.stdout)
                self.assertEqual(route['availability'], 'unchecked')
                if kwargs.get('listing'):
                    self.assertEqual(set(route['candidates']), {'luna', 'sol'})
                else:
                    self.assertEqual(route['candidate'], 'sol')
                self.assertEqual(self.hits, [])

        result = self.cli('--candidate', 'sol')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('Backend sol is unhealthy', result.stderr)
        self.assertEqual(self.hits, ['/sol'])


if __name__ == '__main__':
    unittest.main()
