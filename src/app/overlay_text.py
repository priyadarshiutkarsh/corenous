"""Text shaping helpers for overlay row/title rendering."""
from __future__ import annotations

import re
from datetime import date

from ..memory.summaries import truncate_text


def subject(text: str) -> str:
    """First sentence (15-60 chars) or first word-boundary truncation."""
    t = text.strip().replace("\n", " ")
    m = re.search(r"^(.{15,60}[.!?])\s", t)
    if m:
        return m.group(1)
    if len(t) <= 60:
        return t
    cut = t[:60]
    sp = cut.rfind(" ")
    return truncate_text(cut[:sp] if sp > 10 else cut, 60)


def context_line(text: str, subj: str) -> str:
    """Content after the subject, collapsed to one short line."""
    rest = text[len(subj):].lstrip(" .!?\n").replace("\n", " ").strip()
    if not rest:
        return ""
    if len(rest) > 80:
        cut = rest[:80]
        sp = cut.rfind(" ")
        return truncate_text(cut[:sp] if sp > 20 else cut, 80)
    return rest


def clip_timeline_words(text: str, max_words: int) -> str:
    """Hard cap words on-screen so timeline rows stay scannable."""
    if not text:
        return ""
    parts = text.split()
    if len(parts) <= max_words:
        return text.strip()
    return " ".join(parts[:max_words]).rstrip(",.;:") + "…"


def clean_subject_display(text: str, source: str) -> str:
    """Single-line subtitle: drop clipboard boilerplate, normalize whitespace."""
    s = (text or "").strip()
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    if source == "clipboard":
        low = s.lower()
        for prefix in ("copied text", "copied:", "copied -"):
            if low.startswith(prefix):
                s = s[len(prefix):].lstrip(" :").strip()
                break
    return s


def trim_redundant_subject(title: str, subj: str) -> str:
    """Hide subtitles that mostly repeat the headline (cleaner two-line rows)."""
    title = (title or "").strip()
    subj = (subj or "").strip()
    if not subj:
        return ""
    if len(subj) < 7:
        return subj
    tl, sl = title.lower(), subj.lower()
    if sl == tl or sl in tl or tl in sl:
        return ""
    if tl.startswith(sl) or sl.startswith(tl):
        return ""
    tw = {w for w in re.findall(r"[a-z0-9]{3,}", tl)}
    sw = [w for w in re.findall(r"[a-z0-9]{3,}", sl)]
    if not sw:
        return subj
    if sum(1 for w in sw if w in tw) / len(sw) >= 0.62:
        return ""
    return subj


def extractive_summary(text: str, n: int = 3) -> str:
    """Word-frequency extractive summary — top n sentences, original order."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 20]
    if len(sents) <= n:
        return text
    stop = frozenset({
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could", "should",
        "may", "might", "can", "shall", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "with", "by", "from", "as", "it", "its", "this", "that", "these",
        "those", "i", "you", "he", "she", "we", "they", "what", "which", "who",
        "not", "no", "so", "if", "than", "then", "also", "just", "about", "into",
        "up", "out", "more", "all", "any", "one", "two", "three", "here", "there",
    })
    words = re.findall(r"\b[a-z]{3,}\b", text.lower())
    freq: dict[str, int] = {}
    for w in words:
        if w not in stop:
            freq[w] = freq.get(w, 0) + 1
    scored = []
    for s in sents:
        ws = re.findall(r"\b[a-z]{3,}\b", s.lower())
        score = sum(freq.get(w, 0) for w in ws if w not in stop) / max(len(ws), 1)
        scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    top = {s for _, s in scored[:n]}
    ordered = [s for s in sents if s in top][:n]
    return " ".join(ordered)


def footer_tagline_for_day() -> str:
    """Short friendly line under the memory count — rotates with the calendar."""
    lines = (
        "your Mac, remembered",
        "private by default",
        "nothing leaves this machine",
        "searchable as you work",
        "your second brain, on disk",
        "captured quietly in the background",
        "find anything you saw again",
    )
    idx = date.today().toordinal() % len(lines)
    return lines[idx]


_CATCHY_REPLACE = {
    "Copied Code Text": "Snagged a snippet of code",
    "Copied Web Link": "Saved a link worth coming back to",
    "Copied Secret Text": "Stashed a sensitive snippet",
    "Manual Memory": "A note to my future self",
    "Browser Activity": "Was reading on the web",
    "Browser activity": "Was reading on the web",
    "Browser Session": "Was browsing",
    "Browser session": "Was browsing",
    "Captured Memory": "A passing thought",
    "Captured memory": "A passing thought",
    "Edited In Code": "Heads-down in the codebase",
    "Reviewed Code Screen": "Reading some code",
    "Working in": "Was working in",
    "Worked in": "Was working in",
    "Viewed in": "Was looking at",
    "Copied in": "Copied something in",
    "Captured in": "Captured something in",
    "Used ": "Was working in ",
    "Viewed ": "Was looking at ",
    "Captured ": "A moment in ",
}

_COPIED_APP_RE = re.compile(r"^Copied\s+(.+?)\s+Text$", re.IGNORECASE)

# Generic observation/consumption verb leads. Stripped so a timeline title
# starts with what's unique to the capture instead of a repeated verb
# ("Reviewing X", "Searched for Y" → "X", "Y"). Longest-first so multi-word
# leads match before their single-word prefixes.
_LEAD_VERB_PREFIXES = (
    "searched github for ",
    "viewed github repo about ",
    "reviewed pull request about ",
    "checked github issues for ",
    "read reddit thread about ",
    "read research about ",
    "read solution for ",
    "checked travel options for ",
    "reading code in ",
    "search results for ",
    "read through ",
    "searching for ",
    "searched for ",
    "looking at ",
    "looked at ",
    "read about ",
    "reviewing ",
    "reviewed ",
    "exploring ",
    "explored ",
    "browsing ",
    "browsed ",
    "watching ",
    "watched ",
    "checking ",
    "checked ",
    "viewing ",
    "viewed ",
    "reading ",
    "editing ",
    "edited ",
)
_LEAD_VERB_KEEP_NEXT = frozenset(
    {"a", "an", "the", "some", "my", "to", "in", "of", "and"}
)


def _strip_lead_verb(t: str) -> str:
    """Drop a generic leading verb so the title leads with its unique subject.

    Leaves friendly first-person templates intact: only strips when the
    remainder is substantial (>=2 words, >=6 chars) and its first word is not
    an article/preposition (which would read as a mangled sentence fragment).
    """
    low = t.lower()
    for pre in _LEAD_VERB_PREFIXES:
        if low.startswith(pre):
            rest = t[len(pre):].strip()
            words = rest.split()
            if len(words) >= 2 and len(rest) >= 6 and words[0].lower() not in _LEAD_VERB_KEEP_NEXT:
                return rest[:1].upper() + rest[1:]
            return t
    return t


_HEURISTIC_RE = re.compile(
    r"^(was\s+(deep|working|looking|browsing|reading)|"
    r"stashed\s+a|grabbed\s+code|lifted\s+(text|a\s+passage)|"
    r"snagged\s+a\s+snippet|saved\s+a\s+link|"
    r"saved\s+a\s+secret|stashed\s+a\s+sensitive|"
    r"wrote\s+a\s+(note|quick)|caught\s+a\s+glance|"
    r"a\s+(moment|passing\s+moment)\s+in|"
    r"read\s+through\s+some\s+code|reading\s+code\s+in|"
    r"heads[- ]down\s+in|in\s+the\s+middle\s+of|"
    r"copied|viewed|captured|worked|used|browser)",
    re.IGNORECASE,
)


def catchy_title(title: str, subj: str, app_name: str | None, full_text: str) -> str:
    t = (title or "").strip()
    if not t:
        return subj or "A passing thought"

    for sep in ("  ·  ", " · ", " — ", " - "):
        if app_name and t.lower().endswith((sep + app_name).lower()):
            t = t[:-(len(sep) + len(app_name))].strip()
            break

    looks_heuristic = bool(_HEURISTIC_RE.match(t))
    if looks_heuristic:
        replaced = False
        for stale, fresh in _CATCHY_REPLACE.items():
            if t.lower() == stale.lower().rstrip():
                t = fresh
                replaced = True
                break
        if not replaced:
            m = _COPIED_APP_RE.match(t)
            if m:
                t = f"Lifted a passage from {m.group(1).strip()}"
                replaced = True
        if not replaced:
            for stale, fresh in _CATCHY_REPLACE.items():
                if stale.endswith(" ") and t.lower().startswith(stale.lower()):
                    t = fresh + t[len(stale):]
                    break
        t = re.sub(
            r"^(Captured|Copied|Viewed|Worked)\s+in\s+\S+\s*[-–—:·]\s*",
            "",
            t,
            flags=re.IGNORECASE,
        )

    t = t.strip(" ·-—:")
    if not t:
        return subj or "A passing thought"

    t = _strip_lead_verb(t)

    if t:
        t = t[:1].upper() + t[1:]
    return t[:90].rstrip()
