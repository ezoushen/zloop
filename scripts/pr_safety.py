#!/usr/bin/env python3
"""Verify a GitHub merge target and submit through the trusted local T3 host."""
import argparse
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

import state_files
from human_intent import WaitingOnUser, local_mcp


MERGE_METHODS = ('merge', 'squash', 'rebase')
REVIEWED_HEAD = re.compile(r'[0-9a-f]{40}')


def command(*args):
    """Read a bounded Git or GitHub CLI response, without interactive prompts."""
    try:
        return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=30, check=True).stdout
    except (OSError, UnicodeError, subprocess.SubprocessError) as error:
        raise WaitingOnUser(f'cannot verify GitHub merge evidence ({args[0]} {args[1]})') from error


def repository():
    """Resolve the local origin, independent of the GitHub CLI's default repo."""
    origin = command('git', 'remote', 'get-url', 'origin').strip()
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)'
                         r'([\w.-]+/[\w.-]+?)(?:\.git)?/?', origin, re.ASCII)
    if not match:
        raise WaitingOnUser('cannot verify a GitHub repository from origin')
    return match[1]


def api(path, *flags):
    """Read one GitHub REST response, or all pages when the caller requests them."""
    try:
        return json.loads(command('gh', 'api', '--hostname', 'github.com', *flags, path))
    except ValueError as error:
        raise WaitingOnUser(f'cannot verify GitHub response for {path}') from error


def pull_base(repo, pr, head=None, *, workstream_branch=None):
    """Return the open PR's base and verify any expected head commit and source branch."""
    pull = api(f'repos/{repo}/pulls/{pr}')
    try:
        base = pull['base']
        if (type(pull['number']) is not int or pull['number'] != pr or pull['state'] != 'open'
                or base['repo']['full_name'].casefold() != repo.casefold()
                or not isinstance(base['ref'], str) or not base['ref'].strip()
                or head is not None and pull['head']['sha'] != head):
            raise ValueError('mismatch')
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise WaitingOnUser(f'cannot verify open PR {repo}#{pr}, its base, or its reviewed head') from error
    if workstream_branch is not None:
        try:
            matches = (pull['head']['ref'] == workstream_branch
                       and pull['head']['repo']['full_name'].casefold() == repo.casefold())
        except (AttributeError, KeyError, TypeError):
            matches = False
        if not matches:
            raise WaitingOnUser('the PR head does not match the verified workstream branch')
    return base['ref']


def required_checks(repo, base):
    """Require named checks in verified classic protection or active rules for this exact branch."""
    command('git', 'check-ref-format', f'refs/heads/{base}')
    ref = quote(base, safe='')
    branch = api(f'repos/{repo}/branches/{ref}')
    pages = api(f'repos/{repo}/rules/branches/{ref}', '--paginate', '--slurp')
    try:
        protection = branch['protection']
        if (branch['name'] != base or type(branch['protected']) is not bool
                or type(protection['enabled']) is not bool
                or protection['enabled'] and not branch['protected']):
            raise ValueError('branch mismatch')
        legacy = protection.get('required_status_checks', {})
        contexts = legacy.get('contexts', [])
        level = legacy.get('enforcement_level')
        if (level not in (None, 'off', 'non_admins', 'everyone')
                or not isinstance(contexts, list) or not all(isinstance(c, str) and c.strip() for c in contexts)):
            raise ValueError('invalid protection')
        checks = set(contexts) if protection['enabled'] and level in ('non_admins', 'everyone') else set()
        if not isinstance(pages, list) or not pages or any(not isinstance(page, list) for page in pages):
            raise ValueError('invalid rule pages')
        for page in pages:
            for rule in page:
                if not isinstance(rule['type'], str) or not rule['type']:
                    raise ValueError('invalid rule')
                if rule['type'] != 'required_status_checks':
                    continue
                scope = {'Repository': repo, 'Organization': repo.split('/')[0]}.get(rule['ruleset_source_type'])
                ruleset = rule['ruleset_id']
                if (type(ruleset) is not int or ruleset < 1
                        or not scope or rule['ruleset_source'].casefold() != scope.casefold()):
                    raise ValueError('unverified ruleset scope')
                entries = rule['parameters']['required_status_checks']
                if not isinstance(entries, list) or not all(isinstance(c['context'], str) and c['context'].strip()
                                                            for c in entries):
                    raise ValueError('invalid required checks')
                checks.update(c['context'] for c in entries)
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise WaitingOnUser(f'cannot verify required checks for {repo}:{base}') from error
    if not checks:
        raise WaitingOnUser(f'no required checks apply to {repo}:{base}')
    return sorted(checks)


def require_merge_authority(confirmed_by):
    """Keep a relayed mandate PR-only even when the operator has a human item."""
    if confirmed_by is not None:
        state_files.validate_confirmation_source(confirmed_by)
        if confirmed_by != 'human':
            raise WaitingOnUser('relayed confirmation cannot authorize auto-merge')


def requested_target(base=None, pr=None):
    """Resolve the exact origin and base for an existing PR or planned workstream branch."""
    if (base is None) == (pr is None) or pr is not None and pr < 1 or base is not None and not base.strip():
        raise WaitingOnUser('the intended GitHub base branch or PR is required')
    target = dict(repo=repository().casefold(), base=base, pr=pr)
    if pr is not None:
        target['base'] = pull_base(target['repo'], pr)
    else:
        message = 'the planned merge grant requires a checked-out workstream branch'
        try:
            ref = command('git', 'symbolic-ref', '--quiet', 'HEAD').strip()
        except WaitingOnUser as error:
            raise WaitingOnUser(message) from error
        if not ref.startswith('refs/heads/'):
            raise WaitingOnUser(message)
        target['branch'] = ref.removeprefix('refs/heads/')
    return target


def verify_target(target):
    """Verify required checks for the scoped destination and recheck any existing PR's base."""
    repo, base, pr = target['repo'], target['base'], target['pr']
    checks = required_checks(repo, base)
    if pr is not None and pull_base(repo, pr) != base:
        raise WaitingOnUser(f'PR {repo}#{pr} changed base during verification')
    return target, checks


def merge_target(pr, head, method, grant):
    """Check origin, repository policy and the exact approved target and head."""
    if pr is None or pr < 1 or not REVIEWED_HEAD.fullmatch(head or ''):
        raise WaitingOnUser('a GitHub PR number and reviewed head commit are required')
    if method not in MERGE_METHODS:
        raise WaitingOnUser('the approved GitHub merge method is required')
    repo = repository()
    if repo.casefold() != grant['repo'] or grant['pr'] not in (None, pr):
        raise WaitingOnUser('the PR does not match the verified merge grant')
    branch = grant.get('branch')
    base = pull_base(repo, pr, head, workstream_branch=branch)
    if base != grant['base']:
        raise WaitingOnUser('the PR base does not match the verified merge grant')
    required_checks(repo, base)
    if pull_base(repo, pr, head, workstream_branch=branch) != base:
        raise WaitingOnUser(f'PR {repo}#{pr} changed base during verification')
    return dict(repo=grant['repo'], base=base, pr=pr, head=head, method=method)


def merge_transport():
    """Resolve the trusted local authenticated T3 transport or defer the merge."""
    try:
        return local_mcp()
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise WaitingOnUser('the local T3 merge transport is unavailable') from error


def merge_tool(transport, tool, target, **arguments):
    """Return an optional result from one T3 tool call for the intended PR."""
    import t3_mcp

    endpoint, token = transport
    try:
        code, answer = t3_mcp.call(endpoint, token, tool,
                                   dict(repository=target['repo'], number=target['pr'], **arguments),
                                   15, local=True)
    except ValueError:
        return None
    return answer if code == 0 and isinstance(answer, dict) else None


def matches_pull(answer, target):
    """Match the host, repository and exact PR number in the native result."""
    repo, number = target['repo'], target['pr']
    return (answer.get('host') == 'github.com' and isinstance(answer.get('repository'), str)
            and answer['repository'].casefold() == repo
            and type(answer.get('number')) is int and answer['number'] == number)


def read_merge_state(transport, target):
    """Read the authoritative PR state, distinguishing missing proof and changed targets."""
    answer = merge_tool(transport, 'pull_request_read', target)
    if answer is None:
        return 'unavailable'
    if (not matches_pull(answer, target) or answer.get('baseBranch') != target['base']
            or answer.get('headSha') != target['head'] or answer.get('state') not in ('open', 'merged')):
        return 'mismatch'
    return answer['state']


def require_merge_state(observed, expected):
    """Defer when the fresh host readback cannot prove the expected PR state."""
    if observed != expected:
        detail = {
            'unavailable': 'fresh T3 pull request readback is unavailable',
            'mismatch': 'readback does not match the reviewed pull request',
            'open': 'the reviewed pull request is still open',
            'merged': 'the reviewed pull request is already merged',
        }[observed]
        raise WaitingOnUser(detail)


def submit_merge(transport, target):
    """Make one normal host submission and return whether the host reports it still open."""
    answer = merge_tool(transport, 'pull_request_merge', target, expectedHeadSha=target['head'],
                        method=target['method'], auto=False, stack=False)
    return answer is not None and matches_pull(answer, target) and answer.get('state') == 'open'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='command', required=True)
    grant = modes.add_parser('grant')
    target = grant.add_mutually_exclusive_group(required=True)
    target.add_argument('--base')
    target.add_argument('--pr', type=int)
    merge = modes.add_parser('merge')
    merge.add_argument('--pr', type=int, required=True)
    merge.add_argument('--head', required=True, help='reviewed PR head commit')
    merge.add_argument('--method', choices=MERGE_METHODS, required=True)
    for command in (grant, merge):
        command.add_argument('--card', type=Path, help='legacy autopilot run card')
    args = parser.parse_args()
    try:
        if args.command == 'merge' or args.card is not None:
            raise WaitingOnUser('automated merge authority requires a managed run and a verified human grant')
        _, checks = verify_target(requested_target(args.base, args.pr))
        print(json.dumps({'required_checks': checks}))
    except (OSError, ValueError) as error:
        parser.exit(2, f'waiting-on-user: {error}\n')


if __name__ == '__main__':
    main()
