#!/usr/bin/env python3
"""Take over an autopilot run card: `autopilot_card.py take-over <card> [--auto-merge]`.

Rewrites the card's State in place. `auto-merge:` becomes `yes` only with `--auto-merge`, so a grant from the run that
ended never carries into the next. The ended run's `terminal:` state moves, with today's date, to the end of
`history:`, and `terminal:` is left empty. A card that has not ended keeps its history. Restoring the phase is the
caller's: the card does not record the phase the run stopped in.
"""
import argparse
import datetime
from pathlib import Path
import re
import sys


def field(text, key):
    """The value of the card's `key:` line, '' for a blank or `<placeholder>` value; exits when the card has no such line."""
    match = re.search(rf'^{re.escape(key)}:[ \t]*(.*)$', text, re.M)
    if not match:
        sys.exit(f'the card has no `{key}:` line')
    return '' if match.group(1).startswith('<') else match.group(1).strip()


def set_field(text, key, value):
    return re.sub(rf'^{re.escape(key)}:.*$', lambda _: f'{key}: {value}'.rstrip(), text, count=1, flags=re.M)


def take_over(text, auto_merge, today):
    """The card's text after a take-over."""
    ended, history = field(text, 'terminal'), field(text, 'history')
    field(text, 'auto-merge')
    if ended:
        text = set_field(set_field(text, 'terminal', ''), 'history', '; '.join(filter(None, [history, f'{ended} {today}'])))
    return set_field(text, 'auto-merge', 'yes' if auto_merge else 'no')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['take-over'])
    parser.add_argument('card', type=Path)
    parser.add_argument('--auto-merge', action='store_true', help='grant merging to the new run')
    args = parser.parse_args()
    args.card.write_text(take_over(args.card.read_text(), args.auto_merge, datetime.date.today().isoformat()))


if __name__ == '__main__':
    main()
