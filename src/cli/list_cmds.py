"""Fast read-only listing commands: search, recent, tail.

These differ from `query` in that they do NOT load the local LLM. They are
keyword search and listing primitives meant for scripting from a terminal
and for piping into `jq` or other tools via the --json flag.
"""
from __future__ import annotations

import json
import time

import click

from .context import AppContext


# ── formatters ────────────────────────────────────────────────────────────

def _format_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _human_row(r: dict, show_score: bool = False) -> str:
    ts = _format_ts(r["created_at"])
    app = r.get("app_name") or "?"
    label = r.get("heading") or r.get("text_snippet") or ""
    score = ""
    if show_score and r.get("bm25_score") is not None:
        score = f"  score={float(r['bm25_score']):.2f}"
    return f"[{r['id']}] {ts}  {app}{score}  —  {label}"


def _json_row(r: dict) -> dict:
    return {
        "id": r["id"],
        "created_at": r["created_at"],
        "created_human": _format_ts(r["created_at"]),
        "source": r.get("source"),
        "app": r.get("app_name"),
        "heading": r.get("heading"),
        "summary": r.get("summary"),
        "snippet": r.get("text_snippet"),
        "activity": r.get("activity"),
        "window_title": r.get("window_title"),
        "tags": r.get("tags"),
        "starred": bool(r.get("is_starred", 0)),
        "bm25_score": r.get("bm25_score"),
    }


def _emit(rows: list[dict], as_json: bool, show_score: bool = False) -> None:
    if as_json:
        click.echo(json.dumps([_json_row(r) for r in rows], indent=2, ensure_ascii=False))
        return
    if not rows:
        click.echo("No memories found.")
        return
    for r in rows:
        click.echo(_human_row(r, show_score=show_score))


# ── commands ──────────────────────────────────────────────────────────────

@click.command("search")
@click.argument("terms", nargs=-1, required=True)
@click.option("--limit", "-n", default=20, show_default=True, type=int,
              help="Maximum results to return.")
@click.option("--json", "as_json", is_flag=True,
              help="Emit machine readable JSON instead of human text.")
@click.pass_context
def search_cmd(ctx: click.Context, terms: tuple[str, ...], limit: int, as_json: bool) -> None:
    """Fast keyword search over your memory (FTS5, no AI loading).

    Examples:

        corenous-ai search github vector quantization

        corenous-ai search "exact phrase" --json | jq '.[0]'
    """
    app: AppContext = ctx.obj["app"]
    query = " ".join(terms).strip()
    rows = app.store.fts_search(query, limit=limit)
    _emit(rows, as_json=as_json, show_score=True)


@click.command("recent")
@click.option("--limit", "-n", default=20, show_default=True, type=int)
@click.option("--json", "as_json", is_flag=True,
              help="Emit machine readable JSON instead of human text.")
@click.pass_context
def recent_cmd(ctx: click.Context, limit: int, as_json: bool) -> None:
    """Show the most recent memories (newest first)."""
    app: AppContext = ctx.obj["app"]
    rows = app.store.get_recent(limit=limit)
    _emit(rows, as_json=as_json)


@click.command("tail")
@click.option("--interval", "-i", default=2.0, show_default=True, type=float,
              help="Poll interval in seconds.")
@click.option("--json", "as_json", is_flag=True,
              help="Emit one JSON object per new memory (newline delimited).")
@click.pass_context
def tail_cmd(ctx: click.Context, interval: float, as_json: bool) -> None:
    """Stream new memories as they are captured (Ctrl+C to stop).

    Polls the SQLite store every --interval seconds. New rows appear
    in chronological order so the output reads like a live timeline.
    """
    app: AppContext = ctx.obj["app"]
    bootstrap = app.store.get_recent(limit=1)
    seen_max_id = int(bootstrap[0]["id"]) if bootstrap else 0
    if not as_json:
        click.echo(
            f"Watching for new memories (poll every {interval:g}s). Ctrl+C to stop.",
            err=True,
        )
    try:
        while True:
            rows = app.store.get_recent(limit=50)
            new = [r for r in rows if int(r["id"]) > seen_max_id]
            if new:
                seen_max_id = max(int(r["id"]) for r in new)
                for r in reversed(new):  # newest-first → chronological
                    if as_json:
                        click.echo(json.dumps(_json_row(r), ensure_ascii=False))
                    else:
                        click.echo(_human_row(r))
            time.sleep(interval)
    except KeyboardInterrupt:
        if not as_json:
            click.echo("\nStopped.", err=True)
