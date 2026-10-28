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

    def test_the_interface_covers_more_than_its_operations(self):
        self.assertIn('invariants, ordering constraints, error modes, required configuration and performance limits',
                      prose(DESIGN))

    def test_the_deletion_test_tells_a_pass_through_from_a_module_that_earns_its_place(self):
        text = prose(DESIGN)
        self.assertIn('**The deletion test.** Imagine deleting the module.', text)
        self.assertIn('If its complexity vanishes, it was a pass-through', text)
        self.assertIn('if the complexity reappears across its callers, it earns its place', text)

    def test_a_seam_needs_two_adapters(self):
        text = prose(DESIGN)
        self.assertIn('**One adapter is a hypothetical seam, two a real one.**', text)
        self.assertIn('a seam with one adapter is only indirection', text)

    def test_tests_cross_the_interface_callers_use(self):
        text = prose(DESIGN)
        self.assertIn('**The interface is the test surface.**', text)
        self.assertIn('Tests cross the same seam as callers and assert what is observable there, not internal state',
                      text)
        self.assertIn('a test that must reach past the interface means the module has the wrong shape', text)

    def test_the_interface_is_shaped_for_its_callers(self):
        text = prose(DESIGN)
        self.assertIn('**Shape the interface for its callers.** Prefer fewer operations, simpler inputs and more hidden '
                      'inside', text)
        self.assertIn('A module that receives its dependencies, and returns results instead of only changing shared '
                      'state, can be tested through its interface alone', text)

    def test_outside_data_is_parsed_once_where_it_enters(self):
        self.assertIn('**Parse outside data once, where it enters.** Input from files, commands, other processes or the '
                      'network is checked at the seam where it enters, into a form that cannot hold an illegal value; '
                      'code inside trusts that form instead of checking it again', prose(DESIGN))

    def test_a_seam_is_placed_by_what_the_dependency_is(self):
        text = prose(DESIGN)
        for rule in ('In-process computation and state need no seam: test through the module\'s interface',
                     'A dependency with a local stand-in, such as an embedded database, runs in the tests behind an '
                     'internal seam',
                     'An owned remote service gets a seam with a production and an in-memory adapter',
                     'A third-party service gets a seam whose test adapter is a mock'):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_tests_of_merged_shallow_modules_are_replaced_not_layered(self):
        self.assertIn('**Replace shallow tests, don\'t layer them.** When shallow modules merge into a deep one, write '
                      'tests at its interface and delete the old tests of the parts', prose(DESIGN))

    def test_a_questioned_interface_is_designed_several_ways_by_independent_designers(self):
        text = prose(DESIGN)
        self.assertIn('**Design it twice** when an interface is in question', text)
        self.assertIn('three or more independent designers, launched in parallel through the orchestrator', text)
        self.assertIn('each produce a materially different interface under a different constraint', text)
        self.assertIn('with its operations, a usage sketch, what it hides and its dependency strategy', text)
        for constraint in ('the smallest interface', 'the most flexibility', 'the simplest common call',
                           'ports and adapters across the seam'):
            with self.subTest(constraint=constraint):
                self.assertIn(constraint, text)
        self.assertIn('compare them on depth, locality and seam placement and recommend one', text)

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
