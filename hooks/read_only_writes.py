#!/usr/bin/env python3
"""PreToolUse hook: a zloop read-only subagent in Claude Code, or native child in Codex, leaves the user's
checkouts unchanged.

Claude Code names the subagent a tool call runs in (`agent_type`). For `zloop:<agent>`, or a generated
`zloop-<agent>-<digest>` profile, whose agent declares `writes: none`, it denies an Edit, Write or
NotebookEdit of a file inside the main checkout or one of its worktrees (the session cwd's git common dir family),
except under the main checkout's .scratch/<slug>/reports/ and in the `worktree:` the subagent's assignment names
(outside any `.git` there), whose temporary experiment edits collection's restored-worktree check guards. The assignment is the first user
message of the subagent's own transcript, <session transcript without .jsonl>/subagents/agent-<agent_id>.jsonl,
present from its first tool call, so a write before registration is judged the same; anything under that session
directory is always denied, so the agent cannot widen its own assignment. Anything else outside the family (/tmp,
the scratchpad, an isolated copy) is allowed, and so is it all for a session outside any checkout. It
also denies a Bash command that clearly writes such a file: a `>`, `>>`, `>|`, `&>` or `&>>` redirect, or `tee`, to
a literal path, relative ones following the command's own `cd`, `pushd` or `builtin cd`, real paths resolved. Collection's read-only worktree
check cannot see a write outside the worktree, since a subagent's transcript carries its orchestrator's directory.
The reports and paths outside the family are decided before anything else is read; a check that cannot run for a
path inside the family denies the call.

Codex (argument `codex`, Bash and apply_patch): a native child's payload carries `agent_id` and its own rollout,
but its task message is stored encrypted, so its parent records the assignment first (`codex-assign`) in
<main>/.scratch/codex-children/<CODEX_SESSION_ID>/<task_name>.json, the agent its agent_file names and its worktree,
and indexes that checkout in <XDG state>/zloop/codex-children/<session>/. codex-assign never overwrites a
record, and the hook denies writing records, the index or the child's own rollout by redirect, `tee` or apply_patch;
`rm`, `mv` or an interpreter can still remove a record and record it anew, and codex-assign's refusal of a child
rests on CODEX_THREAD_ID, which a child's command can set, so neither holds against a child that sets out to evade.
The task name is the child's rollout session_meta `agent_path` (`/root/<task_name>`) when that meta's id is the
payload's `agent_id`. A session is checked only when the index or the turn cwd's `.git` (read without git, each
source on its own, a `.git` naming no git directory passed over) leads to a record directory; else the call is allowed before any parsing, as is every call of the parent. Once found, each
written path is judged by the record in the checkout it lands in: a `writes: none` agent gets the decisions above,
a child with no record there, or a corrupt one, is held to the reports, a relative target is denied unless the
child writes in its turn cwd's checkout. The hook always exits 0: before the records are found an error allows,
after it denies, as does a call it cannot judge within 5 s. apply_patch paths (the tool's or a shell heredoc's) are
writes.

Not caught: a target known only at run time (`$VAR`, `$(...)`, globs), `>&` to a file, `<>`, escaped spaces, `#`
inside a word, a shell's `-c` string, `sudo tee` or `xargs tee`, a `cd` that does not run (`false && cd x`), a symlink
the same command creates, an interpreter or tool that opens files itself (`python -c`, `sed -i`, `cp`, `git`), and
MCP tools.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import agent_defs  # noqa: E402
import native_settings  # noqa: E402
import pr_body  # noqa: E402
import yield_gate  # noqa: E402

FILE_TOOLS = {'Edit': 'file_path', 'Write': 'file_path', 'NotebookEdit': 'notebook_path'}
REDIRECTS = {'>', '>>', '>|', '&>', '&>>'}
# Operators whose next word is not a file this command writes: input, heredoc delimiters and fd copies.
SKIPS_NEXT = {'<', '<<', '<<-', '<<<', '>&', '<&', '<>'}
ASSIGNED = re.compile(r'^worktree:[ \t]*(.+?)[ \t]*$', re.M)
RECORDS = Path('.scratch/codex-children')  # <main>/<RECORDS>/<Codex session id>/<task name>.json
TASK = re.compile(r'[a-z0-9_]+')  # The characters Codex allows in a spawn_agent task name.
SESSION = re.compile(r'[0-9A-Za-z-]+')  # A Codex session id (a UUID); it is a path segment of the records.
PATCH_HEADERS = ('*** Add File: ', '*** Delete File: ', '*** Update File: ', '*** Move to: ')  # Lines trimmed.
BUDGET = 5  # Seconds to judge one call, well under the 10 s hook timeouts past which it would run unchecked.
WORKDIR = '<workdir>'  # Codex's Bash payload omits exec_command's workdir, so relative targets stay unresolved.


@contextlib.contextmanager
def deadline(seconds=BUDGET):
    """Raise TimeoutError in the block once it has run `seconds`: a command shlex cannot lex in time (one huge
    word costs it quadratic time) is denied rather than left to the hook timeout."""
    def expire(signum, frame):
        raise TimeoutError(f'judging it took over {seconds} s')
    previous = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def read_only(agent_type):
    """The zloop agent a subagent type names when that agent declares `writes: none`, else None."""
    named = re.fullmatch(r'zloop(?::([a-z0-9-]+)|-([a-z0-9-]+)-[0-9a-f]{12})', agent_type or '')
    agent = named and (named.group(1) or named.group(2))
    definition = agent and agent_defs.load(ROOT)['agents'].get(agent)
    return agent if definition and definition.get('writes') == 'none' else None


def in_reports(path, main):
    """Whether a resolved path lies in some <main>/.scratch/<slug>/reports/."""
    try:
        parts = Path(path).relative_to(Path(os.path.realpath(main / '.scratch'))).parts
    except ValueError:
        return False
    return len(parts) >= 3 and parts[1] == 'reports'


def literal(word, cwd):
    """The absolute path a shell word names when it is a literal, else None: a quoted or unquoted word with no
    expansion, relative ones resolved against cwd when it is known."""
    if not word or any(char in word for char in '$`*?[{'):
        return None
    try:
        [text] = shlex.split(word)
    except ValueError:
        return None
    text = os.path.expanduser(text)
    if not (os.path.isabs(text) or cwd):
        return None
    return os.path.join(cwd or '', text)


def tokens(command):
    """The command's shell tokens, quotes kept, heredoc bodies cut out and each line ended by `;`."""
    lines, kept, index = command.replace('\\\n', ' ').split('\n'), [], 0
    while index < len(lines):
        line, index = lines[index], index + 1
        kept.append(line)
        for match in pr_body.HEREDOC.finditer(line):
            end = next((i for i in range(index, len(lines)) if lines[i].strip() == match.group(2)), None)
            if end is not None:
                index = end + 1
    found, group, quote = [], [], None
    for line in kept:
        group.append(line)
        # An open quote continues on the next line; the lines are lexed together once it closes, each line once.
        quote = open_quote(line, quote)
        if quote:
            continue
        lexer = shlex.shlex('\n'.join(group), posix=False, punctuation_chars=True)
        lexer.whitespace_split = True
        try:
            found += list(lexer) + [';']
            group = []
        except ValueError:
            pass  # The scan and shlex disagree; keep the lines together as before.
    return found


def open_quote(line, quote):
    """The quote still open at the end of a line, given the one open at its start, by the rules `tokens` lexes with
    (shlex, not POSIX): `#` outside quotes, inside a word too, comments out the rest of the line, and a quote opens
    only where a token starts."""
    state = ' '
    for char in line:
        if quote:
            if char == quote:
                quote, state = None, ' '
        elif char == '#':
            break
        elif char in ' \t\r':
            state = ' '
        elif char in '();<>|&':
            state = 'c'
        elif state == 'a':
            continue  # Inside a word, a quote is a word character.
        elif char in '\'"':
            quote = char
        else:
            state = 'a'
    return quote


def directory(words, cwd):
    """The directory a `cd` or `pushd` moves to, past its flags and `--`; None when unknown (`cd -`, a stack entry)."""
    args = words[1:]
    while args and args[0].startswith('-') and args[0] != '-':
        flag, args = args[0], args[1:]
        if flag == '--':
            break
    if not args:
        return None if words[0] == 'pushd' else literal('~', cwd)  # A bare pushd swaps the top two entries.
    return None if args[0] == '-' or args[0].startswith('+') else literal(args[0], cwd)


def shell_writes(command, cwd):
    """The literal paths a Bash command writes by redirection or `tee`, following its own `cd`."""
    written, words, subshells, at, items = [], [], [], 0, tokens(command)
    while at < len(items):
        token, at = items[at], at + 1
        operator = token != '((' and not token.strip('();<>|&')
        if token in REDIRECTS and at < len(items):
            written.append(literal(items[at], cwd))
            at += 1
        elif token in SKIPS_NEXT:
            at += 1
        elif token in ('((', '[['):  # Arithmetic and tests compare with `>`; they write nothing.
            close = '))' if token == '((' else ']]'
            at = next((i + 1 for i in range(at, len(items)) if items[i] == close), len(items))
        elif not operator:
            words.append(token)
        if operator:
            words = pr_body.unwrap(words)
            while words and pr_body.ASSIGNMENT.match(words[0]):
                words = pr_body.unwrap(words[1:])
            if words[:1] == ['builtin']:
                words = words[1:]
            if words[:1] in (['cd'], ['pushd']):
                cwd = directory(words, cwd)
            elif words[:1] == ['popd']:
                cwd = None  # Back to a directory the command may not show.
            elif words and Path(words[0]).name == 'tee':
                written += [literal(word, cwd) for word in words[1:] if not word.startswith('-')]
            elif words and Path(words[0]).name in ('apply_patch', 'applypatch'):  # Codex applies the patch itself.
                written += [os.path.join(cwd, path) for path in patch_paths(command) if cwd or os.path.isabs(path)]
            words = []
            if '(' in token:
                subshells.append(cwd)
            elif ')' in token and subshells:
                cwd = subshells.pop()  # A `cd` inside a subshell ends with it.
    return [path for path in written if path]


def patch_paths(patch):
    """The files a Codex apply_patch text adds, updates, deletes or moves to, as written in it."""
    return [line.strip()[len(header):] for line in patch.splitlines() for header in PATCH_HEADERS
            if line.strip().startswith(header)]


def checkouts(main):
    """The main checkout and every worktree of its repository, real paths, the main checkout first."""
    result = subprocess.run(['git', '-C', str(main), 'worktree', 'list', '--porcelain'], capture_output=True,
                            text=True, timeout=5, check=True)
    return [Path(os.path.realpath(line[len('worktree '):])) for line in result.stdout.splitlines()
            if line.startswith('worktree ')]


def assigned_worktree(event):
    """The real path of the `worktree:` the subagent's assignment names, or None when it cannot be read: the first
    user message of its own transcript, which Claude Code keeps in the session's directory."""
    transcript, agent_id = event.get('transcript_path') or '', event.get('agent_id')
    if not (agent_id and transcript.endswith('.jsonl')):
        return None
    try:
        with open(Path(transcript[:-len('.jsonl')]) / 'subagents' / f'agent-{agent_id}.jsonl') as rows:
            first = next(row for row in map(json.loads, rows) if row.get('type') == 'user')
        content = first['message']['content']
        text = content if isinstance(content, str) else '\n'.join(block.get('text', '') for block in content)
    except (OSError, ValueError, StopIteration, KeyError, TypeError, AttributeError):
        return None
    match = ASSIGNED.search(text)
    return Path(os.path.realpath(match.group(1))) if match else None


def protected(paths, cwd, guarded, assigned, main):
    """(main checkout, assigned worktree, the paths a read-only agent may not write): those under any of `guarded`,
    which hold what widens its allowance, and those inside `main` (None: outside any checkout) or one of its worktrees,
    outside its reports and the worktree `assigned()` names. The reports and paths outside those checkouts are
    decided first, so no later lookup can deny them."""
    resolved = [os.path.realpath(os.path.join(cwd, os.path.expanduser(path))) for path in paths]
    own = [path for path in resolved if any(Path(path).is_relative_to(place) for place in guarded)]
    resolved = [path for path in resolved if path not in own]
    if main is None:
        return None, None, own  # A session outside any checkout protects none of them.
    held = [path for path in resolved if not in_reports(path, main)]
    family = checkouts(main) if held else []
    held = [path for path in held if any(Path(path).is_relative_to(checkout) for checkout in family)]
    assigned = assigned() if held else None
    if assigned not in family[1:]:  # Only a worktree of the family, never the main checkout itself.
        assigned = None
    # A `.git` is never an experiment edit, and a junk one would mislead the Codex mode's search for records.
    mine = [path for path in held if assigned and Path(path).is_relative_to(assigned)
            and '.git' not in Path(path).relative_to(assigned).parts]
    return main, assigned, own + [path for path in held if path not in mine]


def refusal(name, root, assigned, written, guarded):
    """The deny reason for a read-only agent's writes outside what it may write."""
    own = f' and in its assigned worktree {assigned}, outside any .git' if assigned else ''
    where = f'its report under {root}/.scratch/<slug>/reports/{own}' if root else 'no file'
    return (f'{name} is read-only (writes: none): inside the checkouts it writes only {where}, and never '
            f'{guarded}; {", ".join(written)} is outside that')


def claude(event):
    """A Claude Code subagent's call: `agent_type` names the agent, its transcript holds the assignment."""
    tool, tool_input = event.get('tool_name'), event.get('tool_input') or {}
    agent = tool in (*FILE_TOOLS, 'Bash') and read_only(event.get('agent_type'))
    if not agent:
        return
    cwd = event.get('cwd') or os.getcwd()
    # The session's transcripts hold the assignment that widens what the agent may write, so it never writes them.
    transcript = event.get('transcript_path') or ''
    sessions = [Path(os.path.realpath(transcript[:-len('.jsonl')]))] if transcript.endswith('.jsonl') else []
    try:
        with deadline():
            paths = (shell_writes(tool_input.get('command') or '', cwd) if tool == 'Bash'
                     else [tool_input.get(FILE_TOOLS[tool]) or ''])
            root, assigned, written = (protected(paths, cwd, sessions, lambda: assigned_worktree(event),
                                                 yield_gate.main_checkout(cwd)) if paths else (None, None, []))
    except Exception as error:  # A check that cannot run denies the call.
        deny(f'{event["agent_type"]} is read-only (writes: none) and this write could not be checked: {error}')
        return
    if written:
        deny(refusal(event['agent_type'], root, assigned, written, "its session's transcripts") +
             f' ({ROOT}/agents/{agent}.md).')


def child_task(event):
    """The task name of the Codex child making the call: its rollout's session_meta `agent_path` (`/root/<task>`)
    when that meta is the child's own; None when unreadable or nested deeper."""
    try:
        with open(event['transcript_path']) as rows:
            meta = json.loads(next(rows))['payload']
    except (OSError, ValueError, StopIteration, KeyError, TypeError):
        return None
    path = meta.get('agent_path') or '' if meta.get('id') == event['agent_id'] else ''
    task = path[len('/root/'):] if path.startswith('/root/') else ''
    return task if TASK.fullmatch(task) else None


def state_index(session):
    """<zloop state>/codex-children/<session>/: codex-assign leaves one file there per checkout holding the
    session's records, naming its main checkout, so a session without records is told apart without parsing."""
    state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state').expanduser()
    return state / 'zloop/codex-children' / session


def git_main(directory):
    """The main checkout of the repository holding a directory, read from its `.git` without running git (a linked
    worktree's `.git` file names its git directory, whose `commondir` leads to the main one); a `.git` file that
    names no git directory is passed over, as junk a child may write; None outside any repository."""
    for place in (directory, *directory.parents):
        dot = place / '.git'
        if dot.is_dir():
            return place
        try:
            gitdir = place / dot.read_text().split('gitdir:', 1)[1].strip() if dot.is_file() else None
        except (OSError, ValueError, IndexError):
            continue
        if gitdir and gitdir.is_dir():
            common = gitdir / 'commondir'
            return Path(os.path.realpath(gitdir / common.read_text().strip() if common.is_file() else gitdir)).parent
    return None


def recording_mains(session, cwd):
    """The main checkouts holding the session's record directory: those its index names, and the turn cwd's, found
    without git (the index may lie in another state directory); only those whose record directory exists. Each
    source is read on its own, so a bad one hides none of the others."""
    found = []
    try:
        index = state_index(session)
        entries = list(index.iterdir()) if index.is_dir() else []
    except (OSError, RuntimeError):  # RuntimeError: no home directory to name the state directory from.
        entries = []
    for source in [*(entry.read_text for entry in entries), lambda: git_main(Path(os.path.realpath(cwd)))]:
        try:
            named = source()
            main = named and Path(os.path.realpath(str(named).strip()))
            if main and (main / RECORDS / session).is_dir():
                found.append(main)
        except (OSError, ValueError):
            continue
    return list(dict.fromkeys(found))


def judge(event, command, cwd, session, mains, task):
    """The deny reason for a Codex child's call in a session whose records lie under `mains`, else None. Each path
    is judged by the record in the checkout it lands in; no child writes the records, their index or its rollout."""
    paths = (shell_writes(command, WORKDIR) if event['tool_name'] == 'Bash'
             else [os.path.join(cwd, path) for path in patch_paths(command)])  # The patch tool's cwd is the turn's.
    name = f'Codex child {task or event["agent_id"]}'
    # The rollout names the child's task, which picks its record, so no child writes it.
    rollout = event.get('transcript_path')
    guarded = [Path(os.path.realpath(place)) for place in
               (*(main / RECORDS for main in mains), state_index(session), *([rollout] if rollout else []))]
    never = f'the assignment records, their index {state_index(session)} or its rollout {rollout}'
    written = [path for path in paths if os.path.isabs(path) and
               any(Path(os.path.realpath(path)).is_relative_to(place) for place in guarded)]
    if written:
        return f'{name} never writes {never}; {", ".join(written)} is there.'
    records = {}
    for main in mains:
        try:
            record = json.loads((main / RECORDS / session / f'{task}.json').read_text()) if task else None
        except (OSError, ValueError):
            record = None
        definition = isinstance(record, dict) and agent_defs.load(ROOT)['agents'].get(record.get('agent'))
        records[main] = (record, definition) if definition else (None, None)
    writer = {main for main, (record, definition) in records.items()
              if definition and definition.get('writes') != 'none'}
    relative = [path[len(WORKDIR) + 1:] for path in paths if not os.path.isabs(path)]
    if relative and git_main(Path(os.path.realpath(cwd))) not in writer:
        return (f'{name} is held read-only (writes: none) and {", ".join(relative)} is relative: Codex does not '
                f'pass the shell workdir to hooks, so write to an absolute path.')
    reasons = []
    for main, (record, definition) in records.items():
        if main in writer:
            continue
        worktree = record and record.get('worktree')
        root, assigned, held = protected([path for path in paths if os.path.isabs(path)], cwd, [],
                                         lambda: worktree and Path(worktree), main)
        if held:
            agent = (f'({ROOT}/agents/{record["agent"]}.md).' if record else
                     f'(no assignment record in {main / RECORDS / session}).')
            reasons.append(refusal(name, root, assigned, held, never) + f' {agent}')
    return ' '.join(reasons) or None


def codex(event):
    """A Codex native child's Bash or apply_patch call (the payload carries `agent_id`), judged by the assignment
    its parent recorded under <main>/.scratch/codex-children/<session id>/. A session without that directory is
    not checked; the guard fails closed only once it is found."""
    tool, tool_input = event.get('tool_name'), event.get('tool_input')
    command = tool_input.get('command') if isinstance(tool_input, dict) else None
    session = event.get('session_id') or ''
    if (not event.get('agent_id') or tool not in ('Bash', 'apply_patch') or not isinstance(command, str)
            or not SESSION.fullmatch(session)):
        return
    cwd = event.get('cwd') or os.getcwd()
    try:
        mains = recording_mains(session, cwd)
    except Exception:  # Before the session's records are known, a lookup that fails leaves the call unchecked.
        return
    if not mains:
        return
    try:
        task = child_task(event)
    except Exception:
        task = None  # An odd rollout names no task: no record applies.
    try:
        with deadline():
            reason = judge(event, command, cwd, session, mains, task)
    except Exception as error:  # Once they are known, a check that cannot run denies the call.
        reason = (f'Codex child {task or event["agent_id"]} is held read-only (writes: none) and this call could '
                  f'not be checked: {error}')
    if reason:
        deny(reason)


def assign(args):
    """`codex-assign --attempt <id> --agent-file <abs> [--worktree <abs>]`: record and print the child's task name.
    The parent records the assignment before `spawn_agent`, since the task message is encrypted, and indexes the
    checkout under the session in the zloop state directory. Run from inside the checkout the child works in.
    An existing record is never overwritten, though `rm` can remove it. The refusal of a child's shell, where
    CODEX_THREAD_ID is not the root session's id, is advisory: a child's command can set that variable."""
    parser = argparse.ArgumentParser(prog='read_only_writes.py codex-assign')
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--agent-file', required=True)
    parser.add_argument('--worktree')
    options = parser.parse_args(args)
    task = native_settings.codex_task_name(options.attempt)
    session, thread = os.environ.get('CODEX_SESSION_ID'), os.environ.get('CODEX_THREAD_ID')
    agent = Path(options.agent_file).stem
    main = yield_gate.main_checkout(os.getcwd())
    worktree = options.worktree and Path(os.path.realpath(options.worktree))
    problem = (
        'run it from the Codex parent session\'s own shell' if not session or thread not in (None, session) else
        f'session id {session!r} is not a Codex session id' if not SESSION.fullmatch(session) else
        'attempt id must not be empty' if not options.attempt.strip() else
        f'{options.agent_file} is not a zloop agent file'
        if not (Path(options.agent_file).is_file() and agent in agent_defs.load(ROOT)['agents']) else
        'run it from inside the repository' if main is None else
        f'{options.worktree} is not a worktree of {main}' if worktree and worktree not in checkouts(main)[1:] else None)
    if problem:
        print(f'codex-assign: {problem}; nothing recorded.', file=sys.stderr)
        return 2
    path = main / RECORDS / session / f'{task}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    index = state_index(session)
    index.mkdir(parents=True, exist_ok=True)
    (index / hashlib.sha256(str(main).encode()).hexdigest()[:16]).write_text(f'{main}\n')
    try:
        with open(path, 'x') as record:  # Never overwritten: a spawned child cannot rewrite its own record.
            record.write(json.dumps({'agent': agent, **({'worktree': str(worktree)} if worktree else {})}) + '\n')
    except FileExistsError:
        print(f'codex-assign: attempt {options.attempt} is already recorded at {path}; use a fresh attempt id.',
              file=sys.stderr)
        return 2
    print(task)
    return 0


def main():
    if sys.argv[1:2] == ['codex-assign']:
        sys.exit(assign(sys.argv[2:]))
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    if isinstance(event, dict):  # Anything else is no tool call; a hook that crashed would not block either.
        (codex if sys.argv[1:2] == ['codex'] else claude)(event)


def deny(reason):
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': reason}}))


if __name__ == '__main__':
    main()
