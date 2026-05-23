"""Small text helpers for memory headings and compact row summaries."""
from __future__ import annotations

import re


_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.']*")
_WORD_OR_CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*|\b\d{1,4}\b")
_URL_RE = re.compile(r"(?:https?://)?([A-Za-z0-9.-]+\.[A-Za-z]{2,})(/[^\s?#]*)?")
_DASH_RE = re.compile(r"[-\u2010-\u2015]+")
_SECRET_RE = re.compile(r"\bsk\s+proj\b|\bapi\s+key\b|\bsecret\s+key\b", re.IGNORECASE)
_SCREENSHOT_RE = re.compile(
    r"\bscreenshot\s+\d{4}\s+\d{1,2}\s+\d{1,2}\s+at\s+"
    r"\d{1,2}\s+\d{1,2}(?:\s+\d{1,2})?\s*(?:am|pm)?\s*"
    r"(?:png|jpg|jpeg|heic)?\b",
    re.IGNORECASE,
)
_IMAGE_FILE_RE = re.compile(
    r"\b[\w .()]*\.(?:png|jpg|jpeg|gif|heic|webp|pdf)\b",
    re.IGNORECASE,
)

_STOP_WORDS = {
    "about", "after", "again", "also", "and", "are", "because", "been",
    "being", "can", "com", "could", "did", "does", "doing", "done", "for",
    "from", "have", "here", "into", "just", "like", "more", "not",
    "now", "only", "that", "the", "their", "them", "then", "there",
    "these", "they", "this", "those", "through", "too", "use", "was",
    "were", "what", "when", "where", "which", "with", "would", "you",
    "your", "http", "https", "www", "html", "next", "proj",
    "png", "jpg", "jpeg", "gif", "heic", "webp", "pdf", "screenshot",
    "screenshots", "image", "images", "temporaryitems", "temporaryltems", "screencaptureui",
    "nsird", "item", "page", "corenous", "explorer", "outline", "timeline",
    "sessions", "chat", "screen", "claude", "venv", "config", "conf", "src",
    "scripts", "pyproject", "requirements", "egg", "info", "data", "toml", "txt",
    # web-specific noise
    "official", "site", "home", "welcome", "login", "sign", "free",
    "online", "best", "top", "new", "get", "buy", "shop", "store",
    # common 2-char prepositions / articles (let through: my, we, he, ai, ml, cs)
    "in", "of", "at", "on", "to", "is", "it", "by", "an", "or", "as",
    "do", "so", "if", "up", "no", "vs",
}

# Strip pipe-separated site names (| SiteName) — pipe is always a UI separator
_WEB_TITLE_SUFFIX_RE = re.compile(
    r"\s*\|\s*(?:home|official\s+site|official\s+website|"
    r"google\s+search|google|youtube|reddit|twitter|facebook|"
    r"linkedin|amazon|wikipedia|github|"
    r"search\s+results|results|log\s*in|sign\s+in|"
    r"[A-Z][A-Za-z0-9 .&,]{2,40})\s*$",
    re.IGNORECASE,
)
# For dash-separated: only strip known brand names or generic page-type suffixes
_WEB_TITLE_DASH_SUFFIX_RE = re.compile(
    r"\s*[-–—]\s*(?:home|official\s+site|official\s+website|"
    r"google|youtube|reddit|twitter|facebook|linkedin|amazon|"
    r"wikipedia|github|canvas|instructure|apple|microsoft|netflix|"
    r"spotify|adobe|notion|figma|slack|discord|zoom|dropbox|"
    r"overview|course\s+overview|course\s+page|course\s+info|"
    r"main\s+page|index\s+page|"
    r"search\s+results|results|log\s*in|sign\s+in|"
    r"[A-Za-z\s]+\s+(?:air\s+lines|airlines|airways))\s*$",
    re.IGNORECASE,
)

_SITE_HEADING: dict[str, str] = {
    "youtube.com":       "Watched On YouTube",
    "github.com":        "Browsed GitHub",
    "mail.google.com":   "Read Gmail",
    "docs.google.com":   "Edited Google Docs",
    "sheets.google.com": "Edited Google Sheets",
    "notion.so":         "Worked In Notion",
    "figma.com":         "Designed In Figma",
    "reddit.com":        "Browsed Reddit",
    "twitter.com":       "Browsed Twitter",
    "x.com":             "Browsed Twitter",
    "linkedin.com":      "Browsed LinkedIn",
    "stackoverflow.com": "Read Stack Overflow",
    "wikipedia.org":     "Read Wikipedia",
    "medium.com":        "Read Article",
    "substack.com":      "Read Newsletter",
    "news.ycombinator.com": "Browsed Hacker News",
    "canvas.instructure.com": "Viewed Course Materials",
    "instructure.com":   "Viewed Course Materials",
    "piazza.com":        "Checked Course Forum",
    "gradescope.com":    "Checked Gradescope",
    "chegg.com":         "Read Chegg",
    "coursera.org":      "Studied On Coursera",
    "edx.org":           "Studied On edX",
    "khanacademy.org":   "Studied Khan Academy",
    "overleaf.com":      "Edited In Overleaf",
    "arxiv.org":         "Read Research Paper",
    "scholar.google.com": "Searched Scholar",
    "jstor.org":         "Read Academic Article",
    "chatgpt.com":       "Chatted With ChatGPT",
    "openai.com":        "Browsed OpenAI",
    "perplexity.ai":     "Searched Perplexity",
    "anthropic.com":     "Browsed Anthropic",
}

_NOISE_SUBJECT_WORDS = {
    "captured", "memory", "item", "note", "page", "png", "jpg", "jpeg",
    "gif", "heic", "webp", "pdf", "screenshot", "image", "screen",
    "whole",
}
_NON_URL_TLDS = {
    "png", "jpg", "jpeg", "gif", "heic", "webp", "pdf", "egg", "py",
    "txt", "toml", "yaml", "yml", "json", "md", "db", "sqlite", "log",
    "err", "lock",
}


_BRAND_ONLY_RE = re.compile(
    r"^(?:canvas(?:\s+(?:by\s+)?instructure|\s+lms)?|instructure|"
    r"google|youtube|reddit|twitter|facebook|linkedin|amazon|wikipedia|"
    r"github|notion|figma|slack|discord|zoom|microsoft|apple|"
    r"coursera|edx|piazza|gradescope|overleaf|brightspace|blackboard|"
    r"chegg|khan\s+academy|arxiv|jstor|perplexity|chatgpt|openai|"
    r"netflix|spotify|dropbox|atlassian|confluence|jira|"
    # Airline / travel brands
    r"delta\s+air\s+lines|united\s+airlines?|american\s+airlines?|"
    r"southwest\s+airlines?|jetblue(?:\s+airways)?|spirit\s+airlines?|"
    r"alaska\s+airlines?|british\s+airways|lufthansa|air\s+canada|"
    r"air\s+france|emirates|booking\.com|expedia|airbnb|hotels\.com|"
    # Generic airline pattern
    r"[A-Za-z\s]+\s+(?:air\s+lines|airlines|airways)"
    r")\s*$",
    re.IGNORECASE,
)
_COURSE_CODE_IN_PART_RE = re.compile(r"\b[A-Z]{1,7}\s*\d{2,4}\b")


def _clean_web_title(title: str) -> str:
    """Strip site-name noise from browser page titles."""
    # Split on pipe first and filter brand-name-only segments
    pipe_parts = [p.strip() for p in re.split(r"\s*\|\s*", title) if p.strip()]
    if len(pipe_parts) > 1:
        meaningful = [p for p in pipe_parts if not _BRAND_ONLY_RE.match(p)]
        if meaningful:
            # Always keep the first content part
            result_parts = [meaningful[0]]
            # Enrich subject from remaining pipe segments
            for extra in meaningful[1:]:
                cm = _COURSE_CODE_IN_PART_RE.search(extra)
                if cm:
                    if len(extra) <= 14:
                        result_parts.append(extra)          # short segment: "CS 540"
                    else:
                        result_parts.insert(0, cm.group())  # extract just "CS 537"
                    break
                elif 6 <= len(extra) <= 28:
                    result_parts.append(extra)              # short content: "Question about Homework 4"
                    break
            result = " ".join(result_parts).strip()
            if result:
                return result
    # Fallback: strip known brand/generic terms after dash (conservative)
    t = _WEB_TITLE_DASH_SUFFIX_RE.sub("", title).strip()
    t = _WEB_TITLE_DASH_SUFFIX_RE.sub("", t).strip()
    return t or title


_CAMEL_SPLIT_RE = re.compile(r"([a-z]{2,})([A-Z])")
_ARXIV_ID_RE = re.compile(r"\barXiv\s*:\s*\d{4}\.\d{4,5}\b\s*", re.IGNORECASE)
_DISPLAY_WORDS = {
    "api": "API",
    "ai": "AI",
    "ml": "ML",
    "cs": "CS",
    "ui": "UI",
    "ux": "UX",
    "dsl": "DSL",
    "nyc": "NYC",
    "heic": "HEIC",
    "jpeg": "JPEG",
    "jpg": "JPG",
    "ist": "IST",
    "utc": "UTC",
    "usa": "USA",
    "uk": "UK",
}
_TOPIC_NOISE_WORDS = {
    "asked", "learn", "example", "examples", "intentded", "intended",
    "generalization", "generalisation", "return", "form", "following",
    "small", "searches", "over", "temporary", "history", "train",
    "models", "magic", "control", "stunning", "visuals", "graphics",
    "realistic", "photos", "precision", "write", "edit", "look",
    "something", "safety", "copy", "days",
}


def _web_title_words(title: str, max_words: int = 6) -> str:
    """
    Extract a meaningful subject from a cleaned browser page title.
    Keeps course codes (CS, 540), dept abbreviations (AI, ML), skips noise.
    Splits CamelCase words (MachineLearning → Machine Learning).
    """
    # Strip arXiv paper IDs (keep the actual paper title that follows)
    title = _ARXIV_ID_RE.sub("", title)
    # Split CamelCase before other processing (handles r/MachineLearning etc.)
    # Requires 2+ lowercase chars to avoid splitting "iPhone" → "i Phone"
    title = _CAMEL_SPLIT_RE.sub(r"\1 \2", title)
    title = clean_text(re.sub(r"[._/|:]+", " ", title))
    if not title:
        return ""
    tokens = _WORD_OR_CODE_RE.findall(title)
    result: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        low = tok.lower()
        # Keep ALL-CAPS abbreviations: CS, AI, ML, ECE, JSTOR, ARXIV — up to 6 chars
        if tok.isupper() and 1 <= len(tok) <= 6:
            if low not in seen:
                seen.add(low)
                result.append(tok)
            continue
        # Keep numeric tokens: single digits (hw/module numbers) and 2-4 digit course numbers
        # But skip consecutive digit tokens (artifacts from "3.4" → "3 4")
        if tok.isdigit() and 1 <= len(tok) <= 4:
            if result and result[-1].isdigit():
                continue  # skip "4" after "3" (split decimal artifact)
            result.append(tok)
            continue
        # Skip stop words and single-char tokens
        if len(low) < 2 or low in _STOP_WORDS:
            continue
        # Skip long digit-containing tokens (years like 2026, IDs)
        if any(ch.isdigit() for ch in tok) and len(tok) > 5:
            continue
        if low in seen:
            continue
        seen.add(low)
        result.append(tok)
        if len(result) >= max_words:
            break
    if not result:
        return ""
    words = []
    for w in result[:max_words]:
        low = w.lower()
        if low in _DISPLAY_WORDS:
            words.append(_DISPLAY_WORDS[low])
        elif w.isupper() and len(w) <= 6:
            words.append(w)
        else:
            words.append(w.title())
    joined = " ".join(words)
    joined = re.sub(r"\bGit Hub\b", "GitHub", joined)
    joined = re.sub(r"\bYou Tube\b", "YouTube", joined)
    return joined


def _display_token(token: str, lower: bool = False) -> str:
    low = token.lower()
    if low in _DISPLAY_WORDS:
        return _DISPLAY_WORDS[low]
    if token.isupper() and len(token) <= 7:
        return token
    return low if lower else token.title()


def _natural_topic(text: str, max_words: int = 5) -> str:
    """Extract a short noun phrase that reads naturally after a verb."""
    text = _ARXIV_ID_RE.sub("", text or "")
    text = _CAMEL_SPLIT_RE.sub(r"\1 \2", text)
    text = clean_text(re.sub(r"[._/|:]+", " ", text))
    words: list[str] = []
    seen: set[str] = set()
    for tok in _WORD_OR_CODE_RE.findall(text):
        low = tok.lower()
        if tok.isdigit():
            if len(tok) <= 4 and not (words and words[-1].isdigit()):
                words.append(tok)
            continue
        if low in _DISPLAY_WORDS:
            words.append(_DISPLAY_WORDS[low])
            seen.add(low)
            if len(words) >= max_words:
                break
            continue
        if low in _STOP_WORDS or low in _TOPIC_NOISE_WORDS:
            continue
        if len(low) < 2 or len(low) > 28:
            continue
        if any(ch.isdigit() for ch in tok) and len(tok) > 7:
            continue
        if low in seen:
            continue
        seen.add(low)
        words.append(_display_token(tok, lower=True))
        if len(words) >= max_words:
            break
    return " ".join(words)


def _natural_subject(prefix: str, topic: str = "", max_chars: int = 48, max_words: int = 5) -> str:
    topic = _natural_topic(topic, max_words=max_words) if topic else ""
    phrase = f"{prefix} {topic}".strip()
    if phrase:
        phrase = phrase[0].upper() + phrase[1:]
    return truncate_text(phrase, max_chars)


def _search_subject(query: str, max_chars: int = 48) -> str:
    low = (query or "").lower()
    if "heic" in low and ("jpeg" in low or "jpg" in low):
        return truncate_text("Searched for HEIC to JPEG", max_chars)
    city_m = _FLIGHT_CITY_RE.search(query or "")
    if city_m and "flight" in low:
        return truncate_text(
            f"Searched flights from {city_m.group(1)} to {city_m.group(2)}",
            max_chars,
        )
    return _natural_subject("Searched for", query, max_chars, max_words=5)


def _assistant_name(domain: str, window_title: str) -> str:
    seed = f"{domain} {window_title}".lower()
    if "claude" in seed:
        return "Claude"
    if "perplexity" in seed:
        return "Perplexity"
    if "gemini" in seed:
        return "Gemini"
    return "ChatGPT"


def _ai_topic(body: str, first: str, window_title: str) -> str:
    hay = clean_text(f"{window_title} {body} {first}")
    low = hay.lower()
    if "prompt injection" in low and "sql injection" in low:
        return "prompt and SQL injection"
    if "prompt injection" in low:
        return "prompt injection"
    if "sql injection" in low:
        return "SQL injection"
    if "api boundary" in low:
        return "API boundary mitigation"
    if "trusted instructions" in low and "untrusted user input" in low:
        return "trusted and untrusted input"
    if "program synthesis" in low:
        return "program synthesis homework"
    if "string transformation" in low or "first name" in low:
        return "string transformations"
    if "heic" in low and ("jpeg" in low or "jpg" in low):
        return "HEIC to JPEG conversion"
    if "image" in low and "solve" in low:
        return "image problem solving"
    hay = re.sub(r"\bproblem\s+solving\s+request\b", " ", hay, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:this chat won'?t appear.*?models|the next era of image creation.*|"
        r"for safety.*|create an image|write or edit|look something up)\b",
        " ",
        hay,
        flags=re.IGNORECASE,
    )
    return _natural_topic(cleaned, max_words=5)


def _first_body_sentence(text: str, min_len: int = 15, max_len: int = 90) -> str:
    """Pull first meaningful line from body text after the 'Site:' marker."""
    after_site = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("Site:"):
            after_site = True
            continue
        if not after_site or len(stripped) < min_len:
            continue
        if re.search(r"https?://", stripped):
            continue
        return stripped[:max_len].strip()
    return ""


# ── Action-oriented web subject extraction ────────────────────────────────────

_NAV_NOISE_WORDS = frozenset({
    "home", "menu", "skip", "sign", "login", "register", "subscribe",
    "click", "here", "back", "next", "prev", "close", "open", "share",
    "like", "follow", "comment", "report", "flag", "reply", "load",
    "more", "less", "expand", "collapse", "toggle", "select", "choose",
})
_DOMAIN_RE = re.compile(r"\b([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?:/[^\s]*)?")
_CONTENT_PREFIX_NOISE_RE = re.compile(
    r"\b(?:chatgpt|ask\s+gemini|get\s+plus|new\s+chat|temporary\s+chat|"
    r"chrome|google\s+chrome|safari|brave|microsoft\s+edge)\b",
    re.IGNORECASE,
)

_FLIGHT_CITY_RE = re.compile(r"\b([A-Z][a-z]{2,12})\s+to\s+([A-Z][a-z]{2,12})\b")
_UNREAD_EMAIL_RE = re.compile(r"\[UNREAD\]\s+([^|\n]+)\|([^|\n]+)")
_ANY_EMAIL_RE   = re.compile(r"([^|\n]{3,30})\s*\|\s*([^|\n]{3,50})\s*\|")


def _parse_body(text: str) -> tuple[str, str, list[str]]:
    """
    Split captured browser text into (search_query, domain, body_lines).
    text format from daemon:
        Searched: {query}       ← optional
        {page_title}
        Site: {domain}
        {body...}
    """
    search_q = ""
    domain   = ""
    body_lines: list[str] = []
    after_site = False
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("Searched:"):
            search_q = s[9:].strip()
        elif s.startswith("Site:"):
            domain = s[5:].strip().lower()
            after_site = True
        elif after_site and s:
            body_lines.append(s)
    if not domain:
        m = _DOMAIN_RE.search(text or "")
        if m:
            domain = m.group(1).lower().lstrip("www.")
    if not body_lines and text:
        body_lines = [line.strip() for line in text.splitlines() if line.strip()]
    return search_q, domain, body_lines


def _first_content_sentence(body_lines: list[str]) -> str:
    """Find first line that reads like actual content, not navigation."""
    for line in body_lines[:20]:
        line = line.strip()
        line = _DOMAIN_RE.sub(" ", line)
        line = _CONTENT_PREFIX_NOISE_RE.sub(" ", line)
        for chunk in re.split(r"(?<=[.!?])\s+|[•|]+", line):
            chunk = clean_text(chunk)
            if len(chunk) > 180:
                chunk = chunk[:180].rsplit(" ", 1)[0]
            if len(chunk) < 12:
                continue
            words = re.findall(r"[A-Za-z]{3,}", chunk)
            if len(words) < 3:
                continue
            # Skip lines that are mostly nav noise
            nav_ratio = sum(1 for w in words[:6] if w.lower() in _NAV_NOISE_WORDS) / max(len(words[:6]), 1)
            if nav_ratio > 0.5:
                continue
            return chunk
    return ""


def _web_content_subject(
    text: str,
    window_title: str,
    activity: str,
    max_chars: int = 56,
) -> str:
    """
    Build a concise action-oriented mini-summary of what the user actually did:
    'Watched backprop lecture Stanford', 'Checked CS 540 homework 3 deadline',
    'Searched flights NYC Chicago', 'Read 3 unread emails'.
    Mines the body text, not just the page title.
    """
    search_q, domain, body_lines = _parse_body(text)
    body = "\n".join(body_lines)

    # ── 1. Search query — strongest intent signal ─────────────────────────────
    if search_q:
        subject = _search_subject(search_q, max_chars)
        if subject != "Searched for":
            return subject
    if "github" not in domain and re.search(r"\bgoogle\s+search\b|\bsearch\s+results\b", window_title, re.IGNORECASE):
        cleaned_search = re.sub(r"\s+(?:Google\s+Search|Search\s+Results)\b.*$", "", window_title, flags=re.IGNORECASE)
        if cleaned_search and cleaned_search.lower() != window_title.lower():
            subject = _search_subject(cleaned_search, max_chars)
            if subject != "Searched for":
                return subject

    # ── 2. Gmail: extract email info ──────────────────────────────────────────
    if "gmail" in domain or "mail.google" in domain:
        m = _UNREAD_EMAIL_RE.search(body)
        if m:
            sender = m.group(1).strip()[:18]
            subj = _natural_topic(m.group(2).strip(), max_words=4)
            return truncate_text(f"Read email from {sender} about {subj}".strip(), max_chars)
        m2 = _ANY_EMAIL_RE.search(body)
        if m2:
            sender = m2.group(1).strip()[:18]
            return truncate_text(f"Read email from {sender}", max_chars)
        unread_count = len(re.findall(r"\[UNREAD\]", body))
        if unread_count:
            return f"Read {unread_count} unread emails"
        return "Checked Gmail inbox"

    # ── 3. YouTube: extract video title ──────────────────────────────────────
    if "youtube" in domain:
        # YouTube JS puts video title as first line after site
        ch_m = re.search(r"Channel:\s*(.+)", body)
        channel = ch_m.group(1).strip()[:20] if ch_m else ""
        if body_lines:
            vtitle = _clean_web_title(body_lines[0])[:55]
            is_watch = (
                "/watch" in text.lower()
                or "watch" in window_title.lower()
                or ("search" not in window_title.lower() and "results" not in window_title.lower())
            )
            verb = "Watched" if is_watch else "Browsed YouTube"
            topic = _natural_topic(vtitle, max_words=5)
            if topic:
                return truncate_text(f"{verb} {topic}".strip(), max_chars)
        if channel:
            return truncate_text(f"Browsed YouTube {channel}", max_chars)
        return "Browsed YouTube videos"

    # ── 4. Login / verification pages ────────────────────────────────────────
    low_all = f"{window_title}\n{body}".lower()
    if "duosecurity" in domain or "duo security" in low_all:
        return "Completed login verification"
    if "login.wisc.edu" in domain or ("wisconsin" in low_all and "login" in low_all):
        return "Opened Wisconsin login"

    # ── 4. Canvas / LMS / Piazza: course-specific ────────────────────────────
    lms_match = any(x in domain for x in ("canvas", "instructure", "piazza", "gradescope", "brightspace"))
    if lms_match:
        code_m = re.search(r"\b[A-Z]{1,7}\s*\d{2,4}\b", f"{window_title}\n{body}")
        course = code_m.group().replace(" ", " ") if code_m else ""
        if "saml" in low_all or "login" in low_all:
            return "Opened Canvas login"
        if "files" in low_all and course:
            return truncate_text(f"Checked {course} course files", max_chars)
        if "grade" in low_all and course:
            return truncate_text(f"Checked {course} course grades", max_chars)
        if ("syllabus" in low_all or "course summary" in low_all) and course:
            return truncate_text(f"Reviewed {course} course summary", max_chars)
        # Look for assignment/quiz/module name in body
        hw_m = re.search(
            r"\b(homework|assignment|quiz|exam|lab|project|module|lecture|reading|discussion|problem\s+set)"
            r"\s*(\d+[A-Za-z]?)?[^.\n]{0,35}",
            body, re.IGNORECASE,
        )
        if hw_m:
            found = hw_m.group()[:52]
            seed = f"Checked {course} {found}".strip()
            return _natural_subject("", seed, max_chars, max_words=7)
        # Fall back to cleaned title with "Checked" prefix
        cleaned = _clean_web_title(window_title)
        if cleaned and len(cleaned) > 3:
            return _natural_subject("Checked", cleaned, max_chars, max_words=5)
        return "Checked course materials"

    # ── 5. Research / academic ────────────────────────────────────────────────
    if any(x in domain for x in ("arxiv", "jstor", "scholar.google", "pubmed", "springer",
                                  "researchgate", "semanticscholar", "acm.org", "ieee")):
        cleaned = _clean_web_title(window_title)
        if cleaned and len(cleaned) > 5:
            return _natural_subject("Read research about", cleaned, max_chars, max_words=4)
        first = _first_content_sentence(body_lines)
        if first:
            return _natural_subject("Read research about", first, max_chars, max_words=4)
        return "Read research paper"

    # ── 6. Wikipedia ──────────────────────────────────────────────────────────
    if "wikipedia" in domain:
        cleaned = _clean_web_title(window_title)
        if cleaned:
            return _natural_subject("Read about", cleaned, max_chars, max_words=5)

    # ── 7. Stack Overflow ─────────────────────────────────────────────────────
    if "stackoverflow" in domain:
        cleaned = _clean_web_title(window_title)
        if cleaned and len(cleaned) > 5:
            return _natural_subject("Read solution for", cleaned, max_chars, max_words=4)

    # ── 8. Reddit ─────────────────────────────────────────────────────────────
    if "reddit" in domain:
        cleaned = _clean_web_title(window_title)
        if cleaned:
            return _natural_subject("Read Reddit thread about", cleaned, max_chars, max_words=4)

    # ── 9. GitHub ─────────────────────────────────────────────────────────────
    if "github" in domain:
        if "search" in low_all:
            q_m = re.search(r"[?&]q=([^&\s]+)", text, re.IGNORECASE)
            if not q_m:
                q_m = re.search(r"(?:search\s+)([A-Za-z0-9._ ]{2,40})", text, re.IGNORECASE)
            q = q_m.group(1) if q_m else window_title
            return _natural_subject("Searched GitHub for", q, max_chars, max_words=4)
        cleaned = _clean_web_title(window_title)
        if cleaned and len(cleaned) > 3:
            low_clean = cleaned.lower()
            if "pull request" in low_clean or "wants to merge" in low_all:
                if "reminder" in low_clean and ("wrong time" in low_clean or "fires" in low_clean):
                    return "Reviewed reminder timing pull request"
                return _natural_subject("Reviewed pull request about", cleaned, max_chars, max_words=4)
            if "issues" in low_clean:
                return _natural_subject("Checked GitHub issues for", cleaned, max_chars, max_words=3)
            return _natural_subject("Viewed GitHub repo about", cleaned, max_chars, max_words=4)

    # ── 10. Flight / travel ───────────────────────────────────────────────────
    travel_domains = ("delta", "united.com", "southwest", "expedia", "kayak",
                      "google.com/flights", "flights.google", "skyscanner", "priceline")
    if any(x in domain for x in travel_domains):
        city_m = _FLIGHT_CITY_RE.search(body)
        if city_m:
            return truncate_text(
                f"Searched flights from {city_m.group(1)} to {city_m.group(2)}", max_chars
            )
        cleaned = _clean_web_title(window_title)
        if cleaned and len(cleaned) > 3:
            return _natural_subject("Checked travel options for", cleaned, max_chars, max_words=3)
        return "Searched travel options"

    # ── 11. ChatGPT / AI tools ────────────────────────────────────────────────
    if any(x in domain for x in ("chatgpt", "claude", "perplexity", "gemini")):
        first = _first_content_sentence(body_lines)
        assistant = _assistant_name(domain, window_title)
        ai_low = f"{window_title}\n{body}\n{first}".lower()
        if "new chat" in ai_low and (
            "search chats" in ai_low or "what's on the agenda" in ai_low
            or "welcome" in ai_low
        ):
            return f"Opened {assistant} home screen"
        if "solve it" in ai_low and any(w in ai_low for w in ("img", "image", "heic", "jpg", "jpeg")):
            return f"Asked {assistant} to solve an image problem"
        topic = _ai_topic(body, first, window_title)
        if topic:
            return truncate_text(f"Asked {assistant} about {topic}", max_chars)
        cleaned = _clean_web_title(window_title)
        topic = _natural_topic(cleaned, max_words=4)
        if topic and "problem solving request" in topic:
            return truncate_text(f"Asked {assistant} for problem solving help", max_chars)
        return truncate_text(f"Used {assistant} chat", max_chars)

    # ── 12. Generic: first meaningful sentence from page body ─────────────────
    first = _first_content_sentence(body_lines)
    if first:
        words = _natural_topic(first, max_words=5)
        if words and not _noisy_subject(words):
            return truncate_text(f"Read about {words}", max_chars)

    # ── 13. Cleaned title as last resort ─────────────────────────────────────
    cleaned = _clean_web_title(window_title)
    if cleaned and len(cleaned) > 3:
        return _natural_subject("Viewed", cleaned, max_chars, max_words=5)
    return ""


_LMS_DOMAINS = re.compile(
    r"(?:^|\.)canvas\.|instructure\.com|(?:^|\.)brightspace\.|(?:^|\.)blackboard\.|(?:^|\.)moodle\.",
    re.IGNORECASE,
)

def _domain_heading(text: str, window_title: str, activity: str) -> str:
    """Return a specific heading for known sites, or fall back to activity."""
    # Extract domain from text (text starts with "Site: domain.com")
    m = re.search(r"Site:\s*([A-Za-z0-9.-]+\.[a-z]{2,})", text or "")
    domain = m.group(1).lower() if m else ""
    for d, heading in _SITE_HEADING.items():
        if domain == d or domain.endswith("." + d):
            return heading
    # Canvas / LMS subdomains (canvas.wisc.edu, etc.)
    if _LMS_DOMAINS.search(domain):
        return "Viewed Course Materials"
    # YouTube video watch page
    if "youtube.com/watch" in (text or "").lower():
        return "Watched On YouTube"
    if "searched" in (activity or "").lower() or activity == "Searched Web":
        return "Searched The Web"
    return activity or "Browser Activity"


def clean_text(text: str) -> str:
    text = (text or "").replace("\x00", " ").replace("…", " ")
    text = re.sub(r"\.{2,}", " ", text)
    text = _DASH_RE.sub(" ", text)
    text = re.sub(r"\blog\s+in\b", "login", text, flags=re.IGNORECASE)
    return _SPACE_RE.sub(" ", text).strip()


def truncate_text(text: str, max_chars: int) -> str:
    text = clean_text(text)
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rstrip()
    sp = cut.rfind(" ")
    if sp > max_chars * 0.55:
        cut = cut[:sp]
    return cut.rstrip(" .,-")


def _title_words(words: list[str], target_words: int, filler: str = "Memory") -> str:
    if not words:
        return "Captured Memory Item"
    return " ".join(words[:target_words]).title()


def short_subject(text: str, max_words: int = 5) -> str:
    """Return a compact topical phrase (default ≤5 words) for timeline subtitles."""
    text = clean_text(text)
    if not text:
        return "Captured Memory Item"
    if _SECRET_RE.search(text):
        return "Api Key Copied"

    url = _URL_RE.search(text)
    if url:
        tld = url.group(1).rsplit(".", 1)[-1].lower()
        if tld in _NON_URL_TLDS:
            url = None
    is_url = bool(url)
    if url:
        domain = url.group(1).replace("www.", "")
        path = (url.group(2) or "").strip("/")
        seed = f"{domain} {path.replace('_', ' ')}"
    else:
        seed = text

    seed = clean_text(re.sub(r"[._/|:]+", " ", seed))
    words = []
    seen = set()
    for raw in _WORD_RE.findall(seed):
        raw = re.sub(r"'s$", "", raw.strip(".'"), flags=re.IGNORECASE)
        word = raw.lower()
        if any(ch.isdigit() for ch in word):
            continue
        if len(word) < 3 or len(word) > 28 or word in _STOP_WORDS or word in seen:
            continue
        seen.add(word)
        words.append(raw)
        if len(words) >= max_words:
            break

    if not words:
        words = [w.strip(".'") for w in _WORD_RE.findall(seed)[:max_words] if w.strip(".'")]

    return _title_words(words, max_words, filler="Page" if is_url else "Memory")


def _without_file_noise(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r"\bnsird[_ ]screencaptureui[_ ][A-Za-z0-9]+\b", " ", text, flags=re.IGNORECASE)
    text = _SCREENSHOT_RE.sub(" ", text)
    text = _IMAGE_FILE_RE.sub(" ", text)
    text = re.sub(r"\b(?:cmd|private|tmp|var|folders|temporaryitems|temporaryltems)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b[a-z0-9]{14,}\b", " ", text, flags=re.IGNORECASE)
    return clean_text(text)


def _noisy_subject(subject: str) -> bool:
    words = [w.lower() for w in _WORD_RE.findall(subject)]
    if not words:
        return True
    return len(set(words) - _NOISE_SUBJECT_WORDS) == 0


def summarize_subject(
    text: str,
    max_chars: int = 56,
    window_title: str = "",
    app_name: str = "",
    activity: str = "",
) -> str:
    """Return a compact, display-safe memory subject."""
    is_web = any(name in (app_name or "").lower() for name in
                 ("chrome", "safari", "firefox", "brave", "arc", "edge", "microsoft edge"))

    # For browser pages, prefer an action-oriented mini summary from captured content.
    # Page titles are only a fallback because they usually say where, not what happened.
    if is_web and text:
        subject = _web_content_subject(text, window_title, activity, max_chars=max_chars)
        if subject and not _noisy_subject(subject):
            return subject

    # Fallback for browser pages: use cleaned page title as subject.
    if is_web and window_title:
        cleaned_title = _clean_web_title(window_title)
        if cleaned_title and len(cleaned_title) > 3:
            subject = truncate_text(_web_title_words(cleaned_title), max_chars)
            if subject and not _noisy_subject(subject):
                return subject

    text = _without_file_noise(text)
    if not text:
        text = clean_text(window_title or activity or app_name)

    context = _without_file_noise(" ".join(p for p in (window_title, activity, app_name) if p))
    parts = []
    if context:
        parts.append(context)
    parts.extend(re.split(r"(?<=[.!?])\s+|\s{2,}|[\r\n]+", text))
    candidates = [p.strip(" -:|") for p in parts if len(p.strip()) >= 8]
    if not candidates:
        candidates = [text]

    noise = {
        "search", "timeline", "starred", "recent", "results", "copy",
        "delete", "save", "edit", "summary", "view usage",
    }
    for candidate in candidates:
        low = candidate.lower()
        if re.search(r"(?:temporaryitems|temporaryltems|screencaptureui|nsird)", low):
            continue
        words = set(re.findall(r"[a-z]{3,}", low))
        if not words or words <= noise:
            continue
        subject = truncate_text(short_subject(candidate, max_words=6), max_chars)
        if not _noisy_subject(subject):
            return subject
    fallback = truncate_text(short_subject(context or candidates[0], max_words=6), max_chars)
    if not _noisy_subject(fallback) and not fallback.endswith(("Memory Item", "Page Item")):
        return fallback
    if app_name:
        app_word = short_subject(app_name, max_words=1).split()[0]
        return f"{app_word} Screen Viewed"
    return "Screen Activity Viewed"


def memory_title(
    source: str,
    app_name: str = "",
    activity: str = "",
    window_title: str = "",
    text: str = "",
    max_chars: int = 54,
) -> str:
    """Return the action title only, separate from the dated subject."""
    app = clean_text(app_name) or "Mac App"
    activity = clean_text(activity)
    window_title = clean_text(window_title)
    text = clean_text(text)
    low_activity = activity.lower()
    low_app = app.lower()

    is_browser = any(name in low_app for name in ("chrome", "safari", "firefox", "brave", "arc", "edge", "microsoft edge"))

    if _SECRET_RE.search(text) and (source or "").lower() in ("clipboard", "manual", ""):
        title = "Stashed a sensitive snippet for later"
    elif is_browser:
        title = _domain_heading(text, window_title, activity)
    elif (source or "").lower() == "clipboard":
        if _URL_RE.search(text):
            title = "Saved a link worth coming back to"
        elif any(name in low_app for name in ("code", "cursor", "xcode", "pycharm", "webstorm")):
            title = f"Snagged a snippet of code from {app}"
        else:
            title = f"Lifted a passage from {app}"
    elif (source or "").lower() == "manual":
        title = "Wrote a quick note to my future self"
    elif (source or "").lower() == "window":
        if any(name in low_app for name in ("code", "cursor", "xcode", "pycharm", "webstorm")):
            title = f"Heads-down in the {app} codebase"
        else:
            title = f"In the middle of something in {app}"
    elif (source or "").lower() == "screen":
        if any(name in low_app for name in ("code", "cursor", "xcode", "pycharm", "webstorm")):
            title = f"Reading code in {app}"
        else:
            title = f"Caught a glance of {app}"
    else:
        title = f"A passing moment in {app}"

    subject = short_subject(text or window_title)
    if title.lower() == subject.lower():
        title = f"On {title}"
    # Sentence case (first letter capitalised, rest preserves natural casing).
    raw = truncate_text(title, max_chars).strip()
    if raw:
        result = raw[:1].upper() + raw[1:]
    else:
        result = raw
    # Keep TLDs lowercase even if title-casing crept in upstream.
    result = re.sub(r"\.([A-Z][a-z]{1,5})\b", lambda m: "." + m.group(1).lower(), result)
    return result


def format_heading(
    source: str,
    app_name: str,
    window_title: str = "",
    text: str = "",
    max_chars: int = 74,
) -> str:
    """Build a clear "what happened" heading for a memory row."""
    return memory_title(source, app_name, window_title=window_title, text=text, max_chars=max_chars)
