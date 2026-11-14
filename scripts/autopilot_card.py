#!/usr/bin/env python3
"""Confirm an autopilot run card, set its phase or take it over: `autopilot_card.py confirm <card> --words "<the user's
words>"`, `autopilot_card.py phase <card> <align|refine|dispatch|pr>`, or `autopilot_card.py take-over <card>
[--auto-merge] [--effort <level>] [--dispatch-effort <level>] [--review-effort <level>]`.

A phase of dispatch or pr needs the tickets of the board the card names on `board:`: a tracker's URL holds its own; a
`local` board needs ticket files that pass `next_tickets.py --check`.

A confirmation records the date and the user's words on `confirmed:` (on a `mode: direct` card, as `by rule` with
the goal the user typed, since a direct run asks nothing), and on `session:` each host session ID this shell exports,
as the stop hook counts them, so the panel and the stop hold find the run. A direct card whose local board holds more
than two tickets is refused its dispatch: it takes the full path, and a `mode: full` card still confirmed `by rule` is
refused dispatch and pr until `confirm --words` records the user's words.

Rewrites the lines of the card's `## State` section in place and nothing else, so a Goal quoting `auto-merge:` is
left alone. `auto-merge:` becomes `yes` only with `--auto-merge`, so a grant from the run that
ended never carries into the next. The ended run's `terminal:` state moves, with today's date, to the end of
`history:`, and `terminal:` is left empty. A card that has not ended keeps its history. `phase:` is left as
recorded: a run that ends keeps the phase it stopped in.

`dispatch-effort:` and `review-effort:` keep the levels the card confirmed unless a level flag is given: `--effort`
sets both, and a split flag overrides it for its own step. A card without either line, or with a level outside
`low|medium|high|xhigh|max`, is refused.
"""
import argparse
import datetime
import re
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import front_matter  # noqa: E402
import state_files  # noqa: E402
import next_tickets  # noqa: E402
from worker_lifecycle import SESSION_VARIABLES, host_sessions, state_fields  # noqa: E402

LEVELS = ('low', 'medium', 'high', 'xhigh', 'max')
# The lines of the run card format's State, every one written before the user confirms the card.
STATE_LINES = ('confirmed', 'session', 'auto-merge', 'mode', 'board', 'dispatch-effort', 'review-effort', 'phase', 'terminal',
               'history', 'evidence', 'timer', 'resume')
RUN_CARD = Path(__file__).resolve().parents[1] / 'skills' / 'autopilot' / 'run-card.md'
PHASES = ('align', 'refine', 'dispatch', 'pr')
TICKETS = Path(__file__).resolve().parent / 'next_tickets.py'
MODES = ('full', 'direct')
DIRECT_TICKETS = 2  # A direct run finishes at most this many tickets; more is the full path.


def field(text, key):
    """The value of the State's `key:` line, '' for a blank or `<placeholder>` value; exits when the card has no such line."""
    fields = state_fields(text)
    if key not in fields:
        sys.exit(f'the card has no `{key}:` line')
    return fields[key]


def set_field(text, key, value):
    return re.sub(rf'^{re.escape(key)}:.*$', lambda _: f'{key}: {value}'.rstrip(), text, count=1, flags=re.M)


def level(text, key):
    """The card's `key:` level; exits when the line is blank, a placeholder or outside LEVELS."""
    value = field(text, key)
    if value not in LEVELS:
        sys.exit(f'the card\'s `{key}:` line is not one of {", ".join(LEVELS)}: {value!r}')
    return value


def state_section(text):
    """The card's `## State` section, as a match; exits when the card has none."""
    state = re.search(r'^## State[ \t]*\n.*?(?=^## |\Z)', text, re.M | re.S)
    if not state:
        sys.exit('the card has no `## State` section')
    return state


def session_line(sessions, command):
    """The `session:` value naming each host session exported here; exits when there is none."""
    if not sessions:
        sys.exit(f'no host session ID is exported here ({", ".join(SESSION_VARIABLES)}): {command} from the shell of '
                 'the host session that runs this autopilot')
    return ', '.join(sessions)


def tracker_url(board):
    """Whether `board` names a tracker by its web address: http or https, a host, and nothing that splits it."""
    try:
        address = urlsplit(board)
        address.port  # Raises on a port that is no number in range.
    except ValueError:
        return False
    return (address.scheme in ('http', 'https') and bool(re.fullmatch(r'[a-z0-9.-]+', address.hostname or ''))
            and not re.search(r'\s', board))


def ticket_files(issues):
    """The files of a local board that are tickets as the board reads them: a front matter with a `ticket:` key, whose
    value the board's own check judges, or with a ticket's other keys, or a file it cannot read, both of which that check
    names."""
    def ticket(path):
        try:
            fields = front_matter.parse(path.read_text()) or {}
        except (OSError, ValueError):  # Unreadable, or not text: the board refuses it, so it is one.
            return True
        return 'ticket' in fields or bool(set(fields) & next_tickets.TICKET_KEYS)
    return [path for path in issues.glob('*.md') if ticket(path)]


def board_problem(card, state):
    """Why the run of `card` cannot work its board yet, or None: a tracker's URL holds its own tickets; a `local` board
    needs ticket files that pass the board's check."""
    if state_fields(state).get('mode') == 'full' and re.match(r'\S+ by rule "', field(state, 'confirmed')):
        return (f"the full card of {card.parent.name} was confirmed by rule, not by the user: have the user confirm it, "
                "then record it with `confirm --words` and the user's words")
    board = field(state, 'board')
    if tracker_url(board):
        return None
    if board != 'local':
        return f"the card's `board:` is neither `local` nor a tracker's URL: {board!r}"
    slug, repo = card.parent.name, card.resolve().parents[2]
    tickets = ticket_files(card.parent / 'issues')
    if state_fields(state).get('mode') == 'direct' and len(tickets) > DIRECT_TICKETS:
        return (f'the direct card of {slug} has {len(tickets)} tickets, above {DIRECT_TICKETS}: take the full path, '
                'set `mode: full` and have the user confirm the card')
    if not tickets:
        new = shlex.join(['python3', str(TICKETS), '--repo', str(repo), slug, '--new', '<title>'])
        return f'the local board of {slug} has no tickets: write each with `{new}`, then set the phase'
    check = subprocess.run([sys.executable, str(TICKETS), '--repo', str(repo), slug, '--check'],
                           capture_output=True, text=True, timeout=60)
    if check.returncode:
        return f'the local board of {slug} fails its check: {(check.stderr or check.stdout).strip()}'
    return None


def phase(card, text, value):
    """The card's text with `phase:` set to `value`; exits when dispatch or pr has no board to work."""
    state = state_section(text)
    problem = value in ('dispatch', 'pr') and board_problem(card, state[0])
    if problem:
        sys.exit(problem)
    lines = state[0]
    if not re.search(r'^phase:', lines, re.M):  # A State that lost its line gets one.
        kept = lines.rstrip('\n')
        lines = kept + '\nphase:' + lines[len(kept):]
    return text[:state.start()] + set_field(lines, 'phase', value) + text[state.end():]


def confirm(text, words, sessions, today):
    """The card's text once the user confirmed it; only `confirmed:` and `session:` of its State change. Exits on a
    State missing a line of the format, on no words and on no host session."""
    state = state_section(text)
    missing = [key for key in STATE_LINES if not re.search(rf'^{re.escape(key)}:', state[0], re.M)]
    if missing:
        sys.exit(f"the card's State lacks {', '.join(missing)}: write each line of the run card's State ({RUN_CARD}), "
                 'then confirm')
    mode = field(state[0], 'mode')
    if mode not in MODES:
        sys.exit(f'the card\'s `mode:` line is not one of {", ".join(MODES)}: {mode!r}')
    words = ' '.join((words or '').split())  # On one line, so no word can become a State line.
    if not words:
        sys.exit("confirm takes --words: the user's words that confirmed this card")
    by_rule = ' by rule' if mode == 'direct' else ''
    lines = set_field(set_field(state[0], 'confirmed', f'{today}{by_rule} "{words}"'), 'session', session_line(sessions, 'confirm'))
    return text[:state.start()] + lines + text[state.end():]


def take_over(text, auto_merge, dispatch_effort, review_effort, today, sessions):
    """The card's text after a take-over by the host sessions exported here; only the lines of its `## State` section
    change."""
    state = state_section(text)
    taken = take_over_state(state[0], auto_merge, dispatch_effort, review_effort, today)
    return text[:state.start()] + set_field(taken, 'session', session_line(sessions, 'take over')) + text[state.end():]


def take_over_state(text, auto_merge, dispatch_effort, review_effort, today):
    ended, history = field(text, 'terminal'), field(text, 'history')
    field(text, 'session')
    field(text, 'auto-merge')
    dispatch, review = level(text, 'dispatch-effort'), level(text, 'review-effort')
    dispatch, review = dispatch_effort or dispatch, review_effort or review
    if ended:
        text = set_field(set_field(text, 'terminal', ''), 'history', '; '.join(filter(None, [history, f'{ended} {today}'])))
    text = set_field(set_field(text, 'auto-merge', 'yes' if auto_merge else 'no'), 'dispatch-effort', dispatch)
    return set_field(text, 'review-effort', review)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['confirm', 'phase', 'take-over'])
    parser.add_argument('card', type=Path)
    parser.add_argument('phase', nargs='?', choices=PHASES, help='phase: the phase the run enters')
    parser.add_argument('--words', help="confirm: the user's words that confirmed the card")
    parser.add_argument('--auto-merge', action='store_true', help='grant merging to the new run')
    parser.add_argument('--effort', choices=LEVELS, help='set both steps to this level')
    parser.add_argument('--dispatch-effort', choices=LEVELS, help="set the dispatch's verification level")
    parser.add_argument('--review-effort', choices=LEVELS, help="set the review's level")
    args = parser.parse_args()
    today = datetime.date.today().isoformat()
    if args.command == 'phase':
        if not args.phase:
            parser.error(f'phase takes one of {", ".join(PHASES)}')
        state_files.write_atomic(args.card, phase(args.card, args.card.read_text(), args.phase))
        return
    if args.command == 'confirm':
        state_files.write_atomic(args.card, confirm(args.card.read_text(), args.words, host_sessions(), today))
        return
    levels = (args.dispatch_effort or args.effort, args.review_effort or args.effort)
    state_files.write_atomic(args.card, take_over(args.card.read_text(), args.auto_merge, *levels, today,
                                                  host_sessions()))


if __name__ == '__main__':
    main()
