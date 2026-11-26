"""Whether the plugin a session loaded is behind the repository it is working on: `lagging(repository)`.

A session dogfoods when the plugin root and the repository share a Git directory (a worktree of the same repository
counts). The plugin root lags when it is on the local `main` branch and its `HEAD` lacks commits that
`refs/remotes/origin/main` has. Only local refs are read and nothing is fetched, so the verdict is as fresh as the last
fetch of `origin/main`. A plugin root that is detached or on another branch, where `git pull --ff-only` does not bring
it to `origin/main`, one that is not a Git checkout, a repository without that ref, or any Git failure is not judged.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
MAIN = 'refs/remotes/origin/main'


def git(directory, *args):
    """Stdout of `git -C directory args`, stripped, or None when Git fails, is missing or does not answer."""
    try:
        done = subprocess.run(['git', '-C', str(directory), *args], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def common_directory(directory):
    """The absolute Git directory that `directory`'s checkout and worktrees share, or None outside a repository."""
    found = git(directory, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    return Path(found).resolve() if found else None


def lagging(repository, root=ROOT):
    """The refresh advice when `root`, on `main`, lags `origin/main` while `repository` shares its Git directory."""
    common = common_directory(root)
    if common is None or common != common_directory(repository):
        return None
    if git(root, 'symbolic-ref', '--quiet', 'HEAD') != 'refs/heads/main':
        return None
    count = git(root, 'rev-list', '--count', f'HEAD..{MAIN}')
    if not count or int(count) == 0:
        return None
    head, main = (git(root, 'rev-parse', '--short', revision) for revision in ('HEAD', MAIN))
    return (f'the plugin root {root} is at {head}, {count} behind origin/main at {main}, and this session works in '
            f'that repository. Refresh it with `git -C {root} pull --ff-only`, then start a new session (on Claude, '
            '/reload-plugins also works).')
