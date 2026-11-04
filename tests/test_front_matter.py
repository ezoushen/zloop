#!/usr/bin/env python3
"""The one front-matter reader every script and hook shares."""
import unittest

import support  # noqa: F401  (puts scripts/ on sys.path)
import front_matter


class FrontMatter(unittest.TestCase):
    def test_a_file_without_front_matter_has_none(self):
        # Rule: text that does not open with a `---` block has no front matter, so a reader can tell it from an empty one.
        for text in ('', 'status: ready\n', 'Title\n---\nstatus: ready\n---\n', '---\nstatus: ready\n'):
            with self.subTest(text=text):
                self.assertIsNone(front_matter.parse(text))

    def test_values_are_scalars_bracketed_lists_or_block_lists(self):
        # Rule: `key: value` is a string, `key: [a, b]` and a bare `key:` followed by `- item` lines are lists.
        text = ('---\n# a comment\nstatus: ready\n\nneeds:\n  - network\n  - docker\ntouches: [a.py, b.py]\n'
                'blocked_by: []\n---\nbody: not a field\n')
        self.assertEqual(front_matter.parse(text), {'status': 'ready', 'needs': ['network', 'docker'],
                                                    'touches': ['a.py', 'b.py'], 'blocked_by': []})

    def test_a_block_that_ends_the_file_still_closes(self):
        # Rule: a report whose last line is the closing `---` has front matter, with or without a final newline.
        for text in ('---\nreview_status: passed\n---', '---\nreview_status: passed\n---\n'):
            with self.subTest(text=text):
                self.assertEqual(front_matter.parse(text), {'review_status': 'passed'})

    def test_a_dashed_line_inside_a_value_or_the_body_does_not_end_the_block_early(self):
        # Rule: only a line that is exactly `---` closes the block; `a---b` and `----` stay inside it.
        text = '---\nblocker: a---b\nnote: ----\nstatus: ready\n---\nbody\n---\nlater: x\n---\n'
        self.assertEqual(front_matter.parse(text), {'blocker': 'a---b', 'note': '----', 'status': 'ready'})

    def test_list_items_lose_their_quotes_and_a_string_does_only_through_unquoted(self):
        # Rule: a list item loses its quotes (`["a", "b"]` is the list a, b); a string value keeps what it was written
        # with until unquoted() strips the quotes or backticks around it.
        fields = front_matter.parse('---\ninputs: ["worktree", "ticket context"]\nstatus: `passed`\n---\n')
        self.assertEqual(fields, {'inputs': ['worktree', 'ticket context'], 'status': '`passed`'})
        self.assertEqual([front_matter.unquoted(v) for v in ('"a"', "'a'", '`a`', 'a', '"a b" ')], ['a', 'a', 'a', 'a', 'a b'])

    def test_split_returns_the_body_after_the_block_and_the_whole_text_when_there_is_no_block(self):
        # Rule: callers that read a document's sections get the text after the closing `---`, untouched.
        self.assertEqual(front_matter.split('---\nname: a\n---\n# a\n\ntext\n'), ({'name': 'a'}, '# a\n\ntext\n'))
        self.assertEqual(front_matter.split('# a\n'), (None, '# a\n'))


if __name__ == '__main__':
    unittest.main()
