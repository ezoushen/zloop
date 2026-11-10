#!/usr/bin/env python3
"""The summary-line rules the scripts refuse (the titles standard names them, the ones left to review and the sources).

Usage: title_rules.py <commit message file>   (the commit-msg hook; exits 1 naming each broken rule)
"""
import re
import sys
from pathlib import Path

STANDARD = "git's summary-line convention"
FULL_STOP = 'ends with a full stop; drop it'
TYPE = re.compile(r'(feat|fix|refactor|docs|test|chore|perf|ci|build|style|revert)(\([^)]+\))?!?: \S')
GIT_OWN = ('Merge ', 'Revert ', 'fixup! ', 'squash! ', 'amend! ')  # git writes these; merging main in is allowed


def ticket_problems(title):
    """The rules a ticket title breaks: over 50 characters, a full stop, a leading article, no English word."""
    problems = []
    if len(title) > 50:
        problems.append(f'is {len(title)} characters; keep it at most 50')
    if title.endswith('.'):
        problems.append(FULL_STOP)
    if title.split(maxsplit=1)[0].lower() in ('a', 'an', 'the'):
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
    header = Path(argv[1]).read_text().split('\n', 1)[0]
    problems = header_problems(header)
    for problem in problems:
        print(f'commit-msg: the header {problem} ({STANDARD})', file=sys.stderr)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
