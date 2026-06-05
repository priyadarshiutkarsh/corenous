#!/usr/bin/env python3
"""Embedding bake-off on LongMemEval_s.

Compares embedding models on pure dense retrieval (exact float32 cosine, no
TurboQuant, no FTS, no rerank) so the number reflects the EMBEDDING MODEL alone.
This is directly comparable to MemPalace's "96.6% raw semantic, no heuristics,
no LLM". The point is to pick the best embedding empirically BEFORE paying for
any TurboQuant dimension retune.

Each model uses its own recommended query/document prompting so none is
handicapped. Models that fail to load (e.g. gated on the hub) are skipped.

Run:  ./.venv/bin/python scripts/embed_bakeoff.py [/path/to/longmemeval_s.json] [limit]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

MODELS = [
    {"label": "all-MiniLM-L6-v2 (current)", "name": "sentence-transformers/all-MiniLM-L6-v2"},
    {"label": "bge-small-en-v1.5", "name": "BAAI/bge-small-en-v1.5",
     "q_prefix": "Represent this sentence for searching relevant passages: "},
    {"label": "embeddinggemma-300m (MemPalace)", "name": "google/embeddinggemma-300m",
     "use_prompt_name": True},
]


def _encode(model, cfg, texts, kind):
    """kind is 'query' or 'document'. Use the model's recommended prompting."""
    if cfg.get("use_prompt_name"):
        try:
            return model.encode(
                texts, prompt_name=("query" if kind == "query" else "document"),
                normalize_embeddings=True, show_progress_bar=False, batch_size=64,
            ).astype(np.float32)
        except Exception:
            pass
    if kind == "query" and cfg.get("q_prefix"):
        texts = [cfg["q_prefix"] + t for t in texts]
    return model.encode(
        texts, normalize_embeddings=True, show_progress_bar=False, batch_size=64,
    ).astype(np.float32)


def _eval_model(cfg, data, k=10):
    from sentence_transformers import SentenceTransformer
    # Pin to CPU: on MPS the bigger models stall on a Metal command-buffer
    # timeout (the same failure that bit the daemon). CPU is slower but reliable.
    model = SentenceTransformer(cfg["name"], device="cpu")
    dim = model.get_sentence_embedding_dimension()
    r5, rk, rr = [], [], []
    t0 = time.perf_counter()
    for sample in data:
        sess_ids = sample["haystack_session_ids"]
        sids, texts = [], []
        for sid, turns in zip(sess_ids, sample["haystack_sessions"]):
            for t in turns:
                c = (t.get("content") or "").strip()
                if c:
                    sids.append(sid)
                    texts.append(f'{t.get("role","")}: {c}')
        if not texts:
            continue
        X = _encode(model, cfg, texts, "document")        # (N, d)
        q = _encode(model, cfg, [str(sample["question"])], "query")[0]
        order = np.argsort(X @ q)[::-1]
        ranked = [sids[int(i)] for i in order[:k]]
        gold = set(sample.get("answer_session_ids") or [])
        r5.append(1.0 if any(s in gold for s in ranked[:5]) else 0.0)
        rk.append(1.0 if any(s in gold for s in ranked[:k]) else 0.0)
        rr.append(next((1.0 / (j + 1) for j, s in enumerate(ranked) if s in gold), 0.0))
    return {
        "label": cfg["label"], "dim": dim, "n": len(r5),
        "r5": float(np.mean(r5)), "rk": float(np.mean(rk)), "mrr": float(np.mean(rr)),
        "secs": time.perf_counter() - t0,
    }


def main():
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/longmemeval_s.json")
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    if not p.is_file():
        sys.exit(f"LongMemEval not found at {p}.")
    data = json.loads(p.read_text())[:limit]
    print(f"Embedding bake-off: pure dense exact retrieval, {len(data)} questions, "
          f"session-level recall\n", flush=True)
    rows = []
    for cfg in MODELS:
        try:
            print(f"[loading] {cfg['label']} ...", flush=True)
            rows.append(_eval_model(cfg, data))
            r = rows[-1]
            print(f"  {r['label']}: dim={r['dim']}  R@5={r['r5']*100:.1f}%  "
                  f"R@10={r['rk']*100:.1f}%  MRR={r['mrr']:.3f}  ({r['secs']:.0f}s)",
                  flush=True)
        except Exception as e:
            print(f"  SKIPPED {cfg['label']}: {type(e).__name__}: {str(e)[:120]}", flush=True)

    print("\n" + "=" * 70)
    print(f"EMBEDDING BAKE-OFF  (LongMemEval_s, {len(data)} q, pure dense exact float32)")
    print("=" * 70)
    print(f"  {'model':<34} {'dim':>4} {'R@5':>7} {'R@10':>7} {'MRR':>7}")
    for r in rows:
        print(f"  {r['label']:<34} {r['dim']:>4} {r['r5']*100:>6.1f}% {r['rk']*100:>6.1f}% {r['mrr']:>7.3f}")
    print("=" * 70)
    print("Pure embedding quality (no TurboQuant, no FTS, no rerank).")
    print("Comparable to MemPalace's 96.6% raw semantic.")


if __name__ == "__main__":
    main()
