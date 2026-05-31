"""
Local LLM runtime — now a thin shim over the MLX Qwen2.5-VL model in
:mod:`src.ai.vision`.

corenous used to run a separate GGUF text model (Llama 3.2 3B via
llama-cpp-python) alongside the vision model. On an 8 GB Mac that meant two sets
of weights fighting for the Metal working set, and the text model produced the
weaker summaries. The vision model handles text-only prompts perfectly well, so
this module no longer loads any GGUF: every text job (Q&A, digests, sensitivity
checks, non screenshot summaries) is routed to the single VL brain via
:func:`src.ai.vision.infer_text`.

The public surface is unchanged so existing callers (summarizer, daemon, CLI,
menu bar app) keep working without edits. ``configure_local_llm`` /
``ensure_model_ready`` / ``load_model_sync`` delegate to the vision runtime, and
``_ready`` is the *same* Event the vision runtime sets, so status UI reflects the
VL model's readiness. The OpenRouter cloud path is preserved untouched.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import vision

# Kept so the Settings UI (overlay imports ``_PRESETS as LLM_PRESETS``) still has
# something to render. There is now exactly one local engine: the VL model.
_PRESETS: dict[str, dict[str, Any]] = {
    "qwen2.5-vl-3b": {
        "label": "Qwen2.5-VL 3B",
        "size_blurb": "~3 GB",
        "chat_stops": [],
    },
}
_DEFAULT_PRESET = "qwen2.5-vl-3b"
_MODEL_LABEL = _PRESETS[_DEFAULT_PRESET]["label"]

# ``_ready`` IS the vision runtime's readiness Event — same object, so callers
# that do ``from .llm import _ready`` observe the VL model loading/ready state.
_ready = vision._ready

# Loading-started sentinel for the status UI (overlay treats ``_llm is not None``
# as "loading" until ``_ready`` is set). Stays None until a load is kicked off.
_llm: object | None = None


def _ai_log(msg: str) -> None:
    """Quiet by default; enable with CORENOUS_VERBOSE=1."""
    if os.environ.get("CORENOUS_VERBOSE", "").strip() == "1":
        print(msg, flush=True)


def chat_stop_sequences() -> list[str]:
    """Stop strings for the active chat template. The VL chat template handles
    end-of-turn internally, so none are needed."""
    return []


def model_path() -> Path:
    """Resolved path to the active model directory (the VL weights)."""
    return vision.model_dir()


def model_status_label() -> str:
    """Short human label for status UI."""
    return _MODEL_LABEL


def configure_local_llm(
    config_path: Path | None = None,
    *,
    cfg: dict[str, Any] | None = None,
) -> None:
    """Configure the local runtime. Now a passthrough to the vision runtime —
    the text GGUF is gone, so there is nothing else to configure here."""
    vision.configure_vision()


def download_model_if_missing() -> bool:
    """No GGUF to download anymore. The VL weights are managed separately
    (see :mod:`src.ai.vision`). Returns True when the VL weights are present."""
    return vision.vision_available()


def ensure_model_ready() -> None:
    """Kick off the VL model load (idempotent, non-blocking)."""
    global _llm
    _llm = _llm or object()  # mark "loading" for the status UI
    vision.ensure_vision_ready()


def load_model_sync(timeout: float = 120.0) -> bool:
    """Start loading (if not already) and block until ready or timeout."""
    global _llm
    _llm = _llm or object()
    return vision.load_vision_sync(timeout=timeout)


def infer(prompt: str, max_tokens: int = 80, stop: list[str] | None = None) -> str:
    """Blocking inference. Routes to OpenRouter if the user configured a cloud
    provider in Settings; otherwise runs text-only on the local VL model.
    Returns empty string on any failure (caller handles fallbacks).

    ``stop`` is accepted for backward compatibility but ignored locally — the VL
    chat template manages stopping itself."""
    try:
        from .remote_llm import is_remote_active, openrouter_chat
        if is_remote_active():
            return openrouter_chat(prompt, max_tokens=max_tokens, stop=stop)
    except Exception:
        pass
    return vision.infer_text(prompt, max_tokens=max_tokens)


def infer_stream(
    prompt: str,
    on_token,
    max_tokens: int = 256,
    stop: list[str] | None = None,
):
    """Streaming-style inference. The VL runtime generates in one shot, so this
    runs the full generation then delivers it through ``on_token`` once. Returns
    the final text (also passed as the second arg of the ``on_token`` call)."""
    text = infer(prompt, max_tokens=max_tokens, stop=stop)
    if text:
        try:
            on_token(text, text)
        except Exception:
            pass
    return text


def infer_nowait(prompt: str, max_tokens: int = 40) -> str:
    """Non-blocking inference — returns '' immediately if the model is busy or
    not ready. Used in the capture hot-path so the daemon never stalls behind a
    long VL image summary."""
    try:
        from .remote_llm import is_remote_active, openrouter_chat
        if is_remote_active():
            return openrouter_chat(prompt, max_tokens=max_tokens, timeout_s=10.0)
    except Exception:
        pass
    if not _ready.is_set() or vision.worker_busy():
        return ""
    return vision.infer_text(prompt, max_tokens=max_tokens)
