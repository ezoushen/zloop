"""Send a bounded JSON tool request to a T3 MCP server."""
import json
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def call(url, token, tool, arguments, timeout, *, local=False):
    """Return an MCP tool result; local calls bypass proxies and reject redirects."""
    open_request = (urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open
                    if local else urllib.request.urlopen)

    def post(payload, session=None):
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
                   'Authorization': f'Bearer {token}'}
        if session:
            headers['mcp-session-id'] = session
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
        with open_request(request, timeout=timeout) as answer:
            limit = 4 * 1024 * 1024
            raw = answer.read(limit + 1) if local else answer.read()
            if local and len(raw) > limit:
                raise ValueError('T3 MCP response is too large')
            return json.loads(raw), answer.headers.get('mcp-session-id')

    try:
        initialized, session = post({'jsonrpc': '2.0', 'id': 0, 'method': 'initialize',
                                     'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                                                'clientInfo': {'name': 'zloop', 'version': '1.0'}}})
        if local and (initialized.get('id') != 0 or not isinstance(initialized.get('result'), dict)):
            raise ValueError('T3 MCP initialization failed')
        try:
            post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, session)
        except (OSError, ValueError):
            pass
        body, _ = post({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                        'params': {'name': tool, 'arguments': dict(arguments)}}, session)
        if local and (body.get('jsonrpc') != '2.0' or body.get('id') != 1 or 'error' in body):
            raise ValueError('T3 MCP read failed')
        result = body.get('result') or {}
        if result.get('isError'):
            message = (result.get('content') or [{}])[0].get('text', '')
            return 1, {'error': {'code': 'tool', 'message': message[:200]}}
        if isinstance(result.get('structuredContent'), dict):
            return 0, result['structuredContent']
        return 0, json.loads((result.get('content') or [{}])[0].get('text', '{}'))
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ValueError('the T3 MCP credential was refused (expired or revoked): re-run `t3 pair` on '
                             'that environment and approve the zloop client again') from None
        return 1, {'error': {'code': 'http', 'message': f'T3 MCP answered {error.code}'}}
    except (OSError, ValueError, AttributeError, TypeError, KeyError):
        return 1, {'error': {'code': 'unreachable', 'message': 'T3 MCP did not return a readable answer'}}
