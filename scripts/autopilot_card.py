#!/usr/bin/env python3
"""Take over an autopilot run card: `autopilot_card.py take-over <card> [--auto-merge]
[--effort <level>] [--dispatch-effort <level>] [--review-effort <level>]`.

Rewrites the card's State in place. `auto-merge:` becomes `yes` only with `--auto-merge`, so a grant from the run that
ended never carries into the next. The ended run's `terminal:` state moves, with today's date, to the end of
`history:`, and `terminal:` is left empty. A card that has not ended keeps its history. `phase:` is left as
recorded: a run that ends keeps the phase it stopped in.

`dispatch-effort:` and `review-effort:` keep the levels the card confirmed unless a level flag is given: `--effort`
sets both, and a split flag overrides it for its own step. A card without either line is refused, and so is a level
outside `low|medium|high|xhigh|max`.
"""
import argparse
import datetime
import re
import sys
from pathlib import Path

LEVELS = ('low', 'medium', 'high', 'xhigh', 'max')


def field(text, key):
    """The value of the card's `key:` line, '' for a blank or `<placeholder>` value; exits when the card has no such line."""
    match = re.search(rf'^{re.escape(key)}:[ \t]*(.*)$', text, re.M)
    if not match:
        sys.exit(f'the card has no `{key}:` line')
    return '' if match.group(1).startswith('<') else match.group(1).strip()


def set_field(text, key, value):
    return re.sub(rf'^{re.escape(key)}:.*$', lambda _: f'{key}: {value}'.rstrip(), text, count=1, flags=re.M)


def take_over(text, auto_merge, dispatch_effort, review_effort, today):
    """The card's text after a take-over."""
    ended, history = field(text, 'terminal'), field(text, 'history')
    field(text, 'auto-merge')
    dispatch, review = field(text, 'dispatch-effort'), field(text, 'review-effort')
    dispatch, review = dispatch_effort or dispatch, review_effort or review
    if ended:
        text = set_field(set_field(text, 'terminal', ''), 'history', '; '.join(filter(None, [history, f'{ended} {today}'])))
    text = set_field(set_field(text, 'auto-merge', 'yes' if auto_merge else 'no'), 'dispatch-effort', dispatch)
    return set_field(text, 'review-effort', review)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['take-over'])
    parser.add_argument('card', type=Path)
    parser.add_argument('--auto-merge', action='store_true', help='grant merging to the new run')
    parser.add_argument('--effort', choices=LEVELS, help='set both steps to this level')
    parser.add_argument('--dispatch-effort', choices=LEVELS, help="set the dispatch's verification level")
    parser.add_argument('--review-effort', choices=LEVELS, help="set the review's level")
    args = parser.parse_args()
    levels = (args.dispatch_effort or args.effort, args.review_effort or args.effort)
    args.card.write_text(take_over(args.card.read_text(), args.auto_merge, *levels, datetime.date.today().isoformat()))


if __name__ == '__main__':
    main()
