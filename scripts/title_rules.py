#!/usr/bin/env python3
"""The summary-line rules the scripts refuse (the titles standard names them, the ones left to review and the sources).

Usage: title_rules.py <commit message file>   (the commit-msg hook; exits 1 naming each broken rule)
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
import unicodedata

STANDARD = "git's summary-line convention"
FULL_STOP = 'ends with a full stop; drop it'
TYPE = re.compile(r'(feat|fix|refactor|docs|test|chore|perf|ci|build|style|revert)(\([^)]+\))?!?: \S')
GIT_OWN = ('Merge ', 'Revert ', 'fixup! ', 'squash! ', 'amend! ')  # git writes these; merging main in is allowed


def title_words(title):
    plain = unicodedata.normalize('NFKD', re.sub("['\u2019]", '', title.lower())).encode('ascii', 'ignore')
    return '-'.join(re.findall(r'[a-z0-9]+', plain.decode()))


def branch_paths(slug, title):
    words = title_words(title)
    if len(words) > 50:
        words = words[:51].rsplit('-', 1)[0] if '-' in words[:51] else words[:50]
    branch = f'{slug}/{words}'
    if not words or subprocess.run(['git', 'check-ref-format', '--branch', branch],
                                   capture_output=True).returncode:
        raise ValueError('Title and slug must produce a valid Git branch')
    return dict(branch=branch, worktree=f'.worktrees/{slug}--{words}')


def ticket_problems(title):
    """The rules a ticket title breaks: over 50 characters, a full stop, a leading article, no English word."""
    problems = []
    if len(title) > 50:
        problems.append(f'is {len(title)} characters; keep it at most 50')
    if title.endswith('.'):
        problems.append(FULL_STOP)
    if title.split(maxsplit=1) and title.split(maxsplit=1)[0].lower() in ('a', 'an', 'the'):
        problems.append('starts with an article; lead with the imperative verb')
    if not re.search('[A-Za-z]', title):  # Its words name the ticket's branch.
        problems.append('has no English word; write it in English')
    return problems


def header_problems(header):
    """The rules a commit header breaks: over 72 characters, no type, a full stop."""
    if header.startswith(GIT_OWN):
        return []
    problems = []
    if len(header) > 72:
        problems.append(f'is {len(header)} characters; keep it at most 72')
    if not TYPE.match(header):
        problems.append('has no type; start with `<type>: ` such as `fix: `')
    if header.endswith('.'):
        problems.append(FULL_STOP)
    return problems


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('message_file', nargs='?', type=Path, help='commit-msg hook input')
    source.add_argument('--ticket', help='inspect a ticket title without creating a ticket')
    source.add_argument('--commit', help='inspect a commit or PR title')
    source.add_argument('--branch', help='generate fallback remote-ticket branch/worktree paths from its title')
    parser.add_argument('--slug', help='workstream slug for --branch')
    args = parser.parse_args(argv[1:])
    if args.branch is not None:
        if not args.slug:
            parser.error('--branch requires --slug')
        try:
            print(json.dumps(branch_paths(args.slug, args.branch)))
            return 0
        except ValueError as error:
            parser.error(str(error))
    if args.ticket is not None or args.commit is not None:
        problems = ticket_problems(args.ticket) if args.ticket is not None else header_problems(args.commit)
        print(json.dumps(dict(ok=not problems, problems=problems)))
        return int(bool(problems))
    # The hook sees the message before git drops blank and comment lines, so the header is the first line left.
    header = next((line for line in args.message_file.read_text().splitlines()
                   if line.strip() and not line.startswith('#')), '')
    problems = header_problems(header)
    for problem in problems:
        print(f'commit-msg: the header {problem} ({STANDARD})', file=sys.stderr)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
