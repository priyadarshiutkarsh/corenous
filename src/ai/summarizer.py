"""
AI-powered memory summarizer and Q&A engine — wraps the local GGUF model
(see ``local_llm`` in ``config/settings.yaml``).

Three public functions:
  ai_summarize()    — heading + subject + optional multi-paragraph narrative
  ai_answer_query() — natural-language answer over a retrieved memory set
  ai_is_sensitive() — contextual privacy check (returns bool + reason)

All functions return safe fallback values when the model is not ready.

Capture uses ``fast=True`` (non-blocking ``infer_nowait``) so the daemon never
waits on long GPU generations; CLI uses blocking inference with a single pass.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from .llm import chat_stop_sequences, infer, infer_nowait


def _infer_stops(*extras: str) -> list[str]:
    return [*chat_stop_sequences(), *extras]

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _strip_json_noise(s: str) -> str:
    """Fix common small-model JSON mistakes (trailing commas)."""
    t = (s or "").strip()
    t = re.sub(r",(\s*})", r"\1", t)
    t = re.sub(r",(\s*])", r"\1", t)
    return t


def _extract_json_object(raw: str) -> dict | None:
    """Parse first JSON object from model output (plain, fenced, or embedded)."""
    text = (raw or "").strip()
    if not text:
        return None
    m = _JSON_FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(_strip_json_noise(text))
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        chunk = text[start : end + 1]
        try:
            return json.loads(_strip_json_noise(chunk))
        except json.JSONDecodeError:
            pass
    return None


# ── Activity summarization (one JSON: heading + kicker + multi-line story) ─

_SUMMARIZE_PROMPT = """You summarize one captured moment from the user's Mac for a private memory app. The user will scan many of these in a list — make this one EARN its place.

Reply with ONE JSON object only. No markdown fences. Keys exactly "heading", "subject", and "paragraphs".

Schema:
{{"heading":"<string>","subject":"<string>","paragraphs":["<string>", ...]}}

Rules for heading:
- EXACTLY 4 to 6 words. Past tense. Lead with a strong, specific action verb.
- The heading MUST describe what is actually in THIS capture's content. NEVER invent. NEVER reuse a phrase from these rules or examples elsewhere.
- The heading MUST be unique — it cannot match or paraphrase any heading in "Recently used headings" below. If your first instinct overlaps, pick a different angle (a different visible noun, a different verb, a different facet).
- Name the actual thing visible: a real file name, feature, error message, person, repo, site section, or topic FROM THE CAPTURE TEXT.
- Banned filler verbs: "Worked on", "Looked at", "Browsed", "Used", "Opened", "Checked", "Saw", "Viewed". Replace with precise ones grounded in the content.
- Use clean, fluent English. FIX obvious OCR mistakes ("polnt" -> "point", "vi5ion" -> "vision", "racords" -> "records") instead of copying them verbatim.
- No hyphens, dashes, slashes, quotes, or "in [App]" / "on [Site]" suffixes.

Rules for subject:
- One crisp phrase, 3 to 5 words, the core topic only. Newspaper kicker style. MUST be grounded in the captured text and not duplicate the heading.

Rules for paragraphs:
- Between 4 and 7 entries (each entry is one clear sentence when possible, past tense, neutral factual voice).
- Together they tell what was on screen or what the user was doing, in plain prose. No bullet characters, no numbering, no markdown.
- Capture the ESSENCE first, then details: include at least one sentence that states the central theme of the document/page/session in plain language.
- If clearly visible and useful, include valuable identifiers for follow-up (person names, work emails, usernames, ticket ids, repo or file names). Keep them concise and contextual.
- Describe only what is supported by the captured text and the window context. Do not invent actions, purchases, replies, or clicks unless the text shows them.
- FIX obvious OCR typos when paraphrasing. Write in clean English. Do not quote garbled tokens verbatim.
- No URLs pasted in full; paraphrase or shorten site names. No long code dumps; name the file or language instead.
- Do not use hyphens or em dashes as punctuation (use commas or periods).
- If prior_context is provided, use it only to ground what the user was likely doing; it does not override the main content.

If the captured text is too thin or too garbled to write 4 paragraphs honestly, write 2 to 3 short honest sentences that say what is visible and that the rest was unclear. Do NOT pad with filler.

Context:
App: {app}
Window: {title}
Activity: {activity}
Prior activity: {prior_context}
Recently used headings (DO NOT REUSE OR PARAPHRASE these — pick a different angle):
{avoid_headings}
Content:
{content}"""

_SUMMARIZE_PROMPT_COMPACT = """You write tiny, magazine-headline summaries of one computer moment for a personal memory app on macOS.
Reply with ONE JSON object only. No markdown fences. Keys exactly "heading" and "subject".

Schema:
{{"heading":"<string>","subject":"<string>"}}

Rules:
- heading: EXACTLY 4 to 6 words, past tense, strong specific verb first. MUST be grounded in this capture's content — describe what is actually visible. MUST be unique — do not match or paraphrase anything in "Recently used headings" below. Name the actual thing (file, feature, error, concept, person) shown in the text. Never app name alone. Banned filler verbs: Worked on, Looked at, Browsed, Used, Opened, Checked, Saw, Viewed. FIX obvious OCR typos when writing (e.g. "polnt" -> "point", "vi5ion" -> "vision"). No hyphens, dashes, quotes, or "in [App]" suffixes.
- subject: ONE crisp phrase, 3 to 5 words, the core topic only. Newspaper kicker style. Grounded in the text. No hyphens. No quotes. Must not repeat the heading.

If input is noisy or garbled, infer intent from app + window title; never paste raw tokens into subject.

Context:
App: {app}
Window: {title}
Activity: {activity}
Recently used headings (DO NOT REUSE OR PARAPHRASE):
{avoid_headings}
Content: {content}"""

_SUMMARIZE_RETRY = """Your reply must be ONLY valid JSON. Keys heading, subject, and paragraphs (array of 4 to 7 strings). Example:
{{"heading":"Read Rust async chapter","subject":"Tokio async-await","paragraphs":["The screen showed a chapter on async Rust.","The text focused on await and pinning.","The window title mentioned Tokio.","No other apps were visible in the snippet.","The excerpt was mid paragraph.","The user was likely studying concurrency.","The capture was brief but readable."]}}

No code fences. No extra text.

Context:
App: {app}
Window: {title}
Activity: {activity}
Content:
{content}"""


def ai_summarize(
    text: str,
    window_title: str = "",
    app_name: str = "",
    activity: str = "",
    *,
    fast: bool = False,
    content_char_limit: int | None = None,
    completion_max_tokens: int | None = None,
    prior_context: str = "",
    avoid_headings: list[str] | None = None,
) -> tuple[str, str, str]:
    """Return (heading, subject, narrative). Narrative may be empty.

    ``avoid_headings``: list of recently-generated headings the model should
    not reuse or paraphrase. Used to keep the timeline list scannable instead
    of full of near-duplicate titles.
    """
    max_tok = int(completion_max_tokens or 300)
    max_tok = max(120, min(max_tok, 600))
    narr_limit = int(content_char_limit or 1100)
    narr_limit = max(400, min(narr_limit, 4000))

    limit = 650 if fast else narr_limit

    # Strip OS chrome (version banners, weather widgets, sidebar nav) before
    # truncation so the budget goes to actual content, not "Relaunch to update".
    from ..memory.summaries import strip_ui_chrome
    raw_content = strip_ui_chrome((text or "").strip())[:limit]
    import re as _re
    content = _re.sub(r"\n{3,}", "\n\n", raw_content) if not fast else raw_content.replace("\n", " ")

    app = app_name or "Unknown"
    title = window_title or "Unknown"
    act = activity or "Unknown"
    prior = (prior_context or "").strip()[:200] or "none"

    # Format the "do not reuse" list — short and scannable for the model.
    avoid_list = [h.strip() for h in (avoid_headings or []) if h and h.strip()]
    seen: set[str] = set()
    uniq_avoid: list[str] = []
    for h in avoid_list:
        key = h.lower()
        if key not in seen:
            seen.add(key)
            uniq_avoid.append(h)
        if len(uniq_avoid) >= 10:
            break
    if uniq_avoid:
        avoid_block = "\n".join(f"- {h}" for h in uniq_avoid)
    else:
        avoid_block = "(none yet)"

    base = {
        "app": app,
        "title": title,
        "activity": act,
        "content": content or "none",
    }
    if fast:
        base_fast = {**base, "avoid_headings": avoid_block}
        prompt = _SUMMARIZE_PROMPT_COMPACT.format(**base_fast)
        raw = infer_nowait(prompt, max_tokens=96)
        obj = _extract_json_object(raw)
        h, s = _coerce_heading_subject(obj)
        return h, s, ""

    base_full = {**base, "prior_context": prior, "avoid_headings": avoid_block}

    # Retry budget: similar ceiling to the main pass so Gemma-class models
    # are not asked for a second long completion.
    retry_tokens = max(160, min(400, max_tok + 80))

    prompt = _SUMMARIZE_PROMPT.format(**base_full)
    raw = infer(prompt, max_tokens=max_tok, stop=_infer_stops())
    obj = _extract_json_object(raw)
    h, s = _coerce_heading_subject(obj)
    nar = _paragraphs_to_narrative(obj)
    if h and s:
        return h, s, nar
    nar_keep = nar
    base_compact = {**base, "avoid_headings": avoid_block}
    if raw:
        raw2 = infer(_SUMMARIZE_RETRY.format(**base), max_tokens=retry_tokens, stop=_infer_stops())
        obj2 = _extract_json_object(raw2)
        h2, s2 = _coerce_heading_subject(obj2)
        nar2 = _paragraphs_to_narrative(obj2)
        if h2 and s2:
            return h2, s2, nar2
        h, s, nar = h2, s2, nar2
        nar_keep = nar2 or nar_keep
    raw_c = infer(
        _SUMMARIZE_PROMPT_COMPACT.format(**base_compact),
        max_tokens=128,
        stop=_infer_stops(),
    )
    obj_c = _extract_json_object(raw_c)
    hc, sc = _coerce_heading_subject(obj_c)
    if hc and sc:
        return hc, sc, (nar_keep or "").strip()
    return "", "", ""


_NARRATE_PROMPT = """You are the user's private memory assistant on macOS. They just looked at a screen and you have the OCR or accessibility text from that moment. Write a neutral, factual recap of WHAT WAS ON THE SCREEN, in 2 to 3 short sentences (35 to 70 words total).

Output: plain prose, past tense. No bullets, no headings, no markdown, no quotes.

VOICE: describe the page and the activity. You may say "the user opened" or "this was a" or "the page showed". Do NOT use first person ("I"). Do NOT roleplay as the user.

What to include (pick the strongest 2 to 4 of these, not all):
1. The KIND of content (a LinkedIn post, a Stripe doc, a GitHub PR, a recipe, an error log, an email draft).
2. WHO appeared on the page (real names, products, companies, repos, files, subreddit, channel).
3. The TOPIC or thesis in plain words.
4. ONE concrete detail visible on screen (a number, a quote, a feature, a section heading, a search query, an error message).
5. The user's likely INTENT only when the evidence is unambiguous (a search query suggests "researching", a diff suggests "reviewing").

CRITICAL ANTI-HALLUCINATION RULES:
- ONLY describe what is visible in the captured text. NEVER claim the user took an action (replied, clicked, typed, bought, signed up, agreed, replied) unless their action is literally shown in the text.
- A call-to-action on the page (like "Reply if interested" or "Click to subscribe") is part of the PAGE, not evidence the user did it.
- If you are unsure whether something happened, omit it. Brevity is fine.
- Never invent names, numbers, dates, or quotes that are not in the captured text.
- Never paste raw URLs, raw HTML, code dumps, or token soup.
- Never include hyphens or em dashes in the output.
- Site name only when meaningful ("on LinkedIn", "on GitHub"). Skip "in Chrome" type filler.

If the captured text is too sparse for a real recap, write exactly: Captured something brief; the page text was too thin to summarize confidently.

Context for grounding:
App: {app}
Window: {title}
Activity: {activity}
Source: {source}

CAPTURED TEXT (truncated):
{content}

Write the 2 to 3 sentence neutral recap now."""


_DISTILL_PROMPT = """You extract structured facts from one captured Mac moment. Reply with ONE JSON object only. No markdown fences. Keys exactly "topic", "who", "where", "gist".

Schema:
{{"topic":"<2 to 5 word topic phrase>","who":["<person, product, repo, file, or company>", ...],"where":"<site or app, max 3 words>","gist":"<single 12 to 20 word summary>"}}

Rules:
- topic: short noun phrase, like "Omi AI hardware launch" or "Tokio async deadlock fix" or "Stripe webhook setup". No hyphens.
- who: up to 4 entries; real proper nouns visible in the text. People names, product names, repos, file names, companies. Empty list if nothing applies.
- where: where this happened in the user's mental model. "LinkedIn", "GitHub", "Notes", "Cursor on file foo.py", "Google search". Concrete and short.
- gist: one sentence describing what was on the page in user POV. Past tense. No hyphens.
- Never invent values that are not supported by the captured text.

Context:
App: {app}
Window: {title}
Activity: {activity}

CAPTURED TEXT (truncated):
{content}"""


def ai_narrate(
    text: str,
    *,
    window_title: str = "",
    app_name: str = "",
    activity: str = "",
    source: str = "",
) -> str:
    """2 to 3 sentence narrative of what the user saw and did. Empty string
    if the LLM is unavailable."""
    from ..memory.summaries import strip_ui_chrome
    body = strip_ui_chrome(text or "").replace("\n", " ").strip()
    if not body:
        return ""
    snippet = body[:1600]
    prompt = _NARRATE_PROMPT.format(
        app=app_name or "Unknown",
        title=window_title or "Unknown",
        activity=activity or "Unknown",
        source=source or "screen",
        content=snippet,
    )
    raw = infer(prompt, max_tokens=180, stop=_infer_stops("\n\n\n"))
    out = (raw or "").strip()
    if not out:
        return ""
    # Defensive: strip rare bullet/quote prefixes the model might still emit.
    if out.startswith(("- ", "• ", "* ")):
        out = out[2:].strip()
    if out.startswith('"') and out.endswith('"'):
        out = out[1:-1].strip()
    # Honour the user's "no hyphens or dashes" rule everywhere we surface text.
    out = (out
           .replace("—", ", ")
           .replace(" – ", ", ")
           .replace(" - ", ", "))
    out = re.sub(r"\s+", " ", out).strip()
    from ..memory.summaries import normalize_sentence_breaks
    out = normalize_sentence_breaks(out)
    return out[:520]


def ai_distill(
    text: str,
    *,
    window_title: str = "",
    app_name: str = "",
    activity: str = "",
) -> dict:
    """Return structured facts: {topic, who:list[str], where, gist}. Empty
    dict if the LLM is unavailable or the JSON could not be parsed."""
    from ..memory.summaries import strip_ui_chrome
    body = strip_ui_chrome(text or "").replace("\n", " ").strip()
    if not body:
        return {}
    snippet = body[:1400]
    prompt = _DISTILL_PROMPT.format(
        app=app_name or "Unknown",
        title=window_title or "Unknown",
        activity=activity or "Unknown",
        content=snippet,
    )
    raw = infer(prompt, max_tokens=200, stop=_infer_stops())
    obj = _extract_json_object(raw)
    if not obj or not isinstance(obj, dict):
        return {}
    topic = str(obj.get("topic") or "").strip()
    who_raw = obj.get("who") or []
    where = str(obj.get("where") or "").strip()
    gist = str(obj.get("gist") or "").strip()
    if isinstance(who_raw, str):
        who_raw = [w.strip() for w in re.split(r"[,;]", who_raw) if w.strip()]
    who = [str(w).strip() for w in who_raw if str(w).strip()][:4]
    # Honour "no hyphens" rule on user-visible fields.
    def _clean(s: str) -> str:
        s = (s.replace("—", ", ")
              .replace(" – ", ", ")
              .replace(" - ", ", "))
        return re.sub(r"\s+", " ", s).strip()
    return {
        "topic": _clean(topic)[:80],
        "who": [_clean(w)[:48] for w in who],
        "where": _clean(where)[:48],
        "gist": _clean(gist)[:200],
    }


# Back-compat shim: keep old call sites working until they migrate.
def ai_observe(
    text: str,
    *,
    window_title: str = "",
    app_name: str = "",
    activity: str = "",
    source: str = "",
) -> str:
    """Deprecated alias for ``ai_narrate``."""
    return ai_narrate(
        text,
        window_title=window_title,
        app_name=app_name,
        activity=activity,
        source=source,
    )


def _clamp_words(text: str, max_words: int) -> str:
    parts = text.split()
    if len(parts) <= max_words:
        return text.strip()
    clipped = " ".join(parts[:max_words]).rstrip(",.;:")
    return clipped + "…"


# User dislikes hyphens / em-dashes in any AI-facing copy.  This helper
# normalizes em / en dashes to commas, drops standalone hyphens used as
# rhetorical pauses ("foo - bar"), and keeps inline hyphens in compound
# words ("multi-pass") intact.
_DASH_BETWEEN = re.compile(r"\s+[\u2014\u2013-]+\s+")
_DASH_LEADING = re.compile(r"^[\u2014\u2013-]+\s+")
_DASH_TRAILING = re.compile(r"\s+[\u2014\u2013-]+$")


def _strip_dashes(s: str) -> str:
    if not s:
        return s
    out = _DASH_LEADING.sub("", s)
    out = _DASH_TRAILING.sub("", out)
    out = _DASH_BETWEEN.sub(", ", out)
    # Collapse double commas/spaces left behind by the substitution above.
    out = re.sub(r",\s*,", ",", out)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip()


def _coerce_heading_subject(obj: dict | None) -> tuple[str, str]:
    if not obj or not isinstance(obj, dict):
        return "", ""
    h = str(obj.get("heading", "")).strip()
    s = str(obj.get("subject", "")).strip()
    if len(h) > 200:
        h = h[:200]
    if len(s) > 120:
        s = s[:120]
    # Dash cleanup matches heuristic path (memory.summaries.clean_text) before clamp.
    from ..memory.summaries import clean_text

    h = clean_text(_clamp_words(h, 9))
    s = clean_text(_clamp_words(s, 6))
    return h, s


def _paragraphs_to_narrative(obj: dict | None) -> str:
    """Join model paragraphs into the narrative column; cap length for SQLite."""
    if not obj or not isinstance(obj, dict):
        return ""
    from ..memory.summaries import clean_text, normalize_sentence_breaks

    raw = obj.get("paragraphs")
    parts: list[str] = []
    if isinstance(raw, list) and raw:
        for p in raw:
            t = clean_text(_strip_dashes(str(p).strip()))
            if not t:
                continue
            t = normalize_sentence_breaks(t)
            if not t.endswith((".", "!", "?")):
                t = t + "."
            parts.append(t)
        body = "\n\n".join(parts)
    else:
        body = str(obj.get("narrative", "") or obj.get("body", "") or "").strip()
        body = clean_text(_strip_dashes(body)) if body else ""
        if body:
            body = normalize_sentence_breaks(body)
    if not body:
        return ""
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body[:2400]


# ── Q&A synthesis ─────────────────────────────────────────────────────────────

_QA_PROMPT = """You are a precise personal memory assistant. The user asks about their Mac activity.

MEMORY LOG (each line begins with [#id] then time, app, topic):
{context}

QUESTION: {question}

Answer rules:
- Reply in 2 to 3 sentences. Total length ≤ 65 words.
- Each sentence must carry weight: name the apps, sites, files, topics, or outcomes that appear in the log. No filler, no hedging, no greetings.
- CITE the memory ids you used inline in [#id] brackets right after the claim they support, like "Cursor at 11:32 [#9457]". Cite at least one memory if any apply.
- Past tense for actions the user took. Present tense only for what's still open.
- Never invent events that are not supported by the log. If the log is thin on the question, say so in one sentence and stop.
- Plain prose only. No bullets, no markdown, no numbered lists, no headings, no em-dashes or rhetorical hyphens.

Write the answer now:"""


_DAY_RECAP_PROMPT = """You are a precise personal memory assistant. The user wants a real recap of their day from the log below.

MEMORY LOG (oldest first, newest last; each line: [#id] time | app | headline | excerpt):
{context}

QUESTION: {question}

Write a proper recap, not a vague overview.

Rules:
- Use 6 to 10 sentences. Total length 170 to 280 words.
- Sentence 1: state the calendar day you infer and the exact first and last timestamps you see in the log (use the times printed in the lines).
- Next sentences: walk through the day in time order when possible. Cluster by theme (web, coding, messaging, other) but weave concrete names: apps, sites, window titles, topics, files, or search phrases that appear in the log.
- At least eight distinct anchors from the log (examples: app names, domains, video titles, repo names, document titles). Do not invent anchors.
- After important claims, cite the memory id in [#id] form exactly as in the log.
- If the log is mostly one app or one site, say that plainly and say what they were doing there.
- If the log is thin, say so in two sentences and still cite what exists.
- Plain prose only. No bullet points, no markdown, no numbered lists, no headings.
- Do not use hyphens or em dashes as punctuation (use commas, semicolons, or periods).

Write the recap now:"""


_MEMORY_BULLETS_PROMPT = """You are reading ONE moment from someone's Mac and writing a short, beautifully-curated recap they will see later when they revisit this memory. Help them instantly remember what this was and why it mattered. Write in clean, fluent, natural English.

Write 4 to 6 bullets. EVERY line starts with "• " followed by one tight, concrete sentence. Each bullet should add a NEW piece of information — never restate the same fact twice.

WHAT EACH BULLET SHOULD COVER (pick the strongest, in roughly this order):
- First, state the essence: what this page/session was fundamentally about (for example people, hiring, product strategy, debugging, outreach, finance, research).
- What kind of thing this was — name it with a clear noun (a GitHub PR, a Stripe doc page, a Slack DM, an error stack, a draft email, code being edited, etc.).
- The real subject in plain words — the project, person, repo, file, feature, or article that the text is actually about.
- One concrete detail worth remembering: a meaningful quote, a number, an error message, a section heading, a question being asked.
- Any decision, conclusion, or insight visible in the text.
- A possible follow-up the user might want to do later — ONLY if the content directly suggests one.
- If clearly shown and useful for follow-up, include important individual identifiers (email addresses, usernames, candidate names, company names, ticket IDs). Do this only when present in the capture.
- Never output a bullet that simply repeats or paraphrases the Headline metadata. Add information beyond the headline.

IGNORE THESE COMPLETELY (do not mention them, do not summarise them, do not let them shape any bullet):
- Advertisements, sponsored content, "Promoted", "Ad", "AdChoices", "Sponsored by …".
- Call-to-action banners ("Shop now", "Limited time offer", "Subscribe to save", "Sign up today").
- Marketing copy, generic discount offers, newsletter prompts, cookie banners, paywall walls.
- Navigation chrome, footer links, social share buttons, "Recommended for you" widgets.

ENGLISH QUALITY:
- Fix obvious OCR typos when paraphrasing ("polnt" -> "point", "vi5ion" -> "vision", "racords" -> "records", "tne" -> "the", "ware" -> "were", etc.). Never paste a garbled word verbatim; if you cannot infer the correct word, omit that detail.
- Each bullet must read as a complete, grammatical sentence. No fragments. No telegram-speak. No raw URLs.
- Avoid pointless quotation marks around phrases. Just say what it is in plain prose.
- Do not start every bullet with the same verb; vary the openings.

ANTI-HALLUCINATION:
- Use ONLY what is in the captured text and metadata. Never invent facts, names, numbers, or actions.
- Never claim the user did something (clicked, replied, bought, signed up) unless the text literally shows that action. A call-to-action on the page is part of the PAGE, not evidence the user did it.
- Skip secrets, passwords, card numbers, full URLs.
- Never include private secrets even if visible, but non-secret contact identifiers (like a work email) are allowed when they help future recall.
- If the text is too sparse to write 4 useful bullets, write 2 or 3 honest bullets rather than padding.

Metadata for grounding (use these to interpret the text, do not echo them as bullets):
App: {app}
Window: {window}
Activity: {activity}
Headline: {heading}

CAPTURED TEXT:
{text}

Reply with bullets only. No title, no preamble, no closing remark."""


def _fmt_ts(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%b %d %I:%M %p").replace(" 0", " ")
    except Exception:
        return ""


def wants_day_recap_question(question: str) -> bool:
    """True for broad 'what did I do today / recap my day' style prompts."""
    q = (question or "").lower()
    if not q.strip():
        return False
    dayish = any(
        w in q
        for w in (
            "today",
            "yesterday",
            "this morning",
            "this afternoon",
            "tonight",
            "so far",
            "whole day",
            "my day",
            "this week",
            "past week",
        )
    )
    if not dayish:
        return False
    recapish = any(
        w in q
        for w in (
            "what did",
            "what have",
            "what was",
            "what have i",
            "what happened",
            "anything i",
            "catch me up",
            "recap",
            "summarize",
            "summary",
            "overview",
            "tell me",
            "walk me",
            "how was",
            "how did",
            "everything i",
            "all i",
        )
    )
    # "What did I do" without repeating "today" in the same clause
    if not recapish and "today" in q and "did" in q and "i" in q:
        recapish = True
    return recapish


def _stratify_memories_by_time(memories: list[dict], n: int) -> list[dict]:
    """Spread ``n`` picks across the timeline so a long day is covered."""
    if not memories:
        return []
    sorted_m = sorted(memories, key=lambda m: float(m.get("created_at") or 0.0))
    if len(sorted_m) <= n:
        return sorted_m
    if n <= 1:
        return [sorted_m[-1]]
    out: list[dict] = []
    last_i = -1
    for k in range(n):
        i = int(round(k * (len(sorted_m) - 1) / max(n - 1, 1)))
        if i != last_i:
            out.append(sorted_m[i])
            last_i = i
    return out or sorted_m


def build_recap_context(memories: list[dict], *, max_items: int = 52, excerpt_len: int = 88) -> str:
    """Dense log for day recaps: more rows, shorter excerpts, time order."""
    picked = _stratify_memories_by_time(memories, max_items)
    lines: list[str] = []
    for mem in picked:
        mid = int(mem.get("id") or 0)
        if mid <= 0:
            continue
        ts = _fmt_ts(float(mem.get("created_at") or 0))
        app = (mem.get("app_name") or "").strip() or "unknown app"
        head = (mem.get("heading") or "").strip()[:72]
        subj = (mem.get("summary") or mem.get("text_snippet") or "").strip()
        subj = subj.replace("\n", " ")[:excerpt_len]
        line = f"[#{mid}] {ts} | {app} | {head}" if head else f"[#{mid}] {ts} | {app}"
        if subj and subj.lower() not in line.lower():
            line += f" | {subj}"
        lines.append(line)
    return "\n".join(lines)


def _build_qa_context(memories: list[dict]) -> tuple[str, list[int]]:
    """Build the context block for the QA prompt. Returns the context
    string AND the ordered list of memory ids the model is allowed to
    cite (so we can validate citations in the answer downstream)."""
    lines: list[str] = []
    cite_ids: list[int] = []
    for mem in memories[:22]:
        mid = int(mem.get("id") or 0)
        if mid <= 0:
            continue
        ts = _fmt_ts(float(mem.get("created_at") or 0))
        app = (mem.get("app_name") or "").strip()
        head = (mem.get("heading") or "").strip()
        subj = (mem.get("summary") or mem.get("text_snippet") or "").strip()[:96]
        subj = subj.replace("\n", " ")
        parts = [p for p in (ts, app, head) if p]
        line = "  ".join(parts)
        if subj and subj.lower() not in line.lower():
            line += f" — {subj}"
        if not line.strip():
            continue
        lines.append(f"[#{mid}] {line}")
        cite_ids.append(mid)
    return "\n".join(lines), cite_ids


def ai_answer_query(question: str, memories: list[dict]) -> str:
    """
    Synthesize a natural-language answer from retrieved memory rows.
    Returns '' if model is not ready — caller should fall back to templates.
    """
    if not memories:
        return ""

    context, _ids = _build_qa_context(memories)
    if not context:
        return ""

    prompt = _QA_PROMPT.format(question=question, context=context)
    raw = infer(prompt, max_tokens=520, stop=_infer_stops()) or ""
    # Same dash policy as the rest of the AI surface — no em/en dashes,
    # no rhetorical hyphens, but compounds like "multi-pass" survive.
    cleaned_lines = [_strip_dashes(ln) for ln in raw.splitlines()]
    return "\n".join(cleaned_lines).strip()


def ai_answer_recap_local(question: str, memories: list[dict]) -> str:
    """Long form day recap on the local GGUF. Returns '' if model not ready."""
    if not memories:
        return ""
    ctx = build_recap_context(memories)
    if not ctx.strip():
        return ""
    prompt = _DAY_RECAP_PROMPT.format(question=question, context=ctx)
    raw = infer(prompt, max_tokens=1100, stop=_infer_stops()) or ""
    cleaned_lines = [_strip_dashes(ln) for ln in raw.splitlines()]
    return "\n".join(cleaned_lines).strip()


def ai_answer_recap_stream(
    question: str,
    memories: list[dict],
    on_chunk,
) -> str:
    """Streaming day recap on the local GGUF."""
    if not memories:
        return ""
    ctx = build_recap_context(memories)
    if not ctx.strip():
        return ""
    from .llm import infer_stream

    prompt = _DAY_RECAP_PROMPT.format(question=question, context=ctx)

    def _on_token(_piece: str, acc: str) -> None:
        cleaned = "\n".join(_strip_dashes(ln) for ln in acc.splitlines())
        try:
            on_chunk(cleaned)
        except Exception:
            pass

    raw = infer_stream(prompt, _on_token, max_tokens=1100, stop=_infer_stops()) or ""
    cleaned_lines = [_strip_dashes(ln) for ln in raw.splitlines()]
    return "\n".join(cleaned_lines).strip()


def ai_answer_query_stream(
    question: str,
    memories: list[dict],
    on_chunk,
) -> str:
    """Streaming variant: ``on_chunk(text_so_far)`` is invoked as the
    model produces tokens. Returns the final cleaned answer. Empty string
    if the model is not ready or there is no context."""
    if not memories:
        return ""
    context, _ids = _build_qa_context(memories)
    if not context:
        return ""

    from .llm import infer_stream

    prompt = _QA_PROMPT.format(question=question, context=context)

    def _on_token(_piece: str, acc: str) -> None:
        # Strip dashes on the partial too so the user never sees an
        # em-dash flicker into the bubble before cleanup.
        cleaned = "\n".join(_strip_dashes(ln) for ln in acc.splitlines())
        try:
            on_chunk(cleaned)
        except Exception:
            pass

    raw = infer_stream(prompt, _on_token, max_tokens=520, stop=_infer_stops()) or ""
    cleaned_lines = [_strip_dashes(ln) for ln in raw.splitlines()]
    return "\n".join(cleaned_lines).strip()


def _extractive_bullet_fallback(text: str, max_bullets: int = 6) -> str:
    """Cheap bullet list when the LLM path is unavailable (frequency-ranked sentences)."""
    raw = (text or "").strip()
    if not raw:
        return "• (Nothing to summarize.)"
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", raw) if len(s.strip()) > 20]
    if not sents:
        clip = raw.replace("\n", " ")[:260]
        return f"• {clip}{'…' if len(raw) > 260 else ''}"
    if len(sents) <= max_bullets:
        return "\n".join(f"• {s.rstrip()}" for s in sents)
    stop = frozenset({
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could", "should",
        "may", "might", "can", "shall", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "with", "by", "from", "as", "it", "its", "this", "that", "these",
        "those", "i", "you", "he", "she", "we", "they", "what", "which", "who",
        "not", "no", "so", "if", "than", "then", "also", "just", "about", "into",
        "up", "out", "more", "all", "any", "one", "two", "three", "here", "there",
    })
    blob = raw.lower()
    words = re.findall(r"\b[a-z]{3,}\b", blob)
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
    top = {s for _, s in scored[:max_bullets]}
    ordered = [s for s in sents if s in top][:max_bullets]
    return "\n".join(f"• {s.rstrip()}" for s in ordered)


_DAILY_DIGEST_PROMPT = """You are summarizing one day of someone's Mac activity for a private memory app.
You will read a chronological log of captured moments and produce a short digest.

LOG (each line is one captured moment):
{log}

DAY: {day_label}

Write the digest in this exact shape:

Line 1: A single warm opening sentence (≤22 words) about the shape of the day. No greeting words like "yesterday" or "today" — the date label is already shown above.
Then 4–6 bullet lines starting with the character • followed by a space.
Each bullet covers ONE concrete theme: a project, an idea explored, a person mentioned, a decision made, or an unfinished thread. Past tense, specific names of apps/sites/files, never generic.
Optionally end with one line beginning with "Loose end:" pointing to something open or worth following up on. Skip if nothing fits.

Never invent items not supported by the log. If the log is sparse, use fewer bullets (minimum 2). Do not produce headings, numbered lists, or markdown — only • bullets after the opening line."""


def ai_daily_digest(memories: list[dict], day_label: str = "Yesterday") -> str:
    """Single-pass 5-bullet digest of a day's memory log. Returns '' if model
    is not ready or there is too little material to summarize."""
    if not memories:
        return ""

    lines: list[str] = []
    for mem in memories[:120]:
        ts = _fmt_ts(float(mem.get("created_at") or 0))
        app = (mem.get("app_name") or "").strip()
        head = (mem.get("heading") or "").strip()
        subj = (mem.get("summary") or "").strip()
        text = (mem.get("text_snippet") or "").strip()[:140]
        text = text.replace("\n", " ")
        parts = [p for p in (ts, app, head) if p]
        line = "  ".join(parts)
        if subj and subj.lower() not in line.lower():
            line += f" — {subj}"
        elif text and text.lower() not in line.lower():
            line += f" — {text}"
        if line.strip():
            lines.append(line)
    if len(lines) < 2:
        return ""
    prompt = _DAILY_DIGEST_PROMPT.format(
        log="\n".join(lines),
        day_label=day_label,
    )
    raw = infer(prompt, max_tokens=560, stop=_infer_stops())
    text = (raw or "").strip()
    if not text:
        return ""
    # Light cleanup: drop fenced code, ensure bullet glyph consistency,
    # strip em/en-dashes and standalone hyphens (user dislikes the look).
    cleaned: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("```"):
            continue
        if s.startswith("- "):
            s = "• " + s[2:].strip()
        s = _strip_dashes(s)
        cleaned.append(s)
    return "\n".join(cleaned).strip()


def ai_memory_bullets(
    text: str,
    *,
    heading: str = "",
    app_name: str = "",
    window_title: str = "",
    activity: str = "",
) -> str:
    """Tight • bullets for a single memory body; falls back to extractive bullets."""
    from ..memory.summaries import strip_ui_chrome
    body = strip_ui_chrome((text or "").strip())
    if not body:
        return ""

    snippet = body.replace("\n", " ").strip()
    if len(snippet) > 4500:
        snippet = snippet[:4000] + "\n…\n" + snippet[-500:]

    prompt = _MEMORY_BULLETS_PROMPT.format(
        app=app_name or "—",
        window=window_title or "—",
        activity=activity or "—",
        heading=heading or "—",
        text=snippet or "—",
    )
    raw = infer(prompt, max_tokens=380, stop=_infer_stops())
    bullets: list[str] = []
    for ln in (raw or "").splitlines():
        s = ln.strip()
        if s.startswith("•"):
            bullets.append(s)
        elif s.startswith("- "):
            bullets.append("• " + s[2:].strip())
        elif (
            s
            and s[0].isupper()
            and s.endswith((".", "!", "?"))
            and len(s.split()) >= 4
        ):
            # Small models occasionally forget the • on continuation lines.
            # Promote orphan complete sentences (capital start, terminal
            # punctuation, 4+ words) so real content is not silently dropped.
            # Short fragments without punctuation are still discarded as
            # likely chrome / heading echoes.
            bullets.append("• " + s)

    # Small GGUF models occasionally pack multiple sentences into a single
    # bullet AND drop the periods between them. Split such bullets so each
    # sentence renders on its own line with a proper terminal period.
    from ..memory.summaries import split_run_on_bullet

    expanded: list[str] = []
    for b in bullets:
        expanded.extend(split_run_on_bullet(b))
    bullets = expanded

    if len(bullets) >= 2:
        return "\n".join(bullets[:8])
    return _extractive_bullet_fallback(body, max_bullets=6)


# ── Sensitivity classification ────────────────────────────────────────────────

_SENSITIVE_PROMPT = """Does the following text contain sensitive or private information?
Look for: personal health info, financial data, passwords, API keys, \
private personal conversations, or confidential content.
Reply on ONE line only: "yes: <brief reason>" or "no".

Text: {text}"""


def ai_is_sensitive(text: str) -> tuple[bool, str]:
    """
    Contextual privacy check using the local LLM.
    Non-blocking — returns (False, '') immediately if model is busy.
    Only called when structural regex patterns found nothing.
    """
    if not text or len(text) < 40:
        return False, ""
    prompt = _SENSITIVE_PROMPT.format(text=text[:400].replace("\n", " "))
    raw = infer_nowait(prompt, max_tokens=25)
    if not raw:
        return False, ""
    low = raw.lower().strip()
    if low.startswith("yes"):
        reason = raw[3:].strip().lstrip(":").strip().split("\n")[0]
        return True, reason or "sensitive content"
    return False, ""


# ── Brain tab: second-brain session summary ───────────────────────────────────

_BRAIN_SUMMARY_PROMPT = """You are the user's private second brain on macOS. You have access to a log of everything they've been doing across apps, websites, and documents in the last few hours. Your job is to produce a rich, curated summary that reads like a high-quality executive brief.

LOG (most recent first):
{log}

Write the summary in this exact plain-text structure (no markdown):

NOW
One sentence (≤20 words) about the most recent activity. Be specific, name the app, file, site, or topic.

SESSION ARC
2 to 3 sentences covering the flow of this session. What themes emerged, what contexts changed, what the user was trying to accomplish. Name real things: apps, domains, files, repos, people.

KEY MOMENTS
4 to 6 bullet lines, each starting with "• ". Each bullet contains one concrete moment: a document opened, a search made, a page read, code reviewed, a message seen, or a decision made. Past tense, specific, no filler.

OPEN THREADS
1 to 3 lines starting with "→ " for unfinished threads worth revisiting. If nothing fits, omit this section.

Rules:
- Write in clean, fluent English. Fix obvious OCR typos (e.g. "polnt" → "point", "vi5ion" → "vision").
- Never invent facts not supported by the log. If the log is sparse, say so honestly.
- Ignore ad/sponsored junk and generic marketing copy if they appear in the log.
- No hyphens or em dashes as punctuation. Use commas or periods instead.
- No asterisks for bold, no markdown code fences, no numbered lists, no greetings.
- Keep the whole output under 280 words.

Write the summary now:"""


_BRAIN_QUICK_PROMPT = """You are a personal second-brain assistant on macOS. Given a short log of recent activity, write a 2 to 3 sentence summary of what the user is doing RIGHT NOW and what they were doing just before. Be specific: name the app, site, topic, or file. Past tense for earlier activity, present tense for the most recent. Fix OCR typos. No hyphens or dashes. Under 60 words.

LOG (most recent first):
{log}

Write the summary now:"""


def ai_brain_summary(memories: list[dict], *, quick: bool = False) -> str:
    """Generate a rich second-brain summary of recent activity.

    ``quick=True`` returns a short 2-3 sentence version for the status line.
    ``quick=False`` returns the full structured Brain tab summary.
    Returns '' if the model is not ready or there is too little material.
    """
    if not memories:
        return ""

    # Build a compact log from the most recent memories (newest first)
    sorted_mems = sorted(
        memories, key=lambda m: float(m.get("created_at") or 0.0), reverse=True
    )
    lines: list[str] = []
    for mem in sorted_mems[:60]:
        ts = _fmt_ts(float(mem.get("created_at") or 0))
        app = (mem.get("app_name") or "").strip()
        head = (mem.get("heading") or "").strip()
        subj = (mem.get("summary") or "").strip()
        narr = (mem.get("narrative") or "").strip()
        text = (mem.get("text_snippet") or "").strip()[:120]
        text = text.replace("\n", " ")

        # Prefer AI-generated heading + summary; fall back to raw text
        label = head or subj or text
        label = label[:90]
        detail = ""
        if narr and not quick:
            detail = narr[:100].replace("\n", " ")
        elif subj and subj.lower() not in label.lower():
            detail = subj[:80]

        parts = [p for p in (ts, app, label) if p]
        line = "  |  ".join(parts)
        if detail and detail.lower() not in line.lower():
            line += f"  —  {detail}"
        if line.strip():
            lines.append(line)

    if len(lines) < 2:
        return ""

    log_text = "\n".join(lines)

    if quick:
        prompt = _BRAIN_QUICK_PROMPT.format(log=log_text)
        raw = infer(prompt, max_tokens=120, stop=_infer_stops("\n\n\n"))
    else:
        prompt = _BRAIN_SUMMARY_PROMPT.format(log=log_text)
        raw = infer(prompt, max_tokens=600, stop=_infer_stops())

    text = (raw or "").strip()
    if not text:
        return ""

    # Clean up dashes and normalize whitespace
    cleaned_lines = [_strip_dashes(ln) for ln in text.splitlines()]
    result = "\n".join(cleaned_lines).strip()
    return result
