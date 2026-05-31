"""Tolerant JSON extraction for small-model summary output.

These reproduce the two malformations the local VL model actually emitted on
real captures, each of which threw away a perfectly good summary before the
extractor was made tolerant:

  1. Unescaped double quotes around a word inside a string value
     (the "corenous" repo).
  2. A trailing extra closing brace (``...]}}``).
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.ai.summarizer import _extract_json_object


class TestExtractJsonObject(unittest.TestCase):

    def test_plain_valid_json(self):
        obj = _extract_json_object('{"heading":"Read Rust chapter","subject":"async"}')
        self.assertEqual(obj["heading"], "Read Rust chapter")

    def test_unescaped_inner_quotes_are_recovered(self):
        raw = (
            '{"heading":"Reviewed repo fixes","subject":"corenous work",'
            '"paragraphs":["The user is reviewing the "corenous" repository.",'
            '"They discuss committing two bug fixes."]}'
        )
        obj = _extract_json_object(raw)
        self.assertIsNotNone(obj)
        self.assertEqual(obj["heading"], "Reviewed repo fixes")
        self.assertEqual(len(obj["paragraphs"]), 2)
        self.assertIn("corenous", obj["paragraphs"][0])

    def test_trailing_extra_brace_is_recovered(self):
        raw = (
            '{"heading":"Reviewing session notes","subject":"Coding notes",'
            '"paragraphs":["The notes discuss fixes.","Two bug fixes staged."]}}'
        )
        obj = _extract_json_object(raw)
        self.assertIsNotNone(obj)
        self.assertEqual(obj["subject"], "Coding notes")
        self.assertEqual(len(obj["paragraphs"]), 2)

    def test_prose_after_object_is_ignored(self):
        raw = '{"heading":"Did a thing","subject":"topic"} Here is why I chose that.'
        obj = _extract_json_object(raw)
        self.assertEqual(obj["heading"], "Did a thing")

    def test_trailing_comma_still_handled(self):
        obj = _extract_json_object('{"heading":"A","subject":"B",}')
        self.assertEqual(obj["subject"], "B")

    def test_garbage_returns_none(self):
        self.assertIsNone(_extract_json_object("not json at all"))
        self.assertIsNone(_extract_json_object(""))


if __name__ == "__main__":
    unittest.main()
