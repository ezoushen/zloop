#!/usr/bin/env python3
"""Wait until a worker with no wait process of its own has finished a turn: a `watch` argv.

usage: native_wait.py claude <session record> [--timeout SECONDS] [--interval SECONDS]
       native_wait.py t3 <thread> [--timeout SECONDS] [--interval SECONDS]

claude, a native subagent: exits 0 once the record's last assistant row ended its turn (`stop_reason` is `end_turn`)
with no user row after it (tool results are user rows, so a turn in progress always has one); a missing record is
waited for. Arm it after the assignment is sent: an end_turn already followed by no user row counts as finished.

t3, a worker in a T3 Code thread on this machine: exits 0 once T3 reports the thread's turn completed, and 1, printing
T3's answer, once the turn ended any other way, the agent waits for an answer in the app, or the thread is gone. A turn
still running and a read T3 does not answer are waited on. Arm it after `submit`: T3 reports the thread running from
the moment it accepts the send.

Both exit 124 when the timeout passes first, and 2 for any other runtime.
"""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from worker_lifecycle import t3_state  # noqa: E402


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


def t3_turn(thread):
    """(Exit code once the thread's turn is over, None while it runs or T3 does not answer; T3's answer as one line)."""
    line, found, placed = t3_state({'t3': {'thread': thread}})
    if found in ('busy', 'silent'):
        return None, line
    return int(found != 'idle' or placed.get('status') != 'completed'), line


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('runtime')
    parser.add_argument('target', help='the claude session record, or the t3 thread id')
    parser.add_argument('--timeout', type=float, default=3600)
    parser.add_argument('--interval', type=float, default=2)
    args = parser.parse_args()
    if args.runtime not in ('claude', 't3'):
        parser.exit(2, f'native_wait.py waits for a claude session record or a t3 thread, not {args.runtime}\n')
    deadline, last = time.monotonic() + args.timeout, ''
    while time.monotonic() < deadline:
        if args.runtime == 't3':
            code, answer = t3_turn(args.target)
            if code is not None:
                print(answer)
                return code
            last = f' ({answer})'
        elif Path(args.target).is_file() and turn_ended(Path(args.target)):
            return 0
        time.sleep(args.interval)
    print(f'The worker did not end a turn within {args.timeout:g} seconds{last}', file=sys.stderr)
    return 124


if __name__ == '__main__':
    sys.exit(main())
