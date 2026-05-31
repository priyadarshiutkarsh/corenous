"""
Local vision-language runtime — Qwen2.5-VL via mlx-vlm (Apple Metal, MLX).

This is the image-aware counterpart to :mod:`src.ai.llm`. Where ``llm.py`` runs
a text GGUF model on OCR text, this runs a VL model on the actual screenshot,
so the summary is grounded in what was visibly on screen (layout, who said what,
which file changed) instead of a flattened OCR transcript.

MLX GPU streams are thread-affine: arrays and the default stream belong to the
thread that created them. So unlike ``llm.py`` (which loads on a background
thread and infers on the caller thread), every MLX op here — load AND generate —
runs on ONE dedicated worker thread, and callers marshal work to it. That single
worker also serializes inference for free.

Opt-in. On an 8 GB Mac the VL weights (~3 GB) plus the text GGUF (~2 GB) sit
close to the Metal working-set ceiling, so vision stays OFF unless the user sets
``CORENOUS_VISION=1`` (or passes ``enabled=True`` to :func:`configure_vision`).
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

# Resolution order for the VL model directory:
#   1. $CORENOUS_VL_MODEL_DIR (explicit override)
#   2. ~/.corenous/models/qwen2.5-vl-3b (validated 4-bit Qwen2.5-VL-3B)
_DEFAULT_VL_DIR = Path.home() / ".corenous" / "models" / "qwen2.5-vl-3b"

_model: object | None = None
_processor: object | None = None
_config: object | None = None

_ready: threading.Event = threading.Event()
_started = False
_enabled: bool | None = None  # None = auto (on when weights are present)
_model_dir: Path = _DEFAULT_VL_DIR

# Single worker thread that owns ALL MLX/Metal work (see module docstring).
_worker: ThreadPoolExecutor | None = None
_worker_lock = threading.Lock()


def _ai_log(msg: str) -> None:
    if os.environ.get("CORENOUS_VERBOSE", "").strip() == "1":
        print(msg, flush=True)


def _truthy(val: str | None) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")


def _mlx_worker() -> ThreadPoolExecutor:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="corenous-vision")
        return _worker


def configure_vision(*, enabled: bool | None = None, model_dir: str | Path | None = None) -> None:
    """Set whether the VL runtime is active and where its weights live.

    Call before :func:`ensure_vision_ready`. Precedence for enablement:
    explicit ``enabled`` arg > ``CORENOUS_VISION`` env var > auto (on whenever
    the weights are present). The model dir comes from ``CORENOUS_VL_MODEL_DIR``
    or the default models location."""
    global _enabled, _model_dir
    if model_dir is not None:
        _model_dir = Path(str(model_dir)).expanduser()
    else:
        env_dir = os.environ.get("CORENOUS_VL_MODEL_DIR", "").strip()
        _model_dir = Path(env_dir).expanduser() if env_dir else _DEFAULT_VL_DIR
    if enabled is not None:
        _enabled = bool(enabled)
    else:
        flag = os.environ.get("CORENOUS_VISION", "").strip()
        _enabled = _truthy(flag) if flag else None


def model_dir() -> Path:
    """Resolved path to the VL model directory."""
    return _model_dir


def vision_available() -> bool:
    """True if mlx-vlm is importable and the weights are present on disk."""
    if not (_model_dir / "config.json").is_file():
        return False
    try:
        import mlx_vlm  # noqa: F401
    except Exception:
        return False
    return True


def vision_enabled() -> bool:
    """True if vision is active AND the runtime is usable. When enablement was
    left to auto (no explicit setting and no env var), vision is on whenever the
    weights are present on disk."""
    if not vision_available():
        return False
    if _enabled is None:
        flag = os.environ.get("CORENOUS_VISION", "").strip()
        return _truthy(flag) if flag else True
    return _enabled


# ── load + infer (both run on the single MLX worker thread) ──────────────────


def _do_load() -> None:
    global _model, _processor, _config
    try:
        from mlx_vlm import load
        from mlx_vlm.utils import load_config
    except Exception as exc:
        _ai_log(f"[vision] mlx-vlm not installed — vision disabled: {exc}")
        return
    _ai_log(f"[vision] Loading VL model from {_model_dir} …")
    try:
        _model, _processor = load(str(_model_dir))
        _config = load_config(str(_model_dir))
        # Image prefill — not token generation — dominates VL latency, and the
        # processor's default cap (~12.8 MP) leaves our ~1.3 MP screenshots at
        # full resolution. Capping it to ~0.6 MP makes Qwen2.5-VL downscale the
        # image internally, cutting inference from ~36s to ~17s per capture with
        # no measurable loss of quality on screen text.
        try:
            _processor.image_processor.max_pixels = 768 * 28 * 28
        except Exception:
            pass
        _ready.set()
        _ai_log("[vision] VL model ready — image summarization active.")
    except Exception as exc:
        _ai_log(f"[vision] VL model load error: {exc}")


def _do_infer(prompt: str, image_path: str, max_tokens: int) -> str:
    import contextlib
    import io

    try:
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
    except Exception:
        return ""
    try:
        full_prompt = apply_chat_template(_processor, _config, prompt, num_images=1)
        # mlx-vlm prints a hardcoded "Prefill" tqdm bar to stderr on long prompts
        # even with verbose=False. Swallow stderr just for this call so it does
        # not spam the daemon logs. Safe here: this runs on the dedicated MLX
        # worker thread and the daemon's own logs go to stdout.
        with contextlib.redirect_stderr(io.StringIO()):
            out = generate(
                _model,
                _processor,
                full_prompt,
                image=[image_path],
                max_tokens=max_tokens,
                temperature=0.0,
                verbose=False,
            )
        return out if isinstance(out, str) else getattr(out, "text", str(out))
    except Exception as exc:
        _ai_log(f"[vision] inference error: {exc}")
        return ""


# ── public API ───────────────────────────────────────────────────────────────


def ensure_vision_ready() -> None:
    """Kick off background load on the MLX worker (idempotent, non-blocking).
    No-op if vision is disabled or the runtime is unavailable."""
    global _started
    if _started or not vision_enabled():
        return
    _started = True
    _mlx_worker().submit(_do_load)


def load_vision_sync(timeout: float = 120.0) -> bool:
    """Start loading (if enabled) and block until ready or timeout."""
    ensure_vision_ready()
    return _ready.wait(timeout=timeout)


def is_ready() -> bool:
    return _ready.is_set()


def vision_infer(prompt: str, image_path: str, max_tokens: int = 320, timeout: float = 90.0) -> str:
    """Run the VL model on one image with a text prompt, on the MLX worker
    thread. Blocks until the result is ready (or ``timeout``). Returns '' on any
    failure so the caller can fall back to the text path."""
    if not _ready.is_set():
        return ""
    if not image_path or not Path(image_path).is_file():
        return ""
    fut: Future = _mlx_worker().submit(_do_infer, prompt, image_path, max_tokens)
    try:
        return fut.result(timeout=timeout)
    except Exception as exc:
        _ai_log(f"[vision] inference dispatch error: {exc}")
        return ""
