#!/usr/bin/env python3
"""Retrieval evaluation of corenous on the LoCoMo benchmark.

This measures whether corenous's search surfaces the gold *evidence turns* for
each LoCoMo question: retrieval quality (recall@k, MRR@k). It is NOT the
end-to-end answer accuracy (P@1) that hosted systems like supermemory report.
That number needs an answer-generation model plus an LLM judge and is a separate
evaluation. Conflating the two is exactly the apples-to-oranges trap we avoid.

Each LoCoMo conversation is ingested into its OWN fresh corenous store (turns
become memories), then every question with gold evidence is run through the real
production retrieval path (combined_search: TurboQuant coarse + fp16 re-rank +
FTS + metadata fusion). A question scores a hit if any gold evidence turn lands
in the top-k results.

Dataset (Maharana et al. 2024), 10 conversations, 1986 QA pairs:
  curl -L -o /tmp/locomo10.json \\
    https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

Run:  ./.venv/bin/python scripts/eval_locomo.py [/path/to/locomo10.json]
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
from src.memory.embed_context import build_embed_text
from src.turboquant import encoder as tq


def _session_keys(conv: dict) -> list[str]:
    keys = [k for k in conv if k.startswith("session") and "date_time" not in k]
    return sorted(keys, key=lambda k: int(k.split("_")[1]))


def _chunks(conv: dict, window: int) -> list[tuple[frozenset, str, str]]:
    """One memory per turn, mapped to that turn's evidence id.

    Returns (dia set, store_text, embed_text). ``window=1`` is the bare-turn
    baseline (store == embed). ``window>1`` is the CAUSAL context envelope: the
    stored text stays the bare turn (so FTS behaves like production), but the
    EMBED text is wrapped with the session date and the preceding ``window-1``
    turns. Only preceding turns are used, because at capture time the future does
    not exist yet. This mirrors the production transform faithfully."""
    out: list[tuple[frozenset, str, str]] = []
    for k in _session_keys(conv):
        date = conv.get(f"{k}_date_time", "")
        turns = [
            (t["dia_id"], f'{t.get("speaker","")}: {t.get("text","")}'.strip())
            for t in conv[k] if t.get("text")
        ]
        for i, (dia, tx) in enumerate(turns):
            if window <= 1:
                embed_text = tx
            else:
                prev = [turns[j][1] for j in range(max(0, i - (window - 1)), i)]
                embed_text = build_embed_text(tx, [date] + prev)
            out.append((frozenset([dia]), tx, embed_text))
    return out


def _ingest(sample: dict, emb: Embedder, db_path: Path, window: int) -> tuple[MemoryStore, VectorCache, dict]:
    store = MemoryStore(db_path)
    cache = VectorCache(db_path.with_suffix(".npy"))
    chunks = _chunks(sample["conversation"], window)
    if not chunks:
        return store, cache, {}
    vecs = emb.embed_batch([embed_text for _, _, embed_text in chunks]).astype(np.float32)
    mid2dias: dict[int, frozenset] = {}
    for (dias, store_text, _), v in zip(chunks, vecs):
        cv = tq.encode(v)
        mid = store.insert_memory(
            store_text, "locomo", "Chat", cv, cv.residual_norm,
            dedup_window=0.0, window_title=";".join(sorted(dias)),
            fp16=v.astype(np.float16).tobytes(),
        )
        if mid is not None:
            mid2dias[mid] = dias
    cache.load_from_store(store.get_all_compressed_vectors())
    return store, cache, mid2dias


def evaluate(path: Path, k: int = 10, window: int = 1, limit: int | None = None) -> None:
    data = json.loads(path.read_text())
    if limit:
        data = data[:limit]
    emb = Embedder()
    print(f"LoCoMo retrieval eval: {len(data)} conversations, "
          f"top_k={k}, chunk window={window}\n", flush=True)

    recall5: list[float] = []
    recall_k: list[float] = []
    rr: list[float] = []
    scored = skipped_no_evidence = 0

    for si, sample in enumerate(data):
        with tempfile.TemporaryDirectory() as d:
            t0 = time.perf_counter()
            store, cache, mid2dias = _ingest(sample, emb, Path(d) / "m.db", window)
            qas = sample.get("qa", [])
            n_ev = 0
            for q in qas:
                gold = set(q.get("evidence") or [])
                if not gold:
                    skipped_no_evidence += 1
                    continue
                n_ev += 1
                results = combined_search(str(q["question"]), store, cache, emb, top_k=k)
                ranked = [mid2dias.get(r.memory_id, frozenset()) for r in results]
                recall5.append(1.0 if any(gold & dias for dias in ranked[:5]) else 0.0)
                recall_k.append(1.0 if any(gold & dias for dias in ranked[:k]) else 0.0)
                r = 0.0
                for i, dias in enumerate(ranked[:k]):
                    if gold & dias:
                        r = 1.0 / (i + 1)
                        break
                rr.append(r)
                scored += 1
            print(f"  [{si+1}/{len(data)}] {len(mid2dias)} memories, {n_ev} evidence questions "
                  f"({time.perf_counter()-t0:.1f}s)", flush=True)

    print("\n" + "=" * 66)
    print(f"corenous retrieval on LoCoMo  (RETRIEVAL, not answer P@1)  window={window}")
    print("=" * 66)
    print(f"  scored questions (with gold evidence): {scored}")
    print(f"  skipped (adversarial / no evidence):   {skipped_no_evidence}")
    print(f"  Recall@5  = {np.mean(recall5)*100:.1f}%")
    print(f"  Recall@{k} = {np.mean(recall_k)*100:.1f}%")
    print(f"  MRR@{k}    = {np.mean(rr):.3f}")
    print("=" * 66)
    print("Note: this is whether the gold evidence turn was retrieved, NOT whether")
    print("a generated answer was correct. supermemory's 59.7% P@1 is the latter and")
    print("is not directly comparable without an answer-generation + judge pipeline.")


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/locomo10.json")
    window = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else None
    if not p.is_file():
        sys.exit(f"LoCoMo dataset not found at {p}. See the header for the download command.")
    evaluate(p, window=window, limit=limit)
