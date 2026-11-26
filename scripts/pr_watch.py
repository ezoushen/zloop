#!/usr/bin/env python3
"""Bind an active managed PR phase to a local native watch and exact commit checks."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess

import human_intent
import pr_safety
import run_control
import state_files


LOCAL_TIMEOUT = 2
PENDING = ('queued', 'in_progress', 'waiting', 'requested', 'pending')
CONCLUSIONS = ('action_required', 'cancelled', 'failure', 'neutral', 'skipped', 'stale',
               'startup_failure', 'success', 'timed_out')


def scope(control):
    """Identify the current managed PR generation and its confirmed T3 conversation."""
    if control:
        values = state_files.state_fields(control['card_text'])
        if (values.get('phase') == 'pr' and not values.get('terminal')
                and not re.match(r'no\b|$', values.get('confirmed', ''), re.I)
                and (thread := values.get('intent-thread')) and not thread.startswith('-')):
            return dict(run_id=control['run_id'], revision=control['revision'], thread=thread)
    raise ValueError('A bound, active managed PR phase is required')


def repo_root(card):
    """Resolve the local main checkout whose managed card owns the watch."""
    if card.name != run_control.CARD_NAME or card.parent.parent.name != '.scratch':
        raise ValueError('Expected a managed workstream card in the main checkout')
    return card.parent.parent.parent


def git(directory, *args):
    try:
        return pr_safety.command('git', *args, cwd=directory, timeout=LOCAL_TIMEOUT).strip()
    except human_intent.WaitingOnUser as error:
        raise ValueError(f'Cannot verify the local feature worktree with git {args[0]}') from error


def local_head(root, target):
    """Require the selected branch to still point at the verified full SHA."""
    branch = target['branch']
    if not isinstance(branch, str) or not branch or branch == 'HEAD' or branch.startswith('-'):
        raise ValueError('Expected a local feature branch')
    worktree = Path(target['worktree'])
    if not worktree.is_absolute():
        raise ValueError('Expected an absolute local feature worktree')
    if (pr_safety.repository(cwd=root, timeout=LOCAL_TIMEOUT).casefold() != target['repository'].casefold()
            or git(worktree, 'rev-parse', '--path-format=absolute', '--git-common-dir') != str(root / '.git')
            or git(worktree, 'symbolic-ref', '--short', 'HEAD') != branch
            or git(worktree, 'rev-parse', '--verify', 'HEAD') != target['head_sha']):
        raise ValueError('The local feature worktree, branch, or head changed')


def watch_id(target, started):
    return human_intent.canonical_hash([target['thread'], target['repository'], target['pr'], started])


def native_watch(target):
    """Read the current exact linked PR from the OS account's verified local T3 instance."""
    human_intent.local_runtime()
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    base = home / '.t3'
    shim = base / 'bin/t3'
    for path, kind in ((shim.parent, stat.S_ISDIR), (shim, stat.S_ISREG)):
        info = path.lstat()
        if not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError('The local T3 shim is not trusted')
    try:
        result = subprocess.run([str(shim), 'thread', 'show', '--json', '--messages', '0',
                                 '--base-dir', str(base), target['thread']],
                                env={'HOME': str(home), 'PATH': os.defpath}, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, check=True, timeout=LOCAL_TIMEOUT)
        thread = json.loads(result.stdout)['thread']
        if (thread['threadId'] != target['thread'] or thread['archived'] is not False
                or thread['worktreePath'] != target['worktree'] or thread['branch'] != target['branch']):
            raise ValueError('thread or worktree mismatch')
        repo, pr = target['repository'], target['pr']
        links = [link for link in thread['pullRequests'] if link['host'] == 'github.com'
                 and link['repository'].casefold() == repo.casefold() and link['number'] == pr]
        link, = links
        if (type(link['number']) is not int or link['url'].casefold() != f'https://github.com/{repo}/pull/{pr}'.casefold()
                or link['state'] != 'open' or link['watching'] is not True):
            raise ValueError('inactive watch')
        start = link['watchStartedAt']
        if datetime.fromisoformat(start.replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('missing watch start timezone')
    except (AttributeError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as error:
        raise ValueError('The exact local T3 PR watch is not active or could not be verified') from error
    return watch_id(target, start)


def pull(target):
    """Read the exact open PR, accepting only its selected same-repository head."""
    repo, pr = target['repository'], target['pr']
    document = pr_safety.api(f'repos/{repo}/pulls/{pr}')
    try:
        head, base = document['head'], document['base']
        if (type(document['number']) is not int or document['number'] != pr or document['state'] != 'open'
                or any(item['repo']['full_name'].casefold() != repo.casefold() for item in (head, base))
                or head['ref'] != target['branch'] or head['sha'] != target['head_sha']
                or not isinstance(base['ref'], str) or not base['ref'].strip()
                or not pr_safety.REVIEWED_HEAD.fullmatch(base['sha'])):
            raise ValueError('PR scope mismatch')
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f'Cannot verify open PR {repo}#{pr} at the selected feature branch and head') from error
    return dict(base_ref=base['ref'], base_sha=base['sha'])


def checks(target):
    """Read the complete latest check-run set and verify every record names the selected commit."""
    repo, head = target['repository'], target['head_sha']
    pages = pr_safety.api(f'repos/{repo}/commits/{head}/check-runs?per_page=100', '--paginate', '--slurp')
    runs = {}
    try:
        if not isinstance(pages, list) or not pages:
            raise ValueError('no check pages')
        total = pages[0]['total_count']
        if type(total) is not int or total < 0:
            raise ValueError('invalid check count')
        for page in pages:
            if (type(page['total_count']) is not int or page['total_count'] != total
                    or not isinstance(page['check_runs'], list)):
                raise ValueError('invalid check page')
            for run in page['check_runs']:
                ident, name, status, conclusion = run['id'], run['name'], run['status'], run['conclusion']
                if (type(ident) is not int or ident < 1 or ident in runs or not isinstance(name, str) or not name.strip()
                        or run['head_sha'] != head or not (status == 'completed' and conclusion in CONCLUSIONS
                                                          or status in PENDING and conclusion is None)):
                    raise ValueError('invalid check run')
                runs[ident] = dict(id=ident, name=name, head_sha=head, status=status, conclusion=conclusion)
        if len(runs) != total:
            raise ValueError('incomplete checks')
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ValueError('Cannot verify a complete set of GitHub check runs for the selected head') from error
    return list(runs.values())


def observe(target, expected_base=None):
    """Read checks between two matching open PR snapshots and classify the result."""
    base = pull(target)
    if expected_base is not None and base != expected_base:
        raise ValueError('The PR base changed since the watch was armed')
    runs = checks(target)
    if pull(target) != base:
        raise ValueError('The PR base changed during check verification')
    conclusions = {run['conclusion'] for run in runs}
    passing = {'success', 'neutral', 'skipped'}
    if conclusions - passing - {None}:
        status = 'failure'
    elif 'success' in conclusions and None not in conclusions:
        status = 'success'
    else:
        status = 'pending'
    return dict(**base, check_runs=runs, status=status)


def arm(card, pr, head):
    """Record a verified native watch and its initial GitHub observation without granting authority."""
    control = run_control.read(card)
    root = repo_root(card)
    if pr < 1 or not pr_safety.REVIEWED_HEAD.fullmatch(head):
        raise ValueError('Expected a PR number and full feature head SHA')
    worktree = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel')).resolve()
    target = dict(**scope(control), repository=pr_safety.repository(cwd=root, timeout=LOCAL_TIMEOUT),
                  pr=pr, head_sha=head, worktree=str(worktree),
                  branch=git(worktree, 'symbolic-ref', '--short', 'HEAD'))
    local_head(root, target)
    identity = native_watch(target)
    receipt = dict(**target, watch_id=identity, **observe(target))
    with state_files.locked(card):
        if run_control.read(card) != control:
            raise ValueError('The managed run changed during watch verification')
        local_head(root, target)
        if native_watch(target) != identity:
            raise ValueError('The local T3 PR watch changed during verification')
        run_control.write_control(card, dict(control, external_wait=receipt))
    return receipt


def bound_receipt(control):
    """Read a receipt only for the current managed PR generation."""
    receipt = control.get('external_wait') if control else None
    current = scope(control)
    if not (isinstance(receipt, dict) and type(receipt.get('revision')) is int
            and all(receipt.get(key) == value for key, value in current.items())
            and type(receipt.get('pr')) is int and receipt['pr'] > 0
            and isinstance(receipt.get('head_sha'), str) and pr_safety.REVIEWED_HEAD.fullmatch(receipt['head_sha'])
            and isinstance(receipt.get('watch_id'), str) and re.fullmatch(r'[0-9a-f]{64}', receipt['watch_id'])):
        raise ValueError('No external watch is bound to this managed PR generation')
    return receipt


def refresh(card, identity):
    """Refresh the GitHub observation after a wake, even when T3 has ended its watch."""
    control = run_control.read(card)
    receipt = bound_receipt(control)
    if receipt['watch_id'] != identity:
        raise ValueError('The requested watch does not match the managed PR receipt')
    root = repo_root(card)
    problem = None
    try:
        local_head(root, receipt)
        base = {key: receipt[key] for key in ('base_ref', 'base_sha')}
        observed = observe(receipt, base)
    except (AttributeError, OSError, KeyError, TypeError, ValueError) as error:
        problem = error
    with state_files.locked(card):
        if run_control.read(card) != control:
            raise ValueError('The managed run changed during watch verification')
        if problem is None:
            try:
                local_head(root, receipt)
            except (OSError, ValueError) as error:
                problem = error
        if problem is not None:
            observed = dict(status='unverified', check_runs=[])
        result = dict(receipt, **observed)
        run_control.write_control(card, dict(control, external_wait=result))
    if problem is not None:
        raise problem
    return result


def awaiting(card, control):
    """Check a pending receipt and its live wake path without contacting GitHub or changing state."""
    try:
        receipt = bound_receipt(control)
        if receipt['status'] != 'pending' or native_watch(receipt) != receipt['watch_id']:
            return False
        local_head(repo_root(card), receipt)
        return control == run_control.read(card)
    except (AttributeError, OSError, KeyError, TypeError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    arming = commands.add_parser('arm')
    arming.add_argument('card', type=Path)
    arming.add_argument('--pr', type=int, required=True)
    arming.add_argument('--head', required=True)
    refreshing = commands.add_parser('refresh')
    refreshing.add_argument('card', type=Path)
    refreshing.add_argument('--watch-id', required=True)
    args = parser.parse_args()
    try:
        card = args.card.resolve()
        receipt = arm(card, args.pr, args.head) if args.action == 'arm' else refresh(card, args.watch_id)
    except (AttributeError, OSError, KeyError, TypeError, ValueError) as error:
        parser.exit(2, f'{error}\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
