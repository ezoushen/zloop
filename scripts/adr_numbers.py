#!/usr/bin/env python3
"""Report duplicated ADR numbers and the next free one, in the working tree or at a Git ref.

usage: adr_numbers.py --repo <checkout> [--dir docs/adr] [--ref <commit> [--against <commit>]] [--index]

Checks one folder of `NNNN-slug.md` files. With --against, the folder is checked as merging
the two commits would leave it, using `git merge-tree` (Git 2.38 or later) without touching
any branch or working tree. With --index, the folder must also follow zloop's index
convention: a `README.md` index whose Accepted, Superseded and Deprecated sections list each
ADR under its status (front matter `status:`, default accepted), only existing files, and
supersede links answered at both ends. Without it, a project's own convention is not judged. Prints JSON {"dir", "duplicates":
{number: [files]}, "next", "index_problems": [...]}. Exits 1 on a duplicate number or an
index problem, 2 on invalid input. An absent folder has no problems and next "0001".
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import front_matter  # noqa: E402

NAME = re.compile(r'(\d{4})-.+\.md')
# An index entry is a list line whose first link names the ADR file; later links annotate it.
ENTRY = re.compile(r' {0,3}[-*+] +\[(?:[^\]\\]|\\.)*\]\(\s*(?:\./)?(\d{4}-[^)\s#]+\.md)(?:#[^)\s]*)?'
                   r'(?:\s+"(?:[^"\\]|\\.)*"|\s+\'(?:[^\'\\]|\\.)*\')?\s*\)')
LIST_ITEM = re.compile(r' {0,3}[-*+] ')
# A backtick fence's info string may not contain a backtick, so an inline code span opens nothing.
FENCE = re.compile(r' {0,3}(`{3,}(?=[^`]*$)|~{3,})')
MENTION = re.compile(r'\d{4}-[^\s#"\'<>`\[\]()]+\.md')
SECTIONS = {'accepted': 'Accepted', 'superseded': 'Superseded', 'deprecated': 'Deprecated'}


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)


def names(repo, folder, ref):
    if ref is None:
        path = repo / folder
        return {p.name for p in path.iterdir() if p.is_file()} if path.is_dir() else set()
    resolved = git(repo, 'rev-parse', '--verify', '--quiet', f'{ref}^{{tree}}')
    if resolved.returncode:
        raise ValueError(f'{ref}: not a commit in {repo}')
    listed = git(repo, 'ls-tree', '-z', '--name-only', resolved.stdout.strip(), f'{folder.rstrip("/")}/')
    if listed.returncode:
        raise ValueError(listed.stderr.strip())
    return {Path(entry).name for entry in listed.stdout.split('\0') if entry}


def merged_tree(repo, ref, against):
    for name in (ref, against):
        if git(repo, 'rev-parse', '--verify', '--quiet', f'{name}^{{commit}}').returncode:
            raise ValueError(f'{name}: not a commit in {repo}')
    # Exit 1 means the merge has conflicts, which still yields a tree; anything else is fatal.
    merged = git(repo, 'merge-tree', '--write-tree', ref, against)
    if merged.returncode not in (0, 1):
        raise ValueError(merged.stderr.strip() or f'git merge-tree failed for {ref} and {against}')
    return merged.stdout.splitlines()[0]


def read(repo, folder, ref, name):
    if ref is None:
        return (repo / folder / name).read_text()
    shown = git(repo, 'show', f'{ref}:{folder.rstrip("/")}/{name}')
    if shown.returncode:
        raise ValueError(shown.stderr.strip())
    return shown.stdout


def adr_fields(text):
    """An ADR's front matter as strings: a list reads as its items joined by commas."""
    return {key: front_matter.unquoted(', '.join(value) if isinstance(value, list) else value)
            for key, value in (front_matter.parse(text) or {}).items()}


def index_problems(texts, folder):
    """Problems between the ADRs, their supersede links and the index; texts maps file name to content."""
    adrs = {name: adr_fields(text) for name, text in texts.items() if NAME.fullmatch(name)}
    status_of = {name: fields.get('status', 'accepted') for name, fields in adrs.items()}
    by_number = {}
    for name in adrs:
        by_number.setdefault(name[:4], []).append(name)
    problems = []
    for name, fields in sorted(adrs.items()):
        status = status_of[name]
        superseded = re.fullmatch(r'superseded by ADR-(\d{4})', status)
        if status not in ('accepted', 'deprecated') and not superseded:
            problems.append(f'{name}: unknown status {status}')
        if superseded and superseded[1] not in by_number:
            problems.append(f'{name}: {status}, which does not exist')
        elif superseded and not any(name[:4] in re.findall(r'ADR-(\d{4})', adrs[newer].get('supersedes', ''))
                                  for newer in by_number.get(superseded[1], [])):
            problems.append(f'{name}: {status}, which does not name it in supersedes')
        for older in re.findall(r'ADR-(\d{4})', fields.get('supersedes', '')):
            if not any(status_of[o] == f'superseded by ADR-{name[:4]}' for o in by_number.get(older, [])):
                problems.append(f'{name}: supersedes ADR-{older}, which is not superseded by ADR-{name[:4]}')
    if 'README.md' not in texts:
        return problems + ([f'{folder.rstrip("/")}/README.md: no index'] if adrs else [])
    listed, section, fence = {}, 'outside a status section', None
    for line in texts['README.md'].splitlines():
        opening = FENCE.match(line)
        if fence is None and opening:
            fence = opening[1]
            continue
        if fence is not None:
            # A fence closes only on a line of its own character, at least as long as the opening.
            if opening and line.strip() == opening[1] and opening[1][0] == fence[0] and len(opening[1]) >= len(fence):
                fence = None
            continue
        if line.startswith('## '):
            section = line[3:].strip()
        entry = ENTRY.match(line)
        if not LIST_ITEM.match(line) or not (entry or MENTION.search(line)):
            continue
        if not entry:
            problems.append(f'README.md: cannot read an index entry in: {line.strip()}')
            continue
        listed.setdefault(entry[1], []).append(section)
        for named in MENTION.findall(line):
            if named not in adrs and named != entry[1]:
                listed.setdefault(named, [])
    for target in sorted(listed):
        if target not in adrs:
            problems.append(f'{target}: listed in the index but no such file')
    for name, status in sorted(status_of.items()):
        want = SECTIONS.get(status.split()[0]) if status.split() else None
        if want is None:
            continue
        sections = listed.get(name, [])
        if want not in sections:
            problems.append(f'{name}: {status} but not listed under {want}')
        for other in sorted(set(sections) - {want}, key=lambda s: (s != 'outside a status section', s)):
            where = other if other == 'outside a status section' else f'under {other}'
            problems.append(f'{name}: {status} but also listed {where}')
    return problems


def report(files):
    numbered = {}
    for name in sorted(files):
        match = NAME.fullmatch(name)
        if match:
            numbered.setdefault(match[1], []).append(name)
    duplicates = {number: group for number, group in sorted(numbered.items()) if len(group) > 1}
    highest = max(map(int, numbered), default=0)
    return duplicates, f'{highest + 1:04d}'


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--dir', default='docs/adr', help="the project's ADR folder")
    parser.add_argument('--ref', help='read the folder at this commit instead of the working tree')
    parser.add_argument('--against', help='check the folder as merging this commit would leave it')
    parser.add_argument('--index', action='store_true', help="also check zloop's index and supersede links")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    try:
        if not repo.is_dir():
            raise ValueError(f'{repo}: not a directory')
        if args.against and not args.ref:
            raise ValueError('--against needs --ref')
        ref = args.ref
        if args.against:
            ref = merged_tree(repo, args.ref, args.against)
        files = names(repo, args.dir, ref)
        duplicates, following = report(files)
        problems = []
        if args.index:
            texts = {name: read(repo, args.dir, ref, name) for name in files
                     if NAME.fullmatch(name) or name == 'README.md'}
            problems = index_problems(texts, args.dir)
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 2
    print(json.dumps({'dir': args.dir, 'duplicates': duplicates, 'next': following, 'index_problems': problems},
                     ensure_ascii=False))
    return 1 if duplicates or problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
