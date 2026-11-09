"""Preflight for zloop's built-in t3 executor route: verify the T3 Code environment a route needs before any
launch. Layered, each layer with its own reason:

- home: T3CODE_HOME (default ~/.t3) with userdata/server-runtime.json — installed and has run here;
- server + project: `t3 thread list --json --project <path>` exits 0 AND its body parses as a JSON array —
  the server answers and the project is registered. Exit 1 means no server answers; exit 0 with a
  "No T3 Code project matches" message means the server is up but the project is not registered, so the
  body is parsed, never the exit code alone.

`t3 status` and bare `t3 doctor` are never liveness probes: both exit 0 while no service is installed (a
desktop-app server can still be serving), and bare doctor repairs and restarts the service. Diagnostics use
`--dry-run` only.
"""
import json
import os
import subprocess
from pathlib import Path

LOOKUP_SECONDS = 20


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
        # Exit 0 with a message (no project registered) or a body that is not a JSON array; the doc contract names
        # both, so an unparseable body is its own reason, distinct from the no-project message .
        return {'ok': False, 'reason': 'project' if code == 0 and 'No T3 Code project matches' in body else 'body',
                'home': str(resolved), 'detail': body[:200]}
    if not parsed:
        return {'ok': False, 'reason': 'project', 'home': str(resolved),
                'detail': 'the project is registered but holds no threads'}
    return {'ok': True, 'reason': None, 'home': str(resolved),
            'thread_list': {'ok': True, 'threads': len(parsed)}}


def diagnose(project, home=None):
    """Read-only diagnostics for a failing gate: doctor --dry-run only, never a repair or a restart."""
    resolved = home_dir(home)
    try:
        process = subprocess.run(['t3', 'doctor', '--dry-run'], capture_output=True, text=True,
                                 timeout=LOOKUP_SECONDS)
    except (subprocess.TimeoutExpired, FileNotFoundError) as error:
        return {'doctor': str(error)}
    return {'doctor': (process.stdout + process.stderr).strip()[:500]}
