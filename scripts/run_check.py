#!/usr/bin/env python3
"""Run a noninteractive POSIX check with a deadline and a private output log."""
import argparse
import json
import math
import os
import signal
import subprocess
import sys
import time


def stop_group(process):
    # A launcher can exit on TERM while its descendants still hold the build lock.
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, signal.SIG_IGN)
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    time.sleep(1)
    process.poll()
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, required=True, help='check deadline in seconds')
    parser.add_argument('--log', required=True, help='new full-output log path')
    parser.add_argument('--require-output', action='append', default=[],
                        help='literal execution output required in the log; repeatable')
    parser.add_argument('--classify-failure', metavar='JOURNAL',
                        help='absolute decisions journal: journal a hidden failure-cause label when the check fails')
    parser.add_argument('--assignment-id', help='with --classify-failure: stable assignment/check identity')
    parser.add_argument('--repo', help='with --classify-failure: canonical project checkout')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if bool(args.classify_failure) != bool(args.assignment_id and args.repo) or (
            args.classify_failure and not os.path.isabs(args.classify_failure)):
        parser.error('--classify-failure takes an absolute journal, with --assignment-id and --repo')
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be finite and greater than zero')
    if any(not value.strip() or '\n' in value or '\r' in value for value in args.require_output):
        parser.error('--require-output must be nonblank single-line text')
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('a command is required after --')
    timed_out = False
    interrupted = None
    cleanup_required = False
    started = current_head()

    def interrupt(signum, frame):
        nonlocal interrupted
        # Raising here could interrupt Popen before the new child's handle is saved.
        interrupted = signum

    with os.fdopen(os.open(args.log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as log:
        # A summary an earlier run left under this log name never describes this one.
        try:
            os.remove(args.log + '.summary.json')
        except FileNotFoundError:
            pass
        handlers = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            deadline = time.monotonic() + args.timeout
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            while True:
                remaining = deadline - time.monotonic()
                if interrupted is not None or remaining <= 0:
                    stop_group(process)
                    timed_out = interrupted is None
                    code = 124 if timed_out else 128 + interrupted
                    break
                try:
                    code = process.wait(timeout=min(remaining, .1))
                    code = code if code >= 0 else 128 - code
                    break
                except subprocess.TimeoutExpired:
                    continue
            if not timed_out and interrupted is None:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    cleanup_required = True
                    stop_group(process)
                    code = code or 125
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    command_code = process.wait()
    command_code = command_code if command_code >= 0 else 128 - command_code
    missing = args.require_output[:]
    if missing:
        with open(args.log, 'rb') as output:
            for line in output:
                missing = [value for value in missing if value.encode() not in line]
                if not missing:
                    break
    source = 'deadline' if timed_out else 'signal' if interrupted is not None else 'command'
    if cleanup_required and command_code == 0:
        source = 'cleanup'
    if code == 0 and missing:
        code, source = 1, 'required-output'
    summary = {'exit_code': code, 'command_exit_code': command_code,
               'exit_source': source, 'timed_out': timed_out,
               'cleanup_required': cleanup_required,
               'interrupted_signal': interrupted, 'missing_output': missing,
               'log': os.path.abspath(args.log)}
    # The commit the check ran at, when it ran in a git checkout and nothing moved it meanwhile: a delivery gate
    # takes a run only at its head.
    finished = current_head()
    if started and finished == started:
        summary['head'] = started
    keep(summary)
    if code and args.classify_failure:
        summary['failure_cause'] = classify(summary, args)
        keep(summary)
    print(json.dumps(summary))
    return code


def current_head():
    """The commit checked out where the check runs, or None outside a git checkout or without git."""
    try:
        head = subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], capture_output=True, text=True)
    except OSError:
        return None
    return head.stdout.strip() if head.returncode == 0 else None


def keep(summary):
    """Keep the summary beside its log, replacing one a reused log name left."""
    path = summary['log'] + '.summary.json'
    with os.fdopen(os.open(path + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as output:
        json.dump(summary, output)
    os.replace(path + '.tmp', path)


def classify(summary, args):
    """Journal System One's hidden failure-cause label for this failed check; the host diagnoses as usual."""
    path = summary['log'] + '.summary.json'
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Reading executor configuration needs the plugin environment's dependencies.
    python = os.path.join(root, '.venv/bin/python')
    try:
        result = subprocess.run(
            [python if os.path.exists(python) else sys.executable, '-B', os.path.join(root, 'scripts/decisions.py'),
             'classify-failure', '--repo', args.repo, '--summary', path, '--journal', args.classify_failure,
             '--assignment-id', args.assignment_id], stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=120)
        return json.loads(result.stdout) if result.returncode == 0 else {
            'status': 'unavailable', 'error': result.stderr.strip()}
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {'status': 'unavailable', 'error': str(error)}


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except OSError as exc:
        print(f'run-check: {exc}', file=sys.stderr)
        raise SystemExit(125)
