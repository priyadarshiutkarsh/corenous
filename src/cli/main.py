import shutil
import os
import re
import subprocess
import sys
import time

import click
from pathlib import Path

from .context import AppContext


@click.group()
@click.version_option(version="0.1.0", prog_name="corenous-ai")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Corenous AI — privacy-first local memory AI."""
    ctx.ensure_object(dict)
    ctx.obj["app"] = AppContext.load(Path.cwd())


from .query import query_cmd          # noqa: E402  (register subcommand)
from .vault_cli import vault_group    # noqa: E402
from .daemon_ctl import daemon_group  # noqa: E402
from .hotkey_ctl import hotkey_group  # noqa: E402
from .list_cmds import search_cmd, recent_cmd, tail_cmd  # noqa: E402

cli.add_command(query_cmd, name="query")
cli.add_command(vault_group, name="vault")
cli.add_command(daemon_group, name="daemon")
cli.add_command(search_cmd, name="search")
cli.add_command(recent_cmd, name="recent")
cli.add_command(tail_cmd, name="tail")


@cli.command("add")
@click.argument("text")
@click.option("--source", default="manual", help="Source label (default: manual)")
@click.option(
    "--no-ai",
    is_flag=True,
            help="Skip local LLM summarization (use fast text heuristics only)",
)
@click.pass_context
def add_cmd(ctx: click.Context, text: str, source: str, no_ai: bool) -> None:
    """Manually add a memory."""
    app: AppContext = ctx.obj["app"]
    from ..privacy.detector import SensitivityDetector
    from ..memory.embedder import Embedder
    from ..memory.summaries import memory_title, summarize_subject
    from ..turboquant import encoder as tq

    detector = SensitivityDetector.from_config(app.config_path)
    result = detector.classify(text)

    if result.is_sensitive:
        click.echo(f"[sensitive] Routing to vault. Reasons: {', '.join(result.reasons)}")
        if not app.vault.is_initialized():
            click.echo("Vault not initialized. Run: corenous-ai vault init")
            return
        if not app.vault.is_unlocked():
            pw = click.prompt("Vault passphrase", hide_input=True)
            if not app.vault.unlock(pw):
                click.echo("Wrong passphrase.", err=True)
                return
        import time
        app.vault.store(text, source, "cli", result.reasons, time.time())
        click.echo("Stored in vault.")
    else:
        ai_heading, ai_summary, ai_story = "", "", ""
        if not no_ai:
            from ..ai.llm import load_model_sync
            from ..ai.summarizer import ai_summarize

            click.echo("Loading local AI for heading/summary…", err=True)
            if load_model_sync(timeout=120):
                ai_heading, ai_summary, ai_story = ai_summarize(
                    text,
                    window_title="",
                    app_name="cli",
                    activity="Manual memory",
                )
        vec = Embedder.get().embed(text)
        cv = tq.encode(vec)
        heading = ai_heading or memory_title(source, "cli", "Manual memory", text=text)
        summary = ai_summary or summarize_subject(
            text, app_name="cli", activity="Manual memory",
        )
        mid = app.store.insert_memory(
            text, source, "cli", cv, cv.residual_norm,
            activity="Manual memory",
            heading=heading,
            summary=summary,
        )
        if mid is None:
            click.echo("Duplicate — skipped.")
        else:
            if ai_story and len(ai_story.strip()) > 40:
                app.store.update_ai(mid, narrative=ai_story.strip(), ai_state="narrated")
            app.cache.append(mid, cv, cv.residual_norm)
            click.echo(f"Stored as memory #{mid}.")


@cli.group("memories")
def memories_group() -> None:
    """Manage timeline memories (SQLite + vector cache)."""


@memories_group.command("clear")
@click.option(
    "--yes", "-y",
    is_flag=True,
    help="Skip confirmation (destructive)",
)
@click.pass_context
def memories_clear_cmd(ctx: click.Context, yes: bool) -> None:
    """Delete all non-vault memories, vectors, and page content cache."""
    app: AppContext = ctx.obj["app"]
    if not yes:
        click.confirm(
            "Erase every saved memory row and search cache? "
            "The encrypted vault is left untouched.",
            abort=True,
        )
    app.store.clear_all_memories()
    app.cache.clear()
    cc = app.data_dir / "content_cache"
    if cc.is_dir():
        shutil.rmtree(cc)
    click.echo("All memories cleared. Restart Corenous if it is running.")


# Pattern that flags narratives written before strip_ui_chrome shipped. Strong
# signals only: a version string (v1.2.3 family) or an update banner phrase.
_LEGACY_CHROME_NARRATIVE_RE = re.compile(
    r"\bv\d+\.\d+(?:\.\d+)*\b|"
    r"\bRelaunch\s+to\s+update\b|"
    r"\bUpdate\s+available\b",
    re.IGNORECASE,
)


@memories_group.command("regenerate")
@click.argument("memory_id", type=int)
@click.option("--dry-run", is_flag=True,
              help="Show what would be regenerated without writing.")
@click.pass_context
def memories_regenerate_cmd(
    ctx: click.Context, memory_id: int, dry_run: bool
) -> None:
    """Force AI narrative regeneration for a single memory by id.

    Useful for verifying a summariser change end to end (capture, store,
    refine, render) without waiting on the daemon's background queue, and
    for pushing an individual broken memory through the current pipeline
    on demand. Reuses the same code path the daemon uses, so the result
    reflects every fix currently on disk.
    """
    app: AppContext = ctx.obj["app"]
    row = app.store._conn.execute(
        "SELECT id, app_name, window_title, activity, heading, narrative, full_text "
        "FROM memories WHERE id = ? AND is_sensitive = 0",
        (memory_id,),
    ).fetchone()
    if row is None:
        raise click.ClickException(
            f"Memory {memory_id} not found (or marked sensitive)."
        )

    text = row["full_text"] or ""
    if not text.strip():
        raise click.ClickException(
            f"Memory {memory_id} has no full_text to regenerate from."
        )

    click.echo(f"[{memory_id}] {row['app_name'] or '?':15}  {row['heading'] or ''}")
    cur = (row["narrative"] or "").strip()
    if cur:
        click.echo(f"Current narrative ({len(cur)} chars, first lines):")
        for ln in cur.splitlines()[:3]:
            if ln.strip():
                click.echo(f"  {ln[:120]}")
    else:
        click.echo("Current narrative: (empty)")

    if dry_run:
        click.echo("\n--dry-run, nothing written.")
        return

    from ..ai.llm import load_model_sync
    from ..ai.summarizer import ai_memory_bullets

    click.echo("\nLoading local AI model...", err=True)
    if not load_model_sync(timeout=180):
        raise click.ClickException("Could not load the local AI model.")

    new = ai_memory_bullets(
        text,
        heading=row["heading"] or "",
        app_name=row["app_name"] or "",
        window_title=row["window_title"] or "",
        activity=row["activity"] or "",
    )
    if not new.strip():
        raise click.ClickException("AI returned empty; narrative left unchanged.")

    app.store.update_ai(memory_id, narrative=new.strip())
    click.echo(f"\nRegenerated ({len(new)} chars):")
    for ln in new.splitlines():
        if ln.strip():
            click.echo(f"  {ln}")


@memories_group.command("regenerate-affected")
@click.option("--dry-run", is_flag=True,
              help="List affected memories without regenerating.")
@click.option("--limit", default=None, type=int,
              help="Cap the number of memories to process.")
@click.pass_context
def memories_regenerate_affected_cmd(
    ctx: click.Context, dry_run: bool, limit: int | None
) -> None:
    """Regenerate AI narratives for memories with known chrome leaks.

    Detects narratives that contain a version string (vX.Y.Z) or an update
    banner phrase. These are signatures of OCR chrome that leaked into the
    AI output before strip_ui_chrome shipped. Re-runs ai_memory_bullets on
    each detected row, which now applies the chrome filter and the orphan
    UI prompt rules, and persists the clean result.
    """
    app: AppContext = ctx.obj["app"]
    rows = app.store._conn.execute(
        "SELECT id, app_name, window_title, activity, heading, narrative, full_text "
        "FROM memories WHERE narrative != '' AND is_sensitive = 0"
    ).fetchall()
    affected = [r for r in rows if _LEGACY_CHROME_NARRATIVE_RE.search(r["narrative"] or "")]

    if not affected:
        click.echo("No memories found with known chrome leak signatures.")
        return

    click.echo(f"Found {len(affected)} memories with chrome leak signatures.")
    if limit:
        affected = affected[:limit]
        click.echo(f"Processing first {len(affected)} (--limit applied).")

    if dry_run:
        click.echo("\nDry run, showing affected memories without regenerating:")
        for r in affected:
            head = (r["heading"] or "").strip()
            click.echo(f"  [{r['id']}] {r['app_name'] or '?':15}  {head}")
        return

    from ..ai.llm import load_model_sync
    from ..ai.summarizer import ai_memory_bullets

    click.echo("Loading local AI model...", err=True)
    if not load_model_sync(timeout=180):
        raise click.ClickException("Could not load the local AI model.")

    success = 0
    skipped = 0
    for r in affected:
        text = r["full_text"] or ""
        if not text.strip():
            click.echo(f"  [{r['id']}] no full_text stored, skipping")
            skipped += 1
            continue
        new = ai_memory_bullets(
            text,
            heading=r["heading"] or "",
            app_name=r["app_name"] or "",
            window_title=r["window_title"] or "",
            activity=r["activity"] or "",
        )
        if not new.strip():
            click.echo(f"  [{r['id']}] AI returned empty, narrative left unchanged")
            skipped += 1
            continue
        app.store.update_ai(r["id"], narrative=new.strip())
        click.echo(f"  [{r['id']}] regenerated ({len(new)} chars)")
        success += 1

    click.echo(f"\nDone. Regenerated {success}, skipped {skipped}.")


def _parse_day_arg(day_str: str) -> tuple[float, float, str, str]:
    """Resolve a --day argument into (start_epoch, end_epoch, human_label, day_key).

    Accepts the literals ``today``, ``yesterday``, or an ISO ``YYYY-MM-DD``
    date. Boundaries are local midnight to local midnight of the next day.
    ``day_key`` is the canonical ``YYYY-MM-DD`` form used as the digest
    cache primary key. Raises ``click.BadParameter`` on anything else.
    """
    from datetime import date, datetime, time as dtime, timedelta
    s = (day_str or "").strip().lower()
    if s == "today":
        d = date.today()
        label = "Today"
    elif s == "yesterday":
        d = date.today() - timedelta(days=1)
        label = "Yesterday"
    else:
        try:
            d = datetime.strptime(day_str, "%Y-%m-%d").date()
        except ValueError as e:
            raise click.BadParameter(
                f"--day must be 'today', 'yesterday', or YYYY-MM-DD (got {day_str!r})"
            ) from e
        label = d.strftime("%A, %b %d")
    start_dt = datetime.combine(d, dtime.min)
    end_dt = datetime.combine(d + timedelta(days=1), dtime.min)
    return start_dt.timestamp(), end_dt.timestamp(), label, d.strftime("%Y-%m-%d")


@memories_group.command("digest")
@click.option("--day", default="today", show_default=True,
              help="Which day to digest: today, yesterday, or YYYY-MM-DD.")
@click.option("--regenerate", is_flag=True,
              help="Force fresh generation, overwriting any cached digest.")
@click.pass_context
def memories_digest_cmd(ctx: click.Context, day: str, regenerate: bool) -> None:
    """Generate a recap of the captures from one day.

    Pulls all non sensitive memories for the chosen calendar day (local
    time), runs the daily digest synthesiser over them, and prints the
    result. The result is cached per day so subsequent reads are
    instant. Pass --regenerate to force a fresh pass.
    """
    app: AppContext = ctx.obj["app"]
    start_ts, end_ts, label, day_key = _parse_day_arg(day)

    if not regenerate:
        cached = app.store.get_digest(day_key)
        if cached and cached.get("content", "").strip():
            from datetime import datetime as _dt
            ts = _dt.fromtimestamp(cached["generated_at"]).strftime("%b %d %H:%M")
            click.echo(
                f"{label}: {cached['source_count']} memories "
                f"(cached digest from {ts}, pass regenerate for a fresh one).\n",
                err=True,
            )
            click.echo(cached["content"])
            return

    rows = app.store.get_memories_in_range(start_ts, end_ts, limit=500)
    if not rows:
        click.echo(f"No memories captured on {label}.")
        return

    click.echo(f"{label}: {len(rows)} memories captured.\n", err=True)

    from ..ai.llm import load_model_sync
    from ..ai.summarizer import ai_daily_digest

    click.echo("Loading local AI model...", err=True)
    if not load_model_sync(timeout=180):
        raise click.ClickException("Could not load the local AI model.")

    digest = ai_daily_digest(rows, day_label=label)
    if not digest.strip():
        raise click.ClickException(
            "AI returned an empty digest. The day's captures may be too thin "
            "to synthesise — try a different --day."
        )

    # Persist so the next read is instant and so later phases (scheduler,
    # notification, overlay view) all read from one source of truth.
    app.store.upsert_digest(day_key, digest, time.time(), len(rows))
    click.echo(digest)


@cli.group("models")
def models_group() -> None:
    """Download or inspect the local GGUF (preset from config/settings.yaml → local_llm)."""


@models_group.command("download")
def models_download_cmd() -> None:
    """Fetch the active preset's GGUF from Hugging Face into ~/.corenous/models/."""
    from ..ai.llm import download_model_if_missing, model_path

    click.echo("Downloading model (only needed once per machine)…", err=True)
    if download_model_if_missing():
        click.echo(f"OK — {model_path()}")
    else:
        raise click.ClickException(
            "Download failed. Install huggingface-hub and try again, "
            "or download the GGUF manually into ~/.corenous/models/"
        )


@models_group.command("path")
def models_path_cmd() -> None:
    """Print the filesystem path to the active GGUF file."""
    from ..ai.llm import model_path

    click.echo(model_path())


@cli.command("stats")
@click.pass_context
def stats_cmd(ctx: click.Context) -> None:
    """Show memory statistics."""
    app: AppContext = ctx.obj["app"]
    count = app.store.get_memory_count()
    vault_count = len(app.store.get_vault_entries())
    cache_count = len(app.cache)
    click.echo(f"Memories : {count}")
    click.echo(f"Vault    : {vault_count} sensitive entries")
    click.echo(f"Cache    : {cache_count} vectors loaded")


@cli.command("export")
@click.option("--format", "fmt", default="json",
              type=click.Choice(["json", "markdown"]), show_default=True)
@click.option("--output", "-o", default=None, help="Output file (default: stdout)")
@click.pass_context
def export_cmd(ctx: click.Context, fmt: str, output: str) -> None:
    """Export all memories to JSON or Markdown."""
    import json, time as _time
    app: AppContext = ctx.obj["app"]
    rows = app.store.get_all_by_date(limit=10000)

    if fmt == "json":
        data = []
        for r in rows:
            data.append({
                "id": r["id"],
                "text": r.get("full_text") or r["text_snippet"],
                "source": r["source"],
                "app": r["app_name"],
                "tags": r.get("tags", ""),
                "starred": bool(r.get("is_starred", 0)),
                "created_at": r["created_at"],
                "created_human": _time.strftime(
                    "%Y-%m-%d %H:%M", _time.localtime(r["created_at"])),
            })
        out = json.dumps(data, indent=2, ensure_ascii=False)
    else:
        lines = ["# Corenous AI — Memory Export\n"]
        cur_date = None
        for r in rows:
            d = _time.strftime("%Y-%m-%d", _time.localtime(r["created_at"]))
            if d != cur_date:
                lines.append(f"\n## {d}\n")
                cur_date = d
            star = "★ " if r.get("is_starred") else ""
            tags = f" `{r['tags']}`" if r.get("tags") else ""
            ts   = _time.strftime("%H:%M", _time.localtime(r["created_at"]))
            text = (r.get("full_text") or r["text_snippet"]).replace("\n", " ")
            lines.append(f"- **{ts}**{tags} {star}{text}  _({r['source']})_")
        out = "\n".join(lines)

    if output:
        with open(output, "w") as f: f.write(out)
        click.echo(f"Exported {len(rows)} memories → {output}")
    else:
        click.echo(out)


@cli.command("compact")
@click.pass_context
def compact_cmd(ctx: click.Context) -> None:
    """Reclaim space (VACUUM + ANALYZE + FTS optimize) and refresh planner stats."""
    app: AppContext = ctx.obj["app"]
    info = app.store.compact()
    before = info.get("bytes_before", 0) / (1024 * 1024)
    after = info.get("bytes_after", 0) / (1024 * 1024)
    saved = info.get("bytes_reclaimed", 0) / (1024 * 1024)
    click.echo(f"Compacted {info.get('db_path')}")
    click.echo(f"  Before  : {before:6.2f} MB")
    click.echo(f"  After   : {after:6.2f} MB")
    click.echo(f"  Saved   : {saved:6.2f} MB")


@cli.command("install-service")
@click.pass_context
def install_service_cmd(ctx: click.Context) -> None:
    """Install launchd plist so daemon auto-starts on login (macOS)."""
    import os, sys
    from pathlib import Path
    app_ctx: AppContext = ctx.obj["app"]

    python  = sys.executable
    project = Path.cwd()
    data    = app_ctx.data_dir.resolve()
    config  = app_ctx.config_path.resolve()
    label   = "com.corenous.daemon"
    plist_path = Path.home() / "Library/LaunchAgents" / f"{label}.plist"

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>        <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string><string>src.monitor.daemon</string>
        <string>--data-dir</string><string>{data}</string>
        <string>--config</string><string>{config}</string>
    </array>
    <key>WorkingDirectory</key>  <string>{project}</string>
    <key>RunAtLoad</key>         <true/>
    <key>KeepAlive</key>         <true/>
    <key>StandardOutPath</key>   <string>{data}/daemon.log</string>
    <key>StandardErrorPath</key> <string>{data}/daemon.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')}</string>
    </dict>
</dict>
</plist>"""

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist)

    os.system(f"launchctl unload '{plist_path}' 2>/dev/null")
    ret = os.system(f"launchctl load '{plist_path}'")
    if ret == 0:
        click.echo(f"Service installed and started → {plist_path}")
        click.echo("Corenous will now auto-start on every login.")
    else:
        click.echo(f"Plist written to {plist_path} but launchctl load failed.")
        click.echo("Try:  launchctl load ~/Library/LaunchAgents/com.corenous.daemon.plist")


@cli.command("app")
@click.option(
    "--only-if-free",
    is_flag=True,
    help="Do not replace an existing menu bar app; exit with an error if one is running.",
)
@click.option(
    "--restart",
    "-r",
    is_flag=True,
    help="Explicit replace (default for corenous-ai app with no flags).",
)
@click.argument(
    "action",
    type=click.Choice(["restart", "quit"]),
    required=False,
)
@click.pass_context
def app_cmd(
    ctx: click.Context,
    only_if_free: bool,
    restart: bool,
    action: str | None,
) -> None:
    """Launch the menu bar + overlay app (macOS only).

    By default, existing menu bar instances are stopped first, then one fresh
    instance starts (same as subcommand restart or flag -r/--restart).

    Subcommand quit stops menu bar processes without starting a new one.
    """
    import time

    from ..app.main import launch, stop_existing_app_instances

    app_ctx: AppContext = ctx.obj["app"]

    if action == "quit":
        killed = stop_existing_app_instances(app_ctx.data_dir)
        if killed:
            joined = ", ".join(str(p) for p in killed)
            click.echo(f"Quit Corenous menu bar instance(s): {joined}")
        else:
            click.echo("No Corenous menu bar instance was running.")
        return

    replace_first = (action == "restart") or restart or (not only_if_free)

    if replace_first:
        killed = stop_existing_app_instances(app_ctx.data_dir)
        if killed:
            joined = ", ".join(str(p) for p in killed)
            click.echo(f"Stopped menu bar instance(s): {joined}")
        time.sleep(0.35)

    if not launch(app_ctx.data_dir, app_ctx.config_path):
        msg = (
            "Could not start Corenous AI (another instance may still be running).\n"
            "Try:  corenous-ai app quit"
        )
        if only_if_free:
            msg = (
                "Corenous AI is already running — check the menu bar (🧠).\n"
                "Start without --only-if-free to replace it, or run:  corenous-ai app quit"
            )
        click.echo(msg, err=True)


@cli.command("start")
@click.option(
    "--foreground",
    is_flag=True,
    help="Run in foreground (blocks terminal).",
)
@click.pass_context
def start_cmd(ctx: click.Context, foreground: bool) -> None:
    """Simple product-style startup: one command to run Corenous.

    Default behavior starts the app quietly in the background. The app
    auto-manages its capture engine, so no separate daemon command is needed.
    """
    app_ctx: AppContext = ctx.obj["app"]
    from ..app.main import launch, stop_existing_app_instances

    if foreground:
        stop_existing_app_instances(app_ctx.data_dir)
        if not launch(app_ctx.data_dir, app_ctx.config_path):
            raise click.ClickException("Corenous is already running.")
        return

    stop_existing_app_instances(app_ctx.data_dir)
    env = dict(os.environ)
    env.setdefault("CORENOUS_VERBOSE", "0")

    cmd = [sys.executable, "-m", "src.cli.main", "app", "--only-if-free"]
    app_log = app_ctx.data_dir / "app.log"
    app_err = app_ctx.data_dir / "app.err"

    log_f = None
    err_f = None
    proc = None
    try:
        app_ctx.data_dir.mkdir(parents=True, exist_ok=True)
        log_f = app_log.open("a")
        err_f = app_err.open("a")
        proc = subprocess.Popen(
            cmd,
            stdout=log_f,
            stderr=err_f,
            start_new_session=True,
            close_fds=True,
            cwd=str(Path.cwd()),
            env=env,
        )
        # Smoke check so we do not print success on immediate crash. Give the
        # spawned process enough time to fail during startup; the previous
        # 700ms missed import errors and config failures.
        time.sleep(1.0)
        if log_f is not None:
            log_f.flush()
        if err_f is not None:
            err_f.flush()
    except Exception as exc:
        raise click.ClickException(f"Could not start Corenous: {exc}") from exc
    finally:
        if log_f is not None:
            log_f.close()
        if err_f is not None:
            err_f.close()
    if proc is not None and proc.poll() is not None:
        tail = ""
        try:
            if app_err.exists():
                with app_err.open("r") as f:
                    lines = f.readlines()
                tail = "".join(lines[-15:]).rstrip()
        except Exception:
            pass
        msg = f"Corenous failed to start (menu bar app exited with code {proc.returncode})."
        if tail:
            msg += f"\nLast lines from {app_err}:\n{tail}"
        raise click.ClickException(msg)
    click.echo("Corenous is running. Open with Option+Command+Shift+Space.")


cli.add_command(memories_group, name="memories")
cli.add_command(models_group, name="models")


@cli.group("agent")
def agent_group() -> None:
    """Agent-facing interfaces (MCP/stdin tools)."""


@agent_group.command("serve")
@click.pass_context
def agent_serve_cmd(ctx: click.Context) -> None:
    """Run an MCP-compatible stdio server for AI agent integrations."""
    app: AppContext = ctx.obj["app"]
    from ..agent.mcp_server import serve_stdio

    click.echo("Starting Corenous agent server on stdio…", err=True)
    serve_stdio(app)


cli.add_command(agent_group, name="agent")
cli.add_command(hotkey_group, name="hotkey")


if __name__ == "__main__":
    cli()
