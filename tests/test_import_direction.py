#!/usr/bin/env python3
"""Scripts and hooks import only modules at or below their own layer (architecture.json)."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import support

DOC_CHECKER = support.ROOT / '.agents/skills/writing-agent-docs'
sys.path.insert(0, str(DOC_CHECKER))
from architecture import Architecture, check_imports  # noqa: E402

FIXTURE = {
    'levels': [['hooks'], ['high'], ['low']],
    'modules': {
        'hooks': {'code': ['hooks/*.py']},
        'high': {'code': ['scripts/high.py', 'scripts/top.py']},
        'low': {'code': ['scripts/low.py']},
    },
    'debt': {},
}


class ImportDirection(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='import direction ')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.arch = Architecture(json.loads(json.dumps(FIXTURE)))

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def errors(self):
        return [(src, line, code) for src, line, severity, code, _ in check_imports(self.root, self.arch) if severity == 'error']

    def test_downward_imports_pass_including_a_hook_importing_a_script(self):
        self.write('scripts/high.py', 'import low\n')
        self.write('scripts/low.py', 'X = 1\n')
        self.write('hooks/run.py', 'from high import X\n')
        self.assertEqual(self.errors(), [])

    def test_an_import_of_a_higher_layer_inside_a_function_names_both_scripts(self):
        self.write('scripts/high.py', 'X = 1\n')
        self.write('scripts/low.py', 'def run():\n    import high\n    return high.X\n')
        findings = check_imports(self.root, self.arch)
        self.assertEqual([(f[0], f[1], f[2], f[3]) for f in findings if f[3] != 'cycle'],
                         [('scripts/low.py', 2, 'error', 'import-direction')])
        message = [f[4] for f in findings if f[3] == 'import-direction'][0]
        self.assertIn('scripts/low.py', message)
        self.assertIn('scripts/high.py', message)

    def test_package_qualified_imports_name_the_script_they_import(self):
        self.write('scripts/high.py', 'X = 1\n')
        for form in ('import scripts.high', 'from scripts.high import X', 'from scripts import high'):
            with self.subTest(form=form):
                self.write('scripts/low.py', form + '\n')
                self.assertEqual(self.errors(), [('scripts/low.py', 1, 'import-direction')])
                self.write('hooks/run.py', form.replace('high', 'low') + '\n')
                self.write('scripts/low.py', 'X = 1\n')
                self.assertEqual(self.errors(), [])

    def test_a_script_path_named_as_a_string_is_a_dependency_too(self):
        self.write('scripts/high.py', 'X = 1\n')
        self.write('scripts/top.py', 'from high import X\n')
        self.write('hooks/run.py', "SPEC = ROOT / 'scripts/low.py'\n")
        self.write('scripts/low.py', "SPEC = 'scripts/top.py'\n")
        self.assertEqual(self.errors(), [('scripts/low.py', 1, 'import-direction')])

    def test_two_scripts_importing_each_other_across_modules_are_a_cycle(self):
        self.write('scripts/high.py', 'import low\n')
        self.write('scripts/low.py', 'def f():\n    import high\n')
        codes = {finding[3] for finding in check_imports(self.root, self.arch)}
        self.assertEqual(codes, {'import-direction', 'cycle'})

    def test_a_script_no_module_owns_is_an_error(self):
        self.arch.data['owned_code'] = ['scripts/*.py', 'hooks/*.py']
        self.write('scripts/high.py', 'X = 1\n')
        self.write('scripts/helper.py', 'import high\n')
        self.write('hooks/run.py', 'X = 1\n')
        self.write('scripts/notes.txt', 'not code\n')
        found = [(src, line, code) for src, line, severity, code, _ in check_imports(self.root, self.arch)
                 if severity == 'error']
        self.assertEqual(sorted(found), [('scripts/helper.py', 1, 'unmapped')])

    def test_a_script_that_does_not_parse_is_a_finding_not_a_crash(self):
        self.write('scripts/high.py', 'X = 1\n')
        self.write('scripts/low.py', 'def broken(:\n')
        self.assertEqual(self.errors(), [('scripts/low.py', 1, 'syntax')])

    def test_recorded_import_debt_is_tolerated_and_only_shrinks(self):
        self.write('scripts/high.py', 'X = 1\n')
        self.write('scripts/low.py', 'import high\n')
        self.arch.debt = {'imports': ['scripts/low.py -> scripts/high.py'], 'import_cycles': []}
        self.assertEqual(self.errors(), [])
        self.write('scripts/low.py', 'X = 1\n')
        self.assertEqual([f[3] for f in check_imports(self.root, self.arch)], ['stale-debt'])

    def test_the_repository_scripts_and_hooks_break_no_rule_beyond_recorded_debt(self):
        architecture = Architecture.load(DOC_CHECKER / 'architecture.json')
        findings = [f for f in check_imports(support.ROOT, architecture) if f[2] == 'error']
        self.assertEqual(findings, [])

    def test_the_config_modules_load_without_the_decision_backend_or_routing(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import executor_config, journal_store; "
                "loaded = {'decisions', 'agent_models', 'decision_journal'} & set(sys.modules); "
                "print(','.join(sorted(loaded)))")
        run = subprocess.run([sys.executable, '-B', '-c', code, str(support.ROOT / 'scripts')],
                             capture_output=True, text=True)
        self.assertEqual((run.returncode, run.stdout.strip(), run.stderr.strip()), (0, '', ''))


if __name__ == '__main__':
    unittest.main()
