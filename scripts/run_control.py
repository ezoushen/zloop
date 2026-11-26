#!/usr/bin/env python3
"""Record supported run-control transitions; legacy cards require explicit adoption."""
import argparse
from contextlib import contextmanager
import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_files  # noqa: E402

ENDED = ('delivered', 'delivered-local', 'merged')
PAUSED = ('waiting-on-user', 'paused-allowance', 'paused-by-user', 'blocked')
TERMINAL_STATES = ENDED + PAUSED
DISPATCH_POLICY = dict(version=1, routing='configured', scheduling='independent-ready',
                       isolation='worktree-per-writer', integration='single-owner', max_live=8)
CARD_NAME = 'autopilot.md'
CONTROL_NAME = 'autopilot-control.json'


def location(card):
    return Path(card).with_name(CONTROL_NAME)


def cards(repo):
    """Find both managed and legacy cards, including an absent managed projection."""
    projections = repo.glob(f'.scratch/*/{CARD_NAME}')
    records = repo.glob(f'.scratch/*/{CONTROL_NAME}')
    return sorted(set(projections) | {path.with_name(CARD_NAME) for path in records})


def present(card):
    return Path(card).exists() or location(card).exists()


def digest(text):
    """Hash UTF-8 text, including any original bytes preserved by surrogateescape."""
    return hashlib.sha256(text.encode(errors='surrogateescape')).hexdigest()


def sessions(text):
    return [part.strip() for part in state_files.state_fields(text).get('session', '').split(',') if part.strip()]


def fields(text, updates):
    """Replace only State fields, adding a missing field in that section."""
    match = re.search(r'^## State[ \t]*\n.*?(?=^## |\Z)', text, re.M | re.S)
    if not match:
        raise ValueError('Card has no State section')
    section = match[0]
    for key, value in updates.items():
        if '\n' in str(value):
            raise ValueError('State values must fit one line')
        line = f'{key}: {value}'.rstrip()
        if re.search(rf'^{re.escape(key)}:', section, re.M):
            section = re.sub(rf'^{re.escape(key)}:.*$', lambda _: line, section, count=1, flags=re.M)
        else:
            section = section.rstrip() + '\n' + line + '\n'
    return text[:match.start()] + section + text[match.end():]


def canonical(card):
    """Read validated canonical data without treating a diverged view as authoritative."""
    state = json.loads(location(card).read_text())
    if (not isinstance(state, dict) or state.get('schema') != 1 or not isinstance(state.get('events'), list)
            or not state['events'] or not all(isinstance(event, dict) for event in state['events'])
            or type(state.get('revision')) is not int or state['revision'] < 1
            or type(state.get('allow_resume')) is not bool or not isinstance(state.get('run_id'), str)
            or type(state.get('created_at')) not in (int, float) or not math.isfinite(state['created_at'])):
        raise ValueError('Unreadable managed control record; reconcile it before continuing')
    if not isinstance(state.get('card_text'), str) or state_files.state_fields(state['card_text']).get('control') != state['run_id']:
        raise ValueError('Unreadable canonical card view')
    if 'pending_view' in state and (not isinstance(state['pending_view'], str)
                                   or not re.fullmatch(r'[0-9a-f]{64}', state['pending_view'])):
        raise ValueError('Unreadable pending card projection')
    return state


def observed(card, *, errors='strict'):
    """Read the exact Markdown bytes as UTF-8, or None when the projection is absent."""
    try:
        return Path(card).read_bytes().decode(errors=errors)
    except FileNotFoundError:
        return None


def projects(state, text):
    return (text is None or text == state['card_text']
            or ('pending_view' in state and digest(text) == state['pending_view']))


def read(card):
    """Read the canonical projection without modifying files, refusing unrelated view edits."""
    card = Path(card)
    try:
        state = canonical(card)
    except FileNotFoundError:
        text = card.read_text()
        if state_files.state_fields(text).get('control'):
            raise ValueError('Managed control record is missing; reconcile it before continuing')
        return None
    if not projects(state, observed(card, errors='surrogateescape')):
        state = canonical(card)
        if projects(state, observed(card, errors='surrogateescape')):
            return state
        raise ValueError('Run card and control record diverged; reconcile the saved transition and manual edits')
    return state


def write_control(card, state):
    state_files.write_atomic(location(card), json.dumps(state, indent=2) + '\n', mode=0o600)


def recover(card, state):
    """Finish publishing a known projection while holding the card's lock."""
    text = observed(card, errors='surrogateescape')
    if not projects(state, text):
        raise ValueError('Run card and control record diverged; reconcile the saved transition and manual edits')
    if text != state['card_text']:
        state_files.write_atomic(Path(card), state['card_text'])
    if 'pending_view' in state:
        del state['pending_view']
        write_control(card, state)


def save(card, state, previous):
    """Commit authority first, then publish its view with a recoverable predecessor."""
    if previous != state['card_text']:
        state['pending_view'] = digest(previous)
    write_control(card, state)
    recover(card, state)


def restore_view(card, expected_view, reference, request_id):
    """Explicitly restore the reviewed canonical view, preserving the displaced manual view."""
    card = Path(card)
    if not reference or not request_id:
        raise ValueError('View reconciliation requires its review reference and request ID')
    with state_files.locked(card):
        state = canonical(card)
        text = observed(card, errors='surrogateescape')
        previous = next((e for e in state['events'] if e['id'] == request_id), None)
        if previous and (previous.get('view_hash'), previous.get('reference')) != (expected_view, reference):
            raise ValueError('Reconciliation request ID already names different input')
        current_hash = digest(text) if text is not None else None
        if previous:
            if not projects(state, text) or (text not in (None, state['card_text']) and current_hash != expected_view):
                raise ValueError('Manual view changed; review it again before reconciliation')
            recover(card, state)
            return state
        if text is None or current_hash != expected_view:
            raise ValueError('Manual view changed; review it again before reconciliation')
        state['revision'] += 1
        state['events'].append(dict(id=request_id, actor='host', reference=reference, view_hash=expected_view,
                                    displaced_view=text, at=time.time()))
        save(card, state, text)
        return state


def adopt(card, reference, allow_resume=False):
    """Explicitly adopt a confirmed card; this does not grant new actions or hook trust."""
    card = Path(card)
    if not reference.strip():
        raise ValueError('Adoption requires the confirmation reference')
    with state_files.locked(card):
        if existing := read(card):
            recover(card, existing)
            if 'dispatch_policy' not in existing:
                existing['dispatch_policy'] = dict(DISPATCH_POLICY)
                existing['revision'] += 1
                existing['events'].append(dict(id='dispatch-policy-v1', actor='host',
                    source={'kind': 'confirmation', 'reference': reference}, at=time.time()))
                save(card, existing, existing['card_text'])
            return existing
        before = observed(card)
        if before is None:
            raise FileNotFoundError(card)
        text = before.replace('\r\n', '\n').replace('\r', '\n')
        values = state_files.state_fields(text)
        if re.match(r'no\b|$', values.get('confirmed', ''), re.I):
            raise ValueError('Only a confirmed card can be adopted')
        now, run = time.time(), uuid.uuid4().hex
        state = dict(schema=1, run_id=run, revision=1, created_at=now, allow_resume=bool(allow_resume),
                     dispatch_policy=dict(DISPATCH_POLICY),
                     card_text=fields(text, {'control': run}),
                     events=[dict(id='adopt', actor='host', source={'kind': 'confirmation', 'reference': reference},
                                  terminal=values.get('terminal', ''), at=now)])
        save(card, state, before)
        return state


TAGGED_COMMAND = re.compile(r'<command-message>zloop:autopilot</command-message>\s*<command-name>/zloop:autopilot'
                            r'</command-name>\s*<command-args>(?P<args>[^<>\r\n]*)</command-args>')


def user_source(path, runtime, sessions, action, after):
    """Find an explicit control command in a bound native user's active conversation."""
    path = Path(path).resolve()
    raw = path.read_text()
    document = json.loads(raw) if runtime in ('opencode', 't3') else None
    rows = document.get('messages', []) if document else [json.loads(line) for line in raw.splitlines()]
    if runtime == 'codex':
        session = next((r.get('payload', {}).get('id') for r in rows if r.get('type') == 'session_meta'), None)
    elif runtime == 'pi':
        session = next((r.get('id') for r in rows if r.get('type') == 'session'), None)
        by_id = {r.get('id'): r for r in rows}
        active, current = [], rows[-1]
        while current.get('type') != 'session':
            active.append(current)
            parent = current.get('parentId')
            if parent is None:
                break
            current = by_id[parent]
        rows = list(reversed(active))
    elif runtime == 'claude':
        session = next((r.get('sessionId') for r in rows if r.get('sessionId')), None)
    elif runtime == 'opencode':
        session = document.get('info', {}).get('id')
    elif runtime == 't3':
        session = (document.get('thread') or document).get('threadId')
    else:
        raise ValueError('Unsupported native control source')
    if session not in sessions:
        raise ValueError('Control source is not bound to this run')
    flag = '--pause' if action == 'pause' else '--take-over'
    extra = r'(?:\s+--auto-merge)?' if action == 'resume' else ''
    pattern = re.compile(r'^(?:[$/]zloop:|/skill:)?autopilot\s+' + flag + extra + r'\s*$')
    for index, row in reversed(list(enumerate(rows))):
        if runtime == 'codex':
            message = row.get('payload', {}) if row.get('type') == 'response_item' else {}
        elif runtime in ('pi', 'claude'):
            message = row.get('message', {})
        else:
            message = row.get('info') or row
        role = row.get('type') if runtime == 'claude' else message.get('role', message.get('type'))
        parts = message.get('content', row.get('parts', row.get('content', row.get('text', ''))))
        text = parts if isinstance(parts, str) else '\n'.join(p.get('text', '') for p in parts
                   if isinstance(p, dict) and p.get('type') in ('text', 'input_text'))
        stamp = row.get('timestamp') or message.get('timestamp') or (message.get('time') or {}).get('created')
        try:
            at = (datetime.datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() if isinstance(stamp, str)
                  else stamp / 1000 if stamp and stamp > 1e11 else float(stamp or 0))
        except (ValueError, TypeError):
            continue
        if runtime == 'claude' and role == 'user':
            tagged = TAGGED_COMMAND.fullmatch(text.strip())
            text = f'/zloop:autopilot {tagged["args"]}' if tagged else text
        if role == 'user' and at >= after and pattern.fullmatch(text.strip()):
            if runtime == 'claude' and (row.get('sessionId') != session or row.get('agentId') or row.get('isSidechain')):
                continue
            if runtime == 'opencode' and message.get('sessionID', session) != session:
                continue
            key = digest(json.dumps([session, row.get('id', index), text, at]))
            return dict(kind='user', native=str(path), runtime=runtime, session=session, command=action,
                        at=at, key=key, reference=f'{path}#{row.get("id", index)}', flags=text.split()[1:])
    raise ValueError(f'No recent explicit user command {flag} in the bound native record')


def user_boundary(state, action):
    """Keep user decisions ordered; a resume must also follow the terminal decision it clears."""
    events = [event for event in state['events'] if event.get('source', {}).get('kind') == 'user'
              or action == 'resume' and event.get('terminal')]
    return max([state['created_at']] + [event.get('source', {}).get('at', event['at']) for event in events])


def transition(card, terminal, source, reason, expected, request_id, updates=None):
    """Commit an idempotent transition at an expected revision, then update its card view."""
    if terminal not in ('', *TERMINAL_STATES) or not reason.strip() or not request_id:
        raise ValueError('Transition requires a known state, reason and request ID')
    card = Path(card)
    request = dict(terminal=terminal, source=source, reason=reason, updates=updates or {})
    if set(updates or {}) - {'timer', 'resume', 'phase', 'session'}:
        raise ValueError('Control updates cannot change grants or criteria')
    with state_files.locked(card):
        state = read(card)
        if state is None:
            raise ValueError('Legacy card: adopt its confirmed state explicitly first')
        recover(card, state)
        previous = next((e for e in state['events'] if e['id'] == request_id), None)
        if previous:
            if previous.get('request') != request:
                raise ValueError('Transition request ID was already used for different input')
            return state
        if state['revision'] != expected:
            raise ValueError('Control revision changed; inspect the current run before retrying')
        old = state_files.state_fields(state['card_text']).get('terminal', '')
        if old == 'paused-by-user' and terminal not in ('', 'paused-by-user'):
            raise ValueError('Run is paused-by-user; a new user resume is required')
        if terminal == 'merged' and state_files.state_fields(state['card_text']).get('auto-merge') != 'yes':
            raise ValueError('Merged state requires the existing merge grant')
        if terminal == '' and source.get('kind') == 'timer':
            proof = timer_source(source['queue'], source['key'])
            event = proof['wake']
            if (source != proof or not state['allow_resume'] or old != 'paused-allowance'
                    or event['request']['run_id'] != state['run_id']
                    or event['request']['control_revision'] != state['revision']):
                raise ValueError('Wake no longer owns this allowance pause')
        elif terminal == 'paused-by-user' or terminal == '':
            action = 'pause' if terminal else 'resume'
            if source.get('kind') != 'user' or source.get('command') != action:
                raise ValueError('Transition requires the bound user command')
            proof = user_source(source['native'], source['runtime'],
                                sessions(state['card_text']),
                                action, math.nextafter(user_boundary(state, action), math.inf))
            if proof != source or any(e.get('source', {}).get('key') == source['key'] for e in state['events']):
                raise ValueError('User command source changed or was already consumed')
        elif source.get('kind') != 'host' or not source.get('reference'):
            raise ValueError('Host transition requires its evidence reference')
        changes = dict(updates or {}, terminal=terminal, evidence=f'{source["reference"]}: {reason}')
        if not terminal:
            changes['auto-merge'] = 'yes' if '--auto-merge' in source.get('flags', []) else 'no'
            history = state_files.state_fields(state['card_text']).get('history', '')
            changes['history'] = '; '.join(filter(None, [history, f'{old} {datetime.date.today().isoformat()}']))
            changes['timer'] = ''
        before = state['card_text']
        state['card_text'] = fields(before, changes)
        state['revision'] += 1
        state['events'].append(dict(id=request_id, actor=source['kind'], source=source,
                                    terminal=terminal, previous=old, reason=reason, at=time.time(), request=request))
        save(card, state, before)
        return state


def require_active(card):
    """Managed paused, ended or unknown runs cannot start/accept productive work."""
    card = Path(card)
    if not present(card):
        return
    state = read(card)
    if state and (terminal := state_files.state_fields(state['card_text']).get('terminal')):
        raise ValueError(f'Run is {terminal}; an authorized resume is required')


@contextmanager
def active_guard(card, enabled=True):
    """Serialize a supported admission/acceptance commit against a control transition."""
    card = Path(card)
    if enabled and present(card):
        with state_files.locked(card):
            require_active(card)
            yield
    else:
        yield


def timer_source(queue, event_id):
    """Read a due, preflighted wake; notification alone cannot grant work or merging."""
    queue = Path(queue).resolve()
    document = json.loads(queue.read_text())
    wake = document['wakes'][event_id]
    checked = wake.get('preflight', {})
    if (document.get('schema') != 1 or wake.get('state') not in ('sending', 'sent', 'uncertain')
            or wake['request']['due'] > time.time() or checked.get('ready') is not True
            or checked.get('allowance') != 'eligible' or checked.get('approval') != 'clear'):
        raise ValueError('Wake is not due, preflighted and in delivery')
    return dict(kind='timer', queue=str(queue), reference=f'{queue}#{event_id}', key=event_id,
                wake={'event_id': event_id, 'request': {k: v for k, v in wake['request'].items() if k != 'config'}})


def phase(card, text, expected):
    """Record a validated phase view without changing grants or user control."""
    card = Path(card)
    with state_files.locked(card):
        state = read(card)
        if not state or state['revision'] != expected:
            raise ValueError('Control revision changed')
        require_active(card)
        recover(card, state)
        previous = state['card_text']
        if text == previous:
            return state
        before, after = state_files.state_fields(previous), state_files.state_fields(text)
        if fields(previous, {'phase': after.get('phase', '')}) != text:
            raise ValueError('Phase update changed fields beyond its phase')
        state['card_text'] = text
        state['revision'] += 1
        state['events'].append(dict(id=uuid.uuid4().hex, actor='host', source={'kind': 'phase'},
                                    previous=before.get('phase'), phase=after.get('phase'), at=time.time()))
        save(card, state, previous)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['adopt', 'status', 'pause', 'resume', 'end', 'restore-view'])
    parser.add_argument('card', type=Path)
    parser.add_argument('--source', help='confirmation or host evidence reference')
    parser.add_argument('--allow-resume', action='store_true', help='record an existing unattended-resume grant')
    parser.add_argument('--native', type=Path)
    parser.add_argument('--runtime', choices=['codex', 'claude', 'pi', 'opencode', 't3'])
    parser.add_argument('--expect', type=int)
    parser.add_argument('--request-id')
    parser.add_argument('--reason')
    parser.add_argument('--terminal', choices=TERMINAL_STATES)
    parser.add_argument('--wake', type=Path)
    parser.add_argument('--event-id')
    parser.add_argument('--expect-view', help='reviewed SHA-256 of a diverged view; restore-view only')
    args = parser.parse_args()
    try:
        if args.command == 'adopt':
            state = adopt(args.card, args.source or '', args.allow_resume)
        elif args.command == 'status':
            state = read(args.card)
        elif args.command == 'restore-view':
            state = restore_view(args.card, args.expect_view, args.source, args.request_id)
        else:
            state = read(args.card)
            if not state:
                raise ValueError('Explicit adoption required')
            source = {'kind': 'host', 'reference': args.source}
            if args.command == 'resume' and args.wake:
                source = timer_source(args.wake, args.event_id)
            elif args.command in ('pause', 'resume'):
                if not args.native or not args.runtime:
                    raise ValueError('User transition needs --native and --runtime')
                source = user_source(args.native, args.runtime,
                                     sessions(state['card_text']),
                                     args.command, state['created_at'])
            terminal = {'pause': 'paused-by-user', 'resume': ''}.get(args.command, args.terminal)
            state = transition(args.card, terminal, source, args.reason or '', args.expect, args.request_id)
        result = {'managed': state is not None}
        if state:
            result.update({key: state[key] for key in ('run_id', 'revision', 'allow_resume')})
            result['dispatch_policy'] = state.get('dispatch_policy')
            result['terminal'] = state_files.state_fields(state['card_text']).get('terminal', '')
            result['last_event'] = {key: state['events'][-1].get(key) for key in ('id', 'actor', 'at')}
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    main()
