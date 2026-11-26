"""Identify and validate test-first check receipts for an assigned ticket."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import subprocess


UNITTEST_CLAIM = re.compile(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*){2,}')


def valid_claim(value):
    """A test ID fits on one result line and has no ambiguous outer whitespace."""
    return bool(value and value.isprintable() and value == value.strip())


def valid_test_file(value):
    """A declared exception is one normalized, repo-relative conventional test file."""
    path = PurePosixPath(value)
    if (not valid_claim(value) or path.is_absolute() or path.as_posix() != value
            or '..' in path.parts or '\\' in value):
        return False
    return (any(part in ('test', 'tests', '__tests__', 'spec', 'specs') for part in path.parts[:-1])
            or path.name.startswith('test_') or path.stem.endswith(('_test', '.test', '.spec')))


def assigned_attempt(state, attempt, claim):
    """Read a running attempt's declared claim without creating or updating worker state."""
    if not state.is_absolute():
        raise ValueError('--state must be absolute')
    with closing(sqlite3.connect(f'{state.as_uri()}?mode=ro', uri=True, timeout=10)) as db:
        row = db.execute('SELECT data FROM attempts WHERE id = ?', (attempt,)).fetchone()
    saved = json.loads(row[0]) if row else {}
    if claim not in saved.get('check_receipts', {}).get('claims', []) or saved.get('result') or saved.get('closed'):
        raise ValueError(f'{attempt}: no open ticket attempt with test claim {claim}')
    if Path.cwd().resolve() != Path(saved['worktree']).resolve():
        raise ValueError(f'{attempt}: run the claim from its assigned worktree root')
    return saved


def git(root, *args):
    """Read Git state without external diffs or text conversion."""
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, stdin=subprocess.DEVNULL).stdout


def entries(raw):
    """Yield NUL-delimited Git entries, including names with whitespace."""
    return (entry for entry in raw.split(b'\0') if entry)


def tree_entries(root, head):
    """Map each commit path to its mode and object identity."""
    paths = {}
    for entry in entries(git(root, 'ls-tree', '-rz', '--full-tree', head)):
        details, name = entry.split(b'\t', 1)
        mode, _, oid = details.split()
        paths[name] = mode, oid
    return paths


def index_entries(root):
    """Map the staged paths, retaining unresolved entries as mismatches."""
    paths = {}
    for entry in entries(git(root, 'ls-files', '--stage', '-z')):
        details, name = entry.split(b'\t', 1)
        mode, oid, stage = details.split()
        paths[name] = (mode, oid) if stage == b'0' else None
    return paths


def working_entry(root, name, expected, algorithm):
    """Return the raw worktree blob identity or gitlink for one path."""
    path = root / os.fsdecode(name)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(mode):
        mode, data = b'120000', os.fsencode(os.readlink(path))
    elif stat.S_ISREG(mode):
        mode, data = (b'100755' if mode & stat.S_IXUSR else b'100644'), path.read_bytes()
    elif stat.S_ISDIR(mode) and expected and expected[0] == b'160000':
        if (path / '.git').exists() and not git(path, 'status', '--porcelain'):
            return b'160000', git(path, 'rev-parse', '--verify', 'HEAD').strip()
        return None
    else:
        return None
    blob = hashlib.new(algorithm, f'blob {len(data)}\0'.encode() + data).hexdigest().encode()
    return mode, blob


def source_snapshot(root, phase, claim, base, test_file):
    """Check the index and worktree and identify the test run at that snapshot."""
    head = git(root, 'rev-parse', '--verify', 'HEAD').decode().strip()
    if phase == 'red' and head != base:
        raise ValueError('red must run at the assigned base')
    tracked, index = tree_entries(root, head), index_entries(root)
    untracked = set(entries(git(root, 'ls-files', '--others', '--exclude-standard', '-z')))
    algorithm = git(root, 'rev-parse', '--show-object-format').decode().strip()
    module = claim.rsplit('.', 2)[0].replace('.', '/') + '.py' if test_file is None else None
    test_module = os.fsencode(module) if module and valid_test_file(module) else None
    declared = os.fsencode(test_file) if test_file else None
    changed_test, test_sources, test_context = {}, {}, {}
    for path in sorted(tracked.keys() | index.keys() | untracked):
        expected = tracked.get(path)
        actual = working_entry(root, path, expected, algorithm)
        name = os.fsdecode(path)
        regular = actual and actual[0] in (b'100644', b'100755')
        is_subject = path == declared or (test_module and (path == test_module or path.endswith(b'/' + test_module)))
        is_test_file = valid_test_file(name)
        if regular and is_subject:
            test_sources[name] = f'{actual[0].decode()}:{actual[1].decode()}'
        elif actual and is_test_file:
            test_context[name] = f'{actual[0].decode()}:{actual[1].decode()}'
        if path not in untracked and actual == expected and index.get(path) == expected:
            continue
        if phase == 'red' and regular and is_test_file:
            changed_test[name] = actual[1].decode()
            continue
        raise ValueError(f'{phase} source differs from {head}: {name}')
    if len(test_sources) != 1:
        raise ValueError(f'{claim}: expected one test source file')
    path, identity = next(iter(test_sources.items()))
    return {'head': head, 'tree': git(root, 'rev-parse', f'{head}^{{tree}}').decode().strip(),
            'test_changes': changed_test, 'test': {'path': path, 'identity': identity, 'context': test_context}}


def observed_claim(log, phase, claim, output_format):
    """Read a test's result in the selected output format."""
    lines = log.read_text(errors='replace').splitlines()
    if output_format == 'marker':
        verdict = 'FAIL' if phase == 'red' else 'PASS'
        outcomes = {f'{status}: {claim}' for status in ('FAIL', 'PASS', 'ERROR', 'SKIP')}
        return outcomes.intersection(lines) == {f'{verdict}: {claim}'}
    return unittest_claim(lines, phase, claim)


def unittest_claim(lines, phase, claim):
    """Read an exact verbose unittest result and its matching run footer."""
    test = f'{claim.rsplit(".", 1)[1]} ({claim})'
    status = 'FAIL' if phase == 'red' else 'ok'
    separator = '=' * 70
    boundary = next((i for i, line in enumerate(lines)
                     if line in (separator, '-' * 70)), len(lines))
    progress = lines[:boundary]
    if not any(line == f'{test} ... {status}' or
               (line == test and progress[i + 1:i + 2] and progress[i + 1].endswith(f' ... {status}'))
               for i, line in enumerate(progress)):
        return False
    footer = next((lines[i + 3] for i, line in enumerate(lines[:-3])
                   if line == '-' * 70 and re.fullmatch(r'Ran [1-9]\d* tests? in .+s', lines[i + 1])
                   and not lines[i + 2]), '')
    if phase == 'green':
        return re.fullmatch(r'OK(?: \(.*\))?', footer) is not None
    failed = re.fullmatch(r'FAILED \((.*)\)', footer)
    return (failed is not None and re.search(r'(?:^|, )failures=[1-9]\d*(?:, |$)', failed[1]) is not None
            and any(line == separator and lines[i + 1] == f'FAIL: {test}'
                    for i, line in enumerate(lines[:-1])))


def receipt(saved, phase, claim, snapshot, started_ns, finished_ns, log, output_format, command):
    """Describe a completed claim and its retained output at the verified source."""
    return dict(attempt=saved['attempt'], identity=saved['check_receipts']['identity'], phase=phase,
                claim=claim, format=output_format, base=saved['head'], source=snapshot,
                started_ns=started_ns, finished_ns=finished_ns,
                command_sha256=hashlib.sha256(json.dumps(command).encode()).hexdigest(),
                log_sha256=hashlib.sha256(log.read_bytes()).hexdigest())


def criteria(path):
    """Read declared test claims and identify the canonical acceptance criteria."""
    document = path.read_text()
    section = re.search(r'^## Acceptance criteria[ \t]*\n(.*?)(?=^## |\Z)', document, re.M | re.S)
    if not section:
        return {'criteria': None, 'claims': [], 'test_files': {}, 'problems': []}
    claims, test_files, problems = [], {}, []
    items = re.split(r'^\s*[-*] \[[ xX]\][ \t]+', section[1], flags=re.M)
    for number, item in enumerate(items[1:], 1):
        if not re.search(r'^\s*Verify \(code\):', item, re.M):
            continue
        values = re.findall(r'^\s*Test claim:[ \t]*(.*)$', item, re.M)
        files = re.findall(r'^\s*Test file:[ \t]*(.*)$', item, re.M)
        claim = values[0].strip() if len(values) == 1 else ''
        if not valid_claim(claim):
            problems.append(f'code criterion {number}: declare one Test claim')
        elif claim in claims:
            problems.append(f'code criterion {number}: test claim {claim} is already used')
        elif files and (len(files) != 1 or not valid_test_file(files[0].strip())):
            problems.append(f'code criterion {number}: declare one repo-relative Test file in a test location')
        else:
            claims.append(claim)
            if files:
                test_files[claim] = files[0].strip()
    return {'criteria': hashlib.sha256(section[1].encode()).hexdigest(), 'claims': claims,
            'test_files': test_files, 'problems': problems}


def sealed_receipts(evidence):
    """Capture the summary files named in the collected result before acceptance."""
    receipts = {}
    for value in evidence:
        path = Path(value)
        if not path.is_absolute():
            continue
        if path.suffix == '.log':
            path = path.with_name(path.name + '.summary.json')
        if not path.name.endswith('.summary.json'):
            continue
        try:
            raw = path.read_bytes()
            summary = json.loads(raw)
        except (OSError, ValueError):
            continue
        if isinstance(summary, dict) and isinstance(summary.get('tdd'), dict):
            receipts[str(path)] = hashlib.sha256(raw).hexdigest()
    return receipts


def retained_receipt(path, digest, saved, collected_ns):
    """Read a phase result only if it still describes the collected attempt and its output."""
    try:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            return None
        summary = json.loads(raw)
        proof = summary['tdd']
        log = Path(summary['log'])
        if (not log.is_absolute() or path != log.with_name(log.name + '.summary.json')
                or hashlib.sha256(log.read_bytes()).hexdigest() != proof['log_sha256']):
            return None
        phase, claim = proof['phase'], proof['claim']
        source_head = proof['source']['head']
        if (not isinstance(source_head, str) or proof['attempt'] != saved['attempt']
                or proof['identity'] != saved['check_receipts']['identity']
                or proof['base'] != saved['head'] or phase not in ('red', 'green')
                or claim not in saved['check_receipts']['claims']
                or not 0 < proof['started_ns'] < proof['finished_ns'] <= collected_ns
                or summary['exit_code'] != (1 if phase == 'red' else 0)
                or (phase == 'red' and source_head != saved['head'])):
            return None
        return proof
    except (OSError, ValueError, KeyError, TypeError):
        return None


def same_test_run(red, green):
    """Match the command and current source identity for the same test format."""
    if (red.get('format') not in ('marker', 'unittest') or red['format'] != green.get('format')
            or not red.get('command_sha256') or red['command_sha256'] != green.get('command_sha256')):
        return False
    before, after = red['source'].get('test'), green['source'].get('test')
    if not isinstance(before, dict) or not isinstance(after, dict) or before.get('path') != after.get('path'):
        return False
    identity = before.get('identity')
    earlier, later = before.get('context'), after.get('context')
    return (isinstance(identity, str) and bool(identity) and identity == after.get('identity')
            and isinstance(earlier, dict) and isinstance(later, dict)
            and all(later.get(path) == blob for path, blob in earlier.items()))


def problems(saved, head, *, ticket=None, base=None):
    """Report missing, moved or out-of-order proof for a managed ticket result."""
    assigned = saved.get('check_receipts')
    if not assigned:
        if ticket is not None and saved.get('result', {}).get('checks_collected_ns'):
            return ['managed attempt has no test claims assigned to this ticket']
        return []
    canonical = Path(assigned['ticket'])
    if ticket is not None and ticket.resolve() != canonical:
        return ['test receipts belong to a different ticket']
    if assigned['claims'] and base is not None and base != saved['head']:
        return ['report base differs from the assigned test receipt base']
    try:
        current = criteria(canonical)
    except (OSError, ValueError):
        return ['cannot read the criteria for the assigned test claims']
    if current['criteria'] != assigned['criteria']:
        return ['acceptance criteria changed since test claims were assigned']
    missing = assigned['problems'][:]
    result = saved.get('result', {})
    proofs = [proof for path, digest in result.get('check_receipts', {}).items()
              if (proof := retained_receipt(Path(path), digest, saved, result['checks_collected_ns']))]
    for claim in assigned['claims']:
        red = [proof for proof in proofs if proof['phase'] == 'red' and proof['claim'] == claim]
        green = [proof for proof in proofs if proof['phase'] == 'green' and proof['claim'] == claim
                 and proof['source']['head'] == head]
        if not red:
            missing.append(f'{claim}: missing red receipt at the assigned base')
        elif not green:
            missing.append(f'{claim}: missing green receipt at accepted head {head}')
        else:
            matches = [(before, after) for before in red for after in green if same_test_run(before, after)]
            if not matches:
                missing.append(f'{claim}: red and green must run the same test, command and format')
            elif all(before['finished_ns'] >= after['started_ns'] for before, after in matches):
                missing.append(f'{claim}: red must finish before green begins')
    return missing
