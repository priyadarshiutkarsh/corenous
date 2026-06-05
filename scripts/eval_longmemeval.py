#!/usr/bin/env python3
"""Retrieval evaluation of corenous on LongMemEval (the benchmark MemPalace and
supermemory report on), so we can compare on a shared yardstick instead of
guessing.

This measures RETRIEVAL: for each question, ingest its full haystack of sessions
as memories, run the production search, and score whether a retrieved memory
belongs to a gold answer session (session-level recall@k, MRR@k). It is NOT the
end-to-end answer accuracy (QA) those projects also report; that needs an
answer-generation model plus a judge and is a separate evaluation.

Honest caveats baked in:
  - Uses the longmemeval_s split (full haystack), the comparable setting. The
    oracle split is trivially easy (a couple of sessions) and not comparable.
  - Memories are ingested per turn and scored at session granularity. Other
    setups chunk differently, so treat this as indicative, not a precise head to
    head.
  - Run a subset for a fast read; the full 500 questions is a long CPU run.

Dataset (no .json extension on the hub):
  curl -L -o /tmp/longmemeval_s.json \\
    "https://huggingface.co/datasets/xiaowu0162/longmemeval/resolve/main/longmemeval_s?download=true"

Run:  ./.venv/bin/python scripts/eval_longmemeval.py [/path/to/longmemeval_s.json] [limit]
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.memory.store import MemoryStore
from src.memory.vector_cache import VectorCache
from src.memory.embedder import Embedder
from src.app.search_combo import combined_search
from src.memory.reranker import rerank_scores
from src.turboquant import encoder as tq


def _ingest(sample: dict, emb: Embedder, db_path: Path) -> tuple[MemoryStore, VectorCache, dict]:
    """Ingest every haystack turn as a memory; map memory id -> session id."""
    store = MemoryStore(db_path)
    cache = VectorCache(db_path.with_suffix(".npy"))
    sessions = sample["haystack_sessions"]
    sess_ids = sample["haystack_session_ids"]
    texts, sids = [], []
    for sid, turns in zip(sess_ids, sessions):
        for t in turns:
            content = (t.get("content") or "").strip()
            if content:
                texts.append(f'{t.get("role","")}: {content}')
                sids.append(sid)
    if not texts:
        return store, cache, {}
    vecs = emb.embed_batch(texts).astype(np.float32)
    mid2sid: dict[int, str] = {}
    for sid, txt, v in zip(sids, texts, vecs):
        cv = tq.encode(v)
        mid = store.insert_memory(
            txt, "lme", "Chat", cv, cv.residual_norm,
            dedup_window=0.0, window_title=sid,
            fp16=v.astype(np.float16).tobytes(),
        )
        if mid is not None:
            mid2sid[mid] = sid
    cache.load_from_store(store.get_all_compressed_vectors())
    return store, cache, mid2sid


def evaluate(path: Path, k: int = 10, limit: int | None = None, cross: bool = False,
             cat: str = "") -> None:
    from collections import defaultdict
    data = json.loads(path.read_text())
    if cat:
        data = [s for s in data if cat in (s.get("question_type") or "")]
    if limit:
        data = data[:limit]
    emb = Embedder()
    rfn = rerank_scores if cross else None
    print(f"LongMemEval_s retrieval eval: {len(data)} questions, top_k={k}, "
          f"cross_encoder={cross}, category_filter={cat or 'all'}\n", flush=True)

    recall5: list[float] = []
    recall_k: list[float] = []
    recall_all_k: list[float] = []   # ALL gold sessions in top-k (strict, multi-session)
    rr: list[float] = []
    by_cat: dict[str, list] = defaultdict(list)   # question_type -> [recall@5 hits]
    for i, sample in enumerate(data):
        with tempfile.TemporaryDirectory() as d:
            t0 = time.perf_counter()
            store, cache, mid2sid = _ingest(sample, emb, Path(d) / "m.db")
            gold = set(sample.get("answer_session_ids") or [])
            results = combined_search(str(sample["question"]), store, cache, emb, top_k=k, rerank_fn=rfn)
            ranked = [mid2sid.get(r.memory_id) for r in results]
            topk = set(ranked[:k])
            hit5 = 1.0 if any(s in gold for s in ranked[:5]) else 0.0
            recall5.append(hit5)
            recall_k.append(1.0 if (gold & topk) else 0.0)
            recall_all_k.append(1.0 if (gold and gold.issubset(topk)) else 0.0)
            r = 0.0
            for j, sid in enumerate(ranked[:k]):
                if sid in gold:
                    r = 1.0 / (j + 1)
                    break
            rr.append(r)
            by_cat[sample.get("question_type") or "?"].append(hit5)
            if (i + 1) % 10 == 0 or i == 0:
                print(f"  [{i+1}/{len(data)}] {len(mid2sid)} memories "
                      f"({time.perf_counter()-t0:.1f}s)", flush=True)

    print("\n" + "=" * 66)
    print("corenous retrieval on LongMemEval_s  (RETRIEVAL, session-level, not QA)")
    print("=" * 66)
    print(f"  questions: {len(recall5)}")
    print(f"  Recall@{k} (ANY gold session)  = {np.mean(recall_k)*100:.1f}%")
    print(f"  Recall@{k} (ALL gold sessions) = {np.mean(recall_all_k)*100:.1f}%  <- strict, multi-session")
    print(f"  Recall@5  = {np.mean(recall5)*100:.1f}%")
    print(f"  Recall@{k} = {np.mean(recall_k)*100:.1f}%")
    print(f"  MRR@{k}    = {np.mean(rr):.3f}")
    print("  by question type (Recall@5):")
    for ct in sorted(by_cat):
        hits = by_cat[ct]
        print(f"    {ct:<28} {np.mean(hits)*100:5.1f}%  (n={len(hits)})")
    print("=" * 66)
    print("Indicative, not a precise head-to-head: turn-level memories scored at")
    print("session granularity, and this is retrieval recall, not the answer-QA P@1")
    print("that MemPalace (96.6% R@5) and supermemory (81.6% LongMemEval) report.")


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/longmemeval_s.json")
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    cross = bool(int(sys.argv[3])) if len(sys.argv) > 3 else False
    cat = sys.argv[4] if len(sys.argv) > 4 else ""
    if not p.is_file():
        sys.exit(f"LongMemEval not found at {p}. See the header for the download command.")
    evaluate(p, limit=limit, cross=cross, cat=cat)
