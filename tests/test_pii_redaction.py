"""Tests for the PII severity split.

High-severity PII (SSN, cards, keys, passwords, PEM) routes the whole capture
to the vault. Low-severity contact details (email, phone) no longer vault the
memory — they are redacted inline so the memory stays in the normal timeline."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.privacy.detector import SensitivityDetector
from src.privacy.patterns import redact_pii


class TestSeveritySplit(unittest.TestCase):
    def setUp(self) -> None:
        self.det = SensitivityDetector()

    def test_email_only_is_not_sensitive(self):
        self.assertFalse(
            self.det.classify("Ping me at john@example.com about the demo").is_sensitive
        )

    def test_phone_only_is_not_sensitive(self):
        self.assertFalse(
            self.det.classify("Call the office at 415-555-0199 tomorrow").is_sensitive
        )

    def test_ssn_is_sensitive(self):
        self.assertTrue(self.det.classify("SSN 123-45-6789 on file").is_sensitive)

    def test_api_key_is_sensitive(self):
        r = self.det.classify("export OPENAI=sk-" + "a" * 40)
        self.assertTrue(r.is_sensitive)

    def test_credit_card_is_sensitive(self):
        self.assertTrue(
            self.det.classify("card 4111 1111 1111 1111 expires soon").is_sensitive
        )


class TestRedaction(unittest.TestCase):
    def test_email_is_redacted(self):
        out = redact_pii("reach me at jane.doe@work.co please")
        self.assertNotIn("jane.doe@work.co", out)
        self.assertIn("[email]", out)

    def test_phone_is_redacted(self):
        out = redact_pii("my cell is (415) 555-0199 ok")
        self.assertNotIn("555-0199", out)
        self.assertIn("[phone]", out)

    def test_plain_text_unchanged(self):
        text = "Rust async runtime internals and tokio scheduling"
        self.assertEqual(redact_pii(text), text)

    def test_empty_is_safe(self):
        self.assertEqual(redact_pii(""), "")


if __name__ == "__main__":
    unittest.main()
