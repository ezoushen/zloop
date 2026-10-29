"""Shared test helpers: plugin paths, script loading and machine-state isolation."""
import importlib.util
import os
import re
from pathlib import Path
import sys
from native_records import assistant_turn, session_record

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'scripts'
HOOKS = ROOT / 'hooks'
# The word the glossary retires for the host. Besides the glossary, only this file and tests/test_glossary.py spell it,
# so the repository-wide check can refuse it everywhere else.
RETIRED_HOST_WORD = 'harness'
# Scripts import their siblings by module name, as they do when run from scripts/.
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def script(filename):
    """Path of a script in scripts/."""
    return SCRIPTS / filename


def load_script(filename, name=None):
    """Import a fresh copy of a script from scripts/ as a module."""
    path = SCRIPTS / filename
    spec = importlib.util.spec_from_file_location(name or path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wait_until(condition, what, limit=120):
    """Poll condition until it holds, failing with `what` only after limit seconds, a guard against a hang that
    load alone does not reach."""
    import time
    deadline = time.monotonic() + limit
    while not condition():
        assert time.monotonic() < deadline, f'never happened: {what}'
        time.sleep(0.02)


def isolated_env(root, **values):
    """The current environment with zloop state, config and Codex home under root."""
    root = Path(root)
    return dict(os.environ, XDG_STATE_HOME=str(root / 'state'), XDG_CONFIG_HOME=str(root / 'config'),
                CODEX_HOME=str(root / 'codex'), **values)


def assignment_file(root, job, text=''):
    """An assignment for job that names every input the resolver requires, followed by text."""
    import agent_defs
    definition = agent_defs.load()['jobs'][job]
    # The ticket is a tracker ID, not a path: an absolute path to a missing ticket file is refused at collection.
    # A ticket assignment needs no capability.
    lines = [f'{name}: ' + {'ticket': 'fx/01', 'needs': '[]'}.get(name, f'/fixture/{name.replace(" ", "-")}')
             for name in (*agent_defs.COMMON_INPUTS, *definition['inputs'])]
    path = Path(root) / f'{job}-assignment.txt'
    path.write_text('\n'.join(lines) + '\n' + text)
    return path


def code_samples(text):
    """Code a prose-only doc must not contain: code spans, fences, dotted identifiers, and any line indented four
    or more spaces after a blank line, which such a doc never needs since each list item keeps to one paragraph."""
    unquoted = '\n'.join(re.sub(r'^ {0,3}(?:> ?)+', '', line).rstrip() for line in text.expandtabs(4).splitlines())
    prose = re.sub(r'\]\([^)]*\)', ']', unquoted)
    return (re.findall(r'`[^`\n]+`', prose) + re.findall(r'(?m)^[ \t]*(?:```|~~~).*$', prose)
            + re.findall(r'(?m)(?<=\n\n) {4,}\S.*$', '\n\n' + prose)
            + [name for name in re.findall(r'\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+', prose)
               if not name.endswith('.md') and max(map(len, name.split('.'))) > 1])


def collected_attempt(repo, slug, attempt, report, job='ticket', head='head-1', status='completed'):
    """A worker attempt of a workstream's lifecycle that returned report and was collected, driven through the
    lifecycle CLI: registered, started with the settings it was given, prompted, then its result recorded."""
    import json
    import subprocess
    import sys
    import agent_defs
    state = Path(repo) / '.scratch' / slug / 'reports' / 'workers.sqlite'
    state.parent.mkdir(parents=True, exist_ok=True)
    work = state.parent / 'fixture' / attempt.replace('/', '-')
    work.mkdir(parents=True, exist_ok=True)
    execution = work / 'execution.json'
    execution.write_text(json.dumps({'host': 'claude', 'native_dispatch': True, 'agent': agent_defs.load()['jobs'][job]['agent'], 'job': job,
                                     'settings': {'model': 'sonnet', 'effort': 'high'}}))
    env = isolated_env(work)

    def record(kind, **fields):
        event = work / 'event.json'
        event.write_text(json.dumps(dict(kind=kind, attempt=attempt, **fields)))
        subprocess.run([sys.executable, '-B', str(script('worker_lifecycle.py')), 'record', '--state', str(state),
                        '--input', str(event)], check=True, capture_output=True, text=True, env=env)

    record('register', assignment=attempt.rpartition('/')[0] or attempt, repo=str(repo), worktree=str(repo),
           head=head, execution=str(execution), runtime='claude', worker=f'pane-{attempt}',
           native_session=f'native-{attempt}', owned=True, packet=str(assignment_file(work, job)))
    native = session_record(work / 'native.jsonl', 'claude', f'native-{attempt}', repo)
    record('startup', native=str(native))
    record('submitted', evidence=['native.jsonl:2:accepted prompt'])
    assistant_turn(native, 'claude')
    record('result', status=status, head=head, report=str(report), native=str(native), evidence=['checks.log'])
    return state
