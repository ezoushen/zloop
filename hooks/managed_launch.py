#!/usr/bin/env python3
"""Require a checked managed batch before native worker tools receive their exact assignment packet."""
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'hooks'))
import dispatch_state  # noqa: E402
import run_control  # noqa: E402
import yield_gate  # noqa: E402


def prompts(event):
    name = re.split(r'__|\.', event.get('tool_name', ''))[-1]
    data = event.get('tool_input') or {}
    if name in ('Agent', 'Task'):
        return [(data.get('prompt', ''), name, data)]
    if name in ('spawn_agent', 'delegate_task'):
        return [(data.get('message', data.get('task', '')), name, data)]
    if name == 'followup_task':
        return [(data.get('message', ''), name, data)]
    if name == 'subagent' and (data.get('workflow') or data.get('task') is not None or data.get('tasks') is not None
                              or data.get('action') in ('dispatch', 'resume', 'parallel', 'chain')):
        if isinstance(data.get('tasks'), list):
            return [(task.get('task', ''), name, dict(data, **task)) for task in data['tasks']]
        return [(data.get('task', data.get('message', '')), name, data)]
    return None


def field(prompt, name):
    match = re.search(rf'^{name}:[ \t]*(.+)$', prompt, re.M)
    return match[1].strip() if match else None


def settings(name, data, execution, receipt):
    expected = execution['settings']
    runtime = execution.get('target_runtime') or execution['host']
    native = execution.get('native_dispatch')
    if name == 'delegate_task':
        dispatch_state.require(execution.get('executor_type') == 't3', 'T3 launch requires a selected T3 route')
        target = data.get('target') or {}
        for setting, argument in (('model', 'model'), ('provider', 'providerInstanceId')):
            dispatch_state.require(target.get(argument) == expected.get(setting),
                                   f'T3 {argument} differs from resolved settings')
        if 'effort' in expected:
            options = target.get('options') or {}
            if isinstance(options, list):
                options = {row['id']: row['value'] for row in options}
            option = dispatch_state.native_settings.T3_OPTIONS[runtime]
            dispatch_state.require(options.get(option) == expected['effort'], 'T3 thinking differs from resolved settings')
        if dispatch_state.agent_defs.load()['agents'][execution['agent']]['writes'] != 'none':
            dispatch_state.require(data.get('isolation') == 'worktree', 'T3 writers require worktree isolation')
        return
    actual_runtime = 'pi' if name == 'subagent' else 'claude' if name in ('Agent', 'Task') else 'codex'
    dispatch_state.require(native and runtime == actual_runtime, 'Native tool differs from selected executor/runtime')
    if name == 'subagent':
        model, effort = expected.get('model'), expected.get('effort')
        supplied = data.get('model')
        if model:
            wanted = model + (f':{effort}' if effort else '')
            dispatch_state.require(supplied == wanted, 'Pi model/thinking differs from resolved settings')
        elif effort:
            dispatch_state.require(isinstance(supplied, str) and supplied.endswith(f':{effort}')
                                   and supplied != f'inherit:{effort}', 'Pi thinking needs its verified native model')
        else:
            dispatch_state.require(supplied in (None, 'inherit'), 'Inherited Pi route cannot substitute a model')
        dispatch_state.require(data.get('cwd') and Path(data['cwd']).resolve() == Path(receipt['worktree'])
                               and data.get('worktree') is False, 'Pi must use the assigned worktree with worktree: false')
    else:
        profile = execution.get('native_profile') or {}
        using_profile = name in ('Agent', 'Task') and data.get('subagent_type') == profile.get('name') and profile.get('name')
        if not using_profile:
            dispatch_state.require('model' not in expected or data.get('model') == expected['model'],
                                   'Native model differs from resolved settings')
        if actual_runtime == 'codex':
            dispatch_state.require(data.get('reasoning_effort') == expected.get('effort')
                                   and ('model' in expected or 'model' not in data), 'Codex settings differ from resolved route')
            dispatch_state.require(not expected or data.get('fork_turns') == 'none', 'Explicit Codex settings need fresh context')


def check_event(event):
    tasks = prompts(event)
    if tasks is None:
        return
    session = event.get('session_id') or os.environ.get('CODEX_SESSION_ID') or os.environ.get('ZLOOP_SESSION')
    root = yield_gate.main_checkout(event.get('cwd') or os.getcwd())
    cards = []
    for path in run_control.cards(root) if root else []:
        if not run_control.location(path).exists():
            continue
        view = run_control.observed(path, errors='replace') or ''
        try:
            state = run_control.canonical(path)
        except (OSError, ValueError):
            if session in run_control.sessions(view):
                raise
            continue
        if session in run_control.sessions(state['card_text']) or session in run_control.sessions(view):
            run_control.read(path)
            cards.append(path.resolve())
    if not cards:
        return  # Unmanaged sessions and unrelated sessions keep their native behavior.
    dispatch_state.require(tasks, 'Managed launch has no assignment packets')
    launches, inputs = [], {}
    for task, name, data in tasks:
        dispatch_state.require(not data.get('workflow'),
            'Managed dispatch needs individual routed packets; workflow scripts hide their child launch inputs')
        dispatch_state.require(isinstance(task, str), 'Managed launch requires assignment text')
        card, execution_path = field(task, 'dispatch_card'), field(task, 'execution')
        dispatch_state.require(card and Path(card).resolve() in cards and execution_path and Path(execution_path).is_absolute(),
            'Managed worker needs dispatch_card: and execution: in its routed assignment packet; '
            'complete agent_models.py --route --dispatch-card and dispatch_state.py plan first')
        execution = json.loads(Path(execution_path).read_text())
        receipt = execution.get('dispatch') or {}
        packet = receipt.get('packet')
        probe = name == 'delegate_task' and task.strip() == dispatch_state.startup_probe(card, execution_path, receipt)
        dispatch_state.require(packet and (probe or task.strip() == Path(packet).read_text().strip()),
                               'Launch must send the exact routed assignment packet')
        settings(name, data, execution, receipt)
        launches.append((Path(card).resolve(), receipt['attempt'], execution, packet, receipt['worktree']))
        inputs[(Path(card).resolve(), receipt['attempt'])] = name, data
    dispatch_state.require(len({(row[0], row[1]) for row in launches}) == len(launches),
                           'A native batch cannot launch an attempt twice')
    call = event.get('tool_use_id') or event.get('tool_call_id')
    dispatch_state.require(call, 'Managed launch needs the native tool call ID to prevent duplicate children')
    for card in dict.fromkeys(row[0] for row in launches):
        with dispatch_state.state_files.locked(card):
            # Check the whole native batch before claiming any member, so a rejected sibling consumes nothing.
            selected = [row for row in launches if row[0] == card]
            control = dispatch_state.managed(card)
            saved = dispatch_state.read(card, control)
            claims = {}
            for row in selected:
                item = dispatch_state.checked(*row)
                name, data = inputs[(row[0], row[1])]
                if len(saved['batch']['launch']) > 1:
                    background = (data.get('async') is not False if name == 'subagent' else
                                  data.get('run_in_background') is True if name in ('Agent', 'Task') else
                                  data.get('mode') != 'wait')
                    dispatch_state.require(background, 'Selected independent siblings require background launch calls')
                claims[row[1]] = dispatch_state.claim_receipt(saved, row[1], item, row[2], call)
                dispatch_state.require(row[1] not in {r['attempt'] for r in dispatch_state.snapshot(card)['rows']},
                                       'Attempt already registered; reconcile rather than relaunch')
            saved['claims'].update(claims)
            dispatch_state.save(card, saved)


def main():
    try:
        check_event(json.load(sys.stdin))
        result = {'block': False}
    except Exception as error:
        result = {'block': True, 'reason': f'Managed dispatch: {error}'}
    if sys.argv[1:] == ['check']:
        print(json.dumps(result))
    elif result['block']:
        print(json.dumps({'hookSpecificOutput': dict(hookEventName='PreToolUse', permissionDecision='deny',
                                                    permissionDecisionReason=result['reason'])}))


if __name__ == '__main__':
    main()
