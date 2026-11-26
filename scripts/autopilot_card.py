#!/usr/bin/env python3
"""Confirm an autopilot run card, set its phase or take it over: `autopilot_card.py confirm <card> --words "<the user's
words>"`, `autopilot_card.py phase <card> <align|refine|dispatch|pr>`, or `autopilot_card.py take-over <card>
[--effort <level>] [--dispatch-effort <level>] [--review-effort <level>]`.

A phase of dispatch or pr needs the tickets of the board the card names on `board:`: a tracker's URL holds its own; a
`local` board needs ticket files that pass `next_tickets.py --check`.

A confirmation records its verified human, relayed, or direct-by-rule source and the words on `confirmed:`
(on a `mode: direct` card, as `by rule` with
the goal the user typed, since a direct run asks nothing). `session:` records routing IDs for the panel and stop hold.
`intent-thread:` records the T3 conversation for later human decisions. A direct card whose local board holds more
than two tickets is refused its dispatch: it takes the full path, and a `mode: full` card still confirmed `by rule` is
refused dispatch and pr until `confirm --words` records the user's words.

Rewrites the lines of the card's `## State` section in place and nothing else, so a Goal quoting `auto-merge:` is
left alone. Legacy control clears `auto-merge:`. Managed control records merge grants. An ended run moves
its `terminal:` state and date to `history:` and clears `terminal:`. A card that has not ended keeps its history. `phase:` is left as
recorded: a run that ends keeps the phase it stopped in.

`dispatch-effort:` and `review-effort:` keep the levels the card confirmed unless a level flag is given: `--effort`
sets both, and a split flag overrides it for its own step. A card without either line, or with a level outside
`low|medium|high|xhigh|max`, is refused.
"""
import argparse
import datetime
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import front_matter  # noqa: E402
import human_intent  # noqa: E402
import state_files  # noqa: E402
import next_tickets  # noqa: E402
import plugin_lag  # noqa: E402
import run_control  # noqa: E402
from worker_lifecycle import SESSION_VARIABLES, host_sessions, state_fields  # noqa: E402

LEVELS = ('low', 'medium', 'high', 'xhigh', 'max')
# The lines of the run card format's State, every one written before the user confirms the card.
STATE_LINES = ('confirmed', 'session', 'auto-merge', 'mode', 'board', 'dispatch-effort', 'review-effort', 'phase', 'terminal',
               'history', 'evidence', 'timer', 'resume')
RUN_CARD = Path(__file__).resolve().parents[1] / 'skills' / 'autopilot' / 'run-card.md'
PHASES = ('align', 'refine', 'dispatch', 'pr')
TICKETS = Path(__file__).resolve().parent / 'next_tickets.py'
PUBLISH_PROBE = Path(__file__).resolve().parent / 'publish_probe.py'
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
    """Why the run of `card` cannot work its board yet, or None: a full card confirmed by rule is not yet the user's; a
    tracker's URL holds its own tickets; a `local` board needs ticket files that pass the board's check, at most
    DIRECT_TICKETS of them on a direct card."""
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


def publish_problem(card, state):
    """Why the run of `card` cannot enter pr yet, or None: its `publish-probe:` line is missing or does not say `ok`."""
    if state_fields(state).get('publish-probe', '').split(' ')[0] == 'ok':
        return None
    probe = shlex.join(['python3', str(PUBLISH_PROBE), str(card)])
    return (f'the publish probe of {card.parent.name} is missing or failed: run `{probe}` and fix what it reports, '
            'then set the phase')


def lag_problem(card):
    """Why the run of `card` cannot enter dispatch or pr yet, or None: the plugin root lags `origin/main` of the
    repository holding the card's `.scratch/<slug>/` folder, so its hooks are older than that repository's rules."""
    lag = plugin_lag.lagging(card.resolve().parents[2])
    return f'{lag} Then set the phase.' if lag else None


def phase(card, text, value):
    """The card's text with `phase:` set to `value`; exits naming the problem when dispatch or pr has a board it cannot
    work (`board_problem`) or a plugin root behind the repository's main (`lag_problem`), or when pr has no passing
    publish probe (`publish_problem`)."""
    state = state_section(text)
    problem = (value in ('dispatch', 'pr') and (board_problem(card, state[0]) or lag_problem(card))
               or value == 'pr' and publish_problem(card, state[0]))
    if problem:
        sys.exit(problem)
    lines = state[0]
    if not re.search(r'^phase:', lines, re.M):  # A State that lost its line gets one.
        kept = lines.rstrip('\n')
        lines = kept + '\nphase:' + lines[len(kept):]
    return text[:state.start()] + set_field(lines, 'phase', value) + text[state.end():]


def crew_problem(card):
    """Why the brief beside `card` does not start from the project's crew profile, or None: once `.scratch/crew.md`
    exists, the brief's `## Crew` section names it."""
    profile, brief = card.parent.parent / 'crew.md', card.parent / 'alignment.md'
    if not profile.is_file():
        return None
    text = brief.read_text() if brief.is_file() else ''
    section = text.split('\n## Crew', 1)[1].split('\n## ', 1)[0] if '\n## Crew' in text else ''
    if 'crew.md' in section:
        return None
    return (f"{brief}'s `## Crew` section does not name {profile}: take the rows of the areas it covers from it and "
            "write its `From` line (align's Crew template), then confirm")


def confirm(card, text, words, sessions, today, confirmed_by=None):
    """Record the confirmation words, supplied provenance and active host sessions in State."""
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
    problem = crew_problem(card)
    if problem:
        sys.exit(problem)
    by_rule = ' by rule' if mode == 'direct' and confirmed_by is None else ''
    lines = set_field(set_field(state[0], 'confirmed', f'{today}{by_rule} "{words}"'), 'session', ', '.join(sessions))
    if confirmed_by is not None:
        state_files.validate_confirmation_source(confirmed_by)
        if 'confirmed-by' in state_fields(lines):
            lines = set_field(lines, 'confirmed-by', confirmed_by)
        else:
            lines = re.sub(r'^confirmed:.*$', lambda match: f'{match[0]}\nconfirmed-by: {confirmed_by}',
                           lines, count=1, flags=re.M)
    return text[:state.start()] + lines + text[state.end():]


def take_over(text, dispatch_effort, review_effort, today, sessions):
    """The card's text after a take-over by the host sessions exported here; only the lines of its `## State` section
    change."""
    state = state_section(text)
    taken = take_over_state(state[0], dispatch_effort, review_effort, today)
    return text[:state.start()] + set_field(taken, 'session', ', '.join(sessions)) + text[state.end():]


def take_over_state(text, dispatch_effort, review_effort, today):
    ended, history = field(text, 'terminal'), field(text, 'history')
    field(text, 'session')
    field(text, 'auto-merge')
    dispatch, review = level(text, 'dispatch-effort'), level(text, 'review-effort')
    dispatch, review = dispatch_effort or dispatch, review_effort or review
    if ended:
        text = set_field(set_field(text, 'terminal', ''), 'history', '; '.join(filter(None, [history, f'{ended} {today}'])))
    text = set_field(set_field(text, 'auto-merge', 'no'), 'dispatch-effort', dispatch)
    return set_field(text, 'review-effort', review)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['confirm', 'phase', 'take-over'])
    parser.add_argument('card', type=Path)
    parser.add_argument('phase', nargs='?', choices=PHASES, help='phase: the phase the run enters')
    parser.add_argument('--words', help="confirm: the user's words that confirmed the card")
    parser.add_argument('--confirmed-by', help='confirm: human or relayed(agent)')
    parser.add_argument('--auto-merge', action='store_true', help='merge authorization requires managed control')
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--merge-base', help='explicit base of a planned auto-merge PR')
    target.add_argument('--merge-pr', type=int, help='open PR for an auto-merge request')
    parser.add_argument('--effort', choices=LEVELS, help='set both steps to this level')
    parser.add_argument('--dispatch-effort', choices=LEVELS, help="set the dispatch's verification level")
    parser.add_argument('--review-effort', choices=LEVELS, help="set the review's level")
    args = parser.parse_args()
    if args.confirmed_by is not None and args.command != 'confirm':
        parser.error('--confirmed-by is only supported by confirm')
    today = datetime.date.today().isoformat()
    if args.command == 'phase':
        if not args.phase:
            parser.error(f'phase takes one of {", ".join(PHASES)}')
        with state_files.locked(args.card):
            managed = run_control.read(args.card)
            text = phase(args.card, managed['card_text'] if managed else args.card.read_text(), args.phase)
            if managed is None:
                state_files.write_atomic(args.card, text)
                return
        run_control.phase(args.card, text, managed['revision'])
        return
    try:
        with state_files.locked(args.card):
            if run_control.read(args.card):
                parser.error('Managed run: use run_control.py for pause or resume and grant verification')
            original = args.card.read_text()
            values = state_fields(original)
            source = args.confirmed_by or values.get('confirmed-by')
            if source is not None:
                state_files.validate_confirmation_source(source)
            relayed = source is not None and source.startswith('relayed(')
            if args.auto_merge or args.merge_pr is not None or args.merge_base is not None:
                if relayed:
                    raise human_intent.WaitingOnUser('relayed confirmation cannot authorize auto-merge')
                raise human_intent.WaitingOnUser('automated merge authority requires a managed run and a verified human grant')
            thread = os.environ.get('T3CODE_THREAD_ID')
            bound_thread = values.get('intent-thread')
            provider_sessions = host_sessions()
            sessions = provider_sessions.copy()
            if thread and thread not in sessions:
                sessions.append(thread)
            if any(session.splitlines() != [session] for session in sessions):
                raise ValueError('Host session IDs must fit one line')
            consumed = (values.get('confirmation-item'), values.get('take-over-item'))
            if args.command == 'confirm':
                direct = values.get('mode') == 'direct' and source is None
                initial = re.match(r'no\b|$', values.get('confirmed', ''), re.I)
                candidate_thread = bound_thread or (thread if initial else None)
                proposed = confirm(args.card, original, args.words, sessions, today,
                                   source if direct or relayed else 'human')
                if (direct or relayed) and not sessions:
                    sys.exit(f'no host session ID is exported here ({", ".join(SESSION_VARIABLES)}): confirm from the '
                             'shell of the host session that runs this autopilot')
                if relayed:
                    proposed = run_control.fields(proposed, {'delivery': 'pr-only'})
                elif not direct:
                    old_source = values.get('confirmed-by', '')
                    if old_source.startswith('relayed('):
                        raise human_intent.WaitingOnUser('the relayed mandate stays PR-only')
                    proof = human_intent.verify(args.card, original, 'confirmation', ' '.join(args.words.split()),
                                                candidate_thread, provider_sessions=provider_sessions, consumed=consumed)
                    proposed = run_control.fields(proposed, {'confirmation-item': proof['item']})
                    candidate_thread = proof['thread']
                if candidate_thread:
                    proposed = run_control.fields(proposed, {'intent-thread': candidate_thread})
                proposed = run_control.fields(proposed, {'auto-merge': 'no'})
            else:
                levels = (args.dispatch_effort or args.effort, args.review_effort or args.effort)
                proposed = take_over(original, *levels, today, sessions)
                target = {'dispatch_effort': levels[0], 'review_effort': levels[1]}
                proof = human_intent.verify(args.card, original, 'take-over', 'autopilot --take-over', bound_thread,
                                            target=target, provider_sessions=provider_sessions, consumed=consumed)
                proposed = run_control.fields(proposed, {'take-over-item': proof['item']})
            state_files.write_atomic(args.card, proposed)
    except human_intent.WaitingOnUser as error:
        parser.exit(2, f'waiting-on-user: {error}\n')



if __name__ == '__main__':
    try:
        main()
    except ValueError as error:
        sys.exit(str(error))
