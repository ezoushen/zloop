"""Seatbelt (macOS) write-scope profiles for zloop workers whose host runtime cannot restrict permissions.

Native-child and bash-executor workers get a generated profile per write scope wrapped in front of their
launch argv; the preflight probe checks that /usr/bin/sandbox-exec both exists and enforces, and its failure
selects the tool-allowlist fallback with a recorded reason, never silently. Herdr workers (the kind fixes the
executable) use each CLI's built-in sandbox flags through launch args; T3 workers run under provider defaults
(documented in hosts/t3.md). Non-macOS hosts never wrap.
Verified enforcement and the profile skeleton: .scratch/t3code-support/macos-sandbox.md.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROBE_SECONDS = 15
WRAPPED_EXECUTORS = ('subagent', 'bash')  # Launch argv zloop assembles itself; herdr/t3/skill pass theirs through.


def profile(scope, worktree, reports):
    """The Seatbelt profile text for a write scope: read-only allows temp files and the reports directory;
    worktree adds the assigned worktree."""
    if scope not in ('none', 'worktree'):
        raise ValueError(f'unknown write scope {scope!r}: profile none or worktree')
    lines = [
        '(version 1)',
        ';; zloop worker write scope: read everything, write only what the scope names.',
        '(allow default)',
        '(deny file-write*)',
        '(allow file-write*',
        '       (subpath "/private/tmp")',
        '       (subpath "/tmp")',
        '       (literal "/dev/null")',
        f'       (subpath "{reports}")',
    ]
    if scope == 'worktree':
        lines.append(f'       (subpath "{worktree}")')
    lines.append(')')
    return '\n'.join(lines) + '\n'


def _probe_profile(worktree):
    """The real worker profile shape, probing enforcement by a touch outside every allowed subpath (under the
    user's home): an enforcing sandbox-exec makes that touch fail. A deny-literal after (allow default) is not
    reliable on this macOS build; deny-all-then-allow is what the worker profiles use."""
    outside = Path.home() / f'zloop-sandbox-probe-{os.getuid()}-{os.getpid()}'
    lines = [
        '(version 1)',
        '(allow default)',
        '(deny file-write*)',
        '(allow file-write*',
        '       (subpath "/private/tmp")',
        '       (subpath "/tmp")',
        '       (literal "/dev/null")',
        '       (subpath "' + str(worktree) + '")',
        ')',
    ]
    return '\n'.join(lines) + '\n', outside


def available(worktree=None, reports=None):
    """{available, fallback_reason, probe}: True only when sandbox-exec exists AND denies an outside-scope write.
    Any failure is a recorded fallback to the tool allowlist, never silent."""
    binary = 'sandbox-exec'  # Looked up on PATH, so an isolated environment can stub it.
    if not any(Path(entry, binary).is_file() for entry in os.environ.get('PATH', '').split(os.pathsep)):
        return {'available': False, 'fallback_reason': f'{binary} does not exist on this host',
                'probe': None}
    if sys.platform != 'darwin':
        return {'available': False, 'fallback_reason': f'Seatbelt is macOS-only; this host is {sys.platform}',
                'probe': None}
    profile_text, outside = _probe_profile(worktree or tempfile.gettempdir())
    with tempfile.TemporaryDirectory(prefix='zloop seatbelt probe ') as directory:
        path = Path(directory) / 'probe.sb'
        path.write_text(profile_text)
        try:
            process = subprocess.run([binary, '-f', str(path), '/usr/bin/touch', str(outside)],
                                     capture_output=True, text=True, timeout=PROBE_SECONDS)
        except (subprocess.TimeoutExpired, OSError) as error:
            return {'available': False, 'fallback_reason': f'sandbox-exec probe failed: {error}',
                    'probe': str(path)}
        denied = process.returncode != 0 or not Path(outside).exists()
        if not denied:
            return {'available': False,
                    'fallback_reason': 'sandbox-exec did not enforce the probe denial; falling back to the '
                                       'tool allowlist', 'probe': str(path)}
    return {'available': True, 'fallback_reason': None, 'probe': str(path)}


def wrap(launch, worktree, reports):
    """The launch with a sandbox-exec profile wrapped in front of its argv when the sandbox is available and the
    executor's argv is zloop's own; otherwise the launch unchanged with the fallback reason recorded."""
    scope = launch.get('writes')
    probe = available(worktree=worktree, reports=reports)
    if not probe['available']:
        return {**launch, 'fallback_reason': probe['fallback_reason']}
    text = profile(scope, worktree=worktree, reports=reports)
    directory = Path(reports) / 'sandbox-profiles'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{scope}.sb'
    path.write_text(text)
    argv = ['sandbox-exec', '-f', str(path), *launch['argv']]
    return {**launch, 'argv': argv, 'verify': {**launch.get('verify', {}), 'sandbox_profile': scope}}
