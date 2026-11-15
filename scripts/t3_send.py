#!/usr/bin/env python3
"""Hand a message to a thread on a linked T3 Code environment, as its next turn: what `submit` runs for a worker there.

usage: t3_send.py <thread> <message>

The local `t3 thread send` reaches only this machine's threads, so this asks the environment's MCP server, addressed
by T3_MCP_URL and T3_MCP_BEARER_TOKEN. It prints T3's answer and exits 0 once T3 accepted the message (the answer names
the run it started), and 1 when T3 refused it or did not answer. Like the CLI's send it never interrupts or steers a
running turn: a busy thread takes the message as its next turn.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from worker_lifecycle import t3_mcp  # noqa: E402


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    code, answer = t3_mcp({}, 't3_thread_send', {'threadId': sys.argv[1], 'message': sys.argv[2], 'mode': 'queue'})
    print(json.dumps(answer))
    return code


if __name__ == '__main__':
    sys.exit(main())
