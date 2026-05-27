"""
Tests for the orphan UI guidance in _MEMORY_BULLETS_PROMPT.

The chrome line filter (strip_ui_chrome) catches one and two word
sidebar items by regex, but multi word sidebar entries like
"Corenous repository review" look identical to legitimate page
titles and cannot be filtered without false positives. The prompt
level guidance tells the model to treat orphan capitalized phrases
as UI labels rather than user actions.

These tests verify the guidance strings are present in the prompt
so the rule cannot silently disappear in a future refactor.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ai.summarizer import _MEMORY_BULLETS_PROMPT


class TestOrphanUIPromptGuidance(unittest.TestCase):

    def test_ignore_section_mentions_sidebar_entries(self):
        """The IGNORE THESE COMPLETELY block must explicitly call out
        sidebar entries and project lists so the model knows to skip
        them, not treat them as content."""
        self.assertIn("Sidebar entries", _MEMORY_BULLETS_PROMPT)
        self.assertIn("project lists", _MEMORY_BULLETS_PROMPT)

    def test_ignore_section_includes_concrete_examples(self):
        """Small LLMs follow examples better than abstract rules. The
        prompt cites the exact strings observed in the live failure
        so the model has a pattern to anchor on."""
        self.assertIn("Corenous repository review", _MEMORY_BULLETS_PROMPT)
        self.assertIn("Photo storage for portfolio site", _MEMORY_BULLETS_PROMPT)
        self.assertIn("Customize", _MEMORY_BULLETS_PROMPT)

    def test_ignore_section_covers_action_button_labels(self):
        """Action buttons (Copy, Edit, Delete, etc.) get the same
        treatment as nav items: visible labels, not user actions."""
        prompt = _MEMORY_BULLETS_PROMPT
        for label in ("Copy", "Edit", "Delete", "Save", "Back", "Regenerate"):
            self.assertIn(label, prompt, f"action label '{label}' missing from prompt")

    def test_anti_hallucination_section_addresses_orphan_phrases(self):
        """The ANTI-HALLUCINATION block must give the model an explicit
        rule about standalone capitalized phrases, with reasoning so
        the rule generalizes beyond the cited examples."""
        prompt = _MEMORY_BULLETS_PROMPT
        # The rule itself
        self.assertIn("orphan capitalized", prompt.lower())
        # The reasoning: such phrases are UI labels
        self.assertIn("UI label", prompt)
        # The negative example with concrete word
        self.assertIn("Customize", prompt)

    def test_explicit_do_not_turn_into_action(self):
        """Negative framing is needed too: small models will otherwise
        confidently confabulate verb phrases from noun labels."""
        prompt_lower = _MEMORY_BULLETS_PROMPT.lower()
        self.assertIn("never turn them into actions", prompt_lower)


if __name__ == "__main__":
    unittest.main()
