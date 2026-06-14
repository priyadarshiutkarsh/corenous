"""
Background memory capture daemon.
Merges clipboard + window streams, deduplicates, runs the full
sensitive-detection → vault / embed → store pipeline.

Entry point: python -m src.monitor.daemon
"""
from __future__ import annotations

import asyncio
import itertools
from collections import deque
from difflib import SequenceMatcher
import hashlib
import os
import re
import signal
import sys
import time
from pathlib import Path

import click
import numpy as np
import yaml

# sentence-transformers spawns N tokenizer worker processes by default, each
# printing "MallocStackLogging: can't turn off…" to stderr. Setting this env
# var before the import keeps the daemon's stderr clean.
import os as _os
_os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _rotate_log_if_needed(log_path: Path, max_bytes: int = 10 * 1024 * 1024) -> None:
    """Keep daemon.log under max_bytes by rotating to .log.1 when it exceeds the limit."""
    try:
        if log_path.exists() and log_path.stat().st_size > max_bytes:
            backup = log_path.with_suffix(".log.1")
            backup.unlink(missing_ok=True)
            log_path.rename(backup)
    except Exception:
        pass


def _lower_process_priority() -> None:
    """Run the capture/AI pipeline at low OS priority so it never starves
    the user's foreground work.

    Layers (best-effort, each falls through silently if unavailable):
      * ``os.nice(10)`` — matches what macOS uses for background helpers.
      * macOS ``setiopolicy_np`` (IOPOL_THROTTLE) — disk I/O takes a back
        seat to foreground writes.
      * macOS ``pthread_set_qos_class_self_np`` (QOS_CLASS_BACKGROUND) —
        the scheduler hint that drives Energy Impact in Activity Monitor.
        Background is the lowest non-throttled class and explicitly the
        right tier for "this work is invisible to the user".
    """
    try:
        os.nice(10)
    except Exception:
        pass
    try:
        import ctypes  # local import keeps the cold-start fast on non-darwin.

        libc = ctypes.CDLL("libc.dylib")
        # IOPOL_TYPE_DISK = 0, IOPOL_SCOPE_PROCESS = 1, IOPOL_THROTTLE = 3
        if hasattr(libc, "setiopolicy_np"):
            libc.setiopolicy_np(0, 1, 3)
        # QOS_CLASS_BACKGROUND = 0x09. The third arg is a relative
        # priority offset (0 .. -15); 0 is fine.
        if hasattr(libc, "pthread_set_qos_class_self_np"):
            libc.pthread_set_qos_class_self_np(0x09, 0)
    except Exception:
        pass


_lower_process_priority()


_DEDUP_SPACE_RE = re.compile(r"\s+")


def _norm_activity_text(text: str) -> str:
    text = text.lower()
    text = re.sub(
        r"\bscreenshot\s+\d{4}[- ]\d{1,2}[- ]\d{1,2}\s+at\s+"
        r"\d{1,2}[.: ]\d{1,2}(?:[.: ]\d{1,2})?\s*(?:am|pm)?\s*"
        r"(?:png|jpg|jpeg|heic)?\b",
        " ",
        text,
    )
    text = re.sub(r"\b[\w .()]*\.(?:png|jpg|jpeg|gif|heic|webp|pdf)\b", " ", text)
    text = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", " ", text)
    text = re.sub(r"\b\d+\s*(ms|s|sec|seconds|min|minutes|h|hours|ago)\b", " ", text)
    text = re.sub(r"https?://", "", text)
    text = re.sub(r"\b(?:screenshot|temporaryitems|screencaptureui|png|jpg|jpeg|image)\b", " ", text)
    text = _DEDUP_SPACE_RE.sub(" ", text).strip()
    return text[:1800]


def _activity_key(source: str, app_name: str, window_title: str, bundle_id: str = "") -> str:
    title = _DEDUP_SPACE_RE.sub(" ", (window_title or "").lower()).strip()[:120]
    bundle = (bundle_id or "").lower().strip()
    return f"{source.lower()}|{app_name.lower().strip()}|{bundle}|{title}"


def _is_new_tab_noise(text: str, app_name: str, bundle_id: str, window_title: str) -> bool:
    seed = f"{app_name} {bundle_id}".lower()
    if not any(name in seed for name in ("chrome", "safari", "firefox", "brave", "arc", "edge")):
        return False
    title = (window_title or "").lower().strip()
    low = _DEDUP_SPACE_RE.sub(" ", text.lower()).strip()
    if title in {"new tab", "start page", "untitled"}:
        return True
    if "new tab" not in low and "search google or type" not in low:
        return False
    noise = {
        "new", "tab", "search", "google", "type", "url", "customize",
        "chrome", "most", "visited", "bookmarks", "apps", "shortcuts",
    }
    words = set(re.findall(r"[a-z]{3,}", low))
    return bool(words) and len(words - noise) <= 3


def _is_near_repeat(new_text: str, old_text: str) -> bool:
    if not new_text or not old_text:
        return False
    if new_text == old_text:
        return True
    shorter = min(len(new_text), len(old_text))
    longer = max(len(new_text), len(old_text))
    if shorter >= 120 and new_text[:120] == old_text[:120] and shorter / longer > 0.82:
        return True
    return SequenceMatcher(None, new_text, old_text).quick_ratio() >= 0.94


def _tail_for_similarity(text: str, limit: int = 2200) -> str:
    """Compare against the most recent part of long collective memories."""
    if not text:
        return ""
    return text[-limit:] if len(text) > limit else text


def _is_significant_shift(new_text: str, old_text: str) -> bool:
    """True when the newest capture likely represents a new sub-task.

    We intentionally compare normalized *tails* so collective memory rows that
    already contain many prior updates do not force false negatives.
    """
    n = _norm_activity_text(_tail_for_similarity(new_text))
    o = _norm_activity_text(_tail_for_similarity(old_text))
    if not n or not o:
        return True
    if _is_near_repeat(n, o):
        return False
    ratio = SequenceMatcher(None, n, o).quick_ratio()
    return ratio < 0.70


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    an = float(np.linalg.norm(a))
    bn = float(np.linalg.norm(b))
    if an <= 1e-9 or bn <= 1e-9:
        return 0.0
    return float(np.dot(a, b) / (an * bn))


def _load_cfg(config_path: Path) -> dict:
    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


async def _run(data_dir: Path, config_path: Path) -> None:
    cfg = _load_cfg(config_path)
    mon_cfg  = cfg.get("monitoring", {})
    mem_cfg  = cfg.get("memory", {})

    # Performance-friendly defaults. These previously polled aggressively
    # (clipboard 0.5s, window 1s, screen 2s) which spent meaningful CPU on
    # Apple Vision OCR + AppleScript even when the user was idle. The new
    # defaults are still tight enough to feel "always on" but cut sustained
    # background load by 3-5x on the user's Mac.
    clip_interval   = float(mon_cfg.get("clipboard_poll_interval", 1.5))
    win_interval    = float(mon_cfg.get("window_poll_interval", 2.0))
    win_heartbeat   = float(mon_cfg.get("window_heartbeat_seconds", 90.0))
    dedup_window    = float(mon_cfg.get("dedup_window_seconds", 300))
    min_len         = int(mon_cfg.get("min_text_length", 20))
    max_len         = int(mon_cfg.get("max_text_length", 16000))
    activity_dedup  = float(mon_cfg.get("activity_dedup_seconds", max(dedup_window, 1800)))
    session_window_s = float(mon_cfg.get("session_window_seconds", 4 * 3600))
    session_cap      = max(1, int(mon_cfg.get("session_memory_cap", 3)))
    new_memory_gap_s = float(mon_cfg.get("session_new_memory_min_gap_seconds", 45 * 60))
    semantic_merge_similarity = float(mon_cfg.get("semantic_merge_similarity", 0.90))
    semantic_new_similarity = float(mon_cfg.get("semantic_new_similarity", 0.72))
    excluded_apps   = cfg.get("privacy", {}).get("excluded_apps", [])

    sum_chars = max(400, int(mem_cfg.get("summarize_content_chars", 1100)))
    sum_tok = max(120, int(mem_cfg.get("summarize_max_tokens", 300)))

    # AI refinement budget — keep ai_summarize (heading + kicker + narrative) on by
    # default, but make the heavier narrate + distill passes opt-in so the
    # daemon doesn't churn through three Gemma generations per memory in
    # the background. Users who want full multi-pass refinement can flip
    # ``memory.refine_full`` to true in settings.yaml.
    refine_full     = bool(mem_cfg.get("refine_full", False))

    from ..memory.store import MemoryStore
    from ..memory.vector_cache import VectorCache
    from ..memory.embedder import Embedder
    from ..privacy.detector import SensitivityDetector
    from ..privacy.patterns import redact_pii
    from ..privacy.vault import Vault
    from ..turboquant import encoder as tq
    from .clipboard import ClipboardMonitor
    from .window import WindowMonitor
    from .screen import ScreenMonitor
    from .permissions import require_accessibility_or_warn
    from .app_context import app_tags
    from ..memory.summaries import clean_text, memory_title, summarize_subject
    from ..ai.llm import configure_local_llm, ensure_model_ready
    from ..ai import ai_summarize, ai_narrate, ai_distill
    from ..ai import vision

    configure_local_llm(config_path)
    vision.configure_vision()
    # The single VL model is the only AI brain now (the text GGUF was removed).
    # Captures with a screenshot are summarized from the image; imageless ones
    # (clipboard, window AX text, backfilled rows) go through the VL text path —
    # same model, same MLX worker, so no second set of weights to OOM the GPU.
    vision_on = vision.vision_enabled()
    if vision_on:
        print(
            "[daemon] vision enabled — refining captures via the VL model "
            "(screenshots from the image, everything else from text).",
            flush=True,
        )
        vision.ensure_vision_ready()
    else:
        # Background download + load of the configured GGUF — non-blocking.
        # Heuristics label captures immediately; refine worker upgrades via blocking infer().
        ensure_model_ready()

    refine_enabled      = bool(mem_cfg.get("refine_summaries", True))
    refine_max          = int(mem_cfg.get("refine_queue_max", 400))
    # Minimum capture length for AI refinement. Short captures (button labels,
    # tooltip text, single words) carry no useful context for the LLM and only
    # burn GPU time. Default: skip anything under 60 chars.
    refine_min_chars    = int(mem_cfg.get("min_capture_chars_for_ai", 60))
    # Idle gap inserted *after* every refinement pass. The VL model saturates
    # the GPU for the ~2s of a generate call, and macOS shares that GPU with
    # video decode/compositing — so back-to-back refinements (e.g. a startup
    # backfill of dozens of pending rows) would peg the GPU for minutes and
    # make YouTube/full-screen video stutter. Yielding the GPU between passes
    # caps our share of it; the timeline still fills in, just paced out.
    refine_cooldown_s   = float(mem_cfg.get("refine_cooldown_seconds", 3.0))
    refine_queue: asyncio.PriorityQueue | None = (
        asyncio.PriorityQueue(refine_max) if refine_enabled else None
    )
    _refine_tie = itertools.count()

    store    = MemoryStore(data_dir / mem_cfg.get("db_filename", "memories.db"))
    cache    = VectorCache(data_dir / mem_cfg.get("vectors_filename", "vectors.npy"))
    cache.load_from_store(store.get_all_compressed_vectors())
    vault    = Vault(store)
    detector = SensitivityDetector.from_config(config_path)
    embedder = Embedder.get()

    # Privacy bookkeeping, persisted so `daemon status` can surface it: what
    # the vault could not protect and what the deferred AI re-check fixed.
    _sens_counts = {
        "missed":    int(store.get_config("sensitive_missed_total", "0") or 0),
        "revaulted": int(store.get_config("sensitive_revaulted_total", "0") or 0),
    }

    def _bump_sens(key: str) -> None:
        _sens_counts[key] += 1
        store.set_config(f"sensitive_{key}_total", str(_sens_counts[key]))

    # Captures whose AI sensitivity layer was skipped (model busy) — re-checked
    # in the background by _sensitivity_recheck_loop.
    _recheck_queue: deque[int] = deque(maxlen=256)

    # Heading of the most recently stored capture, fed into the next capture's
    # embedding envelope so fragments stay findable by their surrounding thread.
    _last_heading = {"v": ""}

    ax_ok = require_accessibility_or_warn()

    print(
        f"[daemon] sampling clipboard every {clip_interval}s; "
        f"active window every {win_interval}s (capture when app or focused text changes). "
        f"Skip duplicate body within {dedup_window:g}s; "
        f"skip near-repeat same-window captures within {activity_dedup:g}s. "
        f"Session rollup cap: {session_cap} memories / {session_window_s/3600:.1f}h "
        f"(semantic merge>={semantic_merge_similarity:.2f}, split<{semantic_new_similarity:.2f}).",
        flush=True,
    )

    # Track recently seen hashes for dedup
    _recent: dict[str, float] = {}
    _recent_activity: dict[str, tuple[float, str]] = {}

    # Cache the capture-paused flag + runtime excluded-app list and
    # re-poll every 5 seconds; reading the config table on every capture
    # is fine but cheap to amortize.
    import json as _json
    _pause_state = {"paused": False, "lite_mode": False, "excluded": tuple(), "ts": 0.0}

    def _refresh_pause_state() -> None:
        now = time.time()
        if now - _pause_state["ts"] <= 5.0:
            return
        try:
            _pause_state["paused"] = (
                store.get_config("capture_paused", "0") == "1"
            )
        except Exception:
            _pause_state["paused"] = False
        try:
            _pause_state["lite_mode"] = (
                store.get_config("lite_mode", "0") == "1"
            )
        except Exception:
            _pause_state["lite_mode"] = False
        try:
            raw = store.get_config("excluded_apps", "[]") or "[]"
            items = _json.loads(raw)
            if isinstance(items, list):
                _pause_state["excluded"] = tuple(
                    str(x).strip().lower() for x in items if str(x).strip()
                )
            else:
                _pause_state["excluded"] = tuple()
        except Exception:
            _pause_state["excluded"] = tuple()
        _pause_state["ts"] = now

    def _is_capture_paused() -> bool:
        _refresh_pause_state()
        return bool(_pause_state["paused"])

    def _is_app_excluded_runtime(app_name: str) -> bool:
        _refresh_pause_state()
        if not _pause_state["excluded"]:
            return False
        low = (app_name or "").strip().lower()
        return any(ex in low or low in ex for ex in _pause_state["excluded"])

    def _is_lite_mode() -> bool:
        _refresh_pause_state()
        return bool(_pause_state["lite_mode"])

    async def process(
        text: str,
        source: str,
        app_name: str,
        window_title: str = "",
        bundle_id: str = "",
        activity: str = "",
        image_path: str = "",
    ) -> None:
        if _is_capture_paused():
            return
        if _is_app_excluded_runtime(app_name):
            return
        text = text[:max_len].strip()
        if len(text) < min_len:
            return
        if _is_new_tab_noise(text, app_name, bundle_id, window_title):
            return

        content_hash = hashlib.sha256(text.encode()).hexdigest()
        norm_text = _norm_activity_text(text)
        activity_key = _activity_key(source, app_name, window_title, bundle_id)
        now = time.time()

        # Purge old dedup entries
        expired = [k for k, t in _recent.items() if now - t > dedup_window]
        for k in expired:
            del _recent[k]
        expired_activity = [
            k for k, (t, _) in _recent_activity.items()
            if now - t > activity_dedup
        ]
        for k in expired_activity:
            del _recent_activity[k]

        if content_hash in _recent:
            return
        _recent[content_hash] = now
        old_activity = _recent_activity.get(activity_key)
        if old_activity and _is_near_repeat(norm_text, old_activity[1]):
            return
        _recent_activity[activity_key] = (now, norm_text)

        result = detector.classify(text)
        if result.is_sensitive:
            # Sealed write: protects the capture with the vault's PUBLIC key,
            # so the daemon never needs the passphrase. The old in-process
            # unlock could never happen here (unlock lived and died with the
            # CLI process), which silently dropped every flagged capture.
            if vault.can_seal():
                vault_id = vault.seal(text, source, app_name, result.reasons, now)
                store.insert_sensitive(text, source, app_name, dedup_window=dedup_window + 1)
                print(f"[vault] #{vault_id} sealed  reasons={result.reasons[:2]}  app={app_name}", flush=True)
            elif not vault.is_initialized():
                _bump_sens("missed")
                print(f"[vault-skip] Vault not initialized; sensitive text dropped.", flush=True)
            else:
                _bump_sens("missed")
                print(
                    "[vault-old] Vault predates sealed captures; run "
                    "corenous-ai vault unlock once to upgrade it.",
                    flush=True,
                )
        else:
            # Not vault-bound, but may still carry incidental contact details
            # (email, phone). Scrub them before this memory is embedded, stored,
            # or refined so the plaintext timeline never holds raw PII.
            text = redact_pii(text)
            window_title = redact_pii(window_title)
            tag = app_tags(app_name, bundle_id)
            body = text if len(text) <= 6000 else f"{text[:4000]}\n{text[-2000:]}"
            # Context envelope: wrap the EMBEDDING input with where/when signal
            # (window, app, date, previous heading). Measured on LoCoMo to lift
            # recall; the stored and displayed text stays the raw capture.
            from ..memory.embed_context import capture_embed_text
            embed_text = capture_embed_text(
                body,
                window_title=window_title,
                app_name=app_name,
                ts=now,
                prev_heading=_last_heading["v"],
            )
            # Semantic dedup key: current capture embedding.
            loop = asyncio.get_running_loop()
            vec = await loop.run_in_executor(None, embedder.embed, embed_text)

            # Session-aware rollup:
            # Same app + same window over hours should not explode into hundreds
            # of rows. Keep only a small cap per activity session and merge
            # updates into collective memory text.
            recent_same = store.get_recent_for_activity(
                source=source,
                app_name=app_name,
                window_title=window_title,
                bundle_id=bundle_id,
                within_seconds=session_window_s,
                limit=max(session_cap + 2, 6),
            )

            merged_mid: int | None = None
            merged_text: str = text
            if recent_same:
                latest = recent_same[0]
                latest_mid = int(latest.get("id") or 0)
                latest_full = (latest.get("full_text") or latest.get("text_snippet") or "")
                latest_ts = float(latest.get("created_at") or 0.0)
                # Semantic similarity against same-window recent memories.
                sem_mid = latest_mid
                sem_sim = -1.0
                for r in recent_same[:8]:
                    rid = int(r.get("id") or 0)
                    if rid <= 0:
                        continue
                    row_v = store.get_memory_by_id(rid)
                    if not row_v:
                        continue
                    blob = row_v.get("compressed_data")
                    rn = float(row_v.get("residual_norm") or 0.0)
                    if blob is None:
                        continue
                    try:
                        cv_prev = tq.from_bytes(bytes(blob), rn)
                        prev_vec = tq.decode(cv_prev)
                        sim = _cosine(vec, prev_vec)
                        if sim > sem_sim:
                            sem_sim = sim
                            sem_mid = rid
                    except Exception:
                        continue
                shift = _is_significant_shift(text, latest_full)
                old_enough_for_new = (now - latest_ts) >= new_memory_gap_s
                sem_same_task = sem_sim >= semantic_merge_similarity
                sem_new_task = sem_sim >= 0 and sem_sim <= semantic_new_similarity
                should_create_new = (
                    (shift or sem_new_task)
                    and old_enough_for_new
                    and len(recent_same) < session_cap
                    and not sem_same_task
                )
                target_mid = sem_mid if sem_same_task and sem_mid > 0 else latest_mid
                if not should_create_new and target_mid > 0:
                    merged_text = store.append_memory_update(
                        target_mid,
                        text,
                        update_label=time.strftime("%b %d %H:%M", time.localtime(now)),
                    )
                    store.bump_memory_timestamp(target_mid, now)
                    merged_mid = target_mid
                    # Keep semantic search fresh for collective memories by
                    # updating the target vector to the latest capture state.
                    cv_latest = tq.encode(vec)
                    store.update_memory_vector(
                        target_mid, cv_latest, cv_latest.residual_norm,
                        fp16=vec.astype(np.float16).tobytes(),
                    )
                    cache.replace(target_mid, cv_latest, cv_latest.residual_norm)
                    print(
                        f"[mem-merge] #{target_mid}  app={app_name}  source={source}  "
                        f"title={window_title[:40]}  session={len(recent_same)}/{session_cap}  "
                        f"len+={len(text)}  sem={sem_sim:.3f}",
                        flush=True,
                    )

            mid_for_refine: int | None = None
            text_for_refine = text
            if merged_mid is not None:
                mid_for_refine = merged_mid
                text_for_refine = merged_text
            else:
                cv  = tq.encode(vec)

                # Instant heuristic labels; refine worker upgrades with model output.
                heading = clean_text(memory_title(
                    source, app_name, activity, window_title, text,
                ))
                summary = clean_text(summarize_subject(
                    text,
                    window_title=window_title,
                    app_name=app_name,
                    activity=activity,
                ))
                mid = store.insert_memory(
                    text, source, app_name, cv, cv.residual_norm, dedup_window,
                    tags=tag,
                    window_title=window_title,
                    bundle_id=bundle_id,
                    activity=activity,
                    heading=heading,
                    summary=summary,
                    fp16=vec.astype(np.float16).tobytes(),
                )
                if mid is not None:
                    cache.append(mid, cv, cv.residual_norm)
                    mid_for_refine = mid
                    text_for_refine = text
                    _last_heading["v"] = heading
                    print(
                        f"[mem] #{mid}  app={app_name}  tag={tag}  source={source}  "
                        f"title={window_title[:40]}  len={len(text)}",
                        flush=True,
                    )

            # The AI sensitivity layer was skipped (model busy): queue for the
            # deferred re-check loop so unchecked never means unprotected.
            if result.ai_unchecked and mid_for_refine is not None:
                if len(_recheck_queue) == _recheck_queue.maxlen:
                    print("[sens-recheck] queue full — oldest re-check dropped", flush=True)
                _recheck_queue.append(mid_for_refine)

            if (
                mid_for_refine is not None
                and refine_queue is not None
                and len(text_for_refine) >= refine_min_chars
                and not _is_lite_mode()
            ):
                try:
                    refine_queue.put_nowait(
                        (-mid_for_refine, next(_refine_tie), mid_for_refine,
                         text_for_refine, window_title, app_name, activity, source,
                         image_path))
                except asyncio.QueueFull:
                    print(
                        "[daemon] refine queue full — deferred AI title skipped "
                        f"(raise memory.refine_queue_max in settings.yaml)",
                        flush=True,
                    )

    async def refine_worker():
        """Serial refinement worker. One local LLM pass for heading, kicker, and
        a 5 to 7 paragraph narrative when ``memory.refine_summaries`` is on.

        Optional ``memory.refine_full`` adds narrate (only if narrative missing)
        plus distill for the facts grid.
        """
        import json as _json
        loop = asyncio.get_event_loop()
        from ..ai.llm import load_model_sync

        while True:
            (_, _tie, mid, cap_text, wt, app_n, act, src, image_path) = await refine_queue.get()
            try:

                def _refine_one():
                    use_vision = vision_on and bool(image_path)
                    # One VL model now serves both paths on a single MLX worker:
                    # captures with a screenshot are summarized from the image,
                    # imageless captures (clipboard, window AX text, backfilled
                    # rows) go through the VL *text* path. There is no second text
                    # model to OOM the GPU, so imageless captures are refined
                    # rather than skipped — that is what keeps their timeline
                    # titles specific instead of stuck on the heuristic label.
                    if vision_on:
                        if not vision.load_vision_sync(timeout=120):
                            return None
                    else:
                        if not load_model_sync(timeout=120):
                            return None
                    out: dict = {}

                    # Fetch the most recent memory for this app to give the AI
                    # temporal context (e.g. "user is still in the same file"),
                    # plus the last ~12 headings across all apps to keep the
                    # timeline scannable instead of full of near-duplicate titles.
                    prior_ctx = ""
                    avoid_headings: list[str] = []
                    try:
                        rows = store.get_recent(limit=20)
                        for r in rows:
                            if int(r.get("id", 0)) == mid:
                                continue
                            if not prior_ctx and (r.get("app_name") or "").lower() == (app_n or "").lower():
                                prior_ctx = (
                                    r.get("heading") or r.get("summary") or
                                    (r.get("text_snippet") or "")[:80]
                                ).strip()
                            h_prev = (r.get("heading") or "").strip()
                            if h_prev and len(avoid_headings) < 12:
                                avoid_headings.append(h_prev)
                    except Exception:
                        pass

                    # Pass 1: heading + kicker. Required.
                    h, kicker, story = ai_summarize(
                        cap_text,
                        wt,
                        app_n,
                        act,
                        fast=False,
                        content_char_limit=sum_chars,
                        completion_max_tokens=sum_tok,
                        prior_context=prior_ctx,
                        avoid_headings=avoid_headings,
                        image_path=image_path if use_vision else None,
                    )
                    if h:
                        cleaned_h = clean_text(h)
                        # Drop memories the model itself labels as ad /
                        # sponsored content — the user does not want ads
                        # cluttering the timeline.
                        try:
                            from .screen import is_ad_heading as _is_ad_h
                            if _is_ad_h(cleaned_h):
                                store.delete_memory(mid)
                                print(
                                    f"[mem-refine] #{mid} dropped (ad heading: "
                                    f"{cleaned_h!r})",
                                    flush=True,
                                )
                                return None
                        except Exception:
                            pass
                        out["heading"] = cleaned_h
                    if kicker:
                        out["summary"] = clean_text(kicker)
                    if story and len(story.strip()) > 40:
                        out["narrative"] = clean_text(story)

                    # Passes 2 + 3: narrate + distill only when refine_full is on and
                    # the unified pass did not already produce a narrative. These use
                    # the text model, so they are skipped in vision mode (the VL pass
                    # above already produced the narrative from the screenshot).
                    long_enough = len(cap_text or "") >= 30
                    if long_enough and refine_full and not use_vision:
                        if not out.get("narrative"):
                            narrative = ai_narrate(
                                cap_text,
                                window_title=wt,
                                app_name=app_n,
                                activity=act,
                                source=src,
                            )
                            if narrative:
                                out["narrative"] = narrative

                        facts = ai_distill(
                            cap_text,
                            window_title=wt,
                            app_name=app_n,
                            activity=act,
                        )
                        if facts:
                            out["entities_json"] = _json.dumps(
                                facts, ensure_ascii=False
                            )

                    if not out:
                        return None
                    # State machine: 'narrated' if we only got the story,
                    # 'distilled' if we got the structured facts too.
                    if "entities_json" in out:
                        out["ai_state"] = "distilled"
                    elif "narrative" in out:
                        out["ai_state"] = "narrated"
                    else:
                        out["ai_state"] = "headed"
                    return out

                fields = await loop.run_in_executor(None, _refine_one)
                if fields:
                    if store.update_ai(mid, **fields):
                        labels = ",".join(
                            k for k in (
                                "heading", "summary", "narrative",
                                "entities_json",
                            ) if k in fields
                        )
                        print(
                            f"[mem-refine] #{mid} upgraded ({labels}) "
                            f"state={fields.get('ai_state')}",
                            flush=True,
                        )
            except Exception as exc:
                print(f"[mem-refine] #{mid} error: {exc}", flush=True)
            finally:
                refine_queue.task_done()
                # The screenshot is consumed exactly once, by this pass.
                # Delete the plaintext PNG instead of leaving raw screen
                # pixels in the cache until the prune cycle reaches them.
                if image_path:
                    try:
                        Path(image_path).unlink()
                    except OSError:
                        pass
            # Hand the GPU back to the foreground (video decode, compositing)
            # before the next pass so refinement never monopolizes it. While a
            # backlog is draining (e.g. the startup backfill), back off harder
            # so a burst of queued passes can't peg the GPU for a sustained
            # stretch; resume the normal cadence once caught up to live captures.
            if refine_cooldown_s > 0:
                backlog = refine_queue.qsize()
                await asyncio.sleep(refine_cooldown_s * (3.0 if backlog > 5 else 1.0))

    async def backfill_pending_ai(limit: int = 60):
        """At startup, look for recent captures still in 'pending' AI state
        (e.g. captured before the new pipeline existed, or during a daemon
        outage) and re-queue them for refinement. Runs once after the model
        is ready."""
        if refine_queue is None:
            return
        if _is_lite_mode():
            print("[mem-refine] backfill skipped in lite mode", flush=True)
            return
        try:
            rows = store.get_recent_pending_ai(limit=limit)
        except Exception as exc:
            print(f"[mem-refine] backfill query failed: {exc}", flush=True)
            return
        if not rows:
            print("[mem-refine] backfill: nothing pending", flush=True)
            return
        queued = 0
        for r in rows:
            mid = int(r["id"])
            full = store.get_memory_by_id(mid)
            if not full:
                continue
            text = (full.get("full_text") or full.get("text_snippet") or "").strip()
            # Same floor as the live path: captures with too little text (e.g. a
            # browser-scanner row that is just a URL + page title) carry no body
            # to summarize, so the VL text model would invent content. Keep their
            # grounded heuristic title instead of refining a hallucination.
            if len(text) < refine_min_chars:
                continue
            try:
                refine_queue.put_nowait((
                    -mid,
                    next(_refine_tie),
                    mid,
                    text,
                    full.get("window_title", ""),
                    full.get("app_name", ""),
                    full.get("activity", ""),
                    full.get("source", ""),
                    "",  # no stored screenshot for backfilled rows
                ))
                queued += 1
            except asyncio.QueueFull:
                break
        print(f"[mem-refine] backfill queued {queued} memories", flush=True)

    # Screen OCR is by far the most expensive stream (full-window
    # CGWindowList capture + Apple Vision recognition on every poll).
    screen_interval  = float(mon_cfg.get("screen_poll_interval", 12.0))
    screen_enabled   = bool(mon_cfg.get("screen_recording_enabled", True))
    ocr_max_dim      = int(mon_cfg.get("screen_ocr_max_dimension", 1280))
    ocr_accurate     = bool(mon_cfg.get("accurate_ocr_mode", False))
    ocr_min_conf     = float(mon_cfg.get("ocr_min_confidence", 0.0))
    # Event-driven OCR: skip the Vision pass while the user is idle on an
    # unchanged window, but force a capture every force-refresh seconds so
    # passive on-screen changes (video, live pages) are still sampled.
    ocr_idle_skip    = bool(mon_cfg.get("screen_idle_skip", True))
    ocr_force_refresh = float(mon_cfg.get("screen_force_refresh_seconds", 90.0))
    screen_mon = ScreenMonitor(
        interval=screen_interval,
        max_ocr_dimension=ocr_max_dim,
        accurate_mode=ocr_accurate,
        min_confidence=ocr_min_conf,
        idle_skip=ocr_idle_skip,
        force_refresh_seconds=ocr_force_refresh,
    )

    clip_mon   = ClipboardMonitor(poll_interval=clip_interval)
    win_mon    = WindowMonitor(
        poll_interval=win_interval,
        heartbeat_interval=win_heartbeat,
    )

    async def run_clipboard():
        async for captured in clip_mon.stream(excluded_apps=excluded_apps):
            if _is_capture_paused():
                continue
            await process(
                captured.text, captured.source, captured.app_name,
                captured.window_title, captured.bundle_id, captured.activity,
            )

    async def run_window():
        if not ax_ok:
            return
        async for captured in win_mon.stream(excluded_apps=excluded_apps):
            if _is_capture_paused():
                continue
            await process(
                captured.text, captured.source, captured.app_name,
                captured.window_title, captured.bundle_id, captured.activity,
            )

    async def run_screen():
        if not screen_enabled or not screen_mon.is_available():
            return
        async for captured in screen_mon.stream(
            excluded_apps=excluded_apps,
            skip_if=lambda: _is_capture_paused() or _is_lite_mode(),
        ):
            # Skip the OCR result entirely when paused — even though the
            # ScreenMonitor still ticks, dropping the *result* prevents
            # the downstream embedding + AI passes from running.
            if _is_capture_paused():
                continue
            await process(
                captured.text, captured.source, captured.app_name,
                captured.window_title, captured.bundle_id, captured.activity,
                captured.image_path,
            )

    async def run_browser_scanner():
        """
        When a browser is the macOS frontmost app, read its active tab URL
        and (for Chromium browsers) visible page text via JS.

        Background browsers and non-active tabs are intentionally ignored —
        screen OCR already covers the frontmost window; this stream only
        enriches that capture with structured URL/body data.
        """
        import subprocess as _sp
        from urllib.parse import urlparse, parse_qs
        from .app_context import (
            _parse_browser_url,
            browser_activity,
            canonical_browser_name,
            get_frontmost_context,
        )

        _seen: dict[str, float] = {}   # normalized_url -> last captured ts
        _SEEN_MAX = 512  # cap to avoid unbounded dict growth on long uptime

        # URL query params that are session/tracking noise and must be stripped
        # before using the URL as a dedup key. Without this, Google Search URLs
        # with different sca_esv/ei values count as distinct and get re-captured
        # on every reload.
        _STRIP_PARAMS = frozenset({
            "sca_esv", "sca_upv", "udm", "ei", "iflsig", "ved", "sa", "source",
            "oq", "gs_lp", "gs_lcrp", "sclient", "rlz", "uact", "aqs",
            "sourceid", "ie", "client", "channel",  # Google
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",  # GA
            "fbclid", "gclid", "msclkid", "twclid", "igshid",  # ad trackers
            "_ga", "ref", "referrer",
        })

        def _norm_url(url: str) -> str:
            """Strip session/tracking params so the same page deduplicates."""
            try:
                from urllib.parse import urlparse, urlencode, parse_qsl
                p = urlparse(url)
                qs = urlencode(
                    [(k, v) for k, v in parse_qsl(p.query) if k not in _STRIP_PARAMS]
                )
                return p._replace(query=qs, fragment="").geturl()
            except Exception:
                return url

        # Poll only while a browser is frontmost — AppleScript + JS body fetch
        # is expensive and must not touch background browser windows.
        ACTIVE_POLL  = 20    # poll frontmost browser tab every N seconds
        DEDUP_ACTIVE = 90    # re-capture active URL after N seconds
        _SKIP_SCHEMES = ("chrome://", "about:", "chrome-extension://",
                         "brave://", "edge://", "arc://", "safari-extension://")

        _SEARCH_PARAMS: dict[str, str] = {
            "google.com": "q", "bing.com": "q", "yahoo.com": "p",
            "duckduckgo.com": "q", "reddit.com": "q", "twitter.com": "q",
            "x.com": "q", "amazon.com": "k", "youtube.com": "search_query",
        }

        # ── helpers ──────────────────────────────────────────────────────────

        def _osa(script: str, timeout: float = 1.8) -> str:
            try:
                r = _sp.run(["osascript", "-e", script],
                            capture_output=True, text=True, timeout=timeout, check=False)
                return r.stdout.strip() if r.returncode == 0 else ""
            except Exception:
                return ""

        def _app_target(name: str) -> str:
            low = name.lower()
            for candidate in ("Google Chrome", "Brave Browser", "Microsoft Edge", "Arc"):
                if candidate.lower() in low or low in candidate.lower():
                    return candidate
            return name

        def _active_tab(app: str) -> tuple[str, str]:
            if "safari" in app.lower():
                out = _osa(
                    'tell application "Safari"\n'
                    'if not (exists front window) then return ""\n'
                    'return (name of current tab of front window) & "\\n" & (URL of current tab of front window)\n'
                    'end tell'
                )
            else:
                t = _app_target(app)
                out = _osa(
                    f'tell application "{t}"\n'
                    'if not (exists front window) then return ""\n'
                    'set u to URL of active tab of front window\n'
                    'set ti to title of active tab of front window\n'
                    'return ti & "\\n" & u\n'
                    'end tell'
                )
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            return (lines[0], lines[1] if len(lines) > 1 else "")

        def _page_body(app: str, domain: str = "") -> str:
            """Extract visible page text via JS (Chrome-based only, best-effort)."""
            if "safari" in app.lower():
                return ""
            t = _app_target(app)
            if t not in ("Google Chrome", "Brave Browser", "Microsoft Edge", "Arc"):
                return ""
            from ..memory.content_cache import get_site_js
            js = get_site_js(domain)
            # Escape backslashes and double-quotes for embedding in AppleScript string
            js_escaped = js.replace("\\", "\\\\").replace('"', '\\"')
            return _osa(
                f'tell application "{t}"\n'
                f'try\nreturn execute front window\'s active tab javascript "{js_escaped}"\n'
                'on error\nreturn ""\nend try\nend tell',
                timeout=3.5,
            )[:30000]

        def _search_query(url: str) -> str:
            try:
                p = urlparse(url)
                domain = p.netloc.lower().lstrip("www.")
                qs = parse_qs(p.query)
                for d, param in _SEARCH_PARAMS.items():
                    if d in domain:
                        vals = qs.get(param, [])
                        return vals[0].strip() if vals else ""
            except Exception:
                pass
            return ""

        def _build_activity(tab, search_q: str) -> str:
            if search_q:
                return "Searched Web"
            return browser_activity(tab)

        async def _capture(app_name: str, title: str, url: str,
                           body: str = "", dedup: int = DEDUP_ACTIVE) -> None:
            if not url or any(url.startswith(s) for s in _SKIP_SCHEMES):
                return
            tab = _parse_browser_url(title, url)
            if tab.is_new_tab:
                return
            now = time.time()
            key = _norm_url(url)
            if now - _seen.get(key, 0) < dedup:
                return
            _seen[key] = now
            # Prune oldest entries when the URL set grows past the cap so
            # the daemon never accumulates an unbounded dict over days.
            # Mutate in place so the enclosing closure stays valid.
            if len(_seen) > _SEEN_MAX:
                cutoff = sorted(_seen.values())[len(_seen) - _SEEN_MAX]
                for u in [u for u, t in _seen.items() if t < cutoff]:
                    _seen.pop(u, None)

            search_q = _search_query(url)
            act = _build_activity(tab, search_q)

            # Save full page content to local cache for rich summary queries
            if body and len(body) > 60 and tab.domain:
                try:
                    from ..memory.content_cache import ContentCache
                    _cc = ContentCache(data_dir / "content_cache")
                    _cc.save(url, title, body, app_name, ts=now)
                except Exception:
                    pass

            parts: list[str] = []
            if search_q:
                parts.append(f"Searched: {search_q}")
            if title:
                parts.append(title)
            if tab.domain:
                parts.append(f"Site: {tab.domain}")
            if body and len(body) > 60:
                # Use first 3000 chars for memory text (rest is in cache)
                parts.append(body[:3000])
            text = "\n".join(filter(None, parts))

            print(f"[browser] {app_name}: {act} — {title[:60]}", flush=True)
            await process(text, "browser", app_name, title, "", act)

        while True:
            await asyncio.sleep(ACTIVE_POLL)
            if _is_capture_paused():
                continue
            if _is_lite_mode():
                continue
            loop = asyncio.get_event_loop()
            try:
                front = await loop.run_in_executor(None, get_frontmost_context)
                app_name = canonical_browser_name(front.app_name, front.bundle_id)
                if not app_name:
                    continue

                title, url = await loop.run_in_executor(
                    None, lambda a=app_name: _active_tab(a),
                )
                if not url:
                    continue

                now = time.time()
                body = ""
                if _seen.get(_norm_url(url), 0) < now - 30:
                    parsed_domain = _parse_browser_url(title, url).domain
                    body = await loop.run_in_executor(
                        None,
                        lambda a=app_name, d=parsed_domain: _page_body(a, d),
                    )
                await _capture(app_name, title, url, body, DEDUP_ACTIVE)
            except Exception:
                pass

    # One-shot content-cache cleanup at startup (runs off-loop so it doesn't
    # delay the first captures). Prunes day-directories older than max_days.
    _cache_max_days = int(mem_cfg.get("content_cache_max_days", 30))

    async def _cleanup_content_cache():
        await asyncio.sleep(10)  # let captures start first
        loop = asyncio.get_event_loop()
        try:
            from ..memory.content_cache import ContentCache
            cc = ContentCache(data_dir / "content_cache")
            removed = await loop.run_in_executor(
                None, lambda: cc.cleanup_old_days(_cache_max_days)
            )
            if removed:
                print(f"[cache] pruned {removed} old day-dir(s) (max {_cache_max_days} days)", flush=True)
        except Exception as exc:
            print(f"[cache] cleanup error: {exc}", flush=True)

    # ── Memory retention loop ─────────────────────────────────────────────────
    # Prune memories older than memory.max_age_days (0 = keep forever, default).
    # Bounds unbounded growth of both the database and the in-RAM search index
    # for users who want a rolling window instead of a permanent archive.
    _max_age_days = float(mem_cfg.get("max_age_days", 0) or 0)

    async def _retention_loop():
        if _max_age_days <= 0:
            return
        loop = asyncio.get_event_loop()
        await asyncio.sleep(30)  # let startup settle
        while True:
            try:
                cutoff = time.time() - _max_age_days * 86400.0
                pruned = await loop.run_in_executor(
                    None, lambda: store.prune_older_than(cutoff)
                )
                for mid in pruned:
                    cache.remove(mid)
                if pruned:
                    print(
                        f"[retention] pruned {len(pruned)} memories older than "
                        f"{_max_age_days:g} days",
                        flush=True,
                    )
            except Exception as exc:
                print(f"[retention] error: {exc}", flush=True)
            await asyncio.sleep(6 * 3600)  # re-check every 6 hours

    async def _sensitivity_recheck_loop():
        """Drain the deferred sensitivity re-check queue when the model is free.

        The capture path never blocks on the AI sensitivity layer, so a busy
        model means a capture can land in the plain store unchecked. This loop
        re-runs those checks and re-vaults (or drops) anything the model flags,
        making the privacy check eventually consistent instead of fail-open.
        """
        from ..privacy.recheck import recheck_sensitivity
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(30.0)
            checked = 0
            # Bound work per wake so a long backlog cannot monopolize the model.
            while _recheck_queue and checked < 5:
                mid = _recheck_queue.popleft()
                try:
                    outcome = await loop.run_in_executor(
                        None, recheck_sensitivity, store, cache, vault, mid,
                    )
                except Exception as exc:
                    print(f"[sens-recheck] #{mid} error: {exc}", flush=True)
                    continue
                if outcome == "deferred":
                    # Model is busy again; put it back, wait for the next wake.
                    _recheck_queue.appendleft(mid)
                    break
                checked += 1
                if outcome == "vaulted":
                    _bump_sens("revaulted")
                    print(
                        f"[sens-recheck] #{mid} flagged by deferred AI check — moved to vault",
                        flush=True,
                    )
                elif outcome == "dropped":
                    _bump_sens("missed")
                    print(
                        f"[sens-recheck] #{mid} flagged but vault is locked — dropped. "
                        "Run corenous-ai vault unlock to keep future flagged captures.",
                        flush=True,
                    )

    # ── Performance budget watchdog ───────────────────────────────────────────
    # corenous states a background footprint contract for the 8 GB Mac it runs
    # on (see config `performance`). This watchdog only OBSERVES: it samples the
    # daemon's own RSS and CPU and logs a warning when sustained over budget. It
    # never changes capture behavior on its own — that stays a user decision via
    # lite_mode / settings.
    perf_cfg = cfg.get("performance", {})
    _perf_enabled = bool(perf_cfg.get("budget_enabled", True))
    _perf_ram_mb = float(perf_cfg.get("ram_budget_mb", 3072))
    _perf_cpu_pct = float(perf_cfg.get("cpu_budget_percent", 25.0))
    _perf_sample_s = max(5.0, float(perf_cfg.get("sample_interval_seconds", 30.0)))
    _perf_sustained = max(1, int(perf_cfg.get("sustained_samples", 3)))

    def _read_rss_mb(pid: int) -> float | None:
        """Current resident set size in MB via `ps` (no psutil dependency)."""
        import subprocess as _sp
        try:
            out = _sp.run(
                ["ps", "-o", "rss=", "-p", str(pid)],
                capture_output=True, text=True, timeout=2.0, check=False,
            )
            kb = int((out.stdout or "0").strip() or "0")
            return kb / 1024.0
        except Exception:
            return None

    async def _perf_watchdog():
        if not _perf_enabled:
            return
        import resource
        pid = os.getpid()
        ncpu = max(1, os.cpu_count() or 1)
        await asyncio.sleep(_perf_sample_s)  # let startup settle before sampling
        prev: tuple[float, float] | None = None
        ram_breaches = cpu_breaches = 0
        ram_warned = cpu_warned = False
        while True:
            rss_mb = _read_rss_mb(pid)
            ru = resource.getrusage(resource.RUSAGE_SELF)
            cpu_s = ru.ru_utime + ru.ru_stime
            wall = time.time()
            # CPU as a share of total machine capacity (all cores), so the
            # budget reads the same regardless of core count.
            cpu_pct = None
            if prev is not None:
                dw = wall - prev[0]
                if dw > 0:
                    cpu_pct = 100.0 * (cpu_s - prev[1]) / dw / ncpu
            prev = (wall, cpu_s)

            if rss_mb is not None and rss_mb > _perf_ram_mb:
                ram_breaches += 1
                if ram_breaches >= _perf_sustained and not ram_warned:
                    print(
                        f"[perf] RAM over budget: {rss_mb:.0f} MB > "
                        f"{_perf_ram_mb:.0f} MB for {ram_breaches} samples",
                        flush=True,
                    )
                    ram_warned = True
            else:
                ram_breaches = 0
                ram_warned = False

            if cpu_pct is not None and cpu_pct > _perf_cpu_pct:
                cpu_breaches += 1
                if cpu_breaches >= _perf_sustained and not cpu_warned:
                    print(
                        f"[perf] CPU over budget: {cpu_pct:.0f}% > "
                        f"{_perf_cpu_pct:.0f}% for {cpu_breaches} samples",
                        flush=True,
                    )
                    cpu_warned = True
            else:
                cpu_breaches = 0
                cpu_warned = False

            await asyncio.sleep(_perf_sample_s)

    workers = [
        run_clipboard(),
        run_window(),
        run_screen(),
        run_browser_scanner(),
        _sensitivity_recheck_loop(),
        _cleanup_content_cache(),
        _retention_loop(),
        _perf_watchdog(),
    ]
    if refine_queue is not None:
        print(
            f"[daemon] refine queue on (max {refine_max}, newest-first)",
            flush=True,
        )
        workers.append(refine_worker())

        async def _do_backfill():
            # One-shot: requeue recent captures whose AI passes never ran.
            # Wait briefly so live captures get priority slots in the queue.
            await asyncio.sleep(2.0)
            if _is_lite_mode():
                return
            await backfill_pending_ai(limit=80)

        workers.append(_do_backfill())
    print("[corenous daemon] started", flush=True)
    await asyncio.gather(*workers)


@click.command()
@click.option("--data-dir",  default="data",             show_default=True, help="Path to data directory")
@click.option("--config",    default="config/settings.yaml", show_default=True, help="Path to settings.yaml")
def main(data_dir: str, config: str) -> None:
    """Run the memory capture daemon (blocking)."""
    # Headless background process: claim the shared NSApplication and mark it
    # Prohibited BEFORE the Vision/Quartz capture frameworks load, so the
    # daemon never parks a (Python rocket) tile in the Dock.
    try:
        import AppKit
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyProhibited
        )
    except Exception:
        pass

    data_path   = Path(data_dir)
    config_path = Path(config)
    data_path.mkdir(parents=True, exist_ok=True)

    # Rotate logs before we start writing to them so they don't grow unbounded.
    cfg_raw: dict = {}
    try:
        with open(config_path) as f:
            cfg_raw = yaml.safe_load(f) or {}
    except Exception:
        pass
    _log_max_mb = int(cfg_raw.get("monitoring", {}).get("log_max_mb", 10))
    _rotate_log_if_needed(data_path / "daemon.log", _log_max_mb * 1024 * 1024)
    _rotate_log_if_needed(data_path / "daemon.err", _log_max_mb * 1024 * 1024)

    # Write PID file
    pid_file = data_path / "daemon.pid"
    pid_file.write_text(str(os.getpid()))

    def _cleanup(*_):
        pid_file.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT,  _cleanup)

    try:
        asyncio.run(_run(data_path, config_path))
    finally:
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
