#!/usr/bin/env python3
"""Agents share one language-neutral module-design vocabulary from our own standard."""
import unittest

import support

ROOT = support.ROOT
DESIGN = ROOT / 'standards/design.md'


def prose(path):
    return ' '.join(path.read_text().split())


class DesignStandard(unittest.TestCase):
    def test_each_term_has_a_definition(self):
        text = prose(DESIGN)
        for term, meaning in (
                ('Module', 'anything with an interface and an implementation'),
                ('Interface', 'everything a caller must know to use the module correctly'),
                ('Implementation', 'what is inside the module, behind its interface'),
                ('Depth', 'how much behavior a caller or test can exercise per unit of interface it must learn'),
                ('Seam', 'a place where behavior can be changed without editing that place'),
                ('Adapter', 'a concrete thing that satisfies an interface at a seam'),
                ('Leverage', 'what callers gain from depth'),
                ('Locality', 'what maintainers gain from depth')):
            with self.subTest(term=term):
                self.assertIn(f'**{term}:** {meaning}', text)

    def test_the_standard_keeps_each_required_rule(self):
        text = prose(DESIGN)
        for rule in ('invariants, ordering constraints, error modes, required configuration and performance limits',
                     '**The deletion test.** Imagine deleting the module.',
                     'If its complexity vanishes, it was a pass-through',
                     'if the complexity reappears across its callers, it earns its place',
                     '**One adapter is a hypothetical seam, two a real one.**',
                     'a seam with one adapter is only indirection',
                     '**The interface is the test surface.**',
                     'Tests cross the same seam as callers and assert what is observable there, not internal state',
                     'a test that must reach past the interface means the module has the wrong shape',
                     '**Shape the interface for its callers.** Prefer fewer operations, simpler inputs and more hidden '
                     'inside',
                     'A module that receives its dependencies, and returns results instead of only changing shared '
                     'state, can be tested through its interface alone',
                     '**Parse outside data once, where it enters.** Input from files, commands, other processes or the '
                     'network is checked at the seam where it enters, into a form that cannot hold an illegal value; '
                     'code inside trusts that form instead of checking it again',
                     'In-process computation and state need no seam: test through the module\'s interface',
                     'A dependency with a local stand-in, such as an embedded database, runs in the tests behind an '
                     'internal seam',
                     'An owned remote service gets a seam with a production and an in-memory adapter',
                     'A third-party service gets a seam whose test adapter is a mock',
                     '**Replace shallow tests, don\'t layer them.** When shallow modules merge into a deep one, write '
                     'tests at its interface and delete the old tests of the parts',
                     '**Design it twice** when an interface is in question',
                     'three or more independent designers, launched in parallel through the orchestrator',
                     'each produce a materially different interface under a different constraint',
                     'with its operations, a usage sketch, what it hides and its dependency strategy',
                     'the smallest interface',
                     'the most flexibility',
                     'the simplest common call',
                     'ports and adapters across the seam',
                     'compare them on depth, locality and seam placement and recommend one'):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_the_standard_has_no_code_in_any_language(self):
        self.assertEqual([], support.code_samples(DESIGN.read_text()))

    def test_the_code_detector_finds_each_kind_of_sample(self):
        for sample in ('Use `mock()` here.', '~~~\nprint(1)\n~~~', '```\nx\n```', 'Text\n\n    print(1)\n',
                       '> ~~~python\n> print(1)\n> ~~~\n', '- Example:\n\n        print(1)\n',
                       '- An item\n\n    ~~~\n    print(1)\n    ~~~\n', '- An item\n\nA paragraph.\n\n    print(1)\n',
                       'Patch jest.mock first.',
                       'Use unittest.mock for it.', 'Use re.I to ignore case.', 'Call x.append(1).',
                       'Intro\n\n    - name: example\n      value: 1\n',
                       '- Parent\n  - Nested rule\n\n  Parent paragraph.\n\n      print(1)\n',
                       '- an item\n  wrapped\n\n    its second paragraph\n', 'Text\n  \n    print(1)\n',
                       '- An item\n  ~~~\n  print(1)\n  ~~~\n', 'Para.\n\n\tprint(1)\n', '    print(1)\n\n'):
            with self.subTest(sample=sample):
                self.assertNotEqual([], support.code_samples(sample))
        for legitimate in ('See [module design](design.md).\n- a list\n  continued\n',
                           'A stand-in, e.g. an embedded database, i.e. one that runs locally.',
                           'The tautological-tests.md kind lists them.',
                           '- a list\n    - a nested item\n      continued\n'):
            with self.subTest(legitimate=legitimate):
                self.assertEqual([], support.code_samples(legitimate))

    def test_align_designs_with_our_standard(self):
        align = prose(ROOT / 'skills/align/SKILL.md')
        self.assertIn('[module design](../../standards/design.md)', align)
        self.assertIn('[module design](../../standards/design.md)', prose(ROOT / 'skills/review/SKILL.md'))


if __name__ == '__main__':
    unittest.main()
