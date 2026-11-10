#!/usr/bin/env python3
"""Check the mechanical rules of a PR description before publishing it.

Usage: lint_pr_body.py <pr.md> [--title TITLE] [--published NUMBER [--repo OWNER/NAME]]
Prints {"errors": [...], "warnings": [...]} and exits 1 when there are errors.
Judgment rules (baseline choice, explanations, scope) stay in standards/pr.md.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import title_rules  # noqa: E402

TITLE = re.compile(title_rules.TYPE.pattern + r'.{4,}')
PRIVATE = re.compile(r'\.scratch/|claude\.ai/(?:code/)?(?:artifact|chat|project|share)|/Users/|/home/|/private/tmp|file://')
INTERNAL = re.compile(r'\b(D\d{1,3}|FU(?:-[A-Za-z0-9]+)+|F-\d+|R-\d+|[Tt]icket\s*#?\d+)\b')
FENCE = re.compile(r'^```([^\s`]*).*$')


def split_fences(lines):
    """Yield (language, block lines) for fenced blocks and return prose line indexes."""
    blocks, prose, language, current = [], [], None, []
    for index, line in enumerate(lines):
        match = FENCE.match(line.strip())
        if language is None and match:
            language, current = match.group(1), []
        elif language is not None and line.strip() == '```':
            blocks.append((language, current))
            language = None
        elif language is not None:
            current.append(line)
        else:
            prose.append(index)
    return blocks, prose


def lint(body, title=None):
    errors, warnings = [], []
    lines = body.splitlines()
    _, prose = split_fences(lines)
    text = '\n'.join(lines[i] for i in prose)
    for match in PRIVATE.finditer(text):
        errors.append(f'private path or link "{match.group(0)}": reviewers cannot open it; inline the essentials')
    found = sorted({match.group(0) for match in INTERNAL.finditer(text)})
    if found:
        warnings.append('internal identifiers a reviewer cannot resolve: ' + ', '.join(found))
    if title is not None:
        if 'wip' in title.lower() or not TITLE.fullmatch(title) or len(title) > 72:
            errors.append(f'title "{title}": use "<type>(scope): <resulting behavior>", at most 72 chars, no WIP')
        if title.endswith('.'):
            errors.append(f'title "{title}" {title_rules.FULL_STOP} ({title_rules.STANDARD})')
    return errors, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('body', type=Path)
    parser.add_argument('--title')
    parser.add_argument('--published', help='PR number whose live body must equal the file')
    parser.add_argument('--repo', help='OWNER/NAME for --published')
    args = parser.parse_args()
    body = args.body.read_text()
    errors, warnings = lint(body, args.title)
    if args.published:
        command = ['gh', 'pr', 'view', args.published, '--json', 'body'] + (['--repo', args.repo] if args.repo else [])
        live = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)['body']
        if live.replace('\r\n', '\n').strip() != body.strip():
            errors.append(f'published body of #{args.published} differs from {args.body}; publish the file')
    print(json.dumps({'errors': errors, 'warnings': warnings}, indent=2, ensure_ascii=False))
    raise SystemExit(1 if errors else 0)


if __name__ == '__main__':
    main()
