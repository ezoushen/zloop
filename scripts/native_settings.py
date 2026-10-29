"""Read session-bound native settings without exposing conversation contents."""

import hashlib
import json
from datetime import datetime
from pathlib import Path


def entry_time(row):
    """A native entry's UTC epoch seconds, or None when that format supplies no entry time."""
    value = row.get('timestamp')
    if value is None:
        clock = row.get('time') or {}
        value = clock.get('completed', clock.get('created')) if isinstance(clock, dict) else clock
    if value is None:
        return None
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    if isinstance(value, (float, int)):
        return value / 1000 if value > 100_000_000_000 else value
    raise ValueError('Unsupported native entry timestamp; supply a fresh native session record')


def latest_entry_time(path, runtime):
    """The latest native entry time; file mtime is used only for formats carrying no entry times."""
    path = Path(path)
    if runtime == 'opencode':
        rows = json.loads(path.read_bytes())['messages']
        rows = [row.get('info', row) for row in rows]
    else:
        rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    times = [stamp for row in rows if (stamp := entry_time(row)) is not None]
    return times[-1] if times else path.stat().st_mtime


def assistant_turns(path, runtime, session):
    """Return content-free identities and times of this session's assistant turns on its active branch."""
    path = Path(path)
    if runtime == 'opencode':
        exported = json.loads(path.read_bytes())
        rows = [row.get('info', row) for row in exported['messages']]
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
    turns = []
    for index, row in enumerate(rows):
        payload = row.get('payload') or {}
        if runtime == 'claude':
            assistant = row.get('type') == 'assistant' and (row.get('agentId') or row.get('sessionId')) == session
        elif runtime == 'codex':
            assistant = (row.get('type') == 'response_item' and payload.get('type') == 'message'
                         and payload.get('role') == 'assistant'
                         or row.get('type') == 'event_msg' and payload.get('type') == 'agent_message')
        elif runtime == 'pi':
            assistant = row.get('type') == 'message' and row.get('message', {}).get('role') == 'assistant'
        else:
            assistant = row.get('role', row.get('type')) == 'assistant' and row.get('sessionID', session) == session
        if assistant:
            identity = hashlib.sha256(json.dumps([index, row], sort_keys=True).encode()).hexdigest()
            turns.append({'identity': identity, 'time': entry_time(row)})
    return turns


def worked_after(path, runtime, session, after, startup_turns):
    """Whether the record proves a new assistant turn after the productive-work boundary."""
    known = {turn['identity'] for turn in startup_turns}
    return any(turn['identity'] not in known and (
        turn['time'] if turn['time'] is not None else Path(path).stat().st_mtime) > after
        for turn in assistant_turns(path, runtime, session))


def observe(path, runtime, session, worktree):
    """Return observed controls and the source prefix identity; unknown controls stay absent."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('Native evidence path must be absolute')
    if runtime == 'opencode':
        raw = path.read_bytes()
        exported = json.loads(raw)
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
        if not isinstance(directory, str) or Path(directory).resolve() != Path(worktree).resolve():
            raise ValueError('Native evidence worktree does not match the assignment')
        provider, model = latest['provider'], latest['model']
        settings = {'runtime': runtime, 'model': provider + '/' + model if provider and model else None,
                    'effort': latest['variant'], 'permissions': info.get('permissions')}
        settings = {key: value for key, value in settings.items()
                    if isinstance(value, str) and value.strip() or isinstance(value, list) and value}
        return {'observed': settings, 'session': session, 'worktree': str(Path(worktree).resolve()),
                'source': str(path), 'source_bytes': len(raw), 'source_sha256': hashlib.sha256(raw).hexdigest()}
    if runtime not in ('claude', 'codex', 'pi'):
        raise ValueError('Unsupported native evidence runtime')
    digest, size, latest, header, mode = hashlib.sha256(), 0, None, None, None
    entries = {}
    with path.open('rb') as stream:
        for line in stream:
            if not line.endswith(b'\n'):
                raise ValueError('Native evidence has an incomplete record; retry observation before work')
            digest.update(line)
            size += len(line)
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('Native evidence record must be an object')
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
            elif runtime == 'pi':
                if row.get('type') == 'session':
                    header = row
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
    if latest is None or session not in (identity, (header or {}).get('agent_path')):
        raise ValueError('Native evidence does not belong to the registered session')
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
    return {'observed': settings, 'session': session, 'worktree': str(Path(worktree).resolve()),
            'source': str(path), 'source_bytes': size, 'source_sha256': digest.hexdigest()}


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


def working_directory(path, runtime, session):
    """The working directory a native child's own record gives it, or None when the record names none: the latest
    `cwd` of a Claude transcript's rows for the session (a subagent's rows name it as `agentId`), the latest
    `turn_context` cwd of a Codex rollout (its `id`, or a native child's `agent_path`), a Pi session header's cwd."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('Native evidence path must be absolute')
    if runtime not in ('claude', 'codex', 'pi'):
        raise ValueError('Unsupported native evidence runtime')
    found, cwd, named = False, None, None
    with path.open('rb') as stream:
        for line in stream:
            if not line.endswith(b'\n'):
                raise ValueError('Native evidence has an incomplete record; retry observation before work')
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('Native evidence record must be an object')
            payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
            if runtime == 'claude':
                named = named or row.get('agentId') or row.get('sessionId')
                if session == (row.get('agentId') or row.get('sessionId')):
                    found, cwd = True, row.get('cwd', cwd)
            elif runtime == 'codex' and row.get('type') == 'session_meta':
                named = payload.get('agent_path') or payload.get('id')
                found = session in (payload.get('id'), payload.get('agent_path'))
            elif runtime == 'codex' and row.get('type') == 'turn_context':
                cwd = payload.get('cwd', cwd)
            elif runtime == 'pi' and row.get('type') == 'session':
                named = row.get('id')
                found, cwd = row.get('id') == session, row.get('cwd')
    if not found:
        raise ValueError(f'Native evidence does not belong to the registered session {session}: the record '
                         + (f'is session {named}' if named else 'names no session'))
    return cwd if isinstance(cwd, str) and cwd.strip() else None


def pi_session_id(path):
    """The session id a Pi session record's header names."""
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError('native must be the absolute path of the child\'s session record')
    with path.open('rb') as stream:
        for line in stream:
            row = json.loads(line)
            if isinstance(row, dict) and row.get('type') == 'session' and isinstance(row.get('id'), str):
                return row['id']
    raise ValueError(f'{path} has no session header')
