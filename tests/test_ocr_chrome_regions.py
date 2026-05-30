"""
Tests for `_drop_chrome_observations` — region-aware OCR cleanup that
strips the thin top/bottom chrome strips (title bars, toolbars, status
bars) using Vision's normalized bounding boxes.

Convention (verified against real Vision output): box = (x, y, w, h),
normalized [0,1], origin BOTTOM-LEFT. So y_center near 1.0 = TOP of the
window, near 0.0 = BOTTOM.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.monitor.screen import _drop_chrome_observations


def _obs(text, y_center, *, h=0.018, x=0.3, w=0.3, conf=0.9):
    """Build one (text, box, conf) item from a vertical center."""
    y = y_center - h / 2.0
    return (text, (x, y, w, h), conf)


def _texts(observations):
    return [o[0] for o in observations]


class TestDropChromeObservations(unittest.TestCase):

    def _padding(self, n):
        """Filler middle-of-window content so the safety fallback (don't gut
        the capture) doesn't trigger in tests that only care about edges."""
        return [_obs(f"body paragraph line number {i} with real content", 0.5 - i * 0.02)
                for i in range(n)]

    def test_drops_thin_short_top_strip(self):
        obs = [_obs("corenous / repository review", 0.967)] + self._padding(6)
        out = _drop_chrome_observations(obs)
        self.assertNotIn("corenous / repository review", _texts(out))

    def test_drops_thin_short_bottom_strip(self):
        obs = self._padding(6) + [
            _obs("Opus 4.8", 0.027),
            _obs("Bypass permissions", 0.027),
            _obs("v1.9255.0", 0.083, h=0.016),
        ]
        out = _texts(_drop_chrome_observations(obs))
        self.assertNotIn("Opus 4.8", out)
        self.assertNotIn("Bypass permissions", out)
        self.assertNotIn("v1.9255.0", out)

    def test_keeps_middle_content(self):
        obs = [_obs("This is the real article body sentence", 0.5)] + self._padding(6)
        out = _texts(_drop_chrome_observations(obs))
        self.assertIn("This is the real article body sentence", out)

    def test_keeps_long_text_at_top(self):
        """A long line at the very top is a heading / first paragraph, not a
        toolbar button — must be kept even though it's at the edge."""
        long_line = "Traditional ML versus LLM based OCR engines compared in depth today"
        obs = [_obs(long_line, 0.967)] + self._padding(6)
        self.assertIn(long_line, _texts(_drop_chrome_observations(obs)))

    def test_keeps_tall_box_at_top(self):
        """A tall block at the top (h > 0.03) is a content region, not a thin
        chrome strip — keep it."""
        obs = [_obs("Big Title Block", 0.96, h=0.06)] + self._padding(6)
        self.assertIn("Big Title Block", _texts(_drop_chrome_observations(obs)))

    def test_preserves_order(self):
        obs = [
            _obs("title bar chrome", 0.967),
            _obs("first content", 0.7),
            _obs("second content", 0.5),
            _obs("third content", 0.3),
            _obs("status chrome", 0.027),
        ]
        out = _texts(_drop_chrome_observations(obs))
        self.assertEqual(out, ["first content", "second content", "third content"])

    def test_safety_fallback_when_filtering_guts_capture(self):
        """If nearly everything is chrome-shaped at the edges (weird layout),
        return the original list rather than emit an almost-empty capture."""
        obs = [
            _obs("a", 0.99), _obs("b", 0.98), _obs("c", 0.97),
            _obs("d", 0.02), _obs("e", 0.03), _obs("f", 0.04),
        ]
        out = _drop_chrome_observations(obs)
        self.assertEqual(out, obs)  # unchanged

    def test_too_few_observations_returned_unchanged(self):
        obs = [_obs("x", 0.97), _obs("y", 0.5), _obs("z", 0.03)]
        self.assertEqual(_drop_chrome_observations(obs), obs)

    def test_realistic_claude_window_capture(self):
        """Modeled on the real probe output: Claude app window with a title
        bar at the top, nav tabs, a conversation in the middle, and a status
        bar at the bottom. Chrome strips drop; conversation survives."""
        obs = [
            _obs("corenous / Corenous repository review", 0.967, x=0.26, w=0.214),
            _obs("∞ 2", 0.967, x=0.926, w=0.025),
            _obs("okay do it all, but isnt there any llm model other than", 0.930, x=0.295, w=0.374),
            _obs("The local GGUF model runs entirely on device with no API key", 0.62),
            _obs("It uses the Metal backend for GPU acceleration on Apple silicon", 0.55),
            _obs("You can swap presets in config settings yaml under local_llm", 0.48),
            _obs("Type / for commands", 0.082, x=0.293, w=0.115, h=0.025),
            _obs("u Utkarsh • Pro", 0.032, x=0.020, w=0.080, h=0.016),
            _obs("Opus 4.8", 0.027, x=0.894, w=0.044, h=0.016),
            _obs("Bypass permissions", 0.027, x=0.291, w=0.094, h=0.016),
        ]
        out = _texts(_drop_chrome_observations(obs))
        # Title bar + status bar chrome gone.
        self.assertNotIn("corenous / Corenous repository review", out)
        self.assertNotIn("Opus 4.8", out)
        self.assertNotIn("Bypass permissions", out)
        self.assertNotIn("Type / for commands", out)
        # The actual conversation content stays.
        self.assertTrue(any("Metal backend" in t for t in out))
        self.assertTrue(any("local_llm" in t for t in out))


if __name__ == "__main__":
    unittest.main()
