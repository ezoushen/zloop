#!/usr/bin/env python3
"""PreToolUse hook: log each `herdr agent start` a session runs, so the yield gate can name unregistered workers.

It finds launches behind assignments, wrappers (`timeout`, `env` and their options) and a shell's `-c` string, not
inside heredocs or backticks; a command that names HERDR_SOCKET_PATH logs none.

Appends one JSON line per launch to the main checkout's .scratch/herdr-launches.jsonl: the time, agent name and
pane, the Herdr server session it ran in (`--session`, else the one the caller's own HERDR_SOCKET_PATH names, else
`default`), the orchestrator (its HERDR_PANE_ID and host session ID) and the pane's cwd when Herdr reports it.
Claude and Codex run it before Bash; Pi's extension passes its bash and bg_run calls. It never blocks the command.

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
import sys
import time

import pr_body
import yield_gate

SHELLS = {'sh', 'bash', 'zsh', 'dash'}
# All Herdr lookups of one hook run, well inside the hosts' 10-second hook timeout.
LOOKUP_SECONDS = 4
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
    """Yield (Herdr session or None, agent name, pane) for each `herdr [--session S] agent start` the command runs. A
    command that names HERDR_SOCKET_PATH yields none: which server it runs in cannot be read from the text, and the
    stop check's scan finds the agent."""
    if 'HERDR_SOCKET' in command:
        return
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
        log = root / yield_gate.LAUNCHES
        log.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + LOOKUP_SECONDS
        for session, agent, pane in launches:
            record = {'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'agent': agent, 'pane': pane,
                      'herdr_session': session or yield_gate.lifecycle().own_server(), 'orchestrator': orchestrator}
            cwd = pane and pane_cwd(session, pane, deadline)
            with log.open('a') as stream:
                stream.write(json.dumps(dict(record, **({'cwd': cwd} if cwd else {}))) + '\n')
    except Exception:
        return  # Recording never blocks the command.


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
    if sys.argv[1:2] == ['dismiss']:
        dismiss(sys.argv[2:])
    else:
        main()
