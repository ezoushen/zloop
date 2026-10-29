"""Small native session records shared by lifecycle CLI tests."""

from datetime import datetime, timezone
import json
from pathlib import Path
import time


def session_record(path, runtime, session, worktree, *, model='sonnet', effort='high', scope=None,
                   timestamp=None, child=False):
    """Write one runtime's settings and probe response, optionally with a native child identity."""
    path, scope = Path(path), scope or {}
    stamp = datetime.fromtimestamp(timestamp if timestamp is not None else time.time(), timezone.utc).isoformat()
    if runtime == 'claude':
        rows = [{'type': 'assistant', 'sessionId': 'parent' if child else session,
                 **({'agentId': session, 'isSidechain': True} if child else {}),
                 'cwd': str(worktree), 'perTurnEffort': effort, 'message': {'model': model}}]
        if 'permission_mode' in scope:
            rows.append({'type': 'permission-mode', 'permissionMode': scope['permission_mode']})
    elif runtime == 'codex':
        rows = [{'type': 'session_meta', 'payload': {'id': 'thread' if child else session,
                 **({'agent_path': session} if child else {}), 'cwd': str(worktree)}},
                {'type': 'turn_context', 'payload': {'cwd': str(worktree), 'model': model, 'effort': effort,
                 **({'sandbox_policy': {'type': scope['sandbox']}} if 'sandbox' in scope else {}),
                 **({'approval_policy': scope['approval']} if 'approval' in scope else {})}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': 'READY'}]}}]
    elif runtime == 'pi':
        provider, _, model_id = model.partition('/')
        rows = [{'type': 'session', 'id': session, 'cwd': str(worktree)},
                {'type': 'thinking_level_change', 'id': 'thinking', 'parentId': None, 'thinkingLevel': effort},
                {'type': 'message', 'id': 'probe', 'parentId': 'thinking', 'message': {
                    'role': 'assistant', 'provider': provider, 'model': model_id, 'stopReason': 'stop'}}]
    else:
        provider, _, model_id = model.partition('/')
        path.write_text(json.dumps({'info': {'id': session, 'directory': str(worktree)}, 'messages': [
            {'info': {'role': 'assistant', 'sessionID': session, 'providerID': provider,
                      'modelID': model_id, 'variant': effort, 'time': {'created': int(
                          (timestamp if timestamp is not None else time.time()) * 1000)}}, 'parts': []}]}))
        return path
    path.write_text(''.join(json.dumps(dict(row, timestamp=stamp)) + '\n' for row in rows))
    return path


def assistant_turn(path, runtime, *, timestamp=None, role='assistant'):
    """Append a turn to a fixture record, preserving its session and Pi branch identity."""
    path = Path(path)
    when = timestamp if timestamp is not None else time.time()
    stamp = datetime.fromtimestamp(when, timezone.utc).isoformat()
    if runtime == 'opencode':
        data = json.loads(path.read_text())
        message = dict(data['messages'][-1]['info'], role=role, time={'created': int(when * 1000)})
        data['messages'].append({'info': message, 'parts': []})
        path.write_text(json.dumps(data))
        return
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if runtime == 'claude':
        row = dict(next(row for row in rows if row.get('type') == 'assistant'), type=role, timestamp=stamp)
    elif runtime == 'codex':
        row = {'type': 'response_item', 'timestamp': stamp, 'payload': {'type': 'message', 'role': role,
               'content': [{'type': 'output_text', 'text': 'done'}]}}
    else:
        message = dict(next(row['message'] for row in rows if row.get('type') == 'message'), role=role)
        row = {'type': 'message', 'id': f'turn-{len(rows)}', 'parentId': rows[-1]['id'],
               'timestamp': stamp, 'message': message}
    with path.open('a') as stream:
        stream.write(json.dumps(row) + '\n')
