"""OpenRouter cloud inference for users who prefer not to run a local model.

Wire-up: the user sets provider + API key + model in the Settings tab. We
persist that to ``~/.corenous/remote.json`` so both the daemon process and
the app process can read the same configuration. The llm wrapper checks
this file on every inference call and routes to OpenRouter when configured;
otherwise it falls back to the local GGUF path.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_CFG_PATH = Path.home() / ".corenous" / "remote.json"
_CFG_CACHE: dict[str, Any] | None = None
_CFG_CACHE_TS: float = 0.0
_CFG_CACHE_TTL = 5.0  # seconds


def remote_config_path() -> Path:
    return _CFG_PATH


def load_remote_config() -> dict[str, Any]:
    """Read ``~/.corenous/remote.json`` with a tiny in-process cache.

    Returns a dict with keys: ``provider`` (``local`` | ``openrouter``),
    ``openrouter_api_key``, ``openrouter_model``. Missing file → defaults.
    """
    global _CFG_CACHE, _CFG_CACHE_TS
    now = time.time()
    if _CFG_CACHE is not None and (now - _CFG_CACHE_TS) < _CFG_CACHE_TTL:
        return _CFG_CACHE
    cfg: dict[str, Any] = {
        "provider": "local",
        "openrouter_api_key": "",
        "openrouter_model": "google/gemini-2.0-flash-exp:free",
    }
    try:
        if _CFG_PATH.is_file():
            with _CFG_PATH.open() as f:
                disk = json.load(f)
            if isinstance(disk, dict):
                cfg.update({k: v for k, v in disk.items() if v is not None})
    except Exception:
        pass
    # Environment override (handy for power users / CI)
    env_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if env_key and not cfg.get("openrouter_api_key"):
        cfg["openrouter_api_key"] = env_key
    _CFG_CACHE = cfg
    _CFG_CACHE_TS = now
    return cfg


def save_remote_config(cfg: dict[str, Any]) -> None:
    """Write the user's settings to ``~/.corenous/remote.json`` (atomic-ish)."""
    global _CFG_CACHE, _CFG_CACHE_TS
    _CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    safe = {
        "provider": str(cfg.get("provider") or "local").strip().lower(),
        "openrouter_api_key": str(cfg.get("openrouter_api_key") or "").strip(),
        "openrouter_model": str(
            cfg.get("openrouter_model") or "google/gemini-2.0-flash-exp:free"
        ).strip(),
    }
    tmp = _CFG_PATH.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(safe, f, indent=2)
    tmp.replace(_CFG_PATH)
    try:
        os.chmod(_CFG_PATH, 0o600)
    except Exception:
        pass
    _CFG_CACHE = safe
    _CFG_CACHE_TS = time.time()


def is_remote_active() -> bool:
    cfg = load_remote_config()
    return (
        cfg.get("provider") == "openrouter"
        and bool((cfg.get("openrouter_api_key") or "").strip())
        and bool((cfg.get("openrouter_model") or "").strip())
    )


# Pre-vetted OpenRouter models. The "free" suffix means OpenRouter's free
# tier covers it (rate-limited but no per-token charge); the rest bill by
# token at the rate shown on openrouter.ai/models.
RECOMMENDED_MODELS: list[tuple[str, str]] = [
    ("google/gemini-2.0-flash-exp:free",  "Gemini 2.0 Flash (free)"),
    ("meta-llama/llama-3.3-70b-instruct:free", "Llama 3.3 70B (free)"),
    ("qwen/qwen-2.5-72b-instruct:free",   "Qwen 2.5 72B (free)"),
    ("anthropic/claude-3.5-haiku",        "Claude 3.5 Haiku (paid)"),
    ("openai/gpt-4o-mini",                "GPT 4o mini (paid)"),
    ("anthropic/claude-3.5-sonnet",       "Claude 3.5 Sonnet (paid)"),
]


def openrouter_chat(
    prompt: str,
    *,
    max_tokens: int = 400,
    temperature: float = 0.1,
    stop: list[str] | None = None,
    timeout_s: float = 60.0,
) -> str:
    """One blocking chat completion against OpenRouter. Returns text or ''."""
    cfg = load_remote_config()
    key = (cfg.get("openrouter_api_key") or "").strip()
    model = (cfg.get("openrouter_model") or "").strip()
    if not key or not model:
        return ""
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
    }
    if stop:
        body["stop"] = list(stop)[:4]
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://corenous.local",
        "X-Title": "Corenous",
    }
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return ""
    except Exception:
        return ""
    try:
        choices = data.get("choices") or []
        if not choices:
            return ""
        msg = (choices[0].get("message") or {}).get("content") or ""
        return msg.strip()
    except Exception:
        return ""
