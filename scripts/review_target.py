#!/usr/bin/env python3
"""Start a bound review: resolve its target, write its report and resolve its reviewer route."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


def run(repo, *args):
    result = subprocess.run(args, cwd=repo, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f'{args[0]} failed')
    return result.stdout.rstrip('\n')


def parser():
    p = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    p.add_argument('target', help='branch, PR number, or HTTPS PR URL')
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--autofix', action='store_true')
    p.add_argument('--comments', choices=('draft', 'confirm', 'post'), default='draft',
                   help='keep local drafts, preview before posting, or authorize direct posting')
    p.add_argument('--base', help='explicit comparison ref')
    p.add_argument('--host', choices=('claude', 'codex', 'pi', 'opencode'), help='host named by SessionStart')
    p.add_argument('--workstream', help='explicit workstream slug; otherwise start a standalone review')
    return p


def pr_target(target):
    if re.fullmatch(r'[1-9][0-9]*', target):
        return True
    if '://' not in target:
        return False
    url = urlsplit(target)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or not re.fullmatch(r'/[^/]+/[^/]+/pull/[1-9][0-9]*(?:/files)?/?', url.path)):
        raise ValueError('expected an HTTPS repository pull-request URL')
    return True


def repository_url(url):
    if '://' not in url:
        url = 'ssh://' + url.replace(':', '/', 1)
    parsed = urlsplit(url)
    return parsed.hostname, tuple(parsed.path.strip('/').removesuffix('.git').lower().split('/')[:2])


def resolve(args):
    repo = args.repo.resolve()
    root = run(repo, 'git', 'rev-parse', '--show-toplevel')
    if Path(root).resolve() != repo:
        raise ValueError('--repo must be the checkout root')
    target = args.target
    is_pr = pr_target(target)
    pr = None
    if is_pr or (not args.base and shutil.which('gh')):
        repository = json.loads(run(repo, 'gh', 'repo', 'view', '--json', 'url,defaultBranchRef'))
        remotes = run(repo, 'git', 'remote').splitlines()
        identities = {repository_url(run(repo, 'git', 'remote', 'get-url', remote)) for remote in remotes}
        if repository_url(repository['url']) not in identities:
            raise ValueError('GitHub repository is not a checkout remote; select the correct repository first')
        if is_pr:
            if '://' in target and repository_url(target) != repository_url(repository['url']):
                raise ValueError('PR belongs to a different repository; select its checkout first')
            selector = target
        else:
            branch = target.removeprefix('refs/heads/')
            candidates = json.loads(run(repo, 'gh', 'pr', 'list', '--head', branch,
                                        '--state', 'open', '--json', 'number', '--limit', '100'))
            if len(candidates) > 1:
                raise ValueError('branch matches multiple PRs; use a PR number or URL')
            selector = str(candidates[0]['number']) if candidates else None
        if selector:
            pr = json.loads(run(repo, 'gh', 'pr', 'view', selector, '--json',
                                'number,url,title,body,baseRefName,baseRefOid,headRefName,headRefOid'))
            if repository_url(pr['url']) != repository_url(repository['url']):
                raise ValueError('resolved PR repository differs from checkout repository')
        default = (repository.get('defaultBranchRef') or {}).get('name')
    else:
        default = None

    def commit(ref):
        return run(repo, 'git', 'rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}')

    if is_pr:
        head = commit(pr['headRefOid'])
        base_ref = args.base or pr['baseRefOid']
    else:
        # Full refs also disambiguate a numeric branch from a PR number.
        ref = target if target.startswith(('refs/heads/', 'refs/remotes/')) else f'refs/heads/{target}'
        head = commit(ref)
        if pr and head != pr['headRefOid']:
            raise ValueError('local branch and PR head differ; choose PR number/URL or explicit --base for local review')
        base_ref = args.base or (pr['baseRefOid'] if pr else None)
        if not base_ref:
            if default:
                base_ref = f'refs/heads/{default}'
            else:
                remotes = run(repo, 'git', 'remote').splitlines()
                if len(remotes) != 1:
                    raise ValueError('default base is ambiguous; supply --base')
                base_ref = run(repo, 'git', 'symbolic-ref', f'refs/remotes/{remotes[0]}/HEAD')
    base = commit(base_ref)
    merge_bases = run(repo, 'git', 'merge-base', '--all', base, head).splitlines()
    if len(merge_bases) != 1:
        raise ValueError('review requires one unambiguous merge base')
    diff_base = merge_bases[0]
    files = run(repo, 'git', 'diff', '--no-ext-diff', '--name-only', '-z', diff_base, head).split('\0')
    return dict(repo=str(repo), target=target, pr=pr, base=base, head=head,
                diff_base=diff_base, files=[f for f in files if f],
                autofix=args.autofix, comments=args.comments)


def start(args):
    """Resolve and bind one review, returning its report, route and lifecycle commands."""
    resolved = resolve(args)
    if not args.host:
        raise ValueError('starting a review requires --host from the SessionStart hint')
    sessions = [os.environ[name] for name in ('CLAUDE_CODE_SESSION_ID', 'CODEX_SESSION_ID', 'PI_SESSION_ID')
                if os.environ.get(name)]
    pane = os.environ.get('HERDR_PANE_ID')
    if not sessions and not pane:
        raise ValueError('no host session ID or Herdr pane; the review stop hold cannot bind this session')
    if args.workstream is not None and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', args.workstream):
        raise ValueError('--workstream must be a filesystem-safe slug')
    repo = Path(resolved['repo'])
    main = Path(run(repo, 'git', 'rev-parse', '--path-format=absolute', '--git-common-dir')).parent
    identity = hashlib.sha256((args.target + '\0' + resolved['head']).encode()).hexdigest()[:20]
    directory = (main / '.scratch' / args.workstream / 'reports' if args.workstream
                 else main / '.scratch/reviews' / identity)
    report = directory / ('spec-review.md' if args.workstream else 'review.md')
    state, binding_path = directory / 'workers.sqlite', directory / 'review.json'
    previous = json.loads(binding_path.read_text()) if binding_path.is_file() else None
    if (report.exists() or previous) and (
            not previous or previous.get('repo') != str(main) or previous.get('target') != args.target):
        raise ValueError(f'{report}: existing report belongs to a different target; choose another --workstream')
    if previous:
        sessions = list(dict.fromkeys(previous['sessions'] + sessions))
    route_argv = [sys.executable, str(ROOT / 'scripts/agent_models.py'), '--repo', str(main),
                  '--host', args.host, '--route', '--job', 'diff-review', '--reports', str(directory)]
    routed = subprocess.run(route_argv, cwd=repo, text=True, capture_output=True, timeout=30)
    route = ({'status': 'no_route', 'reason': routed.stderr.strip() or routed.stdout.strip() or
              f'agent_models.py exited {routed.returncode}'} if routed.returncode else
             {'status': 'resolved', 'result': json.loads(routed.stdout)})
    if route.get('result', {}).get('status') == 'selection_required':
        route['status'] = 'selection_required'
    lifecycle = [sys.executable, str(ROOT / 'scripts/worker_lifecycle.py')]
    commands = {'route': shlex.join(route_argv)}
    for action, verb in (('register', 'record'), ('submit', 'submit'), ('collect', 'collect')):
        commands[action] = shlex.join(lifecycle + [verb, '--state', str(state),
                                                  *([] if action == 'register' else ['--attempt', '<attempt>']),
                                                  '--input', str(directory / f'{action}.json')])
    template = (ROOT / 'skills/review/report.md').read_text().split('```markdown\n', 1)[1].split('```', 1)[0]
    front, body = template.split('\n---\n', 1)
    fields = dict(review_head=resolved['head'], review_base=resolved['base'], review_diff_base=resolved['diff_base'],
                  autofix=str(args.autofix).lower(), review_level='high')
    for key, value in fields.items():
        front = re.sub(rf'(?m)^{key}: .*$', f'{key}: {value}', front)
    headings = re.findall(r'(?m)^#{2,3} .+$', body)
    content = front + '\n---\n\n' + '\n\n'.join(headings) + '\n'
    content = content.replace('## Scope and priorities\n', '## Scope and priorities\n\n```json\n'
                              + json.dumps(resolved, indent=2) + '\n```\n', 1)
    directory.mkdir(parents=True, exist_ok=True)
    if previous and report.is_file():
        front, body = report.read_text().split('\n---\n', 1)
        refresh = dict(fields, review_status='incomplete', documentation_status='incomplete', review_by='none',
                       blocker='none', e2e_status='not-run', e2e_head='none', e2e_by='none')
        del refresh['review_level']
        for key, value in refresh.items():
            front = re.sub(rf'(?m)^{key}:[^\n]*(?:\n[ \t]+-[^\n]*)*', f'{key}: {value}', front)
        body = body.replace(json.dumps(previous['resolution'], indent=2), json.dumps(resolved, indent=2), 1)
        content = front + '\n---\n' + body
    if not report.is_file() or not previous or previous['resolution'] != resolved:
        report.write_text(content)
    binding = dict(pane=pane, sessions=sessions, report=str(report), state=str(state),
                   repo=str(main), target=args.target, resolution=resolved, route=route, next_commands=commands)
    binding_path.write_text(json.dumps(binding, indent=2) + '\n')
    if 'result' in route:
        (directory / 'execution.json').write_text(json.dumps(route['result'], indent=2) + '\n')
    return dict(resolved, report=str(report), state=str(state), binding=str(binding_path), route=route,
                next_commands=commands)


if __name__ == '__main__':
    p = parser()
    try:
        print(json.dumps(start(p.parse_args()), indent=2))
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        p.exit(2, f'review target: {error}\n')
