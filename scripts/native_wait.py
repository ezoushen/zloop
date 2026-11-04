#!/usr/bin/env python3
"""Wait until a native Claude subagent has finished a turn: a `watch` argv for a worker with no wait process.

usage: native_wait.py claude <session record> [--timeout SECONDS] [--interval SECONDS]

Exits 0 once the record's last assistant row ended its turn (`stop_reason` is `end_turn`) with no user row after it (tool
results are user rows, so a turn in progress always has one), and 124 when the timeout passes first, a missing record
included. Exits 2 for any other runtime. Arm it after the assignment is sent: an end_turn already followed by no user
row counts as finished.
"""
import argparse
import json
from pathlib import Path
import sys
import time


def turn_ended(path):
    """Whether the record's last assistant row ended its turn with no user row after it; a half-written line is ignored."""
    for line in reversed(path.read_bytes().splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('type') == 'user':
            return False
        if row.get('type') == 'assistant':
            return row.get('message', {}).get('stop_reason') == 'end_turn'
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('runtime')
    parser.add_argument('record', type=Path)
    parser.add_argument('--timeout', type=float, default=3600)
    parser.add_argument('--interval', type=float, default=2)
    args = parser.parse_args()
    if args.runtime != 'claude':
        parser.exit(2, f'native_wait.py reads a claude session record only, not {args.runtime}\n')
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if args.record.is_file() and turn_ended(args.record):
            return 0
        time.sleep(args.interval)
    print(f'The worker did not end a turn within {args.timeout:g} seconds', file=sys.stderr)
    return 124


if __name__ == '__main__':
    sys.exit(main())
