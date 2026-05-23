"""Render a polished weekly share card from captured memories.

The output is a single PNG (1080 x 1350, social-friendly 4:5) drawn with
AppKit primitives. Returned as an ``NSImage`` plus raw PNG bytes so callers
can both put the image on the clipboard and persist it to disk.

This module is intentionally side-effect-free; the overlay handles
clipboard, file saving, and user feedback.
"""
from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Any

import AppKit


# ── Layout constants ─────────────────────────────────────────────────────────

CARD_W: float = 1080.0
CARD_H: float = 1350.0
PAD: float = 72.0
ACCENT = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.45, 0.86, 0.74, 1.0)
TEXT_PRIMARY = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.96, 0.97, 0.98, 1.0)
TEXT_SECONDARY = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.72, 0.76, 0.80, 1.0)
BG_TOP = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.07, 0.10, 0.13, 1.0)
BG_BOTTOM = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.04, 0.06, 0.08, 1.0)


def _font(size: float, weight: float = AppKit.NSFontWeightRegular) -> AppKit.NSFont:
    return AppKit.NSFont.systemFontOfSize_weight_(size, weight)


def _attrs(font: AppKit.NSFont, color: AppKit.NSColor, *, align: int = AppKit.NSTextAlignmentLeft, tracking: float = 0.0) -> dict:
    para = AppKit.NSMutableParagraphStyle.alloc().init()
    para.setAlignment_(align)
    para.setLineSpacing_(6.0)
    return {
        AppKit.NSFontAttributeName: font,
        AppKit.NSForegroundColorAttributeName: color,
        AppKit.NSParagraphStyleAttributeName: para,
        AppKit.NSKernAttributeName: tracking,
    }


def _draw_string(text: str, rect: AppKit.NSRect, attrs: dict) -> None:
    ns = AppKit.NSString.stringWithString_(text or "")
    ns.drawInRect_withAttributes_(rect, attrs)


# ── Summary extraction ───────────────────────────────────────────────────────

def _summarize(rows: list[dict]) -> dict[str, Any]:
    rows = [r for r in (rows or []) if not int(r.get("is_sensitive") or 0)]
    apps = Counter((r.get("app_name") or "Unknown").strip() for r in rows)

    topics: list[str] = []
    seen: set[str] = set()
    for r in rows:
        h = (r.get("heading") or r.get("summary") or "").strip()
        if not h:
            continue
        key = h.lower()
        if key in seen:
            continue
        seen.add(key)
        topics.append(h)
        if len(topics) >= 5:
            break

    return {
        "count": len(rows),
        "top_apps": [a for a, _ in apps.most_common(3)],
        "topics": topics,
    }


def share_text_from_summary(summary: dict[str, Any]) -> str:
    apps = summary.get("top_apps") or []
    topics = summary.get("topics") or []
    app_line = ", ".join(apps) if apps else "mixed apps"
    bullets = topics if topics else ["Captured and organized daily work context"]
    return (
        "My week in Corenous\n"
        f"Captured {summary.get('count', 0)} moments across {app_line}.\n\n"
        + "\n".join(f"• {b}" for b in bullets[:5])
        + "\n\nGenerated with corenous.ai"
    )


# ── Image drawing ────────────────────────────────────────────────────────────

def _draw_background() -> None:
    gradient = AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
        BG_TOP, BG_BOTTOM,
    )
    gradient.drawInRect_angle_(
        AppKit.NSMakeRect(0, 0, CARD_W, CARD_H), 90.0,
    )
    glow = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
        0.45, 0.86, 0.74, 0.10,
    )
    glow.setFill()
    AppKit.NSBezierPath.bezierPathWithOvalInRect_(
        AppKit.NSMakeRect(-220, CARD_H - 260, 720, 720),
    ).fill()


def _draw_brand_row() -> None:
    dot_r = 14.0
    dot_x = PAD
    dot_y = CARD_H - PAD - 18.0
    ACCENT.setFill()
    AppKit.NSBezierPath.bezierPathWithOvalInRect_(
        AppKit.NSMakeRect(dot_x, dot_y, dot_r, dot_r),
    ).fill()
    _draw_string(
        "CORENOUS  ·  WEEK IN MEMORY",
        AppKit.NSMakeRect(dot_x + dot_r + 14, dot_y - 6, CARD_W - PAD * 2, 28),
        _attrs(_font(14, AppKit.NSFontWeightSemibold), TEXT_SECONDARY, tracking=2.0),
    )


def _draw_headline(summary: dict[str, Any]) -> None:
    count = summary.get("count", 0)
    apps = summary.get("top_apps") or []
    headline = "Your week, captured."
    if count > 0 and apps:
        headline = "Your week, captured."
    elif count > 0:
        headline = "Your week, captured."

    _draw_string(
        headline,
        AppKit.NSMakeRect(PAD, CARD_H - PAD - 200, CARD_W - PAD * 2, 120),
        _attrs(_font(72, AppKit.NSFontWeightHeavy), TEXT_PRIMARY),
    )

    app_line = ", ".join(apps) if apps else "mixed apps"
    subhead = (
        f"{count} moments across {app_line}."
        if count > 0
        else "A second brain for everything you read, write, and ship."
    )
    _draw_string(
        subhead,
        AppKit.NSMakeRect(PAD, CARD_H - PAD - 280, CARD_W - PAD * 2, 60),
        _attrs(_font(28, AppKit.NSFontWeightMedium), TEXT_SECONDARY),
    )


def _draw_bullets(summary: dict[str, Any]) -> None:
    topics = summary.get("topics") or ["Captured and organized daily work context"]
    top_y = CARD_H - PAD - 380
    line_h = 78.0
    for i, topic in enumerate(topics[:5]):
        y = top_y - i * line_h
        ACCENT.setFill()
        AppKit.NSBezierPath.bezierPathWithOvalInRect_(
            AppKit.NSMakeRect(PAD, y + 22, 10, 10),
        ).fill()
        _draw_string(
            topic[:80],
            AppKit.NSMakeRect(PAD + 28, y, CARD_W - PAD * 2 - 28, 60),
            _attrs(_font(30, AppKit.NSFontWeightSemibold), TEXT_PRIMARY),
        )


def _draw_footer() -> None:
    _draw_string(
        "corenous.ai  ·  a memory layer for your AI agents",
        AppKit.NSMakeRect(PAD, PAD - 10, CARD_W - PAD * 2, 32),
        _attrs(_font(20, AppKit.NSFontWeightMedium), TEXT_SECONDARY),
    )


def _make_bitmap_rep() -> AppKit.NSBitmapImageRep:
    return AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None,
        int(CARD_W),
        int(CARD_H),
        8,
        4,
        True,
        False,
        AppKit.NSDeviceRGBColorSpace,
        0,
        0,
    )


def _build_image(summary: dict[str, Any]) -> tuple[AppKit.NSImage, AppKit.NSBitmapImageRep]:
    rep = _make_bitmap_rep()
    ctx = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.saveGraphicsState()
    try:
        AppKit.NSGraphicsContext.setCurrentContext_(ctx)
        _draw_background()
        _draw_brand_row()
        _draw_headline(summary)
        _draw_bullets(summary)
        _draw_footer()
        if ctx is not None:
            ctx.flushGraphics()
    finally:
        AppKit.NSGraphicsContext.restoreGraphicsState()

    image = AppKit.NSImage.alloc().initWithSize_(AppKit.NSMakeSize(CARD_W, CARD_H))
    image.addRepresentation_(rep)
    return image, rep


def _png_bytes(rep: AppKit.NSBitmapImageRep) -> bytes:
    data = rep.representationUsingType_properties_(
        AppKit.NSBitmapImageFileTypePNG, {},
    )
    if data is None:
        return b""
    return bytes(data)


# ── Public API ───────────────────────────────────────────────────────────────

def build_week_share_card(rows: list[dict]) -> tuple[AppKit.NSImage, bytes, str, dict[str, Any]]:
    """Build a weekly share card.

    Returns ``(image, png_bytes, share_text, summary)``.
    Caller decides clipboard, file path, and user feedback.
    """
    summary = _summarize(rows)
    image, rep = _build_image(summary)
    png = _png_bytes(rep)
    text = share_text_from_summary(summary)
    return image, png, text, summary


def default_share_path() -> Path:
    """Suggested destination for the saved PNG."""
    base = Path.home() / "Pictures"
    base.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    return base / f"corenous-week-{stamp}.png"
