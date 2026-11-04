"""Atomic writes for the state files the stop hooks read.

write_atomic(path, text) replaces a file whole: an interrupted write leaves the old content, never an empty or partial
file. The new text goes to a temporary file in the same directory, then renames over the target.
"""
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
