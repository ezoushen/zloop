#!/usr/bin/env python3
"""PreToolUse hook: an Agent launch names its model, unless its definition pins one.

Without a model, Claude Code runs helpers on the session's model, usually the most
expensive one. Set ZLOOP_AGENT_MODEL_HOOK=off to disable the check.
"""
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import agent_defs  # noqa: E402
import front_matter  # noqa: E402
# Built-in agents that follow the session model when no model is passed.
INHERITING_BUILTINS = {None, '', 'general-purpose', 'Explore', 'Plan', 'claude'}
REASON = (
    'Pass an explicit `model` to this Agent call; without it the subagent runs on the session model. '
    'Choose the cheapest adequate one: haiku for search, lookups and summaries; sonnet for research, '
    'review and bounded implementation; opus only for hard design, adversarial review or deep diagnosis. '
    'zloop agents are routed with agent_models.py --job first.'
)

ASSIGNMENT_REASON = ('A zloop agent needs its assignment: write `job_file: <absolute job path>` and every input '
                     'that job lists as `name: value` in the prompt (see the job file and the host adapter). ')


def field(path, name):
    """Return a front-matter field of an agent definition, a list as listed and a string unquoted, or None."""
    value = (front_matter.parse(path.read_text(errors='replace')) or {}).get(name)
    return value if isinstance(value, list) else front_matter.unquoted(value or '') or None


def pinned_model(path):
    model = field(path, 'model')
    return model.split()[0] if model else None


def tools(path):
    value = field(path, 'tools')
    if value is None:
        return None
    return {tool.strip() for tool in (value if isinstance(value, list) else value.split(','))}


def assignment_problem(agent, prompt):
    """Why a launch prompt cannot carry out its job, or None: the job file it names and that job's inputs."""
    match = re.search(r'(?:^|\s)job_file:[ \t]*(\S+)', prompt, re.M)
    if not match:
        return 'No `job_file:` found.'
    path = Path(match.group(1))
    if not path.is_absolute() or path.parent.name != 'jobs' or not path.is_file():
        return f'`job_file: {match.group(1)}` is not an absolute path to an existing skills/<skill>/jobs/<job>.md.'
    job = agent_defs.load(path.parents[3])['jobs'].get(path.stem)
    if job is None or job['agent'] != agent:
        return f'Job {path.stem} is not done by {agent}.'
    if not isinstance(job.get('inputs'), list):
        return f'{path} declares no inputs list; fix the job file.'
    missing = agent_defs.missing_inputs(job, prompt)
    return f'Missing: {", ".join(missing)}.' if missing else None


def stale_profile(name, cwd):
    """Return why a generated `zloop-<agent>-<digest>` profile no longer matches its agent, or None."""
    match = re.fullmatch(r'zloop-([a-z0-9-]+)-[0-9a-f]{12}', name)
    agent = match and ROOT / 'agents' / f'{match.group(1)}.md'
    profile = match and definition(name, cwd)
    if not (profile and agent.is_file()) or None in (tools(profile), tools(agent)) or tools(profile) == tools(agent):
        return None
    missing, extra = sorted(tools(agent) - tools(profile)), sorted(tools(profile) - tools(agent))
    return (f'{profile} was rendered from an older `agents/{match.group(1)}.md`: its tools differ '
            f'(missing: {", ".join(missing) or "none"}; extra: {", ".join(extra) or "none"}). Re-render it with '
            f'`agent_models.py --route ... --profile` as {ROOT}/hosts/claude/workers.md describes, and launch '
            f'the name it returns.')


def definition(name, cwd):
    """Return the agent definition file for a subagent name, or None when unknown."""
    if ':' in name:
        plugin, agent = name.split(':', 1)
        manifest = ROOT / '.claude-plugin/plugin.json'
        if manifest.is_file() and json.loads(manifest.read_text()).get('name') == plugin:
            candidates = [ROOT / 'agents' / f'{agent}.md']
        else:
            candidates = sorted((Path.home() / '.claude/plugins/cache').glob(f'*/{plugin}/*/agents/{agent}.md'))
    else:
        candidates = [Path(cwd) / '.claude/agents' / f'{name}.md', Path.home() / '.claude/agents' / f'{name}.md']
    return next((path for path in candidates if path.is_file()), None)


def main():
    if os.environ.get('ZLOOP_AGENT_MODEL_HOOK') == 'off':
        return
    try:
        event = json.load(sys.stdin)
        if event.get('tool_name') not in ('Agent', 'Task'):
            return
        tool_input = event.get('tool_input') or {}
        subagent = tool_input.get('subagent_type') or ''
        named = re.fullmatch(r'zloop(?::([a-z0-9-]+)|-([a-z0-9-]+)-[0-9a-f]{12})', subagent)
        if named:
            problem = assignment_problem(named.group(1) or named.group(2), tool_input.get('prompt') or '')
            if problem:
                deny(ASSIGNMENT_REASON + problem)
                return
        stale = stale_profile(subagent, event.get('cwd') or os.getcwd())
        if stale:
            deny(stale)
            return
        if tool_input.get('model'):
            return
        subagent = tool_input.get('subagent_type')
        if subagent == 'fork':
            return  # Forks always share the parent's model and context.
        if subagent not in INHERITING_BUILTINS:
            path = definition(subagent, event.get('cwd') or os.getcwd())
            if path is None or pinned_model(path) not in (None, 'inherit'):
                return  # Unknown definitions are not blocked; pinned ones choose their own model.
    except Exception:
        return  # A broken check must never block work.
    deny(REASON)


def deny(reason):
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': reason}}))


if __name__ == '__main__':
    main()
