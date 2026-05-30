"""
Screen OCR monitor — captures only the frontmost app's window via CGWindowList,
runs Vision text recognition, diffs against last capture to avoid duplicates.
Requires Screen Recording permission (System Settings → Privacy → Screen Recording).

Images are downscaled before Vision to reduce Metal/CPU spikes on large Retina
windows. OCR runs on a single background thread so it does not compete with
the default asyncio thread pool used elsewhere.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

try:
    import Quartz
    _HAS_QUARTZ = True
except ImportError:
    _HAS_QUARTZ = False

try:
    import Vision
    _HAS_VISION = True
    # VNRequestTextRecognitionLevelAccurate = 0, VNRequestTextRecognitionLevelFast = 1
    _OCR_ACCURATE = getattr(Vision, "VNRequestTextRecognitionLevelAccurate", 0)
    _OCR_FAST = getattr(Vision, "VNRequestTextRecognitionLevelFast", 1)
except ImportError:
    _HAS_VISION = False
    _OCR_ACCURATE = 0
    _OCR_FAST = 1


@dataclass
class CapturedScreen:
    text: str
    source: str = "screen"
    app_name: str = "screen"
    captured_at: float = 0.0
    window_title: str = ""
    bundle_id: str = ""
    activity: str = ""


_OCR_TOKEN_REPAIRS: dict[str, str] = {
    "polnt": "point",
    "vi5ion": "vision",
    "racords": "records",
    "permlssions": "permissions",
    "axampla": "example",
    "mamories": "memories",
    "tne": "the",
    "ware": "were",
    "lnsertion": "insertion",
}


# Lines that are clearly ad / sponsored copy. The matcher is intentionally
# strict — we only drop a line if it is *exactly* one of these tokens or
# matches a tight regex, so genuine content with the word "ad" inside it
# (e.g. "ad-hoc", "Adobe", "read") stays intact.
_AD_EXACT_TOKENS: set[str] = {
    "ad",
    "ads",
    "adchoices",
    "ad choices",
    "sponsored",
    "promoted",
    "promotion",
    "advertisement",
    "advertisements",
    "paid partnership",
    "sponsored content",
    "sponsored post",
    "sponsored by",
    "ad info",
    "ad · info",
    "why this ad?",
    "why this ad",
    "stop seeing this ad",
    "report ad",
    "skip ad",
    "skip ads",
    "learn more",
    "shop now",
    "buy now",
    "limited time offer",
    "limited offer",
    "free shipping",
    "free trial",
    "save 20%",
    "save 30%",
    "save 40%",
    "save 50%",
}

_AD_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(ad|ads|sponsored|promoted)\s*[·•|:\-–—]?\s*[a-z0-9]{1,12}?\s*$", re.I),
    re.compile(r"^\s*sponsored\s+by\b.*$", re.I),
    re.compile(r"^\s*paid\s+(partnership|content|advertisement)\b.*$", re.I),
    re.compile(r"^\s*ad\s*·\s*\S.*$", re.I),
    re.compile(r"^\s*\d{1,3}%\s*off\b.*$", re.I),
    re.compile(r"^\s*get\s+\d{1,3}%\s+off\b.*$", re.I),
    re.compile(r"^\s*free\s+(trial|shipping|delivery)\b.*$", re.I),
    re.compile(r"^\s*limited\s+time\s+offer\b.*$", re.I),
    re.compile(r"^\s*shop\s+(now|the|our)\b.*$", re.I),
    re.compile(r"^\s*buy\s+now\b.*$", re.I),
    re.compile(r"^\s*subscribe\s+(now|today|and\b)\b.*$", re.I),
    re.compile(r"^\s*sign\s+up\s+to\s+(save|win|get)\b.*$", re.I),
    re.compile(r"^\s*download\s+the\s+app\b.*$", re.I),
    re.compile(r"^\s*click\s+here\s+to\b.*$", re.I),
    # Generic call-to-action all-caps tag lines
    re.compile(r"^\s*[A-Z][A-Z\s]{2,18}!\s*$"),
)


_AD_HEADING_RE = re.compile(
    r"\b("
    r"ad|ads|advert|advertisement|sponsored|promoted|promo|"
    r"discount|coupon|deal|sale|offer"
    r")\b",
    re.I,
)


def is_ad_heading(heading: str) -> bool:
    """Return True if the AI-generated heading looks like an ad / sponsored
    capture. Used to drop or hide rows whose heading effectively labels
    them as marketing chrome (e.g. "Browsed Tarot Card Ad").

    Conservative — we only flag if the heading is short (< 6 words) and
    contains an ad-ish word that is unlikely to appear in a normal action
    verb phrase. This avoids stripping legitimate headings like
    "Read article about Adobe acquisition" or "Address book lookup"."""
    h = (heading or "").strip()
    if not h:
        return False
    words = h.split()
    if len(words) > 6:
        return False
    if not _AD_HEADING_RE.search(h):
        return False
    # Reject obvious false positives where the ad-ish word is part of a
    # larger token. The regex uses word boundaries so "Adobe" already
    # won't match, but a defensive check keeps the heuristic stable.
    low = h.lower()
    safe_substrings = ("adobe", "address", "adapt", "addition", "additi",
                       "adams", "admin", "adopt", "advice")
    if any(s in low for s in safe_substrings):
        return False
    return True


def _is_ad_line(line: str) -> bool:
    """Heuristic: does this single OCR'd line look like ad / marketing copy?

    The bar is deliberately conservative — false positives here mean a
    legitimate UI line gets stripped. The strict-exact + tight-regex
    combination keeps this safe for normal text content.
    """
    s = line.strip().lower().rstrip(".:")
    if not s:
        return False
    if s in _AD_EXACT_TOKENS:
        return True
    if len(s) <= 64:
        for pat in _AD_REGEXES:
            if pat.match(line):
                return True
    return False


def _strip_ad_lines(text: str) -> str:
    """Remove individual lines that are obvious ads / marketing chrome."""
    out: list[str] = []
    for ln in text.splitlines():
        if _is_ad_line(ln):
            continue
        out.append(ln)
    cleaned = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _looks_like_mostly_ad(text: str) -> bool:
    """Decide whether the capture is dominated by ad copy. If so the
    daemon should drop it instead of storing a row whose body is just
    "Sponsored · Buy now · 20% off"."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    ad_count = sum(1 for ln in lines if _is_ad_line(ln))
    # Threshold: more than half the lines are ads AND total content is short.
    return ad_count >= 3 and ad_count >= 0.6 * len(lines) and len(text) < 600


def _looks_low_signal_ocr(text: str) -> bool:
    """Reject captures that are mostly OCR noise / gibberish.

    Goal: if we cannot confidently recover meaningful text, do not store it.
    This keeps timeline quality high and avoids summaries over token soup.
    """
    body = (text or "").strip()
    if not body:
        return True
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not lines:
        return True

    # Character-level sanity checks.
    chars = len(body)
    alnum = sum(1 for c in body if c.isalnum())
    alpha = sum(1 for c in body if c.isalpha())
    # If most characters are punctuation/symbol junk, drop it.
    if chars >= 70 and alnum / max(chars, 1) < 0.35:
        return True
    # OCR sometimes yields mostly symbols with tiny alpha signal.
    if chars >= 70 and alpha / max(chars, 1) < 0.22:
        return True

    # Token-level sanity checks.
    tokens = re.findall(r"[A-Za-z0-9]+", body)
    if not tokens:
        return True
    long_alpha = [t for t in tokens if re.fullmatch(r"[A-Za-z]{4,}", t)]
    if len(tokens) >= 14 and len(long_alpha) < 2:
        return True

    one_char = sum(1 for t in tokens if len(t) == 1)
    if len(tokens) >= 18 and one_char / max(len(tokens), 1) > 0.55:
        return True

    short_lines = sum(1 for ln in lines if len(re.findall(r"[A-Za-z0-9]+", ln)) <= 1 and len(ln) <= 6)
    if len(lines) >= 6 and short_lines / len(lines) > 0.66:
        return True

    # Safe by default; only reject when multiple hard signals indicate junk.
    return False


def _repair_ocr_text(text: str) -> str:
    """Normalize common OCR noise before storing captures.

    This pass is intentionally cheap and deterministic. It repairs obvious
    token-level mistakes, strips ad / marketing chrome, and normalises
    whitespace so downstream heading, summary, and bullet generation
    start from cleaner text.
    """
    if not text:
        return ""
    cleaned = text
    cleaned = cleaned.replace("ﬁ", "fi").replace("ﬂ", "fl")
    cleaned = cleaned.replace("—", "-")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    def _fix_word(match: re.Match[str]) -> str:
        token = match.group(0)
        low = token.lower()
        fixed = _OCR_TOKEN_REPAIRS.get(low)
        if not fixed:
            return token
        if token.isupper():
            return fixed.upper()
        if token[:1].isupper():
            return fixed.capitalize()
        return fixed

    cleaned = re.sub(r"\b[A-Za-z0-9]{3,}\b", _fix_word, cleaned)
    cleaned = _strip_ad_lines(cleaned)
    if _looks_like_mostly_ad(cleaned):
        return ""
    if _looks_low_signal_ocr(cleaned):
        return ""
    return cleaned.strip()


def _frontmost_app():
    """Return frontmost application context."""
    try:
        from .app_context import get_frontmost_context
        return get_frontmost_context()
    except Exception:
        from .app_context import FrontmostAppContext
        return FrontmostAppContext()


def _downscale_cg_image(cg_img, max_dim: int):
    """Return a smaller CGImage for Vision when the capture is huge (Retina UHD)."""
    if max_dim <= 0 or not _HAS_QUARTZ:
        return cg_img
    try:
        w = int(Quartz.CGImageGetWidth(cg_img))
        h = int(Quartz.CGImageGetHeight(cg_img))
    except Exception:
        return cg_img
    if w <= 0 or h <= 0:
        return cg_img
    m = max(w, h)
    if m <= max_dim:
        return cg_img
    scale = max_dim / float(m)
    nw = max(1, int(w * scale))
    nh = max(1, int(h * scale))
    color_space = Quartz.CGColorSpaceCreateDeviceRGB()
    if color_space is None:
        return cg_img
    try:
        ctx = Quartz.CGBitmapContextCreate(
            None,
            nw,
            nh,
            8,
            0,
            color_space,
            Quartz.kCGImageAlphaPremultipliedLast,
        )
        if ctx is None:
            return cg_img
        Quartz.CGContextSetInterpolationQuality(ctx, Quartz.kCGInterpolationLow)
        Quartz.CGContextDrawImage(
            ctx,
            Quartz.CGRectMake(0.0, 0.0, float(nw), float(nh)),
            cg_img,
        )
        out = Quartz.CGBitmapContextCreateImage(ctx)
        return out if out is not None else cg_img
    except Exception:
        return cg_img


def _is_new_tab_text(text: str) -> bool:
    low = re.sub(r"\s+", " ", text.lower()).strip()
    if "new tab" not in low and "search google or type" not in low:
        return False
    noise = {
        "new", "tab", "search", "google", "type", "url", "customize",
        "chrome", "most", "visited", "bookmarks", "apps", "shortcuts",
    }
    words = set(re.findall(r"[a-z]{3,}", low))
    return bool(words) and len(words - noise) <= 3


def _drop_chrome_observations(
    observations: list[tuple[str, tuple[float, float, float, float], float]],
) -> list[tuple[str, tuple[float, float, float, float], float]]:
    """Drop OCR observations that sit in the thin top/bottom chrome strips.

    Each item is (text, (x, y, w, h), confidence) where (x, y, w, h) is the
    Vision normalized boundingBox — origin BOTTOM-LEFT, so y near 1.0 is the
    TOP of the window and y near 0.0 is the BOTTOM (verified against real
    Vision output). Title bars, tab strips, toolbars, and status bars live in
    those strips as short, thin lines; mining them into the body adds noise
    like "Opus 4.8" / "Bypass permissions" / window titles.

    Deliberately conservative — a line is only chrome if it is thin AND short
    AND pinned to the extreme top/bottom. We do NOT touch the left edge
    (sidebars vary too much: code line numbers, file trees, channel lists are
    often real content). Order is preserved (Vision's native reading order;
    multi-column reflow is an explicit non-goal). If filtering would gut the
    capture (unusual layout where content lives at the edges), keep everything.
    """
    if len(observations) < 4:
        return observations

    def _is_chrome(text: str, box: tuple[float, float, float, float]) -> bool:
        _x, y, _w, h = box
        y_center = y + h / 2.0
        thin = h <= 0.030
        short = len(text.split()) <= 6 and len(text) <= 48
        # Bands sized from real Vision output: top toolbars/title bars sit at
        # y_center >= ~0.95; bottom status/input bars span up to ~0.09.
        at_edge = y_center >= 0.95 or y_center <= 0.09
        return thin and short and at_edge

    survivors = [o for o in observations if not _is_chrome(o[0], o[1])]
    # Safety: never let spatial filtering gut a capture. The drop rule is
    # already conservative (thin AND short AND edge — body paragraphs can't
    # match), so this guard only catches the pathological case where almost
    # everything is edge chrome: keep the originals if we'd leave under 3 lines
    # or drop more than ~75% of them.
    if len(survivors) < 3 or len(survivors) < 0.25 * len(observations):
        return observations
    return survivors


def _ocr_frontmost_window(
    max_dimension: int = 1280,
    accurate_mode: bool = False,
    min_confidence: float = 0.0,
) -> CapturedScreen | None:
    """
    Capture the frontmost app's main window and OCR it.
    Returns (text, app_name) or None on failure/no permission.
    """
    if not _HAS_QUARTZ or not _HAS_VISION:
        return None

    ctx = _frontmost_app()
    if not ctx.pid:
        return None

    try:
        from .app_context import browser_activity, get_browser_tab_context, is_browser_app
        browser_tab = get_browser_tab_context(ctx)
        browser_app = is_browser_app(ctx.app_name, ctx.bundle_id)
    except Exception:
        browser_tab = None
        browser_app = False

    if browser_app and browser_tab and browser_tab.is_new_tab:
        return None

    try:
        # List all on-screen windows
        window_list = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
        if not window_list:
            return None

        # Find the largest window belonging to the frontmost PID at layer 0
        best_wid   = None
        best_area  = 0
        for info in window_list:
            if info.get("kCGWindowOwnerPID") != ctx.pid:
                continue
            if info.get("kCGWindowLayer", 99) != 0:
                continue
            bounds = info.get("kCGWindowBounds", {})
            area   = bounds.get("Width", 0) * bounds.get("Height", 0)
            if area > best_area:
                best_area = area
                best_wid  = info.get("kCGWindowNumber")

        if not best_wid:
            return None

        # Capture that single window
        img = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull,                          # let it use the window bounds
            Quartz.kCGWindowListOptionIncludingWindow,
            best_wid,
            Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageShouldBeOpaque,
        )
        if img is None:
            return None

        if max_dimension > 0:
            img = _downscale_cg_image(img, max_dimension)

        # Vision OCR: accurate mode gives better multi-line structure; fast is fine
        # for dedup-only use-cases. Language correction helps with symbol confusions
        # (l/1, O/0) when running accurate mode.
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(_OCR_ACCURATE if accurate_mode else _OCR_FAST)
        req.setUsesLanguageCorrection_(accurate_mode)

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, {})
        ok = handler.performRequests_error_([req], None)
        if not ok:
            return None

        observations: list[tuple[str, tuple[float, float, float, float], float]] = []
        for obs in (req.results() or []):
            cands = obs.topCandidates_(1)
            if not cands:
                continue
            cand = cands[0]
            conf = cand.confidence()
            # Skip observations below confidence threshold (avoids garbage chars
            # from low-quality screen regions like shadows or transparent overlays).
            if min_confidence > 0.0 and conf < min_confidence:
                continue
            s = str(cand.string()).strip()
            if not s:
                continue
            try:
                b = obs.boundingBox()
                box = (float(b.origin.x), float(b.origin.y),
                       float(b.size.width), float(b.size.height))
            except Exception:
                # Neutral box (vertical centre) → never treated as chrome.
                box = (0.0, 0.5, 1.0, 0.02)
            observations.append((s, box, float(conf)))

        # Region-aware cleanup: drop thin top/bottom chrome strips (title bars,
        # toolbars, status bars) using the normalized bounding boxes.
        observations = _drop_chrome_observations(observations)

        # Join with newlines to preserve document structure — each Vision observation
        # corresponds to a line of text. Space-joining (old behaviour) destroyed
        # paragraph breaks, code indentation, and list formatting which the AI
        # needs to infer context correctly.
        text = "\n".join(o[0] for o in observations).strip()
        if not text:
            return None
        text = _repair_ocr_text(text)
        if not text:
            return None
        if browser_app and _is_new_tab_text(text):
            return None
        from .app_context import activity_label
        import time

        window_title = ctx.window_title
        activity = activity_label("screen")
        if browser_app and browser_tab:
            window_title = browser_tab.title or ctx.window_title
            activity = browser_activity(browser_tab)
            context_bits = [browser_tab.title, browser_tab.domain, browser_tab.query]
            context = " ".join(bit for bit in context_bits if bit).strip()
            if context:
                text = f"{context}\n{text}"
        elif browser_tab and browser_tab.title:
            window_title = browser_tab.title
        return CapturedScreen(
            text=text,
            app_name=ctx.app_name,
            captured_at=time.time(),
            window_title=window_title,
            bundle_id=ctx.bundle_id,
            activity=activity,
        )

    except Exception as e:
        return None


class ScreenMonitor:
    """
    Polls the frontmost window every `interval` seconds, OCRs it,
    and yields CapturedScreen only when content changes meaningfully.
    """

    def __init__(
        self,
        interval: float = 4.0,
        *,
        max_ocr_dimension: int = 1280,
        accurate_mode: bool = False,
        min_confidence: float = 0.0,
        executor: ThreadPoolExecutor | None = None,
    ):
        self._interval = max(4.0, float(interval))
        self._max_ocr_dimension = max(0, int(max_ocr_dimension))
        self._accurate_mode = bool(accurate_mode)
        self._min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self._last_hash = ""
        self._available = _HAS_QUARTZ and _HAS_VISION
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="corenous_ocr",
        )
        self._own_executor = executor is None

    def is_available(self) -> bool:
        return self._available

    def shutdown(self) -> None:
        if self._own_executor and self._executor is not None:
            try:
                self._executor.shutdown(wait=False, cancel_futures=False)
            except TypeError:
                self._executor.shutdown(wait=False)
            self._executor = None

    async def stream(
        self,
        excluded_apps: list[str] | None = None,
        skip_if: Callable[[], bool] | None = None,
    ):
        if not self._available:
            print("[screen] Vision or Quartz not available — OCR disabled.", flush=True)
            return

        print(
            f"[screen] OCR monitor started (frontmost window, every {self._interval}s, "
            f"max {self._max_ocr_dimension}px for Vision)",
            flush=True,
        )
        loop = asyncio.get_event_loop()

        while True:
            await asyncio.sleep(self._interval)
            try:
                if skip_if is not None and skip_if():
                    continue
                result = await loop.run_in_executor(
                    self._executor,
                    lambda: _ocr_frontmost_window(
                        self._max_ocr_dimension,
                        accurate_mode=self._accurate_mode,
                        min_confidence=self._min_confidence,
                    ),
                )
                if not result:
                    continue
                text, app_name = result.text, result.app_name

                if len(text) < 40:
                    continue

                # Skip excluded apps
                if excluded_apps:
                    low = app_name.lower()
                    if any(ex.lower() in low for ex in excluded_apps):
                        continue

                # Dedup: keep browser title/site changes, skip identical OCR noise
                sig_src = f"{result.window_title}|{result.activity}|{text[:900]}"
                sig = hashlib.md5(sig_src.encode()).hexdigest()
                if sig == self._last_hash:
                    continue
                self._last_hash = sig

                yield result
            except Exception as e:
                print(f"[screen] error: {e}", flush=True)
