"""
Local LLM runtime — GGUF models via llama-cpp-python (Metal on Apple Silicon).

The default model is chosen in ``config/settings.yaml`` under ``local_llm``:
presets download from Hugging Face on first use (no API key; public repos only).
All inference is serialized through a single lock. Loading runs in a background
thread so the daemon starts quickly and can fall back to heuristics while
weights are still loading.

Call :func:`configure_local_llm` once per process before :func:`ensure_model_ready`
(usually from the daemon, the menu bar app, or :class:`~src.cli.context.AppContext`).
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

# ── Presets: repo + file + chat stop strings for create_chat_completion ─────

_PRESETS: dict[str, dict[str, Any]] = {
    "gemma-3-4b": {
        "hf_repo_id": "lmstudio-community/gemma-3-4b-it-GGUF",
        "gguf_filename": "gemma-3-4b-it-Q4_K_M.gguf",
        "chat_stops": ["<end_of_turn>"],
        "label": "Gemma 3 4B Instruct",
        "size_blurb": "~2.5 GB",
    },
    "llama-3.2-3b": {
        "hf_repo_id": "bartowski/Llama-3.2-3B-Instruct-GGUF",
        "gguf_filename": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "chat_stops": ["<|eot_id|>"],
        "label": "Llama 3.2 3B Instruct",
        "size_blurb": "~2.0 GB",
    },
    "phi-4-mini": {
        "hf_repo_id": "unsloth/Phi-4-mini-instruct-GGUF",
        "gguf_filename": "Phi-4-mini-instruct-Q4_K_M.gguf",
        "chat_stops": ["<|end|>", "<|endoftext|>"],
        "label": "Phi-4 Mini Instruct",
        "size_blurb": "~2.5 GB",
    },
    # Qwen2.5 7B Instruct is the recommended default: Apache 2.0 license, strong
    # instruction following, much better at writing clean English (fixes obvious
    # OCR typos when paraphrasing), reliable JSON output. ~4.4 GB Q4_K_M — heavier
    # than 3B presets but the GPU offload keeps it responsive on Apple Silicon.
    "qwen2.5-7b": {
        "hf_repo_id": "bartowski/Qwen2.5-7B-Instruct-GGUF",
        "gguf_filename": "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        "chat_stops": ["<|im_end|>", "<|endoftext|>"],
        "label": "Qwen2.5 7B Instruct",
        "size_blurb": "~4.4 GB",
    },
}

_DEFAULT_PRESET = "llama-3.2-3b"

_MODEL_DIR = Path.home() / ".corenous" / "models"
_REPO_ID: str = _PRESETS[_DEFAULT_PRESET]["hf_repo_id"]
_FILENAME: str = _PRESETS[_DEFAULT_PRESET]["gguf_filename"]
_CHAT_STOPS: list[str] = list(_PRESETS[_DEFAULT_PRESET]["chat_stops"])
_MODEL_LABEL: str = _PRESETS[_DEFAULT_PRESET]["label"]
_SIZE_BLURB: str = _PRESETS[_DEFAULT_PRESET]["size_blurb"]
_N_CTX = 8192
_N_GPU_LAYERS = -1
_N_THREADS = 4
_N_BATCH = 256

_llm: object | None = None
_lock: threading.Lock = threading.Lock()
_ready: threading.Event = threading.Event()
_started = False
_configure_done = False


def _ai_log(msg: str) -> None:
    """Quiet by default; enable with CORENOUS_VERBOSE=1."""
    if os.environ.get("CORENOUS_VERBOSE", "").strip() == "1":
        print(msg, flush=True)


def _model_path() -> Path:
    return _MODEL_DIR / _FILENAME


def chat_stop_sequences() -> list[str]:
    """Stop strings for the active chat template (used by summarizer)."""
    return list(_CHAT_STOPS)


def model_path() -> Path:
    """Resolved path to the active GGUF file."""
    return _model_path()


def model_status_label() -> str:
    """Short human label for status UI (preset name, not file path)."""
    return _MODEL_LABEL


def configure_local_llm(
    config_path: Path | None = None,
    *,
    cfg: dict[str, Any] | None = None,
) -> None:
    """Apply ``local_llm`` from settings. Call before weights finish loading;
    once the model is ready, further calls are ignored (restart to switch)."""
    global _configure_done, _REPO_ID, _FILENAME, _CHAT_STOPS, _MODEL_LABEL
    global _SIZE_BLURB, _N_CTX, _N_GPU_LAYERS, _N_THREADS, _N_BATCH, _MODEL_DIR

    if _ready.is_set():
        _ai_log(
            "[ai] configure_local_llm ignored (model already loaded); "
            "restart app and daemon to switch models.",
        )
        return

    data: dict[str, Any] = {}
    if cfg is not None:
        data = dict(cfg.get("local_llm") or {})
    elif config_path is not None and Path(config_path).is_file():
        try:
            import yaml

            with open(config_path) as f:
                root = yaml.safe_load(f) or {}
            data = dict(root.get("local_llm") or {})
        except Exception:
            data = {}

    # User override from the Settings tab (lives in the store config table).
    # Falls back to settings.yaml. Read from the same SQLite DB the app uses.
    override = ""
    try:
        import sqlite3 as _sql
        db = Path.home() / ".." / "corenous" / "data" / "memories.db"
        # Try the project-relative path first, then env var.
        cand = [
            Path.cwd() / "data" / "memories.db",
            Path(os.environ.get("CORENOUS_DATA_DIR", "")) / "memories.db"
            if os.environ.get("CORENOUS_DATA_DIR") else None,
        ]
        for c in cand:
            if c is not None and c.is_file():
                conn = _sql.connect(str(c))
                try:
                    row = conn.execute(
                        "SELECT value FROM config WHERE key = 'local_llm_preset'"
                    ).fetchone()
                    if row and row[0]:
                        override = str(row[0]).strip().lower()
                finally:
                    conn.close()
                break
    except Exception:
        pass

    preset = (override or str(data.get("preset") or _DEFAULT_PRESET)).strip().lower()
    if preset == "custom":
        repo = str(data.get("hf_repo_id") or "").strip()
        fn = str(data.get("gguf_filename") or "").strip()
        stops_raw = data.get("chat_stop_sequences")
        if not repo or not fn:
            _ai_log(
                "[ai] local_llm preset custom needs hf_repo_id and gguf_filename; "
                f"falling back to {_DEFAULT_PRESET}.",
            )
            preset = _DEFAULT_PRESET
        else:
            _REPO_ID = repo
            _FILENAME = fn
            if isinstance(stops_raw, list) and stops_raw:
                _CHAT_STOPS = [str(s) for s in stops_raw if str(s).strip()]
            else:
                _CHAT_STOPS = ["<|eot_id|>"]
            _MODEL_LABEL = str(data.get("label") or "Custom GGUF").strip() or "Custom GGUF"
            _SIZE_BLURB = str(data.get("size_blurb") or "see Hugging Face").strip()
            _apply_hw_tuning(data)
            _maybe_set_model_dir(data)
            _configure_done = True
            return

    if preset not in _PRESETS:
        _ai_log(f"[ai] unknown local_llm preset {preset!r}; using {_DEFAULT_PRESET}.")
        preset = _DEFAULT_PRESET

    p = _PRESETS[preset]
    _REPO_ID = p["hf_repo_id"]
    _FILENAME = p["gguf_filename"]
    _CHAT_STOPS = list(p["chat_stops"])
    _MODEL_LABEL = p["label"]
    _SIZE_BLURB = p["size_blurb"]
    _apply_hw_tuning(data)
    _maybe_set_model_dir(data)
    _configure_done = True


def _apply_hw_tuning(data: dict[str, Any]) -> None:
    global _N_CTX, _N_GPU_LAYERS, _N_THREADS, _N_BATCH
    try:
        _N_CTX = int(data.get("n_ctx", _N_CTX))
    except (TypeError, ValueError):
        pass
    try:
        _N_GPU_LAYERS = int(data.get("n_gpu_layers", _N_GPU_LAYERS))
    except (TypeError, ValueError):
        pass
    try:
        _N_THREADS = int(data.get("n_threads", _N_THREADS))
    except (TypeError, ValueError):
        pass
    try:
        _N_BATCH = int(data.get("n_batch", _N_BATCH))
    except (TypeError, ValueError):
        pass


def _maybe_set_model_dir(data: dict[str, Any]) -> None:
    global _MODEL_DIR
    raw = data.get("model_dir")
    if raw is None or str(raw).strip() == "":
        _MODEL_DIR = Path.home() / ".corenous" / "models"
        return
    p = Path(str(raw)).expanduser()
    _MODEL_DIR = p


# ── download ─────────────────────────────────────────────────────────────────


def _download() -> bool:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        _ai_log("[ai] huggingface-hub not installed — skipping model download")
        return False

    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    _ai_log(f"[ai] Downloading {_FILENAME} ({_SIZE_BLURB}) to {_MODEL_DIR} …")
    try:
        hf_hub_download(
            repo_id=_REPO_ID,
            filename=_FILENAME,
            local_dir=str(_MODEL_DIR),
        )
        _ai_log("[ai] Download complete.")
        return True
    except Exception as exc:
        _ai_log(f"[ai] Download failed: {exc}")
        return False


def download_model_if_missing() -> bool:
    """
    Ensure the GGUF file exists on disk (Hugging Face Hub). Does not load weights.
    Returns True if the file is present after the call.
    """
    if _model_path().exists():
        return True
    return _download()


# ── load ─────────────────────────────────────────────────────────────────────


def _load_worker() -> None:
    global _llm

    if not _configure_done:
        configure_local_llm()

    if not _model_path().exists():
        if not _download():
            return

    try:
        from llama_cpp import Llama
    except ImportError:
        _ai_log("[ai] llama-cpp-python not installed — AI summarization disabled")
        return

    _ai_log(f"[ai] Loading {_MODEL_LABEL} (Metal) …")
    try:
        model = Llama(
            model_path=str(_model_path()),
            n_gpu_layers=_N_GPU_LAYERS,
            n_ctx=_N_CTX,
            n_threads=_N_THREADS,
            n_batch=_N_BATCH,
            verbose=False,
        )
        with _lock:
            _llm = model
        _ready.set()
        _ai_log("[ai] Model ready — AI summarization active.")
    except Exception as exc:
        _ai_log(f"[ai] Model load error: {exc}")


# ── public API ────────────────────────────────────────────────────────────────


def ensure_model_ready() -> None:
    """Kick off background download + load (idempotent, non-blocking)."""
    global _started
    if _started:
        return
    _started = True
    t = threading.Thread(target=_load_worker, daemon=True, name="corenous-ai-loader")
    t.start()


def load_model_sync(timeout: float = 120.0) -> bool:
    """Start loading (if not already) and block until ready or timeout.
    Returns True if the model is usable. Use ``corenous-ai models download``
    first so load stays fast; keeps waits predictable."""
    ensure_model_ready()
    return _ready.wait(timeout=timeout)


def _default_stops() -> list[str]:
    return list(_CHAT_STOPS)


def _run_chat(prompt: str, max_tokens: int, stop: list[str] | None = None) -> str:
    use_stop = stop if stop is not None else _default_stops()
    kwargs: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }
    if use_stop:
        kwargs["stop"] = use_stop
    out = _llm.create_chat_completion(**kwargs)  # type: ignore[union-attr]
    return out["choices"][0]["message"]["content"].strip()


def infer(prompt: str, max_tokens: int = 80, stop: list[str] | None = None) -> str:
    """Blocking inference. Routes to OpenRouter if the user configured a
    cloud provider in Settings; otherwise uses the local GGUF model.
    Returns empty string on any failure (caller handles fallbacks)."""
    # Cloud path: route to OpenRouter when configured. This bypasses the
    # local model entirely — the user explicitly opted in via Settings.
    try:
        from .remote_llm import is_remote_active, openrouter_chat
        if is_remote_active():
            return openrouter_chat(prompt, max_tokens=max_tokens, stop=stop)
    except Exception:
        pass
    if not _ready.is_set():
        return ""
    try:
        with _lock:
            return _run_chat(prompt, max_tokens, stop)
    except Exception:
        return ""


def infer_stream(
    prompt: str,
    on_token,
    max_tokens: int = 256,
    stop: list[str] | None = None,
):
    """Blocking, streaming inference. Calls ``on_token(piece, accumulated)``
    once per generated chunk so the UI can render the response as it grows.

    Returns the final, full text (also passed as the second arg of the
    last ``on_token`` call). Returns empty string if the model is not
    ready or any error occurs."""
    if not _ready.is_set():
        return ""
    accumulated = ""
    use_stop = stop if stop is not None else _default_stops()
    kwargs: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "stream": True,
    }
    if use_stop:
        kwargs["stop"] = use_stop
    try:
        with _lock:
            iterator = _llm.create_chat_completion(**kwargs)  # type: ignore[union-attr]
            for chunk in iterator:
                try:
                    delta = chunk["choices"][0]["delta"]
                    piece = delta.get("content") or ""
                except Exception:
                    piece = ""
                if not piece:
                    continue
                accumulated += piece
                try:
                    on_token(piece, accumulated)
                except Exception:
                    pass
    except Exception:
        return accumulated.strip()
    return accumulated.strip()


def infer_nowait(prompt: str, max_tokens: int = 40) -> str:
    """Non-blocking inference — returns '' immediately if model is busy or not ready.
    Use in the capture hot-path so the daemon never stalls waiting for the LLM."""
    # When OpenRouter is active, run synchronously (it's a network call, so
    # the local GIL won't be tied up by inference). Short timeout so the
    # capture pipeline still feels non-blocking.
    try:
        from .remote_llm import is_remote_active, openrouter_chat
        if is_remote_active():
            return openrouter_chat(prompt, max_tokens=max_tokens, timeout_s=10.0)
    except Exception:
        pass
    if not _ready.is_set():
        return ""
    acquired = _lock.acquire(blocking=False)
    if not acquired:
        return ""
    try:
        base = _default_stops()
        extra = ["\n\n"]
        use_stop = list(dict.fromkeys(base + extra))
        return _run_chat(prompt, max_tokens, stop=use_stop)
    except Exception:
        return ""
    finally:
        _lock.release()
