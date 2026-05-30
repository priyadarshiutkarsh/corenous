import time

import click

from .context import AppContext


@click.command()
@click.argument("question")
@click.option("--top-k", default=20, show_default=True, help="Memories to retrieve before AI synthesis")
@click.option("--raw", is_flag=True, help="Show raw memory rows instead of AI answer")
@click.option("--include-vault", is_flag=True, help="Also search encrypted vault (prompts for passphrase)")
@click.pass_context
def query_cmd(ctx: click.Context, question: str, top_k: int,
              raw: bool, include_vault: bool) -> None:
    """Ask a question about your memory. Example: corenous query \"what was I studying today\""""
    app: AppContext = ctx.obj["app"]

    from ..memory.embedder import Embedder
    from ..app.search_combo import combined_search

    click.echo("Searching memory...", err=True)
    results = combined_search(question, app.store, app.cache, Embedder.get(), top_k=top_k)

    if not results:
        click.echo("No matching memories found.")
        return

    if raw:
        click.echo(f"\nFound {len(results)} result(s):\n")
        for i, r in enumerate(results, 1):
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.created_at))
            click.echo(f"  [{i}] score={r.score:.3f}  app={r.app_name or '?'}  {ts}")
            click.echo(f"      {r.heading or r.text_snippet}")
        if include_vault:
            _search_vault(app, question)
        return

    # AI synthesis over the retrieved rows
    rows = [
        {
            "created_at": r.created_at,
            "app_name":   r.app_name,
            "heading":    r.heading,
            "summary":    r.summary,
            "text_snippet": r.text_snippet,
            "activity":   r.activity,
            "window_title": r.window_title,
        }
        for r in results
    ]

    try:
        from ..ai.llm import load_model_sync
        from ..ai.summarizer import ai_answer_query
        click.echo("Loading AI model...", err=True)
        ready = load_model_sync(timeout=120)
        if ready:
            answer = ai_answer_query(question, rows)
            if answer and len(answer.strip()) > 10:
                click.echo(f"\n{answer}\n")
                click.echo(f"(based on {len(results)} memories — use --raw to see them)", err=True)
                if include_vault:
                    _search_vault(app, question)
                return
    except Exception:
        pass

    # Fallback: plain list if AI unavailable
    click.echo(f"\nFound {len(results)} result(s):\n")
    for i, r in enumerate(results, 1):
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.created_at))
        label = r.heading or r.text_snippet
        click.echo(f"  [{i}] {ts}  {r.app_name or '?'}  —  {label}")
    if include_vault:
        _search_vault(app, question)


def _search_vault(app: AppContext, question: str) -> None:
    if not app.vault.is_initialized():
        click.echo("[vault] Not initialized.")
        return
    pw = click.prompt("\nVault passphrase", hide_input=True)
    if not app.vault.unlock(pw):
        click.echo("[vault] Wrong passphrase.", err=True)
        return

    entries = app.vault.list_entries()
    tokens = set(question.lower().split())
    matches = []
    for entry in entries:
        try:
            data = app.vault.retrieve(entry["id"])
            if any(tok in data["text"].lower() for tok in tokens):
                matches.append(data)
        except Exception:
            continue
    app.vault.lock()

    if matches:
        click.echo(f"\n[vault] Found {len(matches)} sensitive result(s):\n")
        for m in matches:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["ts"]))
            click.echo(f"  app={m['app']}  source={m['source']}  {ts}")
            click.echo(f"  reasons: {', '.join(m.get('reasons', []))}")
            click.echo(f"  {m['text'][:200]}")
            click.echo()
    else:
        click.echo("[vault] No sensitive matches.")
