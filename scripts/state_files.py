"""Atomic writes and locked updates for the state files the stop hooks read.

write_atomic(path, text) replaces a file whole: an interrupted write leaves the old content, never an empty or partial
file. The new text goes to a temporary file in the same directory, then renames over the target.

`with locked(path):` holds an exclusive lock on a sibling `<name>.lock` file, so a read-modify-write of path by
concurrent commands runs one at a time and none loses another's update. The directory must exist.
"""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import tempfile


def write_atomic(path, text):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


@contextlib.contextmanager
def locked(path):
    path = Path(path)
    with open(path.with_name(path.name + '.lock'), 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


BINDING = 'orchestrator.json'


def read_binding(path):
    """The validated orchestrator binding, or None, and its raw text for attributing a malformed file."""
    try:
        raw = path.read_text(errors='replace')
    except OSError:
        return None, ''
    try:
        binding = json.loads(raw)
        if (isinstance(binding, dict) and (binding.get('pane') is None or isinstance(binding['pane'], str))
                and isinstance(binding.get('sessions'), list)
                and all(isinstance(known, str) for known in binding['sessions'])):
            return binding, raw
    except ValueError:
        pass
    return None, raw
