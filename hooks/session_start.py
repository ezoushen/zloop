#!/usr/bin/env python3
"""Supply the registered host adapter and applicable environment guidance."""
import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('host', choices=('claude', 'codex'))
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
context = f'zloop host: {args.host}. When using a zloop skill, first read {root}/bootstrap.md and {root}/hosts/{args.host}/session.md. Plugin root: {root}. Load only this host session part; pass its absolute path, the agent file and the job file to workers.'
if os.environ.get('HERDR_ENV') == '1':
    context += f' Herdr environment detected: before delegation also read {root}/hosts/herdr.md for routing, waits and cleanup.'
print(json.dumps({'hookSpecificOutput': {
    'hookEventName': 'SessionStart',
    'additionalContext': context
}}))
