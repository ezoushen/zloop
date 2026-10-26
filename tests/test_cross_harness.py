#!/usr/bin/env python3
"""Integration regressions; all repositories and state are temporary."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import support

PLUGIN = support.ROOT
SCRIPT = PLUGIN / 'scripts/next_tickets.py'


def run(*args, cwd=None, ok=True, input=None):
    p = subprocess.run(args, cwd=cwd, text=True, input=input, capture_output=True)
    if ok:
        assert p.returncode == 0, (args, p.stdout, p.stderr)
    return p


def write_ticket(repo, name, status='ready', deps=(), area=None):
    slug, nn = name.split('/')
    path = repo / '.scratch' / slug / 'issues' / f'{nn}-task.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\nticket: {name}\nstatus: {status}\nblocked_by: [{", ".join(deps)}]\ntouches: [{area or name}]\ndevices: []\nmodel: standard\nworker:\n---\n# Task\n')
    return path


class CrossHarness(unittest.TestCase):
    """Package, scheduler and lock regressions in temporary repositories with isolated state."""

    def setUp(self):
        state = tempfile.TemporaryDirectory(prefix='zloop cross harness state ')
        self.addCleanup(state.cleanup)
        environment = patch.dict(os.environ, support.isolated_env(state.name))
        environment.start()
        self.addCleanup(environment.stop)

    def test_scheduler_scopes_locks_waves_and_acceptance(self):
        with tempfile.TemporaryDirectory(prefix='zloop check ') as tmp:
            repo = Path(tmp) / 'repo'
            repo.mkdir()
            def git(*args):
                return run('git', '-C', str(repo), *args).stdout.strip()
            git('init', '-b', 'main')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.invalid')
            git('commit', '--allow-empty', '-m', 'base')
            def cli(*args, ok=True):
                return run(sys.executable, '-B', str(SCRIPT), '--repo', str(repo), *args, cwd=tmp, ok=ok)
            def names(*args):
                return [x['ticket'] for x in json.loads(cli(*args).stdout)]
            write_ticket(repo, 'a/01')
            b = write_ticket(repo, 'b/01')
            assert names('a') == ['a/01']
            assert 'b/01' not in cli('a', '--board').stdout
            assert cli('missing', ok=False).returncode != 0
            assert cli('--unknown', ok=False).returncode != 0
            assert cli('--board', '--counts', ok=False).returncode != 0
            assert cli('a', '--set', 'b/01', 'status=done', ok=False).returncode != 0
            assert 'status: ready' in b.read_text()
            write_ticket(repo, 'b/01', 'implementing', area='shared')
            write_ticket(repo, 'a/01', area='shared')
            assert names('a') == []  # Scope must still see other efforts' area locks.
            write_ticket(repo, 'a/01')
            assert names('a', '--max-live', '1') == []
            write_ticket(repo, 'b/01', deps=['a/01'])
            write_ticket(repo, 'a/01', deps=['b/01'])
            assert 'cycle' in cli('--check', ok=False).stderr.lower()
            write_ticket(repo, 'a/01', deps=['a/01'])
            assert cli('--check', ok=False).returncode != 0
            write_ticket(repo, 'a/01', 'done')
            review_base = git('rev-parse', 'main')
            git('checkout', '-b', 'feat/a')
            (repo / 'prerequisite.txt').write_text('dependency code\n')
            git('add', 'prerequisite.txt')
            git('commit', '-m', 'dependency')
            git('checkout', 'main')
            git('branch', 'feat/b')  # Existing downstream branch predates the dependency.
            assert names('b') == []
            git('merge', '--no-edit', 'feat/a')
            assert names('b') == []
            git('checkout', 'feat/b')
            git('merge', '--no-edit', 'main')
            git('checkout', 'main')
            assert names('b') == []  # Implemented and merged is not reviewed and verified.
            source_report = repo / '.scratch/a/reports/spec-review.md'
            source_report.parent.mkdir(parents=True)
            reviewed_head = git('rev-parse', 'feat/a')
            evidence = (f'---\nreview_status: clean\ndocumentation_status: passed\nreview_head: {reviewed_head}\n'
                    f'review_base: {review_base}\nreview_diff_base: {review_base}\n'
                    f'e2e_status: passed\ne2e_head: {reviewed_head}\n'
                    f'review_by: review/a/1\ne2e_by: e2e/a/1\n---\n')
            source_report.write_text(evidence)
            assert cli('a', '--delivery', ok=False).returncode != 0  # No worker returned these statuses yet.
            support.collected_attempt(repo, 'a', 'review/a/1', source_report, job='diff-review', head=reviewed_head)
            support.collected_attempt(repo, 'a', 'e2e/a/1', source_report, job='e2e-gate', head=reviewed_head)
            cli('a', '--delivery')
            for valid, invalid in [('review_status: clean', 'review_status: findings'),
                                   ('documentation_status: passed\n', ''),
                                   ('documentation_status: passed', 'documentation_status: findings'),
                                   ('documentation_status: passed', 'documentation_status: incomplete'),
                                   ('e2e_status: passed', 'e2e_status: not-run'),
                                   (f'e2e_head: {reviewed_head}', f'e2e_head: {review_base}'),
                                   (f'review_head: {reviewed_head}', f'review_head: {review_base}'),
                                   (f'review_diff_base: {review_base}', f'review_diff_base: {reviewed_head}'),
                                   # Status without the run that produced it is not delivery evidence.
                                   ('review_by: review/a/1\n', ''),
                                   ('e2e_by: e2e/a/1', 'e2e_by: orchestrator'),
                                   ('review_by: review/a/1', 'review_by: host'),
                                   ('review_by: review/a/1', 'review_by: none'),
                                   ('review_by: review/a/1', 'review_by: zloop-reviewer/agent-1')]:
                source_report.write_text(evidence.replace(valid, invalid))
                assert cli('a', '--delivery', ok=False).returncode != 0
                assert names('b') == []
            source_report.write_text(evidence)
            assert names('b') == ['b/01']
            write_ticket(repo, 'a/02', 'implementing')
            assert names('b') == []  # An unfinished prerequisite effort still holds the wave.
            write_ticket(repo, 'a/02', 'done')
            changed = cli('b', '--set', 'b/01', 'status=implementing', '--counts')
            assert '1 live' in changed.stdout and '0 done' in changed.stdout
            assert cli('--set', 'b/01', 'status=bogus', ok=False).returncode != 0
            assert cli('--set', 'b/01', 'worker=x\nstatus: done', ok=False).returncode != 0
            # Acceptance requires a real branch descendant and a report in the main checkout.
            base = git('rev-parse', 'feat/b')
            git('checkout', '-b', 'feat/b-01', 'feat/b')
            git('commit', '--allow-empty', '-m', 'ticket')
            git('checkout', 'main')
            reports = repo / '.scratch/b/reports'
            reports.mkdir()
            report = reports / '01.md'
            report.write_text(f'base: {base}\n## Implemented\n- [x] done\n')
            assert cli('b', '--accept', 'b/01', ok=False).returncode != 0  # No worker returned it yet.
            support.collected_attempt(repo, 'b', 'b/01/implementation-1', report, head=git('rev-parse', 'feat/b-01'))
            cli('b', '--accept', 'b/01')
            report.write_text('# empty\n')
            assert cli('b', '--accept', 'b/01', ok=False).returncode != 0
            # No ticket is silently dropped or allowed to overwrite another id.
            duplicate = b.with_name('02-duplicate.md')
            duplicate.write_text(b.read_text())
            assert cli('--check', ok=False).returncode != 0
            duplicate.unlink()
            write_ticket(repo, 'a/02', deps=['b/01'])
            assert 'cycle' in cli('--check', ok=False).stderr.lower()  # effort-level cycle
            assert not (repo / 'scripts').exists()

    def test_package_manifests_skills_links_and_hooks(self):
        import re
        portable = json.loads((PLUGIN / 'plugin.json').read_text())
        claude = json.loads((PLUGIN / '.claude-plugin/plugin.json').read_text())
        assert portable['name'] == claude['name'] == 'zloop'
        codex = json.loads((PLUGIN / '.codex-plugin/plugin.json').read_text())
        assert portable['version'] == claude['version'] == codex['version']
        assert codex['skills'] == './skills/'
        assert codex['hooks'] == './hosts/codex/hooks.json'
        assert portable['extensions']['com.openai']['hooks'] == './hosts/codex/hooks.json'
        # Codex 0.162 reads a root manifest declaring the Agent Plugins $schema as that format and drops
        # .codex-plugin hooks, so a copied install would register none.
        assert '$schema' not in portable, 'root plugin.json must not declare $schema'
        # Three invocation policies: commands only the user types, a skill only the agent loads by its trigger, and
        # skills both can start.
        commands = {'align', 'refine', 'dispatch', 'review', 'pr', 'autopilot', 'remediate', 'setup', 'brief', 'huh'}
        implicit_skills = {'housekeeping'}
        both = {'diagnose', 'research', 'prototype', 'explain', 'tdd'}
        assert {path.parent.name for path in (PLUGIN / 'skills').glob('*/SKILL.md')} == commands | implicit_skills | both
        for path in (PLUGIN / 'skills').glob('*/SKILL.md'):
            name = path.parent.name
            implicit = name in implicit_skills | both
            text = path.read_text()
            # Every skill holds its procedure in SKILL.md; none is a thin entry over a lower doc.
            # huh is a short re-pitch trigger that launches nothing (tests/test_huh.py).
            if name != 'huh':
                assert len(text.encode()) >= 2500, f'{path}: a skill holds its procedure'
            first_read = re.search(r'^First read:.*?(?:\n\n|\Z)', text, re.M | re.S).group(0)
            if name in {'huh', 'explain'}:
                # They launch nothing, so they skip the bootstrap the session hook points to.
                assert 'Skip the runtime bootstrap and the host session part' in ' '.join(first_read.split()), path
            elif name not in implicit_skills:
                assert first_read.startswith('First read: the runtime bootstrap (`../../bootstrap.md`), '
                                             'the host session part'), path
                assert 'skip' not in first_read.lower(), path
            if name in commands:
                assert 'disable-model-invocation: true' in text, path
                assert 'user-invocable: false' not in text, path
            elif name in both:
                assert 'disable-model-invocation: true' not in text, f'{path}: the agent may load it by its trigger'
                assert 'user-invocable: false' not in text, f'{path}: the user may type it'
            else:
                assert 'disable-model-invocation: true' not in text, path
                assert 'user-invocable: false' in text, f'{path}: only the commands appear in the typed menu'
            ui = (path.parent / 'agents/openai.yaml').read_text()
            assert f'policy:\n  allow_implicit_invocation: {str(implicit).lower()}\n' in ui, path
        # Agent and job structure is checked by agent_defs (tests/test_agent_definitions.py).
        for folder in ('skills', 'agents', 'standards', 'runtime', 'hosts'):
            for path in (PLUGIN / folder).rglob('*.md'):
                for link in re.findall(r'\]\(([^)]+)\)', path.read_text()):
                    if '://' not in link and '<' not in link:
                        assert (path.parent / link.split('#')[0]).exists(), (path, link)
        for harness, relative in (('claude', 'hooks/hooks.json'), ('codex', 'hosts/codex/hooks.json')):
            config = json.loads((PLUGIN / relative).read_text())['hooks']
            assert set(config) == {'SessionStart', 'PreToolUse', 'Stop'}, (harness, config)
            assert config['Stop'][0]['hooks'][0]['command'].endswith(f'hooks/yield_gate.py" {harness}'), config
            guards = {entry['matcher']: [hook['command'] for hook in entry['hooks']] for entry in config['PreToolUse']}
            # Both hosts log each Herdr launch before Bash runs it, so the yield gate can name unregistered workers;
            # Codex shows a hook's statusMessage on every matching call, so the launch hook has none.
            launch = [hook for entry in config['PreToolUse'] if entry['matcher'] == 'Bash' for hook in entry['hooks']
                      if hook['command'].endswith('hooks/herdr_launch.py"')]
            assert len(launch) == 1 and 'statusMessage' not in launch[0], guards
            if harness == 'claude':
                guard = config['PreToolUse'][0]
                assert guard['matcher'] == 'Agent|Task' and guard['hooks'][0]['command'].endswith('hooks/agent_model.py"')
                assert guards['Bash'][0].endswith('hooks/pr_body.py"'), guards
            hook = config['SessionStart'][0]['hooks'][0]
            command = hook['command'].replace('${CLAUDE_PLUGIN_ROOT}', str(PLUGIN)).replace('${PLUGIN_ROOT}', str(PLUGIN))
            import shlex
            result = run(*shlex.split(command), input='{}')
            context = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
            assert f'harness: {harness}' in context
            assert str(PLUGIN / 'bootstrap.md') in context
            assert str(PLUGIN / 'hosts' / harness / 'session.md') in context
            assert (PLUGIN / 'bootstrap.md').is_file() and (PLUGIN / 'hosts' / harness / 'session.md').is_file()
            other = 'codex' if harness == 'claude' else 'claude'
            assert f'/hosts/{other}/' not in context
            for herdr in ('', '0', '1'):
                result = subprocess.run(shlex.split(command), input='{}', text=True,
                                        capture_output=True, check=True,
                                        env=dict(os.environ, HERDR_ENV=herdr))
                context = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
                assert (str(PLUGIN / 'hosts/herdr.md') in context) == (herdr == '1')
                assert str(PLUGIN / 'bootstrap.md') in context
                assert str(PLUGIN / 'hosts' / harness / 'session.md') in context
                assert f'/hosts/{other}/' not in context

    def test_python_scripts_import_by_name_and_named_scripts_exist(self):
        import re
        for path in [*(PLUGIN / 'scripts').glob('*.py'), *(PLUGIN / 'hooks').glob('*.py')]:
            assert path.stem.isidentifier(), f'{path.name}: Python scripts are named as importable modules'
        tracked = run('git', '-C', str(PLUGIN), 'ls-files', '-z').stdout.split('\0')
        for name in filter(None, tracked):
            path = PLUGIN / name
            if name.startswith('tests/'):
                continue  # Fixtures quote recorded commands, including names from before a rename.
            if path.suffix not in {'.md', '.py', '.sh', '.json', '.yaml', '.mjs', '.ts'} or not path.is_file():
                continue
            for folder, script in re.findall(r'\b(scripts|hooks)/([A-Za-z0-9_.-]+\.(?:py|sh))\b', path.read_text()):
                assert (PLUGIN / folder / script).is_file(), (name, folder, script)

    def test_every_named_command_is_a_skill_that_exists(self):
        import re
        skills = {path.parent.name for path in (PLUGIN / 'skills').glob('*/SKILL.md')}
        commands = {path.parent.name for path in (PLUGIN / 'skills').glob('*/SKILL.md')
                    if 'user-invocable: false' not in path.read_text()}
        tracked = run('git', '-C', str(PLUGIN), 'ls-files', '-z').stdout.split('\0')
        for name in filter(None, tracked):
            path = PLUGIN / name
            if name.startswith(('tests/', 'journals/')) or not path.is_file() or path.is_symlink():
                continue
            if path.suffix not in {'.md', '.py', '.json', '.yaml', '.mjs', '.ts'}:
                continue
            text = path.read_text()
            # Codex lists every skill; the typed Claude and Pi forms name only the skills the user can type.
            for command in re.findall(r'\$zloop:([a-z][a-z0-9-]*)', text):
                assert command in skills, (name, command)
            for command in re.findall(r'(?:/zloop:|/skill:)([a-z][a-z0-9-]*)', text):
                assert command in commands, (name, command)

    def test_agent_docs_pass_the_doc_checker(self):
        # Agents read a linked doc whole, with no way to jump to an anchor; the checker
        # (project skill writing-agent-docs) enforces size, contents, links, anchors,
        # orphans and doc paths named in code. Known debt lives in its config.json.
        checker = PLUGIN / '.agents/skills/writing-agent-docs/check_docs.py'
        result = run(sys.executable, str(checker), '--root', str(PLUGIN), ok=False)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_oversized_selected_work_is_split_by_delivery_and_children_inherit_the_selection(self):
        split = (PLUGIN / 'skills/dispatch/split-work.md').read_text()
        assert '](../../runtime/board/slicing.md' in split and 'too large for one worker' in split
        assert '](split-work.md' in (PLUGIN / 'skills/dispatch/SKILL.md').read_text()
        scope = (PLUGIN / 'runtime/board/scope.md').read_text()
        assert "inherit the parent's selection" in scope and 'Keep the parent outstanding' in scope

    def test_remediate_skill_and_its_kinds(self):
        import re
        path = PLUGIN / 'skills/remediate/SKILL.md'
        entry = path.read_text()
        source = (PLUGIN / 'skills/remediate/SKILL.md').read_text()
        metadata = entry.split('---', 2)[1]
        assert re.search(r'^name: remediate$', metadata, re.M)
        assert re.search(r'^disable-model-invocation: true$', metadata, re.M)
        hint = re.search(r'^argument-hint: (.+)$', metadata, re.M).group(1)
        for value in ('tautological-tests', 'misleading-comments', '--base <ref>',
                      '--head <ref>', '<path>...', '--project'):
            assert value in hint, (value, hint)
        assert '[scope]' not in hint, hint
        for value in ('--base', '--head', '--project'):
            assert f'`{value}' in source, value
        ui = (path.parent / 'agents/openai.yaml').read_text()
        assert 'allow_implicit_invocation: false' in ui
        assert '$zloop:remediate' in ui
        for host in ('claude', 'codex', 'pi', 'opencode'):
            assert (PLUGIN / 'hosts' / host / 'session.md').is_file()
            assert (PLUGIN / 'hosts' / host / 'workers.md').is_file()
        assert json.loads((PLUGIN / '.codex-plugin/plugin.json').read_text())['skills'] == './skills/'
        for kind in ('tautological-tests', 'misleading-comments'):
            definition = PLUGIN / 'standards/kinds' / f'{kind}.md'
            assert definition.is_file()
            assert f'standards/kinds/{kind}.md' in source

    def test_remediate_proof_experiments_follow_the_kind(self):
        import re
        skill = (PLUGIN / 'skills/remediate/SKILL.md').read_text()
        prove = re.search(r'^2\. \*\*Prove\.\*\*(.+?)^3\. ', skill, re.M | re.S).group(1)
        prove = ' '.join(prove.split())
        assert "only where the kind's Proof calls for them" in prove, prove
        assert "Send disputed or UNVERIFIED findings to a reviewer's [reproduce]" in prove, prove
        assert 'never to the worker that raised them' in prove, prove
        assert 'needs no further run' in prove, prove
        kind = (PLUGIN / 'standards/kinds/tautological-tests.md').read_text()
        proof = ' '.join(re.search(r'^## Proof$(.+?)^## Repair$', kind, re.M | re.S).group(1).split())
        assert 'Confirming a finding changes no code and runs no test' in proof, proof
        job = ' '.join((PLUGIN / 'skills/review/jobs/reproduce.md').read_text().split())
        assert 'When the assigned proof steps are reading-only, edit and run nothing' in job, job

    def test_both_callers_of_the_tautological_kind_still_require_a_reproduce_job(self):
        review = ' '.join((PLUGIN / 'skills/review/jobs/diff-review.md').read_text().split())
        assert 'A flag you would dismiss is disputed: send it to a [reproduce](reproduce.md) job' in review, review
        remediate = ' '.join((PLUGIN / 'skills/remediate/SKILL.md').read_text().split())
        assert "Send disputed or UNVERIFIED findings to a reviewer's [reproduce](../review/jobs/reproduce.md)" in remediate

    def test_remediate_detection_covers_every_test(self):
        import re
        kind = (PLUGIN / 'standards/kinds/tautological-tests.md').read_text()
        proof = ' '.join(re.search(r'^## Proof$(.+?)^## Repair$', kind, re.M | re.S).group(1).split())
        assert 'every test in scope, not only the ones you suspect' in proof, proof
        assert '`protected` clears a test only when its checklist raised no doubt' in proof, proof
        assert 'a generated or default value is not an observation' in proof, proof
        assert '`unknown` only when the code does not settle it' in proof, proof
        assert 'A flag you would dismiss is disputed and needs independent verification' in proof, proof
        routing = ' '.join((PLUGIN / 'runtime/decisions/test-shape.md').read_text().split())
        assert 'A flagged test is not resubmitted' in routing, routing
        skill = (PLUGIN / 'skills/remediate/SKILL.md').read_text()
        detect = ' '.join(re.search(r'^1\. \*\*Detect read-only\.\*\*(.+?)^2\. ', skill, re.M | re.S).group(1).split())
        assert "run them for every test in scope during detection, before selecting candidates" in detect, detect
        assert "For a paths or whole-project scan, reconcile the inventory against the workers' coverage item by item" in detect, detect
        assert 'cover every file and item no worker accounted for' in detect, detect

    def test_resource_lock_excludes_releases_and_keeps_exit_status(self):
        with tempfile.TemporaryDirectory(prefix='zloop mutex ') as tmp:
            env = dict(os.environ, RESOURCE_LOCK=str(Path(tmp) / 'resource.lock'), RESOURCE_LOCK_TIMEOUT='0')
            wrapper = str(PLUGIN / 'scripts/resource_lock.py')
            # The exec'd build holds the descriptor: a contender must fail until it exits.
            holder = subprocess.Popen([sys.executable, wrapper, sys.executable, '-c',
                                       'import time; print("locked", flush=True); time.sleep(0.5)'],
                                      env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                assert holder.stdout.readline().strip() == 'locked'
                contender = subprocess.run([sys.executable, wrapper, 'true'], env=env, capture_output=True, text=True)
                assert contender.returncode != 0 and 'still held' in contender.stderr
                assert holder.wait(timeout=5) == 0
                done = subprocess.run([sys.executable, wrapper, 'true'], env=env, capture_output=True, text=True)
                assert done.returncode == 0, done.stderr
                failed = subprocess.run([sys.executable, wrapper, 'sh', '-c', 'exit 7'], env=env, capture_output=True)
                assert failed.returncode == 7
                pipe = subprocess.run([sys.executable, wrapper, 'bash', '-c', 'yes | head -n 1 >/dev/null; echo "${PIPESTATUS[0]}"'],
                                      env=env, capture_output=True, text=True)
                assert pipe.stdout.strip() == '141', pipe  # the command gets default SIGPIPE, as from a shell
                blank = subprocess.run([sys.executable, wrapper, 'true'], capture_output=True, text=True,
                                       env=dict(env, RESOURCE_LOCK=str(Path(tmp) / 'other.lock'), RESOURCE_LOCK_TIMEOUT=''))
                assert blank.returncode == 0, blank.stderr  # an empty timeout means the default
                missing = subprocess.run([sys.executable, wrapper, 'zloop-no-such-command'], env=env,
                                         capture_output=True, text=True)
                assert missing.returncode != 0 and 'not found' in missing.stderr, missing.stderr
            finally:
                if holder.poll() is None:
                    holder.kill()
                    holder.wait()


class NativeHarnesses(unittest.TestCase):
    """Installed Claude, Codex, Pi and OpenCode package readers, without model calls."""

    @unittest.skipUnless(os.environ.get('ZLOOP_NATIVE') == '1', 'set ZLOOP_NATIVE=1 to check installed CLIs')
    def test_claude_and_codex_read_the_package(self):
        import selectors
        import time
        run('claude', 'plugin', 'validate', '--strict', str(PLUGIN))
        inventory = run('claude', '--plugin-dir', str(PLUGIN), 'plugin', 'details', 'zloop').stdout
        skill_count = len(list((PLUGIN / 'skills').glob('*/SKILL.md')))
        hook_events = len(json.loads((PLUGIN / 'hooks/hooks.json').read_text())['hooks'])
        for expected in (f'Skills ({skill_count})', 'Agents (3)', f'Hooks ({hook_events})'):
            assert expected in inventory, inventory
        # Source inventory does not resolve installed-plugin dependencies.
        installed = json.loads(run('claude', 'plugin', 'list', '--json').stdout)
        current = [entry for entry in installed
                   if entry['id'].startswith('zloop@')
                   and Path(entry['installPath']).resolve() == PLUGIN]
        for entry in current:
            assert entry.get('enabled') and not entry.get('errors'), entry
        if current:
            print('Claude installed development plugin: enabled, no loading errors')
        else:
            print('Claude installed development plugin: not present; source inventory only')
        server = subprocess.Popen(['codex', 'app-server', '--stdio'], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        selector = selectors.DefaultSelector()
        selector.register(server.stdout, selectors.EVENT_READ)
        def request(identifier, method, params):
            server.stdin.write(json.dumps({'id': identifier, 'method': method, 'params': params}) + '\n')
            server.stdin.flush()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if selector.select(timeout=1):
                    line = server.stdout.readline()
                    assert line, 'Codex server exited before responding'
                    response = json.loads(line)
                    if response.get('id') == identifier:
                        assert 'error' not in response, response
                        return response['result']
            raise AssertionError(f'Codex timed out: {method}')
        try:
            request(1, 'initialize', {'clientInfo': {'name': 'zloop-validation', 'version': '0.3.0'},
                                      'capabilities': {'experimentalApi': True}})
            plugin = request(2, 'plugin/read', {'marketplacePath': str(PLUGIN / '.agents/plugins/marketplace.json'),
                                               'pluginName': 'zloop'})['plugin']
            assert {s['name'] for s in plugin['skills']} == {f'zloop:{p.name}' for p in (PLUGIN / 'skills').iterdir()}
            assert all(Path(s['path']).is_relative_to(PLUGIN / 'skills') for s in plugin['skills'])
            for skill in plugin['skills']:
                interface = skill.get('interface') or {}
                assert 25 <= len(interface.get('shortDescription') or '') <= 64, skill['name']
                assert f"${skill['name']}" in (interface.get('defaultPrompt') or ''), skill['name']
            assert plugin['summary']['localVersion'] == json.loads((PLUGIN / 'plugin.json').read_text())['version']
            print(f'native readers: Claude {skill_count} skills / 5 agents / {hook_events} hook events; Codex {skill_count} shared skills')
        finally:
            selector.close()
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
            server.stdin.close()
            server.stdout.close()

    @unittest.skipUnless(os.environ.get('ZLOOP_NATIVE') == '1', 'set ZLOOP_NATIVE=1 to check installed CLIs')
    def test_opencode_discovers_skills_and_their_invocation_policy(self):
        import re
        import shutil
        import socket
        import time
        executable = shutil.which('opencode')
        if not executable:
            self.skipTest('the opencode CLI is not installed')
        with tempfile.TemporaryDirectory(prefix='zloop native skills ') as tmp:
            root = Path(tmp)
            env = dict(os.environ)
            for key, directory in [('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'),
                                   ('XDG_DATA_HOME', 'data'), ('XDG_STATE_HOME', 'state')]:
                env[key] = str(root / directory)
                Path(env[key]).mkdir()
            link = Path(env['XDG_CONFIG_HOME']) / 'opencode/skills/zloop'
            link.parent.mkdir(parents=True)
            link.symlink_to(PLUGIN / 'skills', target_is_directory=True)  # the documented development install

            def opencode(*args):
                return subprocess.run([executable, *args], cwd=root, env=env, capture_output=True,
                                      text=True, timeout=60)

            def skill_list():
                # A piped response is cut short once it is large, so let the CLI write a file.
                listing = root / 'skills.json'
                with listing.open('w') as output:
                    result = subprocess.run([executable, 'api', 'skill.list'], cwd=root, env=env,
                                            stdout=output, stderr=subprocess.PIPE, text=True, timeout=60)
                assert result.returncode == 0, result.stderr
                return json.loads(listing.read_text())['data']

            with socket.socket() as probe:  # the service's fixed port is held by the user's own service
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            assert opencode('service', 'set', 'port', str(port)).returncode == 0
            try:
                expected = {path.name for path in (PLUGIN / 'skills').iterdir() if (path / 'SKILL.md').exists()}
                # A location's first request can answer before it finishes loading its skill sources.
                for _ in range(30):
                    found = {entry['id']: entry for entry in skill_list()
                             if Path(entry.get('path', '')).resolve().is_relative_to((PLUGIN / 'skills').resolve())}
                    if found.keys() >= expected:
                        break
                    time.sleep(0.5)
                assert set(found) == expected, (sorted(found), sorted(expected))
                policies = {}
                for path in (PLUGIN / 'skills').glob('*/SKILL.md'):
                    metadata = path.read_text().split('---', 2)[1]
                    disabled = re.search(r'^disable-model-invocation:\s*true\s*$', metadata, re.M) is not None
                    policies[path.parent.name] = not disabled
                    if disabled:
                        assert found[path.parent.name].get('autoinvoke') is False, path
                    else:
                        assert found[path.parent.name].get('autoinvoke') is not False, path
                print(f'OpenCode native skills: {len(found)} registered, '
                      f'{sum(policies.values())} model-invocable')
            finally:
                opencode('service', 'stop')

    @unittest.skipUnless(os.environ.get('ZLOOP_NATIVE_PI') == '1', 'set ZLOOP_NATIVE_PI=1 to check the installed Pi SDK')
    def test_pi_sdk_discovers_and_bootstraps(self):
        import shutil
        executable = shutil.which('pi')
        assert executable, 'Pi must be installed for ZLOOP_NATIVE_PI=1'
        cli = Path(executable).resolve()
        if cli.name == 'mise':  # A mise shim resolves to mise itself; ask it for the real CLI.
            cli = Path(run('mise', 'which', 'pi').stdout.strip()).resolve()
        managed = cli.parent.parent / 'install'
        if (managed / 'current-version').is_file():  # Pi's managed launcher runs the release current-version names.
            release = managed / 'releases' / (managed / 'current-version').read_text().strip()
            cli = (release / 'node_modules/.bin/pi').resolve()
        package = next((parent for parent in cli.parents
                        if (parent / 'package.json').is_file()
                        and json.loads((parent / 'package.json').read_text()).get('name', '').endswith('/pi-coding-agent')), None)
        assert package, 'Cannot locate the installed Pi SDK from its CLI'
        print(run('node', str(PLUGIN / 'tests/test_pi_adapter.mjs'),
                  str(package / 'dist/index.js')).stdout.strip())


if __name__ == '__main__':
    unittest.main()
