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
        with self.assertRaisesRegex(ValueError, 'Unknown executor type'):
            models.expand_executor_type({'executor': {'type': 'no-such-type'}}, {})

    def test_schema_accepts_an_environment_target_and_rejects_its_misuse(self):
        # A t3 route may name a linked environment; the field is a nonempty string, so a number or a
        # structured form is misuse.
        from executor_config import validate_schema
        validate_schema({'agents': {'reviewer': {'executor': {'type': 't3', 'environment': 'peer-host'}}}},
                        'test', 'executors.schema.json')
        with self.assertRaises(ValueError):
            validate_schema({'agents': {'reviewer': {'executor': {'type': 't3', 'environment': 7}}}},
                            'test', 'executors.schema.json')
        with self.assertRaises(ValueError):
            validate_schema({'agents': {'reviewer': {'executor':
                                         {'type': 't3', 'environment': {'label': 'peer-host'}}}}},
                            'test', 'executors.schema.json')
        with self.assertRaises(ValueError):
            validate_schema({'agents': {'reviewer': {'executor': {'type': 't3', 'environment': '   '}}}},
                            'test', 'executors.schema.json')

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
        # A caller key is preferred over the explicit t3 fallback; and REMOVING the t3 key must leave the resolution
        # byte-identical, so the t3 route's presence is provably not what the caller-key path consumed.
        config = {
            'models': {'sol': {'description': 'Implementation and repair.',
                               'runtimes': {'pi': {'id': 'pi-sol', 'provider': 'pi'}}}},
            'runtimes': {'pi': {'executors': {'codex': {'type': 'skill', 'config': {'skill': 'pi-bridge', 'instruction': 'Run it.'}},
                                              't3': {'type': 't3'}}}},
            'agents': {'reviewer': {'model': 'sol'}}}
        self.user.write_text(json.dumps(config))
        with_t3 = self.resolve(host='codex')
        without = dict(config)
        without['runtimes'] = {'pi': {'executors': {'codex': config['runtimes']['pi']['executors']['codex']}}}
        self.user.write_text(json.dumps(without))
        result = self.resolve(host='codex')
        self.assertEqual((result['executor_type'], result['executor_context']), ('skill', 'codex'))
        self.assertEqual(result, with_t3)
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
    """The resolver runs the layered T3 preflight before returning a t3 route; a failing environment refuses
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

    def test_a_dead_environment_refuses_with_the_layers_reason(self):
        self.home_marker()
        with self.stub_t3(server=False):
            with self.assertRaisesRegex(ValueError, 'server'):
                self.resolve()

    def test_a_missing_home_refuses_before_any_cli_call(self):
        with self.stub_t3():
            with self.assertRaisesRegex(ValueError, 'home'):
                self.resolve()


class EnvironmentTarget(unittest.TestCase):
    """A t3 route may name a linked environment: the resolver carries the resolved target and the layered
    preflight verifies it is linked and answering, at or above the agent's writes need, before the route
    returns. A route without the field behaves exactly as before and never asks the environment source."""

    PEER = {'environmentId': 'env-peer-1', 'label': 'peer', 'urls': ['https://peer.example:3773'],
            'access': 'approval-required', 'status': 'reachable'}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='zloop t3 environment ')
        self.addCleanup(tmp.cleanup)
        machine = tempfile.TemporaryDirectory(prefix='zloop machine ')
        self.addCleanup(machine.cleanup)
        env = patch.dict(os.environ, support.isolated_env(machine.name, ZLOOP_PROFILE='', HERDR_ENV=''))
        env.start()
        self.addCleanup(env.stop)
        self.root = Path(tmp.name)
        self.user = self.root / 'executors.json'
        self.write_route(environment=None, agent='reviewer')

    def write_route(self, environment, agent='reviewer'):
        route = {'type': 't3'}
        if environment is not None:
            route['environment'] = environment
        self.user.write_text(json.dumps({
            'models': {'sol': {'description': 'Implementation and repair.',
                               'runtimes': {'pi': {'id': 'pi-sol', 'provider': 'pi'}}}},
            'runtimes': {'pi': {'executors': {'t3': route}}},
            'agents': {agent: {'model': 'sol'}}}))

    def stub_t3(self, links, *, environment_fails=False):
        import stat
        directory = self.root / 'bin'
        directory.mkdir(exist_ok=True)
        executable = directory / 't3'
        executable.write_text('#!' + sys.executable + '\n' + f'''
import json, sys
args = sys.argv[1:]
with open({str(self.root / 'argv.log')!r}, 'a') as log:
    log.write(' '.join(args) + chr(10))
if args[:2] == ['environment', 'list'] and '--json' in args:
    if {environment_fails!r}:
        sys.exit(3)
    print(json.dumps({links!r} if {links!r} is not None else []))
    sys.exit(0)
print(json.dumps([{{'threadId': 'th-1'}}]))
sys.exit(0)
''')
        executable.chmod(stat.S_IRWXU)
        env = patch.dict(os.environ, {'PATH': str(directory) + os.pathsep + os.environ['PATH'],
                                      'T3CODE_HOME': str(self.root / 't3home')})
        env.start()
        self.addCleanup(env.stop)
        home = self.root / 't3home' / 'userdata'
        home.mkdir(parents=True, exist_ok=True)
        (home / 'server-runtime.json').write_text('{}')

    def calls(self):
        log = self.root / 'argv.log'
        return log.read_text().splitlines() if log.exists() else []

    def resolve(self, agent='reviewer'):
        return models.resolve_execution(self.root, self.root / 'absent.json', self.user,
                                        'codex', agent, {})

    def test_a_route_naming_a_linked_environment_resolves_carrying_it(self):
        # The resolved execution names the linked environment the route targeted, with what the link grants.
        self.stub_t3([self.PEER])
        self.write_route('peer')
        result = self.resolve()
        self.assertEqual((result['executor_type'], result['executor_context']), ('t3', 't3'))
        # Ledger A5 as repaired (review F1): the top-level key is the environment id string; the access detail
        # rides environment_access for provenance.
        self.assertEqual(result['environment'], 'env-peer-1')
        self.assertEqual((result['environment_access']['id'], result['environment_access']['label'],
                          result['environment_access']['access']), ('env-peer-1', 'peer', 'approval-required'))
        self.assertIn('environment list --json', self.calls())

    def test_a_route_naming_an_unlinked_environment_refuses_at_the_environment_layer(self):
        # An empty answer and a populated one refuse alike; the refusal is not a local home/server/project one.
        self.stub_t3([self.PEER])
        self.write_route('nowhere')
        with self.assertRaisesRegex(ValueError, "environment.*unlinked.*'nowhere'"):
            self.resolve()

    def test_a_route_naming_a_silent_environment_refuses_unreachable(self):
        self.stub_t3([dict(self.PEER, status='unreachable')])
        self.write_route('peer')
        with self.assertRaisesRegex(ValueError, 'environment.*unreachable'):
            self.resolve()

    def test_a_route_narrower_than_the_link_refuses_at_the_ceiling(self):
        # A builder writes its worktree there, so approval-required access is below the route's need.
        self.stub_t3([self.PEER])
        self.write_route('peer', agent='builder')
        with self.assertRaisesRegex(ValueError, 'environment.*ceiling.*auto-accept-edits'):
            self.resolve(agent='builder')

    def test_without_an_environment_field_the_resolution_asks_the_environment_source_nothing(self):
        # The no-environment control: the route resolves exactly as delivered and `t3 environment list` is
        # never consulted, so the field's absence is provably what the gate read.
        self.stub_t3([self.PEER])
        result = self.resolve()
        self.assertEqual((result['executor_type'], result['executor_context']), ('t3', 't3'))
        self.assertNotIn('environment', result)
        self.assertNotIn('environment list', ' '.join(self.calls()))


if __name__ == '__main__':
    unittest.main()
