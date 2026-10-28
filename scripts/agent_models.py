#!/usr/bin/env python3
"""Resolve worker choices; session state is written only by explicit save/reset options."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import time
import agent_defs
import allowance
import capacity
import decisions
import journal_store
import outcomes
from executor_config import (read_executors, read_session, reset_session, save_session, session_path,
                             validate_execution_config, validate_schema, validate_settings)

from decisions import systemone_request


ROOT = Path(__file__).resolve().parents[1]
HOSTS = ('claude', 'codex', 'pi', 'opencode')
# Herdr is zloop's built-in worker provider: in a Herdr session every runtime is reachable through it, and a host whose
# own delegation would come from a third-party extension (Pi) prefers it. Its procedure is hosts/herdr.md.
BUILT_IN_ROUTES = {'herdr': {'type': 'herdr'}}
BUILT_IN_TYPES = ('herdr',)
EXTENSION_DELEGATION = ('pi',)
DEFINITIONS = agent_defs.load()
AGENTS = tuple(sorted(DEFINITIONS['agents']))
JOBS = DEFINITIONS['jobs']
DEFAULT_ROUTER = {'type': 'host'}


def check_thinking(policy, value, *, automatic=False):
    baseline = policy.get('thinking')
    if isinstance(baseline, dict):
        if value not in baseline['allowed']:
            raise ValueError(f'thinking {value!r} is outside allowed {baseline["allowed"]}')
    elif automatic and (policy.get('mode', 'inherit') != 'override'
                        or (value != baseline and not policy.get('allow_thinking_override', False))):
        raise ValueError('agent does not allow this automatic thinking override')


QUALITY_SIGNALS = {'review_defects': 'review defects', 'repair_rounds': 'repair rounds',
                   'reverts': 'reverts', 'owner_defects': 'owner-found defects'}


def state_prefix(agent, job):
    """The decision state's first lines, which outcome history and the outcome ledger read back."""
    return f'Agent: {agent}\nJob: {job}\n'


def outcome_history(trace, agent, job):
    """Summarize earlier worker outcomes in this journal: per actual model/effort for the agent's job, plus gaps."""
    path = Path(trace['path']) if trace else None
    if path is None or not path.is_file():
        return [], 0
    rows = journal_store.read(path)
    latest = {}
    for row in rows:
        if row['event'] in ('execution', 'outcome'):
            latest.setdefault(row['decision_id'], {})[row['event']] = row
    counts, signals, missing = {}, {}, 0
    for row in rows:
        if row['event'] != 'decision' or row['request'].get('family') != 'worker-selection':
            continue
        if row.get('assignment') == trace.get('assignment'):
            continue
        events = latest.get(row['decision_id'], {})
        if 'outcome' not in events:
            missing += 1
        if not row['request'].get('state', '').startswith(state_prefix(agent, job)) or 'execution' not in events:
            continue
        observed = events['execution'].get('observed') or {}
        key = ' '.join(str(observed[k]) for k in ('model', 'effort') if observed.get(k))
        status = events.get('outcome', {}).get('status', 'no outcome')
        counts.setdefault(key, {}).setdefault(status, 0)
        counts[key][status] += 1
        # Quality signals attributed after acceptance; elapsed time is not one.
        for metric, value in events.get('outcome', {}).get('metrics', {}).items():
            if metric in QUALITY_SIGNALS:
                signals.setdefault(key, {}).setdefault(metric, 0)
                signals[key][metric] += value
    lines = []
    for key, statuses in sorted(counts.items()):
        quality = [f'{QUALITY_SIGNALS[metric]}: {value:g}' for metric, value in signals.get(key, {}).items() if value]
        lines.append(f'- {key}: ' + ', '.join(f'{n} {status}' for status, n in sorted(statuses.items()))
                     + ('; ' + ', '.join(quality) if quality else ''))
    return lines, missing


def recorded_state(trace):
    """The request state of this assignment's earlier worker-selection decision, if any."""
    path = Path(trace['path']) if trace else None
    if path is None or not path.is_file():
        return None
    for row in journal_store.read(path):
        if (row['event'] == 'decision' and row['request'].get('family') == 'worker-selection'
                and all(row.get(key) == trace.get(key) for key in ('repo', 'session', 'assignment'))):
            return row['request'].get('state', '')
    return None


def decision_model_selection(router, candidates, definitions, agent, job, host, assignment, task, *, decision_policy=None, context=None, eligible_thinking=None, trace=None, excluded=None, failure=None):
    """Score permitted model/effort pairs together; the service never supplies launch settings."""
    if not isinstance(assignment, str) or not assignment.strip() or len(assignment) > 16000:
        raise ValueError('Decision model requires a nonempty assignment of at most 16000 characters')
    options, criteria = {}, {}
    for name, instance in candidates.items():
        policy = instance.get('models', {'mode': 'inherit'})
        baseline = policy.get('thinking')
        if policy['mode'] == 'inherit' and task:
            continue
        if 'model' in task and task['model'] != policy.get('model'):
            continue
        levels = baseline['allowed'] if isinstance(baseline, dict) else [baseline]
        if 'effort' in task:
            try:
                check_thinking(policy, task['effort'])
            except ValueError:
                continue
            levels = [task['effort']]
        for level in levels:
            if name in (eligible_thinking or {}) and level not in eligible_thinking[name]:
                continue
            key = f'option_{len(options)}'
            options[key] = {'candidate': name, **({'thinking': level} if level is not None else {})}
            route = definitions.get(name, {})
            criteria[key] = json.dumps({
                **options[key], 'model': policy.get('model', 'inherited'),
                'runtime': route.get('target_runtime', 'executor-defined'),
                'route': route.get('executor_context', instance['executor']['type']),
                'task_fit': instance.get('description', 'No task-fit description configured.')})
    if not options:
        raise ValueError('No permitted model/thinking pairs remain for the explicit task settings')
    if len(options) > 254:
        raise ValueError('System One permits at most 254 worker options plus abstention; narrow the eligible pool')
    decision_policy = decision_policy or {'backend': {key: value for key, value in router.items()
                                                      if key not in decisions.ROUTING_KEYS},
                        'enabled': ['worker-selection']}
    if len(options) == 1:
        # Nothing to choose: settle without inference and keep the phase accounted for.
        reason = 'Only one permitted model/thinking option remains.'
        record = {'status': 'skipped', 'family': 'worker-selection', 'choice': None, 'warnings': [],
                  'skip_reason': 'settled', 'reason': reason}
        if trace is not None:
            record = decisions.checkpoint(decision_policy, 'worker-selection', None, context=context, trace=trace,
                                          skip='settled', reason=reason,
                                          evidence=['agent-models:option_0 ' + criteria['option_0']])
        record['proposer'] = 'resolver'
        record['options'] = options
        return dict(options['option_0'], reason=reason), record
    instructions = ('Choose one worker and thinking setting to complete the assignment efficiently. '
                    'Use the supplied task-fit descriptions and evidence, considering correctness, '
                    'repair/review cost and runtime startup. Honor the assignment\'s user priorities, '
                    'including remaining allowance and optional work. For well-understood work with '
                    'reliable failure checks, prefer the lightest adequate pair. Do not infer subscription '
                    'credits from token counts or treat synthetic scores as measured completion quality. '
                    'Avoid guessing model capabilities. '
                    'Abstain if the evidence is insufficient.')
    history, missing = outcome_history(trace, agent, job)
    heading = f'Earlier {agent} {job} outcomes in this journal (actual model and effort):\n'
    history = heading + '\n'.join(history) + '\n' if history else ''
    previous = recorded_state(trace)
    if previous is not None:
        # A resume reuses the history its decision first saw, so the checkpoint key stays stable.
        history = previous[previous.index(heading):previous.index('Assignment:\n')] if heading in previous else ''
    state = state_prefix(agent, job) + f'Caller: {host}\n'
    if failure:
        # Before the history, which a resume reuses from the first request.
        state += f'Earlier attempt failed: {failure}\n'
    if excluded:
        state += 'Configured candidates excluded by the host:\n' + ''.join(
            f'- {name}: {reason}\n' for name, reason in excluded.items())
    state += history
    request = {'family': 'worker-selection',
               'state': state + f'Assignment:\n{assignment}',
               'instruction': instructions, 'options': criteria}
    if trace is None:
        record = decisions.decide(decision_policy, request, context=context, transport=systemone_request)
    else:
        record = decisions.checkpoint(decision_policy, 'worker-selection', request, context=context,
                                      transport=systemone_request, trace=trace)
    record['options'] = options
    if missing:
        record.setdefault('warnings', []).append({'code': 'missing_outcomes', 'message': (
            f'{missing} earlier worker decision(s) in this journal have no outcome; record them with '
            'decision_journal.py outcome when their work is accepted or rejected.')})
    if record['status'] != 'recommended':
        return None, record
    return dict(options[record['choice']],
                reason=f'System One selected {record["choice"]} using {record["model"]}.'), record


# Shadow scoring: what a worker spends, as a weight against its chance of acceptance. A subscription's
# weight rises with its provider's allowance pressure; an unknown reading keeps the base weight.
COSTS = {'subscription': 1.0, 'free': 0.0, 'local': 0.0}
COST_WEIGHT = 0.15
PRESSURE_WEIGHTS = {'normal': COST_WEIGHT, 'elevated': 0.4, 'critical': 1.0}


def allowance_levels():
    """Each subscription provider's pressure level; a reader failure leaves every provider unknown."""
    try:
        readings = allowance.status(allowance.codex_sessions(), time.time(), persist=False)['providers']  # Routing stays read-only.
        return {name: value['level'] for name, value in readings.items()}
    except (OSError, ValueError, KeyError) as error:
        return {'error': str(error)}


CRITICAL = ('{provider} allowance is critical; a subscription candidate on it is excluded unless a configuration '
            'layer sets include_critical_allowance')
TRAINING = 'Model trains on prompts; this repository does not allow training endpoints (allow_training)'


def shadow_selection(names, catalog, agent, job, seed, levels=None):
    """What a cost-aware policy would choose: per candidate, a Thompson sample of its acceptance rate in this
    agent's recorded outcomes for this job, less the cost weight for subscription spend. It never changes the route."""
    try:
        counts = outcomes.summary(agent, job).get(agent, {}).get(job, {})
    except (OSError, ValueError, sqlite3.Error) as error:
        return {'status': 'unavailable', 'error': str(error)}
    scored = {}
    for name in names:
        definition = catalog[name][0] if name in catalog else {}
        # Attempts registered without a candidate name are known by their model ID.
        known = {name} | {binding['id'] for binding in definition.get('runtimes', {}).values()}
        successes = sum(counts.get(key, {}).get('successes', 0) for key in known)
        failures = sum(counts.get(key, {}).get('failures', 0) for key in known)
        cost = definition.get('cost')
        draw = random.Random(f'{seed}|{name}').betavariate(1 + successes, 1 + failures)
        scored[name] = {'cost': cost or 'unknown', 'successes': successes, 'failures': failures,
                        'mean': round((1 + successes) / (2 + successes + failures), 3), 'sample': round(draw, 3),
                        'utility': round(draw - weight(names[name], levels or {}) * COSTS.get(cost, 1.0), 3)}
    choice = max(scored, key=lambda name: scored[name]['utility']) if scored else None
    return {'policy': 'thompson-cost-v1', 'cost_weight': COST_WEIGHT, 'allowance': levels or {},
            'candidates': scored, 'choice': choice}


def backend_unavailable(definition, backends, checked=None):
    """Why a model's local backend cannot take a worker now (no free slot, unhealthy, short of memory), or None.
    `checked` keeps each backend's answer for the rest of one route."""
    backend = definition.get('backend')
    if backend not in backends:
        return None
    if checked is not None:
        if backend not in checked:
            checked[backend] = backend_unavailable(definition, backends)
        return checked[backend]
    try:
        held = capacity.in_use(backend)
        slots = backends[backend]['slots']
        if held >= slots:
            return f'Backend {backend} is at capacity ({held}/{slots})'
        capacity.check(backend, backends[backend])
    except ValueError as error:
        return str(error)
    except (OSError, sqlite3.Error):
        return None  # The lease is still checked when the worker registers.
    return None


def allowance_refusal(unavailable):
    """Refuse a job left with no eligible candidate when an allowance exclusion was among the reasons."""
    reasons = {name: reason for name, reason in unavailable.items() if reason.endswith('include_critical_allowance')}
    if reasons:
        raise ValueError('No eligible candidates remain; allowance exclusion: ' + '; '.join(
            f'{name}: {reason}' for name, reason in reasons.items()))


def weight(runtime, levels):
    return PRESSURE_WEIGHTS.get(levels.get(runtime), COST_WEIGHT)


def select_candidate(pool, candidate, recommendation, eligible_candidates, task,
                     *, overrides=None, definitions=None, target_runtime=None, configured_candidates=None,
                     default_runtime=None):
    """Validate a recommendation or pin; never infer runtime support from model names.

    Profile overrides are keyed by worker runtime; a rule route over a pool spanning runtimes
    passes the runtime whose entry applies (`default_runtime`); a plain profile router passes none.
    """
    candidates = pool['candidates']
    eligible = list(candidates) if eligible_candidates is None else eligible_candidates
    if (not isinstance(eligible, list) or any(not isinstance(name, str) or name not in candidates
                                              for name in eligible) or len(set(eligible)) != len(eligible)):
        raise ValueError('eligible candidates must be a unique list of configured candidate IDs')
    info = {'eligible_candidates': eligible}
    pinned = candidate is not None
    thinking = None
    if not eligible:
        if pinned:
            raise ValueError(f'candidate {candidate!r} is not eligible; explicit choice prevents fallback')
        raise ValueError('No eligible candidates remain; configured alternatives are exhausted')
    if candidate is not None:
        source, reason = 'user', 'Explicit candidate choice.'
        if candidate not in eligible:
            raise ValueError(f'candidate {candidate!r} is not eligible; explicit choice prevents fallback')
    elif recommendation is not None:
        try:
            if isinstance(recommendation, str):
                recommendation = json.loads(recommendation)
            validate_schema(recommendation, 'selection', 'selection.schema.json')
            if recommendation['candidate'] is not None:
                if recommendation['candidate'] not in eligible:
                    raise ValueError('recommended candidate is not eligible')
        except ValueError as error:
            if (isinstance(recommendation, dict) and isinstance(recommendation.get('candidate'), str)
                    and recommendation['candidate'] in (configured_candidates or candidates)
                    and recommendation['candidate'] not in eligible and not pool.get('fallback', True)):
                raise ValueError('Selected candidate is not eligible; policy prevents fallback') from error
            info['selection_error'] = str(error)
            return None, None, dict(info, status='selection_required')
        else:
            if recommendation['candidate'] is None:
                raise ValueError('No suitable candidate: ' + recommendation['reason'])
            candidate, source, reason = recommendation['candidate'], 'selector', recommendation['reason']
            thinking = recommendation.get('thinking')
    else:
        source, reason = 'sole-candidate', 'Only one eligible candidate remains.'
    definitions = definitions or {}
    runtime = target_runtime or definitions.get(candidate, {}).get('target_runtime')
    if runtime is None:
        possible = {definitions.get(name, {}).get('target_runtime') for name in eligible}
        if len(possible) == 1:
            runtime = possible.pop()
        elif candidate is None:
            runtime = default_runtime
    override = (overrides or {}).get(runtime, {}) if not pinned else {}
    if 'candidate' in override:
        preferred = override['candidate']
        if preferred not in eligible:
            if not pool.get('fallback', True):
                raise ValueError(f'profile candidate {preferred!r} is not eligible; policy prevents fallback')
            info['fallback_from'] = preferred
            override = {}
            if candidate is None:
                return None, None, dict(info, status='selection_required')
        else:
            candidate, source, reason = preferred, 'profile', f'Configured choice for worker runtime {runtime}.'
    if candidate is None:
        if len(eligible) != 1:
            return None, None, dict(info, status='selection_required')
        candidate = eligible[0]
    if 'thinking' in override:
        thinking = override['thinking']
        info['profile_thinking'] = thinking
    instance = candidates[candidate]
    policy = instance.get('models', {'mode': 'inherit'})
    if thinking is not None and 'effort' not in task:
        try:
            check_thinking(policy, thinking, automatic=True)
        except ValueError as error:
            return None, None, dict(info, status='selection_required', selection_error=str(error))
        if thinking == policy.get('thinking'):
            thinking = None
    else:
        thinking = None
    if 'model' in task and task['model'] != policy.get('model'):
        raise ValueError('task model conflicts with candidate; select a candidate or an explicit --route-choice')
    info.update(candidate=candidate, selection_source=source, selection_reason=reason)
    return instance, thinking, info


def expand_executor_type(instance, executor_types):
    """Resolve a named skill usage while leaving assignment and model policy intact."""
    name = instance['executor']['type']
    if name in ('subagent', 'skill', 'bash'):
        return instance, {}
    if name not in executor_types and name in BUILT_IN_TYPES:
        # Built-in types are the lowest layer: a user, project or session type of the same name replaces them.
        return instance, {'configured_executor_type': name, 'executor_definition_source': 'built-in'}
    if name not in executor_types:
        raise ValueError(f'Unknown executor type {name!r}; define it in executor_types')
    definition, source = executor_types[name]
    return dict(instance, executor=definition), {
        'configured_executor_type': name, 'executor_definition_source': source}


def expand_model(name, catalog, runtimes, host, target_runtime=None):
    """Resolve a model's runtime binding and a launch route available to this caller."""
    if name not in catalog:
        raise ValueError(f'Unknown model definition {name!r}')
    definition, source = catalog[name]
    bindings = definition['runtimes']
    for runtime in bindings:
        if runtime not in runtimes:
            raise ValueError(f'Unknown runtime configuration {runtime!r} for model {name!r}')
    ordered = ([host] if host in bindings else []) + [runtime for runtime in bindings if runtime != host]
    if target_runtime is not None:
        ordered = [runtime for runtime in ordered if runtime == target_runtime]
    for runtime in ordered:
        runtime_definition, runtime_source = runtimes[runtime]
        executors = runtime_definition['executors']
        contexts = [host]
        if os.environ.get('HERDR_ENV') == '1':
            contexts = ['herdr', host] if host in EXTENSION_DELEGATION else [host, 'herdr']
        context = next((name for name in contexts if name in executors or name in BUILT_IN_ROUTES), None)
        if context is None:
            continue
        binding = bindings[runtime]
        policy = {key: binding[key] for key in ('thinking', 'allow_thinking_override') if key in binding}
        instance = {'executor': executors.get(context) or BUILT_IN_ROUTES[context],
                    'description': definition['description'],
                    'models': dict(policy, mode='override', model=binding['id'])}
        return instance, {'executor_context': context, 'target_runtime': runtime,
                          'model_definition_source': source,
                          'model_binding_source': source + '.runtimes.' + runtime,
                          'executor_source': (runtime_source + '.executors.' + context if context in executors
                                              else 'built-in')}
    return None, f'No configured route from {host!r} to model runtimes {ordered} in this environment'


def profile_entry(profile, agent, job, job_pool=None):
    """A routing profile's overrides for this job, or else for its agent. When the job has its own pool,
    an agent-level override applies only where its candidate belongs to that pool."""
    if job in profile:
        return profile[job]
    entry = profile.get(agent, {})
    if job_pool is None:
        return entry
    return {runtime: override for runtime, override in entry.items()
            if override.get('candidate') is None or override['candidate'] in job_pool}


def resolve_execution(repo, user_config, user_executors, host, agent, task, choice=None, config_profile=None,
                      adaptive_thinking=None, *, job=None, session_id=None, candidate=None, selection=None,
                      eligible_candidates=None, list_candidates=False, target_runtime=None, assignment=None,
                      eligible_thinking=None, journal=None, assignment_id=None, excluded_candidates=None,
                      after_failure=None, worktree=None, reports=None):
    if job is not None:
        if job not in JOBS:
            raise ValueError(f'Unknown job {job!r}; jobs: {", ".join(sorted(JOBS))}')
        if JOBS[job]['agent'] != agent:
            raise ValueError(f'Job {job!r} is done by {JOBS[job]["agent"]}, not {agent}')
    layers = [(path, read_executors(path))
              for path in (user_executors, repo / '.zloop/executors.json')]
    session_id = session_id if session_id is not None else os.environ.get('ZLOOP_SESSION') or None
    trace = journal_store.trace_config(journal, repo, assignment_id, session_id)
    session = {}
    if session_id is not None:
        path = session_path(repo, session_id)
        layers.append((path, read_session(repo, session_id)))
        session = {'session': {'id': session_id, 'repo': str(repo.resolve()), 'path': str(path)}}
    profiles = {name: (value, f'{path}:profiles.{name}')
                for path, data in layers for name, value in data.get('profiles', {}).items()}
    router, router_source = DEFAULT_ROUTER, 'built-in'
    for path, data in layers:
        if 'router' in data:
            router, router_source = data['router'], f'{path}:router'
    decision_policy = decisions.effective_policy(layers, router)
    decision_context = decisions.decision_context(layers)
    profile = config_profile if config_profile is not None else os.environ.get('ZLOOP_PROFILE') or None
    if profile is not None:
        router = {'type': 'profile', 'profile': profile}
        router_source = 'argument' if config_profile is not None else 'environment'
    profile = router.get('profile')
    for name in (profile, router.get('fallback_profile')):
        if name is not None and name not in profiles:
            raise ValueError(f'Unknown routing profile {name!r}; define it in executors.json')
    overrides = profile_entry(profiles[profile][0], agent, job) if profile is not None else {}
    metadata = {'config_profile': profile, 'profile_source': profiles[profile][1] if profile else None,
                'router': router, 'router_source': router_source, 'profile_overrides': overrides,
                'decision_policy': decision_policy, **session}
    catalog = {name: (definition, f'{path}:models.{name}')
               for path, data in layers for name, definition in data.get('models', {}).items()}
    allow_training, include_critical, backends, checked = False, False, {}, {}
    for _, data in layers:
        allow_training = data.get('allow_training', allow_training)
        include_critical = data.get('include_critical_allowance', include_critical)
        backends.update(data.get('capacity', {}))
    pressure = {} if include_critical else allowance_levels()

    def spent(name, runtime):
        # Only a subscription model spends an allowance; free and local ones do not. A model names the reading it
        # spends (`allowance`), else its worker runtime does: a Pi binding can spend a Claude or Codex subscription.
        definition = catalog[name][0]
        return (pressure.get(definition.get('allowance', runtime)) == 'critical'
                and COSTS.get(definition.get('cost'), 1.0) > 0)

    def critical(name, runtime):
        return CRITICAL.format(provider=catalog[name][0].get('allowance', runtime))
    runtimes = {name: (definition, f'{path}:runtimes.{name}')
                for path, data in layers for name, definition in data.get('runtimes', {}).items()}
    executor_types = {name: (definition, f'{path}:executor_types.{name}')
                      for path, data in layers for name, definition in data.get('executor_types', {}).items()}
    instance, source = None, None
    # Layers run user → project → session; within one layer a job's entry wins over its agent's.
    for path, data in layers:
        if job in data.get('jobs', {}):
            instance, source = data['jobs'][job], f'{path}:jobs.{job}'
        elif agent in data.get('agents', {}):
            instance, source = data['agents'][agent], f'{path}:agents.{agent}'
    if choice is not None:
        validate_execution_config({'agents': {agent: choice}}, 'task choice')
        if 'executor' not in choice:
            raise ValueError('task choice requires a complete single agent instance')
        instance, source = choice, 'task choice'
    pooled = instance is not None and 'candidates' in instance
    configured_candidates = list(instance['candidates']) if pooled else []
    job_pool = configured_candidates if pooled and ':jobs.' in (source or '') else None
    if profile is not None and job_pool is not None:
        overrides = metadata['profile_overrides'] = profile_entry(profiles[profile][0], agent, job, job_pool)
    definitions = {}
    if target_runtime is not None and target_runtime not in HOSTS:
        raise ValueError(f'Unknown target runtime {target_runtime!r}')
    if target_runtime is not None and not (instance is not None and (
            'model' in instance or (pooled and isinstance(instance['candidates'], list)))):
        raise ValueError('An explicit target runtime requires a catalog model reference or pool; '
                         'complete executor instances determine their own destination')
    if pooled:
        if eligible_candidates is not None and (
                not isinstance(eligible_candidates, list)
                or any(not isinstance(name, str) or name not in configured_candidates for name in eligible_candidates)
                or len(set(eligible_candidates)) != len(eligible_candidates)):
            raise ValueError('eligible candidates must be a unique list of configured candidate IDs')
        if isinstance(excluded_candidates, dict):
            both = [name for name in eligible_candidates or [] if name in excluded_candidates]
            if both:
                raise ValueError('candidates cannot be both eligible and excluded: ' + ', '.join(both))
            if eligible_candidates is None:
                eligible_candidates = [name for name in configured_candidates if name not in excluded_candidates]
        for runtime, override in overrides.items():
            name = override.get('candidate')
            if name is None:
                continue
            if name not in instance['candidates']:
                raise ValueError(f'Profile candidate {name!r} is outside the pool at {source}')
            if isinstance(instance['candidates'], list):
                if name not in catalog or runtime not in catalog[name][0]['runtimes']:
                    raise ValueError(f'Profile candidate {name!r} has no {runtime!r} binding')
                binding = catalog[name][0]['runtimes'][runtime]
                if 'thinking' in override:
                    check_thinking(dict(binding, mode='override'), override['thinking'], automatic=True)
        if isinstance(instance['candidates'], list) and overrides and target_runtime is None:
            baseline = candidate
            if baseline is None:
                try:
                    value = json.loads(selection) if isinstance(selection, str) else selection
                    baseline = value.get('candidate') if isinstance(value, dict) else None
                except ValueError:
                    pass
            if baseline in instance['candidates'] and (eligible_candidates is None or baseline in eligible_candidates):
                resolved, detail = expand_model(baseline, catalog, runtimes, host)
                if resolved is not None:
                    target_runtime = detail['target_runtime']
    if pooled and isinstance(instance['candidates'], list):
        expanded, unavailable = {}, {}
        for name in instance['candidates']:
            if name in catalog and catalog[name][0].get('data') == 'trains' and not allow_training:
                unavailable[name] = TRAINING
                continue
            full = backend_unavailable(catalog[name][0] if name in catalog else {}, backends, checked)
            if full:
                unavailable[name] = full
                continue
            resolved, detail = expand_model(name, catalog, runtimes, host, target_runtime)
            if resolved is None:
                unavailable[name] = detail
            elif spent(name, detail['target_runtime']):
                unavailable[name] = critical(name, detail['target_runtime'])
            else:
                expanded[name], definitions[name] = resolved, detail
                backend = catalog[name][0].get('backend')
                if backend in backends:
                    # The lifecycle takes this backend's lease when the worker registers.
                    definitions[name].update(backend=backend, capacity=backends[backend])
        if not expanded and not list_candidates:
            allowance_refusal(unavailable)
        metadata['unavailable_candidates'] = unavailable
        metadata['candidate_routes'] = definitions
        instance = dict(instance, candidates=expanded)
    elif instance is not None and 'model' in instance:
        name = instance['model']
        if name in catalog and catalog[name][0].get('data') == 'trains' and not allow_training:
            raise ValueError(f'Model {name!r}: {TRAINING}')
        full = backend_unavailable(catalog[name][0] if name in catalog else {}, backends, checked)
        if full:
            raise ValueError(f'Model {name!r}: {full}')
        resolved, detail = expand_model(name, catalog, runtimes, host, target_runtime)
        if resolved is None:
            raise ValueError(f'Model {name!r}: {detail}')
        if spent(name, detail['target_runtime']):
            raise ValueError(f'Model {name!r}: {critical(name, detail["target_runtime"])}')
        instance = resolved
        metadata.update(detail)
        backend = catalog[name][0].get('backend')
        if backend in backends:
            metadata.update(backend=backend, capacity=backends[backend])
    if pooled:
        expanded = {}
        for name, value in instance['candidates'].items():
            expanded[name], detail = expand_executor_type(value, executor_types)
            if (expanded[name]['executor']['type'] == 'subagent'
                    and not expanded[name]['executor'].get('config', {}).get('agent')):
                definitions.setdefault(name, {}).setdefault('target_runtime', host)
            if detail:
                definitions.setdefault(name, {}).update(detail)
        instance = dict(instance, candidates=expanded)
        if definitions:
            metadata['candidate_routes'] = definitions
    elif instance is not None:
        instance, detail = expand_executor_type(instance, executor_types)
        metadata.update(detail)
    if eligible_thinking is not None:
        if not pooled or not isinstance(eligible_thinking, dict):
            raise ValueError('eligible thinking requires a candidate pool and a JSON candidate-to-levels object')
        for name, levels in eligible_thinking.items():
            if (name not in configured_candidates or not isinstance(levels, list)
                    or any(level is not None and (not isinstance(level, str) or not level) for level in levels)
                    or len(set(levels)) != len(levels)):
                raise ValueError('eligible thinking requires configured candidate IDs and unique levels')
            if name in instance['candidates']:
                baseline = instance['candidates'][name].get('models', {}).get('thinking')
                permitted = baseline['allowed'] if isinstance(baseline, dict) else [baseline]
                if any(level not in permitted for level in levels):
                    raise ValueError(f'eligible thinking for {name!r} exceeds configured thinking choices')
        metadata['eligible_thinking'] = eligible_thinking
        eligible_candidates = [name for name in (eligible_candidates if eligible_candidates is not None
                                                 else instance['candidates'])
                               if name not in eligible_thinking or eligible_thinking[name]]
    if list_candidates:
        return dict(metadata, host=host, agent=agent, job=job, route_source=source,
                    candidates=instance['candidates'] if pooled else {},
                    instance=None if pooled else instance)
    if pooled:
        metadata['candidate_pool'] = instance
        pool = instance
        # Shadow scoring weighs every reachable candidate the host did not exclude, whatever the route's runtime.
        competing = {}
        for name in configured_candidates:
            if ((eligible_candidates is None or name in eligible_candidates) and name in catalog
                    and (catalog[name][0].get('data') != 'trains' or allow_training)
                    and not backend_unavailable(catalog[name][0], backends, checked)):
                reachable, route = expand_model(name, catalog, runtimes, host)
                if reachable is not None and not spent(name, route['target_runtime']):
                    competing[name] = route['target_runtime']
        if eligible_candidates is not None:
            eligible_candidates = [name for name in eligible_candidates if name in pool['candidates']]
            if not eligible_candidates:
                allowance_refusal(metadata.get('unavailable_candidates', {}))
        decision_requested = (router['type'] == 'decision-model' and assignment is not None
                              and candidate is None and selection is None)
        if decision_requested and router.get('consult') == 'after-failure' and after_failure is None:
            # Rule first: the configured profile settles routine routes; System One sees failures.
            rule_profile = router.get('fallback_profile')
            if rule_profile is None:
                raise ValueError('router consult "after-failure" needs a fallback_profile as its rule route')
            rule = profile_entry(profiles[rule_profile][0], agent, job, job_pool)
            usable = [runtime for runtime, entry in rule.items()
                      if entry.get('candidate') in (eligible_candidates or pool['candidates'])
                      and definitions.get(entry['candidate'], {}).get('target_runtime') == runtime]
            rule_runtime = host if host in usable else usable[0] if len(usable) == 1 else None
            try:
                settled = select_candidate(pool, None, None, eligible_candidates, task, overrides=rule,
                                           definitions=definitions, target_runtime=target_runtime,
                                           configured_candidates=configured_candidates,
                                           default_runtime=rule_runtime)[2]
            except ValueError:
                settled = {}  # The rule cannot route this assignment; consult as usual.
            if settled.get('selection_source') == 'profile':
                overrides, decision_requested = rule, False
                metadata.update(config_profile=rule_profile, profile_source=profiles[rule_profile][1],
                                profile_overrides=rule, rule_route=True)
        if decision_requested:
            if adaptive_thinking is not None:
                raise ValueError('Use decision-model thinking selection or --adaptive-thinking, not both')
            eligible = list(pool['candidates']) if eligible_candidates is None else eligible_candidates
            if not eligible:
                raise ValueError('No eligible candidates remain; configured alternatives are exhausted')
            if excluded_candidates is not None and not (isinstance(excluded_candidates, dict) and all(
                    name in configured_candidates for name in excluded_candidates)):
                unknown = sorted(set(excluded_candidates) - set(configured_candidates)) \
                    if isinstance(excluded_candidates, dict) else []
                raise ValueError('excluded candidates must map configured candidate IDs to reasons'
                                 + (f'; unknown: {", ".join(unknown)}' if unknown else '')
                                 + f'; configured: {", ".join(sorted(configured_candidates))}')
            excluded = {}
            for name in pool['candidates']:
                if name in eligible:
                    continue
                if (eligible_thinking or {}).get(name) == []:
                    excluded[name] = 'no eligible thinking level after runtime preflight'
                elif isinstance((excluded_candidates or {}).get(name), str) and excluded_candidates[name].strip():
                    excluded[name] = excluded_candidates[name].strip()
            unexplained = [name for name in pool['candidates'] if name not in eligible and name not in excluded]
            if unexplained:
                raise ValueError('Give a reason for each configured candidate you exclude from this decision '
                                 '(--excluded-candidates \'{"<id>": "<reason>"}\'): ' + ', '.join(unexplained))
            validate_settings(task, 'task', runtime_labels=True)
            try:
                selection, record = decision_model_selection(
                    router, {name: pool['candidates'][name] for name in eligible}, definitions,
                    agent, job, host, assignment, task, decision_policy=decision_policy, context=decision_context,
                    eligible_thinking=eligible_thinking, trace=trace, excluded=excluded, failure=after_failure)
                metadata['decision_model'] = record
                metadata['decision_warnings'] = record['warnings']
                if selection is None:
                    raise ValueError(record.get('error', 'Decision model abstained; use host judgment'))
            except (OSError, ValueError) as error:
                fallback = router.get('fallback_profile')
                # Only a backend outage/abstention/invalid answer falls back; configuration or journal errors do not.
                if fallback is None or 'decision_model' not in metadata:
                    return dict(metadata, status='selection_required', selection_error=str(error),
                                host=host, agent=agent, job=job, route_source=source,
                                eligible_candidates=eligible, candidates=pool['candidates'],
                                selection_router=DEFAULT_ROUTER)
                # Deterministic recovery: apply the configured profile before asking the host.
                overrides = profile_entry(profiles[fallback][0], agent, job, job_pool)
                metadata.update(config_profile=fallback, profile_source=profiles[fallback][1],
                                profile_overrides=overrides, selection_error=str(error))
                metadata['decision_warnings'] = metadata['decision_warnings'] + [{
                    'code': 'decision_fallback',
                    'message': f'Decision backend did not select ({error}); applied fallback profile {fallback!r}.'}]
                decision_requested, selection = False, None
        instance, recommended_thinking, info = select_candidate(
            pool, candidate, selection, eligible_candidates, task,
            overrides=overrides, definitions=definitions, target_runtime=target_runtime,
            configured_candidates=configured_candidates,
            default_runtime=rule_runtime if metadata.get('rule_route') else None)
        metadata.update(info)
        if info.get('status') == 'selection_required':
            return dict(metadata, host=host, agent=agent, job=job, route_source=source,
                        candidates=pool['candidates'],
                        selection_router=DEFAULT_ROUTER if router['type'] == 'profile'
                        or 'selection_error' in info or 'selection_error' in metadata else router)
        metadata.update(definitions.get(info['candidate'], {}))
        if 'selection_error' in metadata and metadata.get('selection_source') == 'profile':
            metadata['selection_source'] = 'fallback-profile'
        if metadata.pop('rule_route', False):
            metadata['selection_source'] = 'rule-profile'
        if decision_requested:
            metadata['selection_source'] = ('sole-option' if metadata['decision_model']['status'] == 'skipped'
                                            else 'decision-model')
        if isinstance(configured_candidates, list) and configured_candidates:
            shadow = shadow_selection(competing, catalog, agent, job,
                                      f'{agent}|{job}|{assignment_id or assignment or ""}', allowance_levels())
            if shadow.get('choice') is not None:
                shadow['agrees'] = shadow['choice'] == info['candidate']
            metadata['shadow_selection'] = shadow
        source += '.candidates.' + info['candidate']
        if recommended_thinking is not None:
            if adaptive_thinking is not None:
                raise ValueError('use selection thinking or adaptive thinking, not both')
            adaptive_thinking = recommended_thinking
    elif choice is None and any(value is not None for value in (candidate, selection, eligible_candidates)):
        raise ValueError('candidate selection requires a configured candidate pool')
    if not pooled and 'target_runtime' not in metadata and pressure.get(host) == 'critical' and (
            instance is None or (instance['executor']['type'] == 'subagent'
                                 and not instance['executor'].get('config', {}).get('agent'))):
        raise ValueError(f'Native route: {CRITICAL.format(provider=host)}')  # A catalog model's own cost decides.
    if instance is None:
        if adaptive_thinking is not None:
            raise ValueError('adaptive thinking requires an agent with allow_thinking_override: true')
        result = resolve(repo, user_config, host, agent, task)
        result.update(host=host, job=job, executor='native', executor_type='subagent',
                      executor_config={}, native_dispatch=True, model_mode='existing',
                      route_source=source, allow_thinking_override=False, **metadata,
                      executor_guide=str(ROOT / 'hosts/executors/subagent.md'))
        return result
    executor = instance['executor']
    kind, config = executor['type'], executor.get('config', {})
    native = kind == 'subagent' and not config.get('agent')
    policy = instance.get('models', {'mode': 'inherit'})
    mode = policy['mode']
    validate_settings(task, 'task', runtime_labels=True)
    if mode == 'inherit' and task:
        raise ValueError('inherit conflicts with task model/thinking; choose override mode explicitly')
    thinking = policy.get('thinking')
    bounded = isinstance(thinking, dict)
    allow_thinking_override = bounded or policy.get('allow_thinking_override', False)
    if bounded and 'model' in task and task['model'] != policy.get('model'):
        raise ValueError('task model conflicts with bounded thinking; use an explicit --route-choice')
    if bounded and 'effort' in task:
        check_thinking(policy, task['effort'])
    if adaptive_thinking is not None:
        if not allow_thinking_override:
            raise ValueError('agent does not allow automatic thinking overrides')
        if 'effort' in task:
            raise ValueError('explicit user thinking takes precedence; omit adaptive thinking')
        validate_settings({'effort': adaptive_thinking}, 'adaptive thinking', runtime_labels=True)
        if adaptive_thinking == 'native':
            raise ValueError('adaptive thinking requires a runtime label, not native')
        check_thinking(policy, adaptive_thinking, automatic=True)
    settings = {}
    sources = {}
    if mode == 'override':
        for field, key in (('model', 'model'), ('thinking', 'effort')):
            if field in policy:
                settings[key] = thinking['default'] if field == 'thinking' and bounded else policy[field]
                sources[key] = (metadata['model_binding_source'] + '.' + ('id' if field == 'model' else field)
                                if 'model_binding_source' in metadata else source + '.models.' + field)
        for key, value in task.items():
            if value is None:
                settings.pop(key, None)
            else:
                settings[key] = value
            sources[key] = 'task'
    if adaptive_thinking is not None:
        settings['effort'] = adaptive_thinking
        sources['effort'] = 'adaptive thinking'
    selected = metadata.get('candidate')
    if selected in (eligible_thinking or {}) and settings.get('effort') not in eligible_thinking[selected]:
        raise ValueError(f'candidate {selected!r} thinking {settings.get("effort")!r} failed runtime preflight; '
                         'choose an eligible setting or verify registration before retrying')
    # A native child takes no flags; a worker started as its own runtime process gets them explicitly.
    runtime = metadata.get('target_runtime')
    launch = (launch_controls(runtime, agent, settings, repo.resolve(), worktree, reports)
              if not native and runtime in HOSTS else None)
    # Named delegates and skills can forward to another runtime; the host is not the destination.
    return {'host': host, 'agent': agent, 'job': job,
            'settings': settings, 'sources': sources, 'files': [],
            'executor': 'native' if native else kind, 'executor_type': kind,
            'executor_config': config, 'native_dispatch': native, 'model_mode': mode,
            'allow_thinking_override': allow_thinking_override,
            'thinking_baseline': thinking['default'] if bounded else thinking,
            **({'thinking_allowed': thinking['allowed']} if bounded else {}),
            'route_source': source, **metadata, **({'launch': launch} if launch else {}),
            'executor_guide': str(ROOT / {'bash': 'runtime/config/bash-executor.md', 'herdr': 'hosts/herdr.md'}.get(
                kind, f'hosts/executors/{kind}.md'))}


PI_TOOLS = {'none': ('read', 'grep', 'find', 'ls', 'bash'), 'worktree': ('read', 'bash', 'edit', 'write', 'grep', 'find', 'ls')}
OPENCODE_DENY = {'none': ('edit', 'subagent'), 'worktree': ('subagent',)}  # `none` keeps the shell for checks.


def git_dir(checkout, which='--git-common-dir'):
    """The git directory a checkout's linked worktrees share, also when `checkout` is itself a linked worktree; with
    `--git-dir`, the checkout's own one (.git/worktrees/<name> for a linked worktree)."""
    process = subprocess.run(['git', '-C', str(checkout), 'rev-parse', '--path-format=absolute', which],
                             capture_output=True, text=True)
    if process.returncode:
        raise ValueError(f'{checkout} is not a git checkout: {process.stderr.strip()}')
    return str(Path(process.stdout.strip()).resolve())


def launch_controls(runtime, agent, settings, repo, worktree=None, reports=None):
    """Flags for a worker started as its own runtime process: the agent's write scope and the resolved model and
    effort, each passed explicitly because every user default allows full writes. `verify` holds the native
    record's values that the worker lifecycle's submit compares. A read-only worker keeps a shell for checks; the
    read-only worktree check at collection covers what a shell writes. A launch that needs the absolute worktree
    or reports directory and lacks it names them in `missing` instead."""
    definition = DEFINITIONS['agents'][agent]
    writes = definition['writes']
    model, effort = settings.get('model'), settings.get('effort')
    needs = {('claude', 'none'): ('worktree', 'reports'), ('codex', 'none'): ('reports',),
             ('codex', 'worktree'): ('worktree', 'reports')}.get((runtime, writes), ())
    missing = [f'--{name}' for name, value in (('worktree', worktree), ('reports', reports))
               if name in needs and value is None]
    if missing:
        return {'writes': writes, 'missing': missing}
    if runtime == 'claude':
        # dontAsk denies whatever the allowlist leaves out. A read-only agent may edit its reports but nothing under the
        # worktree: absolute rules (`//` + path), deny beating allow, which also cover the shell's redirects.
        denied = set(agent_defs.tool_set(definition.get('disallowedTools')))
        tools = [tool.strip() for tool in definition['tools'].split(',') if tool.strip() not in denied]
        argv = ['--permission-mode', 'dontAsk', '--allowedTools', ','.join(tools)]
        if writes == 'none':
            if reports == worktree or worktree in reports.parents:
                raise ValueError('A read-only Claude worker could never write its report: the reports directory is '
                                 'inside the worktree, whose edit deny beats the reports allow; review from a separate '
                                 'worktree, such as .worktrees/review-<slug>, with the reports outside it')
            rules = {'deny': [f'Edit(/{worktree}/**)'], 'allow': [f'Edit(/{reports}/**)']}
            argv += ['--settings', json.dumps({'permissions': rules}, separators=(',', ':'))]
        argv += (['--model', model] if model else []) + (['--effort', effort] if effort else [])
        return {'writes': writes, 'argv': argv, 'verify': {'permission_mode': 'dontAsk'}}
    if runtime == 'pi':
        # An allowlist: an exclude list leaves extension shells such as bg_run; it keeps MCP tools, hence --no-mcp.
        # `none` keeps bash for checks; the read-only worktree check at collection covers its writes.
        tools = PI_TOOLS[writes]
        provider, _, name = model.partition('/') if model and '/' in model else ('', '', model)
        argv = ['--tools', ','.join(tools), '--no-mcp'] + (['--provider', provider] if provider else []) + (
            ['--model', name] if name else []) + (['--thinking', effort] if effort else [])
        return {'writes': writes, 'argv': argv, 'verify': {'tools': sorted(tools)}}
    if runtime == 'opencode':
        # The TUI has no --agent or -m: the session is created with them. A denied agent still writes through the
        # general subagent unless subagent is denied too.
        rules = [{'action': action, 'resource': '*', 'effect': 'deny'} for action in OPENCODE_DENY[writes]]
        body = {'agent': 'build', 'permissions': rules}
        if model:
            provider, _, name = model.partition('/')
            body['model'] = dict(providerID=provider, id=name, **({'variant': effort} if effort else {}))
        return {'writes': writes, 'api': {'method': 'POST', 'path': '/api/session', 'body': body},
                'verify': {'permissions': rules}}
    # A permissions profile given with -c applies over the user's sandbox_mode and is recorded as a workspace-write
    # policy with its entries. Both scopes read everything and write temporary files and the reports; `worktree` also
    # writes the worktree and the main checkout's .git, where a linked worktree's commits land, and the worktree's own
    # git directory, which Codex keeps read-only under the common one.
    writable = [':tmpdir', str(reports)]
    if writes == 'worktree':
        writable += dict.fromkeys([str(worktree), git_dir(repo), git_dir(worktree, '--git-dir')])
    profile = f'zloop_{writes}'
    table = ','.join(['":root"="read"'] + [f'{json.dumps(path)}="write"' for path in writable])
    argv = ['-c', f'default_permissions="{profile}"', '-c', f'permissions.{profile}.filesystem={{{table}}}',
            '-a', 'never'] + (['-m', model] if model else []) + (
        ['-c', f'model_reasoning_effort={json.dumps(effort)}'] if effort else [])
    return {'writes': writes, 'argv': argv, 'verify': {
        'sandbox': 'workspace-write', 'approval': 'never',
        'filesystem': sorted(['read :root'] + [f'write {path}' for path in writable])}}


def read_config(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or set(data) - set(HOSTS):
        raise ValueError(f'{path}: expected host keys {HOSTS}')
    for host, entries in data.items():
        if not isinstance(entries, dict) or set(entries) - set(AGENTS + ('defaults',)):
            raise ValueError(f'{path}:{host}: unknown agent or invalid object')
        for agent, settings in entries.items():
            validate_settings(settings, f'{path}:{host}.{agent}')
    return data


def resolve(repo, user_config, host, agent, task):
    settings, sources = {}, {}
    files = (user_config, repo / '.zloop/models.json')
    layers = []
    for path in files:
        data = read_config(path).get(host, {})
        for key in ('defaults', agent):
            layers.append((data.get(key, {}), f'{path}:{host}.{key}'))
    validate_settings(task, 'task', runtime_labels=True)
    layers.append((task, 'task'))
    for values, source in layers:
        for key, value in values.items():
            if value is None:
                settings.pop(key, None)
            else:
                settings[key] = value
            sources[key] = source
    return {'host': host, 'agent': agent, 'settings': settings,
            'sources': sources, 'files': [str(path) for path in files]}


def native_profile(result):
    host, agent, settings = result['host'], result['agent'], result['settings']
    if host not in ('claude', 'opencode'):
        raise ValueError('--profile is only for Claude and OpenCode')
    if not settings:
        return None
    if host == 'claude':
        _, header, body = (ROOT / 'agents' / f'{agent}.md').read_text().split('---', 2)
        fields = dict(line.split(': ', 1) for line in header.strip().splitlines())
        for key in ('name', 'writes', 'core_skills'):
            fields.pop(key)  # Claude reads its own keys; these are zloop's.
        if 'maxTurns' in fields:
            fields['maxTurns'] = int(fields['maxTurns'])
        fields.update(settings)
        if 'effort' in settings and settings['effort'] not in ('low', 'medium', 'high', 'xhigh', 'max'):
            raise ValueError('Claude does not support this effort level')
        folder = '.claude/agents'
    else:
        fields = {'description': f'zloop {agent}; use only for an assigned zloop job.',
                  'mode': 'subagent'}
        if 'model' in settings:
            fields['model'] = settings['model']
        if 'effort' in settings:
            fields['variant'] = settings['effort']
        body = ('\n\nRead the absolute `adapter`, `agent_file` and `job_file` in your assignment.\n'
                'Verify the supplied worktree and follow the agent and its job.\n')
        folder = '.opencode/agents'
    # JSON strings are YAML scalars, including model IDs with punctuation.
    header = '\n'.join(f'{key}: {json.dumps(value)}' for key, value in fields.items())
    digest = hashlib.sha256((header + body).encode()).hexdigest()[:12]
    name = f'zloop-{agent}-{digest}'
    content = (f'---\nname: {name}\n{header}\n---\n'
               '<!-- Generated by zloop agent_models.py; settings snapshot. -->' + body)
    return {'name': name, 'path': f'{folder}/{name}.md', 'content': content}


def json_argument(value, flag, kind, example):
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        raise ValueError(f'{flag} expects inline JSON, for example {example}') from None
    if not isinstance(parsed, kind):
        raise ValueError(f'{flag} expects inline JSON, for example {example}')
    return parsed


def assignment_text(path):
    try:
        return path.expanduser().read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        raise ValueError('--assignment requires a readable UTF-8 text file path, not inline prose; '
                         'write the brief to a file and pass --assignment /absolute/path/assignment.txt') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path,
                        help='canonical project checkout, not the worker worktree')
    parser.add_argument('--user-config', type=Path,
                        default=Path.home() / '.config/zloop/models.json')
    parser.add_argument('--host', required=True, choices=HOSTS)
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument('--agent', choices=AGENTS, help='inspect or edit settings for an agent')
    who.add_argument('--job', choices=sorted(JOBS), help='the job to launch; selects its agent')
    parser.add_argument('--model', help='explicit task override; native masks saved overrides')
    parser.add_argument('--thinking', '--effort', dest='effort',
                        help='explicit thinking label within bounds; native removes an unbounded override')
    parser.add_argument('--adaptive-thinking', help='agent-chosen thinking label; requires --route and opt-in')
    parser.add_argument('--profile', action='store_true', help='also render a native profile; no writes')
    parser.add_argument('--config-profile', help='select a named configuration profile; requires --route')
    parser.add_argument('--target-runtime', choices=HOSTS,
                        help='worker runtime for catalog selection/profile lookup; requires --route')
    parser.add_argument('--route', action='store_true', help='resolve the worker executor and model policy')
    parser.add_argument('--user-executors', type=Path,
                        default=Path.home() / '.config/zloop/executors.json')
    parser.add_argument('--route-choice', help='complete JSON agent instance for this assignment only; requires --route')
    parser.add_argument('--candidate', help='explicit user pin to a configured candidate; requires --route')
    parser.add_argument('--selection', help='JSON candidate/thinking/reason recommendation; requires --route')
    parser.add_argument('--assignment', type=Path, metavar='PATH',
                        help='compact assignment text file for the decision-model router; requires --route')
    parser.add_argument('--eligible-candidates', help='inline JSON array of candidate IDs, e.g. ["luna","sol"]')
    parser.add_argument('--excluded-candidates',
                        help='JSON object giving a reason for each configured candidate left out of --eligible-candidates; '
                             'alone, it removes those candidates')
    parser.add_argument('--eligible-thinking',
                        help='JSON candidate-to-thinking-level lists from runtime preflight; null means inherited')
    parser.add_argument('--session', help='root session ID; defaults to ZLOOP_SESSION, requires --route')
    parser.add_argument('--after-failure', metavar='EVIDENCE',
                        help='retry after a failed attempt: consult the decision model with this failure evidence')
    parser.add_argument('--journal', type=Path, help='absolute session journal for decision-model calls')
    parser.add_argument('--worktree', type=Path, help='the worker\'s worktree, for launch controls; requires --route')
    parser.add_argument('--reports', type=Path, help='the reports directory a worker writes, for launch controls')
    parser.add_argument('--assignment-id', help='stable assignment/attempt identity; required with --journal')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--list-candidates', action='store_true', help='inspect the effective pool without selecting')
    actions.add_argument('--save-session', type=Path, help='replace session overrides from a validated config JSON file')
    actions.add_argument('--reset-session', action='store_true', help='remove only this session override record')
    args = parser.parse_args()
    try:
        repo = args.repo.resolve()
        if not repo.is_dir():
            raise ValueError(f'project directory does not exist: {repo}')
        task = {key: None if value == 'native' else value
                for key, value in {'model': args.model, 'effort': args.effort}.items()
                if value is not None}
        user_config = args.user_config.expanduser().resolve()
        pool_options = (args.candidate, args.selection, args.eligible_candidates, args.eligible_thinking,
                        args.session, args.assignment, args.excluded_candidates, args.after_failure)
        session_action = args.save_session is not None or args.reset_session
        if (args.journal or args.assignment_id) and (not args.route or args.list_candidates or session_action):
            raise ValueError('Decision recording requires --route and cannot be used for inspection/session edits')
        if (any(value is not None for value in pool_options) or args.list_candidates or session_action) and not args.route:
            raise ValueError('candidate and session options require --route')
        if (args.list_candidates or session_action) and (args.profile or args.route_choice or task
                or args.assignment is not None or args.adaptive_thinking is not None
                or any(value is not None for value in pool_options[:4])):
            raise ValueError('inspection/session edits cannot be combined with launch overrides or --profile')
        if session_action:
            session_id = args.session if args.session is not None else os.environ.get('ZLOOP_SESSION') or None
            if session_id is None:
                raise ValueError('session edits require --session or ZLOOP_SESSION')
            if args.save_session is not None:
                config = json.loads(args.save_session.expanduser().read_text())
                path = save_session(repo, session_id, config)
            else:
                path = reset_session(repo, session_id)
                config = {}
            print(json.dumps({'session': {'id': session_id, 'repo': str(repo), 'path': str(path)},
                              'config': config}, indent=2))
            return
        if args.route_choice and not args.route:
            raise ValueError('--route-choice requires --route')
        if args.config_profile is not None and not args.route:
            raise ValueError('--config-profile requires --route')
        if args.target_runtime is not None and not args.route:
            raise ValueError('--target-runtime requires --route')
        if args.adaptive_thinking is not None and not args.route:
            raise ValueError('--adaptive-thinking requires --route')
        agent = JOBS[args.job]['agent'] if args.job else args.agent
        if args.route and not args.list_candidates and args.job is None:
            raise ValueError('a launch route requires --job; use --agent only to inspect or edit settings')
        if args.route:
            choice = json_argument(args.route_choice, '--route-choice', dict,
                                   '\'{"executor":{"type":"subagent"}}\'')
            result = resolve_execution(repo, user_config, args.user_executors.expanduser().resolve(),
                                       args.host, agent, task, choice, args.config_profile,
                                       args.adaptive_thinking, job=args.job, session_id=args.session, candidate=args.candidate,
                                       selection=args.selection,
                                       eligible_candidates=json_argument(args.eligible_candidates, '--eligible-candidates', list,
                                                                         '\'["luna","sol"]\''),
                                       eligible_thinking=json_argument(args.eligible_thinking, '--eligible-thinking', dict,
                                                                       '\'{"opus":["medium"]}\''),
                                       list_candidates=args.list_candidates, target_runtime=args.target_runtime,
                                       assignment=assignment_text(args.assignment) if args.assignment else None,
                                       journal=args.journal, assignment_id=args.assignment_id,
                                       after_failure=args.after_failure,
                                       # Canonical, as Codex records resolved paths (/private/var for /var).
                                       worktree=args.worktree.expanduser().resolve() if args.worktree else None,
                                       reports=args.reports.expanduser().resolve() if args.reports else None,
                                       excluded_candidates=json_argument(args.excluded_candidates, '--excluded-candidates',
                                                                         dict, '\'{"luna":"quota exhausted"}\''))
        else:
            result = resolve(repo, user_config, args.host, agent, task)
        if result.get('launch', {}).get('missing'):
            raise ValueError(f'This {result["target_runtime"]} route starts its own process, and its launch controls '
                             f'need {", ".join(result["launch"]["missing"])}: pass the absolute paths')
        if args.profile:
            if result.get('status') == 'selection_required':
                raise ValueError('Select a candidate before rendering a native profile')
            if result.get('executor', 'native') != 'native':
                raise ValueError('external executors own their launch settings; do not use native child profiles')
            result['native_profile'] = native_profile(result)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    main()
