#!/usr/bin/env python3
"""Machine-wide leases on local model backends, so the workers of every workstream share their slots.

A lease belongs to a lifecycle attempt. Collection, closure or removal of its state frees the slot.
An expired lease is reclaimable once its verified local owner has exited or its PID has been reused.
"""

import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time

# A lease taken while its attempt is still being registered must not look abandoned.
REGISTRATION_GRACE = 60
LEASE_TTL = 60
HEALTH_SECONDS = 2


def ledger_path():
    state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state').expanduser()
    if not state.is_absolute():
        raise ValueError('XDG_STATE_HOME must be an absolute directory')
    return state / 'zloop/capacity.sqlite'


def connect(timeout=30):
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=timeout, isolation_level=None)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS leases (state TEXT NOT NULL, attempt TEXT NOT NULL, '
               'backend TEXT NOT NULL, acquired_at REAL NOT NULL, PRIMARY KEY (state, attempt))')
    return db


def process_stamp(pid):
    """Return the local start stamp on a successful ps lookup."""
    process = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='], capture_output=True,
                             text=True, timeout=3, env=dict(os.environ, LC_ALL='C', TZ='UTC'))
    return process.stdout.strip() if process.returncode == 0 else None


def owner_alive(owner):
    """Return False for a missing or reused PID, True for a match, and None for unavailable evidence."""
    pid, stamp = owner['pid'], owner['stamp']
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return None
    try:
        observed = process_stamp(pid)
    except (OSError, subprocess.SubprocessError):
        return None
    return observed == stamp if observed else None


def registration_lock_path(state):
    """The lock shared by registrations and probed before reclaiming an orphan reservation."""
    return f'{state}.registration.lock'


def registration_active(state):
    """Whether a registration might still be recording the attempt."""
    try:
        with open(registration_lock_path(state), 'rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return False


def live(state, attempt, acquired_at, now, *, registering=False):
    """Whether a lease still protects a reservation or work that may be active."""
    path = Path(state)
    if not path.is_file():
        return False
    try:
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=30)
        try:
            query = 'SELECT data FROM attempts WHERE id = ?'
            row = db.execute(query, (attempt,)).fetchone()
            if row is None:
                if now - acquired_at < REGISTRATION_GRACE:
                    return True
                if not registering:
                    if registration_active(state):
                        return True
                    row = db.execute(query, (attempt,)).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return True  # An unreadable state is not proof the worker stopped.
    if row is None:
        return False
    saved = json.loads(row[0])
    if saved.get('result') or saved.get('closed'):
        return False
    owner = saved.get('local_owner')
    return not owner or now - acquired_at < LEASE_TTL or owner_alive(owner) is not False


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
            import urllib.request  # Here, not at the top: most starts of this script check no backend.
            try:
                with urllib.request.urlopen(config['health'], timeout=HEALTH_SECONDS) as response:
                    answer['status'] = response.status
            except (OSError, ValueError) as error:
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


def acquire(backend, config, state, attempt, now=None, *, registering=False):
    """Reserve or refresh an attempt's slot. registering requires the state's lifecycle write transaction."""
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
                if not live(held_state, held_attempt, acquired_at, now, registering=registering and held_state == state):
                    db.execute('DELETE FROM leases WHERE state = ? AND attempt = ?', (held_state, held_attempt))
            reservation = db.execute('SELECT backend, acquired_at FROM leases WHERE state = ? AND attempt = ?',
                                     (state, attempt)).fetchone()
            if reservation:
                reserved_backend, acquired_at = reservation
                if reserved_backend == backend:
                    db.execute('UPDATE leases SET acquired_at = ? WHERE state = ? AND attempt = ?',
                               (now, state, attempt))
                    return
                if live(state, attempt, acquired_at, now, registering=registering):
                    raise ValueError(f'Attempt {attempt} already holds capacity on backend {reserved_backend}')
                db.execute('DELETE FROM leases WHERE state = ? AND attempt = ?', (state, attempt))
            held = db.execute('SELECT COUNT(*) FROM leases WHERE backend = ?', (backend,)).fetchone()[0]
            if held >= slots:
                raise ValueError(f'Backend {backend} is at capacity ({held}/{slots}); route to another candidate '
                                 'or wait for a worker to finish')
            db.execute('INSERT INTO leases VALUES (?, ?, ?, ?)', (state, attempt, backend, now))
    finally:
        db.close()


def release(state, attempt, timeout=30):
    path = ledger_path()
    if not path.is_file():
        return
    db = connect(timeout)
    try:
        db.execute('DELETE FROM leases WHERE state = ? AND attempt = ?', (state, attempt))
    finally:
        db.close()
