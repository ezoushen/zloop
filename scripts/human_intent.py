"""Read and bind the active T3 user item for a pending control authorization."""
from contextlib import ExitStack
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import time
from urllib.parse import urlsplit


FRESH_SECONDS = 300
ORIGINS = ('web', 'mobile')
PAGE_LIMIT = 100
MAX_PAGES = 20


class WaitingOnUser(ValueError):
    """The pending action lacks verified user authority."""


def canonical_hash(value):
    """Hash the exact action scope with canonical JSON encoding."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def runtime_record():
    """Read the runtime through owned directories anchored at the OS account home."""
    uid = os.getuid()
    home = Path(pwd.getpwuid(uid).pw_dir)
    if not home.is_absolute():
        raise ValueError('the OS account home is unavailable')
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with ExitStack() as stack:
        parent = None
        for path in (home, '.t3', 'userdata'):
            parent = os.open(path, flags | os.O_DIRECTORY, dir_fd=parent)
            stack.callback(os.close, parent)
            info = os.fstat(parent)
            if info.st_uid != uid or info.st_mode & 0o022:
                raise ValueError('the local T3 runtime directory is not trusted')
        with os.fdopen(os.open('server-runtime.json', flags, dir_fd=parent)) as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
                raise ValueError('the local T3 runtime file is not trusted')
            raw = handle.read(8193)
            if len(raw) > 8192:
                raise ValueError('the local T3 runtime record is too large')
    return json.loads(raw)


def listener_owned_by(pid, address, port):
    """Identify the process listening on this loopback port, including wildcard bindings."""
    try:
        result = subprocess.run(['/usr/sbin/lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-Fpn'],
                                capture_output=True, text=True, check=True, timeout=2, env={'LC_ALL': 'C'})
    except (OSError, subprocess.SubprocessError):
        return False
    owner, owners = None, set()
    for line in result.stdout.splitlines():
        if line.startswith('p'):
            owner = line[1:]
        elif line in (f'n*:{port}', f'n{address}:{port}', f'n[{address}]:{port}'):
            owners.add(owner)
    return owners == {str(pid)}


def local_runtime():
    """Verify the OS account's loopback runtime and its listening process."""
    runtime = runtime_record()
    origin, pid, port = runtime['origin'], runtime['pid'], runtime['port']
    parsed = urlsplit(origin)
    if (type(pid) is not int or pid < 1 or type(port) is not int or not 0 < port <= 65535
            or parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1')
            or parsed.port != port or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('the T3 runtime endpoint is not loopback')
    if not listener_owned_by(pid, parsed.hostname, port):
        raise ValueError('the local T3 listener does not belong to the T3 runtime')
    return origin.rstrip('/')


def local_mcp():
    """Give the host's local MCP endpoint and inherited credential, or refuse the transport."""
    token = os.environ.get('T3_MCP_BEARER_TOKEN')
    if not token:
        raise ValueError('the local T3 read credential is unavailable')
    endpoint = local_runtime() + '/mcp'
    if os.environ.get('T3_MCP_URL') != endpoint:
        raise ValueError('the MCP endpoint does not match the local T3 runtime')
    return endpoint, token


def current_item(thread):
    """Get the only user item for the active T3 turn of the bound thread."""
    import t3_mcp

    endpoint, token = local_mcp()
    args = {'threadId': thread, 'limit': PAGE_LIMIT, 'view': 'messages'}
    snapshot = candidate = None
    seen = set()
    deadline = time.monotonic() + 12
    for _ in range(MAX_PAGES):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        code, answer = t3_mcp.call(endpoint, token, 't3_thread_read', args, min(4, remaining / 3), local=True)
        if code:
            raise ValueError('the local T3 thread read failed')
        owner = answer['thread']
        run = owner['activeRunId']
        if owner['threadId'] != thread or owner['archived'] is not False or not isinstance(run, str) or not run:
            raise ValueError('the T3 read did not identify the bound active run')
        runs = [entry for entry in answer['recentRuns'] if entry.get('runId') == run]
        if len(runs) != 1 or type(owner['itemCount']) is not int:
            raise ValueError('the T3 read did not identify the bound active run')
        identity = (run, runs[0]['requestedAt'], owner['itemCount'])
        if snapshot is not None and snapshot != identity:
            raise ValueError('the active T3 thread changed during pagination')
        snapshot = identity

        before = args.get('beforePosition')
        items = answer['items']
        more, cursor = answer['hasMore'], answer.get('earlierPosition')
        if (not isinstance(items, list) or len(items) > PAGE_LIMIT or type(more) is not bool
                or not items and (more or before is not None)):
            raise ValueError('the T3 pagination is incomplete')
        positions = set()
        for item in items:
            position, item_id = item['position'], item['itemId']
            if (type(position) is not int or position < 0 or position in positions
                    or before is not None and position >= before or not isinstance(item_id, str)
                    or not item_id or item_id in seen):
                raise ValueError('the T3 pagination repeated or skipped its cursor')
            positions.add(position)
            seen.add(item_id)
            if item['runId'] == run and item['type'] == 'user_message':
                if candidate is not None or item.get('sourceThreadId') != thread:
                    raise ValueError('the current T3 user item is absent or ambiguous')
                candidate = item
        if not more:
            if candidate is None:
                raise ValueError('the current T3 user item is absent or ambiguous')
            return runs[0], candidate
        if type(cursor) is not int or cursor != min(positions):
            raise ValueError('the T3 pagination cursor did not match the oldest item')
        args['beforePosition'] = cursor
    raise ValueError('the T3 pagination did not complete within its read limit')


def verify(card, text, action, words, bound_thread, *, run_id=None, revision=0, target=None,
           provider_sessions=(), after=0, consumed=()):
    """Attest a fresh exact response for this card state and action, or request scoped human input."""
    if not bound_thread:
        remedy = 'To authorize this action, confirm a fresh run card in T3.'
        raise WaitingOnUser(f'human intent for {action}: no current T3 thread is bound to this card. {remedy}')
    thread = os.environ.get('T3CODE_THREAD_ID')
    scope = {'card': str(card.resolve()), 'view': canonical_hash(text), 'action': action,
             'thread': bound_thread, 'run_id': run_id, 'revision': revision, 'target': target}
    if provider_sessions:
        scope['provider_sessions'] = provider_sessions
    challenge = canonical_hash(scope)
    expected = f'{words}\nzloop-intent: {challenge}'
    try:
        if not thread or thread != bound_thread:
            raise ValueError('no current T3 thread is bound to this card')
        run, item = current_item(thread)
        requested = datetime.fromisoformat(run['requestedAt'])
        at = requested.timestamp()
        now = time.time()
        item_id = item['itemId']
        if (requested.tzinfo is None or not isinstance(item.get('messageId'), str) or not item['messageId']
                or item.get('textTruncated') is not False or not isinstance(item_id, str) or not item_id or item_id in consumed
                or item['createdBy'] != 'user' or item['creationSource'] not in ORIGINS
                or item['visibility'] != 'local' or item['text'] != expected
                or not max(after, now - FRESH_SECONDS) < at <= now):
            raise ValueError('the current T3 item is not a fresh matching human response')
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
        detail = str(error) if type(error) is ValueError else 'the T3 provenance read is unavailable or incomplete'
        destination = f' for {json.dumps(target, sort_keys=True)}' if target else ''
        if provider_sessions:
            destination += f' with provider sessions {json.dumps(provider_sessions)}'
        raise WaitingOnUser(f'human intent for {action} on {card.resolve()} at revision {revision}{destination} '
                            f'could not be verified ({detail}). Ask the bound T3 user to send exactly:\n{expected}') from None
    return {'thread': thread, 'run': run['runId'], 'item': item_id, 'origin': item['creationSource'], 'at': at,
            'scope': scope, 'challenge': challenge}
