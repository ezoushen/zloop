#!/usr/bin/env python3
"""Load and check the plugin's agents (agents/*.md) and jobs (skills/*/jobs/*.md).

An agent holds permissions, principles and core skills; a job holds what one assignment
needs and returns. Code reads both from here, so no script needs an agent's name.
Run directly to print problems; exits 1 when any are found.
"""
import ast
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
WRITES = ('none', 'worktree')
AGENT_SECTIONS = ('Permissions', 'Principles', 'Core skills')
JOB_SECTIONS = ('Inputs', 'Finish line', 'Return')
AGENT_MAX_BYTES = 6000
# Every assignment carries these besides its job's own inputs.
COMMON_INPUTS = ('adapter', 'agent_file', 'job_file', 'plugin_root', 'repo')
IDENTITY = re.compile(r'\b(you are|act as|expert)\b', re.I)
LINK = re.compile(r'\]\(([^)\s]+)\)')


def frontmatter(path):
    """Top-level `key: value` fields; JSON arrays and booleans are decoded, other values stay strings."""
    match = re.match(r'---\n(.*?)\n---\n', path.read_text(), re.S)
    if not match:
        return None, path.read_text()
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(':')
        value = value.strip()
        fields[key.strip()] = json.loads(value) if value[:1] == '[' or value in ('true', 'false') else value
    return fields, path.read_text()[match.end():]


def headings(body, level='## '):
    return [line[len(level):].strip() for line in body.splitlines() if line.startswith(level)]


def section(body, name):
    match = re.search(rf'^## {re.escape(name)}\n(.*?)(?=^## |\Z)', body, re.M | re.S)
    return match.group(1) if match else ''


def tool_set(value):
    return {item.strip() for item in (value or '').split(',') if item.strip()}


def job_files(root):
    """Job files of a plugin root, named by file stem across skills."""
    return sorted(Path(root).glob('skills/*/jobs/*.md'))


def load(root=ROOT):
    root = Path(root)
    agents, jobs = {}, {}
    for path in sorted((root / 'agents').glob('*.md')):
        fields, _ = frontmatter(path)
        agents[path.stem] = dict(fields or {}, path=str(path))
    for path in job_files(root):
        fields, _ = frontmatter(path)
        fields = fields or {}
        jobs.setdefault(path.stem, {key: fields.get(key) for key in
                                    ('name', 'agent', 'inputs', 'returns', 'independent_of', 'acceptance')})
        jobs[path.stem]['path'] = jobs[path.stem].get('path', str(path))
    return {'agents': agents, 'jobs': jobs}


def missing_inputs(job, assignment):
    """Inputs an assignment text lacks: each must appear as `name: value` at a line start or after a space."""
    return [name for name in (*COMMON_INPUTS, *job['inputs'])
            if not re.search(rf'(?:^|\s){re.escape(name)}:[ \t]*\S', assignment, re.M)]


def input_value(job, assignment, name):
    """The value of one input in an assignment text, read as `missing_inputs` finds it: from after `name:` to the
    end of its line or to the next input's name, whichever comes first; None when the input is absent."""
    found = re.search(rf'(?:^|\s){re.escape(name)}:[ \t]*(\S.*)$', assignment, re.M)
    if not found:
        return None
    others = '|'.join(re.escape(other) for other in (*COMMON_INPUTS, *job['inputs']) if other != name)
    return re.split(rf'\s(?:{others}):', found.group(1))[0].strip()


def agent_problems(root, path):
    fields, body = frontmatter(path)
    name = f'agents/{path.name}'
    if fields is None:
        return [f'{name}: missing frontmatter']
    problems = []
    if fields.get('name') != path.stem:
        problems.append(f'{name}: name {fields.get("name")} does not match file {path.stem}')
    for key in ('description', 'tools'):
        if not isinstance(fields.get(key), str) or not fields[key]:
            problems.append(f'{name}: {key} is required')
    writes = fields.get('writes')
    allowed = tool_set(fields.get('tools')) - tool_set(fields.get('disallowedTools'))
    if writes not in WRITES:
        problems.append(f'{name}: writes must be one of {", ".join(WRITES)}')
    elif writes == 'none' and 'Write' in allowed:
        problems.append(f'{name}: writes: none but Write is allowed')
    elif writes == 'worktree' and not {'Write', 'Edit'} <= allowed:
        problems.append(f'{name}: writes: worktree but Write is not allowed')
    skills = fields.get('core_skills')
    if not isinstance(skills, list) or not all(isinstance(item, str) and item for item in skills):
        problems.append(f'{name}: core_skills must be a list of paths or host skill names')
        skills = []
    for item in skills:
        if ':' not in item and not (root / item.split('#')[0]).is_file():
            problems.append(f'{name}: core skill {item} does not exist')
    if tuple(headings(body)) != AGENT_SECTIONS:
        problems.append(f'{name}: sections must be {", ".join(AGENT_SECTIONS)}')
    principles = [line for line in section(body, 'Principles').splitlines() if line.startswith('- ')]
    if not principles:
        problems.append(f'{name}: Principles needs at least one rule')
    problems += [f'{name}: principle without "shown by:": {line[:60]}' for line in principles
                 if 'shown by:' not in line]
    if IDENTITY.search(body):
        problems.append(f'{name}: identity phrasing ("{IDENTITY.search(body).group(0)}"); state rules instead')
    size = len(path.read_bytes())
    if size > AGENT_MAX_BYTES:
        problems.append(f'{name}: {size} bytes, over {AGENT_MAX_BYTES}; job procedure belongs in a job')
    linkable = {(root / item.split('#')[0]).resolve() for item in skills if ':' not in item}
    for target in LINK.findall(body):
        if re.match(r'[a-z]+:', target) or target.startswith('#'):
            continue
        resolved = (path.parent / target.split('#')[0]).resolve()
        if resolved not in linkable and not any((root / folder).resolve() in resolved.parents
                                                for folder in ('standards', 'runtime')):
            problems.append(f'{name}: links outside core skills, standards and runtime: {target}')
    return problems


def job_problems(path, agents, job_names):
    fields, body = frontmatter(path)
    name = str(Path(*path.parts[-4:]))
    if fields is None:
        return [f'{name}: missing frontmatter']
    problems = []
    if fields.get('name') != path.stem:
        problems.append(f'{name}: name {fields.get("name")} does not match file {path.stem}')
    if fields.get('agent') not in agents:
        problems.append(f'{name}: unknown agent {fields.get("agent")}')
    for key in ('inputs', 'returns', 'independent_of'):
        if not isinstance(fields.get(key), list) or not all(isinstance(item, str) and item for item in fields[key]):
            problems.append(f'{name}: {key} must be a list of strings')
    if isinstance(fields.get('returns'), list) and not all(str(item).startswith('#') for item in fields['returns']):
        problems.append(f'{name}: returns must be Markdown headings, e.g. "## Coverage"')
    for other in fields.get('independent_of') or []:
        if other not in job_names:
            problems.append(f'{name}: independent_of names unknown job {other}')
    if not isinstance(fields.get('acceptance'), bool):
        problems.append(f'{name}: acceptance must be true or false')
    if tuple(headings(body)) != JOB_SECTIONS:
        problems.append(f'{name}: sections must be {", ".join(JOB_SECTIONS)}')
    return problems


def branches_on(path, names):
    """Agent names a script compares against; a field or loop value that shares a name does not count."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Compare):
            continue
        for operand in [node.left, *node.comparators]:
            values = operand.elts if isinstance(operand, (ast.Tuple, ast.List, ast.Set)) else [operand]
            found |= {value.value for value in values
                      if isinstance(value, ast.Constant) and value.value in names}
    return found


def problems(root=ROOT):
    root = Path(root)
    agent_paths = sorted((root / 'agents').glob('*.md'))
    job_paths = job_files(root)
    agents = {path.stem for path in agent_paths}
    found = [problem for path in agent_paths for problem in agent_problems(root, path)]
    seen = {}
    for path in job_paths:
        if path.stem in seen:
            found.append(f'job {path.stem} is defined twice: {seen[path.stem]} and {path}')
        seen.setdefault(path.stem, path)
    found += [problem for path in job_paths for problem in job_problems(path, agents, set(seen))]
    for path in sorted([*root.glob('scripts/*.py'), *root.glob('hooks/*.py')]):
        for agent in sorted(branches_on(path, agents)):
            found.append(f'{path.relative_to(root)} names agent {agent}; read it from agent_defs')
    return found


def main():
    found = problems()
    print('\n'.join(found) or 'agent and job definitions ok')
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
