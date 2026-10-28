#!/usr/bin/env python3
"""check_docs.py finds smells in agent-facing Markdown: each rule on a small fixture tree."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import support

CHECKER = support.ROOT / '.agents/skills/writing-agent-docs/check_docs.py'
CONFIG = {
    'docs': ['skills/**/*.md', 'shared/**/*.md'],
    'linking': ['README.md', 'skills/**/*.md', 'shared/**/*.md'],
    'code': ['scripts/*.py'],
    'code_doc_pattern': r'(?:shared|skills)/[\w/-]+\.md',
    'entry_points': ['skills/*/SKILL.md'],
    'skill_files': 'skills/*/SKILL.md',
    'max_bytes': 2000,
    'description_budget': 300,
    'debt': {},
}
SKILL = '---\nname: {name}\ndescription: {description}\n---\n\n# Tool\n\nRead [the guide](../../shared/guide.md).\n'
GUIDE = '# Guide\n\n## Steps\n\nRun the tool.\n'


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout


class Fixture(unittest.TestCase):
    """A fixture repository, written and checked through the checker's command line."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='doc checker ')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config_dir = tempfile.TemporaryDirectory(prefix='doc checker config ')
        self.addCleanup(self.config_dir.cleanup)
        self.config = dict(CONFIG)
        self.write('README.md', '# Project\n\nSee [tool](skills/tool/SKILL.md).\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool. Use when asked to run it.'))
        self.write('shared/guide.md', GUIDE)
        self.write('scripts/run.py', "GUIDE = 'shared/guide.md'\n")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def check_full(self, *args):
        config = Path(self.config_dir.name) / 'checker.json'
        settings = dict(self.config)
        if getattr(self, 'architecture', None) is not None:
            architecture = Path(self.config_dir.name) / 'architecture.json'
            architecture.write_text(json.dumps(self.architecture))
            settings['architecture'] = 'architecture.json'
        config.write_text(json.dumps(settings))
        result = subprocess.run([sys.executable, str(CHECKER), '--root', str(self.root), '--config', str(config),
                                 '--json', *args], capture_output=True, text=True)
        assert result.returncode in (0, 1), result.stderr
        findings = json.loads(result.stdout)
        assert result.returncode == int(any(f['severity'] == 'error' for f in findings)), (result.returncode, findings)
        return findings

    def check(self, *args):
        findings = self.check_full(*args)
        keys = [(f['path'], f['line'], f['code'], f['severity']) for f in findings]
        assert len(keys) == len(set(keys)), f'duplicate findings: {findings}'
        return set(keys)

    def commit(self):
        if not (self.root / '.git').exists():
            git(self.root, 'init', '-q')
        git(self.root, 'add', '-A')
        git(self.root, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base')


class DocChecker(Fixture):
    """Each rule reports the smell at its file and line, and a clean tree reports nothing."""

    def test_a_clean_tree_has_no_findings(self):
        self.assertEqual(self.check(), set())

    def test_a_doc_over_its_byte_budget_is_an_error_unless_recorded_as_debt_that_has_not_grown(self):
        self.write('shared/guide.md', GUIDE + 'x' * 2100 + '\n')
        self.assertEqual(self.check(), {('shared/guide.md', 1, 'size', 'error')})
        size = (self.root / 'shared/guide.md').stat().st_size
        self.config['debt'] = {'shared/guide.md': {'bytes': size}}
        self.assertEqual(self.check(), {('shared/guide.md', 1, 'debt', 'warning')})
        self.config['debt'] = {'shared/guide.md': {'bytes': size - 1}}
        self.assertEqual(self.check(), {('shared/guide.md', 1, 'size', 'error')})
        self.config['debt'] = {'shared/guide.md': {'bytes': size + 500}}
        findings = self.check_full()
        self.assertEqual([(f['code'], f['severity']) for f in findings], [('debt', 'warning')])
        self.assertIn(f'lower the recorded size to {size}', findings[0]['message'])
        self.write('shared/guide.md', GUIDE)
        self.assertEqual(self.check(), {('shared/guide.md', 1, 'stale-debt', 'warning')})

    def test_a_peoples_doc_over_the_byte_budget_is_an_error_too(self):
        # Rule: agents also read the docs people read (README, rehearsals), so every checked doc fits one read.
        self.write('README.md', '# Project\n\nSee [tool](skills/tool/SKILL.md).\n' + 'x' * 2100 + '\n')
        self.assertEqual(self.check(), {('README.md', 1, 'size', 'error')})
        size = (self.root / 'README.md').stat().st_size
        self.config['debt'] = {'README.md': {'bytes': size}}
        self.assertEqual(self.check(), {('README.md', 1, 'debt', 'warning')})

    def test_a_peoples_doc_gets_only_the_size_rule_of_the_per_file_rules(self):
        # Rule: contents lists and wording smells are for agent docs; a person's doc is held only to one read.
        sections = ''.join(f'## Part {n}\n\n' + 'Line.\n' * 30 for n in range(3))
        self.write('README.md', '# Project\n\nSee [tool](skills/tool/SKILL.md), as of 2026.\n\n' + sections)
        self.assertEqual(self.check(), set())

    def test_a_skill_body_over_500_lines_is_an_error(self):
        self.config['max_bytes'] = 100000
        body = SKILL.format(name='tool', description='Runs the tool.') + 'line\n' * 496
        self.write('skills/tool/SKILL.md', body)
        self.assertEqual(self.check(), set())
        self.write('skills/tool/SKILL.md', body + 'line\n')
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'skill-lines', 'error')})

    def test_frontmatter_names_and_descriptions_follow_the_skill_format(self):
        self.config['description_budget'] = 5000
        valid = {'t' * 64: 'Runs the tool.', 'tool': 'Runs the tool with a > b.'}
        invalid = {
            'Tool_X': 'Runs the tool.',
            't' * 65: 'Runs the tool.',
            'claude-tool': 'Runs the tool.',
            'anthropic-tool': 'Runs the tool.',
            'tool': '',
            'tool ': 'Runs <b>the</b> tool.',
            'tool  ': 'd' * 1025,
        }
        for name, description in valid.items():
            with self.subTest(name=name, description=description):
                self.write('skills/tool/SKILL.md', SKILL.format(name=name, description=description))
                self.assertEqual(self.check(), set())
        for name, description in invalid.items():
            with self.subTest(name=name, description=description):
                self.write('skills/tool/SKILL.md', SKILL.format(name=name.strip(), description=description))
                self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'frontmatter', 'error')})
        block = '---\nname: tool\ndescription: >\n  ' + 'd' * 600 + '\n  ' + 'd' * 600 + '\n---\n\n# Tool\n' \
            + 'Read [the guide](../../shared/guide.md).\n'
        self.write('skills/tool/SKILL.md', block)
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'frontmatter', 'error')})

    def test_broken_links_and_anchors_are_errors_and_numbered_duplicates_resolve(self):
        self.write('shared/guide.md', GUIDE + '\n## Steps\n\n## Steps\n\n```text\n## Fenced\n```\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + 'See [third](../../shared/guide.md#steps-2), [gone](../../shared/old.md),\n'
                   + '[fenced](../../shared/guide.md#fenced),\n'
                   + 'and [here](#nowhere) but [the tool](#tool).\n')
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 9, 'link', 'error'),
                                        ('skills/tool/SKILL.md', 10, 'anchor', 'error'),
                                        ('skills/tool/SKILL.md', 11, 'anchor', 'error')})

    def test_github_heading_forms_give_the_anchors_github_gives(self):
        self.write('shared/guide.md', '# Guide\n\n## Steps ##\n\nSetup\n=====\n\n## Use _this_ way\n\n'
                   + '## See [the tool](../skills/tool/SKILL.md)\n\nText\n---\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + '[a](../../shared/guide.md#steps) [b](../../shared/guide.md#setup)\n'
                   + '[c](../../shared/guide.md#use-this-way) [d](../../shared/guide.md#see-the-tool)\n'
                   + '[e](../../shared/guide.md#text)\n')
        self.assertEqual(self.check(), set())

    def test_fences_of_either_kind_and_any_length_hide_their_contents(self):
        self.write('shared/guide.md', GUIDE + '\n~~~md\n[a](missing.md)\n~~~\n\n````md\n```sh\n[b](missing.md)\n```\n'
                   + '[c](missing.md)\n````\n\n[d](missing.md)\n')
        self.assertEqual(self.check(), {('shared/guide.md', 18, 'link', 'error')})

    def test_every_link_form_is_resolved_and_counts_as_reaching_its_target(self):
        self.write('shared/child(1).md', '# Child\n')
        self.write('shared/my file.md', '# Spaced\n')
        self.write('skills/tool/SKILL.md', '---\nname: tool\ndescription: Runs the tool.\n---\n\n# Tool\n\n'
                   + 'Read [the guide][g] and [the child](../../shared/child(1).md "title").\n'
                   + 'Also [spaced](../../shared/my%20file.md?plain=1) and [gone](../../shared/gone.md "t").\n\n'
                   + '[g]: ../../shared/guide.md\n[x]: <../../shared/missing.md>\n')
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 9, 'link', 'error'),
                                        ('skills/tool/SKILL.md', 12, 'link', 'error')})

    def test_footnotes_prose_labels_indented_and_quoted_fences_are_not_links(self):
        self.write('shared/guide.md', '---\n\n# Guide\n\n---\n\n[^1]: Footnote text here.\n[Status]: ok\n'
                   + '[Role]: Reviewer owns this\n\n1. Step\n\n    ```sh\n    [in code](nope.md)\n    ```\n\n'
                   + '> ```sh\n> [quoted](nope.md)\n> ```\n\n[ref]: ../skills/tool/SKILL.md "Tool"\n[bad]: gone.md\n')
        self.assertEqual(self.check(), {('shared/guide.md', 22, 'link', 'error')})

    def test_anchors_keep_underscores_and_percent_encoding_and_multi_line_setext_headings(self):
        self.write('shared/guide.md', '# Guide\n\n## my_var_name\n\n## `tool_output_token_limit` setting\n\n'
                   + '## Émile café\n\npara line one\npara line two\n---\n')
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + '[a](../../shared/guide.md#my_var_name) [b](../../shared/guide.md#tool_output_token_limit-setting)\n'
                   + '[c](../../shared/guide.md#%C3%A9mile-caf%C3%A9) [d](../../shared/guide.md#para-line-one-para-line-two)\n')
        self.assertEqual(self.check(), set())

    def test_a_plain_description_continued_on_the_next_lines_is_read_whole(self):
        self.write('skills/tool/SKILL.md', '---\nname: tool\ndescription:\n  Runs the tool.\n  Use when asked.\n---\n\n'
                   + '# Tool\n\nRead [the guide](../../shared/guide.md).\n')
        self.assertEqual(self.check(), set())

    def test_debt_for_a_missing_file_or_a_short_doc_is_stale(self):
        self.config['debt'] = {'shared/gone.md': {'bytes': 5000}, 'shared/guide.md': {'contents': True}}
        self.assertEqual(self.check(), {('shared/gone.md', 1, 'stale-debt', 'warning'),
                                        ('shared/guide.md', 1, 'stale-debt', 'warning')})

    def test_changed_mode_reads_only_text_and_parses_diffs_whatever_the_git_config(self):
        self.write('shared/guide.md', GUIDE)
        self.commit()
        git(self.root, 'config', 'diff.noprefix', 'true')
        (self.root / 'shared/pic.png').write_bytes(b'\x89PNG\r\n\x1a\n\xff')
        self.write('shared/guide.md', GUIDE + '\n++ plus plus\nSee the example above here.\n')
        self.assertEqual(self.check('--changed', 'HEAD'), {('shared/guide.md', 8, 'positional', 'warning')})

    def test_link_case_must_match_the_file_for_case_sensitive_checkouts(self):
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool.')
                   + 'See [it](../../shared/Guide.md).\n')
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 9, 'link', 'error')})

    def test_a_doc_named_only_in_code_is_not_an_orphan(self):
        self.write('skills/tool/SKILL.md', '---\nname: tool\ndescription: Runs the tool.\n---\n\n# Tool\n')
        self.assertEqual(self.check(), set())

    def test_a_long_multi_section_doc_starts_with_its_exact_contents(self):
        sections = ''.join(f'## Part {n}\n\n' + 'text\n' * 33 for n in range(1, 4))
        self.write('shared/guide.md', '# Guide\n\n' + sections)
        self.assertEqual(self.check(), {('shared/guide.md', 1, 'contents', 'error')})
        self.config['debt'] = {'shared/guide.md': {'contents': True}}
        self.assertEqual(self.check(), {('shared/guide.md', 1, 'debt', 'warning')})
        self.config['debt'] = {}
        contents = 'Contents:\n- [Part 1](#part-1)\n- [Part 3](#part-3)\n- [Part 2](#part-2)\n\n'
        self.write('shared/guide.md', '# Guide\n\n' + contents + sections)
        self.assertEqual(self.check(), {('shared/guide.md', 1, 'contents', 'error')})
        contents = 'Contents:\n- [Part 1](#part-1)\n- [Part 2](#part-2)\n- [Part 3](#part-3)\n\n'
        fenced = '```text\n## Not a section\n```\n'
        self.write('shared/guide.md', '# Guide\n\n' + contents + fenced + sections)
        self.assertEqual(self.check(), set())
        self.config['debt'] = {'shared/guide.md': {'contents': True}}
        self.assertEqual(self.check(), {('shared/guide.md', 1, 'stale-debt', 'warning')})
        self.config['debt'] = {}
        short = ''.join(f'## Part {n}\n\n' + 'text\n' * 30 for n in range(1, 4))
        self.write('shared/guide.md', '# Guide\n\n' + short)
        self.assertEqual(self.check(), set())

    def test_a_doc_nothing_links_is_an_orphan_but_entry_points_are_not(self):
        self.write('shared/lost.md', '# Lost\n')
        self.write('skills/other/SKILL.md', SKILL.format(name='other', description='Runs another tool.'))
        self.assertEqual(self.check(), {('shared/lost.md', 1, 'orphan', 'error')})

    def test_a_doc_path_named_in_code_must_exist_outside_excluded_fixtures(self):
        self.write('scripts/run.py', "GUIDE = 'shared/guide.md'\nOLD = 'shared/old-guide.md'\n")
        self.write('scripts/fixture.py', "FAKE = 'shared/fake.md'\n")
        self.config['exclude'] = ['scripts/fixture.py']
        self.assertEqual(self.check(), {('scripts/run.py', 2, 'code-path', 'error')})

    def test_positional_words_pointing_into_another_file_are_flagged(self):
        self.write('shared/guide.md', GUIDE + '\nApply the [limits below](../skills/tool/SKILL.md).\n'
                   + 'The steps below follow [the tool](../skills/tool/SKILL.md).\n'
                   + 'Read the [steps below](#steps).\n')
        self.assertEqual(self.check(), {('shared/guide.md', 7, 'positional', 'warning')})

    def test_changed_lines_with_positional_references_are_flagged_only_when_added(self):
        self.write('shared/guide.md', GUIDE + '\nUse the command below.\n')
        self.commit()
        self.assertEqual(self.check('--changed', 'HEAD'), set())
        self.write('shared/guide.md', GUIDE + '\nUse the command below.\nRun the example above.\n'
                   + 'Keep tokens below the limit.\n')
        self.assertEqual(self.check('--changed', 'HEAD'), {('shared/guide.md', 8, 'positional', 'warning')})

    def test_changed_mode_handles_spaces_renames_untracked_files_and_a_bad_base(self):
        self.write('shared/my guide.md', GUIDE)
        self.write('README.md', '# Project\n\nSee [tool](skills/tool/SKILL.md) and [mine](shared/my%20guide.md).\n')
        self.write('shared/old name.md', GUIDE + '\nSee the steps above.\n')
        self.write('scripts/run.py', "GUIDE = 'shared/guide.md'\nOLD = 'shared/new-name.md'\n")
        self.commit()
        self.write('shared/my guide.md', GUIDE + '\nSee the example above.\n')
        git(self.root, 'mv', 'shared/old name.md', 'shared/new-name.md')
        self.write('shared/fresh.md', '# Fresh\n\n' + 'x' * 2100 + '\n')
        self.write('scripts/run.py', "GUIDE = 'shared/guide.md'\nOLD = 'shared/new-name.md'\nNEW = 'shared/fresh.md'\n")
        self.assertEqual(self.check('--changed', 'HEAD'), {('shared/my guide.md', 7, 'positional', 'warning'),
                                                           ('shared/fresh.md', 1, 'size', 'error')})
        result = subprocess.run([sys.executable, str(CHECKER), '--root', str(self.root), '--changed', 'no-such-base'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('no-such-base', result.stderr)

    def test_changed_mode_limits_file_rules_but_keeps_repo_wide_links(self):
        self.write('shared/big.md', '# Big\n\n' + 'x' * 2100 + '\n')
        self.write('README.md', '# Project\n\nSee [tool](skills/tool/SKILL.md) and [big](shared/big.md).\n')
        git(self.root, 'init', '-q')
        git(self.root, 'add', '-A')
        git(self.root, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base')
        (self.root / 'shared/guide.md').rename(self.root / 'shared/moved.md')
        self.assertEqual(self.check('--changed', 'HEAD'), {('skills/tool/SKILL.md', 8, 'link', 'error'),
                                                           ('scripts/run.py', 1, 'code-path', 'error'),
                                                           ('shared/moved.md', 1, 'orphan', 'error')})

    def test_text_naming_a_doc_that_exists_nowhere_is_flagged(self):
        self.write('shared/guide.md', GUIDE + '\nSee the migration described in selection.md.\n'
                   + 'Read `notes.md` in the target project, then README.md and guide.md.\n'
                   + 'Follow the project AGENTS.md and shared/guide.md, not shared/gone.md.\n'
                   + 'The plan is in drafts.md.\n')
        self.write('.gitignore', '.scratch/\n')
        self.write('.scratch/drafts.md', '# Ignored\n')
        self.commit()
        self.config['known_names'] = ['AGENTS.md']
        self.assertEqual(self.check(), {('shared/guide.md', 7, 'stale-name', 'warning'),
                                        ('shared/guide.md', 9, 'stale-name', 'warning'),
                                        ('shared/guide.md', 10, 'stale-name', 'warning')})

    def test_dates_and_backslash_paths_are_flagged_but_plain_numbers_are_not(self):
        self.write('shared/guide.md', GUIDE + '\nBefore August 2025, use the old API.\nRun scripts\\helper.py first.\n'
                   + 'As of 2026 it changed; measured 2026-10-18.\n'
                   + 'Keep it under 2000 characters, by 2048 bytes, after 2000 ms.\n')
        self.assertEqual(self.check(), {('shared/guide.md', 7, 'dated', 'warning'),
                                        ('shared/guide.md', 8, 'backslash', 'warning'),
                                        ('shared/guide.md', 9, 'dated', 'warning')})

    def test_descriptions_are_written_in_the_third_person_and_fit_the_shared_list_budget(self):
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Use this skill when running the tool.'))
        self.assertEqual(self.check(), set())
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='You can use this to run the tool.'))
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'person', 'warning')})
        self.write('skills/tool/SKILL.md', SKILL.format(name='tool', description='Runs the tool. ' * 25))
        self.assertEqual(self.check(), {('skills/tool/SKILL.md', 1, 'description-budget', 'warning')})


GLOSSARY = """# Fixture

## Language

**Workstream**:
A body of work organized around one goal.
_Avoid_: effort, effort branch, effort slug

**Effort**:
The model's reasoning effort.
_Avoid_: none

**Host**:
The app that runs the session.
_Avoid_: gadget, app shell
"""


class AvoidedWords(Fixture):
    """Rule: an agent doc does not use a word the glossary lists under `_Avoid_`; the error names the term to use."""

    def setUp(self):
        super().setUp()
        self.config['glossary'] = 'GLOSSARY.md'
        self.config['docs'] = [*CONFIG['docs'], 'GLOSSARY.md']
        self.config['entry_points'] = [*CONFIG['entry_points'], 'GLOSSARY.md']
        self.write('GLOSSARY.md', GLOSSARY)

    def avoided(self):
        return [(f['path'], f['line'], f['message']) for f in self.check_full() if f['code'] == 'avoided-word']

    def test_an_avoided_phrase_is_an_error_naming_the_phrase_the_line_and_the_term_to_use(self):
        self.write('shared/guide.md', GUIDE + '\nOpen the effort branch.\n')
        self.assertEqual(self.avoided(), [(
            'shared/guide.md', 7, '"effort branch" is an _Avoid_ word in GLOSSARY.md; write Workstream')])
        self.assertEqual(self.check(), {('shared/guide.md', 7, 'avoided-word', 'error')})

    def test_a_quoted_phrase_is_not_a_use_and_neither_is_a_link_target(self):
        # The one way to quote: inline code or a fenced block; a link target is a file name, not prose.
        self.write('shared/effort-branch.md', GUIDE)
        self.write('shared/guide.md', GUIDE + '\nThe old word was `effort branch`, and ``a gadget`` too.\n'
                   + '\n```\nthe effort branch\n```\n\nSee [the guide](effort-branch.md).\n')
        self.assertEqual(self.avoided(), [])

    def test_frontmatter_values_are_scanned_but_a_quoted_phrase_there_passes(self):
        # Rule: a skill's description and argument-hint are agent-facing text, so an avoided phrase there is an error.
        self.write('skills/tool/SKILL.md', '---\nname: tool\ndescription: Open the effort branch when asked.\n'
                   + 'argument-hint: <effort-slug>\n---\n\n# Tool\n\nRead [the guide](../../shared/guide.md).\n')
        self.assertEqual(self.avoided(), [
            ('skills/tool/SKILL.md', 3, '"effort branch" is an _Avoid_ word in GLOSSARY.md; write Workstream'),
            ('skills/tool/SKILL.md', 4, '"effort slug" is an _Avoid_ word in GLOSSARY.md; write Workstream')])
        self.write('skills/tool/SKILL.md', '---\nname: tool\ndescription: Say `effort branch` never. Use when asked.\n---\n\n# Tool\n\n'
                   + 'Read [the guide](../../shared/guide.md).\n')
        self.assertEqual(self.avoided(), [])

    def test_a_phrase_that_is_also_a_term_matches_only_where_the_glossary_lists_a_longer_phrase(self):
        # Rule: "effort" is a term of its own (model effort), so it alone never errors; "effort branch" does.
        self.write('shared/guide.md', GUIDE + '\nSet the model effort, as no effort is spared.\n'
                   + 'Nothing here is gadgeted or none.\n')
        self.assertEqual(self.avoided(), [])
        self.write('shared/guide.md', GUIDE + '\nThe effort slug and the model effort.\n')
        self.assertEqual(self.avoided(), [(
            'shared/guide.md', 7, '"effort slug" is an _Avoid_ word in GLOSSARY.md; write Workstream')])

    def test_a_phrase_is_found_ignoring_case_and_plural_across_a_wrap_and_a_hyphen(self):
        self.write('shared/guide.md', GUIDE + '\nThe Effort Branch.\nAn effort-slug and two\nGadgets; its effort\nslugs.\n')
        self.assertEqual(self.avoided(), [
            ('shared/guide.md', 7, '"effort branch" is an _Avoid_ word in GLOSSARY.md; write Workstream'),
            ('shared/guide.md', 8, '"effort slug" is an _Avoid_ word in GLOSSARY.md; write Workstream'),
            ('shared/guide.md', 9, '"effort slug" is an _Avoid_ word in GLOSSARY.md; write Workstream'),
            ('shared/guide.md', 9, '"gadget" is an _Avoid_ word in GLOSSARY.md; write Host')])

    def test_a_phrase_does_not_join_words_across_a_paragraph_a_list_item_or_a_code_block(self):
        self.write('shared/guide.md', GUIDE + '\nAn effort\n\nbranch of the tree.\n\n- the effort\n- branch\n\n'
                   + 'An effort\n```\ncode\n```\nslug follows, as does an effort `x` slug.\n')
        self.assertEqual(self.avoided(), [])

    def test_the_glossary_passes_with_its_avoid_lists_but_not_a_definition_that_uses_an_avoided_word(self):
        self.assertEqual(self.avoided(), [])
        self.write('GLOSSARY.md', GLOSSARY.replace('around one goal.', 'around one goal, like a gadget.'))
        self.assertEqual(self.avoided(), [(
            'GLOSSARY.md', 6, '"gadget" is an _Avoid_ word in GLOSSARY.md; write Host')])

    def test_a_peoples_doc_may_use_an_avoided_word(self):
        # Rule: only the configured agent docs are held to the glossary; README and docs/ (ADRs included) are not.
        self.write('README.md', '# Project\n\nSee [tool](skills/tool/SKILL.md) for the effort branch.\n')
        self.assertEqual(self.avoided(), [])

    def test_without_a_glossary_in_the_config_nothing_is_avoided_and_a_missing_glossary_is_invalid_input(self):
        self.write('shared/guide.md', GUIDE + '\nOpen the effort branch.\n')
        del self.config['glossary']
        self.assertEqual(self.avoided(), [])
        self.config['glossary'] = 'MISSING.md'
        config = Path(self.config_dir.name) / 'checker.json'
        config.write_text(json.dumps(self.config))
        result = subprocess.run([sys.executable, str(CHECKER), '--root', str(self.root), '--config', str(config)],
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertIn('MISSING.md', result.stderr)


if __name__ == '__main__':
    unittest.main()
