"""Shared test helpers: plugin paths, script loading and machine-state isolation."""
import importlib.util
import os
import re
import shlex
import sqlite3
from pathlib import Path
import sys
from native_records import assistant_turn, session_record, user_message

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'scripts'
HOOKS = ROOT / 'hooks'
# The word the glossary retires for the host. Besides the glossary, only this file and tests/test_glossary.py spell it,
# so the repository-wide check can refuse it everywhere else.
RETIRED_HOST_WORD = 'harness'
# Scripts import their siblings by module name, as they do when run from scripts/.
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import worker_lifecycle


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


def prose(path):
    """A doc's text with runs of whitespace flattened; `path` is absolute or relative to the plugin root."""
    return ' '.join((ROOT / path).read_text().split())


def without_host_sessions(env=None):
    """A copy of env (default: the current environment) with no host session ID, as in a shell no host started."""
    return {key: value for key, value in (os.environ if env is None else env).items()
            if key not in worker_lifecycle.SESSION_VARIABLES}


def ticket_file(repo, name, status='ready', *, blocked_by='', touches=None, devices='', worker=None, title=None,
                extra='', blocker=None, accepted=True, path=None):
    """A local ticket `name` ('slug/NN') under repo's `.scratch/slug/issues/` with the front matter the board reads,
    touching an area of its own number unless `touches` names one; a `worker:` line only when `worker` is given and a
    body only when `title` is. A done one carries the acceptance fields `--accept` writes, unless `accepted` is False.
    `extra` is raw front-matter lines (each ending in a newline); `path` names the file instead of the numbered one
    (repo may then be None)."""
    slug, number = name.split('/')
    path = Path(path or Path(repo) / '.scratch' / slug / 'issues' / f'{number}-task.md')
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ['---', f'ticket: {name}', f'status: {status}', f'blocked_by: [{blocked_by}]',
             f"touches: [{touches or 'area-' + number}]", f'devices: [{devices}]']
    if worker is not None:
        lines.append(f'worker: {worker}'.rstrip())
    if blocker:
        lines.append(f'blocker: {blocker}')
    if status == 'done' and accepted:
        lines += [f"accepted: {'a' * 40}", f"accepted_report: {'b' * 64}"]
    body = f'# {number}: {title}\n\n**Story:** a story.\n' if title else ''
    path.write_text('\n'.join(lines) + '\n' + extra + '---\n' + body)
    return path


def run_card(path, slug=None, *, confirmed='2026-10-27 "go"', session='host-1', terminal='', evidence='',
             phase='dispatch', board='local', **extra):
    """A run card at `path` with the State section the panel, the stop hook and reap read; `board:` only when given
    (None leaves it out) and `extra` fields after the standard ones."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = dict(confirmed=confirmed, session=session, **{'auto-merge': 'no'}, **({'board': board} if board else {}),
                  phase=phase, terminal=terminal, history='', evidence=evidence, timer='',
                  resume='`autopilot --take-over`', **extra)
    path.write_text(f'# {slug or path.parent.name} \u2014 autopilot run card\n\n## Goal\nx\n\n## State\n'
                    + ''.join(f'{key}: {value}\n'.replace(': \n', ':\n') for key, value in fields.items()))
    return path


def attempt_row(repo, **fields):
    """An attempt row of a live Claude worker of repo, as the lifecycle records one; `fields` replace or add."""
    return dict(dict(attempt='ticket-1/1', assignment='ticket-1', repo=str(repo), runtime='claude',
                     native_session='n-1', worker='w-1', owned=True), **fields)


def write_attempts(state, rows, events=True):
    """A run state at `state` holding rows (a `_quiet_seconds` key, default 30, sets how long ago the row's register
    event was); with `events` False the state has no events table at all."""
    import json
    import time
    state = Path(state)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.unlink(missing_ok=True)
    db = sqlite3.connect(state)
    db.execute('CREATE TABLE attempts (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    if events:
        db.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, attempt TEXT NOT NULL, kind TEXT NOT NULL, '
                   'payload TEXT NOT NULL, acknowledged INTEGER NOT NULL)')
    for row in rows:
        quiet = row.pop('_quiet_seconds', 30)
        db.execute('INSERT INTO attempts VALUES (?, ?)', (row['attempt'], json.dumps(row)))
        if events:
            db.execute('INSERT INTO events (attempt, kind, payload, acknowledged) VALUES (?, ?, ?, ?)',
                       (row['attempt'], 'register', json.dumps({'recorded_at': time.time() - quiet, 'attempt': row['attempt']}), 1))
    db.commit()
    db.close()
    return state


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


def stub_router_plugin(root, exit_status, refusal=''):
    """A copy of the plugin (but its tests and checkout state) under `root` whose `.venv/bin/python` is a stub router: it logs its arguments to
    `root/stub.log`, prints `refusal` to stderr and exits `exit_status`. Returns the plugin copy's path."""
    import shutil
    plugin = Path(root) / 'plugin'
    shutil.copytree(ROOT, plugin, ignore=shutil.ignore_patterns('__pycache__', '.git', '.worktrees', '.scratch', '.venv',
                                                                  'tests', 'node_modules', 'benchmarks'))
    stub = plugin / '.venv/bin/python'
    stub.parent.mkdir(parents=True)
    stub.write_text(f'#!/bin/sh\necho "$@" >> {shlex.quote(str(Path(root) / "stub.log"))}\necho {shlex.quote(refusal)} >&2\nexit {exit_status}\n')
    stub.chmod(0o755)
    return plugin


def assignment_file(root, job, text='', *, attempt='ticket-1/1'):
    """An assignment for job that names every input the resolver requires, followed by text."""
    import agent_defs
    definition = agent_defs.load()['jobs'][job]
    # The ticket is a tracker ID, not a path: an absolute path to a missing ticket file is refused at collection.
    # A ticket assignment needs no capability.
    lines = [f'{name}: ' + {'ticket': 'fx/01', 'needs': '[]'}.get(name, f'/fixture/{name.replace(" ", "-")}')
             for name in (*agent_defs.COMMON_INPUTS, *definition['inputs'])]
    path = Path(root) / f'{job}-assignment.txt'
    path.write_text(f'attempt: {attempt}\n' + '\n'.join(lines) + '\n' + text)
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
        done = subprocess.run([sys.executable, '-B', str(script('worker_lifecycle.py')), 'record', '--state', str(state),
                               '--input', str(event)], check=True, capture_output=True, text=True, env=env)
        return json.loads(done.stdout)

    record('register', assignment=attempt.rpartition('/')[0] or attempt, repo=str(repo), worktree=str(repo),
           head=head, execution=str(execution), runtime='claude', worker=f'pane-{attempt}',
           native_session=f'native-{attempt}', owned=True, packet=str(assignment_file(work, job, attempt=attempt)))
    native = session_record(work / 'native.jsonl', 'claude', f'native-{attempt}', repo)
    record('startup', native=str(native))
    record('submitted', evidence=['native.jsonl:2:accepted prompt'])
    user_message(native, 'claude', f'assignment: {attempt.rpartition("/")[0] or attempt}  attempt: {attempt}')
    assistant_turn(native, 'claude')
    result = record('result', status=status, head=head, report=str(report), native=str(native), evidence=['checks.log'])
    # Collection acknowledges the result receipt; a record that left it would be a yield gap of its own.
    subprocess.run([sys.executable, '-B', str(script('worker_lifecycle.py')), 'ack', '--state', str(state),
                    '--event-id', str(result['event_id'])], check=True, capture_output=True, text=True, env=env)
    return state
