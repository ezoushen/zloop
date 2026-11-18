#!/usr/bin/env python3
"""Identify runtime files on disk; compare an installation with its intended source.

The digest describes content, not a running extension's loaded code or hook trust.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ('agents', 'skills', 'standards', 'runtime', 'hosts', 'scripts', 'hooks', 'schemas', 'assets')
MANIFESTS = ('bootstrap.md', '.claude-plugin/plugin.json', '.codex-plugin/plugin.json',
             '.agents/plugins/marketplace.json', 'requirements.txt')
SKIP = {'.git', '.venv', '__pycache__', 'node_modules', '.DS_Store'}


def snapshot(root=ROOT):
    """Return a stable digest and per-file hashes of the runtime under root."""
    root = Path(root).resolve()
    paths = [root / name for name in MANIFESTS if (root / name).is_file()]
    for folder in FOLDERS:
        paths += [p for p in (root / folder).rglob('*') if p.is_file()
                  and not SKIP.intersection(p.relative_to(root).parts)]
    files = {}
    for path in sorted(paths):
        if not path.resolve().is_relative_to(root):
            raise ValueError(f'Runtime file escapes plugin root: {path.relative_to(root)}')
        files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not files:
        raise ValueError(f'No runtime files under {root}')
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'root': str(root), 'digest': digest, 'files': files, 'scope': 'on-disk runtime files'}


def compare(installed, source):
    """Compare complete runtime content, including missing and unexpected files."""
    actual, expected = snapshot(installed), snapshot(source)
    left, right = actual['files'], expected['files']
    return {'status': 'same' if actual['digest'] == expected['digest'] else 'mismatch',
            'installed': actual, 'source': expected,
            'missing': sorted(right.keys() - left.keys()), 'unexpected': sorted(left.keys() - right.keys()),
            'changed': sorted(name for name in left.keys() & right.keys() if left[name] != right[name])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--compare', type=Path, help='intended source root; exit 1 on different content')
    args = parser.parse_args()
    try:
        result = compare(args.root, args.compare) if args.compare else snapshot(args.root)
    except (OSError, ValueError) as error:
        parser.exit(2, f'{error}\n')
    print(json.dumps(result, indent=2))
    return int(result.get('status') == 'mismatch')


if __name__ == '__main__':
    sys.exit(main())
