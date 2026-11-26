#!/usr/bin/env python3
"""Verify effective GitHub required checks before granting or submitting a merge."""
import argparse
import json
import re
import subprocess
from urllib.parse import quote


class WaitingOnUser(ValueError):
    """Required merge evidence is absent or cannot be verified."""


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


def pull_base(repo, pr, head=None):
    """Return the named open PR's base after verifying repository and optional reviewed head."""
    pull = api(f'repos/{repo}/pulls/{pr}')
    try:
        base = pull['base']
        if (type(pull['number']) is not int or pull['number'] != pr or pull['state'] != 'open'
                or base['repo']['full_name'].casefold() != repo.casefold()
                or not isinstance(base['ref'], str) or not base['ref'].strip()
                or head is not None and pull['head']['sha'] != head):
            raise ValueError('mismatch')
        return base['ref']
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise WaitingOnUser(f'cannot verify open PR {repo}#{pr}, its base, or its reviewed head') from error


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


def verify_grant(base=None, pr=None):
    """Verify an explicit planned base or an existing PR before recording a grant."""
    if (base is None) == (pr is None) or pr is not None and pr < 1 or base is not None and not base.strip():
        raise WaitingOnUser('the intended GitHub base branch or PR is required')
    repo = repository()
    return required_checks(repo, pull_base(repo, pr) if pr is not None else base)


def submit_merge(pr, head, method):
    """Submit a reviewed PR through GitHub's normal approval and check enforcement."""
    if pr is None or pr < 1 or not re.fullmatch(r'[0-9a-f]{40}', head or ''):
        raise WaitingOnUser('a GitHub PR number and reviewed head commit are required')
    if method not in ('merge', 'squash', 'rebase'):
        raise WaitingOnUser('the approved GitHub merge method is required')
    repo = repository()
    base = pull_base(repo, pr, head)
    required_checks(repo, base)
    # GitHub can condition submission on the head SHA, but has no corresponding base guard.
    if pull_base(repo, pr, head) != base:
        raise WaitingOnUser(f'PR {repo}#{pr} changed base during verification')
    return command('gh', 'pr', 'merge', str(pr), '--repo', f'github.com/{repo}',
                   '--match-head-commit', head, f'--{method}')


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
    merge.add_argument('--method', choices=('merge', 'squash', 'rebase'), required=True)
    args = parser.parse_args()
    try:
        if args.command == 'grant':
            print(json.dumps({'required_checks': verify_grant(args.base, args.pr)}))
        else:
            print(submit_merge(args.pr, args.head, args.method), end='')
    except WaitingOnUser as error:
        parser.exit(2, f'waiting-on-user: {error}\n')


if __name__ == '__main__':
    main()
