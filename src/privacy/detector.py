from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from .patterns import (
    ALL_PII_PATTERNS,
    FINANCE_KEYWORDS,
    HEALTH_KEYWORDS,
    PRIVACY_COMPLIANCE_KEYWORDS,
)

if TYPE_CHECKING:
    pass


@dataclass
class DetectionResult:
    is_sensitive: bool
    reasons: list[str] = field(default_factory=list)


class SensitivityDetector:
    def __init__(self, user_keywords: list[str] | None = None) -> None:
        self._user_re: re.Pattern | None = None
        if user_keywords:
            escaped = [re.escape(kw) for kw in user_keywords if kw.strip()]
            if escaped:
                pattern = r"\b(?:" + "|".join(escaped) + r")\b"
                self._user_re = re.compile(pattern, re.IGNORECASE)

    @classmethod
    def from_config(cls, config_path: Path) -> "SensitivityDetector":
        try:
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            keywords = cfg.get("privacy", {}).get("sensitive_keywords", []) or []
            return cls(user_keywords=keywords)
        except FileNotFoundError:
            return cls()

    def classify(self, text: str) -> DetectionResult:
        # Layer 1: structural regex — fast, reliable, always runs
        reasons: list[str] = self._check_regex(text)

        # Layer 2: user-defined keywords
        reasons.extend(self._check_user_keywords(text))

        if reasons:
            # Already flagged — no need to go further
            return DetectionResult(is_sensitive=True, reasons=reasons)

        # Layer 3: keyword lists for health / finance
        reasons.extend(self._check_health_finance(text))

        if reasons:
            return DetectionResult(is_sensitive=True, reasons=reasons)

        # Layer 4: AI contextual check — non-blocking, skipped if model is busy
        reasons.extend(self._check_ai(text))

        return DetectionResult(is_sensitive=bool(reasons), reasons=reasons)

    def _check_regex(self, text: str) -> list[str]:
        found = []
        for name, pattern in ALL_PII_PATTERNS:
            if pattern.search(text):
                found.append(name)
        return found

    def _check_health_finance(self, text: str) -> list[str]:
        lower = text.lower()
        found = []
        for kw in HEALTH_KEYWORDS:
            if kw in lower:
                found.append(f"health:{kw}")
        for kw in FINANCE_KEYWORDS:
            if kw in lower:
                found.append(f"finance:{kw}")
        for kw in PRIVACY_COMPLIANCE_KEYWORDS:
            if kw in lower:
                found.append(f"privacy:{kw}")
        return found

    def _check_user_keywords(self, text: str) -> list[str]:
        if self._user_re and self._user_re.search(text):
            return ["user_keyword"]
        return []

    def _check_ai(self, text: str) -> list[str]:
        """Non-blocking LLM check for contextual sensitivity not caught by patterns."""
        try:
            from ..ai.summarizer import ai_is_sensitive
            is_sens, reason = ai_is_sensitive(text)
            if is_sens:
                return [f"ai_context:{reason}"]
        except Exception:
            pass
        return []
