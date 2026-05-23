"""Optional remote LLM for Summary chat (Groq free tier).

Runs inference on Groq's servers so your Mac does not load a second
local model. Set ``GROQ_API_KEY`` in the environment (see
``config/settings.yaml`` → ``chat_summary``) and keep ``provider`` on
``auto`` or ``groq``. If the key is missing or the request fails, callers
fall back to the local Gemma path in ``summarizer``."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def load_chat_summary_config(config_path: Path | None) -> dict[str, Any]:
    """Return ``chat_summary`` block from settings, or defaults."""
    defaults: dict[str, Any] = {
        "provider": "auto",
        "groq_model": "llama-3.1-8b-instant",
        "groq_api_key_env": "GROQ_API_KEY",
        "max_tokens": 900,
    }
    if not config_path or not config_path.is_file():
        return defaults
    try:
        import yaml

        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        block = cfg.get("chat_summary") or {}
        out = {**defaults, **block}
        return out
    except Exception:
        return defaults


def _groq_api_key(cfg: dict[str, Any]) -> str | None:
    env_name = str(cfg.get("groq_api_key_env") or "GROQ_API_KEY")
    key = os.environ.get(env_name, "").strip()
    if key:
        return key
    # Optional inline key for power users (not recommended in git).
    inline = str(cfg.get("groq_api_key") or "").strip()
    return inline or None


def groq_should_run(cfg: dict[str, Any]) -> bool:
    prov = str(cfg.get("provider") or "auto").lower().strip()
    if prov == "groq":
        return _groq_api_key(cfg) is not None
    if prov == "auto":
        return _groq_api_key(cfg) is not None
    return False


def groq_chat_completion(
    *,
    system: str,
    user: str,
    model: str,
    api_key: str,
    max_tokens: int = 900,
    temperature: float = 0.25,
    timeout_s: float = 75.0,
) -> str:
    """One non-streaming chat completion. Returns assistant text or ``''``."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": int(max_tokens),
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        _GROQ_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    choices = payload.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    return str(msg.get("content") or "").strip()


def try_groq_recap(
    *,
    question: str,
    memory_log: str,
    config_path: Path | None,
) -> str | None:
    """If Groq is configured and reachable, return a recap string; else ``None``."""
    cfg = load_chat_summary_config(config_path)
    if not groq_should_run(cfg):
        return None
    key = _groq_api_key(cfg)
    if not key:
        return None
    model = str(cfg.get("groq_model") or "llama-3.1-8b-instant")
    max_tokens = int(cfg.get("max_tokens") or 900)
    system = (
        "You are a precise personal memory assistant. You only use facts from "
        "the MEMORY LOG the user provides. Write rich, concrete prose. Never "
        "invent apps, sites, or actions that are not supported by the log. "
        "No markdown, no bullet lists, no numbered lists. Do not use hyphens "
        "or em dashes as punctuation (use commas or periods). "
        "Cite memory ids as [#123] after the claims they support when possible."
    )
    user = f"USER QUESTION:\n{question}\n\nMEMORY LOG:\n{memory_log}\n\n"
    user += (
        "Write a proper day recap answering the question. Use 5 to 9 sentences "
        "and 160 to 260 words. Open with the time span covered (first and last "
        "timestamps you see in the log). Then group themes: web reading, coding, "
        "messaging, and other apps, with specific names from the log. Close with "
        "one sentence on what dominated their attention if that is clear from the log."
    )
    try:
        out = groq_chat_completion(
            system=system,
            user=user,
            model=model,
            api_key=key,
            max_tokens=max_tokens,
            temperature=0.22,
        )
        return out or None
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, KeyError, ValueError):
        return None
