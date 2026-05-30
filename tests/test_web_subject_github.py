"""
Tests for the GitHub branch of `_web_content_subject` — pull request pages
must derive the topic from the actual PR title, never from a hardcoded
literal.

Regression target: the PR branch carried an overfit literal that returned
"Reviewed reminder timing pull request" for any PR whose title mentioned a
reminder firing at the "wrong time" — overfit to one of the developer's own
past PRs. The fix drops the literal so every PR page falls through to the
content-derived "Reviewed pull request about ..." label.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.summaries import _web_content_subject


def _gh(body: str, title: str) -> str:
    return f"{title} github.com\nSite: github.com\n{body}"


class TestGitHubPullRequestSubject(unittest.TestCase):

    def test_reminder_pr_is_not_hardcoded_literal(self):
        out = _web_content_subject(
            _gh("wants to merge 1 commit", "Reminder fires at the wrong time by user Pull Request"),
            "Reminder timing", "Viewed",
        )
        self.assertNotEqual(out, "Reviewed reminder timing pull request")
        self.assertTrue(out.lower().startswith("reviewed pull request about"), out)
        self.assertIn("reminder", out.lower())

    def test_generic_pr_title_is_content_derived(self):
        out = _web_content_subject(
            _gh("wants to merge 2 commits", "Add retry logic to the uploader by someone Pull Request"),
            "Add retry logic", "Viewed",
        )
        self.assertTrue(out.lower().startswith("reviewed pull request about"), out)
        self.assertIn("retry", out.lower())


if __name__ == "__main__":
    unittest.main()
