#!/usr/bin/env python3
"""Supply the registered host adapter and applicable environment guidance."""
import argparse
import json
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument('host', choices=('claude', 'codex'))
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / 'scripts'))
import plugin_lag  # noqa: E402
context = f'zloop host: {args.host}. When using a zloop skill, first read {root}/bootstrap.md and {root}/hosts/{args.host}/session.md. Plugin root: {root}. Load only this host session part; pass its absolute path, the agent file and the job file to workers.'
if os.environ.get('HERDR_ENV') == '1':
    context += f' Herdr environment detected: before delegation also read {root}/hosts/herdr.md for routing, waits and cleanup.'
try:
    lag = plugin_lag.lagging(Path.cwd(), root)
except OSError:  # The session's directory is gone: nothing to judge, and the rest of the context stands.
    lag = None
if lag:
    context += f'\nzloop plugin lag: {lag}'
print(json.dumps({'hookSpecificOutput': {
    'hookEventName': 'SessionStart',
    'additionalContext': context
}}))
