"""Seatbelt (macOS) write-scope profiles for zloop workers whose host runtime cannot restrict permissions.

The bash executor wraps its configured worker argv with a generated profile (zloop owns that subprocess); the
profile permits writes only inside the scope (temp files, the reports directory, and for the worktree scope the
assigned worktree). Native children need no profile: their tool profile already denies write tools, and the
read-only worktree check at collection covers what their shell writes. Herdr workers use each CLI's
built-in sandbox flags through launch args; T3 workers run under provider defaults (documented in hosts/t3.md).
The preflight probe checks that sandbox-exec both exists and enforces — an inside-scope write must succeed AND
an outside-scope write must fail — and its failure selects the tool-allowlist fallback with a recorded reason,
never silently. Non-macOS hosts never wrap.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROBE_SECONDS = 15


def profile(scope, worktree, reports, *, read_bound=True, extra_reads=()):
    """The Seatbelt profile text for a write scope: writes only inside the scope's paths; reads are bounded by
    denying the user's credential directories (ssh, aws, gnupg, gcloud, netrc) — enough that a worker cannot read
    credentials, while the runtime stays loadable (macOS exec needs metadata/ioctl reads of its interpreter)."""
    if scope not in ('none', 'worktree'):
        raise ValueError(f'unknown write scope {scope!r}: profile none or worktree')
    # Seatbelt matches canonical paths: /var/folders is a symlink to /private/var/folders, so an allowed subpath
    # must be the resolved one or the write lands outside every allowance.
    reports_real = os.path.realpath(reports)
    writes = [
        f'       (subpath "{reports_real}")',
    ]
    if scope == 'worktree':
        worktree_real = os.path.realpath(worktree)
        writes.append(f'       (subpath "{worktree_real}")')
        # A linked worktree's commits land in the main checkout's shared git directory.
        import shutil as _shutil
        if _shutil.which('git'):
            git = subprocess.run(['git', '-C', worktree_real, 'rev-parse', '--git-common-dir'],
                                 capture_output=True, text=True)
            if git.returncode == 0 and git.stdout.strip():
                writes.append(f'       (subpath "{os.path.realpath(git.stdout.strip())}")')
    home = os.path.realpath(Path.home())
    lines = [
        '(version 1)',
        ';; zloop worker write scope: write only what the scope names; credential reads denied.',
        '(deny default)',
        '(allow process-exec)',
        '(allow process-fork)',
        '(allow file-read*)',
        '(allow file-write*',
        '       (subpath "/private/tmp")',
        '       (subpath "/tmp")',
        '       (subpath "' + os.path.realpath(tempfile.gettempdir()) + '")',
        '       (literal "/dev/null")',
        *writes,
        ')',
        '(allow network*)',
        '(allow sysctl-read)',
        '(allow mach-lookup)',
    ]
    for denied in ('.ssh', '.aws', '.gnupg', '.config/gcloud', '.netrc'):
        lines.append(f'(deny file-read* (subpath "{home}/{denied}"))')
    return '\n'.join(lines) + '\n'


def _probe_paths(worktree):
    """(profile text, inside path, outside path) for the probe: the real profile shape, touching inside an allowed
    subpath and under the user's home outside every allowance."""
    outside = Path.home() / f'zloop-sandbox-probe-{os.getuid()}-{os.getpid()}'
    inside_parent = Path(tempfile.mkdtemp(prefix='zloop-sandbox-probe-in-'))
    inside = inside_parent / 'inside.txt'
    text = profile('worktree', worktree=worktree or str(inside_parent), reports=str(inside_parent),
                   read_bound=False)
    return text, inside, outside, inside_parent


def available(worktree=None, reports=None):
    """{available, fallback_reason, probe}: True only when sandbox-exec exists, an inside-scope write succeeds and
    an outside-scope write fails. Any failure is a recorded fallback to the tool allowlist, never silent."""
    binary = 'sandbox-exec'  # Looked up on PATH, so an isolated environment can stub it.
    if not any(Path(entry, binary).is_file() for entry in os.environ.get('PATH', '').split(os.pathsep)):
        return {'available': False, 'fallback_reason': f'{binary} does not exist on this host',
                'probe': None}
    if sys.platform != 'darwin':
        return {'available': False, 'fallback_reason': f'Seatbelt is macOS-only; this host is {sys.platform}',
                'probe': None}
    text, inside, outside, inside_parent = _probe_paths(worktree)
    try:
        with tempfile.TemporaryDirectory(prefix='zloop seatbelt probe ') as directory:
            path = Path(directory) / 'probe.sb'
            path.write_text(text)
            try:
                inside_run = subprocess.run([binary, '-f', str(path), '/usr/bin/touch', str(inside)],
                                            capture_output=True, text=True, timeout=PROBE_SECONDS)
                outside_run = subprocess.run([binary, '-f', str(path), '/usr/bin/touch', str(outside)],
                                             capture_output=True, text=True, timeout=PROBE_SECONDS)
            except (subprocess.TimeoutExpired, OSError) as error:
                return {'available': False, 'fallback_reason': f'sandbox-exec probe failed: {error}',
                        'probe': str(path)}
            enforced = inside_run.returncode == 0 and inside.exists() \
                and outside_run.returncode != 0 and not outside.exists()
            if not enforced:
                return {'available': False,
                        'fallback_reason': 'sandbox-exec did not enforce the probe (inside write must succeed, '
                                           'outside write must fail); falling back to the tool allowlist',
                        'probe': str(path)}
    finally:
        outside.unlink(missing_ok=True)
        shutil.rmtree(inside_parent, ignore_errors=True)
    return {'available': True, 'fallback_reason': None, 'probe': str(inside)}


def wrap(argv, scope, worktree, reports, *, tag='worker', extra_reads=()):
    """The bash executor's worker argv with a sandbox-exec profile in front, when the sandbox is available;
    otherwise the argv unchanged with the fallback reason recorded. The profile file persists under the reports
    directory, named per scope and tag, so concurrent launches never overwrite one another."""
    probe = available(worktree=worktree, reports=reports)
    if not probe['available']:
        return {'argv': list(argv), 'fallback_reason': probe['fallback_reason']}
    directory = Path(reports) / 'sandbox-profiles'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{scope}-{tag}.sb'
    path.write_text(profile(scope, worktree=worktree, reports=reports, extra_reads=extra_reads))
    return {'argv': ['sandbox-exec', '-f', str(path), *argv],
            'verify': {'sandbox_profile': scope}}
