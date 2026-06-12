#!/usr/bin/env python3
"""LoCoMo answer-accuracy (J score) for corenous.

This is the end-to-end QA number competitors quote (Mem0, Zep, supermemory all
report a LoCoMo "J" / answer-accuracy, judged by an LLM). It is NOT the
retrieval recall that eval_locomo.py reports. Pipeline per question:

  1. ingest the conversation into a fresh corenous store (the real capture path)
  2. retrieve top-k memories with the production search (combined_search)
  3. GENERATE an answer from the retrieved memories with an LLM
  4. JUDGE the generated answer against the gold answer with an LLM

Generator and judge are chosen independently (--gen / --judge), each one of:
  local       on-device Qwen2.5-VL via MLX (no cloud, but ties up the GPU and a
              3B model is a weak judge: not comparable to the GPT-4-class judges
              the published numbers use)
  openrouter  whatever model is set in ~/.corenous/remote.json (set a strong
              model here for the judge to make the number comparable)

Honest knobs: adversarial questions (category 5) are scored separately as an
abstention check, not folded into the main J score, matching common practice.

Dataset (same as eval_locomo.py):
  curl -L -o /tmp/locomo10.json \\
    https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

Examples:
  # cheapest credible setup: generate locally, judge with a strong cloud model
  CORENOUS provider in remote.json = a strong model, then:
  ./.venv/bin/python scripts/eval_locomo_qa.py /tmp/locomo10.json \\
      --gen local --judge openrouter --limit 2

  # fully local (fast to run, weak judge — for smoke only):
  ./.venv/bin/python scripts/eval_locomo_qa.py /tmp/locomo10.json \\
      --gen local --judge local --limit 1
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_SCRIPTS.parent))

from eval_locomo import _ingest  # reuse the exact ingestion path
from src.app.search_combo import combined_search
from src.memory.embedder import Embedder
from src.memory.reranker import rerank_scores

# LoCoMo category 5 is adversarial (the answer is not in the conversation; a good
# system abstains). Scored separately from the answerable J score.
_ADVERSARIAL_CAT = 5


_GEN_PROMPT = """You answer a question using ONLY the conversation excerpts below. Be concise: a name, date, phrase, or one short sentence. If the answer is not in the excerpts, reply exactly: I don't know.

Conversation excerpts:
{context}

Question: {question}
Answer:"""

_JUDGE_PROMPT = """You are grading a predicted answer against the reference answer for a question. They match if the predicted answer conveys the same fact as the reference, even if worded differently or more verbosely. Ignore style, extra words, and formatting.

Question: {question}
Reference answer: {gold}
Predicted answer: {pred}

Reply with exactly one word: CORRECT or WRONG."""


def generate_answer(question: str, contexts: list[str], gen_fn) -> str:
    """Produce an answer from retrieved memory texts. ``gen_fn(prompt,
    max_tokens) -> str`` is injected so this is testable without a model."""
    ctx = "\n".join(f"- {c}" for c in contexts if c.strip())
    if not ctx:
        return "I don't know"
    prompt = _GEN_PROMPT.format(context=ctx[:6000], question=question)
    out = (gen_fn(prompt, 96) or "").strip()
    return out or "I don't know"


def judge_answer(question: str, gold: str, pred: str, judge_fn) -> bool:
    """True if ``pred`` matches ``gold`` per the LLM judge. ``judge_fn`` is
    injected. A blank/abstention prediction is never CORRECT for an answerable
    question, so we short-circuit it without spending a judge call."""
    p = (pred or "").strip().lower()
    if not p or p in ("i don't know", "i dont know", "unknown"):
        return False
    verdict = (judge_fn(
        _JUDGE_PROMPT.format(question=question, gold=gold, pred=pred), 4
    ) or "").strip().lower()
    return verdict.startswith("correct")


def is_abstention(pred: str) -> bool:
    p = (pred or "").strip().lower()
    return (not p) or p.startswith(("i don't know", "i dont know", "unknown"))


# ── Provider plumbing ─────────────────────────────────────────────────────────

def make_llm(provider: str):
    p = provider.lower()
    if p == "local":
        from src.ai import vision
        vision.configure_vision()
        if not vision.load_vision_sync(timeout=240):
            raise SystemExit("local VL model failed to load")
        return lambda prompt, max_tokens=96: vision.infer_text(prompt, max_tokens=max_tokens)
    if p == "openrouter":
        from src.ai.remote_llm import openrouter_chat, is_remote_active, load_remote_config
        cfg = load_remote_config()
        if not (cfg.get("openrouter_api_key") or "").strip():
            raise SystemExit(
                "openrouter selected but no key in ~/.corenous/remote.json. "
                "Set provider/openrouter_api_key/openrouter_model there first."
            )
        return lambda prompt, max_tokens=96: openrouter_chat(prompt, max_tokens=max_tokens, temperature=0.0)
    raise SystemExit(f"unknown provider: {provider} (use local or openrouter)")


def evaluate(path: Path, *, gen: str, judge: str, k: int, window: int,
             cross: bool, limit: int | None) -> None:
    data = json.loads(path.read_text())
    if limit:
        data = data[:limit]
    emb = Embedder()
    rfn = rerank_scores if cross else None
    gen_fn = make_llm(gen)
    judge_fn = gen_fn if judge == gen else make_llm(judge)

    print(f"LoCoMo QA (J score): {len(data)} conversations, top_k={k}, "
          f"window={window}, cross_encoder={cross}, gen={gen}, judge={judge}\n",
          flush=True)

    correct = total = 0
    by_cat: dict[int, list[float]] = defaultdict(list)
    adv_correct = adv_total = 0

    for si, sample in enumerate(data):
        with tempfile.TemporaryDirectory() as d:
            t0 = time.perf_counter()
            store, cache, _ = _ingest(sample, emb, Path(d) / "m.db", window)
            for q in sample.get("qa", []):
                question = str(q.get("question") or "")
                gold = str(q.get("answer") if q.get("answer") is not None else "")
                cat = int(q.get("category") or 0)
                results = combined_search(question, store, cache, emb, top_k=k, rerank_fn=rfn)
                contexts = [r.full_text or r.text_snippet for r in results]
                pred = generate_answer(question, contexts, gen_fn)

                if cat == _ADVERSARIAL_CAT:
                    adv_total += 1
                    adv_correct += 1 if is_abstention(pred) else 0
                    continue
                if not gold:
                    continue
                ok = judge_answer(question, gold, pred, judge_fn)
                correct += 1 if ok else 0
                total += 1
                by_cat[cat].append(1.0 if ok else 0.0)
            print(f"  [{si+1}/{len(data)}] answered {total} ({time.perf_counter()-t0:.1f}s)",
                  flush=True)

    print("\n" + "=" * 66)
    print("corenous LoCoMo ANSWER ACCURACY (J score)")
    print("=" * 66)
    j = (correct / total * 100.0) if total else 0.0
    print(f"  answerable questions: {total}")
    print(f"  J score (judged correct): {j:.1f}%")
    if by_cat:
        print("  by category:")
        names = {1: "single-hop", 2: "multi-hop", 3: "temporal", 4: "open-domain"}
        for c in sorted(by_cat):
            hits = by_cat[c]
            print(f"    {names.get(c, str(c)):<14} {np.mean(hits)*100:5.1f}%  (n={len(hits)})")
    if adv_total:
        print(f"  adversarial abstention: {adv_correct/adv_total*100:.1f}%  (n={adv_total})")
    print("=" * 66)
    print("J score is end-to-end answer accuracy, comparable to Mem0/Zep/supermemory")
    print("LoCoMo numbers ONLY when judged by a comparable (GPT-4-class) model.")
    print(f"This run judged with: {judge}.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", nargs="?", default="/tmp/locomo10.json")
    ap.add_argument("--gen", default="local", choices=["local", "openrouter"])
    ap.add_argument("--judge", default="local", choices=["local", "openrouter"])
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--window", type=int, default=3)
    ap.add_argument("--no-cross", action="store_true", help="disable cross-encoder rerank")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    p = Path(args.dataset)
    if not p.is_file():
        sys.exit(f"LoCoMo dataset not found at {p}. See the header for the download command.")
    evaluate(p, gen=args.gen, judge=args.judge, k=args.k, window=args.window,
             cross=not args.no_cross, limit=args.limit)
