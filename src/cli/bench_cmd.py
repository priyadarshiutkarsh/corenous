"""`corenous-ai bench` — reproducible search-latency measurement.

Times the real production search path (combined_search: int8 TurboQuant coarse
+ fp16 re-rank + FTS + metadata fusion + cross-encoder) over the actual local
store, and reports p50/p95/p99 plus the resident index size. This is the
"X ms across N memories, on CPU, no cloud" number for the pitch — and anyone
can rerun it on their own machine.
"""
from __future__ import annotations

import time

import click

from .context import AppContext


@click.command()
@click.option("--queries", "n_queries", default=50, show_default=True,
              help="Number of timed search queries")
@click.option("--top-k", default=12, show_default=True)
@click.option("--no-rerank", is_flag=True, help="Skip the cross-encoder rerank")
@click.pass_context
def bench_cmd(ctx: click.Context, n_queries: int, top_k: int, no_rerank: bool) -> None:
    """Benchmark search latency over your real memory store."""
    app: AppContext = ctx.obj["app"]
    from ..memory.embedder import Embedder
    from ..app.search_combo import combined_search

    store, cache = app.store, app.cache
    n_mem = len(cache)
    if n_mem == 0:
        click.echo("No memories to benchmark. Capture some first.")
        return

    # Draw realistic queries from stored headings/snippets so the search does
    # real work instead of matching nothing.
    rows = store.get_recent(limit=max(n_queries * 2, 100))
    pool = []
    for r in rows:
        text = (r.get("heading") or r.get("text_snippet") or "").strip()
        words = text.split()
        if len(words) >= 3:
            pool.append(" ".join(words[:6]))
    if not pool:
        click.echo("Not enough text in memories to form benchmark queries.")
        return
    queries = [pool[i % len(pool)] for i in range(n_queries)]

    rerank_fn = None
    if not no_rerank:
        try:
            from ..memory.reranker import rerank_scores
            rerank_fn = rerank_scores
        except Exception:
            rerank_fn = None

    emb = Embedder.get()
    # Warm up (model load, first-call JIT) so it doesn't skew the percentiles.
    combined_search(queries[0], store, cache, emb, top_k=top_k, rerank_fn=rerank_fn)

    timings_ms: list[float] = []
    for q in queries:
        t0 = time.perf_counter()
        combined_search(q, store, cache, emb, top_k=top_k, rerank_fn=rerank_fn)
        timings_ms.append((time.perf_counter() - t0) * 1000.0)

    timings_ms.sort()

    def pct(p: float) -> float:
        if not timings_ms:
            return 0.0
        idx = min(len(timings_ms) - 1, int(round(p / 100.0 * (len(timings_ms) - 1))))
        return timings_ms[idx]

    index_mb = cache.index_bytes() / 1e6
    click.echo("")
    click.echo(f"  Search latency over {n_mem} memories  (cross_encoder={not no_rerank})")
    click.echo(f"  queries: {len(timings_ms)}   top_k: {top_k}")
    click.echo(f"    p50: {pct(50):.1f} ms")
    click.echo(f"    p95: {pct(95):.1f} ms")
    click.echo(f"    p99: {pct(99):.1f} ms")
    click.echo(f"    min/max: {timings_ms[0]:.1f} / {timings_ms[-1]:.1f} ms")
    click.echo(f"  resident search index: {index_mb:.1f} MB "
               f"({cache.index_bytes() / max(n_mem, 1):.0f} bytes/memory)")
    click.echo("")
