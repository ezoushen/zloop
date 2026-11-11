"""Preflight for zloop's built-in t3 executor route: verify the T3 Code environment a route needs before any
launch. Layered, each layer with its own reason:

- home: T3CODE_HOME (default ~/.t3) with userdata/server-runtime.json — installed and has run here;
- server + project: `t3 thread list --json --project <path>` exits 0 AND its body parses as a JSON array —
  the server answers and the project is registered. Exit 1 means no server answers; exit 0 with a
  "No T3 Code project matches" message means the server is up but the project is not registered, so the
  body is parsed, never the exit code alone.
- environment: `t3 environment list --json` answers with the linked environments and whether each answers now;
  a route naming one of them needs it linked, answering, and granting access at or above the route's need.

`t3 status` and bare `t3 doctor` are never liveness probes: both exit 0 while no service is installed (a
desktop-app server can still be serving), and bare doctor repairs and restarts the service. Diagnostics use
`--dry-run` only.
"""
import json
import os
import subprocess
from pathlib import Path

LOOKUP_SECONDS = 20

# A link's access ladder (the v0.1.8 wire's `access` levels, narrowest first) and the floor each agent writes
# scope needs: a read-only link grants only the orchestration-read scope, so it cannot launch a thread there;
# a worktree-writing worker needs edits accepted, which approval-required does not cover.
ACCESS_RANK = {'read-only': 0, 'approval-required': 1, 'auto-accept-edits': 2, 'auto': 3, 'full-access': 4}
NEED_FLOOR = {'none': 'approval-required', 'worktree': 'auto-accept-edits'}


def home_dir(home=None):
    """The environment's data directory: the override, T3CODE_HOME, or the default ~/.t3."""
    if home:
        return Path(home)
    return Path(os.environ.get('T3CODE_HOME') or Path.home() / '.t3')


def list_threads(project, timeout=LOOKUP_SECONDS):
    """(exit code, parsed body or None, stdout text) for `t3 thread list --json --project <project>`."""
    try:
        process = subprocess.run(['t3', 'thread', 'list', '--json', '--project', str(project)],
                                 capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as error:
        return None, None, str(error)
    body = process.stdout.strip()
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    return process.returncode, parsed, body


def verify(project, home=None):
    """{ok, reason, ...} for the environment a t3 route needs; reason names the failing layer: home, server or
    project. Never runs `t3 status` or `doctor`."""
    resolved = home_dir(home)
    if not (resolved / 'userdata' / 'server-runtime.json').is_file():
        return {'ok': False, 'reason': 'home', 'home': str(resolved),
                'detail': f'no server-runtime.json under {resolved}/userdata'}
    code, parsed, body = list_threads(project)
    if code == 1 or code is None:
        return {'ok': False, 'reason': 'server', 'home': str(resolved),
                'detail': body[:200] if body else 't3 thread list did not answer'}
    if not isinstance(parsed, list):
        # Exit 0 with the no-project message means the project is not registered; any other non-array body did not
        # parse — a distinct reason from the no-project message.
        return {'ok': False, 'reason': 'project' if 'No T3 Code project matches' in body else 'body',
                'home': str(resolved), 'detail': body[:200]}
    return {'ok': True, 'reason': None, 'home': str(resolved),
            'thread_list': {'ok': True, 'threads': len(parsed)}}


def verify_environment(target, writes, timeout=LOOKUP_SECONDS):
    """{ok, reason, problem?, environment?} for the linked environment a t3 route names: `t3 environment list
    --json` must answer with a link matching `target` by label or environmentId whose status is reachable and
    whose access is at or above the `writes` scope's floor. The refusal is the environment layer's own, with a
    distinct problem per rule: unlinked, unreachable or ceiling."""
    floor = NEED_FLOOR.get(writes)
    if floor is None:
        raise ValueError(f'Unknown writes scope {writes!r}; the access floor is unknown')
    try:
        process = subprocess.run(['t3', 'environment', 'list', '--json'], capture_output=True, text=True,
                                 timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as error:
        return _environment_problem('unreachable', target, str(error))
    try:
        links = json.loads(process.stdout.strip())
    except ValueError:
        links = None
    if process.returncode != 0 or not isinstance(links, list):
        detail = (process.stdout.strip() or process.stderr.strip())[:200]
        return _environment_problem('unreachable', target, detail or 't3 environment list did not answer')
    for link in links:
        if not isinstance(link, dict) or target not in (link.get('label'), link.get('environmentId')):
            continue
        label, environment_id = link.get('label', target), link.get('environmentId', target)
        if link.get('status') != 'reachable':
            return _environment_problem('unreachable', target,
                                        f'{label} ({environment_id}) is {link.get("status")!r}')
        access = link.get('access')
        if ACCESS_RANK.get(access, -1) < ACCESS_RANK[floor]:
            return _environment_problem('ceiling', target, f'{label} grants {access!r} access; '
                                        f'a {writes!r} route needs {floor}')
        return {'ok': True, 'reason': None,
                'environment': {'id': environment_id, 'label': label, 'access': access}}
    return _environment_problem('unlinked', target, 'no linked environment matches it')


def _environment_problem(problem, target, detail):
    """The environment layer's refusal: the layer reason, the distinct problem and the environment named."""
    return {'ok': False, 'reason': 'environment', 'problem': problem,
            'detail': f'{problem}: linked environment {target!r}: {detail}'}


def diagnose(project, home=None):
    """Read-only diagnostics for a failing gate: doctor --dry-run only, never a repair or a restart."""
    resolved = home_dir(home)
    try:
        process = subprocess.run(['t3', 'doctor', '--dry-run'], capture_output=True, text=True,
                                 timeout=LOOKUP_SECONDS)
    except (subprocess.TimeoutExpired, FileNotFoundError) as error:
        return {'doctor': str(error)}
    return {'doctor': (process.stdout + process.stderr).strip()[:500]}
