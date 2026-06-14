#!/usr/bin/env python3
"""Honest retrieval benchmark for corenous.

Measures the numbers the landing page claims against what the code actually
does. Nothing here is hand tuned to hit a target; it reports what it sees.

Two metrics, both reproducible on this machine:

  Recall@10 and MRR
    TurboQuant compressed search vs EXACT float32 nearest neighbour. Exact NN
    is the ground truth (the question is "does the 58 byte compression return
    the same neighbours as the full 384 dim float32 vector"), so no human
    relevance labels are needed. Run on REAL all-MiniLM-L6-v2 embeddings.

  Latency
    VectorCache.scores in coarse mode (the production dense ranker: an N x 384
    Stage 1 matmul, QJL correction skipped) vs an exact float32 numpy brute
    force baseline, scaled up toward 1,000,000 vectors. Latency is value
    independent, so large N uses tiled vectors to avoid a million Python encodes.

FAISS / Chroma are not installed, so the only competitor baseline reported is
exact float32 numpy brute force, clearly labelled. We do not invent numbers for
unnamed "Vector DB A / B".

Run:  ./.venv/bin/python scripts/benchmark.py
"""
from __future__ import annotations

import gc
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.memory.embedder import Embedder
from src.memory.vector_cache import VectorCache
from src.turboquant import encoder as tq
from src.turboquant.encoder import batch_decode_angles
from src.turboquant import qjl


# ── corpus generation (real text -> real embeddings) ───────────────────────────

_SUBJECTS = [
    "the scaling laws paper", "the embedding benchmark", "the quarterly budget",
    "the onboarding doc", "the incident postmortem", "the API rate limiter",
    "the vector index", "the privacy policy", "the migration script",
    "the design review", "the customer interview", "the kubernetes cluster",
    "the model checkpoint", "the dataset card", "the pricing page",
    "the auth flow", "the cache layer", "the retrieval pipeline",
    "the quantization scheme", "the load test", "the GPU memory profile",
    "the release notes", "the security audit", "the feature flag rollout",
]
_PREDICATES = [
    "showed a clear regression after the latest change",
    "needs review before the Tuesday deadline",
    "was discussed at length in the standup",
    "improved recall without hurting latency",
    "introduced a subtle off by one in the ranking",
    "cut memory use by more than half",
    "failed under sustained concurrent load",
    "matched the numbers from the earlier prototype",
    "depends on the upstream embedding model",
    "was cited in the arxiv 2401.04088 reference",
    "broke when the input exceeded the token limit",
    "is blocked on the data team's export",
    "passed every test except the flaky one",
    "raised questions about the dedup precision",
    "looked fine on M2 but slow on the older Intel box",
]


_ENTITIES = [
    "Ajay", "Priya", "the Helsinki team", "Q3", "the Frankfurt office",
    "Maria", "the Tokyo cluster", "Wei", "the v2 branch", "the Berlin pilot",
    "Sora", "the Austin lab", "Diego", "the Oslo rollout", "Lin",
]


def build_corpus(n_corpus: int, n_query: int, seed: int = 7) -> tuple[list[str], list[str]]:
    """Each document is three distinct clauses plus a named entity, so the
    embeddings spread out instead of collapsing into near-duplicate clusters.
    With 24 subjects x 15 predicates x 15 entities composed three at a time the
    combination space is far larger than the corpus, so the exact top-10 for a
    query are genuinely distinct documents (a fair recall@10 test)."""
    rng = np.random.default_rng(seed)

    def clause() -> str:
        return f"{_SUBJECTS[rng.integers(len(_SUBJECTS))]} {_PREDICATES[rng.integers(len(_PREDICATES))]}"

    texts: set[str] = set()
    while len(texts) < n_corpus + n_query:
        ent = _ENTITIES[rng.integers(len(_ENTITIES))]
        doc = f"{clause()}. {clause()}. According to {ent}, {clause()}."
        texts.add(doc)
    pool = list(texts)
    rng.shuffle(pool)
    return pool[:n_corpus], pool[n_corpus:n_corpus + n_query]


# ── recall / MRR ───────────────────────────────────────────────────────────────

def _perturb(doc: str, rng) -> str:
    """Make a query that is similar to ``doc`` but not identical: drop one of
    its three clauses. Mirrors real use, where the query resembles (but does not
    equal) a stored memory."""
    clauses = [c for c in doc.split(". ") if c]
    if len(clauses) > 1:
        drop = int(rng.integers(len(clauses)))
        clauses = [c for i, c in enumerate(clauses) if i != drop]
    return ". ".join(clauses)


def measure_recall(n_corpus: int = 10_000, n_query: int = 500, k: int = 10) -> dict:
    """Known-item retrieval. Each query is a perturbed copy of one corpus doc,
    so that doc is the known relevant target. We report two numbers:

      exact float32 recall@10  -> ceiling set by the embedding model itself
      TurboQuant     recall@10  -> what the 58-byte compression actually delivers

    The gap between them is the pure quantization loss, which is the thing the
    landing page's Recall@10 claim is really about.
    """
    print(f"\n[recall] embedding {n_corpus} corpus texts (known-item queries) "
          f"with all-MiniLM-L6-v2 ...", flush=True)
    emb = Embedder()
    corpus_txt, _ = build_corpus(n_corpus, 0)

    t0 = time.perf_counter()
    X = emb.embed_batch(corpus_txt).astype(np.float32)          # (N, 384)

    rng = np.random.default_rng(99)
    target_ids = rng.choice(n_corpus, size=n_query, replace=False)
    query_txt = [_perturb(corpus_txt[int(t)], rng) for t in target_ids]
    Q = emb.embed_batch(query_txt).astype(np.float32)           # (Qn, 384)
    print(f"[recall] embedded in {time.perf_counter() - t0:.1f}s", flush=True)

    cache = VectorCache(Path("/tmp/_bench_cache"))
    cvs = [tq.encode(X[i]) for i in range(n_corpus)]
    cache.load_from_store([(i, cvs[i], cvs[i].residual_norm) for i in range(n_corpus)])

    rerank_ns = [50, 100, 200]
    exact_hit: list[float] = []
    approx_hit: list[float] = []
    overlap_recall: list[float] = []
    rrs: list[float] = []
    exact_rrs: list[float] = []
    coarse_hit = {n: [] for n in rerank_ns}     # target within TurboQuant top-N
    rerank_hit = {n: [] for n in rerank_ns}     # target in top-10 after exact re-rank of top-N
    rerank_rr = {n: [] for n in rerank_ns}      # reciprocal rank of target after re-rank
    for qi in range(n_query):
        q = Q[qi]
        target = int(target_ids[qi])

        exact = X @ q
        exact_order = np.argsort(exact)[::-1]
        exact_top = set(exact_order[:k].tolist())
        exact_hit.append(1.0 if target in exact_top else 0.0)
        exact_rrs.append(1.0 / (int(np.where(exact_order == target)[0][0]) + 1))

        approx = cache.scores(tq.encode(q), coarse=True)
        approx_order = np.argsort(approx)[::-1]
        approx_top = set(approx_order[:k].tolist())
        approx_hit.append(1.0 if target in approx_top else 0.0)

        overlap_recall.append(len(exact_top & approx_top) / k)
        rank = int(np.where(approx_order == target)[0][0]) + 1
        rrs.append(1.0 / rank)

        # Re-rank: TurboQuant coarse top-N, then exact re-score (production path).
        for n in rerank_ns:
            cand = approx_order[:n]
            coarse_hit[n].append(1.0 if target in cand.tolist() else 0.0)
            rer_order = cand[np.argsort(X[cand] @ q)[::-1]]
            rerank_hit[n].append(1.0 if target in set(rer_order[:k].tolist()) else 0.0)
            where = np.where(rer_order == target)[0]
            rerank_rr[n].append(1.0 / (int(where[0]) + 1) if len(where) else 0.0)

    return {
        "n_corpus": n_corpus,
        "n_query": n_query,
        "exact_recall_at_10": float(np.mean(exact_hit)),
        "recall_at_10": float(np.mean(approx_hit)),
        "quant_fidelity_at_10": float(np.mean(overlap_recall)),
        "mrr_coarse": float(np.mean(rrs)),
        "mrr": float(np.mean(rerank_rr[max(rerank_ns)])),   # production: re-ranked
        "mrr_exact_ceiling": float(np.mean(exact_rrs)),
        "coarse_recall": {n: float(np.mean(coarse_hit[n])) for n in rerank_ns},
        "rerank_recall_at_10": {n: float(np.mean(rerank_hit[n])) for n in rerank_ns},
    }


# ── latency ─────────────────────────────────────────────────────────────────────

def _percentile_ms(fn, trials: int = 40, warmup: int = 3) -> tuple[float, float]:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(trials):
        t = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t) * 1000.0)
    s = np.array(samples)
    return float(np.percentile(s, 50)), float(np.percentile(s, 95))


def _make_cache_at(n: int, base_stage1: np.ndarray, base_signs: np.ndarray,
                   base_norms: np.ndarray) -> VectorCache:
    """Build a VectorCache populated to size n by tiling a real base set.

    Latency is value independent (it is a fixed shape matmul plus sign ops),
    so tiling avoids a million Python encodes while exercising the exact
    scores() code path on full size arrays.
    """
    reps = (n + base_stage1.shape[0] - 1) // base_stage1.shape[0]
    stage1 = np.tile(base_stage1, (reps, 1))[:n].astype(np.float32)
    signs = np.tile(base_signs, (reps, 1))[:n]
    norms = np.tile(base_norms, reps)[:n].astype(np.float32)
    c = VectorCache(Path("/tmp/_bench_cache_lat"))
    c._memory_ids = list(range(n))
    # Match the production int8 index layout (per-row max-abs quantization).
    scale = np.max(np.abs(stage1), axis=1) / 127.0
    scale[scale == 0.0] = 1.0
    c._stage1_q8 = np.round(stage1 / scale[:, None]).astype(np.int8)
    c._stage1_scale = scale.astype(np.float32)
    c._qjl_signs = signs
    c._residual_norms_np = norms
    c._residual_norms = norms.tolist()
    return c


def measure_latency(sizes: list[int]) -> list[dict]:
    rng = np.random.default_rng(11)
    base_n = 4096
    base_vecs = rng.standard_normal((base_n, 384)).astype(np.float32)
    base_vecs /= np.linalg.norm(base_vecs, axis=1, keepdims=True)
    base_cvs = [tq.encode(base_vecs[i]) for i in range(base_n)]
    base_stage1 = batch_decode_angles(base_cvs)
    base_signs = np.vstack([qjl.unpack_signs(cv.qjl_bits) for cv in base_cvs])
    base_norms = np.array([cv.residual_norm for cv in base_cvs], dtype=np.float32)

    qv = base_vecs[0]
    q_cv = tq.encode(qv)

    rows = []
    for n in sizes:
        try:
            cache = _make_cache_at(n, base_stage1, base_signs, base_norms)
            tq_p50, tq_p95 = _percentile_ms(lambda: cache.scores(q_cv, coarse=True))

            Xf = np.tile(base_vecs, ((n + base_n - 1) // base_n, 1))[:n].astype(np.float32)
            bf_p50, bf_p95 = _percentile_ms(lambda: Xf @ qv)

            stage1_mb = cache.index_bytes() / 1e6
            rows.append({
                "n": n,
                "turboquant_p50_ms": tq_p50, "turboquant_p95_ms": tq_p95,
                "exact_float32_p50_ms": bf_p50, "exact_float32_p95_ms": bf_p95,
                "compressed_mb": n * 58 / 1e6,
                "cache_resident_mb": stage1_mb,
            })
            print(f"[latency] N={n:>9,}  TurboQuant p50 {tq_p50:7.2f} ms | "
                  f"exact f32 p50 {bf_p50:7.2f} ms | "
                  f"compressed {n*58/1e6:6.1f} MB, resident {stage1_mb:6.0f} MB",
                  flush=True)
            del cache, Xf
            gc.collect()
        except MemoryError:
            print(f"[latency] N={n:,} ran out of memory on this machine, stopping.",
                  flush=True)
            break
    return rows


def main() -> None:
    print("=" * 74)
    print("corenous retrieval benchmark  (machine:", end=" ")
    import platform
    print(f"{platform.machine()}, {platform.platform()})")
    print("=" * 74)

    rec = measure_recall(n_corpus=10_000, n_query=500, k=10)
    print(f"\n[recall] over {rec['n_corpus']:,} real embeddings, {rec['n_query']} known-item queries")
    print(f"         exact float32 recall@10 = {rec['exact_recall_at_10']*100:.1f}%  (embedding ceiling)")
    print(f"         TurboQuant    recall@10 = {rec['recall_at_10']*100:.1f}%  (what compression delivers)")
    print(f"         quant fidelity@10        = {rec['quant_fidelity_at_10']*100:.1f}%  (approx vs exact top-10 overlap)")
    print(f"         MRR (re-ranked, production) = {rec['mrr']:.3f}  "
          f"(coarse only {rec['mrr_coarse']:.3f}, exact ceiling {rec['mrr_exact_ceiling']:.3f})")
    print(f"\n[rerank] coarse TurboQuant top-N, then exact re-score of those N:")
    for n in rec["coarse_recall"]:
        print(f"         N={n:>3}: coarse recall@{n} = {rec['coarse_recall'][n]*100:5.1f}%   "
              f"-> re-ranked recall@10 = {rec['rerank_recall_at_10'][n]*100:5.1f}%")
    print(f"         (target to beat: exact ceiling {rec['exact_recall_at_10']*100:.1f}%, "
          f"current no-rerank {rec['recall_at_10']*100:.1f}%)")

    if len(sys.argv) > 1 and sys.argv[1] == "recall":
        return

    lat = measure_latency([10_000, 100_000, 500_000, 1_000_000])

    print("\n" + "=" * 74)
    print("SUMMARY vs landing page claims")
    print("=" * 74)
    print(f"  Recall@10   claimed 94.2%   measured {rec['recall_at_10']*100:.1f}% "
          f"(exact float32 ceiling is {rec['exact_recall_at_10']*100:.1f}%)")
    print(f"  MRR         claimed 0.91    measured {rec['mrr']:.3f} "
          f"(re-ranked; exact float32 ceiling is {rec['mrr_exact_ceiling']:.3f})")
    if lat:
        big = lat[-1]
        print(f"  Recall time claimed 8 ms    measured {big['turboquant_p50_ms']:.1f} ms "
              f"(TurboQuant) at N={big['n']:,}")
        print(f"              vs exact f32 numpy {big['exact_float32_p50_ms']:.1f} ms "
              f"at the same N  (the only honest baseline; FAISS not installed)")
    print(f"  Dedup 99.4% : NOT measured here (needs a labelled duplicate set)")
    print("=" * 74)


if __name__ == "__main__":
    main()
