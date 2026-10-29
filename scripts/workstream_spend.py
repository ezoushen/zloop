#!/usr/bin/env python3
"""Report a workstream's recorded token use per accepted ticket from lifecycle state and native transcripts."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import shlex
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import outcomes  # noqa: E402


FIELDS = ('responses', 'input', 'cache_write', 'cache_read', 'output')
# The yield gate's Stop reason header, current first, then the ones older transcripts hold.
YIELD_GATES = ('Stopping now leaves these gaps:', 'These workers could finish or run without waking you',
               'These lifecycle workers could finish without waking you')
SHELL_TOOLS = {'Bash', 'bash', 'exec_command', 'shell', 'shell_command'}
TOOL_ACTIVITIES = {'read': {'Read', 'Grep', 'Glob', 'LS', 'read', 'grep', 'find', 'ls'},
                   'edit': {'Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'edit', 'write', 'apply_patch'},
                   'agent': {'Agent', 'Task', 'SendMessage'}}
READ_COMMANDS = {'cat', 'sed', 'head', 'tail', 'rg', 'grep', 'ls', 'find', 'wc', 'jq'}
# Transcripts from before the scripts were renamed name them with '-'; script_name reads both.
SCRIPTS = {'run_check.py': 'check', 'agent_models.py': 'routing', 'decisions.py': 'routing',
           'decision_journal.py': 'routing'}
SEPARATORS = ';&|()\n'
OPTION_VALUES = {'git': {'-C', '-c', '--git-dir', '--work-tree'}, 'herdr': {'--session'}}


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamps require an explicit UTC offset')
    return parsed


def total(responses):
    sums = dict.fromkeys(FIELDS, 0)
    for response in responses:
        sums['responses'] += 1
        for field in FIELDS[1:]:
            sums[field] += response[field]
    return sums


def trigger(text):
    """What started a host turn: a wake receipt or notification, the yield hook, or the user."""
    text = text.strip()
    if any(header in text for header in YIELD_GATES):
        return 'yield-hook'
    if text.startswith(('<task-notification>', '<background-task-notification>')):
        return 'wake'
    try:
        receipt = json.loads(text)
    except ValueError:
        return 'user'
    return 'wake' if isinstance(receipt, dict) and 'event_id' in receipt and receipt.get('kind') in (
        'wait-finished', 'hold') else 'user'


def uncommented(command):
    """The command without shell comments, keeping their newlines so the next line stays separate."""
    kept, quote, index = [], None, 0
    while index < len(command):
        char = command[index]
        if quote:
            quote = None if char == quote else quote
        elif char in '\'"':
            quote = char
        elif char == '#' and (index == 0 or command[index - 1].isspace()):
            index = command.find('\n', index)
            if index < 0:
                break
            char = '\n'
        kept.append(char)
        index += 1
    return ''.join(kept)


HEREDOC = re.compile(r'<<-?\s*([\'"]?)([A-Za-z_][\w.-]*)\1')


def heredoc_delimiters(line):
    """Here-document delimiters a line opens: a `<<` outside quotes and shell arithmetic."""
    found, quote, depth, index = [], None, 0, 0
    while index < len(line):
        char = line[index]
        if quote:
            quote = None if char == quote else quote
        elif char in '\'"':
            quote = char
        elif line.startswith('$((', index):
            depth, index = depth + 1, index + 2
        elif depth and line.startswith('))', index):
            depth, index = depth - 1, index + 1
        elif not depth and line.startswith('<<', index) and not line.startswith('<<<', index):
            match = HEREDOC.match(line, index)
            if match:
                found.append(match.group(2))
                index = match.end() - 1
            else:
                index += 1
        index += 1
    return found


def without_heredocs(command):
    """The command without here-document bodies, which are data rather than commands."""
    kept, pending = [], []
    for line in command.split('\n'):
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        kept.append(line)
        pending += heredoc_delimiters(line)
    return '\n'.join(kept)


def words(command):
    """Shell words, with separators, subshell brackets and newlines as their own tokens."""
    command = uncommented(without_heredocs(command.replace('\\\n', ' ')))
    lexer = shlex.shlex(command, posix=True, punctuation_chars=SEPARATORS)
    lexer.whitespace, lexer.whitespace_split, lexer.commenters = ' \t\r', True, ''
    try:
        return list(lexer)
    except ValueError:
        # An unbalanced quote: keep separators apart so each simple command is still found.
        return re.findall(r'[;&|()\n]+|[^\s;&|()]+', command)


def segments(tokens):
    """Simple commands with the separator that ends each one."""
    segment = []
    for token in tokens + [';']:
        if set(token) <= set(SEPARATORS):
            yield segment, token
            segment = []
        else:
            segment.append(token)


ASSIGNMENT = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=(.*)', re.S)
IDENTIFIER = re.compile(r'[A-Za-z_][\w-]*')
# Programs that run the command after them, and their duration arguments.
WRAPPERS = {'nohup', 'timeout', 'env', 'nice', 'time', 'caffeinate', 'command', 'exec', 'stdbuf'}
DURATION = re.compile(r'\d+(\.\d+)?[smhd]?')
# Words that open or continue a compound command; the program follows them.
PREFIXES = {'do', 'then', 'else', 'elif', '{', '!', 'time'}
# Shell keywords and builtins that run no program of their own.
BUILTINS = {'for', 'while', 'until', 'if', 'case', 'select', 'in', 'done', 'fi', 'esac', '}', 'set', 'export',
            'local', 'declare', 'readonly', 'unset', 'shift', 'trap', 'cd', 'pushd', 'popd', 'true', 'false', ':',
            'wait', 'return', 'exit', 'function'}


def script_name(token):
    return Path(token).name.replace('-', '_')


def simple_commands(command):
    """Each simple command's words without leading assignments, skipping function bodies and case
    patterns, with the names that hold the lifecycle script: variables assigned it and functions that run it."""
    aliases, commands, body, cases = set(), [], None, 0
    for segment, separator in segments(words(command)):
        while segment and segment[0] in PREFIXES:
            segment = segment[1:]
        while segment and ASSIGNMENT.fullmatch(segment[0]):
            name, value = ASSIGNMENT.fullmatch(segment[0]).groups()
            if 'worker_lifecycle.py' in value.replace('-', '_'):
                aliases.add(name)
            segment = segment[1:]
        if body is not None:
            if segment == ['}']:
                body = None
            elif any(script_name(token) == 'worker_lifecycle.py' for token in segment):
                aliases.add(body)
            continue
        if separator == '()' and len(segment) == 1 and IDENTIFIER.fullmatch(segment[0]):
            body = segment[0]  # A definition; its body runs only when called.
            continue
        cases += (segment[:1] == ['case']) - (segment[:1] == ['esac'])
        if cases and separator == ')':
            continue  # A case pattern.
        if segment:
            commands.append(segment)
    return aliases, commands


def script_index(segment, runs):
    """Where a simple command runs a script: the script word, after only an interpreter, its options or a
    variable. A script that is another program's argument, such as a file being read, is not run."""
    for index, token in enumerate(segment):
        if runs(index, token):
            prefix = segment[:index]
            return index if all(word.startswith(('-', '$')) or 'python' in Path(word).name or word in WRAPPERS
                                or DURATION.fullmatch(word) or ASSIGNMENT.fullmatch(word)
                                for word in prefix) else None
    return None


def lifecycle_action(segment, aliases):
    """The lifecycle action a simple command runs, or None when it does not run the script."""
    index = script_index(segment, lambda index, token: script_name(token) == 'worker_lifecycle.py'
                         or token.startswith('$') and token.lstrip('$').strip('{}') in aliases
                         or index == 0 and token in aliases)
    if index is None:
        return None
    rest = segment[index + 1:]
    while rest[:1] == ['--state']:
        rest = rest[2:]
    action = next((word for word in rest if not word.startswith('-')), '?')
    if '>' in action or '<' in action:
        return '?'  # A redirection, not an action.
    return None if action.startswith('$') else action


def command_activity(command):
    aliases, commands = simple_commands(command)
    for tokens in commands:
        action = lifecycle_action(tokens, aliases)
        if action:
            return 'lifecycle:' + action
        index = script_index(tokens, lambda index, token: script_name(token) in SCRIPTS)
        if index is not None:
            return SCRIPTS[script_name(tokens[index])]
    for tokens in commands:
        if tokens[0] in BUILTINS or tokens[0] in aliases:
            continue
        program, rest, skip = Path(tokens[0]).name, [], False
        for token in tokens[1:]:
            if skip:
                skip = False
            elif token in OPTION_VALUES.get(program, ()):
                skip = True
            elif not token.startswith('-'):
                rest.append(token)
        if program == 'git' and rest:
            return f'git:{rest[0]}'
        if program == 'herdr' and rest:
            return 'herdr:' + ' '.join(rest[:2])
        return 'read' if program in READ_COMMANDS else f'command:{program}'
    return 'command'


def activity(calls):
    """A response's activity is its first tool call; a response without one is a reply."""
    if not calls:
        return 'reply'
    name, command = calls[0]
    if command is not None:
        return command_activity(command)
    return next((label for label, names in TOOL_ACTIVITIES.items() if name in names), f'tool:{name}')


def shell_command(name, arguments):
    if name == 'exec' and isinstance(arguments, str):
        # Codex code mode runs shell commands from JavaScript; read the first one it issues.
        match = re.search(r'exec_command\(\{\s*"?cmd"?\s*:\s*("(?:[^"\\]|\\.)*")', arguments)
        try:
            return json.loads(match.group(1)) if match else None
        except ValueError:
            return None
    if name not in SHELL_TOOLS or not isinstance(arguments, dict):
        return None
    command = arguments.get('command', arguments.get('cmd'))
    return shlex.join(command) if isinstance(command, list) else str(command or '')


def text_of(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or any(block.get('type') == 'tool_result' for block in content):
        return None
    texts = [block.get('text', '') for block in content if block.get('type') in ('text', 'input_text')]
    return '\n'.join(texts) if texts else None


def entries(paths, unreadable):
    """Each JSON line of each file in order; a damaged or partly written line is recorded and skipped."""
    for path in paths:
        with path.open(encoding='utf-8', errors='replace') as stream:
            for number, line in enumerate(stream, 1):
                try:
                    entry = json.loads(line)
                except ValueError:
                    unreadable.append({'path': str(path), 'line': number})
                    continue
                if isinstance(entry, dict):
                    yield path, entry


def claude_responses(paths, unreadable):
    """One entry per response ID; streamed blocks repeat usage, so keep the largest output snapshot."""
    responses, cause = {}, 'user'
    for _, entry in entries(paths, unreadable):
        message = entry.get('message') or {}
        if entry.get('type') == 'user':
            text = text_of(message.get('content'))
            cause = trigger(text) if text is not None else cause
            continue
        usage = message.get('usage')
        if entry.get('type') != 'assistant' or not usage or message.get('model') == '<synthetic>':
            continue
        calls = [(block['name'], shell_command(block['name'], block.get('input')))
                 for block in message.get('content', []) if block.get('type') == 'tool_use']
        response = {'input': usage.get('input_tokens', 0), 'cache_write': usage.get('cache_creation_input_tokens', 0),
                    'cache_read': usage.get('cache_read_input_tokens', 0), 'output': usage.get('output_tokens', 0)}
        previous = responses.get(message['id'])
        if previous is None:
            responses[message['id']] = dict(response, time=entry.get('timestamp'), trigger=cause, calls=calls)
        else:
            previous['calls'] += calls
            if response['output'] > previous['output']:
                previous.update(response)
    return list(responses.values())


def codex_responses(paths, unreadable):
    """Codex repeats a token count without a new response, also across pages; only a growing total marks one."""
    responses, previous, cause, calls = [], None, 'user', []
    for _, entry in entries(paths, unreadable):
        payload = entry.get('payload') or {}
        if entry.get('type') == 'response_item':
            if payload.get('type') == 'message' and payload.get('role') == 'user':
                text = text_of(payload.get('content'))
                cause = trigger(text) if text is not None else cause
            elif payload.get('type') in ('function_call', 'custom_tool_call'):
                try:
                    arguments = payload['input'] if 'input' in payload else json.loads(payload.get('arguments') or '{}')
                except ValueError:
                    arguments = {}
                name, command = payload.get('name'), shell_command(payload.get('name'), arguments)
                if name == 'exec' and command is None and isinstance(arguments, str):
                    called = re.search(r'tools\.(\w+)\(', arguments)
                    name = called.group(1) if called else name
                calls.append((name, command))
            continue
        info = payload.get('info') or {}
        if entry.get('type') != 'event_msg' or payload.get('type') != 'token_count' or not info:
            continue
        if info['total_token_usage'] == previous:
            continue
        previous, usage = info['total_token_usage'], info['last_token_usage']
        cached, written = usage.get('cached_input_tokens', 0), usage.get('cache_write_input_tokens', 0)
        responses.append({'input': max(0, usage.get('input_tokens', 0) - cached - written),
                          'cache_write': written, 'cache_read': cached, 'output': usage.get('output_tokens', 0),
                          'time': entry.get('timestamp'), 'trigger': cause, 'calls': calls})
        calls = []
    return responses


def pi_responses(paths, unreadable):
    """Pi records usage on each assistant message entry, including abandoned branches, which were still paid for."""
    responses, cause = {}, 'user'
    for path, entry in entries(paths, unreadable):
        message = entry.get('message') or {}
        if entry.get('type') == 'custom_message':
            cause = 'yield-hook' if entry.get('customType') == 'zloop_yield_gate' else trigger(
                str(entry.get('content', '')))
            continue
        if entry.get('type') != 'message':
            continue
        if message.get('role') == 'user':
            text = text_of(message.get('content'))
            cause = trigger(text) if text is not None else cause
            continue
        usage = message.get('usage')
        if message.get('role') == 'assistant' and usage:
            calls = [(block.get('name'), shell_command(block.get('name'), block.get('arguments')))
                     for block in message.get('content', []) if block.get('type') == 'toolCall']
            responses[path, entry.get('id')] = {'input': usage.get('input', 0), 'cache_write': usage.get('cacheWrite', 0),
                                      'cache_read': usage.get('cacheRead', 0), 'output': usage.get('output', 0),
                                      'time': entry.get('timestamp'), 'trigger': cause, 'calls': calls}
    return list(responses.values())


READERS = {  # A Claude session's own Agent-tool children are part of that worker's spend.
    'claude': (claude_responses, ('*/{s}.jsonl', '*/{s}/subagents/*.jsonl', '*/*/subagents/agent-{s}.jsonl')),
    'codex': (codex_responses, ('**/rollout-*-{s}.jsonl', '**/rollout-*-{s}_*.jsonl', '**/rollout-*-{s}*.jsonl.zst')),
    'pi': (pi_responses, ('*/*_{s}.jsonl',))}


def host_reader(path):
    with path.open(encoding='utf-8', errors='replace') as stream:
        try:
            first = json.loads(stream.readline() or '{}')
        except ValueError:
            first = {}
    return {'session': pi_responses, 'session_meta': codex_responses}.get(first.get('type'), claude_responses)


def grouped(responses, key):
    groups = {}
    for response in responses:
        groups.setdefault(key(response), []).append(response)
    return {name: total(rows) for name, rows in sorted(groups.items(), key=lambda item: -total(item[1])['output'])}


def report(state, roots, hosts=(), since=None, until=None):
    db = sqlite3.connect(f'file:{state}?mode=ro', uri=True)
    attempts = [json.loads(row[0]) for row in db.execute('SELECT data FROM attempts ORDER BY id')]
    seen, workers, missing, unreadable = set(), {}, [], []
    for saved in attempts:
        key = (saved['runtime'], saved['native_session'])
        if key in seen:
            continue  # A retained session serves later attempts of the same assignment and agent.
        seen.add(key)
        gap = {key: saved[key] for key in ('attempt', 'runtime', 'native_session')}
        if saved['runtime'] not in READERS:
            missing.append(dict(gap, reason='unsupported runtime'))
            continue
        read, patterns = READERS[saved['runtime']]
        found = sorted({path for pattern in patterns
                        for path in roots[saved['runtime']].glob(pattern.format(s=saved['native_session']))})
        paths = [path for path in found if path.suffix == '.jsonl']
        if len(paths) < len(found):
            missing.append(dict(gap, reason='compressed pages not read'))
        elif not paths:
            missing.append(dict(gap, reason='no transcript found'))
        responses = read(paths, unreadable)
        workers.setdefault((saved['assignment'], saved.get('job') or 'unknown'), []).extend(responses)
    by_assignment, by_job = {}, {}
    for (assignment, job), responses in workers.items():
        by_assignment.setdefault(assignment, []).extend(responses)
        by_job.setdefault(job, []).extend(responses)
    host = [response for path in hosts for response in host_reader(path)([path], unreadable)
            if response['time'] and since <= timestamp(response['time']) < until]
    reverted = {row[0] for row in db.execute("SELECT attempt FROM events WHERE kind = 'attribution' "
                                             "AND json_extract(payload, '$.kind') = 'revert'")}
    accepted = sorted({saved['assignment'] for saved in attempts if saved.get('accepted')
                       and outcomes.needs_acceptance(saved.get('job')) and saved['attempt'] not in reverted})
    worker_responses = [response for responses in workers.values() for response in responses]
    spend = total(worker_responses + host)
    return {'state': str(state), 'accepted_tickets': accepted, 'total': spend,
            'per_accepted_ticket': {key: value / len(accepted) for key, value in spend.items()} if accepted else None,
            'workers': {'total': total(worker_responses),
                        'by_assignment': {key: total(value) for key, value in sorted(by_assignment.items())},
                        'by_job': {key: total(value) for key, value in sorted(by_job.items())}},
            'host': {'files': [str(path) for path in hosts], 'since': since and since.isoformat(),
                     'until_exclusive': until and until.isoformat(), 'total': total(host),
                     'by_trigger': grouped(host, lambda response: response['trigger']),
                     'by_activity': grouped(host, lambda response: activity(response['calls']))},
            'coverage': {'missing': missing, 'unreadable_lines': unreadable}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True, help="the workstream's workers.sqlite")
    parser.add_argument('--host', type=Path, action='append', default=[],
                        help='an orchestrator transcript (Claude, Codex or Pi); repeatable, needs --since/--until')
    parser.add_argument('--since', help='inclusive ISO timestamp with timezone for host responses')
    parser.add_argument('--until', help='exclusive ISO timestamp with timezone for host responses')
    parser.add_argument('--claude-root', type=Path, default=Path.home() / '.claude/projects')
    parser.add_argument('--codex-root', type=Path, default=Path.home() / '.codex/sessions')
    parser.add_argument('--pi-root', type=Path, default=Path.home() / '.pi/agent/sessions')
    args = parser.parse_args()
    roots = {'claude': args.claude_root, 'codex': args.codex_root, 'pi': args.pi_root}
    try:
        since = until = None
        if args.host:
            if not (args.since and args.until):
                raise ValueError('--host requires --since and --until')
            since, until = timestamp(args.since), timestamp(args.until)
        print(json.dumps(report(args.state, roots, args.host, since, until), indent=2))
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    main()
