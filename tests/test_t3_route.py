#!/usr/bin/env python3
"""The built-in `t3` executor type routes workers through T3 Code like `herdr` routes them through Herdr.

Business rules: the t3 route mirrors the built-in herdr route; the design doc lives in the workstream alignment.
- `t3` is a built-in executor type: valid wherever the built-in `herdr` type is valid,
  expanding with no executor_types definition, and replaceable by a same-named type.
- Unlike `herdr`, it is never ambient: a configuration layer must select it (a `t3`
  context key in a runtime's executors map, or a `{"type": "t3"}` route). No
  environment variable activates it.
- Its executor guide is hosts/t3.md.
"""

import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import support


models = support.load_script('agent_models.py', 'agent_models')


class BuiltInType(unittest.TestCase):
    def test_t3_expands_as_a_built_in_type_like_herdr(self):
        # A user type named t3 replaces the built-in in full; without one it expands as built-in.
        for name, expect_source in (('t3', 'built-in'), ('herdr', 'built-in')):
            instance, detail = models.expand_executor_type({'executor': {'type': name}}, {})
            self.assertEqual(instance['executor'], {'type': name})
            self.assertEqual((detail['configured_executor_type'], detail['executor_definition_source']),
                             (name, expect_source))
        wrapper = {'type': 'skill', 'config': {'skill': 't3-bridge', 'instruction': 'Run it.'}}
        instance, detail = models.expand_executor_type({'executor': {'type': 't3'}},
                                                       {'t3': (wrapper, 'user:executor_types.t3')})
        self.assertEqual((instance['executor'], detail['executor_definition_source']),
                         (wrapper, 'user:executor_types.t3'))
        for name in ('subagent', 'skill', 'bash'):
            with self.assertRaisesRegex(ValueError, 'Unknown executor type'):
                models.expand_executor_type({'executor': {'type': 'no-such-type'}}, {})

    def test_schema_accepts_t3_where_it_accepts_herdr(self):
        from executor_config import validate_schema
        validate_schema({'agents': {'reviewer': {'executor': {'type': 't3'}}}}, 'test', 'executors.schema.json')
        validate_schema({'executor_types': {'t3': {'type': 't3'}}}, 'test', 'executors.schema.json')
        with self.assertRaises(ValueError):
            validate_schema({'runtimes': {'pi': {'executors': {'no-such-key': {'type': 't3'}}}}},
                            'test', 'executors.schema.json')


class ExplicitGate(unittest.TestCase):
    """The t3 route activates only by configuration selection, never by environment detection."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop t3 gate ')
        self.addCleanup(tmp.cleanup)
        machine = tempfile.TemporaryDirectory(prefix='zloop machine ')
        self.addCleanup(machine.cleanup)
        env = patch.dict(os.environ, support.isolated_env(machine.name, ZLOOP_PROFILE='', HERDR_ENV=''))
        env.start()
        self.addCleanup(env.stop)
        self.root = Path(tmp.name)
        self.user = self.root / 'executors.json'
        self.user.write_text(json.dumps({
            'models': {'sol': {'description': 'Implementation and repair.',
                               'runtimes': {'pi': {'id': 'pi-sol', 'provider': 'pi'}}}},
            'runtimes': {'pi': {'executors': {'t3': {'type': 't3'}}}},
            'agents': {'reviewer': {'model': 'sol'}}}))
        # A live T3 environment: home marker plus a t3 stub on PATH.
        home = self.root / 't3home' / 'userdata'
        home.mkdir(parents=True)
        (home / 'server-runtime.json').write_text('{}')
        import stat
        directory = self.root / 'bin'
        directory.mkdir(exist_ok=True)
        t3 = directory / 't3'
        t3.write_text('#!' + sys.executable + '\nimport json, sys\n'
                      'print(json.dumps([{"threadId": "th-1"}]))\n')
        t3.chmod(stat.S_IRWXU)
        env2 = patch.dict(os.environ, {'PATH': str(directory) + os.pathsep + os.environ['PATH'],
                                       'T3CODE_HOME': str(self.root / 't3home')})
        env2.start()
        self.addCleanup(env2.stop)

    def resolve(self, host='codex', **task):
        return models.resolve_execution(self.root, self.root / 'absent.json', self.user,
                                        host, 'reviewer', task)

    def test_a_configured_t3_route_selects_without_any_environment_marker(self):
        result = self.resolve()
        self.assertEqual((result['executor_type'], result['executor_context']), ('t3', 't3'))
        self.assertEqual(result['settings'], {'model': 'pi-sol', 'provider': 'pi'})
        self.assertEqual(result['executor_guide'], str(models.ROOT / 'hosts/t3.md'))
        self.assertFalse(result['native_dispatch'])

    def test_without_a_t3_route_the_resolver_behaves_exactly_as_before(self):
        # A caller key is preferred over the explicit t3 fallback; no herdr appears outside HERDR_ENV.
        self.user.write_text(json.dumps({
            'models': {'sol': {'description': 'Implementation and repair.',
                               'runtimes': {'pi': {'id': 'pi-sol', 'provider': 'pi'}}}},
            'runtimes': {'pi': {'executors': {'codex': {'type': 'skill', 'config': {'skill': 'pi-bridge', 'instruction': 'Run it.'}},
                                              't3': {'type': 't3'}}}},
            'agents': {'reviewer': {'model': 'sol'}}}))
        result = self.resolve(host='codex')
        self.assertEqual((result['executor_type'], result['executor_context']), ('skill', 'codex'))
        self.assertFalse(result['native_dispatch'])

    def test_no_t3_route_means_no_route_even_inside_herdr(self):
        # Ambient Herdr activation does not make t3 reachable: the explicit gate is the only entry.
        self.user.write_text(json.dumps({
            'models': {'sol': {'description': 'Implementation and repair.',
                               'runtimes': {'pi': {'id': 'pi-sol', 'provider': 'pi'}}}},
            'runtimes': {'pi': {'executors': {'pi': {'type': 'subagent'}, 'herdr': {'type': 'herdr'}}}},
            'agents': {'reviewer': {'model': 'sol'}}}))
        with patch.dict(os.environ, {'HERDR_ENV': '1'}):
            result = self.resolve()
        # The ambient herdr fallback serves the Pi binding even without a configured route; t3 stays unreachable because
        # nothing selected it.
        self.assertEqual((result['executor_type'], result['executor_context']), ('herdr', 'herdr'))

    def test_a_t3_type_route_in_an_agent_entry_selects_by_itself(self):
        self.user.write_text(json.dumps({
            'agents': {'reviewer': {'executor': {'type': 't3'},
                                    'models': {'mode': 'override', 'model': 'direct-model'}}}}))
        result = self.resolve()
        self.assertEqual((result['executor_type'], result['executor_guide']),
                         ('t3', str(models.ROOT / 'hosts/t3.md')))
        self.assertEqual(result['settings'], {'model': 'direct-model'})




class ResolverPreflight(unittest.TestCase):
    """SR-08: the resolver runs the layered T3 preflight before returning a t3 route; a failing environment refuses
    with the layer's reason (the explicit gate includes the environment, not just the config key)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop t3 resolver preflight ')
        self.addCleanup(tmp.cleanup)
        machine = tempfile.TemporaryDirectory(prefix='zloop machine ')
        self.addCleanup(machine.cleanup)
        env = patch.dict(os.environ, support.isolated_env(machine.name, ZLOOP_PROFILE='', HERDR_ENV=''))
        env.start()
        self.addCleanup(env.stop)
        self.root = Path(tmp.name)
        self.user = self.root / 'executors.json'
        self.user.write_text(json.dumps({
            'models': {'sol': {'description': 'Implementation and repair.',
                               'runtimes': {'pi': {'id': 'pi-sol', 'provider': 'pi'}}}},
            'runtimes': {'pi': {'executors': {'t3': {'type': 't3'}}}},
            'agents': {'reviewer': {'model': 'sol'}}}))
        # A t3 stub on PATH; each test decides whether the home marker exists.
        import stat
        directory = self.root / 'bin'
        directory.mkdir(exist_ok=True)
        t3 = directory / 't3'
        t3.write_text('#!' + sys.executable + '\nimport json, sys\n'
                      'print(json.dumps([{"threadId": "th-1"}]))\n')
        t3.chmod(stat.S_IRWXU)
        env2 = patch.dict(os.environ, {'PATH': str(directory) + os.pathsep + os.environ['PATH'],
                                       'T3CODE_HOME': str(self.root / 't3home')})
        env2.start()
        self.addCleanup(env2.stop)

    def resolve(self):
        return models.resolve_execution(self.root, self.root / 'absent.json', self.user,
                                        'codex', 'reviewer', {})

    def stub_t3(self, *, server=True, project=True):
        import stat
        directory = self.root / 'bin'
        directory.mkdir(exist_ok=True)
        executable = directory / 't3'
        body = (json.dumps([{'threadId': 'th-1'}]) if server and project else
                ('No T3 Code project matches. Pass a project id or a path inside a project.' if server
                 else 'No running T3 Code server uses /home. Start T3 Code, then retry.'))
        code = 0 if server else 1
        executable.write_text('#!' + sys.executable + '\n' + f'''
import sys
print({body!r})
sys.exit({code})
''')
        executable.chmod(stat.S_IRWXU)
        return patch.dict(os.environ, {'PATH': str(directory) + os.pathsep + os.environ['PATH'],
                                       'T3CODE_HOME': str(self.root / 't3home')})

    def home_marker(self):
        home = self.root / 't3home' / 'userdata'
        home.mkdir(parents=True, exist_ok=True)
        (home / 'server-runtime.json').write_text('{}')

    def test_a_live_environment_returns_the_route(self):
        self.home_marker()
        with self.stub_t3():
            result = self.resolve()
        self.assertEqual(result['executor_type'], 't3')

    def test_a_dead_environment_refuses_with_the_layers_reason(self):
        self.home_marker()
        with self.stub_t3(server=False):
            with self.assertRaisesRegex(ValueError, 'server'):
                self.resolve()

    def test_a_missing_home_refuses_before_any_cli_call(self):
        with self.stub_t3():
            with self.assertRaisesRegex(ValueError, 'home'):
                self.resolve()



class ProviderMapping(unittest.TestCase):
    """R3-04 (A15/D-2): a t3 route's model binding maps to the T3 provider instance id and the model slug the
    provider accepts; the resolver refuses an unknown runtime binding instead of guessing a slug."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop t3 provider ')
        self.addCleanup(tmp.cleanup)
        machine = tempfile.TemporaryDirectory(prefix='zloop machine ')
        self.addCleanup(machine.cleanup)
        env = patch.dict(os.environ, support.isolated_env(machine.name, ZLOOP_PROFILE='', HERDR_ENV=''))
        env.start()
        self.addCleanup(env.stop)
        self.root = Path(tmp.name)
        self.user = self.root / 'executors.json'
        # A live T3 environment for the resolver's preflight.
        home = self.root / 't3home' / 'userdata'
        home.mkdir(parents=True)
        (home / 'server-runtime.json').write_text('{}')
        import stat
        directory = self.root / 'bin'
        directory.mkdir(exist_ok=True)
        t3 = directory / 't3'
        t3.write_text('#!' + sys.executable + '\nimport json, sys\n'
                      'print(json.dumps([{"threadId": "th-1"}]))\n')
        t3.chmod(stat.S_IRWXU)
        env2 = patch.dict(os.environ, {'PATH': str(directory) + os.pathsep + os.environ['PATH'],
                                       'T3CODE_HOME': str(self.root / 't3home')})
        env2.start()
        self.addCleanup(env2.stop)

    def test_the_binding_names_the_provider_and_the_resolver_carries_it(self):
        # Rule: models.<name>.runtimes.<t3-runtime>.provider names the T3 provider instance id ('pi', 'codex',
        # 'claudeAgent', 'opencode'); the resolved t3 route carries it as settings.provider for t3 thread start.
        self.user.write_text(json.dumps({
            'models': {'sol': {'description': 'Implementation and repair.',
                               'runtimes': {'pi': {'id': 'dgx/glm-5.3-flash', 'provider': 'pi'}}}},
            'runtimes': {'pi': {'executors': {'t3': {'type': 't3'}}}},
            'agents': {'reviewer': {'model': 'sol'}}}))
        result = models.resolve_execution(self.root, self.root / 'absent.json', self.user,
                                          'codex', 'reviewer', {})
        self.assertEqual(result['executor_type'], 't3')
        self.assertEqual(result['settings'], {'model': 'dgx/glm-5.3-flash', 'provider': 'pi'})

    def resolve(self):
        return models.resolve_execution(self.root, self.root / 'absent.json', self.user,
                                        'codex', 'reviewer', {})

if __name__ == '__main__':
    unittest.main()
