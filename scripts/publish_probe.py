#!/usr/bin/env python3
"""Learn whether a run can publish its work before it does the work: `publish_probe.py <card>`.

Checks, from the repository holding the card's `.scratch/<slug>/` folder and without changing anything remote, that
`origin` names a GitHub repository, that `git push --dry-run` of the workstream branch (`HEAD` while the branch does
not exist yet) to `origin` succeeds, and that GitHub grants write permission on the repository, which opening a
pull request needs. Records one State line on the card, `publish-probe: <ok|failed> <UTC time> <remote or reason>`,
prints it and exits 1 when it says `failed`. `autopilot_card.py phase <card> pr` refuses a card whose line is missing
or failed.
"""
import argparse
import datetime
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import human_intent  # noqa: E402
import pr_safety  # noqa: E402
import run_control  # noqa: E402

KEY = 'publish-probe'


def first_line(error):
    """The first line git or gh printed to stderr behind a failed `pr_safety.command`, else the error's own text."""
    lines = (getattr(error.__cause__, 'stderr', None) or '').strip().splitlines()
    return lines[0].strip() if lines else str(error)


def probe(card):
    """Whether `card`'s run can publish, and the remote (`<owner>/<repo>`) when it can, else why it cannot."""
    card = Path(card)
    repo, branch = card.resolve().parents[2], run_control.workstream_branch(card.parent)
    try:
        remote = pr_safety.repository(cwd=repo)
    except human_intent.WaitingOnUser:
        return False, 'origin is not a GitHub repository'
    try:
        pr_safety.command('git', 'show-ref', '--verify', '--quiet', f'refs/heads/{branch}', cwd=repo)
        source = f'refs/heads/{branch}'
    except human_intent.WaitingOnUser:
        source = 'HEAD'
    try:
        pr_safety.command('git', 'push', '--dry-run', 'origin', f'{source}:refs/heads/{branch}', cwd=repo)
    except human_intent.WaitingOnUser as error:
        return False, f'git push --dry-run of {source} to origin failed: {first_line(error)}'
    try:
        permitted = pr_safety.api(f'repos/{remote}')['permissions']['push'] is True
    except (human_intent.WaitingOnUser, KeyError, TypeError):
        return False, f'gh cannot read the permissions on {remote}'
    return (True, remote) if permitted else (False, f'gh reports no write permission on {remote}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('card', type=Path)
    args = parser.parse_args()
    # Nothing may wait for a person: git and gh fail instead of prompting.
    os.environ.update(GIT_TERMINAL_PROMPT='0', GH_PROMPT_DISABLED='1')
    os.environ.setdefault('GIT_SSH_COMMAND', 'ssh -o BatchMode=yes')
    ok, detail = probe(args.card)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    value = f'{"ok" if ok else "failed"} {stamp} {" ".join(detail.split())}'
    run_control.record_field(args.card, KEY, value)
    print(f'{KEY}: {value}')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit(str(error))
