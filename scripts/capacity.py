#!/usr/bin/env python3
"""Machine-wide leases on local model backends, so the workers of every workstream share their slots.

A lease belongs to a lifecycle attempt. It ends when the attempt's result is collected or it closes,
and a lease whose attempt is finished, unregistered or gone with its state file is reclaimed.
"""

import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request

# A lease taken while its attempt is still being registered must not look abandoned.
REGISTRATION_GRACE = 60
HEALTH_SECONDS = 2


def ledger_path():
    state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state').expanduser()
    if not state.is_absolute():
        raise ValueError('XDG_STATE_HOME must be an absolute directory')
    return state / 'zloop/capacity.sqlite'


def connect():
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30, isolation_level=None)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS leases (state TEXT NOT NULL, attempt TEXT NOT NULL, '
               'backend TEXT NOT NULL, acquired_at REAL NOT NULL, PRIMARY KEY (state, attempt))')
    return db


def live(state, attempt, acquired_at, now):
    """Whether a lease's attempt is still registered and has neither a collected result nor a closure."""
    path = Path(state)
    if not path.is_file():
        return False
    try:
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=30)
        try:
            row = db.execute('SELECT data FROM attempts WHERE id = ?', (attempt,)).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return True  # An unreadable state is not proof the worker stopped.
    if row is None:
        return now - acquired_at < REGISTRATION_GRACE
    saved = json.loads(row[0])
    return not saved.get('result') and not saved.get('closed')


def in_use(backend, now=None):
    """Live leases on a backend, without changing the ledger."""
    path = ledger_path()
    if not path.is_file():
        return 0
    now = time.time() if now is None else now
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=30)
    try:
        rows = list(db.execute('SELECT state, attempt, acquired_at FROM leases WHERE backend = ?', (backend,)))
    finally:
        db.close()
    return sum(live(state, attempt, acquired_at, now) for state, attempt, acquired_at in rows)


def free_memory_percent():
    """The machine's free memory share from macOS `memory_pressure`, or None where it is unavailable."""
    try:
        output = subprocess.run(['memory_pressure', '-Q'], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'free percentage:\s*(\d+(?:\.\d+)?)%', output)
    return float(match.group(1)) if match else None


def check(backend, config):
    """Refuse a backend whose health endpoint fails or whose machine is short of free memory."""
    if config.get('health'):
        answer = {}

        def probe():
            try:
                with urllib.request.urlopen(config['health'], timeout=HEALTH_SECONDS) as response:
                    answer['status'] = response.status
            except (OSError, urllib.error.URLError, ValueError) as error:
                answer['error'] = str(error)
        # A thread bounds the whole check, including a host name that never resolves.
        thread = threading.Thread(target=probe, daemon=True)
        thread.start()
        thread.join(HEALTH_SECONDS)
        if answer.get('status') != 200:
            reason = answer.get('error') or (f"health returned {answer['status']}" if answer else 'timed out')
            raise ValueError(f'Backend {backend} is unhealthy: {reason}')
    minimum = config.get('min_free_memory_percent')
    if minimum is not None:
        free = free_memory_percent()
        if free is not None and free < minimum:
            raise ValueError(f'Backend {backend} needs {minimum}% free memory; the machine has {free}%')


def acquire(backend, config, state, attempt, now=None):
    slots = config.get('slots')
    if type(slots) is not int or slots < 1:
        raise ValueError(f'Backend {backend} needs a positive integer slots')
    check(backend, config)
    now = time.time() if now is None else now
    db = connect()
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            for held_state, held_attempt, acquired_at in list(db.execute(
                    'SELECT state, attempt, acquired_at FROM leases WHERE backend = ?', (backend,))):
                if not live(held_state, held_attempt, acquired_at, now):
                    db.execute('DELETE FROM leases WHERE state = ? AND attempt = ?', (held_state, held_attempt))
            held = db.execute('SELECT COUNT(*) FROM leases WHERE backend = ?', (backend,)).fetchone()[0]
            if held >= slots:
                raise ValueError(f'Backend {backend} is at capacity ({held}/{slots}); route to another candidate '
                                 'or wait for a worker to finish')
            db.execute('INSERT OR REPLACE INTO leases VALUES (?, ?, ?, ?)', (state, attempt, backend, now))
    finally:
        db.close()


def release(state, attempt):
    path = ledger_path()
    if not path.is_file():
        return
    db = connect()
    try:
        db.execute('DELETE FROM leases WHERE state = ? AND attempt = ?', (state, attempt))
    finally:
        db.close()
