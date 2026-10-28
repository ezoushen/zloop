#!/usr/bin/env python3
"""The commands ask for a workstream: a body of work takes the glossary's word, never `effort`."""
import re
import unittest

import support

ROOT = support.ROOT
# The commands that take a body of work: align and refine settle and split one, dispatch runs its tickets, pr
# delivers it and brief reports on it.
COMMANDS = ('align', 'refine', 'dispatch', 'pr', 'brief')


def hint(command):
    """The argument hint in the command's front matter."""
    metadata = (ROOT / 'skills' / command / 'SKILL.md').read_text().split('---', 2)[1]
    match = re.search(r'^argument-hint: (.+)$', metadata, re.M)
    assert match, f'{command} has no argument-hint'
    return match.group(1)


class CommandHints(unittest.TestCase):
    def test_a_command_hint_names_a_workstream_and_never_an_effort(self):
        for command in COMMANDS:
            with self.subTest(command=command):
                value = hint(command)
                self.assertIn('workstream', value, value)
                self.assertIsNone(re.search(r'effort', value, re.I), value)


if __name__ == '__main__':
    unittest.main()
