"""Read and bind the active T3 user item for a pending control authorization."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from urllib.parse import urlsplit

import t3_mcp


FRESH_SECONDS = 300
ORIGINS = ('web', 'mobile')


class WaitingOnUser(ValueError):
    """The pending action lacks verified user authority."""


def canonical_hash(value):
    """Hash the exact action scope with canonical JSON encoding."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def local_mcp():
    """Give the host's local MCP endpoint and inherited credential, or refuse the transport."""
    token = os.environ.get('T3_MCP_BEARER_TOKEN')
    if not token:
        raise ValueError('the local T3 read credential is unavailable')
    path = Path.home() / '.t3/userdata/server-runtime.json'
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError('the local T3 runtime file is not trusted')
        runtime = json.loads(handle.read(8192))
    origin, pid, port = runtime['origin'], runtime['pid'], runtime['port']
    parsed = urlsplit(origin)
    if (type(pid) is not int or pid < 1 or type(port) is not int or not 0 < port <= 65535
            or parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1')
            or parsed.port != port or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('the T3 runtime endpoint is not loopback')
    os.kill(pid, 0)
    endpoint = origin.rstrip('/') + '/mcp'
    if os.environ.get('T3_MCP_URL') != endpoint:
        raise ValueError('the MCP endpoint does not match the local T3 runtime')
    return endpoint, token


def current_item(thread):
    """Get the only user item for the active T3 turn of the bound thread."""
    endpoint, token = local_mcp()
    code, answer = t3_mcp.call(endpoint, token, 't3_thread_read', {'threadId': thread, 'limit': 100}, 4, local=True)
    if code:
        raise ValueError('the local T3 thread read failed')
    owner = answer['thread']
    run = owner['activeRunId']
    if owner['threadId'] != thread or owner.get('archived') or not isinstance(run, str) or not run:
        raise ValueError('the T3 read did not identify the bound active run')
    runs = [entry for entry in answer['recentRuns'] if entry.get('runId') == run]
    active = [item for item in answer['items'] if item.get('runId') == run and item.get('type') == 'user_message']
    if len(runs) != 1 or len(active) != 1 or active[0].get('sourceThreadId') != thread:
        raise ValueError('the current T3 user item is absent or ambiguous')
    return runs[0], active[0]


def verify(card, text, action, words, sessions, *, run_id=None, revision=0, target=None, after=0, consumed=()):
    """Attest a fresh exact response for this card state and action, or request scoped human input."""
    thread = os.environ.get('T3CODE_THREAD_ID')
    scope = {'card': str(card.resolve()), 'view': canonical_hash(text), 'action': action,
             'thread': thread, 'run_id': run_id, 'revision': revision, 'target': target}
    challenge = canonical_hash(scope)
    expected = f'{words}\nzloop-intent: {challenge}'
    try:
        if not thread or thread not in sessions:
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
        raise WaitingOnUser(f'human intent for {action} on {card.resolve()} at revision {revision}{destination} '
                            f'could not be verified ({detail}). Ask the bound T3 user to send exactly:\n{expected}') from None
    return {'thread': thread, 'run': run['runId'], 'item': item_id, 'origin': item['creationSource'], 'at': at,
            'scope': scope, 'challenge': challenge}
