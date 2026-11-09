"""Read session-bound native settings without exposing conversation contents."""

import hashlib
import json
import re
from pathlib import Path


def codex_task_name(attempt):
    """A fixed 32-character Codex task name binding the full UTF-8 attempt ID to a 96-bit SHA-256 prefix."""
    return 'attempt_' + hashlib.sha256(attempt.encode('utf-8')).hexdigest()[:24]


def has_attempt(text, attempt):
    """Whether packet text contains the exact attempt header, alone or after its assignment."""
    return bool(re.search(r'(?m)^(?:assignment:[^\r\n]*?[ \t]+)?attempt:[ \t]+'
                          + re.escape(attempt) + r'[ \t]*$', text))


def _packet_reply_t3(messages, attempt):
    """Packet proof and reply from a T3 thread's messages: the packet is the user text naming the attempt; the
    reply is the first assistant text after it."""
    packet = False
    for message in messages:
        role, body = message.get('role'), message.get('text') or ''
        if role == 'user':
            packet = packet or has_attempt(body, attempt)
        elif role == 'assistant' and packet:
            return True, True
    return packet, False


def packet_reply(path, runtime, session, attempt, *, parents=None):
    """Return packet proof and its reply, or parent proof and a reply for a Codex native child.

    Only user text establishes a packet. Codex children have encrypted tasks and use parents from registration.
    Pi follows its active branch.
    """
    path = Path(path)
    if runtime == 't3':
        # A T3 worker's record is the thread state `t3 thread show --json` wrote: nested thread beside messages
        # whose text rides a 'text' field.
        document = json.loads(path.read_bytes())
        return _packet_reply_t3(document.get('messages') or [], attempt)
    if runtime == 'opencode':
        rows = json.loads(path.read_bytes())['messages']
    else:
        rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    if runtime == 'pi':
        entries = {row['id']: row for row in rows if row.get('type') != 'session'}
        branch, current = [], rows[-1]
        while current.get('type') != 'session':
            branch.append(current)
            parent = current.get('parentId')
            if parent is None:
                break
            current = entries[parent]
        rows = list(reversed(branch))
    packet = False
    for row in rows:
        parts, text_types = [], ('text',)
        if runtime == 'claude':
            if (row.get('agentId') or row.get('sessionId')) != session:
                continue
            role = row.get('type')
            parts = row.get('message', {}).get('content', [])
        elif runtime == 'codex':
            payload = row.get('payload') or {}
            if parents is not None and row.get('type') == 'session_meta':
                packet = payload.get('parent_thread_id') in parents
            role = payload.get('role') if row.get('type') == 'response_item' and payload.get('type') == 'message' else None
            if row.get('type') == 'event_msg' and payload.get('type') == 'agent_message':
                role = 'assistant'
            parts, text_types = payload.get('content', []), ('input_text',)
        elif runtime == 'pi':
            message = row.get('message') or {}
            role, parts = message.get('role'), message.get('content', [])
        else:
            info = row.get('info')
            if isinstance(info, dict):
                if info.get('sessionID') != session:
                    continue
                role, parts = info.get('role'), row.get('parts', [])
            else:
                role, parts = row.get('type'), row.get('content', row.get('text', []))
        if role == 'user' and parents is None:
            text = parts if isinstance(parts, str) else '\n'.join(
                part['text'] for part in parts if isinstance(part, dict) and part.get('type') in text_types
                and isinstance(part.get('text'), str))
            packet = packet or has_attempt(text, attempt)
        elif role == 'assistant' and packet:
            return True, True
    return packet, False


def observe(path, runtime, session, worktree):
    """Return observed controls, cwd and source path; unknown controls stay absent. A `worktree` holds the
    record's directory to it; None (a native child's record names its orchestrator's directory, which moves) checks
    none."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('Native evidence path must be absolute')
    if runtime == 't3':
        # A T3 worker's session record is the thread state `t3 thread show --json` wrote at startup: the identity,
        # status and settings come from the thread itself.
        record = json.loads(path.read_bytes())
        thread = record.get('thread') if isinstance(record.get('thread'), dict) else record
        observed = {'runtime': 't3', 'model': thread.get('model'), 'status': thread.get('status')}
        return {'observed': observed, 'cwd': None, 'source': str(path)}
    if runtime == 'opencode':
        exported = json.loads(path.read_bytes())
        if not isinstance(exported, dict) or not isinstance(exported.get('info'), dict):
            raise ValueError('Unsupported native export schema')
        info, messages = exported['info'], exported.get('messages')
        if not isinstance(messages, list) or any(not isinstance(item, dict) for item in messages):
            raise ValueError('Unsupported native message schema')
        if all(isinstance(item.get('info'), dict) for item in messages):
            # OpenCode 1 `export`: each message wraps its metadata in `info` and its parts beside it.
            turn = [dict(role=item['info'].get('role'), session=item['info'].get('sessionID'),
                         provider=item['info'].get('providerID'), model=item['info'].get('modelID'),
                         variant=item['info'].get('variant'), error=item['info'].get('error'),
                         parts=item.get('parts', [])) for item in messages]
            directory = info.get('directory')
        elif all(isinstance(item.get('type'), str) for item in messages):
            # OpenCode 2 `session export`: flat typed messages; the session ID lives only in `info`.
            turn = [dict(role=item['type'], session=info.get('id'), provider=model.get('providerID'),
                         model=model.get('id'), variant=model.get('variant'), error=item.get('error'),
                         parts=item.get('content', []))
                    for item in messages
                    for model in [item.get('model') if isinstance(item.get('model'), dict) else {}]]
            directory = (info.get('location') or {}).get('directory')
        else:
            raise ValueError('Unsupported native message schema')
        latest = next((item for item in reversed(turn) if item['role'] == 'assistant'), None)
        if info.get('id') != session or latest is None or latest['session'] != session:
            raise ValueError('Native evidence does not belong to the registered session')
        if latest['error']:
            raise ValueError('OpenCode has a failed native response; inspect the provider before work')
        for index in range(len(turn) - 1, -1, -1):
            if turn[index]['role'] == 'user':
                turn = turn[index + 1:]
                break
        if any(isinstance(part, dict) and part.get('type') == 'tool'
               and part.get('state', {}).get('status') == 'error'
               for item in turn if item['role'] == 'assistant' for part in item['parts']):
            raise ValueError('OpenCode startup has a failed tool call; inspect it before work')
        if worktree and (not isinstance(directory, str) or Path(directory).resolve() != Path(worktree).resolve()):
            raise ValueError('Native evidence worktree does not match the assignment')
        provider, model = latest['provider'], latest['model']
        settings = {'runtime': runtime, 'model': provider + '/' + model if provider and model else None,
                    'effort': latest['variant'], 'permissions': info.get('permissions')}
        settings = {key: value for key, value in settings.items()
                    if isinstance(value, str) and value.strip() or isinstance(value, list) and value}
        return {'observed': settings, 'session': session, 'worktree': worktree and str(Path(worktree).resolve()),
                'cwd': directory if isinstance(directory, str) and directory.strip() else None, 'source': str(path)}
    if runtime not in ('claude', 'codex', 'pi'):
        raise ValueError('Unsupported native evidence runtime')
    latest, header, mode, directory = None, None, None, None
    entries = {}
    with path.open('rb') as stream:
        for line in stream:
            if not line.endswith(b'\n'):
                raise ValueError('Native evidence has an incomplete record; retry observation before work')
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('Native evidence record must be an object')
            if runtime == 'claude' and session == (row.get('agentId') or row.get('sessionId')):
                directory = row.get('cwd', directory)
            if runtime == 'claude' and row.get('type') == 'assistant':
                if not isinstance(row.get('message'), dict):
                    raise ValueError('Unsupported native assistant schema')
                latest = row
            elif runtime == 'claude' and row.get('type') == 'permission-mode':
                mode = row.get('permissionMode')  # A TUI transcript lists no tools; its latest mode is the evidence.
            elif runtime == 'codex' and row.get('type') == 'session_meta':
                header = row['payload']
                if not isinstance(header, dict):
                    raise ValueError('Unsupported native session schema')
            elif runtime == 'codex' and row.get('type') == 'turn_context':
                latest = row['payload']
                if not isinstance(latest, dict):
                    raise ValueError('Unsupported native turn schema')
                directory = latest.get('cwd', directory)
            elif runtime == 'pi':
                if row.get('type') == 'session':
                    header = row
                    directory = row.get('cwd')
                else:
                    if not isinstance(row.get('message', {}), dict):
                        raise ValueError('Unsupported native message schema')
                    # Retain branch links and settings only, never message contents.
                    latest = {key: row.get(key) for key in ('id', 'parentId', 'type', 'provider',
                                                           'modelId', 'thinkingLevel')}
                    latest['message'] = {key: row.get('message', {}).get(key)
                                         for key in ('role', 'provider', 'model', 'stopReason')}
                    # A compaction's system message is a complete tool checkpoint; other system messages patch tools.
                    system = (row.get('systemMessage') or {}) if row.get('type') == 'compaction' \
                        else row.get('message', {})
                    latest['tools'] = [[tool['name'] for tool in system.get(key) or []]
                                       for key in ('toolsAdded', 'toolsRemoved')]
                    if not isinstance(latest['id'], str) or latest['id'] in entries:
                        raise ValueError('Invalid native branch entry identity')
                    entries[latest['id']] = latest
    if runtime == 'pi':
        branch, visited, current = [], set(), latest
        while current:
            if current['id'] in visited:
                raise ValueError('Native branch contains a cycle')
            visited.add(current['id'])
            branch.append(current)
            parent = current['parentId']
            if parent is not None and parent not in entries:
                raise ValueError('Native branch parent is missing')
            current = entries.get(parent)
        latest = dict(header or {})
        for entry in reversed(branch):
            if entry['type'] == 'thinking_level_change':
                latest['effort'] = entry['thinkingLevel']
            elif entry['type'] == 'model_change':
                latest['model'] = entry['provider'] + '/' + entry['modelId']
            elif entry['type'] == 'compaction':
                latest['tools'] = sorted(set(entry['tools'][0]))
            elif entry['type'] == 'message' and entry['message']['role'] == 'system':
                added, removed = entry['tools']
                latest['tools'] = sorted((set(latest.get('tools', [])) | set(added)) - set(removed))
            elif entry['type'] == 'message' and entry['message']['role'] == 'assistant':
                message = entry['message']
                latest['model'] = message['provider'] + '/' + message['model']
                latest['response_failed'] = message['stopReason'] == 'error'
        if latest.get('response_failed'):
            raise ValueError('Pi has a failed native response; inspect the provider before work')
    identity = ((latest.get('agentId') or latest.get('sessionId')) if runtime == 'claude' and latest
                else (header or {}).get('id'))
    if runtime == 'claude' and latest and latest.get('isSidechain'):
        identity = latest.get('agentId')
    if latest is None or session not in (identity, (header or {}).get('agent_path')):
        raise ValueError(f'Native evidence does not belong to the registered session {session}: the record '
                         + (f'is session {identity}' if identity else 'names no session'))
    if worktree:
        if not isinstance(latest.get('cwd'), str):
            raise ValueError('Native evidence worktree does not match the assignment')
        problem = outside(latest['cwd'], worktree, runtime)
        if problem:
            raise ValueError(problem)
        if header and (not isinstance(header.get('cwd'), str)
                       or Path(header['cwd']).resolve() != Path(worktree).resolve()):
            raise ValueError('Native evidence session worktree does not match the assignment')
    effort = latest.get('perTurnEffort') if runtime == 'claude' else None
    if effort is None:
        effort = latest.get('effort')
    model = latest.get('message', {}).get('model') if runtime == 'claude' else latest.get('model')
    settings = {'runtime': runtime, 'model': model, 'effort': effort}
    if runtime == 'claude':
        settings['permission_mode'] = mode
    elif runtime == 'codex':
        policy = latest.get('sandbox_policy')
        settings.update(sandbox=policy.get('type') if isinstance(policy, dict) else None,
                        approval=latest.get('approval_policy'))
        # A permissions profile also reports workspace-write, so its filesystem entries tell what is writable.
        profile = latest.get('permission_profile') or {}
        system = profile.get('file_system') or {}
        if profile.get('type') == 'managed' and system.get('type') == 'restricted':
            settings['filesystem'] = sorted(
                entry['access'] + ' ' + (':' + entry['path']['value']['kind'] if entry['path']['type'] == 'special'
                                         else entry['path']['path']) for entry in system['entries'])
    else:  # Pi
        settings['tools'] = latest.get('tools')
    settings = {key: value for key, value in settings.items()
                if isinstance(value, str) and value.strip() or isinstance(value, list) and value}
    return {'observed': settings, 'session': session, 'worktree': worktree and str(Path(worktree).resolve()),
            'cwd': directory if isinstance(directory, str) and directory.strip() else None, 'source': str(path)}


def outside(cwd, worktree, runtime):
    """Why a worker working in `cwd` is not in its assigned worktree, or None when it is. Claude updates cwd while
    navigating, so a Claude worker may be in a subdirectory, but a nested checkout is a different assignment."""
    current, assigned = Path(cwd).resolve(), Path(worktree).resolve()
    if current == assigned:
        return None
    if runtime != 'claude' or assigned not in current.parents or not current.is_dir():
        return 'Native evidence worktree does not match the assignment'
    for directory in (current, *current.parents):
        if directory == assigned:
            return None
        if (directory / '.git').exists():
            return 'Native evidence belongs to a nested repository'


def session_id(path, runtime):
    """The session a Claude, Codex or Pi session record names: a Claude row's `agentId` or `sessionId`, a Codex
    rollout's `agent_path` or `id`, a Pi header's `id`."""
    path = Path(path)
    try:
        with path.open('rb') as stream:
            for line in stream:
                row = json.loads(line)
                payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
                named = (row.get('agentId') or row.get('sessionId') if runtime == 'claude'
                         else payload.get('agent_path') or payload.get('id') if row.get('type') == 'session_meta'
                         else row.get('id') if runtime == 'pi' and row.get('type') == 'session' else None)
                if isinstance(named, str) and named.strip():
                    return named
    except (OSError, ValueError, AttributeError):
        pass
    raise ValueError(f'{path} names no readable {runtime} session; pass native_session: the session id, or the '
                     'absolute path of its session record')
