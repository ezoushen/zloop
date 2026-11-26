#!/usr/bin/env python3
"""Validate managed route receipts and dispatch passes; this does not launch or schedule workers."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_defs  # noqa: E402
import front_matter  # noqa: E402
import native_settings  # noqa: E402
import run_control  # noqa: E402
import state_files  # noqa: E402


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def files(paths):
    return {str(Path(path).resolve()): hashlib.sha256(Path(path).read_bytes()).hexdigest()
            if Path(path).is_file() else None for path in paths}


def evidence(paths):
    require(isinstance(paths, list) and paths and all(isinstance(p, str) and Path(p).is_absolute()
            and Path(p).is_file() and Path(p).stat().st_size for p in paths),
            'Evidence requires nonempty absolute files from the capacity/authorization checks')
    return files(paths)


def location(card):
    return Path(card).with_name('dispatch-state.json')


def managed(card, active=True):
    if not run_control.present(card):
        return None
    state = run_control.read(card)
    if state:
        require(state.get('dispatch_policy') == run_control.DISPATCH_POLICY,
                'Managed dispatch policy missing or unsupported; re-adopt with the existing confirmation reference')
        if active:
            run_control.require_active(card)
    return state


def read(card, control):
    path = location(card)
    if not path.exists():
        return dict(run_id=control['run_id'], routes={}, claims={})
    saved = json.loads(path.read_text())
    require(isinstance(saved, dict) and saved.get('run_id') == control['run_id']
            and isinstance(saved.get('routes'), dict) and isinstance(saved.get('claims'), dict),
            'Unreadable managed dispatch state; reconcile it before launching')
    return saved


def save(card, state):
    state_files.write_atomic(location(card), json.dumps(state, indent=2) + '\n', mode=0o600)


def routing_session(card, requested=None):
    """Keep the run's routing configuration scope when a fresh host resumes it."""
    control = managed(card)
    require(control, 'Managed routing requires an adopted card')
    saved = read(card, control)
    bound = saved.get('routing_session')
    require(not bound or requested is None or requested == bound,
            f'Managed routing uses session {bound}; keep that scope or start a newly confirmed run')
    chosen = bound or requested or next(iter(run_control.sessions(control['card_text'])), None)
    require(chosen, 'Managed routing requires its bound root session')
    return chosen


def issue_route(card, repo, attempt, packet, worktree, execution, configuration):
    """Called only on the resolver's completed result, after overrides and launch controls are validated."""
    with state_files.locked(card):
        control = managed(card)
        require(control, 'Route receipts require an explicitly adopted managed card')
        require(execution.get('status') != 'selection_required',
                'selection_required: complete routing before issuing a managed route receipt')
        require(execution.get('job') and isinstance(execution.get('settings'), dict), 'Incomplete resolved route')
        require(attempt and Path(packet).is_absolute() and Path(packet).is_file()
                and Path(worktree).is_absolute() and Path(worktree).is_dir(),
                'Managed routing requires assignment-id, absolute assignment packet and worktree')
        require(native_settings.has_attempt(Path(packet).read_text(), attempt), 'Assignment packet must name its attempt')
        execution = {key: value for key, value in execution.items() if key != 'dispatch'}
        receipt = dict(token=uuid.uuid4().hex, run_id=control['run_id'], revision=control['revision'], card=str(Path(card).resolve()),
                       repo=str(Path(repo).resolve()), attempt=attempt, packet=str(Path(packet).resolve()),
                       worktree=str(Path(worktree).resolve()), packet_hash=files([packet]),
                       configuration=files(configuration), execution_hash=digest(execution))
        saved = read(card, control)
        session = (execution.get('session') or {}).get('id')
        saved['routing_session'] = routing_session(card, session)
        previous = saved['routes'].get(attempt)
        if previous and {k: v for k, v in previous.items() if k != 'token'} == {
                k: v for k, v in receipt.items() if k != 'token'}:
            receipt = previous
        saved['routes'][attempt] = receipt
        save(card, saved)
        return dict(execution, dispatch=receipt)


def route(card, control, saved, attempt, execution, packet, worktree, admitted=False):
    receipt = saved['routes'].get(attempt)
    require(receipt and execution.get('dispatch') == receipt, 'Managed launch requires a recorded route receipt')
    require(receipt['run_id'] == control['run_id'] and (admitted or receipt['revision'] == control['revision']),
            'Managed route is stale after a control transition; resolve routing again')
    require(receipt['execution_hash'] == digest({k: v for k, v in execution.items() if k != 'dispatch'}),
            'Resolved route was changed; resolve routing again instead of substituting settings')
    require(receipt['packet'] == str(Path(packet).resolve()) and receipt['packet_hash'] == files([packet]),
            'Assignment packet changed after routing; resolve routing again')
    require(receipt['worktree'] == str(Path(worktree).resolve()), 'Worktree differs from the resolved route')
    require(receipt['configuration'] == files(receipt['configuration']),
            'Routing configuration changed; resolve routing again without widening pins')
    return receipt


def key(row):
    return f'{row["job"]}:{row["assignment"]}'


def covers(rows):
    return {name for row in rows for name in (key(row), f'ticket:{row["ticket"]}' if row.get('ticket') else key(row))}


def snapshot(card, db=None):
    """Read existing lifecycle plans/outcomes and board files, without constructing a second scheduler."""
    state = Path(card).parent / 'reports/workers.sqlite'
    if db is None and state.exists():
        with closing(sqlite3.connect(f'{state.resolve().as_uri()}?mode=ro', uri=True, timeout=5)) as connection:
            return snapshot(card, connection)
    rows, planned = [], set()
    if db is not None:
        rows = [json.loads(row[0]) for row in db.execute('SELECT data FROM attempts ORDER BY id')]
        planned = {f'{job}:{name}' for job, names in db.execute('SELECT job, assignments FROM plans')
                   for name in json.loads(names)}
    board = sorted(Path(card).parent.glob('issues/*.md'))
    done = {key(row) for row in rows if row.get('result', {}).get('status') == 'completed'}
    tickets = {}
    for path in board:
        fm = front_matter.parse(path.read_text()) or {}
        require(fm.get('ticket') and fm.get('status') and isinstance(fm.get('blocked_by'), list)
                and isinstance(fm.get('touches'), list), f'Unreadable board ticket: {path}')
        tickets[fm['ticket']] = fm
        (done if fm['status'] == 'done' else planned).add(f'ticket:{fm["ticket"]}')
    outcomes = {row['attempt']: [row.get('result'), row.get('accepted'), row.get('declined')]
                for row in rows if row.get('result') or row.get('accepted') or row.get('declined')}
    return dict(rows=rows, planned=sorted(planned), done=sorted(done), outcomes=outcomes, board=files(board), tickets=tickets)


def common_dir(path):
    result = subprocess.run(['git', '-C', str(path), 'rev-parse', '--path-format=absolute', '--git-common-dir',
                             '--show-toplevel'], capture_output=True, text=True, timeout=5)
    require(result.returncode == 0, f'Writer needs an existing git worktree: {path}')
    common, root = result.stdout.strip().splitlines()
    require(Path(root).resolve() == Path(path).resolve(), f'Writer path must be its worktree root: {path}')
    return str(Path(common).resolve())


def conflicts(left, right):
    return left.get('writes') and right.get('writes') and (
        '*' in left['touches'] or '*' in right['touches'] or set(left['touches']) & set(right['touches']))


def plan(card, request, *, expected_revision):
    """Check a complete dispatch decision against known work and retain the policy across host turns."""
    with state_files.locked(card):
        control = managed(card)
        require(control and control['revision'] == expected_revision, 'Dispatch plan requires current managed revision')
        saved, current = read(card, control), snapshot(card)
        items = request.get('assignments')
        require(isinstance(items, list), 'Dispatch plan requires assignments')
        definitions = agent_defs.load()
        indexed = {}
        for item in items:
            require(isinstance(item, dict) and all(isinstance(item.get(f), str) and item[f]
                    for f in ('assignment', 'attempt', 'job')), 'Each dispatch assignment needs job, assignment and attempt')
            require(item['job'] in definitions['jobs'] and item['attempt'] not in indexed, 'Invalid or duplicate dispatch assignment')
            item = dict(item)
            if 'ticket' in definitions['jobs'][item['job']]['inputs'] and item.get('packet'):
                ticket = agent_defs.input_value(definitions['jobs'][item['job']], Path(item['packet']).read_text(), 'ticket')
                require(not item.get('ticket') or item['ticket'] == ticket, 'Batch ticket differs from assignment packet')
                item['ticket'] = ticket
            item['writes'] = definitions['agents'][definitions['jobs'][item['job']]['agent']]['writes'] != 'none'
            require(all(isinstance(item.get(f), list) and all(isinstance(x, str) and x for x in item[f])
                        for f in ('depends_on', 'touches')) and (not item['writes'] or item['touches']),
                    'Assignments need depends_on and touches; a writer must name its write areas')
            indexed[item['attempt']] = item
            board_ticket = current['tickets'].get(item.get('ticket'))
            if board_ticket:
                item['depends_on'] = sorted(set(item['depends_on']) | {f'ticket:{dep}' for dep in board_ticket['blocked_by']})
                item['touches'] = sorted(set(item['touches']) | set(board_ticket['touches']))
                require(board_ticket['status'] != 'blocked' or item['attempt'] not in request.get('launch', []),
                        'Blocked board ticket cannot launch; reconcile its blocker through the board')
        require(len({key(item) for item in indexed.values()}) == len(indexed), 'Duplicate assignment in dispatch pass')
        active = [dict(row.get('dispatch_assignment') or dict(job=row['job'], assignment=row['assignment'],
                      attempt=row['attempt'], writes=True, touches=['*'], worktree=row.get('worktree')))
                  for row in current['rows'] if not row.get('result')]
        previous = [*saved.get('batch', {}).get('assignments', {}).values(), *saved.get('batch', {}).get('active', [])]
        known = set(current['planned']) | {key(row) for row in previous}
        missing = known - set(current['done']) - covers(active) - covers(indexed.values())
        require(not missing, 'Dispatch plan omitted known assignments: ' + ', '.join(sorted(missing)))
        chosen, deferred = request.get('launch'), request.get('deferred')
        require(isinstance(chosen, list) and len(chosen) == len(set(chosen)) and isinstance(deferred, dict)
                and set(chosen).isdisjoint(deferred) and set(chosen) | set(deferred) == set(indexed),
                'Dispatch plan has unexplained assignments: choose or justify every assignment')
        capacity = request.get('capacity') or {}
        limit = capacity.get('limit')
        require(type(limit) is int and 0 <= limit <= control['dispatch_policy']['max_live'], 'Invalid verified capacity limit')
        proof = evidence(capacity.get('evidence'))
        require(len(chosen) <= max(0, limit - len(active)), 'Selected workers exceed verified capacity')
        require(isinstance(request.get('decomposition'), str) and request['decomposition'].strip()
                and request.get('integration_owner') in run_control.sessions(control['card_text']),
                'Dispatch requires a decomposition assessment and one bound integration owner')
        occupied = active + [indexed[name] for name in chosen]
        available = {name: row for row in occupied for name in covers([row])}
        for name, item in indexed.items():
            pending = set(item['depends_on']) - set(current['done'])
            require(not pending - (known | covers(indexed.values()) | set(available)),
                    'Assignment names unknown dependencies')
            if name in chosen:
                require(not pending, f'{name}: dependency is not complete')
                execution = json.loads(Path(item['execution']).read_text())
                receipt = route(card, control, saved, name, execution, item['packet'], item['worktree'])
                require(execution['job'] == item['job'], 'Batch job differs from resolved route')
                item['repo'] = receipt['repo']
                require(not any(conflicts(item, other) for other in occupied if other['attempt'] != name),
                        f'{name}: write conflict with selected or active work')
                require(name not in {row['attempt'] for row in current['rows']}, 'Attempt already registered')
                continue
            reason = deferred[name]
            require(isinstance(reason, dict), f'{name}: invalid deferral')
            kind, because = reason.get('kind'), reason.get('because')
            if kind == 'capacity':
                require(len(occupied) >= limit, f'{name}: capacity does not explain serialization')
            elif kind == 'dependency':
                require(isinstance(because, list) and because and set(because) <= pending,
                        f'{name}: dependency deferral lacks a real unfinished prerequisite')
            elif kind == 'conflict':
                require(isinstance(because, list) and because and all(dep in available and conflicts(item, available[dep])
                        for dep in because), f'{name}: conflict deferral lacks a shared write area')
            elif kind in ('restriction', 'availability'):
                proof.update(evidence(reason.get('evidence')))
            else:
                raise ValueError(f'{name}: unexplained serialization; use a dependency, conflict, capacity, restriction or availability with evidence')
        writers = [row for row in occupied if row.get('writes')]
        if len(writers) > 1:
            roots = [str(Path(row['worktree']).resolve()) for row in writers]
            require(len(set(roots)) == len(roots), 'Concurrent writers require separate worktrees')
            for row in writers:
                require(common_dir(row['worktree']) == common_dir(row.get('repo') or
                        saved['routes'][row['attempt']]['repo']), 'Writer worktree belongs to a different repository')
        batch = dict(revision=control['revision'], assignments=indexed, launch=chosen, deferred=deferred,
                     active=active,
                     routes={name: saved['routes'][name]['token'] for name in chosen},
                     capacity=capacity, evidence=proof, decomposition=request['decomposition'],
                     integration_owner=request['integration_owner'], base_attempts=[r['attempt'] for r in current['rows']],
                     snapshot={k: current[k] for k in ('planned', 'outcomes', 'board')})
        saved['batch'] = batch
        save(card, saved)
        return batch


def current_batch(card, control, saved, db=None):
    batch = saved.get('batch')
    require(batch, 'Managed launch requires a checked dispatch batch; assess approved independent work first')
    now = snapshot(card, db)
    require(batch['revision'] == control['revision'] and batch['snapshot'] == {
            k: now[k] for k in ('planned', 'outcomes', 'board')}
            and batch['routes'] == {name: saved['routes'].get(name, {}).get('token') for name in batch['launch']}
            and set(row['attempt'] for row in now['rows']) <= set(batch['base_attempts']) | set(batch['launch'])
            and batch['evidence'] == files(batch['evidence']),
            'Dispatch state changed; re-evaluate the batch after completion, resume or capacity/configuration changes')
    return batch, now


def check(card, attempt, execution, packet, worktree, *, call_id=None, db=None, admitted=False):
    """Validate before native launch and again under lifecycle admission's transaction/control guard."""
    if call_id:
        with state_files.locked(card):
            require(not admitted, 'A native launch cannot consume an existing admission')
            return checked(card, attempt, execution, packet, worktree, call_id=call_id, db=db)
    return checked(card, attempt, execution, packet, worktree, db=db, admitted=admitted)


def checked(card, attempt, execution, packet, worktree, *, call_id=None, db=None, admitted=False):
    control = managed(card)
    if not control:
        return None
    saved = read(card, control)
    registered = None
    if admitted:
        registered = next((row for row in snapshot(card, db)['rows'] if row['attempt'] == attempt), None)
        require(registered and registered.get('dispatch_assignment') and not registered.get('result')
                and not registered.get('submitted') and registered.get('execution_snapshot') == execution
                and Path(registered['packet']).resolve() == Path(packet).resolve()
                and Path(registered['worktree']).resolve() == Path(worktree).resolve(),
                'Productive submission requires its existing, unchanged, unsubmitted managed admission')
    route(card, control, saved, attempt, execution, packet, worktree, admitted=admitted)
    batch, now = current_batch(card, control, saved, db)
    if registered:
        require(attempt in set(batch['base_attempts']) | set(batch['launch']),
                'Re-evaluated batch does not account for the admitted worker')
        return registered['dispatch_assignment']
    require(attempt in batch['launch'], 'Attempt was not selected in the checked dispatch batch')
    item = batch['assignments'][attempt]
    require(str(Path(packet).resolve()) == str(Path(item['packet']).resolve())
            and str(Path(worktree).resolve()) == str(Path(item['worktree']).resolve()), 'Launch differs from batch assignment')
    if call_id:
        require(attempt not in {row['attempt'] for row in now['rows']}, 'Attempt already registered; reconcile rather than relaunch')
        require(saved['claims'].get(attempt, call_id) == call_id,
                'Attempt already launched; reconcile the existing child before releasing its launch claim')
        saved['claims'][attempt] = call_id
        save(card, saved)
    return item


def startup_probe(card, execution_path, receipt):
    """A startup-only T3 turn; productive submission still requires the registered packet and verified settings."""
    return (f'dispatch_card: {Path(card).resolve()}\nexecution: {Path(execution_path).resolve()}\n'
            f'attempt: {receipt["attempt"]}\n'
            'Startup only. Report provider, model, working directory and runtime mode. Do no productive work.')


def gaps(card):
    control = managed(card, active=False)
    state = state_files.state_fields(control['card_text']) if control else {}
    if not control or state.get('terminal'):
        return []
    saved = read(card, control)
    if state.get('phase') != 'dispatch':
        batch = saved.get('batch')
        if not batch:
            return []
        finished = {row['attempt'] for row in snapshot(card)['rows'] if row.get('result')}
        if set(batch['launch']) <= finished:
            return []
    try:
        batch, now = current_batch(card, control, saved)
        registered = {row['attempt'] for row in now['rows']}
        return [f'{name}: selected ready assignment has not been registered; launch it before yielding'
                for name in batch['launch'] if name not in registered]
    except ValueError as error:
        return [str(error)]


def inspect(card):
    """Read current facts and a request template; no admission, claims or lifecycle files are written."""
    control = managed(card, active=False)
    if not control:
        return {'managed': False}
    saved, now = read(card, control), snapshot(card)
    active = [row for row in now['rows'] if not row.get('result')]
    previous = saved.get('batch') or {}
    known = set(now['planned']) | {key(row) for row in [*previous.get('assignments', {}).values(),
                                                      *previous.get('active', [])]}
    problems = gaps(card)
    registered = {row['attempt'] for row in now['rows']}
    for attempt in previous.get('launch', []):
        if attempt in registered:
            continue
        try:
            item = previous['assignments'][attempt]
            route(card, control, saved, attempt, json.loads(Path(item['execution']).read_text()),
                  item['packet'], item['worktree'])
        except (OSError, ValueError, KeyError, TypeError) as error:
            problems.append(f'{attempt}: {error}')
    return dict(revision=control['revision'], policy=control['dispatch_policy'],
                state=state_files.state_fields(control['card_text']), routing_session=saved.get('routing_session'),
                known_unfinished=sorted(known - set(now['done']) - covers(active)),
                active=[{field: row.get(field) for field in ('attempt', 'job', 'assignment', 'worktree')}
                        for row in active],
                batch={field: previous.get(field) for field in ('launch', 'deferred', 'capacity', 'integration_owner')},
                claims=sorted(saved['claims']), gaps=problems,
                input_template=dict(assignments=[], launch=[], deferred={}, capacity=dict(limit=None, evidence=[]),
                                    decomposition='', integration_owner=next(iter(run_control.sessions(control['card_text'])), None)),
                assignment_template=dict(job='', assignment='', attempt='', depends_on=[], touches=[],
                                         packet='', execution='', worktree=''),
                deferral_forms=dict(dependency=dict(kind='dependency', because=[]),
                                    conflict=dict(kind='conflict', because=[]), capacity=dict(kind='capacity'),
                                    restriction=dict(kind='restriction', evidence=[]),
                                    availability=dict(kind='availability', evidence=[])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'status', 'inspect', 'release', 'probe'))
    parser.add_argument('--card', required=True, type=Path)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--expected-revision', type=int)
    parser.add_argument('--attempt')
    parser.add_argument('--execution', type=Path)
    parser.add_argument('--evidence', action='append')
    args = parser.parse_args()
    try:
        if args.action == 'plan':
            require(args.input, 'plan requires --input')
            result = plan(args.card, json.loads(args.input.read_text()), expected_revision=args.expected_revision)
        elif args.action == 'inspect':
            result = inspect(args.card)
        elif args.action == 'probe':
            require(args.execution and args.execution.is_absolute(), 'probe requires absolute --execution')
            execution = json.loads(args.execution.read_text())
            receipt = execution.get('dispatch') or {}
            check(args.card, receipt['attempt'], execution, receipt['packet'], receipt['worktree'])
            result = {'task': startup_probe(args.card, args.execution, receipt)}
        elif args.action == 'release':
            with state_files.locked(args.card):
                control = managed(args.card)
                require(control and control['revision'] == args.expected_revision, 'Release requires current managed revision')
                saved, now = read(args.card, control), snapshot(args.card)
                require(args.attempt in saved['claims'] and args.attempt not in {r['attempt'] for r in now['rows']},
                        'Only an unregistered, reconciled failed launch claim can be released')
                saved.setdefault('released', []).append(dict(attempt=args.attempt,
                    call_id=saved['claims'].pop(args.attempt), evidence=evidence(args.evidence)))
                save(args.card, saved)
                result = {'released': args.attempt}
        else:
            control = managed(args.card, active=False)
            result = dict(policy=control['dispatch_policy'], **read(args.card, control), gaps=gaps(args.card)) if control else {'managed': False}
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    main()
