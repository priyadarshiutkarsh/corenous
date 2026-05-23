"""
Full-content local cache for pages, emails, and OCR data.
Stored as JSON in data/content_cache/YYYY-MM-DD/
Accessed by the timeline day-brief generator for rich context queries.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path


_PROXY_RE = re.compile(r"\.(ezproxy|proxy|remotexs)\..+$", re.IGNORECASE)
_HYPHEN_DOMAIN_RE = re.compile(r"^www-(.+)-([a-z]{2,6})$")


def _domain_slug(url: str) -> str:
    m = re.search(r"(?:https?://)?(?:www\.)?([A-Za-z0-9._-]+\.[A-Za-z]{2,})", url)
    if not m:
        return "unknown"
    d = _PROXY_RE.sub("", m.group(1).lower())
    # Handle hyphenated proxy domains: www-jstor-org → jstor
    hm = _HYPHEN_DOMAIN_RE.match(d.replace(".", "-"))
    if hm:
        d = f"{hm.group(1)}.{hm.group(2)}"
    parts = d.split(".")
    return parts[-2] if len(parts) >= 2 else d


# ── Site-specific JS extractors ───────────────────────────────────────────────

_SITE_JS: dict[str, str] = {
    "mail.google.com": (
        "(function(){"
        "var rows=document.querySelectorAll('tr.zA');"
        "var emails=[];"
        "rows.forEach(function(r){"
        "var unread=r.classList.contains('zE');"
        "var sender=(r.querySelector('.yP,.zF,.yW span')||{innerText:''}).innerText||'';"
        "var subj=(r.querySelector('.bog span,.y6 span,.bqe')||{innerText:''}).innerText||'';"
        "var preview=(r.querySelector('.y2')||{innerText:''}).innerText||'';"
        "emails.push((unread?'[UNREAD] ':'')+sender+' | '+subj+' | '+preview.substring(0,80));"
        "});"
        "var full=document.body.innerText.substring(0,8000);"
        "return 'EMAILS:\\n'+emails.slice(0,50).join('\\n')+'\\n---\\n'+full;"
        "})()"
    ),
    "youtube.com": (
        "(function(){"
        "var title=document.title;"
        "var ch=(document.querySelector('#channel-name a,#owner-name a')||{innerText:''}).innerText||'';"
        "var desc=(document.querySelector('#description-text,ytd-text-inline-expander')||{innerText:''}).innerText||'';"
        "var related=Array.from(document.querySelectorAll('ytd-compact-video-renderer #video-title')).slice(0,8).map(function(e){return e.innerText;}).join(', ');"
        "return title+'\\nChannel: '+ch+'\\nDescription: '+desc.substring(0,800)+'\\nRelated: '+related;"
        "})()"
    ),
    "github.com": (
        "(function(){"
        "var title=document.title;"
        "var readme=(document.querySelector('#readme article,#wiki-body')||{innerText:''}).innerText||'';"
        "var body=document.body.innerText.substring(0,5000);"
        "return title+'\\nREADME: '+readme.substring(0,2000)+'\\n'+body;"
        "})()"
    ),
    "canvas.instructure.com": (
        "(function(){"
        "var title=document.title;"
        "var content=(document.querySelector('#content,.ic-Layout-contentMain,#wiki_page_show')||{innerText:''}).innerText||'';"
        "var body=document.body.innerText.substring(0,6000);"
        "return title+'\\n'+content.substring(0,4000)+'\\n'+body.substring(0,2000);"
        "})()"
    ),
}

_GENERIC_JS = (
    "document.body?"
    "document.body.innerText.replace(/\\s+/g,' ').trim().substring(0,8000):''"
)


_CANVAS_JS = _SITE_JS["canvas.instructure.com"]

def get_site_js(domain: str) -> str:
    for d, js in _SITE_JS.items():
        if d in domain:
            return js
    # Canvas subdomains (canvas.wisc.edu, canvas.harvard.edu, etc.)
    if domain.startswith("canvas.") or ".canvas." in domain:
        return _CANVAS_JS
    return _GENERIC_JS


# ── Cache class ───────────────────────────────────────────────────────────────

class ContentCache:
    def __init__(self, cache_dir: Path):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _day_dir(self, ts: float) -> Path:
        d = self.dir / datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        d.mkdir(exist_ok=True)
        return d

    def save(
        self,
        url: str,
        title: str,
        content: str,
        app_name: str = "",
        ts: float | None = None,
    ) -> None:
        ts = ts or time.time()
        slug = _domain_slug(url)
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:10]
        fname = f"{int(ts)}_{slug}_{url_hash}.json"
        path = self._day_dir(ts) / fname
        data = {
            "url": url,
            "title": title,
            "domain": slug,
            "app": app_name,
            "ts": ts,
            "content": content[:30000],
        }
        path.write_text(json.dumps(data, ensure_ascii=False))

    def query_domain(self, domain: str, days_back: int = 7, limit: int = 60) -> list[dict]:
        results: list[dict] = []
        for i in range(days_back):
            ts = time.time() - i * 86400
            day_dir = self.dir / datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            if not day_dir.exists():
                continue
            for f in sorted(day_dir.glob(f"*{domain}*"), reverse=True):
                try:
                    results.append(json.loads(f.read_text()))
                    if len(results) >= limit:
                        return results
                except Exception:
                    continue
        return results

    def query_recent(self, days_back: int = 1, limit: int = 200) -> list[dict]:
        results: list[dict] = []
        for i in range(days_back):
            ts = time.time() - i * 86400
            day_dir = self.dir / datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            if not day_dir.exists():
                continue
            for f in sorted(day_dir.glob("*.json"), reverse=True):
                try:
                    results.append(json.loads(f.read_text()))
                    if len(results) >= limit:
                        return results
                except Exception:
                    continue
        return results

    def cleanup_old_days(self, max_days: int = 30) -> int:
        """Delete day-directories older than max_days. Returns number of dirs removed."""
        import shutil
        removed = 0
        cutoff = time.time() - max_days * 86400
        try:
            for day_dir in sorted(self.dir.iterdir()):
                if not day_dir.is_dir():
                    continue
                try:
                    # Directory names are YYYY-MM-DD; parse to timestamp.
                    from datetime import datetime as _dt
                    ts = _dt.strptime(day_dir.name, "%Y-%m-%d").timestamp()
                    if ts < cutoff:
                        shutil.rmtree(day_dir, ignore_errors=True)
                        removed += 1
                except (ValueError, OSError):
                    continue
        except Exception:
            pass
        return removed

    def total_size_bytes(self) -> int:
        """Return approximate total bytes used by the content cache."""
        total = 0
        try:
            for f in self.dir.rglob("*.json"):
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        except Exception:
            pass
        return total

    def search(self, query: str, days_back: int = 7, limit: int = 20) -> list[dict]:
        q_tokens = set(re.findall(r"[a-z]{3,}", query.lower()))
        results: list[tuple[int, dict]] = []
        for entry in self.query_recent(days_back=days_back, limit=500):
            hay = (entry.get("title", "") + " " + entry.get("content", "")).lower()
            score = sum(1 for t in q_tokens if t in hay)
            if score > 0:
                results.append((score, entry))
        results.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in results[:limit]]
