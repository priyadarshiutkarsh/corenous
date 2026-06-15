"""
Tests for the LoCoMo answer-accuracy (J score) pipeline logic.

The generator and judge are injected callables, so the harness's correctness
(abstention handling, judge parsing, short-circuiting) is verified here without
loading any model.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eval_locomo_qa import generate_answer, judge_answer, is_abstention


class TestGenerateAnswer(unittest.TestCase):

    def test_uses_contexts_and_returns_model_text(self):
        seen = {}

        def gen(prompt, max_tokens):
            seen["prompt"] = prompt
            return "  Friday  "

        out = generate_answer("When do they launch?", ["launch is Friday"], gen)
        self.assertEqual(out, "Friday")
        self.assertIn("launch is Friday", seen["prompt"])
        self.assertIn("When do they launch?", seen["prompt"])

    def test_empty_context_short_circuits_without_calling_model(self):
        called = {"n": 0}

        def gen(prompt, max_tokens):
            called["n"] += 1
            return "should not happen"

        out = generate_answer("q?", ["", "   "], gen)
        self.assertEqual(out, "I don't know")
        self.assertEqual(called["n"], 0)

    def test_blank_model_output_becomes_dont_know(self):
        out = generate_answer("q?", ["some context"], lambda p, m: "")
        self.assertEqual(out, "I don't know")


class TestJudgeAnswer(unittest.TestCase):

    def test_correct_verdict(self):
        ok = judge_answer("q?", "Friday", "It is on Friday", lambda p, m: "CORRECT")
        self.assertTrue(ok)

    def test_wrong_verdict(self):
        ok = judge_answer("q?", "Friday", "Monday", lambda p, m: "WRONG")
        self.assertFalse(ok)

    def test_verdict_is_case_and_whitespace_tolerant(self):
        self.assertTrue(judge_answer("q?", "x", "x", lambda p, m: "  correct\n"))

    def test_abstention_never_calls_judge_and_is_wrong(self):
        called = {"n": 0}

        def judge(prompt, max_tokens):
            called["n"] += 1
            return "CORRECT"

        self.assertFalse(judge_answer("q?", "Friday", "I don't know", judge))
        self.assertFalse(judge_answer("q?", "Friday", "", judge))
        self.assertEqual(called["n"], 0)

    def test_judge_prompt_carries_all_three_fields(self):
        seen = {}

        def judge(prompt, max_tokens):
            seen["p"] = prompt
            return "CORRECT"

        judge_answer("the question", "the gold", "the pred", judge)
        for needle in ("the question", "the gold", "the pred"):
            self.assertIn(needle, seen["p"])


class TestGroqProvider(unittest.TestCase):
    """The Groq judge wiring: keyed from env, routed through the shared
    groq_chat_completion helper, with a strong default judge model."""

    def test_missing_key_exits(self):
        import os
        from unittest import mock
        import eval_locomo_qa as q
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            with self.assertRaises(SystemExit):
                q.make_llm("groq")

    def test_calls_groq_with_env_key_and_default_model(self):
        import os
        from unittest import mock
        import eval_locomo_qa as q
        seen = {}

        def fake_completion(*, system, user, model, api_key, max_tokens, temperature):
            seen.update(model=model, api_key=api_key, user=user, temp=temperature)
            return "CORRECT"

        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test"}, clear=False):
            os.environ.pop("GROQ_JUDGE_MODEL", None)
            with mock.patch("src.ai.remote_summary.groq_chat_completion", fake_completion):
                fn = q.make_llm("groq")
                out = fn("is this right?", 4)
        self.assertEqual(out, "CORRECT")
        self.assertEqual(seen["api_key"], "gsk_test")
        self.assertEqual(seen["model"], "llama-3.3-70b-versatile")
        self.assertEqual(seen["temp"], 0.0)

    def test_judge_model_override(self):
        import os
        from unittest import mock
        import eval_locomo_qa as q
        with mock.patch.dict(os.environ,
                             {"GROQ_API_KEY": "k", "GROQ_JUDGE_MODEL": "custom-model"}):
            with mock.patch("src.ai.remote_summary.groq_chat_completion",
                            return_value="WRONG") as m:
                q.make_llm("groq")("x", 4)
        self.assertEqual(m.call_args.kwargs["model"], "custom-model")


class TestIsAbstention(unittest.TestCase):

    def test_variants(self):
        for s in ("", "  ", "I don't know", "i dont know", "Unknown", "UNKNOWN it is"):
            self.assertTrue(is_abstention(s), s)

    def test_real_answer_is_not_abstention(self):
        for s in ("Friday", "the blue one", "2019"):
            self.assertFalse(is_abstention(s), s)


if __name__ == "__main__":
    unittest.main()
