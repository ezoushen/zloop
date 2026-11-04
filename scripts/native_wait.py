#!/usr/bin/env python3
"""Wait until a native Claude subagent has finished a turn: a `watch` argv for a worker with no wait process.

usage: native_wait.py claude <session record> [--timeout SECONDS] [--interval SECONDS]

Exits 0 once the session record has grown past its size when this script started and its last assistant turn ended
(`stop_reason` is `end_turn`), and 124 when the timeout passes first. Exits 2 for any other runtime or a missing record.
"""
import argparse
import json
from pathlib import Path
import sys
import time


def turn_ended(path):
    """Whether the record's last assistant row ended its turn; a half-written last line is ignored."""
    for line in reversed(path.read_bytes().splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
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
    if not args.record.is_file():
        parser.exit(2, f'No session record at {args.record}\n')
    start = args.record.stat().st_size
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if args.record.stat().st_size > start and turn_ended(args.record):
            return 0
        time.sleep(args.interval)
    print(f'The worker did not end a turn within {args.timeout:g} seconds', file=sys.stderr)
    return 124


if __name__ == '__main__':
    sys.exit(main())
