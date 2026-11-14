#!/usr/bin/env python3
"""PreToolUse hook: log each `herdr agent start` a session runs, so the yield gate can name unregistered workers.

It finds launches behind assignments, wrappers (`timeout`, `env` and their options) and a shell's `-c` string, not
inside heredocs or backticks. Text containing HERDR_SOCKET leaves the server and cwd unknown, including longer
variable names.

Appends one JSON line per launch to the main checkout's .scratch/herdr-launches.jsonl: the time, agent name and
pane; server (`--session`, caller's HERDR_SOCKET_PATH, else `default`; omitted when unknown); orchestrator
(HERDR_PANE_ID and host session ID); and cwd when Herdr reports it.
Claude and Codex run it before Bash; Pi's extension passes its bash and bg_run calls. It never blocks the command.

`herdr_launch.py start --route <saved route.json> --worktree <path> --assignment <slug>/<assignment>
--role <role> --name <name>` creates a worker tab and starts the saved recipe without a shell.
OpenCode API routes attach the TUI to a detached, pane-bound private server; the response adds its URL,
session ID and password-file path under `opencode`. Pane disappearance or keeper signals remove its private files.
It logs the launch before starting the agent, and returns Herdr's exit code and JSON response.

`herdr_launch.py dismiss --pane <pane> | --name <agent> [--session <host session>] --reason <text> | --keep <text>`,
run in the checkout, appends a dismissal by this orchestrator (HERDR_PANE_ID and the session given) that accounts for
its own launches of that pane or agent name logged before it, such as one that failed or a probe. Such a pane left open
holds the stop until it is closed, unless dismissed with `--keep`, which records why it stays open on purpose.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pr_body
import yield_gate

SHELLS = {'sh', 'bash', 'zsh', 'dash'}
# All Herdr lookups of one hook run, well inside the hosts' 10-second hook timeout.
LOOKUP_SECONDS = 4
# Seconds of failed pane lookups after which an OpenCode keeper takes Herdr itself for gone and stops its server.
PANE_GRACE = 60
# Options of `env` and `timeout` that take a separate value: env -u/--unset NAME, -C/--chdir DIR, -P PATH, -S STRING;
# timeout -s SIGNAL, -k DURATION.
VALUED = {'-u', '--unset', '-C', '--chdir', '-P', '-S', '-s', '-k'}


def runs(words):
    """The words of the command a simple command runs, past assignments and wrappers with their options."""
    while words:
        head = words[0]
        if pr_body.ASSIGNMENT.match(head) or head in pr_body.WRAPPERS - {'env'}:
            words = words[1:]
        elif head in ('env', 'timeout'):
            words = words[1:]
            while words and words[0].startswith('-'):
                words = words[2:] if words[0] in VALUED else words[1:]
            if head == 'timeout':
                words = words[1:]  # The duration.
        else:
            break
    return words


def starts(command):
    """Yield (Herdr session or None, agent name, pane) for each supported `herdr [--session S] agent start`.
    Socket overrides are not interpreted; the logger leaves their server unknown."""
    for tokens, _ in pr_body.logical_lines(command):
        start = 0
        for index, token in enumerate(tokens + [';']):
            if token not in pr_body.SEPARATORS and index < len(tokens):
                continue
            words, start = runs(tokens[start:index]), index + 1
            if words and Path(words[0]).name in SHELLS:
                for at, word in enumerate(words[1:], 1):  # The string a shell's `-c` runs.
                    if not word.startswith('-'):
                        break
                    if re.fullmatch(r'-[A-Za-z]*c[A-Za-z]*', word) and at + 1 < len(words):
                        yield from starts(words[at + 1])
                        break
                continue
            if not words or Path(words[0]).name != 'herdr':
                continue
            session, words = None, words[1:]
            if words[:1] == ['--session'] and len(words) > 1:
                session, words = words[1], words[2:]
            elif words[:1] and words[0].startswith('--session='):
                session, words = words[0].split('=', 1)[1], words[1:]
            if words[:2] != ['agent', 'start']:
                continue
            words = words[2:words.index('--')] if '--' in words else words[2:]
            names, options, at = [], {}, 0
            while at < len(words):
                flag, joined, value = words[at].partition('=')
                if flag.startswith('--'):
                    if not joined:
                        value, at = (words[at + 1] if at + 1 < len(words) else ''), at + 1
                    options[flag] = value
                else:
                    names.append(words[at])
                at += 1
            # Herdr refuses a start without a name or pane, and --help only prints the usage: none starts an agent. A
            # name, pane or server the shell expands is no real one: the live agent it starts is found by the stop check's scan.
            if (names and options.get('--pane') and '--help' not in options and '-h' not in names
                    and not any(char in value for value in (session or '', names[0], options['--pane'])
                                for char in '$`')):
                yield session, names[0], options['--pane']


def pane_cwd(session, pane, deadline):
    """The pane's working directory as its Herdr server reports it before the deadline, or None."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    try:
        code, response = yield_gate.lifecycle().herdr(session, 'pane', 'get', pane, timeout=remaining)
        return response['result']['pane'].get('cwd') if code == 0 else None
    except Exception:
        return None  # An unknown cwd still counts at stop.


def log_launch(root, session, agent, pane, orchestrator, cwd=None):
    """Append a launch in the stop check's shared format, before its pane can disappear."""
    log = root / yield_gate.LAUNCHES
    log.parent.mkdir(parents=True, exist_ok=True)
    record = {'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'agent': agent, 'pane': pane,
              'orchestrator': orchestrator}
    if session is not None:
        record['herdr_session'] = session
    if cwd:
        record['cwd'] = cwd
    with log.open('a') as stream:
        stream.write(json.dumps(record) + '\n')


def main():
    try:
        event = json.load(sys.stdin)
        command = (event.get('tool_input') or {}).get('command') or ''
        launches = list(starts(command)) if 'herdr' in command else []
        if not launches:
            return
        root = yield_gate.main_checkout(event.get('cwd') or os.getcwd())
        if root is None:
            return
        orchestrator = {'pane': os.environ.get('HERDR_PANE_ID'), 'session': event.get('session_id')}
        deadline = time.monotonic() + LOOKUP_SECONDS
        for session, agent, pane in launches:
            server = None if 'HERDR_SOCKET' in command else session or yield_gate.lifecycle().own_server()
            cwd = server and pane_cwd(session, pane, deadline)
            log_launch(root, server, agent, pane, orchestrator, cwd)
    except Exception:
        return  # Recording never blocks the command.


def start(argv):
    """Start a saved route in its own tab; print Herdr's response and OpenCode connection metadata when needed."""
    parser = argparse.ArgumentParser(prog='herdr_launch.py start')
    parser.add_argument('--route', required=True)
    parser.add_argument('--worktree', required=True, type=lambda path: str(Path(path).resolve()),
                        help="the worker's worktree; a relative path is the caller's")
    parser.add_argument('--assignment', required=True, help='<workstream>/<assignment>')
    parser.add_argument('--role', required=True)
    parser.add_argument('--name', required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', args.name):  # Herdr refuses it only at agent start, after the tab.
        parser.error(f"--name {args.name!r}: an agent name must start with a lowercase letter and hold only lowercase "
                     "letters, digits, '-' or '_' (1-32 characters), as Herdr requires")
    workspace = os.environ.get('HERDR_WORKSPACE_ID')
    if not workspace:
        parser.error('run start in the orchestrator Herdr workspace (HERDR_WORKSPACE_ID)')
    route = json.loads(Path(args.route).read_text())
    lifecycle = yield_gate.lifecycle()
    root = yield_gate.main_checkout(os.getcwd())
    if root is None:
        parser.error('run start inside the checkout whose launches the stop check tracks')
    private = keeper = error_control = None
    connection = None
    extra_env = []
    if route['target_runtime'] == 'opencode':
        integration = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'opencode'
        if not all((integration / name).is_file() for name in (
                'plugins/herdr-agent-state.js', 'herdr-tui-session.js', 'herdr-opencode/tui.js')):
            parser.error('OpenCode integration missing: run herdr integration install opencode')
        private = Path(tempfile.mkdtemp(prefix='zloop-opencode-'))
        try:
            config = private / 'config/opencode'
            (config / 'plugins').mkdir(parents=True)
            (config / 'herdr-opencode').mkdir()
            for name in ('plugins/herdr-agent-state.js', 'herdr-tui-session.js', 'herdr-opencode/tui.js'):
                shutil.copyfile(integration / name, config / name)
            (config / 'tui.jsonc').write_text(json.dumps({'plugin': ['./herdr-tui-session.js']}))
            (config / 'cli.json').write_text(json.dumps({'plugins': ['./herdr-opencode']}))
            (config / 'opencode.json').write_text(json.dumps({'permission': {
                'edit': 'allow', 'bash': 'allow', 'webfetch': 'allow', 'external_directory': 'allow'}}))
            password = private / 'password'
            with os.fdopen(os.open(password, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
                stream.write(secrets.token_urlsafe(32))
            env = dict(os.environ, XDG_CONFIG_HOME=str(private / 'config'), XDG_STATE_HOME=str(private / 'state'),
                       OPENCODE_PASSWORD=password.read_text())
            for key in ('OPENCODE_CONFIG', 'OPENCODE_CONFIG_CONTENT', 'OPENCODE_CONFIG_DIR', 'OPENCODE_TUI_CONFIG'):
                env.pop(key, None)
                os.environ.pop(key, None)
            for key in ('XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'OPENCODE_PASSWORD'):
                extra_env.extend(['--env', f'{key}={env[key]}'])
        except OSError as error:
            shutil.rmtree(private)
            print(json.dumps({'error': f'OpenCode configuration failed: {type(error).__name__}'}))
            return 1
    else:
        launch = route['launch']['argv']
    step = 'tab create'
    code = 1
    try:
        code, response = lifecycle.herdr(None, 'tab', 'create', '--workspace', workspace, '--cwd', args.worktree,
                                         '--label', f'{args.assignment} {args.role}',
                                         '--env', f'ZLOOP_WORKER={args.assignment}', *extra_env, '--no-focus')
        if code == 0:
            pane = response['result']['root_pane']['pane_id']
            log_launch(root, lifecycle.own_server(), args.name, pane,
                       {'pane': os.environ.get('HERDR_PANE_ID'), 'session': (lifecycle.host_sessions() or [None])[0]},
                       args.worktree)
            if private:
                step = 'keeper start'
                error_control = tempfile.TemporaryDirectory(prefix='zloop-opencode-launch-')
                error_file = Path(error_control.name) / 'error'
                keeper = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'opencode',
                    '--route', str(Path(args.route).resolve()), '--worktree', args.worktree,
                    '--private', str(private), '--pane', pane, '--tab', response['result']['tab']['tab_id'],
                    '--error-file', str(error_file)],
                    env=env, start_new_session=True, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                step = 'server/session readiness'
                deadline = time.monotonic() + 30
                while not (private / 'ready.json').exists():
                    if keeper.poll() is not None:
                        reason = error_file.read_text() if error_file.exists() else 'keeper exited'
                        raise ValueError(reason)
                    if time.monotonic() >= deadline:
                        raise ValueError('keeper did not answer within 30 seconds')
                    time.sleep(.05)
                connection = json.loads((private / 'ready.json').read_text())
                launch = ['--server', connection['server'], '--session', connection['session']]
            step = 'agent start'
            code, response = lifecycle.herdr(None, 'agent', 'start', args.name, '--kind', route['target_runtime'],
                                             '--pane', pane, '--', *launch, timeout=120)
            if code == 0 and connection:
                response['opencode'] = connection
        if code and private:
            response['opencode_error'] = f'OpenCode Herdr {step} failed'
    except (OSError, ValueError, KeyError) as error:
        if private is None:
            raise
        code, response = 1, {'error': f'OpenCode Herdr {step} failed: {error}'}
    finally:
        if private and (code or keeper is None):
            if keeper is not None:
                keeper.terminate()
                keeper.wait(timeout=7)
            shutil.rmtree(private, ignore_errors=True)
        if error_control is not None:
            error_control.cleanup()
    print(json.dumps(response))
    return code


def pane_verdict(code, response, failing_since, now):
    """('gone' once Herdr reports the pane missing, 'lost' once every lookup has failed for PANE_GRACE seconds, else
    None; when the current run of failed lookups began, or None after an answer)."""
    if not code:
        return None, None
    if (response.get('error') or {}).get('code') == 'pane_not_found':
        return 'gone', failing_since
    failing_since = now if failing_since is None else failing_since
    return ('lost' if now - failing_since >= PANE_GRACE else None), failing_since


def opencode(argv):
    """Keep a private server until Herdr reports its pane gone, answers no pane lookup for PANE_GRACE seconds, the server
    exits, or this detached owner receives SIGHUP/SIGTERM."""
    parser = argparse.ArgumentParser(prog='herdr_launch.py opencode')
    parser.add_argument('--route', required=True)
    parser.add_argument('--worktree', required=True)
    parser.add_argument('--private', required=True, type=Path)
    parser.add_argument('--pane', required=True)
    parser.add_argument('--tab', required=True)
    parser.add_argument('--error-file', required=True, type=Path)
    args = parser.parse_args(argv)
    private = args.private
    for number in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(number, lambda number, frame: sys.exit(128 + number))
    server = None
    step = 'serve'
    try:
        (private / 'pid').write_text(str(os.getpid()))
        env = dict(os.environ, HERDR_PANE_ID=args.pane, HERDR_TAB_ID=args.tab)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        server = subprocess.Popen(['opencode', 'serve', '--hostname', '127.0.0.1', '--port', str(port)],
                                  env=env, cwd=args.worktree, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        step = 'server readiness'
        deadline = time.monotonic() + 20
        while True:
            if server.poll() is not None:
                step = 'serve'
                raise ValueError(f'server exited {server.returncode}')
            try:
                with urlopen(url, timeout=.5):
                    break
            except HTTPError:
                break
            except (URLError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise ValueError('server did not answer within 20 seconds')
                time.sleep(.05)
        step = 'session POST'
        api = json.loads(Path(args.route).read_text())['launch']['api']
        body = dict(api['body'], location={'directory': args.worktree})
        created = subprocess.run(['opencode', 'api', api['method'], api['path'], '--server', url,
                                  '-d', json.dumps(body)], env=env, cwd=args.worktree,
                                 capture_output=True, text=True, timeout=5)
        if created.returncode:
            raise ValueError(f'command exited {created.returncode}')
        session = json.loads(created.stdout)['data']['id']
        if not isinstance(session, str) or not session:
            raise ValueError('response has no session ID')
        ready = private / 'connection.tmp'
        ready.write_text(json.dumps({'server': url, 'session': session, 'password_file': str(private / 'password')}))
        ready.replace(private / 'ready.json')
        step = 'pane lookup'
        failing_since = None
        while server.poll() is None:
            code, response = yield_gate.lifecycle().herdr(None, 'pane', 'get', args.pane, timeout=4)
            ended, failing_since = pane_verdict(code, response, failing_since, time.monotonic())
            if ended == 'gone':
                return 0
            if ended == 'lost':
                raise ValueError(f'Herdr answered no pane lookup for {PANE_GRACE} seconds')
            time.sleep(2)
        raise ValueError(f'server exited {server.returncode}')
    except Exception as caught:
        if args.error_file.parent.exists():
            args.error_file.write_text(f'OpenCode {step} failed: {type(caught).__name__}: {caught}')
        return 1
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
        shutil.rmtree(private)


def dismiss(argv):
    """Append a dismissal of the earlier launches of one pane or agent name, with the reason they need no attempt."""
    parser = argparse.ArgumentParser(prog='herdr_launch.py dismiss')
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--pane')
    target.add_argument('--name')
    parser.add_argument('--session', help="this orchestrator's host session ID, as the stop message names it")
    why = parser.add_mutually_exclusive_group(required=True)
    why.add_argument('--reason')
    why.add_argument('--keep', help='why the pane stays open on purpose: it is then never held for being open')
    args = parser.parse_args(argv)
    if args.reason is not None and not args.reason.strip():
        parser.error('--reason must say why the launch needs no registered attempt')
    if args.keep is not None and not args.keep.strip():
        parser.error('--keep must say why the pane stays open on purpose')
    by = {'pane': os.environ.get('HERDR_PANE_ID'), 'session': args.session}
    if not any(by.values()):
        parser.error('outside Herdr, pass --session with the host session ID the stop message names')
    root = yield_gate.main_checkout(os.getcwd())
    if root is None:
        parser.error('run it inside the checkout whose launches it dismisses')
    record = {'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'dismissed': {'pane': args.pane} if args.pane else {'agent': args.name}, 'by': by,
              'reason': args.keep if args.keep is not None else args.reason, **({'keep': True} if args.keep else {})}
    with (root / yield_gate.LAUNCHES).open('a') as stream:
        stream.write(json.dumps(record) + '\n')


if __name__ == '__main__':
    if sys.argv[1:2] == ['start']:
        sys.exit(start(sys.argv[2:]))
    elif sys.argv[1:2] == ['dismiss']:
        dismiss(sys.argv[2:])
    elif sys.argv[1:2] == ['opencode']:
        sys.exit(opencode(sys.argv[2:]))
    else:
        main()
