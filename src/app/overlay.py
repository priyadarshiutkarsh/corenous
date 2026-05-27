"""
Corenous overlay: command palette, timeline, starred, agent, settings.
Design: midnight ink + teal–copper accent (distinct from generic SaaS purple).
"""
from __future__ import annotations

import random
import re
import threading
import time
from datetime import datetime, date
from typing import Callable

import objc
import AppKit
from Foundation import NSInsetRect, NSUserDefaults
from PyObjCTools import AppHelper

from .ui_constants import (
    CORNER,
    MAIN_FOOTER_H,
    MAIN_GAP_QUOTE_RULE,
    MAIN_GAP_RULE_SEARCH,
    MAIN_GAP_SEARCH_TABS,
    MAIN_GAP_TABS_BODY,
    MAIN_GUTTER,
    MAIN_QUOTE_H,
    MAIN_TAB_BTN_H,
    MAIN_TOP_PAD,
    PANEL_H,
    PANEL_W,
    ROW_H,
    SEARCH_H,
)
from .overlay_content import footer_shortcut_defs, onboarding_pages
from .overlay_text import (
    catchy_title as _catchy_title,
    clean_subject_display as _clean_subject_display,
    clip_timeline_words as _clip_timeline_words,
    context_line as _context_line,
    subject as _subject,
    trim_redundant_subject as _trim_redundant_subject,
)

from ..memory.summaries import (
    clean_text,
    memory_title,
    short_subject,
    summarize_subject,
    truncate_text,
)
from ..monitor.permissions import (
    all_required_permissions,
    check_accessibility,
    check_screen_recording,
    open_accessibility_settings,
    open_screen_recording_settings,
)

# ── Palette ───────────────────────────────────────────────────────────────────
def _c(r, g, b, a=1.0):
    return AppKit.NSColor.colorWithRed_green_blue_alpha_(r/255, g/255, b/255, a)


# Theme: "light" (default) | "dark" | "auto" (follow macOS appearance).
_THEME_PREF: str = "light"


def _is_dark() -> bool:
    pref = _THEME_PREF
    if pref == "light":
        return False
    if pref == "dark":
        return True
    try:
        ap = AppKit.NSApp.effectiveAppearance()
        nm = ap.bestMatchFromAppearancesWithNames_([
            AppKit.NSAppearanceNameAqua,
            AppKit.NSAppearanceNameDarkAqua,
        ])
        return nm == AppKit.NSAppearanceNameDarkAqua
    except Exception:
        return False


_TOK_DARK = {
    "panel_top":    (8, 10, 18, 0.52),
    "panel_base":   (22, 28, 38, 0.98),
    "panel_rim":    (148, 163, 184, 0.22),
    # Gradient white: pure white at top of hierarchy, cooling slate-tinted
    # whitish at lower tiers (warm → cool descent).
    "fg94":         (255, 255, 255, 1.0),
    "fg60":         (226, 232, 240, 0.86),
    "fg32":         (203, 213, 225, 0.55),
    "fg14":         (148, 163, 184, 0.28),
    "hover":        (255, 255, 255, 0.05),
    "hover_edge":   (94, 234, 212, 0.55),
    "sep":          (148, 163, 184, 0.10),
    "subj":         (226, 232, 240, 0.82),
    "row_bg":       (22, 28, 38, 1.0),
    "section_lbl":  (94, 234, 212, 0.55),
    "input_bg":     (12, 17, 28, 0.86),
    "input_border": (148, 163, 184, 0.18),
    "input_focus":  (94, 234, 212, 0.55),
    "input_text":   (255, 255, 255, 1.0),
    "input_ph":     (203, 213, 225, 0.45),
    "shadow":       (0, 0, 0, 0.55),
    "card_bg":      (255, 255, 255, 0.04),
    "btn_text":     (8, 12, 18, 1.0),
    "chip_bg":      (255, 255, 255, 0.04),
    "chip_stroke":  (148, 163, 184, 0.22),
}

_TOK_LIGHT = {
    "panel_top":    (252, 252, 254, 0.96),
    "panel_base":   (243, 244, 248, 0.98),
    "panel_rim":    (15, 23, 42, 0.18),
    # Pure black for body text, with stepped near-blacks for hierarchy.
    "fg94":         (0, 0, 0, 1.0),
    "fg60":         (28, 30, 35, 1.0),
    "fg32":         (60, 64, 72, 1.0),
    "fg14":         (110, 115, 125, 0.7),
    "hover":        (15, 23, 42, 0.06),
    "hover_edge":   (13, 148, 136, 0.95),
    "sep":          (148, 163, 184, 0.18),
    "subj":         (28, 30, 35, 1.0),
    "row_bg":       (250, 250, 252, 1.0),
    "section_lbl":  (13, 148, 136, 1.0),
    "input_bg":     (255, 255, 255, 1.0),
    "input_border": (15, 23, 42, 0.18),
    "input_focus":  (13, 148, 136, 0.75),
    "input_text":   (0, 0, 0, 1.0),
    "input_ph":     (60, 64, 72, 0.85),
    "shadow":       (15, 23, 42, 0.22),
    "card_bg":      (255, 255, 255, 0.96),
    "btn_text":     (8, 12, 18, 1.0),
    "chip_bg":      (255, 255, 255, 0.65),
    "chip_stroke":  (15, 23, 42, 0.18),
}


def _T(key: str):
    tok = _TOK_DARK if _is_dark() else _TOK_LIGHT
    r, g, b, a = tok[key]
    return _c(r, g, b, a)


# Public color helpers (theme-aware)
BG_TINT    = lambda: _T("panel_top")
SURFACE    = lambda: _T("panel_base")
STONE_DEEP = lambda: _T("panel_top")
ACCENT_MINT     = lambda: _c(13, 148, 136, 0.95) if not _is_dark() else _c(94, 234, 212, 0.95)
ACCENT_MINT_DIM = lambda: _c(13, 148, 136, 0.32) if not _is_dark() else _c(94, 234, 212, 0.28)
ACCENT_SKY = lambda: _c(2, 132, 199, 0.65) if not _is_dark() else _c(125, 211, 252, 0.55)
GOLD       = lambda: _c(217, 119, 6, 1.0) if not _is_dark() else _c(251, 191, 36, 1.0)
STAR_COL   = lambda: _c(234, 179, 8, 1.0) if not _is_dark() else _c(253, 224, 71, 1.0)
W94        = lambda: _T("fg94")
W60        = lambda: _T("fg60")
W32        = lambda: _T("fg32")
W14        = lambda: _T("fg14")
HOVER      = lambda: _T("hover")
HOVER_EDGE = lambda: _T("hover_edge")
SEP        = lambda: _T("sep")
DANGER     = lambda: _c(220, 38, 38, 0.92) if not _is_dark() else _c(239, 68, 68, 0.85)
SRC_BLUE   = lambda: _c( 37,  99, 235) if not _is_dark() else _c( 59, 130, 246)
SRC_VIOLET = lambda: _c(124,  58, 237) if not _is_dark() else _c(139,  92, 246)
SRC_SLATE  = lambda: _c( 71,  85, 105) if not _is_dark() else _c(100, 116, 139)

def _src_col(source: str):
    return {"clipboard": SRC_BLUE(),
            "window": SRC_VIOLET(),
            "screen": SRC_VIOLET()}.get(source, SRC_SLATE())


def _set_theme(pref: str) -> None:
    """Mutate the global theme preference (used by the toggle button)."""
    global _THEME_PREF
    if pref in ("light", "dark", "auto"):
        _THEME_PREF = pref


# ── Panel background drawn in drawRect_ (avoids CGColor GC / sphere artifact) ─
class _PanelBg(AppKit.NSView):
    """Dark rounded panel — draws background in drawRect_ to avoid CGColor GC bug."""
    _is_detail = objc.ivar()

    def initWithFrame_detail_(self, frame, is_detail):
        self = objc.super(_PanelBg, self).initWithFrame_(frame)
        if self is None: return None
        self._is_detail = is_detail
        return self

    def isOpaque(self): return False

    def drawRect_(self, rect):
        bounds = self.bounds()
        bw = bounds.size.width
        bh = bounds.size.height
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, CORNER, CORNER)
        ctx = AppKit.NSGraphicsContext.currentContext()
        ctx.saveGraphicsState()
        path.addClip()
        # Soft vertical wash — looks Mac-native in both light and dark.
        AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
            BG_TINT(), SURFACE(),
        ).drawInRect_angle_(bounds, 270.0)
        ctx.restoreGraphicsState()
        rim_path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSMakeRect(0.5, 0.5, bw - 1, bh - 1), CORNER - 0.5, CORNER - 0.5)
        _T("panel_rim").setStroke()
        rim_path.setLineWidth_(1.0)
        rim_path.stroke()


# ── Fonts ─────────────────────────────────────────────────────────────────────
def _didot(size):
    for name in ("Didot", "GFS Didot", "Georgia"):
        f = AppKit.NSFont.fontWithName_size_(name, size)
        if f: return f
    return AppKit.NSFont.systemFontOfSize_weight_(size, AppKit.NSFontWeightLight)

def _sf(size, weight=None):
    return (AppKit.NSFont.systemFontOfSize_weight_(size, weight)
            if weight is not None else AppKit.NSFont.systemFontOfSize_(size))


def _round(size, weight=None):
    """Prefer SF Pro Rounded for UI chrome — softer, more Mac-native."""
    base = AppKit.NSFont.fontWithName_size_("SF Pro Rounded", size)
    if base is None:
        return _sf(size, weight)
    if weight is None:
        return base
    try:
        d = base.fontDescriptor().fontDescriptorByAddingAttributes_({
            AppKit.NSFontTraitsAttribute: {AppKit.NSFontWeightTrait: float(weight)},
        })
        out = AppKit.NSFont.fontWithDescriptor_size_(d, size)
        if out:
            return out
    except Exception:
        pass
    return _sf(size, weight)


ROW_META_FONT = _round(11)
ROW_TITLE_FONT = _round(14, AppKit.NSFontWeightSemibold)
ROW_SUBJECT_FONT = _round(12)
ROW_TAG_FONT = _round(9, AppKit.NSFontWeightSemibold)
ROW_ACTIVITY_FONT = _round(9)
ROW_STAR_FONT = _round(18, AppKit.NSFontWeightMedium)


# ── Symbol helper ─────────────────────────────────────────────────────────────
def _sym(name, pts, wt=None):
    try:
        w = wt if wt is not None else AppKit.NSFontWeightRegular
        cfg = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(pts, w)
        img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        return img.imageWithSymbolConfiguration_(cfg) if img else None
    except Exception:
        return None


def _prefers_reduced_motion() -> bool:
    """System Reduce Motion — shorter / no panel animations (WCAG 2.3.3)."""
    try:
        dom = NSUserDefaults.standardUserDefaults().persistentDomainForName_("NSGlobalDomain")
        if isinstance(dom, dict):
            v = dom.get("AppleReduceMotionEnabled")
            if v is not None:
                return bool(v)
    except Exception:
        pass
    try:
        ws = AppKit.NSWorkspace.sharedWorkspace()
        if hasattr(ws, "accessibilityDisplayShouldReduceMotion"):
            return bool(ws.accessibilityDisplayShouldReduceMotion())
    except Exception:
        pass
    return False


def _draw_sf_symbol(
    name: str,
    point_size: float,
    color: AppKit.NSColor,
    center_x: float,
    center_y: float,
) -> bool:
    """Draw an SF Symbol tinted with ``color`` — fallback return False if unavailable."""
    try:
        img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, name)
        if img is None:
            return False
        cfg = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
            point_size, AppKit.NSFontWeightMedium)
        img = img.imageWithSymbolConfiguration_(cfg)
        if hasattr(AppKit.NSImageSymbolConfiguration, "configurationWithPaletteColors_"):
            pal = AppKit.NSImageSymbolConfiguration.configurationWithPaletteColors_([color])
            img = img.imageWithSymbolConfiguration_(pal)
        side = point_size + 6
        rect = AppKit.NSMakeRect(center_x - side / 2, center_y - side / 2, side, side)
        img.drawInRect_(rect)
        return True
    except Exception:
        return False


# ── ObjC subclasses ───────────────────────────────────────────────────────────

class _FieldDelegate(AppKit.NSObject):
    _on_change = objc.ivar()
    _on_escape = objc.ivar()
    _on_return = objc.ivar()
    _on_up     = objc.ivar()
    _on_down   = objc.ivar()

    def initWith_escape_return_(self, on_change, on_escape, on_return):
        self = objc.super(_FieldDelegate, self).init()
        if self is None: return None
        self._on_change = on_change
        self._on_escape = on_escape
        self._on_return = on_return
        self._on_up = None
        self._on_down = None
        return self

    @objc.python_method
    def setNavCallbacks_(self, on_up, on_down):
        self._on_up = on_up
        self._on_down = on_down

    def controlTextDidChange_(self, n):
        if self._on_change:
            self._on_change(str(n.object().stringValue()))

    def control_textView_doCommandBySelector_(self, c, tv, sel):
        if sel == b"cancelOperation:":
            if self._on_escape: self._on_escape()
            return True
        if sel == b"insertNewline:" and self._on_return:
            self._on_return(); return True
        if sel == b"moveUp:" and self._on_up:
            self._on_up(); return True
        if sel == b"moveDown:" and self._on_down:
            self._on_down(); return True
        return False


class _WinDelegate(AppKit.NSObject):
    _fn = objc.ivar()
    def initWithFn_(self, fn):
        self = objc.super(_WinDelegate, self).init()
        if self is None: return None
        self._fn = fn; return self
    def windowDidResignKey_(self, _): self._fn()


class _OverlayPanel(AppKit.NSPanel):
    """Borderless panel with ⌘-shortcut hooks for the focused row."""
    _shortcut_handler = objc.ivar()

    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        return True

    @objc.python_method
    def setShortcutHandler_(self, h):
        self._shortcut_handler = h

    def performKeyEquivalent_(self, event):
        h = self._shortcut_handler
        if h is not None:
            try:
                if h(event):
                    return True
            except Exception:
                pass
        return objc.super(_OverlayPanel, self).performKeyEquivalent_(event)


class _GoldBtn(AppKit.NSView):
    _cb      = objc.ivar()
    _title   = objc.ivar()
    _hovered = objc.ivar()

    def initWithTitle_frame_cb_(self, title, frame, cb):
        self = objc.super(_GoldBtn, self).initWithFrame_(frame)
        if self is None: return None
        self._cb = cb; self._title = title; self._hovered = False
        self._track()
        return self

    def _track(self):
        for a in list(self.trackingAreas()): self.removeTrackingArea_(a)
        opts = (
            AppKit.NSTrackingMouseEnteredAndExited
            | AppKit.NSTrackingActiveAlways
            | AppKit.NSTrackingInVisibleRect
        )
        self.addTrackingArea_(AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), opts, self, None))

    def updateTrackingAreas(self): self._track()
    def setHovered_(self, v):
        if self._hovered != v:
            self._hovered = v
            self.setNeedsDisplay_(True)

    def mouseEntered_(self, _): self.setHovered_(True)
    def mouseExited_(self,  _): self.setHovered_(False)
    def mouseDown_(self, _):
        if self._cb: self._cb()
    def acceptsFirstResponder(self): return False

    def drawRect_(self, rect):
        bounds = self.bounds()
        h = bounds.size.height
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, h / 2, h / 2)
        # Single accent fill (looks fresh in both light + dark) with subtle hover tint.
        if _is_dark():
            base = _c(45, 212, 191, 1.0)
            edge = _c(94, 234, 212, 0.6)
        else:
            base = _c(13, 148, 136, 1.0)
            edge = _c(13, 148, 136, 0.7)
        if self._hovered:
            base = base.colorWithAlphaComponent_(0.88)
        base.setFill(); path.fill()
        # Top hairline gloss for tactility
        gloss = AppKit.NSMakeRect(2, h - 1.2, bounds.size.width - 4, 1)
        AppKit.NSColor.colorWithWhite_alpha_(1.0, 0.18).setFill()
        AppKit.NSBezierPath.bezierPathWithRect_(gloss).fill()
        # Outer rim
        edge.setStroke()
        path.setLineWidth_(1.0)
        path.stroke()
        a = {AppKit.NSFontAttributeName: _round(13, AppKit.NSFontWeightSemibold),
             AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor()}
        s = AppKit.NSAttributedString.alloc().initWithString_attributes_(self._title, a)
        sz = s.size()
        s.drawAtPoint_(AppKit.NSMakePoint((bounds.size.width-sz.width)/2,
                                          (bounds.size.height-sz.height)/2))


class _TabBtn(AppKit.NSView):
    _cb      = objc.ivar()
    _title   = objc.ivar()
    _active  = objc.ivar()
    _hovered = objc.ivar()

    def initWithTitle_frame_active_cb_(self, title, frame, active, cb):
        self = objc.super(_TabBtn, self).initWithFrame_(frame)
        if self is None: return None
        self._cb = cb; self._title = title
        self._active = active; self._hovered = False
        self._track()
        return self

    def _track(self):
        for a in list(self.trackingAreas()): self.removeTrackingArea_(a)
        self.addTrackingArea_(AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(),
            AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveInActiveApp,
            self, None))

    def updateTrackingAreas(self): self._track()
    def mouseEntered_(self, _): self._hovered = True;  self.setNeedsDisplay_(True)
    def mouseExited_(self,  _): self._hovered = False; self.setNeedsDisplay_(True)
    def mouseDown_(self, _):
        if self._cb: self._cb()

    def setActive_(self, v):
        self._active = v; self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        bounds = self.bounds()
        h = bounds.size.height
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, h/2, h/2)
        if self._active:
            _T("hover").colorWithAlphaComponent_(0.85).setFill()
            path.fill()
        elif self._hovered:
            _T("hover").setFill()
            path.fill()
        col = W94() if self._active else W60()
        wt  = AppKit.NSFontWeightSemibold if self._active else AppKit.NSFontWeightMedium
        attrs = {AppKit.NSFontAttributeName: _round(11, wt),
                 AppKit.NSForegroundColorAttributeName: col,
                 AppKit.NSKernAttributeName: 0.4}
        s  = AppKit.NSAttributedString.alloc().initWithString_attributes_(self._title, attrs)
        sz = s.size()
        s.drawAtPoint_(AppKit.NSMakePoint((bounds.size.width-sz.width)/2,
                                          (bounds.size.height-sz.height)/2 + 0.5))


class _ActionBtn(AppKit.NSView):
    _cb      = objc.ivar()
    _title   = objc.ivar()
    _hovered = objc.ivar()
    _danger  = objc.ivar()
    _tint_c  = objc.ivar()

    def initWithTitle_frame_tintColor_danger_cb_(self, title, frame, tint, danger, cb):
        self = objc.super(_ActionBtn, self).initWithFrame_(frame)
        if self is None: return None
        self._cb = cb; self._title = title
        self._hovered = False; self._danger = danger; self._tint_c = tint
        self._track()
        return self

    def _track(self):
        for a in list(self.trackingAreas()): self.removeTrackingArea_(a)
        self.addTrackingArea_(AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(),
            AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveInActiveApp,
            self, None))

    def updateTrackingAreas(self): self._track()
    def mouseEntered_(self, _): self._hovered = True;  self.setNeedsDisplay_(True)
    def mouseExited_(self,  _): self._hovered = False; self.setNeedsDisplay_(True)
    def mouseDown_(self, _):
        if self._cb: self._cb()

    def setTitle_(self, t): self._title = t; self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        bounds = self.bounds()
        h = bounds.size.height
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, 8, 8)
        if self._danger:
            (DANGER().colorWithAlphaComponent_(0.20)
             if self._hovered else
             DANGER().colorWithAlphaComponent_(0.08)).setFill()
            path.fill()
        elif self._hovered and self._tint_c:
            self._tint_c.colorWithAlphaComponent_(0.18).setFill()
            path.fill()
            self._tint_c.colorWithAlphaComponent_(0.55).setStroke()
            path.setLineWidth_(1.0)
            path.stroke()
            path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, 8, 8)
        elif self._hovered:
            _T("hover").colorWithAlphaComponent_(0.18).setFill()
            path.fill()
        else:
            _T("card_bg").setFill()
            path.fill()
            _T("input_border").setStroke()
            path.setLineWidth_(1.0)
            path.stroke()
            path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, 8, 8)
        col = (DANGER() if self._danger else
               (self._tint_c if self._tint_c else W94()))
        a = {AppKit.NSFontAttributeName: _round(12, AppKit.NSFontWeightMedium),
             AppKit.NSForegroundColorAttributeName: col}
        s  = AppKit.NSAttributedString.alloc().initWithString_attributes_(self._title, a)
        sz = s.size()
        s.drawAtPoint_(AppKit.NSMakePoint((bounds.size.width-sz.width)/2,
                                          (bounds.size.height-sz.height)/2))


class _StarBtn(AppKit.NSView):
    _mid = objc.ivar()
    _starred = objc.ivar()
    _hovered = objc.ivar()
    _cb = objc.ivar()

    def initWithMemoryId_starred_cb_(self, mid, starred, cb):
        self = objc.super(_StarBtn, self).initWithFrame_(AppKit.NSMakeRect(0, 0, 24, 24))
        if self is None: return None
        self._mid = mid; self._starred = bool(starred); self._hovered = False; self._cb = cb
        self._track()
        return self

    def _track(self):
        for a in list(self.trackingAreas()): self.removeTrackingArea_(a)
        self.addTrackingArea_(AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(),
            AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveInActiveApp,
            self, None))

    def updateTrackingAreas(self): self._track()
    def mouseEntered_(self, _): self._hovered = True; self.setNeedsDisplay_(True)
    def mouseExited_(self, _): self._hovered = False; self.setNeedsDisplay_(True)

    def setStarred_(self, starred):
        self._starred = bool(starred)
        self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        if self._cb:
            self._cb(self._mid, self)

    def drawRect_(self, rect):
        bounds = self.bounds()
        col = STAR_COL() if self._starred else (W60() if self._hovered else W32())
        attrs = {
            AppKit.NSFontAttributeName: _sf(18, AppKit.NSFontWeightMedium),
            AppKit.NSForegroundColorAttributeName: col,
        }
        s = AppKit.NSAttributedString.alloc().initWithString_attributes_(
            "★" if self._starred else "☆", attrs)
        sz = s.size()
        s.drawAtPoint_(AppKit.NSMakePoint(
            (bounds.size.width - sz.width) / 2,
            (bounds.size.height - sz.height) / 2 - 1,
        ))


class _Row(AppKit.NSView):
    _active_hover_row = None
    _scroll_suppressed = False

    _hovered   = objc.ivar()
    _acc       = objc.ivar()
    _text      = objc.ivar()
    _full_text = objc.ivar()
    _mid       = objc.ivar()
    _starred   = objc.ivar()
    _detail_fn = objc.ivar()
    _delete_fn = objc.ivar()
    _flash_fn  = objc.ivar()
    _star_fn   = objc.ivar()
    _title     = objc.ivar()
    _subject   = objc.ivar()
    _meta      = objc.ivar()
    _stamp     = objc.ivar()
    _tag       = objc.ivar()
    _activity  = objc.ivar()
    _activity_c = objc.ivar()
    _star_x    = objc.ivar()
    _star_w    = objc.ivar()
    _minimal   = objc.ivar()
    _focused   = objc.ivar()
    _app_name  = objc.ivar()  # used by right-click "never capture this app"
    _exclude_fn = objc.ivar()
    _rich      = objc.ivar()

    def initWithFrame_(self, frame):
        self = objc.super(_Row, self).initWithFrame_(frame)
        if self is None: return None
        self._hovered = False; self._acc = None
        self._text = ""; self._full_text = ""; self._mid = None
        self._starred = False
        self._detail_fn = None; self._delete_fn = None; self._flash_fn = None; self._star_fn = None
        self._title = ""; self._subject = ""; self._meta = ""; self._stamp = ""
        self._tag = ""; self._activity = ""; self._activity_c = SRC_SLATE()
        self._star_x = 0.0; self._star_w = 24.0
        self._minimal = False
        self._focused = False
        self._app_name = ""
        self._exclude_fn = None
        self._rich = False
        self._track()
        return self

    @objc.python_method
    def setFocused_(self, focused: bool):
        new_v = bool(focused)
        if self._focused != new_v:
            self._focused = new_v
            self.setNeedsDisplay_(True)

    def _track(self):
        for a in list(self.trackingAreas()): self.removeTrackingArea_(a)
        self.addTrackingArea_(AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(),
            AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveInActiveApp,
            self, None))

    def updateTrackingAreas(self): self._track()
    def mouseEntered_(self, _):
        if _Row._scroll_suppressed:
            return
        prev = _Row._active_hover_row
        if prev and prev is not self:
            prev._hovered = False
            prev.setNeedsDisplay_(True)
        _Row._active_hover_row = self
        self._hovered = True
        self.setNeedsDisplay_(True)

    def mouseExited_(self,  _):
        if _Row._active_hover_row is self:
            _Row._active_hover_row = None
        self._hovered = False
        self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        bounds = self.bounds()
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        # Star is centered vertically; allow a generous full-row hit zone.
        if (
            self._star_fn and self._mid
            and self._star_x <= point.x <= self._star_x + self._star_w
            and 4 <= point.y <= bounds.size.height - 4
        ):
            self._star_fn(self._mid, self)
            return
        if self._detail_fn and self._mid:
            self._detail_fn(self._mid)

    def setStarred_(self, starred):
        self._starred = bool(starred)
        self.setNeedsDisplay_(True)

    def rightMouseDown_(self, event):
        menu = AppKit.NSMenu.alloc().init()
        ci = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Copy Text", b"_rowCopy:", "")
        ci.setTarget_(self); menu.addItem_(ci)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        di = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Delete Memory", b"_rowDelete:", "")
        di.setTarget_(self); menu.addItem_(di)
        # "Never capture <app>" — only show when we have an app name and
        # an exclusion callback wired up. Persists to the config table so
        # the daemon honors it on the next capture cycle.
        app = (self._app_name or "").strip()
        if app and self._exclude_fn is not None:
            menu.addItem_(AppKit.NSMenuItem.separatorItem())
            label = f"Never capture {app}"
            ei = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                label, b"_rowExcludeApp:", "")
            ei.setTarget_(self); menu.addItem_(ei)
        AppKit.NSMenu.popUpContextMenu_withEvent_forView_(menu, event, self)

    @objc.typedSelector(b"v@:@")
    def _rowCopy_(self, sender):
        pb = AppKit.NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(self._full_text or self._text or "", AppKit.NSPasteboardTypeString)
        if self._flash_fn: self._flash_fn("Copied")

    @objc.typedSelector(b"v@:@")
    def _rowDelete_(self, sender):
        if self._delete_fn and self._mid: self._delete_fn(self._mid)

    @objc.typedSelector(b"v@:@")
    def _rowExcludeApp_(self, sender):
        if self._exclude_fn and self._app_name:
            self._exclude_fn(self._app_name)

    def drawRect_(self, rect):
        bounds = self.bounds()
        hgt = bounds.size.height
        is_min = bool(self._minimal)
        if self._focused:
            # Keyboard-focus highlight: a soft mint wash with a 2 px left rail.
            ACCENT_MINT().colorWithAlphaComponent_(0.12).setFill()
            AppKit.NSBezierPath.fillRect_(bounds)
            ACCENT_MINT().setFill()
            AppKit.NSBezierPath.fillRect_(
                AppKit.NSMakeRect(0, 0, 2, bounds.size.height))
        elif self._hovered and not _Row._scroll_suppressed:
            HOVER().setFill()
            AppKit.NSBezierPath.fillRect_(bounds)
        # Quiet source dot on every row — no walls, no rails.
        if self._acc:
            self._acc.setFill()
            dy = (hgt - 6.0) / 2.0
            AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                AppKit.NSMakeRect(20.0, dy, 6.0, 6.0)).fill()
        if not is_min:
            SEP().setFill()
            AppKit.NSBezierPath.fillRect_(AppKit.NSMakeRect(20, 0, bounds.size.width - 40, 1))

        def draw_left(text, font, color, x, y):
            if not text:
                return
            attrs = {
                AppKit.NSFontAttributeName: font,
                AppKit.NSForegroundColorAttributeName: color,
            }
            AppKit.NSAttributedString.alloc().initWithString_attributes_(
                text, attrs).drawAtPoint_(AppKit.NSMakePoint(x, y))

        def draw_left_wrapped(text, font, color, rect):
            if not text:
                return
            para = AppKit.NSMutableParagraphStyle.alloc().init()
            para.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
            para.setLineSpacing_(1.5)
            attrs = {
                AppKit.NSFontAttributeName: font,
                AppKit.NSForegroundColorAttributeName: color,
                AppKit.NSParagraphStyleAttributeName: para,
            }
            s = AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs)
            s.drawWithRect_options_(
                rect,
                AppKit.NSStringDrawingUsesLineFragmentOrigin
                | AppKit.NSStringDrawingUsesFontLeading,
            )

        def draw_right(text, font, color, right, y):
            if not text:
                return
            attrs = {
                AppKit.NSFontAttributeName: font,
                AppKit.NSForegroundColorAttributeName: color,
            }
            s = AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs)
            sz = s.size()
            s.drawAtPoint_(AppKit.NSMakePoint(right - sz.width, y))

        right = bounds.size.width - 18
        if is_min:
            # ── Minimal layout: catchy title + faint relative time on the right ──
            ty = (hgt - 16.0) / 2.0
            draw_left(self._title, _round(14, AppKit.NSFontWeightSemibold),
                      W94(), 36, ty)
            draw_right(self._stamp or self._meta, _round(11), W32(), right, ty + 1)
            return

        # ── Compact 2-line layout (search/recent/starred) ─────────────────────
        # Title sits center-aligned vertically when there's no subject;
        # otherwise it sits in the upper third with the subject just under it.
        # The right side carries ONE quiet date+time line — no app, no split.
        # The stamp must end BEFORE the star so they never overlap.
        stamp_right = self._star_x - 14
        if self._subject:
            if self._rich:
                # Roomier stacked layout used by Timeline for readability.
                title_rect = AppKit.NSMakeRect(36, hgt - 34.0, max(120.0, stamp_right - 44), 22.0)
                subj_rect = AppKit.NSMakeRect(36, 8.0, max(120.0, stamp_right - 44), max(18.0, hgt - 44.0))
                draw_left_wrapped(self._title, _round(14, AppKit.NSFontWeightSemibold), W94(), title_rect)
                draw_left_wrapped(self._subject, _round(11), _T("subj"), subj_rect)
            else:
                ty = hgt - 26.0
                sy = ty - 22.0
                draw_left(self._title, ROW_TITLE_FONT, W94(), 36, ty)
                draw_left(self._subject, ROW_SUBJECT_FONT, _T("subj"), 36, sy)
        else:
            ty = (hgt - 16.0) / 2.0 + 1.0
            draw_left(self._title, ROW_TITLE_FONT, W94(), 36, ty)
        # Right column: single date+time line (centered vertically)
        ry = (hgt - 14.0) / 2.0
        draw_right(self._stamp, _round(11), W60(), stamp_right, ry)

        star_col = STAR_COL() if self._starred else (W60() if self._hovered else W32())
        cx = self._star_x + self._star_w / 2
        cy = bounds.size.height / 2
        if not _draw_sf_symbol(
            "star.fill" if self._starred else "star",
            15,
            star_col,
            cx,
            cy,
        ):
            star_attrs = {
                AppKit.NSFontAttributeName: ROW_STAR_FONT,
                AppKit.NSForegroundColorAttributeName: star_col,
            }
            fb = "★" if self._starred else "☆"
            star = AppKit.NSAttributedString.alloc().initWithString_attributes_(fb, star_attrs)
            star_sz = star.size()
            star.drawAtPoint_(AppKit.NSMakePoint(
                self._star_x + (self._star_w - star_sz.width) / 2,
                cy - star_sz.height / 2,
            ))

        # Activity tag and source pill are intentionally omitted in the new
        # layout — the source dot at the left + the title carry that signal.


class _ResultsScrollView(AppKit.NSScrollView):
    """Suppresses hover while scrolling without scanning every row."""
    _scroll_timer = objc.ivar()

    def initWithFrame_(self, frame):
        self = objc.super(_ResultsScrollView, self).initWithFrame_(frame)
        if self is None: return None
        self._scroll_timer = None
        return self

    def _set_scrolling(self, value: bool):
        _Row._scroll_suppressed = value
        row = _Row._active_hover_row
        if row:
            row._hovered = False
            row.setNeedsDisplay_(True)
            _Row._active_hover_row = None

    def scrollWheel_(self, event):
        self._set_scrolling(True)
        objc.super(_ResultsScrollView, self).scrollWheel_(event)
        if self._scroll_timer:
            self._scroll_timer.cancel()
        self._scroll_timer = threading.Timer(
            0.08, lambda: AppHelper.callAfter(self._set_scrolling, False))
        self._scroll_timer.daemon = True
        self._scroll_timer.start()


class _SummarySuggestPill(AppKit.NSView):
    """One-tap starter prompt for the Summary tab (theme-aware pill)."""

    _title = objc.ivar()
    _cb = objc.ivar()
    _hovered = objc.ivar()

    def initWithFrame_label_callback_(self, frame, label, cb):
        self = objc.super(_SummarySuggestPill, self).initWithFrame_(frame)
        if self is None:
            return None
        self._title = (label or "").strip()
        self._cb = cb
        self._hovered = False
        self._track()
        return self

    def _track(self):
        for a in list(self.trackingAreas()):
            self.removeTrackingArea_(a)
        self.addTrackingArea_(
            AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(),
                AppKit.NSTrackingMouseEnteredAndExited
                | AppKit.NSTrackingActiveInActiveApp,
                self,
                None,
            )
        )

    def updateTrackingAreas(self):
        self._track()

    def mouseEntered_(self, _):
        self._hovered = True
        self.setNeedsDisplay_(True)

    def mouseExited_(self, _):
        self._hovered = False
        self.setNeedsDisplay_(True)

    def mouseDown_(self, _event):
        if self._cb is not None:
            try:
                self._cb()
            except Exception:
                pass

    def isOpaque(self):
        return False

    def drawRect_(self, _rect):
        bounds = self.bounds()
        lift = 0.8 if self._hovered else 0.0
        rect = AppKit.NSMakeRect(
            0.5, 0.5 + lift, bounds.size.width - 1.0, bounds.size.height - 1.0,
        )
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, 9.0, 9.0,
        )
        if self._hovered:
            HOVER().setFill()
        else:
            _T("chip_bg").setFill()
        path.fill()
        path.setLineWidth_(0.75)
        (HOVER_EDGE() if self._hovered else _T("chip_stroke")).setStroke()
        path.stroke()
        font = _round(11, AppKit.NSFontWeightMedium)
        col = W94() if self._hovered else W60()
        para = AppKit.NSMutableParagraphStyle.alloc().init()
        para.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        attrs = {
            AppKit.NSFontAttributeName: font,
            AppKit.NSForegroundColorAttributeName: col,
            AppKit.NSParagraphStyleAttributeName: para,
        }
        inset = AppKit.NSMakeRect(10, 5 + lift, bounds.size.width - 20, bounds.size.height - 10)
        s = AppKit.NSAttributedString.alloc().initWithString_attributes_(self._title, attrs)
        s.drawWithRect_options_(inset, AppKit.NSStringDrawingUsesLineFragmentOrigin)


def _measure_wrapped_text_height(text: str, font, width: float) -> float:
    """Pixel-precise height for word-wrapped text used in cards."""
    body = (text or "").strip() or " "
    attrs = {AppKit.NSFontAttributeName: font}
    a = AppKit.NSAttributedString.alloc().initWithString_attributes_(body, attrs)
    rect = a.boundingRectWithSize_options_(
        AppKit.NSMakeSize(max(80.0, width), 8000.0),
        AppKit.NSStringDrawingUsesLineFragmentOrigin
        | AppKit.NSStringDrawingUsesFontLeading,
    )
    return float(rect.size.height) + 4.0


def _measure_pill_width(text: str, max_w: float = 280.0) -> float:
    font = _round(11, AppKit.NSFontWeightMedium)
    attrs = {AppKit.NSFontAttributeName: font}
    a = AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs)
    w = float(a.size().width) + 22.0
    return min(max_w, max(72.0, w))


# ── Pure Python helpers ───────────────────────────────────────────────────────

_PSYCH_FACTS: tuple[str, ...] = (
    "Spacing study sessions beats cramming. Distributed practice wins.",
    "Writing by hand slows you down just enough to remember more.",
    "The brain consolidates memory during sleep; short naps help transfer.",
    "Testing yourself beats rereading. Retrieval strengthens recall.",
    "Chunking turns random digits into meaningful groups you can hold.",
    "Mood at encoding colors what you later recall about an event.",
    "Interleaving topics feels harder but builds flexible skills.",
    "Forgetting is normal; each recall rebuilds the trace stronger.",
    "Elaboration is asking why; it links new facts to what you know.",
    "Context cues matter: same room, same mood can jog memory.",
    "The peak end rule skews how we remember experiences.",
    "Cognitive load drops when you offload steps to a checklist.",
    "Names fade fast without rehearsal within the first day.",
    "Mnemonics trade upfront effort for durable retrieval hooks.",
    "Stress narrows attention; calm recall beats anxious cramming.",
)


def _wrap_line_soft(line: str, max_len: int) -> list[str]:
    line = (line or "").rstrip()
    if not line:
        return []
    if len(line) <= max_len:
        return [line]
    out: list[str] = []
    rest = line
    while len(rest) > max_len:
        chunk = rest[:max_len]
        cut = chunk.rfind(" ")
        if cut < max_len // 2:
            cut = max_len
            chunk = rest[:cut]
        else:
            chunk = rest[:cut]
        out.append(chunk.rstrip())
        rest = rest[len(chunk) :].lstrip()
    if rest:
        out.append(rest)
    return out


def _format_raw_capture_for_display(raw: str, max_line: int = 96) -> str:
    """Break dense OCR into paragraphs and soft-wrap very long lines for reading."""
    s = (raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"\r\n?", "\n", s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{4,}", "\n\n\n", s)
    blocks_out: list[str] = []
    for block in s.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        lines_out: list[str] = []
        for line in block.split("\n"):
            lines_out.extend(_wrap_line_soft(line, max_line))
        blocks_out.append("\n".join(lines_out))
    return "\n\n".join(blocks_out).strip()


def _psychology_fact() -> str:
    """Short line shown when the overlay opens—no personalization."""
    return random.choice(_PSYCH_FACTS)


def _rel(ts: float) -> str:
    d = time.time() - ts
    if d < 60:    return "just now"
    if d < 3600:  return f"{int(d/60)}m ago"
    if d < 86400: return f"{int(d/3600)}h ago"
    return time.strftime("%b %d", time.localtime(ts))


def _stamp(ts: float) -> str:
    return time.strftime("%b %d %I:%M %p", time.localtime(ts)).replace(" 0", " ")


def _text_width(text: str, font) -> float:
    attrs = {AppKit.NSFontAttributeName: font}
    return AppKit.NSAttributedString.alloc().initWithString_attributes_(
        text, attrs).size().width


def _fit_plain_text(text: str, font, width: float) -> str:
    text = truncate_text(text, 180)
    if not text or _text_width(text, font) <= width:
        return text
    words = text.split()
    while len(words) > 1:
        candidate = " ".join(words[:-1])
        if _text_width(candidate, font) <= width:
            return candidate
        words = words[:-1]
    word = words[0] if words else text
    while len(word) > 3 and _text_width(word, font) > width:
        word = word[:-1].rstrip(" .,-")
    return word


def _fit_subject_line(heading: str, ts: float, font, width: float) -> str:
    stamp = _stamp(ts)
    suffix = f"   {stamp}"
    heading = short_subject(heading, max_words=5)
    subject = f"{heading}{suffix}"
    if _text_width(subject, font) <= width:
        return subject
    heading_width = max(30.0, width - _text_width(suffix, font) - 4)
    return f"{_fit_plain_text(heading, font, heading_width)}{suffix}"


def _date_header(ts: float) -> str:
    d = date.fromtimestamp(ts)
    t = date.today()
    diff = (t - d).days
    if diff == 0:  return "TODAY"
    if diff == 1:  return "YESTERDAY"
    if diff < 7:   return d.strftime("%A").upper()
    return d.strftime("%B %d").upper()


def _lbl(text, font, color=None, align=None, lines=1, wrap=False):
    if wrap:
        tf = AppKit.NSTextField.wrappingLabelWithString_(text)
    else:
        tf = AppKit.NSTextField.labelWithString_(text)
    tf.setFont_(font)
    if color:         tf.setTextColor_(color)
    if align is not None: tf.setAlignment_(align)
    if wrap:
        tf.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
        tf.setUsesSingleLineMode_(False)
        tf.setMaximumNumberOfLines_(lines if lines > 1 else 0)
    else:
        tf.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        if lines != 1:
            tf.setMaximumNumberOfLines_(lines)
    tf.setSelectable_(False)
    return tf


def _kern_lbl(text, font, color, frame_rect):
    attrs = {AppKit.NSFontAttributeName: font,
             AppKit.NSForegroundColorAttributeName: color,
             AppKit.NSKernAttributeName: 2.0}
    tf = AppKit.NSTextField.alloc().initWithFrame_(frame_rect)
    tf.setAttributedStringValue_(
        AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs))
    tf.setBezeled_(False); tf.setDrawsBackground_(False)
    tf.setSelectable_(False); tf.setEditable_(False)
    return tf


class _FocusTextField(AppKit.NSTextField):
    """NSTextField whose shared field editor must also hide the accent focus ring."""

    _focus_cb = objc.ivar()

    def setFocusCallback_(self, cb):
        self._focus_cb = cb

    def acceptsFirstResponder(self):
        return True

    def focusRingMaskBounds(self):
        """Tell AppKit not to draw the system accent (often green) focus rectangle."""
        return AppKit.NSMakeRect(0, 0, 0, 0)

    def _strip_field_editor_focus_ring(self):
        win = self.window()
        if not win:
            return
        try:
            ed = win.fieldEditor_forObject_(True, self)
            if ed is not None:
                ed.setFocusRingType_(AppKit.NSFocusRingTypeNone)
                if hasattr(ed, "setDrawsFocusRingIndicator_"):
                    ed.setDrawsFocusRingIndicator_(False)
        except Exception:
            pass

    def becomeFirstResponder(self):
        ok = objc.super(_FocusTextField, self).becomeFirstResponder()
        if ok:
            self._strip_field_editor_focus_ring()
            AppHelper.callAfter(self._strip_field_editor_focus_ring)
        return ok

    def mouseDown_(self, event):
        if self._focus_cb:
            self._focus_cb()
        win = self.window()
        if win:
            win.makeKeyAndOrderFront_(None)
            win.makeFirstResponder_(self)
        objc.super(_FocusTextField, self).mouseDown_(event)
        AppHelper.callAfter(self._strip_field_editor_focus_ring)


class _InputBg(AppKit.NSView):
    """Search bar container — draws rounded bg in drawRect_ (no CGColor GC)."""
    _field = objc.ivar()
    _focus_cb = objc.ivar()

    def setField_focusCb_(self, field, cb):
        self._field = field
        self._focus_cb = cb

    def focusRingMaskBounds(self):
        return AppKit.NSMakeRect(0, 0, 0, 0)

    def isOpaque(self): return False
    def hitTest_(self, point):
        bounds = self.bounds()
        if (0 <= point.x <= bounds.size.width) and (0 <= point.y <= bounds.size.height):
            return self
        return None

    def mouseDown_(self, event):
        if self._focus_cb:
            self._focus_cb()
        win = self.window()
        if win and self._field:
            win.makeKeyAndOrderFront_(None)
            win.makeFirstResponder_(self._field)
            self._field.selectText_(None)

    def drawRect_(self, rect):
        bounds = self.bounds()
        radius = min(bounds.size.height / 2.0, 14.0)
        # Soft floating capsule with shadow under the field — wraps in own context.
        ctx = AppKit.NSGraphicsContext.currentContext()
        ctx.saveGraphicsState()
        sh = AppKit.NSShadow.alloc().init()
        sh.setShadowColor_(_T("shadow"))
        sh.setShadowBlurRadius_(14.0)
        sh.setShadowOffset_(AppKit.NSMakeSize(0, -3))
        sh.set()
        _T("input_bg").setFill()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, radius, radius).fill()
        ctx.restoreGraphicsState()
        # Subtle hairline border (no shadow on the stroke itself)
        inner = AppKit.NSMakeRect(0.5, 0.5,
                                   bounds.size.width - 1, bounds.size.height - 1)
        ip = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            inner, radius - 0.5, radius - 0.5)
        _T("input_border").setStroke()
        ip.setLineWidth_(1.0)
        ip.stroke()

def _input(frame, ph, size=16, centered=False, lpad=18, focus_cb=None):
    x, y, w, h = frame
    con = _InputBg.alloc().initWithFrame_(AppKit.NSMakeRect(x, y, w, h))
    ph_a = {AppKit.NSForegroundColorAttributeName: W32(),
            AppKit.NSFontAttributeName: _sf(size)}
    tf = _FocusTextField.alloc().initWithFrame_(
        AppKit.NSMakeRect(lpad, (h-size-4)/2, w-lpad-14, size+6))
    tf.setFont_(_sf(size)); tf.setTextColor_(_T("input_text"))
    ph_a[AppKit.NSForegroundColorAttributeName] = _T("input_ph")
    tf.setPlaceholderAttributedString_(
        AppKit.NSAttributedString.alloc().initWithString_attributes_(ph, ph_a))
    tf.setBezeled_(False); tf.setDrawsBackground_(False)
    # Full keyboard focus without the system accent ring (often bright green on dark UI).
    tf.setFocusRingType_(AppKit.NSFocusRingTypeNone)
    try:
        cell = tf.cell()
        if cell is not None:
            cell.setFocusRingType_(AppKit.NSFocusRingTypeNone)
    except Exception:
        pass
    tf.setFocusCallback_(focus_cb)
    if centered: tf.setAlignment_(AppKit.NSTextAlignmentCenter)
    con.addSubview_(tf)
    con.setField_focusCb_(tf, focus_cb)
    con.setFocusRingType_(AppKit.NSFocusRingTypeNone)
    return con, tf


class _SettingsCard(AppKit.NSView):
    """Subtle rounded card background for Settings + Daily sections.

    Fills with a soft tinted surface that adapts to light and dark themes,
    plus a hairline border. No shadow, no gradient overhead — keeps the
    UI calm and Mac-native."""

    def isOpaque(self):
        return False

    def drawRect_(self, _rect):
        b = self.bounds()
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSMakeRect(0.5, 0.5, b.size.width - 1.0, b.size.height - 1.0),
            10.0, 10.0,
        )
        _T("chip_bg").setFill()
        path.fill()
        _T("chip_stroke").setStroke()
        path.setLineWidth_(0.7)
        path.stroke()


def _card(x, y, w, h):
    v = _SettingsCard.alloc().initWithFrame_(AppKit.NSMakeRect(x, y, w, h))
    return v


class _HLine(AppKit.NSView):
    """1-px separator — theme-aware (uses SEP token)."""
    _a = objc.ivar()
    def initWithFrame_alpha_(self, frame, a):
        self = objc.super(_HLine, self).initWithFrame_(frame)
        if self is None: return None
        self._a = a; return self
    def isOpaque(self): return False
    def drawRect_(self, rect):
        col = SEP()
        if self._a and self._a > 0:
            col = col.colorWithAlphaComponent_(min(1.0, self._a))
        col.setFill()
        AppKit.NSBezierPath.fillRect_(rect)

def _hline(x, y, w, a=0.0):
    v = _HLine.alloc().initWithFrame_alpha_(AppKit.NSMakeRect(x, y, w, 1), a)
    v.setAutoresizingMask_(AppKit.NSViewWidthSizable)
    return v


class _SignupHeroCard(AppKit.NSView):
    """Soft frosted card behind first-launch sign-up (mint / ink palette)."""

    def isOpaque(self):
        return False

    def drawRect_(self, rect):
        bounds = self.bounds()
        r = 22.0
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, r, r)
        ctx = AppKit.NSGraphicsContext.currentContext()
        ctx.saveGraphicsState()
        sh = AppKit.NSShadow.alloc().init()
        sh.setShadowColor_(_T("shadow"))
        sh.setShadowBlurRadius_(28.0)
        sh.setShadowOffset_(AppKit.NSMakeSize(0, -10))
        sh.set()
        path.addClip()
        if _is_dark():
            top = _c(26, 34, 48, 1.0)
            bot = _c(10, 22, 32, 1.0)
        else:
            top = _c(255, 255, 255, 0.99)
            bot = _c(240, 253, 250, 0.97)
        AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(top, bot).drawInRect_angle_(
            bounds, 270.0)
        ctx.restoreGraphicsState()
        rim = ACCENT_MINT().colorWithAlphaComponent_(0.22 if not _is_dark() else 0.38)
        rim.setStroke()
        path.setLineWidth_(1.0)
        path.stroke()
        hi = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSInsetRect(bounds, 1, 1), r - 1, r - 1)
        AppKit.NSColor.whiteColor().colorWithAlphaComponent_(0.14 if not _is_dark() else 0.06).setStroke()
        hi.setLineWidth_(0.75)
        hi.stroke()


class _MintHairline(AppKit.NSView):
    """Accent divider for onboarding hero."""

    def isOpaque(self):
        return False

    def drawRect_(self, rect):
        ACCENT_MINT().colorWithAlphaComponent_(0.42 if not _is_dark() else 0.55).setFill()
        AppKit.NSBezierPath.fillRect_(self.bounds())


class _ThemeToggle(AppKit.NSView):
    """Tiny sun/moon pill — flips the theme + tells caller to rebuild."""
    _cb      = objc.ivar()
    _hovered = objc.ivar()

    def initWithFrame_cb_(self, frame, cb):
        self = objc.super(_ThemeToggle, self).initWithFrame_(frame)
        if self is None: return None
        self._cb = cb; self._hovered = False
        self._track()
        return self

    def _track(self):
        for a in list(self.trackingAreas()): self.removeTrackingArea_(a)
        self.addTrackingArea_(
            AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(),
                AppKit.NSTrackingMouseEnteredAndExited
                | AppKit.NSTrackingActiveInActiveApp,
                self, None))

    def updateTrackingAreas(self): self._track()
    def mouseEntered_(self, _): self._hovered = True;  self.setNeedsDisplay_(True)
    def mouseExited_(self,  _): self._hovered = False; self.setNeedsDisplay_(True)
    def mouseDown_(self, _):
        if self._cb: self._cb()

    def isOpaque(self): return False

    def drawRect_(self, rect):
        bounds = self.bounds()
        h = bounds.size.height
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, h / 2, h / 2)
        if self._hovered:
            _T("hover").colorWithAlphaComponent_(0.18).setFill()
            path.fill()
        glyph = "moon.fill" if not _is_dark() else "sun.max.fill"
        col = ACCENT_MINT() if _is_dark() else _T("fg60")
        if not _draw_sf_symbol(glyph, 12, col,
                                bounds.size.width / 2, bounds.size.height / 2):
            attrs = {AppKit.NSFontAttributeName: _round(11),
                     AppKit.NSForegroundColorAttributeName: col}
            t = "Dark" if not _is_dark() else "Light"
            s = AppKit.NSAttributedString.alloc().initWithString_attributes_(t, attrs)
            sz = s.size()
            s.drawAtPoint_(AppKit.NSMakePoint(
                (bounds.size.width - sz.width) / 2,
                (bounds.size.height - sz.height) / 2))


def _scroll_to_top(scroll, doc_height: float, viewport_height: float) -> None:
    y = max(0.0, doc_height - viewport_height)
    clip = scroll.contentView()
    clip.setBoundsOrigin_(AppKit.NSMakePoint(0, y))
    clip.scrollToPoint_(AppKit.NSMakePoint(0, y))
    scroll.reflectScrolledClipView_(clip)
    doc = scroll.documentView()
    if doc is not None:
        doc.scrollPoint_(AppKit.NSMakePoint(0, doc_height))


def _scroll_to_bottom(scroll) -> None:
    clip = scroll.contentView()
    clip.setBoundsOrigin_(AppKit.NSMakePoint(0, 0))
    clip.scrollToPoint_(AppKit.NSMakePoint(0, 0))
    scroll.reflectScrolledClipView_(clip)


# ── Shortcut chips ───────────────────────────────────────────────────────────


class _ShortcutChip(AppKit.NSView):
    """Keyboard shortcut chip: optional title (what it does) + key glyphs.

    Footer chips use a **title + keys** layout so users see both meaning
    and shortcut. Onboarding-only chips pass an empty title and render
    a single centered key line."""

    _title    = objc.ivar()
    _glyph    = objc.ivar()
    _hint     = objc.ivar()
    _hovered  = objc.ivar()
    _cb       = objc.ivar()
    _hover_cb = objc.ivar()
    _exit_cb  = objc.ivar()

    def initWithFrame_title_glyph_hint_callback_(self, frame, title, glyph, hint, cb):
        self = objc.super(_ShortcutChip, self).initWithFrame_(frame)
        if self is None:
            return None
        self._title = (title or "").strip()
        self._glyph = glyph or ""
        self._hint = hint or ""
        self._hovered = False
        self._cb = cb
        self._hover_cb = None
        self._exit_cb = None
        if hint:
            self.setToolTip_(hint)
        self._track()
        return self

    # Back-compat for call sites that only pass glyph + hint.
    def initWithFrame_glyph_hint_callback_(self, frame, glyph, hint, cb):
        return self.initWithFrame_title_glyph_hint_callback_(
            frame, "", glyph, hint, cb,
        )

    def _track(self):
        for a in list(self.trackingAreas()):
            self.removeTrackingArea_(a)
        self.addTrackingArea_(
            AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(),
                AppKit.NSTrackingMouseEnteredAndExited
                | AppKit.NSTrackingActiveInActiveApp,
                self, None,
            )
        )

    def updateTrackingAreas(self):
        self._track()

    def mouseEntered_(self, _):
        self._hovered = True
        self.setNeedsDisplay_(True)
        hcb = self._hover_cb
        if hcb is not None:
            try:
                hcb()
            except Exception:
                pass

    def mouseExited_(self, _):
        self._hovered = False
        self.setNeedsDisplay_(True)
        xcb = self._exit_cb
        if xcb is not None:
            try:
                xcb()
            except Exception:
                pass

    def mouseDown_(self, _event):
        if self._cb is not None:
            try:
                self._cb()
            except Exception:
                pass

    def drawRect_(self, _rect):
        bounds = self.bounds()
        # Lift the inner rect slightly on hover.
        lift = 0.5 if self._hovered else 0.0
        rect = AppKit.NSMakeRect(0.5, 0.5 + lift,
                                  bounds.size.width - 1.0,
                                  bounds.size.height - 1.0)
        corner_r = 5.0 if self._title else rect.size.height / 2.0
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, corner_r, corner_r,
        )
        # Background: barely-there fill, lifts on hover.
        if self._hovered:
            HOVER().setFill()
        else:
            _T("chip_bg").setFill()
        path.fill()
        # Outline matches the W32 stroke pattern used elsewhere in the panel.
        path.setLineWidth_(0.7)
        (HOVER_EDGE() if self._hovered else _T("chip_stroke")).setStroke()
        path.stroke()

        title_col = W94() if self._hovered else W60()
        key_col = ACCENT_MINT() if self._hovered else W94()
        key_font = AppKit.NSFont.monospacedSystemFontOfSize_weight_(
            8.0, AppKit.NSFontWeightSemibold,
        )
        title_font = _round(7.5, AppKit.NSFontWeightMedium)
        para_c = AppKit.NSMutableParagraphStyle.alloc().init()
        para_c.setAlignment_(AppKit.NSTextAlignmentCenter)

        if not self._title:
            # Single-line chip (e.g. onboarding tour).
            col = W94() if self._hovered else W60()
            font = AppKit.NSFont.monospacedSystemFontOfSize_weight_(
                9.0, AppKit.NSFontWeightMedium,
            )
            attrs = {
                AppKit.NSFontAttributeName: font,
                AppKit.NSForegroundColorAttributeName: col,
                AppKit.NSParagraphStyleAttributeName: para_c,
            }
            s = AppKit.NSAttributedString.alloc().initWithString_attributes_(
                self._glyph, attrs,
            )
            size = s.size()
            s.drawAtPoint_(AppKit.NSMakePoint(
                (bounds.size.width - size.width) / 2.0,
                (bounds.size.height - size.height) / 2.0 + lift - 0.5,
            ))
            return

        # Two-line: title (what it does) above, keys below.
        title_attrs = {
            AppKit.NSFontAttributeName: title_font,
            AppKit.NSForegroundColorAttributeName: title_col,
            AppKit.NSParagraphStyleAttributeName: para_c,
        }
        key_attrs = {
            AppKit.NSFontAttributeName: key_font,
            AppKit.NSForegroundColorAttributeName: key_col,
            AppKit.NSParagraphStyleAttributeName: para_c,
        }
        ts = AppKit.NSAttributedString.alloc().initWithString_attributes_(
            self._title, title_attrs,
        )
        ks = AppKit.NSAttributedString.alloc().initWithString_attributes_(
            self._glyph, key_attrs,
        )
        tw, th = ts.size().width, ts.size().height
        kw, kh = ks.size().width, ks.size().height
        gap = 0.0
        block_h = th + gap + kh
        y_base = (bounds.size.height - block_h) / 2.0 + lift
        ts.drawAtPoint_(AppKit.NSMakePoint((bounds.size.width - tw) / 2.0, y_base + kh + gap))
        ks.drawAtPoint_(AppKit.NSMakePoint((bounds.size.width - kw) / 2.0, y_base))


def _measure_chip_width(title: str, glyph: str) -> float:
    """Minimum width for a two-line chip from string lengths."""
    title = (title or "").strip()
    if not title:
        per_char = 6.0
        return max(30.0, len(glyph) * per_char + 10.0)
    # Title often wider than keys; approximate both in their fonts.
    t_len = max(len(title), len(glyph) * 0.85)
    return min(
        168.0,
        max(68.0, 7.2 * (t_len ** 0.95) + 12.0),
    )


def _make_chip(
    title: str,
    glyph: str,
    hint: str,
    callback=None,
    width: float | None = None,
) -> _ShortcutChip:
    """Build a shortcut chip. Pass ``title=""`` for a compact single-line chip."""
    title = (title or "").strip()
    w = float(width) if width else _measure_chip_width(title, glyph)
    h = 25.0 if title else 17.0
    return _ShortcutChip.alloc().initWithFrame_title_glyph_hint_callback_(
        AppKit.NSMakeRect(0, 0, w, h), title, glyph, hint, callback,
    )


# ── Onboarding overlay ───────────────────────────────────────────────────────


class _OnboardingCard(AppKit.NSView):
    """First-launch shortcut tour. Three pages of keyboard education.

    Painted as a frosted dimming layer (full-panel) with a centered
    rounded-rect card on top. The card has a header line, a short body,
    a chip strip for the page's shortcut(s), and Prev / Next controls.
    Fades in/out via the parent's NSAnimationContext."""

    _dismissing = objc.ivar()

    def initWithFrame_overlay_(self, frame, overlay):
        self = objc.super(_OnboardingCard, self).initWithFrame_(frame)
        if self is None:
            return None
        self._overlay = overlay
        self._page = 0
        self._cb_done = None
        self._chips_in_card: list[_ShortcutChip] = []
        self._dynamic_views: list[AppKit.NSView] = []
        self._dismissing = False
        self.setWantsLayer_(True)
        try:
            self.layer().setOpacity_(0.0)
        except Exception:
            pass
        return self

    @objc.python_method
    def set_completion_(self, cb):
        self._cb_done = cb

    @objc.python_method
    def show(self):
        # Fade the whole overlay in.
        AppKit.NSAnimationContext.beginGrouping()
        AppKit.NSAnimationContext.currentContext().setDuration_(0.32)
        self.layer().setOpacity_(1.0)
        AppKit.NSAnimationContext.endGrouping()
        self._render_page()

    @objc.python_method
    def dismiss(self):
        # While fading out the view still has a superview; SearchOverlay must
        # not keep the real footer suppressed or the bottom strip looks missing.
        if self._dismissing:
            return
        self._dismissing = True
        AppKit.NSAnimationContext.beginGrouping()
        AppKit.NSAnimationContext.currentContext().setDuration_(0.24)
        self.layer().setOpacity_(0.0)
        AppKit.NSAnimationContext.endGrouping()
        ov = getattr(self, "_overlay", None)
        if ov is not None:
            try:
                ov._sync_footer_visibility_with_tour()
            except Exception:
                pass
        AppKit.NSObject.performSelector_withObject_afterDelay_(
            self, b"_onboardingDrop:", None, 0.28,
        )

    @objc.typedSelector(b"v@:@")
    def _onboardingDrop_(self, _):
        self._dismissing = False
        try:
            self.removeFromSuperview()
        except Exception:
            pass
        if self._cb_done is not None:
            try:
                self._cb_done()
            except Exception:
                pass

    # Mouse: we do not implement mouseDown_ — dim clicks pass through (see
    # hitTest_) so tabs and search stay usable. Dismiss via card buttons,
    # Esc (closes overlay → tear-down), or clicking through then using UI.

    def hitTest_(self, point):
        # During fade-out the layer opacity hits ~0 while the view can still
        # receive hits — let clicks reach views underneath.
        try:
            ly = self.layer()
            if ly is not None and float(ly.opacity()) < 0.04:
                return None
        except Exception:
            pass
        cr = self._card_rect()
        # Only the tour card (plus a small slop for edge controls) may claim
        # mouse hits. The tour view is full-panel sized above the real UI;
        # without this, the dimmed region steals every click from tabs/search.
        pad = 14.0
        expanded = NSInsetRect(cr, -pad, -pad)
        if not AppKit.NSPointInRect(point, expanded):
            return None
        return objc.super(_OnboardingCard, self).hitTest_(point)

    def drawRect_(self, _rect):
        bounds = self.bounds()
        # Dim wash over the whole panel (slightly stronger in light mode so
        # underlying footer copy does not compete with the tour card).
        wash = _c(0, 0, 0, 0.42 if _is_dark() else 0.28)
        wash.setFill()
        AppKit.NSBezierPath.fillRect_(bounds)
        # Card backdrop.
        card = self._card_rect()
        radius = 20.0
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            card, radius, radius,
        )
        _T("card_bg").setFill()
        path.fill()
        # Subtle accent rim.
        ACCENT_MINT_DIM().setStroke()
        path.setLineWidth_(0.9)
        path.stroke()

    @objc.python_method
    def _card_rect(self):
        b = self.bounds()
        w = 480.0
        h = 308.0
        return AppKit.NSMakeRect(
            (b.size.width - w) / 2.0,
            (b.size.height - h) / 2.0 + 28,
            w, h,
        )

    @objc.python_method
    def _render_page(self):
        # Tear down previous page subviews.
        for v in self._dynamic_views:
            try:
                v.removeFromSuperview()
            except Exception:
                pass
        self._dynamic_views = []
        self._chips_in_card = []
        card = self._card_rect()

        pages = self._pages()
        page = pages[self._page]

        # Step indicator (small caps, top of card).
        step_lbl = _lbl(
            f"STEP {self._page + 1} OF {len(pages)}",
            _round(9, AppKit.NSFontWeightSemibold),
            ACCENT_MINT(),
            AppKit.NSTextAlignmentCenter,
        )
        step_lbl.setFrame_(AppKit.NSMakeRect(
            card.origin.x, card.origin.y + card.size.height - 36, card.size.width, 16,
        ))
        self.addSubview_(step_lbl)
        self._dynamic_views.append(step_lbl)

        # Heading.
        head = _lbl(
            page["title"],
            _round(19, AppKit.NSFontWeightSemibold),
            W94(),
            AppKit.NSTextAlignmentCenter,
        )
        head.setFrame_(AppKit.NSMakeRect(
            card.origin.x + 22, card.origin.y + card.size.height - 72,
            card.size.width - 44, 26,
        ))
        self.addSubview_(head); self._dynamic_views.append(head)

        # Body copy — word wrap, no tail truncation; tall enough for full copy.
        body = _lbl(
            page["body"],
            _round(13),
            W60(),
            AppKit.NSTextAlignmentCenter,
            lines=6,
            wrap=True,
        )
        body.setFrame_(AppKit.NSMakeRect(
            card.origin.x + 28, card.origin.y + card.size.height - 178,
            card.size.width - 56, 100,
        ))
        try:
            body.setPreferredMaxLayoutWidth_(card.size.width - 56)
        except Exception:
            pass
        self.addSubview_(body); self._dynamic_views.append(body)

        # Chip cluster centered.
        chips_total_w = 0.0
        chip_objs = []
        for glyph, hint in page["shortcuts"]:
            chip = _make_chip("", glyph, hint)
            chip.setFrame_(AppKit.NSMakeRect(
                0, 0, max(chip.frame().size.width, 48), 22,
            ))
            chip_objs.append(chip)
            chips_total_w += chip.frame().size.width
        chips_total_w += max(0, len(chip_objs) - 1) * 10
        cx = card.origin.x + (card.size.width - chips_total_w) / 2.0
        cy = card.origin.y + 100
        for chip in chip_objs:
            cw = chip.frame().size.width
            chip.setFrame_(AppKit.NSMakeRect(cx, cy, cw, 22))
            cx += cw + 10
            self.addSubview_(chip)
            self._dynamic_views.append(chip)
            self._chips_in_card.append(chip)

        # Footer controls.
        btn_y = card.origin.y + 22
        if self._page > 0:
            prev = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
                "Back",
                AppKit.NSMakeRect(card.origin.x + 22, btn_y, 90, 30),
                W60(), False, lambda: self._goto_page(self._page - 1),
            )
            self.addSubview_(prev); self._dynamic_views.append(prev)

        next_label = "Get Started" if self._page == len(pages) - 1 else "Next"
        nxt = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            next_label,
            AppKit.NSMakeRect(
                card.origin.x + card.size.width - 22 - 110, btn_y, 110, 30,
            ),
            ACCENT_MINT(), False, lambda: self._advance(),
        )
        self.addSubview_(nxt); self._dynamic_views.append(nxt)

        # Pagination dots.
        dot_y = card.origin.y + 36
        dot_w = 8 * len(pages) + 6 * (len(pages) - 1)
        dot_x = card.origin.x + (card.size.width - dot_w) / 2.0
        for i in range(len(pages)):
            dot = AppKit.NSView.alloc().initWithFrame_(
                AppKit.NSMakeRect(dot_x, dot_y, 8, 8),
            )
            dot.setWantsLayer_(True)
            dot.layer().setCornerRadius_(4)
            if i == self._page:
                dot.layer().setBackgroundColor_(ACCENT_MINT().CGColor())
            else:
                dot.layer().setBackgroundColor_(W14().CGColor())
            self.addSubview_(dot); self._dynamic_views.append(dot)
            dot_x += 14

        self.setNeedsDisplay_(True)

    @objc.python_method
    def _goto_page(self, idx: int):
        pages = self._pages()
        if 0 <= idx < len(pages):
            self._page = idx
            self._render_page()

    @objc.python_method
    def _advance(self):
        pages = self._pages()
        if self._page < len(pages) - 1:
            self._goto_page(self._page + 1)
        else:
            self.dismiss()

    @objc.python_method
    def _pages(self):
        return onboarding_pages()

def _make_row(result, width, detail_fn=None, delete_fn=None, flash_fn=None, star_fn=None,
              exclude_fn=None,
              minimal: bool = False, height: float | None = None,
              rich: bool = False) -> _Row:
    h = float(height) if height is not None else ROW_H
    r = _Row.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, width, h))
    r._minimal = bool(minimal)
    r._acc       = _src_col(result.source)
    r._text      = result.text_snippet
    r._full_text = getattr(result, "full_text", "") or result.text_snippet
    r._mid       = result.memory_id
    r._starred   = getattr(result, "is_starred", False)
    r._detail_fn = detail_fn
    r._delete_fn = delete_fn
    r._flash_fn  = flash_fn
    r._star_fn   = star_fn
    r._app_name  = getattr(result, "app_name", "") or ""
    r._exclude_fn = exclude_fn
    r._rich = bool(rich)

    full        = r._full_text or result.text_snippet or ""
    activity_r  = getattr(result, "activity",     "") or ""
    window_r    = getattr(result, "window_title", "") or ""
    heading_r   = (getattr(result, "heading", "") or "").strip()
    summary_r   = (getattr(result, "summary", "") or "").strip()
    app_low     = (getattr(result, "app_name", "") or "").lower()
    is_browser  = any(name in app_low for name in (
        "chrome", "safari", "firefox", "brave", "arc", "edge", "microsoft edge",
    ))

    title = ""
    subject = ""

    # When the model stored both fields, show them as two lines: action headline + topic gist.
    if heading_r and summary_r and heading_r.lower() != summary_r.lower():
        title = heading_r
        subject = summary_r
    else:
        # ── Title: action / combined line (heuristic or single field) ─────────
        action = heading_r
        if not action or action.lower().startswith((
            "copied in ", "worked in ", "viewed in ", "captured in ",
        )):
            action = memory_title(result.source, result.app_name, activity_r, window_r, full)

        topic = summary_r
        if is_browser and topic and not title:
            title = action or "Browser Activity"
        elif topic and action and topic.lower() not in action.lower():
            title = f"{action}  ·  {topic}"
        else:
            title = action or summary_r or "Captured Memory"

        # ── Subject: second line — context not already in the title ─────────
        if is_browser and summary_r and not subject:
            subject = summary_r

    if not subject and window_r and len(window_r) > 10:
        wt = window_r
        # Strip trailing "- AppName" or "| AppName" suffix
        for sep in (" - ", " — ", " | ", " · "):
            if result.app_name and wt.lower().endswith((sep + result.app_name).lower()):
                wt = wt[:-(len(sep) + len(result.app_name))].strip()
                break
        if len(wt) > 6 and wt.lower() not in title.lower():
            subject = wt

    if not subject and full and len(full) > 40:
        first = _subject(full)
        ctx   = _context_line(full, first)
        candidate = ctx or first
        if candidate.lower() not in title.lower() and len(candidate) > 10:
            subject = candidate

    if not subject:
        subject = activity_r or summary_r

    title = clean_text(title)
    subject = clean_text(_clean_subject_display(subject, result.source))
    subject = _trim_redundant_subject(title, subject)
    if not subject:
        alt = _context_line(full, _subject(full))
        alt = clean_text(_clean_subject_display(alt, result.source))
        subject = _trim_redundant_subject(title, alt)
    if not subject:
        loose = clean_text(_clean_subject_display(activity_r or summary_r, result.source))
        subject = _trim_redundant_subject(title, loose)

    # ── Right meta — single quiet date+time line ──────────────────────────
    star_w   = 30
    right_pad = 14
    stamp_str = _stamp(result.created_at)
    stamp_w   = max(110.0,
                    _text_width(stamp_str, _round(11)) + 8.0)
    star_x    = width - star_w - right_pad
    r._meta   = ""                  # no longer rendered
    r._stamp  = stamp_str
    r._star_x = star_x
    r._star_w = star_w

    subj_w = star_x - stamp_w - 44
    if minimal:
        # Minimal row: only the catchy title + relative time on the right.
        catchy = _catchy_title(title, subject, result.app_name, full)
        r._title = _fit_plain_text(_clip_timeline_words(catchy, 12),
                                   _round(14, AppKit.NSFontWeightSemibold),
                                   width - 140)
        r._subject = ""
        r._meta = ""
        r._stamp = _rel(result.created_at)
        r._tag = ""
        r._activity = ""
        return r

    # Rich timeline rows keep the model/heuristic title closer to source
    # content so consecutive entries are more unique and less templated.
    catchy = title if rich else _catchy_title(title, subject, result.app_name, full)
    title_words = 18 if rich else 14
    subject_words = 20 if rich else 9
    r._title = _fit_plain_text(_clip_timeline_words(catchy, title_words),
                               ROW_TITLE_FONT, subj_w)

    # Drop subjects that just repeat the app name or restate the title.
    s_clean = (subject or "").strip()
    if s_clean and result.app_name and s_clean.lower() == result.app_name.lower():
        s_clean = ""
    if s_clean and s_clean.lower() in catchy.lower():
        s_clean = ""
    r._subject = (_fit_plain_text(_clip_timeline_words(s_clean, subject_words),
                                   ROW_SUBJECT_FONT, subj_w)
                  if s_clean else "")
    r._tag = ""
    r._activity = ""
    r._activity_c = _src_col(result.source)
    return r


# ── Main overlay class ────────────────────────────────────────────────────────


class SearchOverlay:
    def __init__(self, search_fn: Callable, store, data_dir=None, cache=None, config_path=None):
        self._fn       = search_fn
        self._store    = store
        self._data_dir = data_dir
        self._config_path = config_path
        # Optional VectorCache; when provided we evict deleted memories from the
        # in-memory cache immediately so they don't ghost into search results.
        self._cache    = cache
        self._panel    = None
        self._count_timer = None
        self._tint  = None
        self._main  = None
        self._ob    = None
        # Main view sub-refs
        self._sf_field = None
        self._nf       = None
        self._doc      = None
        self._scroll   = None
        self._g_lbl    = None
        self._st_lbl   = None
        self._tabs: list[_TabBtn] = []
        self._tab_mode = "search"   # "search" | "timeline" | "starred" | "brain" | "settings"
        # Detail view
        self._detail_view = None
        self._detail_tv   = None   # NSTextView for full text
        self._detail_star_btn = None
        self._detail_summarize_btn = None
        self._current_detail_result = None
        self._is_editing  = False
        self._detail_showing_summary = False
        self._detail_summary_loading = False
        # ObjC retained
        self._fd = None
        self._wd = None
        self._btns: list = []
        self._perm_labels: dict[str, AppKit.NSTextField] = {}
        self._perm_btns: dict[str, _ActionBtn] = {}
        self._perm_msg = None
        # (Chat tab removed — Timeline now owns the conversational
        # narrative via cached AI day-briefs.)
        # Search state
        self._pending = ""
        self._timer   = None
        self._theme_toggle = None
        # Empty-state progressive disclosure
        self._empty_revealed = False
        # Keyboard navigation through rows
        self._visible_rows: list = []
        self._focus_idx: int = -1
        # Daily digest state
        self._digest_in_flight = False
        # Stealth mode — hides the panel from screen capture / screen
        # sharing / recordings. Default ON; persists across launches.
        self._stealth_on = True
        try:
            if store is not None:
                raw = (store.get_config("stealth_capture", "") or "").strip().lower()
                if raw in ("0", "off", "false", "no"):
                    self._stealth_on = False
        except Exception:
            pass
        # Restore theme preference from store (default light).
        try:
            saved = (store.get_config("theme_pref", "") or "").strip().lower() if store else ""
            if saved in ("light", "dark", "auto"):
                _set_theme(saved)
        except Exception:
            pass

    # ── Public ────────────────────────────────────────────────────────────────

    def toggle(self):
        if self._panel is None: self._build()
        if self._panel.isVisible(): self.hide()
        else: self.show()

    def show(self):
        if self._panel is None: self._build()
        # Recover footer if a prior tour left a dangling ref or the view tree
        # changed without clearing _onboard_card (otherwise chips stay hidden).
        self._sync_footer_visibility_with_tour()
        self._panel.center()
        try:
            scr = self._panel.screen() or AppKit.NSScreen.mainScreen()
            if scr is not None:
                fr = self._panel.frame()
                adj = self._panel.constrainFrameRect_toScreen_(fr, scr)
                if abs(adj.origin.x - fr.origin.x) > 0.5 or abs(adj.origin.y - fr.origin.y) > 0.5:
                    self._panel.setFrame_display_(adj, True)
        except Exception:
            pass
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        self._panel.makeKeyAndOrderFront_(None)
        if _prefers_reduced_motion():
            self._panel.setAlphaValue_(1.0)
        else:
            self._panel.setAlphaValue_(0.0)
            def _fade_panel_in(ctx):
                ctx.setDuration_(0.16)
                ctx.setTimingFunction_(
                    AppKit.CAMediaTimingFunction.functionWithName_("easeOut")
                )
                self._panel.animator().setAlphaValue_(1.0)

            AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
                _fade_panel_in, None)
        if self._ob and self._nf:
            self._panel.makeFirstResponder_(self._nf)
        elif self._main and not self._ob:
            self._refresh_greeting()
            self._sf_field.setStringValue_("")
            self._sf_field.selectText_(None)
            self._sync_list_with_store()
        # First-launch onboarding tour. Only fires once until the user
        # explicitly replays it via the menu bar context menu.
        self._maybe_show_onboarding()
        self._sync_footer_visibility_with_tour()

    @objc.python_method
    def _sync_footer_visibility_with_tour(self) -> None:
        """Keep footer chips/status in sync with whether a live tour is mounted.

        If ``_onboard_card`` points at a view that is no longer in the hierarchy,
        clear the reference and un-hide the footer — otherwise the user sees a
        blank bottom strip forever."""
        oc = getattr(self, "_onboard_card", None)
        if oc is None:
            self._set_onboarding_footer_suppressed(False)
            return
        try:
            alive = oc.superview() is not None
        except Exception:
            alive = False
        if not alive:
            self._onboard_card = None
            self._set_onboarding_footer_suppressed(False)
            return
        # Fade-out: the card is still in the hierarchy while the layer fades;
        # do not keep the real footer hidden or the bottom looks blank for the
        # whole fade (and indefinitely if _onboardingDrop_ never runs).
        try:
            if bool(getattr(oc, "_dismissing", False)):
                self._set_onboarding_footer_suppressed(False)
                return
        except Exception:
            pass
        self._set_onboarding_footer_suppressed(True)

    def hide(self):
        if self._panel and self._panel.isVisible():
            self._tear_down_onboarding_tour_presentation()
            if _prefers_reduced_motion():
                self._panel.orderOut_(None)
            else:
                def _fade_panel_out(ctx):
                    ctx.setDuration_(0.11)
                    ctx.setTimingFunction_(
                        AppKit.CAMediaTimingFunction.functionWithName_("easeIn")
                    )
                    self._panel.animator().setAlphaValue_(0.0)

                def _order_out():
                    self._panel.orderOut_(None)

                AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
                    _fade_panel_out, _order_out)

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self):
        # Borderless — eliminates the titled-window vibrancy glow (the sphere)
        panel = _OverlayPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, PANEL_W, PANEL_H),
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered, False)
        panel.setLevel_(AppKit.NSFloatingWindowLevel)
        panel.setMovableByWindowBackground_(True)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHidesOnDeactivate_(False)
        # Stealth mode — exclude the panel from screen capture, screen
        # sharing, AirPlay mirroring, and recordings (Zoom/Chrome/Teams/
        # QuickTime/macOS native sharing all honor NSWindowSharingNone via
        # ScreenCaptureKit). Persistent toggle stored in config so the user
        # can flip it via ⌘⇧I.
        self._apply_stealth_to_panel(panel)
        # Default Mac-native appearance (Aqua = light); follows _THEME_PREF.
        try:
            ap_name = (AppKit.NSAppearanceNameDarkAqua
                       if _is_dark()
                       else AppKit.NSAppearanceNameAqua)
            panel.setAppearance_(AppKit.NSAppearance.appearanceNamed_(ap_name))
        except Exception:
            pass

        wd = _WinDelegate.alloc().initWithFn_(self.hide)
        panel.setDelegate_(wd); self._wd = wd
        panel.setShortcutHandler_(self._dispatch_shortcut)

        tint = _PanelBg.alloc().initWithFrame_detail_(
            AppKit.NSMakeRect(0, 0, PANEL_W, PANEL_H), False)
        # Layer needed only for clipping subviews — NO setBackgroundColor_ on layer
        tint.setWantsLayer_(True)
        tint.layer().setCornerRadius_(CORNER)
        tint.layer().setMasksToBounds_(True)
        tint.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        panel.contentView().addSubview_(tint)

        self._tint = tint; self._panel = panel

        name = self._store.get_config("user_name", "")
        if not name.strip() or not self._permissions_ok(prompt=False):
            self._build_onboarding(tint)
        else:
            self._build_main(tint, name)
            self._build_detail(tint)

        # Live count updater — fires every 25s on main thread
        self._count_timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            25.0, self, b"_refreshCount:", None, True
        )

    def _permissions_ok(self, prompt: bool = False) -> bool:
        if self._store and self._store.get_config("permissions_confirmed", "") == "1":
            return True
        ok = all_required_permissions(prompt=prompt)
        if ok and self._store:
            self._store.set_config("permissions_confirmed", "1")
        return ok

    @objc.python_method
    def _footer_line(self, mode: str = "search") -> str:
        """Single unified footer line across every tab.

        The text is intentionally identical everywhere — no daemon prompts,
        no "no memories yet" copy — so the bottom strip reads as one calm
        line. Key chips on the right carry the shortcut info."""
        if not self._store:
            return "Corenous AI"

        n = self._store.get_memory_count()

        extras: list[str] = []
        if self._is_capture_paused():
            extras.append("capture paused")
        if self._is_lite_mode():
            extras.append("lite mode")
        try:
            vault_n = len(self._store.get_vault_entries())
        except Exception:
            vault_n = 0
        if vault_n > 0:
            extras.append(f"{vault_n} in vault")

        suffix = "  ·  " + "  ·  ".join(extras) if extras else ""
        return f"{n:,} memories{suffix}"

    @objc.python_method
    def _refresh_count_label(self):
        if not self._store or not self._st_lbl:
            return
        mode = getattr(self, "_tab_mode", "search")
        self._st_lbl.setStringValue_(self._footer_line(mode))

    def _refreshCount_(self, timer):
        self._refresh_count_label()
        # If the user is actively looking at the timeline, pull fresh titles —
        # the daemon's AI refinement may have rewritten headings since opening.
        try:
            if (self._panel and self._panel.isVisible()
                    and getattr(self, "_tab_mode", "search") == "timeline"
                    and self._store):
                self._load_timeline()
        except Exception:
            pass
        # Refresh Brain tab live summary every 25s so it stays current
        try:
            if (self._panel and self._panel.isVisible()
                    and getattr(self, "_tab_mode", "search") == "brain"
                    and self._store
                    and not getattr(self, "_brain_generating", False)):
                self._refresh_brain_summary_label()
        except Exception:
            pass

    @objc.python_method
    def _toggle_theme(self):
        """Flip light <-> dark, persist preference, rebuild the panel chrome."""
        new_pref = "dark" if not _is_dark() else "light"
        _set_theme(new_pref)
        if self._store:
            try:
                self._store.set_config("theme_pref", new_pref)
            except Exception:
                pass
        # Re-apply to live window appearance for native scrollers/text caret.
        try:
            ap_name = (AppKit.NSAppearanceNameDarkAqua
                       if _is_dark()
                       else AppKit.NSAppearanceNameAqua)
            if self._panel:
                self._panel.setAppearance_(
                    AppKit.NSAppearance.appearanceNamed_(ap_name))
        except Exception:
            pass
        # Rebuild from scratch so cached colors (text fields, labels) refresh.
        if self._panel:
            visible = self._panel.isVisible()
            self._panel.orderOut_(None)
            for sv in list(self._panel.contentView().subviews()):
                sv.removeFromSuperview()
            self._tabs = []
            self._btns = []
            self._build()
            if visible:
                self.show()

    # ── Onboarding ────────────────────────────────────────────────────────────

    def _build_onboarding(self, parent):
        ob = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, PANEL_W, PANEL_H))
        cx = PANEL_W / 2
        self._perm_labels = {}
        self._perm_btns = {}

        card = _SignupHeroCard.alloc().initWithFrame_(
            AppKit.NSMakeRect(28, 40, PANEL_W - 56, PANEL_H - 80))
        ob.addSubview_(card)

        wm = _lbl("Corenous", _didot(26), GOLD(), AppKit.NSTextAlignmentCenter)
        wm.setFrame_(AppKit.NSMakeRect(60, 414, PANEL_W-120, 34))
        ob.addSubview_(wm)

        tl = _lbl("Private memory. Always with you.",
                  _sf(13, AppKit.NSFontWeightLight), W32(), AppKit.NSTextAlignmentCenter)
        tl.setFrame_(AppKit.NSMakeRect(80, 380, PANEL_W-160, 20))
        ob.addSubview_(tl)

        hair = _MintHairline.alloc().initWithFrame_(AppKit.NSMakeRect(cx - 40, 358, 80, 2))
        ob.addSubview_(hair)

        lbl = _lbl("Name", _sf(11, AppKit.NSFontWeightSemibold), _T("section_lbl"))
        lbl.setFrame_(AppKit.NSMakeRect(cx-145, 332, 290, 15))
        ob.addSubview_(lbl)

        fw = 320
        con, nf = _input((cx-fw/2, 284, fw, 44), "", centered=True)
        saved_name = self._store.get_config("user_name", "") if self._store else ""
        if saved_name:
            nf.setStringValue_(saved_name)
        ob.addSubview_(con); self._nf = nf

        fd = _FieldDelegate.alloc().initWith_escape_return_(
            lambda _: None, self.hide, self._finish_ob)
        nf.setDelegate_(fd); self._fd = fd

        pl = _lbl("Permissions", _sf(11, AppKit.NSFontWeightSemibold), _T("section_lbl"))
        pl.setFrame_(AppKit.NSMakeRect(cx-210, 246, 420, 15))
        ob.addSubview_(pl)

        self._add_permission_row(
            ob, 202, "Accessibility", "accessibility", self._request_accessibility)
        self._add_permission_row(
            ob, 160, "Screen Recording", "screen_recording", self._request_screen_recording)

        msg = _lbl("", _sf(10), DANGER(), AppKit.NSTextAlignmentCenter)
        msg.setFrame_(AppKit.NSMakeRect(70, 124, PANEL_W-140, 16))
        ob.addSubview_(msg); self._perm_msg = msg

        bw, bh = 228, 44
        btn = _GoldBtn.alloc().initWithTitle_frame_cb_(
            "Begin", AppKit.NSMakeRect(cx-bw/2, 74, bw, bh), self._finish_ob)
        ob.addSubview_(btn); self._btns.append(btn)

        foot = _lbl("100% local  ·  AES-256 encrypted  ·  open source",
                    _sf(10), W14(), AppKit.NSTextAlignmentCenter)
        foot.setFrame_(AppKit.NSMakeRect(60, 36, PANEL_W-120, 15))
        ob.addSubview_(foot)

        parent.addSubview_(ob); self._ob = ob
        self._refresh_permission_rows()

    def _add_permission_row(self, parent, y: float, title: str, key: str, cb):
        x = 130
        w = PANEL_W - x * 2
        row = _InputBg.alloc().initWithFrame_(AppKit.NSMakeRect(x, y, w, 32))
        parent.addSubview_(row)

        title_lbl = _lbl(title, _sf(12, AppKit.NSFontWeightMedium), W94())
        title_lbl.setFrame_(AppKit.NSMakeRect(x + 14, y + 8, 180, 16))
        parent.addSubview_(title_lbl)

        status = _lbl("Checking", _sf(11), W32(), AppKit.NSTextAlignmentRight)
        status.setFrame_(AppKit.NSMakeRect(x + 200, y + 8, 90, 16))
        parent.addSubview_(status)
        self._perm_labels[key] = status

        btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            "Open", AppKit.NSMakeRect(x + w - 78, y + 4, 64, 24),
            ACCENT_MINT(), False, cb)
        parent.addSubview_(btn)
        self._perm_btns[key] = btn

    # ── Main view ─────────────────────────────────────────────────────────────

    def _build_main(self, parent, name: str):
        mv = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, PANEL_W, PANEL_H))
        mv.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)

        # Quote — centered column (readable line length)
        quote_bottom = PANEL_H - MAIN_TOP_PAD - MAIN_QUOTE_H
        gx = MAIN_GUTTER
        g_w = PANEL_W - 2 * MAIN_GUTTER

        # Theme toggle — same baseline as the (single-line) quote.
        toggle_w = 26.0; toggle_h = MAIN_QUOTE_H
        toggle = _ThemeToggle.alloc().initWithFrame_cb_(
            AppKit.NSMakeRect(gx, quote_bottom, toggle_w, toggle_h),
            self._toggle_theme,
        )
        mv.addSubview_(toggle); self._theme_toggle = toggle

        # Quote sits inline with the toggle: indent by toggle width so the
        # centered text doesn't visually overlap the icon on narrow facts.
        q_x = gx + toggle_w + 6
        q_w = g_w - (toggle_w + 6) * 2  # symmetric: also reserve right gutter
        g_lbl = _lbl(_psychology_fact(), _didot(15), W94(), AppKit.NSTextAlignmentCenter)
        g_lbl.setFrame_(AppKit.NSMakeRect(q_x, quote_bottom, q_w, MAIN_QUOTE_H))
        try:
            g_lbl.setMaximumNumberOfLines_(1)
            g_lbl.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        except Exception:
            pass
        mv.addSubview_(g_lbl); self._g_lbl = g_lbl

        y_rule_quote = quote_bottom - MAIN_GAP_QUOTE_RULE
        # No painted rule above the floating search — let the shadow do the work.

        sy = y_rule_quote - MAIN_GAP_RULE_SEARCH - SEARCH_H
        search_x = gx
        search_w = g_w
        sc, sf = _input(
            (search_x, sy, search_w, SEARCH_H),
            "Search memories, apps, sites…",
            size=15,
            lpad=44,
            focus_cb=self._activate_search_input,
        )
        mag = _sym("magnifyingglass", 15)
        if mag:
            miv = AppKit.NSImageView.alloc().initWithFrame_(
                AppKit.NSMakeRect(15, (SEARCH_H - 16) / 2, 16, 16))
            miv.setImage_(mag); miv.setContentTintColor_(ACCENT_MINT_DIM())
            sc.addSubview_(miv)
        mv.addSubview_(sc); self._sf_field = sf

        fd = _FieldDelegate.alloc().initWith_escape_return_(
            self._do_search, self.hide, self._activate_focused_row)
        fd.setNavCallbacks_(self._nav_focus_prev, self._nav_focus_next)
        sf.setDelegate_(fd); self._fd = fd

        # Tab pills — directly under search (wireframe strip)
        tab_btn_h = MAIN_TAB_BTN_H
        tab_btn_y = sy - MAIN_GAP_SEARCH_TABS - tab_btn_h
        tab_line_y = tab_btn_y - MAIN_GAP_TABS_BODY
        tab_names = [
            ("Search", "search"),
            ("Timeline", "timeline"),
            ("Starred", "starred"),
            ("Agent", "brain"),
            ("Settings", "settings"),
        ]
        n_tabs = len(tab_names)
        tab_gap = 8.0
        inner_tabs = PANEL_W - 2 * MAIN_GUTTER - (n_tabs - 1) * tab_gap
        tab_w = inner_tabs / float(n_tabs)
        tx = MAIN_GUTTER
        for label, mode in tab_names:
            tb = _TabBtn.alloc().initWithTitle_frame_active_cb_(
                label,
                AppKit.NSMakeRect(tx, tab_btn_y, tab_w, tab_btn_h),
                mode == "search",
                lambda m=mode: self._switch_tab(m))
            mv.addSubview_(tb); self._tabs.append(tb)
            tx += tab_w + tab_gap

        # Tabs and content blend; thin separator only above the footer.

        # Results scroll
        rh = tab_line_y - MAIN_FOOTER_H
        scroll = _ResultsScrollView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, MAIN_FOOTER_H, PANEL_W, rh))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(AppKit.NSNoBorder)
        scroll.setDrawsBackground_(False)
        scroll.contentView().setDrawsBackground_(False)
        # Height must not autoresize with the panel or the clip view can grow
        # downward over the footer strip (chips + memory count).
        scroll.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        scroll.verticalScroller().setControlSize_(AppKit.NSControlSizeSmall)

        doc = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, PANEL_W, rh))
        doc.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        scroll.setDocumentView_(doc)
        mv.addSubview_(scroll)
        self._doc = doc; self._scroll = scroll

        # ── Footer strip ──────────────────────────────────────────────────────
        # Left: memory count (single line). Right: compact key-only shortcut chips.
        # Hover on a chip shows its description in the left label.
        st = AppKit.NSTextField.labelWithString_("")
        st.setFont_(_round(11))
        st.setTextColor_(W60())
        st.setAlignment_(AppKit.NSTextAlignmentLeft)
        st.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        st.setMaximumNumberOfLines_(1)
        st.setSelectable_(False)
        # Chips are now compact key-only (≈200pt total); left label gets the rest.
        _footer_text_w = PANEL_W - gx * 2.0 - 210.0
        st.setFrame_(AppKit.NSMakeRect(gx, (MAIN_FOOTER_H - 16.0) / 2.0,
                                        _footer_text_w, 16))
        mv.addSubview_(st); self._st_lbl = st

        self._build_footer_chips(mv)
        # Keep footer chrome above the scroll view in z-order (some AppKit
        # layout passes retile NSScrollView aggressively when it is fully
        # sizable in both dimensions).
        try:
            mv.addSubview_positioned_relativeTo_(
                st, AppKit.NSWindowAbove, scroll,
            )
            for ch in getattr(self, "_footer_chips", None) or ():
                mv.addSubview_positioned_relativeTo_(
                    ch, AppKit.NSWindowAbove, scroll,
                )
        except Exception:
            pass

        parent.addSubview_(mv); self._main = mv
        self._render_search_empty()

    # ── Detail view ───────────────────────────────────────────────────────────

    def _build_detail(self, parent):
        """Build the detail panel (initially off-screen to the right)."""
        dv = _PanelBg.alloc().initWithFrame_detail_(
            AppKit.NSMakeRect(PANEL_W, 0, PANEL_W, PANEL_H), True)
        dv.setWantsLayer_(True)
        dv.layer().setCornerRadius_(CORNER)
        dv.layer().setMasksToBounds_(True)

        # ── Header row ────────────────────────────────────────────────────────
        back_btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            "Back", AppKit.NSMakeRect(12, PANEL_H - 48, 78, 36),
            W60(), False, self._hide_detail)
        dv.addSubview_(back_btn)

        title_lbl = _lbl("", _round(14, AppKit.NSFontWeightSemibold), W94(),
                          AppKit.NSTextAlignmentCenter)
        title_lbl.setFrame_(AppKit.NSMakeRect(96, PANEL_H - 47, PANEL_W - 192, 28))
        dv.addSubview_(title_lbl)
        self._detail_title_lbl = title_lbl

        # Star button (top-right)
        star_btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            "Star", AppKit.NSMakeRect(PANEL_W - 98, PANEL_H - 48, 86, 36),
            STAR_COL(), False, self._toggle_star)
        dv.addSubview_(star_btn)
        self._detail_star_btn = star_btn

        # Detail header rests directly on the content; no rule.

        # ── Full text scroll ──────────────────────────────────────────────────
        tv_h = PANEL_H - 52 - 110  # header + meta + action bar
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 102, PANEL_W, tv_h))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(AppKit.NSNoBorder)
        scroll.setDrawsBackground_(False)
        scroll.contentView().setDrawsBackground_(False)

        tv = AppKit.NSTextView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, PANEL_W, tv_h))
        tv.setTextContainerInset_(AppKit.NSMakeSize(20, 16))
        tv.setFont_(_round(14))
        tv.setTextColor_(W94())
        tv.setBackgroundColor_(AppKit.NSColor.clearColor())
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setRichText_(False)
        scroll.setDocumentView_(tv)
        dv.addSubview_(scroll)
        self._detail_tv    = tv
        self._detail_scroll = scroll

        # Action bar rests on the content; no rule.

        # ── Meta row ──────────────────────────────────────────────────────────
        meta_lbl = _lbl("", _round(11), W60(), AppKit.NSTextAlignmentCenter)
        meta_lbl.setFrame_(AppKit.NSMakeRect(12, 70, PANEL_W - 24, 22))
        dv.addSubview_(meta_lbl)
        self._detail_meta_lbl = meta_lbl

        # ── Action buttons (min ~32pt height for interaction comfort) ────────────
        # Bullets are now auto-generated when the detail opens, so the legacy
        # "Summarize" button is gone. Edit and Save share the same slot.
        btn_y = 30; bw = 90; bh = 32; gap = 10
        total_w = 4 * bw + 3 * gap
        bx = (PANEL_W - total_w) / 2

        copy_b = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            "Copy", AppKit.NSMakeRect(bx, btn_y, bw, bh),
            W60(), False, self._detail_copy)
        dv.addSubview_(copy_b); bx += bw + gap

        self._detail_edit_btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            "Edit", AppKit.NSMakeRect(bx, btn_y, bw, bh),
            W60(), False, self._detail_toggle_edit)
        dv.addSubview_(self._detail_edit_btn)

        self._detail_save_btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            "Save", AppKit.NSMakeRect(bx, btn_y, bw, bh),
            GOLD(), False, self._detail_save)
        self._detail_save_btn.setHidden_(True)
        dv.addSubview_(self._detail_save_btn)
        bx += bw + gap

        regen_b = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            "Regenerate", AppKit.NSMakeRect(bx, btn_y, bw, bh),
            SRC_VIOLET(), False, self._detail_regenerate_bullets)
        dv.addSubview_(regen_b); bx += bw + gap

        del_b = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            "Delete", AppKit.NSMakeRect(bx, btn_y, bw, bh),
            None, True, self._detail_delete)
        dv.addSubview_(del_b)

        # Status bar in detail (no separator).
        st2 = _lbl("", _round(10), W32(), AppKit.NSTextAlignmentCenter)
        st2.setFrame_(AppKit.NSMakeRect(0, 8, PANEL_W, 16))
        dv.addSubview_(st2)
        self._detail_st_lbl = st2

        parent.addSubview_(dv)
        self._detail_view = dv

    # ── Detail show / hide ────────────────────────────────────────────────────

    def _show_detail(self, mid: int):
        if not self._detail_view: return
        row = self._store.get_memory_by_id(mid) if self._store else None
        if not row or int(row.get("is_sensitive") or 0):
            return

        self._current_detail_result = row
        self._is_editing = False
        self._detail_showing_summary = False

        full = row.get("full_text") or row.get("text_snippet", "")
        tags = row.get("tags", "") or ""
        app  = row.get("app_name", "") or row.get("source", "")
        ts   = float(row.get("created_at", 0))
        src  = row.get("source", "")
        starred = bool(row.get("is_starred", 0))
        heading = row.get("heading") or memory_title(
            src, app, row.get("activity", ""), row.get("window_title", ""), full,
        )
        if heading.lower().startswith(("copied in ", "worked in ", "viewed ", "captured in ")):
            heading = memory_title(src, app, row.get("activity", ""), row.get("window_title", ""), full)

        # Populate the detail body. Bullet summary is the primary surface —
        # if we already cached one (stored in narrative), show it. Otherwise
        # show a placeholder + facts and auto-generate bullets in background.
        body = self._compose_detail_body(row, full, heading)
        self._apply_detail_body_text(body, heading=heading)
        self._detail_tv.setEditable_(False)

        cached_narrative = (row.get("narrative") or "").strip()
        needs_bullets = (
            not cached_narrative
            or not cached_narrative.startswith("•")
        )
        if needs_bullets and len(full.strip()) >= 40:
            self._auto_generate_bullets(int(mid), row, full, heading)

        self._detail_title_lbl.setLineBreakMode_(AppKit.NSLineBreakByClipping)
        self._detail_title_lbl.setStringValue_(truncate_text(heading.replace("\n", " "), 48))

        meta_parts = [p for p in [app[:18] if app else None,
                                   row.get("window_title", "")[:28] if row.get("window_title") else None,
                                   row.get("activity", "") if row.get("activity") else None,
                                   tags if tags else None,
                                   src,
                                   _rel(ts)] if p]
        self._detail_meta_lbl.setStringValue_("  ·  ".join(meta_parts))

        star_label = "Starred" if starred else "Star"
        self._detail_star_btn.setTitle_(star_label)

        self._detail_st_lbl.setStringValue_(self._footer_line("detail"))

        self._detail_edit_btn.setHidden_(False)
        self._detail_save_btn.setHidden_(True)

        # Slide in — quick spring-out for a Mac-feel "swipe-from-right".
        if _prefers_reduced_motion():
            self._detail_view.setFrameOrigin_(AppKit.NSMakePoint(0, 0))
            self._main.setFrameOrigin_(AppKit.NSMakePoint(-PANEL_W, 0))
        else:
            spring = AppKit.CAMediaTimingFunction.functionWithControlPoints____(
                0.22, 1.0, 0.36, 1.0)
            def _slide_detail_in(ctx):
                ctx.setDuration_(0.22)
                ctx.setTimingFunction_(spring)
                self._detail_view.animator().setFrameOrigin_(AppKit.NSMakePoint(0, 0))
                self._main.animator().setFrameOrigin_(AppKit.NSMakePoint(-PANEL_W, 0))

            AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
                _slide_detail_in, None)

    def _hide_detail(self):
        if not self._detail_view: return
        if _prefers_reduced_motion():
            self._detail_view.setFrameOrigin_(AppKit.NSMakePoint(PANEL_W, 0))
            self._main.setFrameOrigin_(AppKit.NSMakePoint(0, 0))
        else:
            spring = AppKit.CAMediaTimingFunction.functionWithControlPoints____(
                0.32, 0.0, 0.78, 1.0)
            def _slide_detail_out(ctx):
                ctx.setDuration_(0.18)
                ctx.setTimingFunction_(spring)
                self._detail_view.animator().setFrameOrigin_(
                    AppKit.NSMakePoint(PANEL_W, 0))
                self._main.animator().setFrameOrigin_(AppKit.NSMakePoint(0, 0))

            AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
                _slide_detail_out, None)
        self._current_detail_result = None
        self._is_editing = False
        self._detail_summary_loading = False

    # ── Detail actions ────────────────────────────────────────────────────────

    def _detail_copy(self):
        if not self._current_detail_result: return
        text = (self._current_detail_result.get("full_text")
                or self._current_detail_result.get("text_snippet", ""))
        pb = AppKit.NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, AppKit.NSPasteboardTypeString)
        self._detail_st_lbl.setStringValue_("Copied")

    def _toggle_star(self):
        if not self._current_detail_result or not self._store: return
        mid = self._current_detail_result["id"]
        new_state = self._store.toggle_star(mid)
        self._current_detail_result["is_starred"] = int(new_state)
        self._detail_star_btn.setTitle_("Starred" if new_state else "Star")
        self._detail_st_lbl.setStringValue_("Starred" if new_state else "Unstarred")

    def _toggle_row_star(self, mid: int, star_btn):
        if not self._store: return
        new_state = self._store.toggle_star(mid)
        if star_btn:
            star_btn.setStarred_(new_state)
        if self._tab_mode == "starred" and not new_state:
            self._load_starred()
        elif self._st_lbl:
            self._st_lbl.setStringValue_("Starred" if new_state else "Unstarred")

    def _detail_toggle_edit(self):
        self._is_editing = True
        self._detail_tv.setEditable_(True)
        self._detail_tv.setSelectable_(True)
        self._panel.makeFirstResponder_(self._detail_tv)
        self._detail_edit_btn.setHidden_(True)
        self._detail_save_btn.setHidden_(False)
        self._detail_st_lbl.setStringValue_("Editing. Click Save when done.")

    def _detail_save(self):
        if not self._current_detail_result or not self._store: return
        mid      = self._current_detail_result["id"]
        new_text = str(self._detail_tv.string())
        self._store.update_memory_text(mid, new_text)
        self._detail_tv.setEditable_(False)
        self._is_editing = False
        self._detail_edit_btn.setHidden_(False)
        self._detail_save_btn.setHidden_(True)
        self._detail_st_lbl.setStringValue_("Saved")

    def _detail_summarize(self):
        if not self._current_detail_result:
            return
        full = (self._current_detail_result.get("full_text")
                or self._current_detail_result.get("text_snippet", ""))
        if getattr(self, "_detail_summary_loading", False):
            return
        if self._detail_showing_summary:
            self._detail_tv.setString_(full)
            if self._detail_summarize_btn:
                self._detail_summarize_btn.setTitle_("Summarize")
                self._detail_summarize_btn.setAlphaValue_(1.0)
            self._detail_st_lbl.setStringValue_(self._footer_line("detail"))
            self._detail_showing_summary = False
            self._detail_summary_loading = False
            return

        row = self._current_detail_result
        self._detail_summary_loading = True
        try:
            from ..ai.summarizer import _extractive_bullet_fallback

            quick = _extractive_bullet_fallback(full, max_bullets=4)
            self._detail_tv.setString_(f"Instant recap\n\n{quick}\n\nSharpening with local model…")
        except Exception:
            self._detail_tv.setString_("Generating instant recap…")
        if self._detail_summarize_btn:
            self._detail_summarize_btn.setAlphaValue_(0.55)
        self._detail_st_lbl.setStringValue_("Local model · bullet recap")

        def _run():
            try:
                from ..ai.summarizer import ai_memory_bullets

                bullets = ai_memory_bullets(
                    full,
                    heading=str(row.get("heading") or ""),
                    app_name=str(row.get("app_name") or ""),
                    window_title=str(row.get("window_title") or ""),
                    activity=str(row.get("activity") or ""),
                )
            except Exception:
                bullets = ""
            AppHelper.callAfter(self._finish_detail_summary, bullets, full)

        threading.Thread(target=_run, daemon=True).start()

    def _finish_detail_summary(self, bullets: str, full_original: str):
        self._detail_summary_loading = False
        if self._detail_summarize_btn:
            self._detail_summarize_btn.setAlphaValue_(1.0)
        row = self._current_detail_result
        if not row:
            return
        cur = (row.get("full_text") or row.get("text_snippet", ""))
        if cur != full_original:
            return
        text = (bullets or "").strip()
        if not text:
            from ..ai.summarizer import _extractive_bullet_fallback

            text = _extractive_bullet_fallback(full_original)

        self._apply_detail_summary_text(text)
        if self._detail_summarize_btn:
            self._detail_summarize_btn.setTitle_("Full text")
        self._detail_st_lbl.setStringValue_("Recap ready — tap Full text to restore the capture.")
        self._detail_showing_summary = True

    @objc.python_method
    def _apply_detail_summary_text(self, text: str) -> None:
        """Render the bullet recap with proper hierarchy — title in semibold,
        bullets in regular body, hanging indent under the dot, and a calm
        kicker line. Falls back to plain text if attributed rendering is
        unavailable for any reason."""
        tv = self._detail_tv
        if tv is None:
            return

        bullets: list[str] = []
        for ln in (text or "").splitlines():
            s = ln.strip()
            if not s:
                continue
            s = re.sub(r"^[\s•*\-]+", "", s).strip()
            s = re.sub(r"\s+", " ", s.replace("\t", " ")).strip()
            if s:
                bullets.append(s)
        # Cap to keep the panel calm; the model occasionally over-generates.
        bullets = [b for b in bullets if b][:8]

        try:
            ts = tv.textStorage()
            if ts is None:
                raise RuntimeError("no text storage")
            ts.beginEditing()
            ts.setAttributedString_(
                AppKit.NSAttributedString.alloc().initWithString_(""),
            )

            primary = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
                0.93, 0.95, 0.97, 1.0,
            )
            muted = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
                0.62, 0.68, 0.74, 1.0,
            )
            accent = ACCENT_MINT()

            kicker = AppKit.NSMutableParagraphStyle.alloc().init()
            kicker.setParagraphSpacing_(8.0)
            kicker.setLineSpacing_(1.0)
            kicker_attrs = {
                AppKit.NSFontAttributeName: _round(11, AppKit.NSFontWeightBold),
                AppKit.NSForegroundColorAttributeName: muted,
                AppKit.NSKernAttributeName: 1.8,
                AppKit.NSParagraphStyleAttributeName: kicker,
            }
            ts.appendAttributedString_(
                AppKit.NSAttributedString.alloc().initWithString_attributes_(
                    "AI RECAP\n", kicker_attrs,
                ),
            )

            title_p = AppKit.NSMutableParagraphStyle.alloc().init()
            title_p.setParagraphSpacing_(14.0)
            title_attrs = {
                AppKit.NSFontAttributeName: _round(16, AppKit.NSFontWeightSemibold),
                AppKit.NSForegroundColorAttributeName: primary,
                AppKit.NSParagraphStyleAttributeName: title_p,
            }
            title = (
                "Here is what this moment was about"
                if bullets else "Not enough text for a full recap"
            )
            ts.appendAttributedString_(
                AppKit.NSAttributedString.alloc().initWithString_attributes_(
                    f"{title}\n", title_attrs,
                ),
            )

            bullet_p = AppKit.NSMutableParagraphStyle.alloc().init()
            bullet_p.setFirstLineHeadIndent_(0.0)
            bullet_p.setHeadIndent_(18.0)
            bullet_p.setDefaultTabInterval_(0.0)
            bullet_p.setParagraphSpacing_(8.0)
            bullet_p.setLineSpacing_(2.0)
            bullet_attrs = {
                AppKit.NSFontAttributeName: _round(13, AppKit.NSFontWeightRegular),
                AppKit.NSForegroundColorAttributeName: primary,
                AppKit.NSParagraphStyleAttributeName: bullet_p,
            }
            dot_attrs = dict(bullet_attrs)
            dot_attrs[AppKit.NSForegroundColorAttributeName] = accent
            dot_attrs[AppKit.NSFontAttributeName] = _round(
                13, AppKit.NSFontWeightBold,
            )

            for b in bullets:
                ts.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        "•  ", dot_attrs,
                    ),
                )
                ts.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        f"{b}\n", bullet_attrs,
                    ),
                )

            if not bullets:
                ts.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        "Capture too short to summarise meaningfully.\n",
                        bullet_attrs,
                    ),
                )

            ts.endEditing()
        except Exception:
            # Graceful fallback — plain text is still readable.
            tv.setString_(text)

    @objc.python_method
    def _apply_detail_body_text(self, body: str, heading: str = "") -> None:
        """Render the default detail body with richer context chips."""
        tv = self._detail_tv
        if tv is None:
            return
        raw_lines = [(ln or "").strip() for ln in (body or "").splitlines()]
        raw_lines = [ln for ln in raw_lines if ln]

        def _is_context_line(s: str) -> bool:
            low = s.lower().lstrip("• ").strip()
            return low.startswith((
                "app:", "activity:", "window:", "captured:", "source:",
                "topic:", "people:", "where:",
            ))

        narrative: list[str] = []
        context: list[str] = []
        in_context = False
        for ln in raw_lines:
            if ln.lower() == "context":
                in_context = True
                continue
            if in_context or _is_context_line(ln):
                c = re.sub(r"^[\s•*\-]+", "", ln).strip()
                c = re.sub(r"\s+", " ", c.replace("\t", " ")).strip()
                if c:
                    context.append(c)
            else:
                narrative.append(ln)

        # Drop bullets that only restate the heading.
        h = re.sub(r"[^a-z0-9 ]+", " ", (heading or "").lower()).strip()
        if h:
            kept: list[str] = []
            for ln in narrative:
                t = ln.lstrip("• ").strip()
                t_norm = re.sub(r"[^a-z0-9 ]+", " ", t.lower()).strip()
                if not t_norm:
                    continue
                if t_norm == h or h in t_norm or t_norm in h:
                    continue
                kept.append(ln)
            narrative = kept

        # Defensive split: legacy bullets sometimes pack multiple sentences
        # into one line with missing periods. Splitting at the render layer
        # means existing stored narratives also look right without needing
        # a regenerate click.
        from ..memory.summaries import split_run_on_bullet
        expanded: list[str] = []
        for ln in narrative:
            pieces = split_run_on_bullet(ln)
            expanded.extend(pieces if pieces else [ln])
        narrative = expanded

        try:
            ts = tv.textStorage()
            if ts is None:
                raise RuntimeError("no text storage")
            ts.beginEditing()
            ts.setAttributedString_(AppKit.NSAttributedString.alloc().initWithString_(""))

            primary = W94()
            muted = W60()
            accent = ACCENT_MINT()

            title_p = AppKit.NSMutableParagraphStyle.alloc().init()
            title_p.setParagraphSpacing_(10.0)
            title_attrs = {
                AppKit.NSFontAttributeName: _round(11, AppKit.NSFontWeightBold),
                AppKit.NSForegroundColorAttributeName: muted,
                AppKit.NSKernAttributeName: 1.4,
                AppKit.NSParagraphStyleAttributeName: title_p,
            }
            ts.appendAttributedString_(
                AppKit.NSAttributedString.alloc().initWithString_attributes_(
                    "DETAIL RECAP\n", title_attrs
                )
            )

            bullet_p = AppKit.NSMutableParagraphStyle.alloc().init()
            bullet_p.setFirstLineHeadIndent_(0.0)
            bullet_p.setHeadIndent_(18.0)
            bullet_p.setDefaultTabInterval_(0.0)
            bullet_p.setParagraphSpacing_(9.0)
            bullet_p.setLineSpacing_(2.0)
            bullet_attrs = {
                AppKit.NSFontAttributeName: _round(13),
                AppKit.NSForegroundColorAttributeName: primary,
                AppKit.NSParagraphStyleAttributeName: bullet_p,
            }
            dot_attrs = dict(bullet_attrs)
            dot_attrs[AppKit.NSForegroundColorAttributeName] = accent
            dot_attrs[AppKit.NSFontAttributeName] = _round(13, AppKit.NSFontWeightBold)

            for ln in narrative:
                s = ln.strip()
                if not s:
                    continue
                txt = re.sub(r"^[\s•*\-]+", "", s).strip()
                txt = re.sub(r"\s+", " ", txt.replace("\t", " ")).strip()
                if not txt:
                    continue
                ts.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_("•  ", dot_attrs)
                )
                ts.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(f"{txt}\n", bullet_attrs)
                )

            if context:
                sec_p = AppKit.NSMutableParagraphStyle.alloc().init()
                sec_p.setParagraphSpacing_(8.0)
                sec_attrs = {
                    AppKit.NSFontAttributeName: _round(11, AppKit.NSFontWeightBold),
                    AppKit.NSForegroundColorAttributeName: muted,
                    AppKit.NSKernAttributeName: 1.2,
                    AppKit.NSParagraphStyleAttributeName: sec_p,
                }
                ts.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_("\nCONTEXT\n", sec_attrs)
                )
                chip_p = AppKit.NSMutableParagraphStyle.alloc().init()
                chip_p.setParagraphSpacing_(6.0)
                chip_attrs = {
                    AppKit.NSFontAttributeName: _round(11, AppKit.NSFontWeightMedium),
                    AppKit.NSForegroundColorAttributeName: primary,
                    AppKit.NSBackgroundColorAttributeName: _T("chip_bg"),
                    AppKit.NSParagraphStyleAttributeName: chip_p,
                }
                for c in context:
                    chip = f"  {c}  "
                    ts.appendAttributedString_(
                        AppKit.NSAttributedString.alloc().initWithString_attributes_(f"{chip}\n", chip_attrs)
                    )
            ts.endEditing()
        except Exception:
            tv.setString_(body)

    @objc.python_method
    def _auto_generate_bullets(self, mid: int, row: dict, full: str, heading: str):
        """Kick off bullet-summary generation in background and persist to
        the narrative column once ready, so the next open is instant.

        If the local model is not loaded yet (still downloading, or cold),
        we DO NOT fall back to raw-OCR extractive bullets — that produced
        garbled output. Instead we leave the placeholder up and the user
        can press Regenerate once the model is ready."""
        in_flight = getattr(self, "_bullets_in_flight", None)
        if in_flight is None:
            in_flight = set()
            self._bullets_in_flight = in_flight
        if mid in in_flight:
            return
        in_flight.add(mid)

        if self._detail_st_lbl:
            self._detail_st_lbl.setStringValue_("Sharpening bullet summary…")

        app_n = str(row.get("app_name") or "")
        win_t = str(row.get("window_title") or "")
        act   = str(row.get("activity") or "")

        def _run():
            bullets = ""
            model_ready = False
            try:
                from ..ai.llm import _ready as _ai_ready
                model_ready = _ai_ready.is_set()
                if model_ready:
                    from ..ai.summarizer import ai_memory_bullets
                    bullets = (
                        ai_memory_bullets(
                            full,
                            heading=heading,
                            app_name=app_n,
                            window_title=win_t,
                            activity=act,
                        )
                        or ""
                    ).strip()
                    if not any(
                        ln.lstrip().startswith("•") for ln in bullets.splitlines()
                    ):
                        bullets = ""
            except Exception:
                bullets = ""
            AppHelper.callAfter(
                self._finish_auto_bullets, mid, bullets, model_ready,
            )

        threading.Thread(target=_run, daemon=True).start()

    @objc.python_method
    def _detail_regenerate_bullets(self):
        """Force-regenerate the bullet summary for the open memory."""
        cur = self._current_detail_result
        if not cur or not self._store:
            return
        mid = int(cur.get("id") or -1)
        if mid <= 0:
            return
        # Wipe the cached narrative so _auto_generate_bullets re-runs.
        try:
            self._store.update_ai(mid, narrative="")
        except Exception:
            pass
        cur["narrative"] = ""
        in_flight = getattr(self, "_bullets_in_flight", None)
        if in_flight is not None:
            in_flight.discard(mid)
        full = cur.get("full_text") or cur.get("text_snippet", "")
        heading = cur.get("heading") or ""
        self._apply_detail_body_text(self._compose_detail_body(cur, full, heading), heading=heading)
        if len(full.strip()) >= 40:
            self._auto_generate_bullets(mid, cur, full, heading)
        else:
            self._detail_st_lbl.setStringValue_("Too short to regenerate")

    @objc.python_method
    def _finish_auto_bullets(self, mid: int, bullets: str, model_was_ready: bool):
        in_flight = getattr(self, "_bullets_in_flight", None)
        if in_flight is not None:
            in_flight.discard(mid)
        text = (bullets or "").strip()
        cur = self._current_detail_result
        same_open = cur and int(cur.get("id") or -1) == int(mid)
        if not text:
            # Show an honest status instead of silently dumping raw OCR.
            if same_open and self._detail_st_lbl and not getattr(
                self, "_detail_showing_summary", False
            ):
                if not model_was_ready:
                    self._detail_st_lbl.setStringValue_(
                        "Local model still loading — tap Regenerate when ready"
                    )
                else:
                    self._detail_st_lbl.setStringValue_(
                        "Model returned no bullets — try Regenerate"
                    )
            return
        # Persist so reopen is instant.
        if self._store:
            try:
                self._store.update_ai(mid, narrative=text)
            except Exception:
                pass
        if not same_open:
            return
        # Don't clobber raw-text view when user has clicked Summarize.
        if getattr(self, "_detail_showing_summary", False):
            return
        cur["narrative"] = text
        full = cur.get("full_text") or cur.get("text_snippet", "")
        heading = cur.get("heading") or ""
        self._apply_detail_body_text(self._compose_detail_body(cur, full, heading), heading=heading)
        if self._detail_st_lbl:
            self._detail_st_lbl.setStringValue_(self._footer_line("detail"))

    def _detail_delete(self):
        self._delete_log("detail_delete: clicked")
        if not self._store:
            self._delete_log("detail_delete: aborted, no store")
            return
        if not self._current_detail_result:
            self._delete_log("detail_delete: aborted, no current detail")
            return
        mid = self._current_detail_result.get("id")
        if mid is None:
            self._delete_log("detail_delete: aborted, no id")
            return
        # Capture id BEFORE we touch _hide_detail (it clears
        # _current_detail_result synchronously). The delete must run
        # OUTSIDE the AppKit animation context — wrapping it in an
        # AppHelper callAfter guarantees the animation can finish on the
        # next runloop tick and any exception in the delete path actually
        # propagates instead of getting eaten by the animation block.
        captured_mid = int(mid)
        self._delete_log(f"detail_delete: routing mid={captured_mid}")
        try:
            self._hide_detail()
        except Exception as exc:
            self._delete_log(f"detail_delete: _hide_detail raised {exc!r}")
        try:
            AppHelper.callAfter(self._delete_memory, captured_mid)
            self._delete_log(f"detail_delete: scheduled _delete_memory({captured_mid})")
        except Exception as exc:
            self._delete_log(f"detail_delete: scheduling raised {exc!r}; calling inline")
            try:
                self._delete_memory(captured_mid)
            except Exception as exc2:
                self._delete_log(f"detail_delete: inline _delete_memory raised {exc2!r}")

    # ── Tab switching ─────────────────────────────────────────────────────────

    def _activate_search_input(self):
        if self._tab_mode != "search":
            self._switch_tab("search")
        if self._panel and self._sf_field:
            AppKit.NSApp.activateIgnoringOtherApps_(True)
            self._panel.makeKeyAndOrderFront_(None)
            self._panel.makeFirstResponder_(self._sf_field)

    def _switch_tab(self, mode: str):
        if getattr(self, "_onboard_card", None) is not None:
            try:
                self._onboard_card.dismiss()
            except Exception:
                pass
        prev_mode = getattr(self, "_tab_mode", None)
        self._tab_mode = mode
        for tb in self._tabs:
            tb.setActive_(tb._title.lower() == mode)
        # Microanimation: crossfade the scroll content on tab change. The
        # render call below repopulates ``self._doc`` synchronously, so
        # animating the doc's opacity from 0 → 1 right after gives a
        # gentle "wipe" without any layout flicker. Skipped on first
        # render and when the user prefers reduced motion.
        if (
            prev_mode is not None
            and prev_mode != mode
            and self._doc is not None
            and not _prefers_reduced_motion()
        ):
            try:
                self._doc.setWantsLayer_(True)
                self._doc.layer().setOpacity_(0.0)
                def _fade_doc_in(ctx):
                    ctx.setDuration_(0.22)
                    ctx.setTimingFunction_(
                        AppKit.CAMediaTimingFunction.functionWithName_("easeOut")
                    )
                    self._doc.animator().setAlphaValue_(1.0)
                    self._doc.layer().setOpacity_(1.0)

                AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
                    _fade_doc_in,
                    None,
                )
            except Exception:
                pass
        # Drop the panel-pinned empty-state label whenever we leave Search.
        if mode != "search":
            prev = getattr(self, "_empty_label", None)
            if prev is not None:
                try:
                    prev.removeFromSuperview()
                except Exception:
                    pass
                self._empty_label = None
        if mode == "timeline":
            self._load_timeline()
        elif mode == "starred":
            self._load_starred()
        elif mode == "brain":
            self._load_brain()
        elif mode == "settings":
            self._load_settings()
        else:
            q = str(self._sf_field.stringValue()) if self._sf_field else ""
            self._do_search(q)

    def _sync_list_with_store(self):
        """Reload visible lists + detail chrome so deferred AI titles appear after reopen."""
        if not self._store or not self._doc or not self._scroll:
            return
        row = self._current_detail_result
        if self._detail_view and row and self._store:
            mid = row.get("id")
            if mid:
                fresh = self._store.get_memory_by_id(mid)
                if fresh and not int(fresh.get("is_sensitive") or 0):
                    self._current_detail_result = fresh
                    full = fresh.get("full_text") or fresh.get("text_snippet", "")
                    heading = (fresh.get("heading") or "").strip() or memory_title(
                        fresh.get("source") or "",
                        fresh.get("app_name") or "",
                        fresh.get("activity") or "",
                        fresh.get("window_title") or "",
                        full,
                    )
                    self._detail_title_lbl.setStringValue_(
                        truncate_text(heading.replace("\n", " "), 48))

        mode = getattr(self, "_tab_mode", "search")
        if mode == "timeline":
            self._load_timeline()
        elif mode == "starred":
            self._load_starred()
        elif mode == "brain":
            self._load_brain()
        else:
            q = str(self._sf_field.stringValue()).strip() if self._sf_field else ""
            if q:
                self._do_search(q)
            else:
                self._render_search_empty()

    def _result_from_row(self, r, starred: bool | None = None):
        from ..memory.search import SearchResult
        return SearchResult(
            memory_id=r["id"], score=1.0,
            text_snippet=r["text_snippet"],
            source=r["source"], app_name=r["app_name"],
            created_at=float(r["created_at"]),
            tags=r.get("tags",""), full_text=r.get("full_text",""),
            is_starred=bool(r.get("is_starred",0)) if starred is None else starred,
            window_title=r.get("window_title", ""),
            bundle_id=r.get("bundle_id", ""),
            activity=r.get("activity", ""),
            heading=r.get("heading", ""),
            summary=r.get("summary", ""),
        )

    def _render_search_empty(self):
        if not self._doc or not self._scroll:
            return
        if getattr(self, "_tab_mode", "search") != "search":
            return
        # Search-only surface: no recents, no preloaded memories.
        for sv in list(self._doc.subviews()):
            sv.removeFromSuperview()
        dh = self._scroll.frame().size.height
        self._doc.setFrame_(AppKit.NSMakeRect(0, 0, PANEL_W, dh))

        # Drop any prior empty-state label sitting on the panel itself
        # (we re-mount it fresh on every render so it always tracks the
        # current panel geometry).
        prev = getattr(self, "_empty_label", None)
        if prev is not None:
            try:
                prev.removeFromSuperview()
            except Exception:
                pass
            self._empty_label = None

        # System empty-state line, the way Spotlight/Finder/Mail render it:
        # SF Pro at body size, tertiary label color, no italic, no accent
        # tinting. Adapts automatically to light/dark via NSColor system roles.
        # IMPORTANT: with setAttributedStringValue_, NSTextField ignores
        # setAlignment_; alignment must be baked into a paragraph style in
        # the attributes dict, otherwise text left-aligns inside the frame.
        para = AppKit.NSMutableParagraphStyle.alloc().init()
        para.setAlignment_(AppKit.NSTextAlignmentCenter)
        head_attrs = {
            AppKit.NSFontAttributeName: _round(17),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.tertiaryLabelColor(),
            AppKit.NSParagraphStyleAttributeName: para,
        }
        head_str = AppKit.NSAttributedString.alloc().initWithString_attributes_(
            "Search what your agent can remember.", head_attrs,
        )
        head_w = PANEL_W - 80
        head_h = 24.0
        head_x = (PANEL_W - head_w) / 2.0
        # Pin in PANEL coordinates (not doc coords) so the line lands at the
        # geometric vertical center of the entire panel — bypasses any
        # scroll-view origin offsets.
        head_y = (PANEL_H / 2.0) - (head_h / 2.0)
        head_tf = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(head_x, head_y, head_w, head_h),
        )
        head_tf.setBezeled_(False)
        head_tf.setDrawsBackground_(False)
        head_tf.setSelectable_(False)
        head_tf.setEditable_(False)
        head_tf.setAlignment_(AppKit.NSTextAlignmentCenter)
        head_tf.setAttributedStringValue_(head_str)
        head_tf.setAutoresizingMask_(AppKit.NSViewNotSizable)
        # Mount on the main panel view so coords are panel-relative. Insert
        # directly above the scroll view only — NSWindowAbove + nil puts the
        # field on top of *everything* (including the footer chips).
        if self._main is not None and self._scroll is not None:
            self._main.addSubview_positioned_relativeTo_(
                head_tf, AppKit.NSWindowAbove, self._scroll,
            )
        elif self._main is not None:
            self._main.addSubview_(head_tf)
        else:
            self._doc.addSubview_(head_tf)
        self._empty_label = head_tf

        if self._st_lbl and self._store:
            self._st_lbl.setStringValue_(self._footer_line("empty"))

    @objc.python_method
    def _toggle_empty_reveal(self):
        self._empty_revealed = not self._empty_revealed
        self._render_search_empty()

    def _load_recent(self):
        if not self._store or not self._doc: return
        rows = self._store.get_recent(limit=12)
        results = [self._result_from_row(r) for r in rows]
        self._render_results(results, header="RECENT")
        if self._st_lbl:
            self._st_lbl.setStringValue_(self._footer_line("search"))

    def _load_timeline(self):
        if not self._store:
            return
        rows = self._store.get_all_by_date(limit=200)
        results = [self._result_from_row(r) for r in rows]
        self._render_timeline(results)
        if self._st_lbl:
            self._st_lbl.setStringValue_(self._footer_line("timeline"))

    def _load_starred(self):
        if not self._store: return
        rows = self._store.get_starred(limit=50)
        results = [self._result_from_row(r, starred=True) for r in rows]

        self._render_results(results, header="STARRED")
        if self._st_lbl:
            self._st_lbl.setStringValue_(self._footer_line("starred"))

    def _load_brain(self):
        """Brain tab — rich second-brain view.

        Shows:
          1. Live AI-generated session summary (what you're doing right now)
          2. Recent activity cards grouped by app with curated English headings
          3. App usage breakdown for today
        """
        if not self._doc or not self._scroll or not self._store:
            return
        for sv in list(self._doc.subviews()):
            sv.removeFromSuperview()
        prev = getattr(self, "_empty_label", None)
        if prev is not None:
            try:
                prev.removeFromSuperview()
            except Exception:
                pass
            self._empty_label = None

        now = time.time()
        now_local = time.localtime(now)
        pad_x = 22.0
        dh = self._scroll.frame().size.height

        # ── Gather data ────────────────────────────────────────────────────────
        # Recent memories (last 3 hours) for session summary
        recent_mems: list[dict] = []
        try:
            recent_mems = [
                dict(r) for r in self._store._conn.execute(
                    """
                    SELECT id, app_name, window_title, activity, heading, summary,
                           narrative, text_snippet, created_at, is_sensitive
                    FROM memories
                    WHERE created_at > ? AND is_sensitive = 0
                    ORDER BY created_at DESC
                    LIMIT 40
                    """,
                    (now - 3 * 3600,),
                ).fetchall()
            ]
        except Exception:
            pass

        # App usage today
        sod = time.mktime(time.struct_time((
            now_local.tm_year, now_local.tm_mon, now_local.tm_mday,
            0, 0, 0, 0, 0, -1)))
        app_usage: list[dict] = []
        today_n = 0
        try:
            rows = self._store._conn.execute(
                """
                SELECT app_name, COUNT(*) AS n, MAX(created_at) AS last_ts,
                       MAX(heading) AS last_heading, MAX(summary) AS last_summary
                FROM memories
                WHERE created_at >= ? AND is_sensitive = 0 AND app_name != ''
                GROUP BY app_name
                ORDER BY n DESC
                LIMIT 8
                """,
                (sod,),
            ).fetchall()
            for r in rows:
                app_usage.append(dict(r))
                today_n += int(r["n"])
        except Exception:
            pass

        top_threads = self._build_brain_threads(recent_mems)

        # ── Layout ────────────────────────────────────────────────────────────
        section_h = 30.0
        card_h_app = 84.0
        card_h_thread = 74.0
        gap = 12.0
        cached_summary = (getattr(self, "_brain_summary_text", "") or "").strip()
        summary_body_w = max(280.0, (PANEL_W - 2 * pad_x) - 44.0)
        if cached_summary:
            est = _measure_wrapped_text_height(cached_summary, _round(13), summary_body_w)
            summary_card_h = min(320.0, max(208.0, 126.0 + est))
        else:
            summary_card_h = 220.0

        n_app = len(app_usage)
        n_threads = len(top_threads)
        total_h = (
            16 + section_h + 28 + 16  # header
            + summary_card_h + gap    # session summary card
            + section_h               # TOP THREADS section
            + max(n_threads, 1) * (card_h_thread + gap)
            + section_h               # TODAY section
            + max(n_app, 1) * (card_h_app + gap)
            + 40
        )
        total_h = max(total_h, dh)
        self._doc.setFrame_(AppKit.NSMakeRect(0, 0, PANEL_W, total_h))

        y = total_h - 8

        # ── Hero header ────────────────────────────────────────────────────────
        y -= 28
        date_str = time.strftime("%A, %b %d", now_local)
        hero = _lbl(
            date_str,
            _round(20, AppKit.NSFontWeightSemibold), W94(),
            AppKit.NSTextAlignmentLeft,
        )
        hero.setFrame_(AppKit.NSMakeRect(pad_x, y, PANEL_W - 2 * pad_x - 100, 26))
        self._doc.addSubview_(hero)

        # Refresh button (top right)
        regen_btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            "Refresh",
            AppKit.NSMakeRect(PANEL_W - pad_x - 80, y, 80, 26),
            ACCENT_MINT(), False,
            self._load_brain,
        )
        self._doc.addSubview_(regen_btn)

        y -= 22
        sub_text = f"{today_n} captures today" if today_n else "No captures yet today"
        if recent_mems:
            last_app = (recent_mems[0].get("app_name") or "").strip()
            if last_app:
                sub_text += f"  ·  Last seen in {last_app}"
        sub = _lbl(sub_text, _round(11), W60(), AppKit.NSTextAlignmentLeft)
        sub.setFrame_(AppKit.NSMakeRect(pad_x, y, PANEL_W - 2 * pad_x, 18))
        self._doc.addSubview_(sub)

        y -= 16

        # ── Session summary card ───────────────────────────────────────────────
        card_w = PANEL_W - 2 * pad_x
        y -= summary_card_h
        summary_card = _card(pad_x, y, card_w, summary_card_h)
        # Prevent long generated summary text from painting outside the card.
        try:
            summary_card.setWantsLayer_(True)
            lyr = summary_card.layer()
            if lyr is not None:
                lyr.setMasksToBounds_(True)
        except Exception:
            pass
        self._doc.addSubview_(summary_card)
        self._brain_summary_card = summary_card
        self._brain_summary_card_y = y
        self._brain_summary_card_h = summary_card_h

        # Summary body (placeholder while generating)
        summary_display = cached_summary if cached_summary else (
            "Composing your session digest…\n"
            "• Reading your recent captures.\n"
            "• Distilling what mattered.\n"
            "• Highlighting unique threads."
        )
        scroll_frame = AppKit.NSMakeRect(22, 20, card_w - 44, summary_card_h - 40)
        summary_scroll = AppKit.NSScrollView.alloc().initWithFrame_(scroll_frame)
        summary_scroll.setBorderType_(AppKit.NSNoBorder)
        summary_scroll.setHasVerticalScroller_(True)
        summary_scroll.setHasHorizontalScroller_(False)
        summary_scroll.setAutohidesScrollers_(True)
        summary_scroll.setDrawsBackground_(False)
        summary_scroll.setScrollerKnobStyle_(AppKit.NSScrollerKnobStyleDefault)
        summary_scroll.setScrollerStyle_(AppKit.NSScrollerStyleOverlay)

        summary_tv = AppKit.NSTextView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, scroll_frame.size.width, scroll_frame.size.height),
        )
        summary_tv.setEditable_(False)
        summary_tv.setSelectable_(True)
        summary_tv.setRichText_(True)
        summary_tv.setImportsGraphics_(False)
        summary_tv.setUsesFindPanel_(False)
        summary_tv.setAllowsUndo_(False)
        summary_tv.setDrawsBackground_(False)
        summary_tv.setVerticallyResizable_(True)
        summary_tv.setHorizontallyResizable_(False)
        try:
            tc = summary_tv.textContainer()
            if tc is not None:
                tc.setLineFragmentPadding_(0.0)
                tc.setContainerSize_(AppKit.NSMakeSize(scroll_frame.size.width, 1.0e7))
                tc.setWidthTracksTextView_(True)
            summary_tv.setTextContainerInset_(AppKit.NSMakeSize(0, 0))
        except Exception:
            pass
        summary_scroll.setDocumentView_(summary_tv)
        summary_card.addSubview_(summary_scroll)
        self._brain_summary_scroll = summary_scroll
        self._brain_summary_tv = summary_tv
        self._set_brain_summary_rich_text(summary_tv, summary_display)

        # Kick off async summary generation if we have memories and no cache
        if recent_mems and not cached_summary:
            self._generate_brain_summary(recent_mems)

        y -= gap

        # ── TOP THREADS section ────────────────────────────────────────────────
        y -= section_h
        sh0 = _kern_lbl(
            "TOP 3 THREADS RIGHT NOW",
            _round(10, AppKit.NSFontWeightBold), ACCENT_MINT_DIM(),
            AppKit.NSMakeRect(pad_x, y + 4, PANEL_W - 2 * pad_x, 18),
        )
        self._doc.addSubview_(sh0)

        if not top_threads:
            y -= 40
            em0 = _lbl(
                "Not enough recent context yet to form top threads.",
                _round(12), W60(), AppKit.NSTextAlignmentLeft,
            )
            em0.setFrame_(AppKit.NSMakeRect(pad_x, y, PANEL_W - 2 * pad_x, 20))
            self._doc.addSubview_(em0)
        else:
            for th in top_threads:
                y -= card_h_thread
                self._render_brain_thread_card(th, y, card_h_thread, pad_x)
                y -= gap

        # ── TODAY section ──────────────────────────────────────────────────────
        y -= section_h
        sh1 = _kern_lbl(
            f"TODAY  {today_n} CAPTURES",
            _round(10, AppKit.NSFontWeightBold), ACCENT_MINT_DIM(),
            AppKit.NSMakeRect(pad_x, y + 4, PANEL_W - 2 * pad_x, 18),
        )
        self._doc.addSubview_(sh1)

        if not app_usage:
            y -= 44
            em = _lbl(
                "No captures yet today. Start Corenous and work normally.",
                _round(12), W60(), AppKit.NSTextAlignmentLeft,
            )
            em.setFrame_(AppKit.NSMakeRect(pad_x, y, PANEL_W - 2 * pad_x, 20))
            self._doc.addSubview_(em)
        else:
            for au in app_usage:
                y -= card_h_app
                self._render_brain_app_card(au, y, card_h_app, pad_x)
                y -= gap

        _scroll_to_top(self._scroll, total_h, dh)
        if self._st_lbl:
            self._st_lbl.setStringValue_(self._footer_line("brain"))

    @objc.python_method
    def _build_brain_threads(self, recent_mems: list[dict]) -> list[dict]:
        """Derive top activity threads from recent memories."""
        buckets: dict[str, dict] = {}
        for mem in (recent_mems or [])[:60]:
            heading = (mem.get("heading") or "").strip()
            summary = (mem.get("summary") or "").strip()
            activity = (mem.get("activity") or "").strip()
            app_n = (mem.get("app_name") or "").strip()
            label = heading or summary or activity or app_n
            if not label:
                continue
            key = re.sub(r"[^a-z0-9 ]+", " ", label.lower())
            key = re.sub(r"\s+", " ", key).strip()
            if len(key) < 4:
                continue
            key = " ".join(key.split()[:8])
            row = buckets.get(key)
            ts = float(mem.get("created_at") or 0.0)
            if row is None:
                buckets[key] = {
                    "title": label[:84],
                    "count": 1,
                    "last_ts": ts,
                    "app_name": app_n,
                    "summary": summary[:140],
                }
            else:
                row["count"] = int(row.get("count") or 0) + 1
                if ts >= float(row.get("last_ts") or 0.0):
                    row["last_ts"] = ts
                    row["title"] = label[:84]
                    row["app_name"] = app_n
                    row["summary"] = summary[:140]

        items = list(buckets.values())
        items.sort(key=lambda d: (-int(d.get("count") or 0), -float(d.get("last_ts") or 0.0)))
        return items[:3]

    @objc.python_method
    def _render_brain_thread_card(self, th: dict, y: float, h: float, pad_x: float):
        card_w = PANEL_W - 2 * pad_x
        card = _card(pad_x, y, card_w, h)
        self._doc.addSubview_(card)

        title = (th.get("title") or "").strip()
        count = int(th.get("count") or 0)
        app_n = (th.get("app_name") or "").strip()
        last_ts = float(th.get("last_ts") or 0.0)
        summary = (th.get("summary") or "").strip()

        tl = _lbl(
            title,
            _round(13, AppKit.NSFontWeightSemibold), W94(),
            AppKit.NSTextAlignmentLeft,
        )
        try:
            tl.setMaximumNumberOfLines_(1)
            tl.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        except Exception:
            pass
        tl.setFrame_(AppKit.NSMakeRect(14, h - 28, card_w - 170, 18))
        card.addSubview_(tl)

        pill = _lbl(
            f"{count} hit{'s' if count != 1 else ''}",
            _round(10, AppKit.NSFontWeightMedium), ACCENT_MINT_DIM(),
            AppKit.NSTextAlignmentRight,
        )
        pill.setFrame_(AppKit.NSMakeRect(card_w - 132, h - 28, 118, 16))
        card.addSubview_(pill)

        meta = f"{app_n}  ·  last {_rel(last_ts)}" if app_n else f"last {_rel(last_ts)}"
        ml = _lbl(meta[:80], _round(10), W32(), AppKit.NSTextAlignmentLeft)
        ml.setFrame_(AppKit.NSMakeRect(14, 10, card_w - 28, 14))
        card.addSubview_(ml)

        if summary:
            sl = _lbl(summary[:110].replace("\n", " "), _round(11), W60(), AppKit.NSTextAlignmentLeft)
            try:
                sl.setMaximumNumberOfLines_(1)
                sl.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            except Exception:
                pass
            sl.setFrame_(AppKit.NSMakeRect(14, 28, card_w - 28, 16))
            card.addSubview_(sl)

    @objc.python_method
    def _render_brain_app_card(self, au: dict, y: float, h: float, pad_x: float):
        """One app-usage card inside the Brain tab."""
        card_w = PANEL_W - 2 * pad_x
        card = _card(pad_x, y, card_w, h)
        self._doc.addSubview_(card)

        app_n = str(au.get("app_name") or "").strip()
        n = int(au.get("n") or 0)
        last_ts = float(au.get("last_ts") or 0.0)
        last_heading = (au.get("last_heading") or "").strip()
        last_summary = (au.get("last_summary") or "").strip()
        topic = last_heading or last_summary or app_n

        # App name + capture count
        title = _lbl(
            app_n[:32],
            _round(13, AppKit.NSFontWeightSemibold), W94(),
            AppKit.NSTextAlignmentLeft,
        )
        title.setFrame_(AppKit.NSMakeRect(14, h - 26, card_w - 180, 20))
        card.addSubview_(title)

        count_lbl = _lbl(
            f"{n} capture{'s' if n != 1 else ''} today",
            _round(10, AppKit.NSFontWeightMedium), ACCENT_MINT_DIM(),
            AppKit.NSTextAlignmentRight,
        )
        count_lbl.setFrame_(AppKit.NSMakeRect(card_w - 180, h - 26, 166, 18))
        card.addSubview_(count_lbl)

        # Topic line — curated English heading or summary
        topic_clean = topic[:100].replace("\n", " ")
        topic_lbl = _lbl(
            topic_clean,
            _round(11), W60(),
            AppKit.NSTextAlignmentLeft,
        )
        try:
            topic_lbl.setMaximumNumberOfLines_(2)
            topic_lbl.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
        except Exception:
            pass
        topic_lbl.setFrame_(AppKit.NSMakeRect(14, h - 58, card_w - 28, 30))
        card.addSubview_(topic_lbl)

        # Bottom: last seen
        ls = _lbl(
            f"Last seen {_rel(last_ts)}",
            _round(10), W32(), AppKit.NSTextAlignmentLeft,
        )
        ls.setFrame_(AppKit.NSMakeRect(14, 12, card_w - 28, 16))
        card.addSubview_(ls)

    @objc.python_method
    def _render_brain_feed_card(self, fm: dict, y: float, h: float, pad_x: float):
        """One recent-moment card inside the Brain tab."""
        card_w = PANEL_W - 2 * pad_x
        card = _card(pad_x, y, card_w, h)
        self._doc.addSubview_(card)

        app_n = str(fm.get("app_name") or "").strip()
        heading = str(fm.get("heading") or "").strip()
        summary = str(fm.get("summary") or "").strip()
        narrative = str(fm.get("narrative") or "").strip()
        ts = float(fm.get("created_at") or 0.0)
        mid = fm.get("id")

        # Match "Today" card rhythm: title + right meta + topic + footer.
        heading_clean = heading[:96].replace("\n", " ") or "Recent moment"
        hl = _lbl(
            heading_clean,
            _round(13, AppKit.NSFontWeightSemibold), W94(),
            AppKit.NSTextAlignmentLeft,
        )
        try:
            hl.setMaximumNumberOfLines_(1)
            hl.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        except Exception:
            pass
        hl.setFrame_(AppKit.NSMakeRect(14, h - 26, card_w - 180, 20))
        card.addSubview_(hl)

        time_str = time.strftime("%H:%M", time.localtime(ts)) if ts else ""
        right_meta = f"{time_str}  {app_n}" if app_n else time_str
        rm = _lbl(
            right_meta[:32],
            _round(10, AppKit.NSFontWeightMedium), ACCENT_MINT_DIM(),
            AppKit.NSTextAlignmentRight,
        )
        rm.setFrame_(AppKit.NSMakeRect(card_w - 170, h - 26, 156, 18))
        card.addSubview_(rm)

        raw_detail = (narrative or summary or "").strip()
        detail_lines: list[str] = []
        if raw_detail:
            # Keep bullets stacked (one per line) instead of flattening them.
            chunks: list[str] = []
            for ln in raw_detail.replace("\r", "\n").splitlines():
                s = ln.strip()
                if not s:
                    continue
                if "•" in s:
                    chunks.extend([p.strip() for p in s.split("•") if p.strip()])
                else:
                    chunks.append(s)
            for c in chunks:
                clean = c.lstrip("-*• ").strip()
                if not clean:
                    continue
                if clean.lower().startswith("the real subject"):
                    clean = clean.rstrip(".")
                detail_lines.append(f"• {truncate_text(clean, 96)}")
                if len(detail_lines) >= 2:
                    break
        detail = "\n".join(detail_lines) if detail_lines else truncate_text(raw_detail.replace("\n", " "), 140)
        if detail:
            dl = _lbl(
                detail,
                _round(11), W60(),
                AppKit.NSTextAlignmentLeft,
            )
            try:
                dl.setMaximumNumberOfLines_(3)
                dl.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
            except Exception:
                pass
            dl.setFrame_(AppKit.NSMakeRect(14, h - 76, card_w - 28, 42))
            card.addSubview_(dl)

        ls = _lbl(
            f"Last seen {_rel(ts)}",
            _round(10), W32(), AppKit.NSTextAlignmentLeft,
        )
        ls.setFrame_(AppKit.NSMakeRect(14, 12, card_w - 28, 16))
        card.addSubview_(ls)

        # Keep card clean: no action buttons here.

    @objc.python_method
    def _open_brain_memory(self, mid: int):
        """Open a memory by id from the Brain tab."""
        if not self._store:
            return
        row = self._store.get_memory_by_id(mid)
        if row and not int(row.get("is_sensitive") or 0):
            self._show_detail(self._result_from_row(row))

    @objc.python_method
    def _generate_brain_summary(self, recent_mems: list):
        """Kick off async AI brain summary generation."""
        if getattr(self, "_brain_generating", False):
            return
        self._brain_generating = True

        def _run(mems):
            try:
                from ..ai.summarizer import ai_brain_summary
                text = ai_brain_summary(mems) or ""
            except Exception as exc:
                text = f"Could not generate summary: {exc}"
            AppHelper.callAfter(self._finish_brain_summary, text)

        threading.Thread(target=_run, args=(recent_mems,), daemon=True).start()

    @objc.python_method
    def _finish_brain_summary(self, text: str):
        """Called on main thread when brain summary is ready."""
        self._brain_generating = False
        text = (text or "").strip()
        if not text:
            text = "Nothing significant captured in the last 3 hours."
        self._brain_summary_text = text
        # Update the live label if Brain tab is still open
        tv = getattr(self, "_brain_summary_tv", None)
        if tv is not None and getattr(self, "_tab_mode", "") == "brain":
            self._set_brain_summary_rich_text(tv, text)
        if self._st_lbl and getattr(self, "_tab_mode", "") == "brain":
            self._st_lbl.setStringValue_(self._footer_line("brain"))

    @objc.python_method
    def _set_brain_summary_rich_text(self, tv, text: str) -> None:
        """Render the Brain session digest with richer hierarchy."""
        if tv is None:
            return
        body = (text or "").strip()
        if not body:
            body = "Nothing significant captured in the last 3 hours."
        # The model may emit markdown-ish headings from older cached outputs.
        body = body.replace("**", "")
        lines = [ln.rstrip() for ln in body.splitlines()]
        lines = [ln for ln in lines if ln.strip()]

        try:
            ts = tv.textStorage()
            if ts is None:
                raise RuntimeError("No text storage")
            ts.beginEditing()
            ts.setAttributedString_(AppKit.NSAttributedString.alloc().initWithString_(""))

            primary = W94()
            muted = W60()
            accent = ACCENT_MINT()

            kicker_p = AppKit.NSMutableParagraphStyle.alloc().init()
            kicker_p.setParagraphSpacing_(8.0)
            kicker_attrs = {
                AppKit.NSFontAttributeName: _round(11, AppKit.NSFontWeightBold),
                AppKit.NSForegroundColorAttributeName: muted,
                AppKit.NSKernAttributeName: 1.6,
                AppKit.NSParagraphStyleAttributeName: kicker_p,
            }
            ts.appendAttributedString_(
                AppKit.NSAttributedString.alloc().initWithString_attributes_(
                    "SESSION DIGEST\n", kicker_attrs
                )
            )

            title_p = AppKit.NSMutableParagraphStyle.alloc().init()
            title_p.setParagraphSpacing_(12.0)
            title_attrs = {
                AppKit.NSFontAttributeName: _round(16, AppKit.NSFontWeightSemibold),
                AppKit.NSForegroundColorAttributeName: primary,
                AppKit.NSParagraphStyleAttributeName: title_p,
            }
            ts.appendAttributedString_(
                AppKit.NSAttributedString.alloc().initWithString_attributes_(
                    "What your session says right now\n", title_attrs
                )
            )

            section_p = AppKit.NSMutableParagraphStyle.alloc().init()
            section_p.setParagraphSpacing_(10.0)
            section_attrs = {
                AppKit.NSFontAttributeName: _round(12, AppKit.NSFontWeightBold),
                AppKit.NSForegroundColorAttributeName: accent,
                AppKit.NSKernAttributeName: 0.6,
                AppKit.NSParagraphStyleAttributeName: section_p,
            }
            body_p = AppKit.NSMutableParagraphStyle.alloc().init()
            body_p.setParagraphSpacing_(10.0)
            body_p.setLineSpacing_(2.6)
            body_attrs = {
                AppKit.NSFontAttributeName: _round(13, AppKit.NSFontWeightRegular),
                AppKit.NSForegroundColorAttributeName: primary,
                AppKit.NSParagraphStyleAttributeName: body_p,
            }
            bullet_p = AppKit.NSMutableParagraphStyle.alloc().init()
            bullet_p.setFirstLineHeadIndent_(0.0)
            bullet_p.setHeadIndent_(18.0)
            # Make each bullet breathe: one bullet, spacing, next bullet.
            bullet_p.setParagraphSpacing_(12.0)
            bullet_p.setLineSpacing_(2.6)
            bullet_attrs = dict(body_attrs)
            bullet_attrs[AppKit.NSParagraphStyleAttributeName] = bullet_p

            dot_attrs = dict(bullet_attrs)
            dot_attrs[AppKit.NSForegroundColorAttributeName] = accent
            dot_attrs[AppKit.NSFontAttributeName] = _round(13, AppKit.NSFontWeightBold)

            for ln in lines:
                raw = ln.strip()
                if not raw:
                    continue
                plain = raw.rstrip(":")
                if (raw.endswith(":") and len(raw) <= 40) or raw.isupper():
                    ts.appendAttributedString_(
                        AppKit.NSAttributedString.alloc().initWithString_attributes_(
                            f"{plain.upper()}\n", section_attrs
                        )
                    )
                    continue
                if raw.startswith("•"):
                    ts.appendAttributedString_(
                        AppKit.NSAttributedString.alloc().initWithString_attributes_(
                            "•  ", dot_attrs
                        )
                    )
                    ts.appendAttributedString_(
                        AppKit.NSAttributedString.alloc().initWithString_attributes_(
                            f"{raw.lstrip('•').strip()}\n", bullet_attrs
                        )
                    )
                    continue
                if raw.startswith("→"):
                    ts.appendAttributedString_(
                        AppKit.NSAttributedString.alloc().initWithString_attributes_(
                            "→  ", dot_attrs
                        )
                    )
                    ts.appendAttributedString_(
                        AppKit.NSAttributedString.alloc().initWithString_attributes_(
                            f"{raw.lstrip('→').strip()}\n", bullet_attrs
                        )
                    )
                    continue
                ts.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        f"{raw}\n", body_attrs
                    )
                )
            ts.endEditing()
            try:
                tv.setSelectedRange_(AppKit.NSMakeRange(0, 0))
            except Exception:
                pass
        except Exception:
            try:
                tv.setString_(body)
            except Exception:
                pass

    @objc.python_method
    def _refresh_brain_summary_label(self):
        """Lightweight refresh: update the summary label if new memories exist."""
        if not self._store:
            return
        now = time.time()
        try:
            recent_mems = [
                dict(r) for r in self._store._conn.execute(
                    """
                    SELECT id, app_name, window_title, activity, heading, summary,
                           narrative, text_snippet, created_at, is_sensitive
                    FROM memories
                    WHERE created_at > ? AND is_sensitive = 0
                    ORDER BY created_at DESC
                    LIMIT 40
                    """,
                    (now - 3 * 3600,),
                ).fetchall()
            ]
        except Exception:
            return
        if recent_mems:
            self._generate_brain_summary(recent_mems)

    @objc.python_method
    def _render_context_card(self, ctx: dict, y: float, h: float, pad_x: float):
        """Legacy context card (kept for compatibility)."""
        card_w = PANEL_W - 2 * pad_x
        card = _card(pad_x, y, card_w, h)
        self._doc.addSubview_(card)

        app_n = str(ctx.get("app") or "")
        topic = str(ctx.get("topic") or "").strip()
        n = int(ctx.get("n") or 0)
        days = int(ctx.get("days") or 0)
        last_ts = float(ctx.get("last_ts") or 0.0)

        # Top row: app name (bold) + frequency badge on the right
        title = _lbl(
            app_n[:30],
            _round(14, AppKit.NSFontWeightSemibold), W94(),
            AppKit.NSTextAlignmentLeft,
        )
        title.setFrame_(AppKit.NSMakeRect(14, h - 30, card_w - 200, 22))
        card.addSubview_(title)

        freq_text = f"{n} captures over {days} days" if days > 1 else f"{n} captures today"
        freq = _lbl(
            freq_text,
            _round(10, AppKit.NSFontWeightMedium), ACCENT_MINT_DIM(),
            AppKit.NSTextAlignmentRight,
        )
        freq.setFrame_(AppKit.NSMakeRect(card_w - 200, h - 28, 186, 18))
        card.addSubview_(freq)

        # Topic line
        topic_clean = topic[:90].replace("\n", " ")
        topic_lbl = _lbl(
            topic_clean,
            _round(12), W60(),
            AppKit.NSTextAlignmentLeft,
        )
        try:
            topic_lbl.setMaximumNumberOfLines_(1)
            topic_lbl.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        except Exception:
            pass
        topic_lbl.setFrame_(AppKit.NSMakeRect(14, h - 50, card_w - 28, 18))
        card.addSubview_(topic_lbl)

        # Bottom row: last-seen text
        last_text = f"Last seen {_rel(last_ts)}"
        ls = _lbl(
            last_text, _round(10), W32(), AppKit.NSTextAlignmentLeft,
        )
        ls.setFrame_(AppKit.NSMakeRect(14, 10, card_w - 28, 18))
        card.addSubview_(ls)

    @objc.python_method
    def _open_app(self, app_name: str):
        """Launch (or bring to front) a macOS app by name. Uses the system
        ``open -a`` command which respects user defaults and existing
        windows. No-op on empty input."""
        name = (app_name or "").strip()
        if not name:
            return
        try:
            import subprocess
            subprocess.Popen(
                ["open", "-a", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if self._st_lbl:
                self._st_lbl.setStringValue_(f"Opened {name}")
        except Exception as exc:
            if self._st_lbl:
                self._st_lbl.setStringValue_(f"Could not open {name}: {exc}")

    @objc.python_method
    def _show_context_memories(self, app_name: str, activity: str):
        """Jump to Search and filter by this context's app, so the user can
        scan or open the actual captures behind this card."""
        if self._sf_field is not None:
            self._sf_field.setStringValue_(app_name)
        self._switch_tab("search")
        self._do_search(app_name)

    def _load_health(self):
        """Render a lightweight Diagnostics surface: capture state, store
        sizes, model state, last capture, ai backlog. Pure read-only — no
        background tasks. Re-renders every time the tab is opened."""
        if not self._doc or not self._scroll:
            return
        for sv in list(self._doc.subviews()):
            sv.removeFromSuperview()

        info = self._gather_health()

        dh = self._scroll.frame().size.height
        line_h = 26.0
        sec_h = 22.0
        pad_x = 24.0
        rows = [
            ("CAPTURE",
                [
                    ("Status", "Paused — ⌘P to resume" if info["paused"] else "Live"),
                    ("Last capture", info["last_capture"] or "—"),
                    ("Excluded apps", info["excluded"] or "none"),
                    ("Stealth", "on" if info["stealth"] else "off"),
                ],
            ),
            ("STORE",
                [
                    ("Memories", f"{info['n_memories']:,}"),
                    ("Starred", f"{info['n_starred']:,}"),
                    ("Encrypted vault", f"{info['n_vault']:,}"),
                    ("Tombstones", f"{info['n_tombstones']:,}"),
                    ("Database size", info["db_size"]),
                    ("Vector cache", info["vec_count"]),
                ],
            ),
            ("AI",
                [
                    ("Model", info["model"] or "—"),
                    ("Pending narratives", f"{info['n_pending_ai']:,}"),
                    ("Full refinement", "on" if info["refine_full"] else "off"),
                ],
            ),
        ]

        total = 0
        for _h, items in rows:
            total += sec_h + len(items) * line_h + 8
        total = max(total + 40, dh)
        self._doc.setFrame_(AppKit.NSMakeRect(0, 0, PANEL_W, total))

        y = total - 16.0
        for header, items in rows:
            y -= sec_h
            hl = _lbl(header, _round(10, AppKit.NSFontWeightSemibold), W32(),
                     AppKit.NSTextAlignmentLeft)
            hl.setFrame_(AppKit.NSMakeRect(pad_x, y, PANEL_W - 2 * pad_x, sec_h))
            self._doc.addSubview_(hl)
            for k, v in items:
                y -= line_h
                kl = _lbl(k, _round(12), W60(), AppKit.NSTextAlignmentLeft)
                kl.setFrame_(AppKit.NSMakeRect(pad_x, y, 160, line_h))
                self._doc.addSubview_(kl)
                vl = _lbl(str(v), _round(12, AppKit.NSFontWeightMedium), W94(),
                          AppKit.NSTextAlignmentRight)
                vl.setFrame_(
                    AppKit.NSMakeRect(pad_x + 160, y,
                                      PANEL_W - 2 * pad_x - 160, line_h),
                )
                try:
                    vl.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
                    vl.setMaximumNumberOfLines_(1)
                except Exception:
                    pass
                self._doc.addSubview_(vl)
            y -= 8

        if self._st_lbl:
            self._st_lbl.setStringValue_(self._footer_line("health"))

    @objc.python_method
    def _gather_health(self) -> dict:
        out = {
            "paused": self._is_capture_paused(),
            "stealth": bool(getattr(self, "_stealth_on", True)),
            "excluded": "",
            "n_memories": 0, "n_starred": 0, "n_vault": 0, "n_tombstones": 0,
            "n_pending_ai": 0, "db_size": "—", "vec_count": "—",
            "model": "", "last_capture": "", "refine_full": False,
        }
        if not self._store:
            return out
        try:
            out["n_memories"] = self._store.get_memory_count()
        except Exception:
            pass
        try:
            out["n_starred"] = len(self._store.get_starred(limit=100000))
        except Exception:
            pass
        try:
            out["n_vault"] = len(self._store.get_vault_entries())
        except Exception:
            pass
        try:
            r = self._store._conn.execute(
                "SELECT COUNT(*) AS c FROM deleted_hashes"
            ).fetchone()
            out["n_tombstones"] = int(r["c"]) if r else 0
        except Exception:
            pass
        try:
            r = self._store._conn.execute(
                "SELECT COUNT(*) AS c FROM memories WHERE ai_state = 'pending'"
            ).fetchone()
            out["n_pending_ai"] = int(r["c"]) if r else 0
        except Exception:
            pass
        try:
            import os as _os
            r = self._store._conn.execute("PRAGMA database_list").fetchone()
            p = r[2] if r and len(r) >= 3 else ""
            if p and _os.path.exists(p):
                sz = _os.path.getsize(p)
                out["db_size"] = (
                    f"{sz / (1024 * 1024):.1f} MB" if sz > 512 * 1024
                    else f"{sz / 1024:.0f} KB"
                )
        except Exception:
            pass
        try:
            cache = self._cache
            if cache is not None and hasattr(cache, "_cvs"):
                out["vec_count"] = f"{len(cache._cvs):,}"
        except Exception:
            pass
        try:
            import json as _json
            raw = self._store.get_config("excluded_apps", "[]") or "[]"
            items = _json.loads(raw)
            if isinstance(items, list) and items:
                shown = ", ".join(str(x) for x in items[:4])
                if len(items) > 4:
                    shown += f" +{len(items) - 4}"
                out["excluded"] = shown
        except Exception:
            pass
        # Last capture timestamp
        try:
            row = self._store._conn.execute(
                "SELECT created_at FROM memories ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row:
                ts = float(row["created_at"])
                out["last_capture"] = time.strftime(
                    "%H:%M:%S", time.localtime(ts),
                )
        except Exception:
            pass
        # Model + refine status from the on-disk config.
        try:
            from ..ai.llm import (  # type: ignore
                _ready as _ai_ready,
                _llm as _ai_llm,
                model_status_label,
            )
            label = model_status_label()
            if _ai_ready.is_set():
                out["model"] = f"{label}  ·  ready"
            elif _ai_llm is not None:
                out["model"] = f"{label}  ·  loading"
            else:
                out["model"] = f"{label}  ·  cold"
        except Exception:
            out["model"] = "Local GGUF"
        try:
            v = self._store.get_config("refine_full", "")
            if v in ("0", "1"):
                out["refine_full"] = v == "1"
        except Exception:
            pass
        return out

    # ── Settings tab ─────────────────────────────────────────────────────────

    def _load_settings(self):
        """Settings surface — compact, curated cards with tighter spacing."""
        if not self._doc or not self._scroll or not self._store:
            return
        for sv in list(self._doc.subviews()):
            sv.removeFromSuperview()
        prev = getattr(self, "_empty_label", None)
        if prev is not None:
            try:
                prev.removeFromSuperview()
            except Exception:
                pass
            self._empty_label = None

        from ..ai.remote_llm import load_remote_config
        from ..ai.llm import _PRESETS as LLM_PRESETS  # type: ignore

        rcfg = load_remote_config()
        provider = (rcfg.get("provider") or "local").lower()
        api_key = (rcfg.get("openrouter_api_key") or "").strip()
        cur_local_preset = ""
        try:
            cur_local_preset = (
                self._store.get_config("local_llm_preset", "") or ""
            ).strip().lower()
        except Exception:
            pass
        if not cur_local_preset:
            cur_local_preset = "llama-3.2-3b"

        dh = self._scroll.frame().size.height
        pad_x = 28.0
        card_w = PANEL_W - 2 * pad_x
        header_h = 84.0   # serif title + hint + hairline
        row_h = 64.0

        # Card heights = header + N rows + bottom padding (must match row count).
        bottom_pad = 28.0
        if provider == "openrouter":
            h_model = header_h + row_h * 3 + bottom_pad  # provider + key + actions
        else:
            h_model = header_h + row_h * 2 + bottom_pad  # provider + preset
        h_capture = header_h + row_h * 3
        h_refine = header_h + row_h * 1 + 4.0
        h_agent = header_h + row_h * 1 + 36.0  # row + command preview
        h_about = header_h + row_h * 1 + 24.0
        gap_v = 18.0

        hero_h = 145.0
        cards = (h_model, h_capture, h_refine, h_agent, h_about)
        total_h = hero_h + sum(cards) + (len(cards) - 1) * gap_v + 72.0
        total_h = max(total_h, dh)
        self._doc.setFrame_(AppKit.NSMakeRect(0, 0, PANEL_W, total_h))

        y = total_h - 32.0

        # ── Hero status banner ──────────────────────────────────────────
        y = self._render_settings_hero(y, pad_x, card_w, hero_h)

        # ── Card factory ────────────────────────────────────────────────
        def begin_card(h: float) -> tuple[AppKit.NSView, float]:
            nonlocal y
            y -= gap_v
            y -= h
            cv = _card(pad_x, y, card_w, h)
            try:
                cv.setWantsLayer_(True)
                cv.layer().setMasksToBounds_(True)
            except Exception:
                pass
            self._doc.addSubview_(cv)
            return cv, h

        # Compensate for the leading gap_v on the first card below the hero.
        y += gap_v

        # ── AI Model card ───────────────────────────────────────────────
        card, ch = begin_card(h_model)
        rows_top = self._render_settings_card_header(
            card, "AI Model",
            "Pick where summaries and bullets come from.",
            ch, card_w,
        )

        # Row 1: Provider segmented control (Local | OpenRouter)
        cx, cy, rows_top = self._settings_row(
            card, rows_top, card_w,
            "Provider",
            "Local stays on your Mac. OpenRouter uses your API key.",
            control_w=200.0, row_h=row_h,
        )
        seg_w = (200.0 - 6) / 2.0
        local_active = provider != "openrouter"
        local_btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            "Local",
            AppKit.NSMakeRect(cx, cy, seg_w, 30),
            ACCENT_MINT() if local_active else W60(),
            False,
            lambda: self._settings_set_provider("local"),
        )
        card.addSubview_(local_btn)
        or_btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            "OpenRouter",
            AppKit.NSMakeRect(cx + seg_w + 6, cy, seg_w, 30),
            ACCENT_MINT() if not local_active else W60(),
            False,
            lambda: self._settings_set_provider("openrouter"),
        )
        card.addSubview_(or_btn)

        if provider != "openrouter":
            # Row 2: Local preset popup with inline save
            cx, cy, rows_top = self._settings_row(
                card, rows_top, card_w,
                "Local model",
                "Switching downloads the new GGUF on next restart.",
                control_w=260.0, row_h=row_h, show_rule=False,
            )
            popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
                AppKit.NSMakeRect(cx, cy, 180, 30), False,
            )
            try:
                popup.setBezelStyle_(AppKit.NSBezelStyleRounded)
            except Exception:
                pass
            items: list[tuple[str, str]] = []
            for k, meta in LLM_PRESETS.items():
                items.append((k, f"{meta['label']}   {meta['size_blurb']}"))
            items.sort(key=lambda kv: kv[0])
            sel_idx = 0
            for i, (k, label) in enumerate(items):
                popup.addItemWithTitle_(label)
                try:
                    popup.lastItem().setRepresentedObject_(k)
                except Exception:
                    pass
                if k == cur_local_preset:
                    sel_idx = i
            popup.selectItemAtIndex_(sel_idx)
            self._settings_local_preset_popup = popup
            card.addSubview_(popup)
            save_btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
                "Save",
                AppKit.NSMakeRect(cx + 186, cy, 74, 30),
                ACCENT_MINT(), False, self._settings_save_local_preset,
            )
            card.addSubview_(save_btn)
        else:
            # Row 2: API key
            cx, cy, rows_top = self._settings_row(
                card, rows_top, card_w,
                "API key",
                "Your OpenRouter local key.",
                control_w=300.0, row_h=row_h,
            )
            con, field = _input(
                (cx, cy, 300.0, 30),
                "sk_or_…", size=12, lpad=12,
            )
            try:
                field.setStringValue_(api_key)
            except Exception:
                pass
            self._settings_or_key_field = field
            card.addSubview_(con)

            # Row 3: action buttons (save + test)
            cx, cy, rows_top = self._settings_row(
                card, rows_top, card_w,
                "Apply changes",
                "Save your key, then verify the route.",
                control_w=300.0, row_h=row_h, show_rule=False,
            )
            save_btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
                "Save & use",
                AppKit.NSMakeRect(cx, cy, 140, 30),
                ACCENT_MINT(), False, self._settings_save_openrouter,
            )
            card.addSubview_(save_btn)
            test_btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
                "Test connection",
                AppKit.NSMakeRect(cx + 150, cy, 150, 30),
                W60(), False, self._settings_test_openrouter,
            )
            card.addSubview_(test_btn)

        # ── Capture card ────────────────────────────────────────────────
        card, ch = begin_card(h_capture)
        rows_top = self._render_settings_card_header(
            card, "Capture",
            "Control how memories are recorded in the background.",
            ch, card_w,
        )
        paused = self._is_capture_paused()
        lite_on = self._is_lite_mode()
        stealth_on = bool(getattr(self, "_stealth_on", True))

        capture_rows = [
            ("Pause capture", "Halts every stream when on.",
             paused, self._settings_toggle_capture),
            ("Stealth mode", "Hides the panel from screen sharing.",
             stealth_on, self._settings_toggle_stealth),
            ("Lite mode", "Skips OCR while keeping lightweight capture.",
             lite_on, self._settings_toggle_lite_mode),
        ]
        for i, (label, hint, is_on, cb) in enumerate(capture_rows):
            cx, cy, rows_top = self._settings_row(
                card, rows_top, card_w, label, hint,
                control_w=96.0, row_h=row_h,
                show_rule=(i < len(capture_rows) - 1),
            )
            tog = self._settings_toggle(is_on, cb)
            tog.setFrame_(AppKit.NSMakeRect(cx, cy, 96, 30))
            card.addSubview_(tog)

        # ── Refinement card ─────────────────────────────────────────────
        card, ch = begin_card(h_refine)
        rows_top = self._render_settings_card_header(
            card, "Refinement",
            "Run an extra narrate + distill pass for richer detail panels.",
            ch, card_w,
        )
        try:
            full_on = (self._store.get_config("refine_full", "") == "1")
        except Exception:
            full_on = False
        cx, cy, rows_top = self._settings_row(
            card, rows_top, card_w,
            "Full refinement",
            "Costs about 3× more local model time per capture.",
            control_w=96.0, row_h=row_h, show_rule=False,
        )
        tog = self._settings_toggle(full_on, self._settings_toggle_refine_full)
        tog.setFrame_(AppKit.NSMakeRect(cx, cy, 96, 30))
        card.addSubview_(tog)

        # ── Agent bridge card ───────────────────────────────────────────
        card, ch = begin_card(h_agent)
        rows_top = self._render_settings_card_header(
            card, "Agent Bridge",
            "Let Claude Desktop, Cursor, and other clients recall your captures.",
            ch, card_w,
        )
        cx, cy, rows_top = self._settings_row(
            card, rows_top, card_w,
            "Connect agents",
            "Copy the JSON config or the CLI command into your client.",
            control_w=300.0, row_h=row_h, show_rule=False,
        )
        copy_cfg_btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            "Copy MCP config",
            AppKit.NSMakeRect(cx, cy, 145, 30),
            ACCENT_MINT(), False, self._settings_copy_mcp_config,
        )
        card.addSubview_(copy_cfg_btn)
        cmd_btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            "Copy command",
            AppKit.NSMakeRect(cx + 155, cy, 145, 30),
            W60(), False, self._settings_copy_agent_command,
        )
        card.addSubview_(cmd_btn)
        # Mono command preview anchored at the bottom of the card.
        try:
            import shutil
            import sys

            binary = shutil.which("corenous-ai")
            cmd_text = (
                f"{binary} agent serve"
                if binary
                else f"{sys.executable or 'python3'} -m src.cli.main agent serve"
            )
        except Exception:
            cmd_text = "corenous-ai agent serve"
        mono = _lbl(
            cmd_text, AppKit.NSFont.userFixedPitchFontOfSize_(11),
            W32(), AppKit.NSTextAlignmentLeft,
        )
        mono.setFrame_(AppKit.NSMakeRect(24, 18, card_w - 48, 16))
        card.addSubview_(mono)

        # ── About card ──────────────────────────────────────────────────
        card, ch = begin_card(h_about)
        rows_top = self._render_settings_card_header(
            card, "About",
            "Corenous AI · MIT licensed · your data stays on this Mac.",
            ch, card_w,
        )
        try:
            data_dir = str(self._data_dir or "")
        except Exception:
            data_dir = ""
        cx, cy, rows_top = self._settings_row(
            card, rows_top, card_w,
            "Tour",
            "Replay the shortcut and privacy onboarding.",
            control_w=170.0, row_h=row_h, show_rule=False,
        )
        tour_btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            "Replay tour",
            AppKit.NSMakeRect(cx, cy, 170, 30),
            W60(), False, self._settings_replay_onboarding,
        )
        card.addSubview_(tour_btn)
        if data_dir:
            display_path = data_dir
            try:
                from pathlib import Path as _P_about
                home = str(_P_about.home())
                if display_path.startswith(home):
                    display_path = "~" + display_path[len(home):]
            except Exception:
                pass
            path_lbl = _lbl(
                f"Data folder · {display_path}",
                AppKit.NSFont.userFixedPitchFontOfSize_(10),
                W32(), AppKit.NSTextAlignmentLeft,
            )
            path_lbl.setFrame_(AppKit.NSMakeRect(24, 18, card_w - 48, 14))
            card.addSubview_(path_lbl)

        _scroll_to_top(self._scroll, total_h, dh)
        if self._st_lbl:
            self._st_lbl.setStringValue_(self._footer_line("settings"))

    @objc.python_method
    def _render_settings_hero(self, y: float, pad_x: float, card_w: float, hero_h: float) -> float:
        """Top banner: title + status chips. No card, just typography."""
        doc = self._doc
        title = _lbl(
            "Settings",
            _didot(26), W94(), AppKit.NSTextAlignmentLeft,
        )
        title.setFrame_(AppKit.NSMakeRect(pad_x, y - 40, card_w, 36))
        doc.addSubview_(title)

        # Status chip row — three cells: Model, Capture, Memories.
        info = {}
        try:
            info = self._gather_settings_stats() or {}
        except Exception:
            info = {}
        chip_w = (card_w - 28) / 3.0
        chip_y = y - 96
        chip_h = 44.0
        chips = [
            ("MODEL", info.get("model", "Local GGUF")),
            (
                "CAPTURE",
                "paused" if self._is_capture_paused() else (
                    "lite" if self._is_lite_mode() else "live"
                ),
            ),
            (
                "MEMORIES",
                f"{info.get('n_memories', 0):,}"
                if isinstance(info.get("n_memories", 0), int)
                else "—",
            ),
        ]
        for i, (cap, val) in enumerate(chips):
            cx = pad_x + i * (chip_w + 14.0)
            chip = _card(cx, chip_y, chip_w, chip_h)
            doc.addSubview_(chip)
            cap_lbl = _kern_lbl(
                cap, _round(9, AppKit.NSFontWeightBold), W32(),
                AppKit.NSMakeRect(14, chip_h - 18, chip_w - 28, 10),
            )
            chip.addSubview_(cap_lbl)
            val_lbl = _lbl(
                str(val),
                _round(13, AppKit.NSFontWeightSemibold), W94(),
                AppKit.NSTextAlignmentLeft,
            )
            val_lbl.setFrame_(AppKit.NSMakeRect(14, 8, chip_w - 28, 18))
            chip.addSubview_(val_lbl)
        return y - hero_h

    @objc.python_method
    def _render_settings_card_header(
        self, card, title: str, hint: str, ch: float, card_w: float,
    ) -> float:
        """Card title (serif) + hint + hairline. Returns the y where rows start."""
        inset = 24.0
        body_w = card_w - inset * 2
        t = _lbl(
            title, _didot(20), W94(), AppKit.NSTextAlignmentLeft,
        )
        t.setFrame_(AppKit.NSMakeRect(inset, ch - 40, body_w, 28))
        card.addSubview_(t)
        if hint:
            ht = _lbl(
                hint, _round(11), W60(), AppKit.NSTextAlignmentLeft,
            )
            try:
                ht.setMaximumNumberOfLines_(2)
                ht.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
            except Exception:
                pass
            ht.setFrame_(AppKit.NSMakeRect(inset, ch - 64, body_w, 18))
            card.addSubview_(ht)
        # Hairline under the header
        rule_y = ch - 84
        rule = AppKit.NSView.alloc().initWithFrame_(
            AppKit.NSMakeRect(inset, rule_y, body_w, 1),
        )
        rule.setWantsLayer_(True)
        try:
            rule.layer().setBackgroundColor_(_T("input_border").CGColor())
        except Exception:
            pass
        card.addSubview_(rule)
        return rule_y

    @objc.python_method
    def _settings_row(
        self, card, y: float, card_w: float, label: str, hint: str,
        control_w: float = 220.0, row_h: float = 64.0, show_rule: bool = True,
    ) -> tuple[float, float, float]:
        """Render a label + description on the left side of a settings row.

        Returns ``(control_x, control_y_center, next_row_y)`` so callers can
        place the right-side control without computing geometry."""
        inset = 24.0
        body_w = card_w - inset * 2
        text_w = body_w - control_w - 16.0
        # Vertical centering of the label/desc block within row_h.
        block_h = 38.0
        block_y = y - row_h + (row_h - block_h) / 2.0
        lbl = _lbl(
            label, _round(13, AppKit.NSFontWeightSemibold), W94(),
            AppKit.NSTextAlignmentLeft,
        )
        lbl.setFrame_(AppKit.NSMakeRect(inset, block_y + 20, text_w, 18))
        card.addSubview_(lbl)
        if hint:
            hl = _lbl(
                hint, _round(11), W60(), AppKit.NSTextAlignmentLeft,
            )
            try:
                hl.setMaximumNumberOfLines_(1)
                hl.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            except Exception:
                pass
            hl.setFrame_(AppKit.NSMakeRect(inset, block_y + 0, text_w, 16))
            card.addSubview_(hl)
        control_x = inset + body_w - control_w
        control_y = y - row_h + (row_h - 30.0) / 2.0
        next_y = y - row_h
        if show_rule:
            rule = AppKit.NSView.alloc().initWithFrame_(
                AppKit.NSMakeRect(inset, next_y, body_w, 1),
            )
            rule.setWantsLayer_(True)
            try:
                rule.layer().setBackgroundColor_(
                    _T("input_border").colorWithAlphaComponent_(0.6).CGColor()
                )
            except Exception:
                pass
            card.addSubview_(rule)
        return control_x, control_y, next_y

    @objc.python_method
    def _settings_toggle(self, on: bool, callback, width: float = 96.0):
        """Compact pill toggle for settings rows."""
        title = "On" if on else "Off"
        tint = ACCENT_MINT() if on else W60()
        btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            title,
            AppKit.NSMakeRect(0, 0, width, 30),
            tint, False, callback,
        )
        return btn

    @objc.python_method
    def _gather_settings_stats(self) -> dict:
        out: dict = {}
        if not self._store:
            return out
        try:
            out["n_memories"] = self._store.get_memory_count()
        except Exception:
            pass
        try:
            from ..ai.llm import model_status_label  # type: ignore

            out["model"] = model_status_label()
        except Exception:
            out["model"] = "Local GGUF"
        return out

    @objc.python_method
    def _settings_replay_onboarding(self) -> None:
        try:
            if self._store:
                self._store.set_config("onboarded", "0")
        except Exception:
            pass
        try:
            self.show_onboarding()
        except Exception:
            pass

    @objc.python_method
    def _settings_set_provider(self, provider: str):
        from ..ai.remote_llm import load_remote_config, save_remote_config
        cfg = load_remote_config()
        cfg["provider"] = "openrouter" if provider == "openrouter" else "local"
        save_remote_config(cfg)
        if self._st_lbl:
            self._st_lbl.setStringValue_(
                f"Provider switched to {cfg['provider']}"
            )
        self._load_settings()

    @objc.python_method
    def _settings_save_openrouter(self):
        from ..ai.remote_llm import (
            RECOMMENDED_MODELS,
            load_remote_config,
            save_remote_config,
        )
        cfg = load_remote_config()
        field = getattr(self, "_settings_or_key_field", None)
        if field is not None:
            cfg["openrouter_api_key"] = str(field.stringValue()).strip()
        if not cfg.get("openrouter_model") and RECOMMENDED_MODELS:
            cfg["openrouter_model"] = RECOMMENDED_MODELS[0][0]
        cfg["provider"] = "openrouter"
        save_remote_config(cfg)
        if self._st_lbl:
            ok = bool(cfg.get("openrouter_api_key"))
            self._st_lbl.setStringValue_(
                "Saved. OpenRouter is now your AI provider."
                if ok else "Saved, but API key is empty. Paste it above."
            )

    @objc.python_method
    def _settings_test_openrouter(self):
        from ..ai.remote_llm import openrouter_chat, load_remote_config
        # Persist any unsaved field changes first so the test uses the
        # value the user just typed.
        self._settings_save_openrouter()
        if self._st_lbl:
            self._st_lbl.setStringValue_("Testing OpenRouter…")
        def _run():
            ok_text = openrouter_chat(
                "Reply with exactly: PONG",
                max_tokens=8, timeout_s=20.0,
            )
            ok = "PONG" in (ok_text or "").upper()
            AppHelper.callAfter(
                self._settings_test_result, ok, (ok_text or "(empty)")[:80],
            )
        threading.Thread(target=_run, daemon=True).start()

    @objc.python_method
    def _settings_test_result(self, ok: bool, sample: str):
        if not self._st_lbl:
            return
        if ok:
            self._st_lbl.setStringValue_("OpenRouter OK. Cloud model is live.")
        else:
            self._st_lbl.setStringValue_(
                f"OpenRouter failed. Check your key and model. Got: {sample}"
            )

    @objc.python_method
    def _settings_save_local_preset(self):
        popup = getattr(self, "_settings_local_preset_popup", None)
        if popup is None or not self._store:
            return
        item = popup.selectedItem()
        rep = item.representedObject() if item is not None else None
        preset = str(rep or "").strip()
        if not preset:
            return
        try:
            self._store.set_config("local_llm_preset", preset)
        except Exception:
            pass
        if self._st_lbl:
            self._st_lbl.setStringValue_(
                f"Preset saved: {preset}. Restart corenous to apply."
            )

    @objc.python_method
    def _settings_toggle_capture(self):
        self._toggle_capture_pause()
        self._load_settings()

    @objc.python_method
    def _settings_toggle_stealth(self):
        self._toggle_stealth()
        self._load_settings()

    @objc.python_method
    def _settings_toggle_lite_mode(self):
        self._toggle_lite_mode()
        self._load_settings()

    @objc.python_method
    def _settings_toggle_refine_full(self):
        if not self._store:
            return
        try:
            cur = self._store.get_config("refine_full", "")
            new = "0" if cur == "1" else "1"
            self._store.set_config("refine_full", new)
        except Exception:
            pass
        self._load_settings()

    @objc.python_method
    def _settings_copy_mcp_config(self):
        """Copy the Claude Desktop / Cursor MCP snippet to the clipboard.

        Resolves the actual ``corenous-ai`` binary if it is on PATH so the
        snippet works without further editing for most users; falls back to
        the module form when running from source.
        """
        import json
        import shutil
        import sys

        binary = shutil.which("corenous-ai")
        if binary:
            entry = {"command": binary, "args": ["agent", "serve"]}
        else:
            entry = {
                "command": sys.executable or "python3",
                "args": ["-m", "src.cli.main", "agent", "serve"],
            }
        snippet = {"mcpServers": {"corenous": entry}}
        text = json.dumps(snippet, indent=2)
        try:
            pb = AppKit.NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(text, AppKit.NSPasteboardTypeString)
            self._flash_status("MCP config copied. Paste into Claude or Cursor.")
        except Exception:
            self._flash_status("Could not copy MCP config")

    @objc.python_method
    def _settings_copy_agent_command(self):
        """Copy the raw CLI invocation for agent integrations."""
        import shutil

        binary = shutil.which("corenous-ai") or "corenous-ai"
        text = f"{binary} agent serve"
        try:
            pb = AppKit.NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(text, AppKit.NSPasteboardTypeString)
            self._flash_status("Agent CLI command copied")
        except Exception:
            self._flash_status("Could not copy CLI command")

    def _render_results(self, results, header="RESULTS"):
        doc = self._doc
        if doc is None: return
        for sv in list(doc.subviews()): sv.removeFromSuperview()
        # Tear down the panel-pinned empty-state label whenever real
        # results take over.
        prev = getattr(self, "_empty_label", None)
        if prev is not None:
            try:
                prev.removeFromSuperview()
            except Exception:
                pass
            self._empty_label = None

        dh = self._scroll.frame().size.height

        if not results:
            icon = _sym("magnifyingglass", 28) if header == "RESULTS" else _sym("clock.arrow.circlepath", 28)
            if icon:
                iv = AppKit.NSImageView.alloc().initWithFrame_(
                    AppKit.NSMakeRect(PANEL_W/2-16, dh/2+14, 32, 32))
                iv.setImage_(icon); iv.setContentTintColor_(W32())
                doc.addSubview_(iv)
            msg  = ("No starred items yet" if header == "STARRED"
                    else "No matches" if header == "RESULTS"
                    else "No captures yet")
            hint = ("Open a memory and tap Star in the detail panel." if header == "STARRED"
                    else "Try other keywords or switch to Timeline." if header == "RESULTS"
                    else "Run corenous-ai start, then browse normally.")
            m1 = _lbl(msg,  _sf(13), W60(), AppKit.NSTextAlignmentCenter)
            m1.setFrame_(AppKit.NSMakeRect(40, dh/2-6, PANEL_W-80, 18))
            doc.addSubview_(m1)
            m2 = _lbl(hint, _sf(11), W32(), AppKit.NSTextAlignmentCenter)
            m2.setFrame_(AppKit.NSMakeRect(40, dh/2-26, PANEL_W-80, 16))
            doc.addSubview_(m2)
            doc.setFrame_(AppKit.NSMakeRect(0, 0, PANEL_W, dh))
        else:
            SECTION_H = 36.0
            label = (f"RESULTS  {len(results)}"
                     if header not in ("RECENT", "STARRED", "TIMELINE")
                     else header)
            total_h = SECTION_H + len(results) * ROW_H
            dh2 = max(total_h, dh)
            doc.setFrame_(AppKit.NSMakeRect(0, 0, PANEL_W, dh2))

            sh = _kern_lbl(label, _round(11, AppKit.NSFontWeightBold), ACCENT_MINT_DIM(),
                           AppKit.NSMakeRect(18, dh2 - SECTION_H + 7, min(PANEL_W - 36, 360), 20))
            doc.addSubview_(sh)

            self._visible_rows = []
            for i, res in enumerate(results):
                y = dh2 - SECTION_H - (i+1)*ROW_H
                row = _make_row(res, PANEL_W,
                                detail_fn=self._show_detail,
                                delete_fn=self._delete_memory,
                                flash_fn=self._flash_status,
                                star_fn=self._toggle_row_star,
                                exclude_fn=self._exclude_app_from_capture)
                row.setFrameOrigin_(AppKit.NSMakePoint(0, y))
                doc.addSubview_(row)
                self._visible_rows.append(row)
            self._focus_idx = -1
            _scroll_to_top(self._scroll, dh2, dh)

    # ── Timeline: plain date-grouped row list ──────────────────────────────

    def _render_timeline(self, results):
        """Clean timeline list: date headers + rows (no boxes)."""
        doc = self._doc
        if doc is None:
            return
        for sv in list(doc.subviews()):
            sv.removeFromSuperview()
        prev = getattr(self, "_empty_label", None)
        if prev is not None:
            try:
                prev.removeFromSuperview()
            except Exception:
                pass
            self._empty_label = None

        dh = self._scroll.frame().size.height

        if not results:
            ic = _sym("calendar.badge.clock", 34)
            if ic:
                iv = AppKit.NSImageView.alloc().initWithFrame_(
                    AppKit.NSMakeRect(PANEL_W / 2 - 17, dh / 2 + 20, 34, 34),
                )
                iv.setImage_(ic)
                iv.setContentTintColor_(ACCENT_MINT_DIM())
                doc.addSubview_(iv)
            m1 = _lbl(
                "Timeline is empty",
                _round(14, AppKit.NSFontWeightSemibold), W60(),
                AppKit.NSTextAlignmentCenter,
            )
            m1.setFrame_(AppKit.NSMakeRect(40, dh / 2 - 14, PANEL_W - 80, 20))
            doc.addSubview_(m1)
            m2 = _lbl(
                "Start Corenous (corenous-ai start). Captures get an "
                "AI headline from your screen and clipboard context.",
                _round(11), W32(), AppKit.NSTextAlignmentCenter,
            )
            m2.setMaximumNumberOfLines_(2)
            m2.setFrame_(AppKit.NSMakeRect(48, dh / 2 - 52, PANEL_W - 96, 36))
            doc.addSubview_(m2)
            doc.setFrame_(AppKit.NSMakeRect(0, 0, PANEL_W, dh))
            return

        groups: list[tuple[str, list]] = []
        cur_hdr: str | None = None
        cur_grp: list = []
        for r in results:
            hdr = _date_header(r.created_at)
            if hdr != cur_hdr:
                if cur_grp:
                    groups.append((cur_hdr, cur_grp))  # type: ignore[arg-type]
                cur_hdr = hdr
                cur_grp = [r]
            else:
                cur_grp.append(r)
        if cur_grp:
            groups.append((cur_hdr, cur_grp))  # type: ignore[arg-type]

        SECTION_H = 36.0
        ROW_H_TIMELINE = 56.0
        total_h = sum(SECTION_H + len(g) * ROW_H_TIMELINE for _, g in groups)
        dh2 = max(total_h, dh)
        doc.setFrame_(AppKit.NSMakeRect(0, 0, PANEL_W, dh2))

        y = dh2
        self._visible_rows = []
        for hdr, grp in groups:
            y -= SECTION_H
            sh = _kern_lbl(
                hdr, _round(10, AppKit.NSFontWeightSemibold),
                _T("section_lbl"),
                AppKit.NSMakeRect(36, y + 4, min(PANEL_W - 72, 360), 22),
            )
            doc.addSubview_(sh)
            count_lbl = _lbl(
                f"{len(grp)}",
                _round(10, AppKit.NSFontWeightMedium), W32(),
                AppKit.NSTextAlignmentRight,
            )
            count_lbl.setFrame_(AppKit.NSMakeRect(PANEL_W - 80, y + 6, 44, 16))
            doc.addSubview_(count_lbl)
            for res in grp:
                y -= ROW_H_TIMELINE
                row = _make_row(
                    res, PANEL_W,
                    detail_fn=self._show_detail,
                    delete_fn=self._delete_memory,
                    flash_fn=self._flash_status,
                    star_fn=self._toggle_row_star,
                    exclude_fn=self._exclude_app_from_capture,
                    minimal=True, height=ROW_H_TIMELINE,
                )
                row.setFrameOrigin_(AppKit.NSMakePoint(0, y))
                doc.addSubview_(row)
                self._visible_rows.append(row)
        self._focus_idx = -1
        _scroll_to_top(self._scroll, dh2, dh)

    # ── Onboarding finish ─────────────────────────────────────────────────────

    def _refresh_permission_rows(self):
        confirmed = self._store and self._store.get_config("permissions_confirmed", "") == "1"
        ax_ok = True if confirmed else check_accessibility(prompt=False)
        sr_ok = True if confirmed else check_screen_recording(prompt=False)
        states = {
            "accessibility": ax_ok,
            "screen_recording": sr_ok,
        }
        for key, ok in states.items():
            lbl = self._perm_labels.get(key)
            btn = self._perm_btns.get(key)
            if lbl:
                lbl.setStringValue_("Allowed" if ok else "Needed")
                if ok:
                    lbl.setTextColor_(ACCENT_MINT())
                else:
                    lbl.setTextColor_(
                        _c(194, 65, 12, 0.92) if not _is_dark() else _c(251, 146, 60, 0.9))
            if btn:
                btn.setTitle_("Done" if ok else "Open")
        if self._perm_msg and ax_ok and sr_ok:
            self._perm_msg.setStringValue_("")
        return ax_ok and sr_ok

    def _request_accessibility(self):
        ok = check_accessibility(prompt=True)
        if not ok:
            open_accessibility_settings()
        self._refresh_permission_rows()
        threading.Timer(1.0, lambda: AppHelper.callAfter(self._refresh_permission_rows)).start()

    def _request_screen_recording(self):
        ok = check_screen_recording(prompt=True)
        if not ok:
            open_screen_recording_settings()
        self._refresh_permission_rows()
        threading.Timer(1.0, lambda: AppHelper.callAfter(self._refresh_permission_rows)).start()

    def _finish_ob(self):
        name = (str(self._nf.stringValue()) if self._nf else "").strip()
        if not name: self._shake(self._nf); return
        confirmed = self._store.get_config("permissions_confirmed", "") == "1"
        ax_ok = True if confirmed else check_accessibility(prompt=True)
        sr_ok = True if confirmed else check_screen_recording(prompt=True)
        if not (ax_ok and sr_ok):
            self._refresh_permission_rows()
            if self._perm_msg:
                self._perm_msg.setStringValue_("Allow both permissions, then click Begin")
            if not ax_ok:
                open_accessibility_settings()
            if not sr_ok:
                open_screen_recording_settings()
            return
        self._store.set_config("user_name", name)
        self._store.set_config("permissions_confirmed", "1")
        ob, tint = self._ob, self._tint

        def _after_main_build():
            self._sf_field.selectText_(None)
            self._render_search_empty()
            AppHelper.callAfter(self._sync_footer_visibility_with_tour)

        if _prefers_reduced_motion():
            ob.removeFromSuperview()
            self._ob = None
            self._build_main(tint, name)
            self._build_detail(tint)
            _after_main_build()
            return

        def _swap():
            ob.removeFromSuperview()
            self._ob = None
            self._build_main(tint, name)
            self._build_detail(tint)
            self._main.setAlphaValue_(0.0)

            def _fade_in(ctx):
                ctx.setDuration_(0.28)
                ctx.setTimingFunction_(
                    AppKit.CAMediaTimingFunction.functionWithName_("easeOut")
                )
                self._main.animator().setAlphaValue_(1.0)

            def _after_fade():
                _after_main_build()

            AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
                _fade_in, _after_fade)

        def _fade_ob_out(ctx):
            ctx.setDuration_(0.18)
            ob.animator().setAlphaValue_(0.0)

        AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
            _fade_ob_out, _swap)

    def _shake(self, view):
        ox = view.frame().origin.x
        for dx in (8,-8,5,-5,2,-2,0):
            def _shake_step(ctx, d=dx):
                ctx.setDuration_(0.04)
                view.animator().setFrameOrigin_(
                    AppKit.NSMakePoint(ox+d, view.frame().origin.y))

            AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
                _shake_step, None)

    # ── Search — debounced 140ms background thread ────────────────────────────

    def _do_search(self, query: str):
        if self._tab_mode != "search": return
        self._pending = query
        if self._timer: self._timer.cancel()
        if not query.strip():
            self._render_search_empty()
            return
        if self._st_lbl and query:
            self._st_lbl.setStringValue_("Searching…")

        def _run(q):
            try:    results = self._fn(q)
            except: results = []
            AppHelper.callAfter(self._apply, q, results)

        t = threading.Timer(0.12 if query else 0.04, _run, args=(query,))
        t.daemon = True; t.start(); self._timer = t

    def _apply(self, query: str, results: list):
        if query != self._pending: return
        header = "RECENT" if not query else "RESULTS"
        self._render_results(results, header=header)
        if self._st_lbl:
            self._st_lbl.setStringValue_(self._footer_line("search"))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _flash_status(self, msg: str):
        if not self._st_lbl:
            return
        self._st_lbl.setStringValue_(msg)
        # Microanimation: subtle pulse — bright on appearance, soft fade
        # back to the resting tertiary tone. The status label is short
        # enough that the eye picks up the change easily, but the pulse
        # makes "Copied", "Starred", and "Capture paused" feel intentional.
        if not _prefers_reduced_motion():
            try:
                self._st_lbl.setTextColor_(W94())
                def _pulse_status(ctx):
                    ctx.setDuration_(0.6)
                    ctx.setTimingFunction_(
                        AppKit.CAMediaTimingFunction.functionWithName_("easeOut")
                    )
                    self._st_lbl.animator().setTextColor_(W32())

                AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
                    _pulse_status,
                    None,
                )
            except Exception:
                pass
        def _reset():
            AppHelper.callAfter(self._refresh_count_label)
        threading.Timer(1.8, _reset).start()

    # ── Keyboard navigation ──────────────────────────────────────────────────
    @objc.python_method
    def _set_focus_idx(self, new_idx: int):
        rows = list(self._visible_rows or [])
        if not rows:
            self._focus_idx = -1
            return
        # Clamp to valid range
        new_idx = max(0, min(len(rows) - 1, new_idx))
        # Repaint old + new
        if 0 <= self._focus_idx < len(rows):
            rows[self._focus_idx].setFocused_(False)
        rows[new_idx].setFocused_(True)
        self._focus_idx = new_idx
        # Auto-scroll to keep the focused row visible.
        try:
            row = rows[new_idx]
            if self._scroll is not None:
                row.scrollRectToVisible_(row.bounds())
        except Exception:
            pass

    @objc.python_method
    def _nav_focus_next(self):
        if not self._visible_rows:
            return
        nxt = self._focus_idx + 1 if self._focus_idx >= 0 else 0
        self._set_focus_idx(nxt)

    @objc.python_method
    def _nav_focus_prev(self):
        if not self._visible_rows:
            return
        prv = self._focus_idx - 1 if self._focus_idx > 0 else 0
        self._set_focus_idx(prv)

    @objc.python_method
    def _dispatch_shortcut(self, event) -> bool:
        """Route ⌘-shortcuts. Returns True if we handled the event."""
        try:
            mods = event.modifierFlags() & AppKit.NSEventModifierFlagDeviceIndependentFlagsMask
            cmd = bool(mods & AppKit.NSEventModifierFlagCommand)
            chars = (event.charactersIgnoringModifiers() or "").lower()
            kc = int(event.keyCode())
        except Exception:
            return False
        if not cmd:
            return False
        # ⌘← / ⌘→ / ⌘↑ / ⌘↓ — nudge the panel around the screen so the user
        # can park it wherever it doesn't cover what they're working on.
        # Step is intentionally chunky (40px) so it feels deliberate, and
        # we shift+option for finer/coarser control.
        if kc in (123, 124, 125, 126):
            shift = bool(mods & AppKit.NSEventModifierFlagShift)
            opt = bool(mods & AppKit.NSEventModifierFlagOption)
            step = 40.0
            if shift: step = 8.0      # ⌘⇧ + arrow → fine nudge
            if opt:   step = 120.0    # ⌘⌥ + arrow → big jump
            dx = -step if kc == 123 else (step if kc == 124 else 0.0)
            dy = -step if kc == 125 else (step if kc == 126 else 0.0)
            self._nudge_panel(dx, dy)
            return True
        if not chars:
            return False
        # ⌘\ — toggle stealth (hide / show panel for screen capture).
        if chars == "\\":
            self._toggle_stealth()
            return True
        # ⌘P — pause / resume background capture daemon.
        if chars == "p":
            self._toggle_capture_pause()
            return True
        # ⌘K — focus the search field
        if chars == "k":
            self._activate_search_input()
            return True
        # ⌘S — toggle star on focused row (or detail's current memory)
        if chars == "s":
            self._kbd_toggle_star_focused()
            return True
        # ⌘D — delete focused row (or detail's current memory)
        if chars == "d":
            self._kbd_delete_focused()
            return True
        # ⌘⌫ — delete focused row
        if event.keyCode() == 51 and cmd:
            self._kbd_delete_focused()
            return True
        # ⌘1..5 — switch tabs
        if chars in ("1", "2", "3", "4", "5"):
            modes = ["search", "timeline", "starred", "brain", "settings"]
            idx = int(chars) - 1
            if idx < len(modes):
                self._switch_tab(modes[idx])
            return True
        return False

    @objc.python_method
    def _exclude_app_from_capture(self, app_name: str):
        """Add ``app_name`` to the persistent per-app exclusion list. The
        daemon reads this list on each capture cycle (cached for 5s) and
        skips any capture from a matching app — covers clipboard, window,
        screen OCR, and browser streams uniformly. We don't delete the
        existing memories from this app; the user can do that explicitly."""
        if not self._store or not app_name:
            return
        import json as _json
        try:
            raw = self._store.get_config("excluded_apps", "[]") or "[]"
            current = _json.loads(raw)
            if not isinstance(current, list):
                current = []
        except Exception:
            current = []
        if app_name not in current:
            current.append(app_name)
        try:
            self._store.set_config(
                "excluded_apps", _json.dumps(current, ensure_ascii=False),
            )
        except Exception:
            return
        self._flash_status(f"{app_name}  will no longer be captured")

    @objc.python_method
    def _maybe_show_onboarding(self) -> None:
        if not self._store:
            return
        try:
            done = self._store.get_config("onboarded", "0")
        except Exception:
            done = "0"
        if done == "1":
            return
        self.show_onboarding()

    @objc.python_method
    def _tear_down_onboarding_tour_presentation(self) -> None:
        """Remove the shortcut-tour overlay if it is still attached.

        If the user closes the panel (Esc / click-away) while the dimmed
        tour is up, we must drop that full-screen view and un-hide the
        footer; otherwise the next open hits ``show_onboarding``'s early
        return while the footer stays suppressed, and a near-transparent
        tour layer can keep stealing mouse hits from tabs and chips."""
        self._set_onboarding_footer_suppressed(False)
        oc = getattr(self, "_onboard_card", None)
        if oc is None:
            return
        self._onboard_card = None
        try:
            AppKit.NSObject.cancelPreviousPerformRequestsWithTarget_selector_object_(
                oc, b"_onboardingDrop:", None)
        except Exception:
            pass
        try:
            oc.removeFromSuperview()
        except Exception:
            pass
        # Closing the overlay (Esc / ⌥Space) cancels the tour's delayed
        # ``_onboardingDrop_`` callback, so ``onboarded`` would never be
        # persisted — the tour reappears on every open. Treat teardown as
        # completion/skip for first-launch gating (menu can still replay).
        try:
            if self._store:
                self._store.set_config("onboarded", "1")
        except Exception:
            pass

    @objc.python_method
    def _set_onboarding_footer_suppressed(self, suppressed: bool) -> None:
        """Hide footer status + chips + empty-state while the shortcut tour is up
        so nothing bleeds through or collides with the dimmed panel."""
        for attr in ("_st_lbl",):
            v = getattr(self, attr, None)
            if v is not None:
                try:
                    v.setHidden_(suppressed)
                except Exception:
                    pass
        for ch in getattr(self, "_footer_chips", None) or ():
            try:
                ch.setHidden_(suppressed)
            except Exception:
                pass
        el = getattr(self, "_empty_label", None)
        if el is not None:
            try:
                el.setHidden_(suppressed)
            except Exception:
                pass
        if not suppressed:
            try:
                self._refresh_count_label()
            except Exception:
                pass

    @objc.python_method
    def show_onboarding(self) -> None:
        """Mount the onboarding tour over the main panel. Idempotent —
        will not double-mount if a tour is already active."""
        if self._main is None:
            return
        # Don't double-mount a live tour. Drop a stale reference if the view
        # was removed (e.g. panel hid mid-tour) so we can mount again cleanly.
        if getattr(self, "_onboard_card", None) is not None:
            try:
                if self._onboard_card.superview() is not None:
                    return
            except Exception:
                pass
            self._onboard_card = None
        bounds = self._main.bounds()
        card = _OnboardingCard.alloc().initWithFrame_overlay_(bounds, self)

        def _done():
            self._set_onboarding_footer_suppressed(False)
            self._onboard_card = None
            try:
                if self._store:
                    self._store.set_config("onboarded", "1")
            except Exception:
                pass
        card.set_completion_(_done)
        # Mount above all other subviews so the dim wash covers them.
        self._main.addSubview_positioned_relativeTo_(
            card, AppKit.NSWindowAbove, None,
        )
        self._onboard_card = card
        self._set_onboarding_footer_suppressed(True)
        card.show()

    @objc.python_method
    def _build_footer_chips(self, parent_view) -> None:
        """Key-only shortcut chips along the bottom-right.

        Chips show only the key glyph (no label). Hovering shows the action
        description in the left status label; mouse-exit restores the count."""
        gx = 18.0
        right = PANEL_W - gx
        y = (MAIN_FOOTER_H - 17.0) / 2.0  # vertically center 17pt chips
        defs = []
        for glyph, desc, method_name in footer_shortcut_defs():
            cb = getattr(self, method_name) if method_name else None
            defs.append((glyph, desc, cb))
        chips: list[_ShortcutChip] = []
        x = right
        for glyph, desc, cb in reversed(defs):

            def _click_cb(inner=cb):
                if inner is not None:
                    inner()

            def _enter(d=desc):
                if self._st_lbl:
                    self._st_lbl.setStringValue_(d)

            def _exit():
                self._refresh_count_label()

            chip = _make_chip("", glyph, desc, _click_cb)
            chip._hover_cb = _enter
            chip._exit_cb = _exit
            fr = chip.frame()
            cw, ch = fr.size.width, fr.size.height
            x -= cw
            chip.setFrame_(AppKit.NSMakeRect(x, y, cw, ch))
            x -= 6
            parent_view.addSubview_(chip)
            chips.append(chip)
        self._footer_chips = chips

    @objc.python_method
    def _hide_panel(self) -> None:
        try:
            self.hide()
        except Exception:
            pass

    @objc.python_method
    def _is_capture_paused(self) -> bool:
        if not self._store:
            return False
        try:
            return self._store.get_config("capture_paused", "0") == "1"
        except Exception:
            return False

    @objc.python_method
    def _toggle_capture_pause(self):
        """Toggle the persistent capture-paused flag. The daemon polls
        this config key on every capture cycle (re-read every 5s) and
        short-circuits the entire pipeline when paused."""
        if not self._store:
            return
        now_paused = self._is_capture_paused()
        new = "0" if now_paused else "1"
        try:
            self._store.set_config("capture_paused", new)
        except Exception:
            return
        self._on_capture_pause_changed(new == "1")

    @objc.python_method
    def _on_capture_pause_changed(self, paused: bool):
        if self._st_lbl is not None:
            self._flash_status(
                "Capture paused  Run with ⌘P to resume"
                if paused else
                "Capture live  Capturing again"
            )

    @objc.python_method
    def _is_lite_mode(self) -> bool:
        if not self._store:
            return False
        try:
            return self._store.get_config("lite_mode", "0") == "1"
        except Exception:
            return False

    @objc.python_method
    def _toggle_lite_mode(self):
        if not self._store:
            return
        now_lite = self._is_lite_mode()
        new = "0" if now_lite else "1"
        try:
            self._store.set_config("lite_mode", new)
        except Exception:
            return
        self._on_lite_mode_changed(new == "1")

    @objc.python_method
    def _on_lite_mode_changed(self, enabled: bool):
        if self._st_lbl is not None:
            self._flash_status(
                "Lite mode on  Lower battery impact"
                if enabled else
                "Lite mode off  Full capture restored"
            )

    @objc.python_method
    def _copy_week_share(self):
        """Build a polished weekly share image, copy it to the clipboard,
        and save a PNG to ~/Pictures so the user can drag, post, or attach
        it without re-running anything."""
        if not self._store:
            return
        now = time.time()
        week_start = now - (7 * 86400)
        try:
            rows = self._store.get_memories_in_range(week_start, now, limit=1200)
        except Exception:
            rows = self._store.get_all_by_date(limit=400)
        rows = [r for r in (rows or []) if not int(r.get("is_sensitive") or 0)]
        if not rows:
            self._flash_status("No memories this week yet")
            return

        try:
            from .share_card import build_week_share_card, default_share_path

            image, png_bytes, text, _ = build_week_share_card(rows)
        except Exception as exc:
            self._flash_status(f"Share card failed: {exc}")
            return

        saved_path = ""
        try:
            path = default_share_path()
            if png_bytes:
                path.write_bytes(png_bytes)
                saved_path = str(path)
        except Exception:
            saved_path = ""

        try:
            pb = AppKit.NSPasteboard.generalPasteboard()
            pb.clearContents()
            wrote_image = False
            if image is not None:
                try:
                    pb.writeObjects_([image])
                    wrote_image = True
                except Exception:
                    wrote_image = False
            pb.setString_forType_(text, AppKit.NSPasteboardTypeString)
            label = (
                "Copied image + text. Saved to Pictures."
                if wrote_image and saved_path else
                "Copied weekly share card"
            )
            self._flash_status(label)
        except Exception:
            self._flash_status("Could not copy share card")

        if saved_path:
            try:
                AppKit.NSWorkspace.sharedWorkspace().selectFile_inFileViewerRootedAtPath_(
                    saved_path, ""
                )
            except Exception:
                pass

    @objc.python_method
    def _apply_stealth_to_panel(self, panel):
        """Apply the current stealth setting to a panel. NSWindowSharingNone
        excludes the window from every modern macOS screen-capture path
        (ScreenCaptureKit, AVFoundation, screen sharing, AirPlay), which
        is what Zoom/Chrome/Teams/QuickTime/macOS share use today.

        We also flip the collection behavior so the panel doesn't tag along
        into Mission Control snapshots when stealth is on.
        """
        if panel is None:
            return
        try:
            mode = (AppKit.NSWindowSharingNone
                    if self._stealth_on else AppKit.NSWindowSharingReadOnly)
            panel.setSharingType_(mode)
        except Exception:
            pass
        try:
            beh = panel.collectionBehavior()
            if self._stealth_on:
                beh = beh | AppKit.NSWindowCollectionBehaviorTransient
            else:
                beh = beh & ~AppKit.NSWindowCollectionBehaviorTransient
            panel.setCollectionBehavior_(beh)
        except Exception:
            pass

    @objc.python_method
    def _toggle_stealth(self):
        """Flip stealth on/off, persist the choice, and reapply to the
        live panel so the change takes effect immediately."""
        self._stealth_on = not self._stealth_on
        try:
            if self._store is not None:
                self._store.set_config(
                    "stealth_capture", "1" if self._stealth_on else "0",
                )
        except Exception:
            pass
        if self._panel is not None:
            self._apply_stealth_to_panel(self._panel)
        if self._st_lbl is not None:
            self._flash_status(
                "Stealth ON  Hidden from screen capture"
                if self._stealth_on else
                "Stealth OFF  Visible to screen capture"
            )

    @objc.python_method
    def _nudge_panel(self, dx: float, dy: float):
        """Move the floating panel by (dx, dy), clamped to the visible
        screen frame so we never lose it behind the menu bar or off-edge.
        Animated when motion is allowed; instant otherwise."""
        if self._panel is None:
            return
        try:
            frame = self._panel.frame()
            scr = self._panel.screen() or AppKit.NSScreen.mainScreen()
            visible = scr.visibleFrame() if scr else None
        except Exception:
            return
        new_x = float(frame.origin.x) + float(dx)
        new_y = float(frame.origin.y) + float(dy)
        if visible is not None:
            min_x = float(visible.origin.x)
            min_y = float(visible.origin.y)
            max_x = min_x + float(visible.size.width) - float(frame.size.width)
            max_y = min_y + float(visible.size.height) - float(frame.size.height)
            new_x = max(min_x, min(max_x, new_x))
            new_y = max(min_y, min(max_y, new_y))
        target = AppKit.NSMakePoint(new_x, new_y)
        if _prefers_reduced_motion():
            self._panel.setFrameOrigin_(target)
            return
        try:
            def _nudge_panel(ctx):
                ctx.setDuration_(0.12)
                ctx.setTimingFunction_(AppKit.CAMediaTimingFunction.functionWithName_(
                    AppKit.kCAMediaTimingFunctionEaseOut))
                self._panel.animator().setFrameOrigin_(target)

            AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
                _nudge_panel,
                None,
            )
        except Exception:
            self._panel.setFrameOrigin_(target)

    @objc.python_method
    def _kbd_focused_mid(self) -> int | None:
        # Prefer the open detail's memory, otherwise the highlighted row.
        if self._current_detail_result:
            return self._current_detail_result.get("id")
        if self._visible_rows and 0 <= self._focus_idx < len(self._visible_rows):
            return getattr(self._visible_rows[self._focus_idx], "_mid", None)
        return None

    @objc.python_method
    def _kbd_toggle_star_focused(self):
        mid = self._kbd_focused_mid()
        if mid is None:
            return
        if self._current_detail_result and self._current_detail_result.get("id") == mid:
            self._toggle_star()
            return
        if self._visible_rows and 0 <= self._focus_idx < len(self._visible_rows):
            row = self._visible_rows[self._focus_idx]
            self._toggle_row_star(mid, row)

    @objc.python_method
    def _kbd_delete_focused(self):
        mid = self._kbd_focused_mid()
        if mid is None:
            return
        if self._current_detail_result and self._current_detail_result.get("id") == mid:
            self._detail_delete()
        else:
            self._delete_memory(mid)

    @objc.python_method
    def _activate_focused_row(self):
        if (self._visible_rows and 0 <= self._focus_idx < len(self._visible_rows)):
            row = self._visible_rows[self._focus_idx]
            mid = getattr(row, "_mid", None)
            if mid:
                self._show_detail(mid)
                return
        # No focused row: re-run the search if there's text.
        if self._sf_field:
            q = str(self._sf_field.stringValue()).strip()
            if q:
                self._do_search(q)

    def _delete_memory(self, mid: int):
        self._delete_log(f"delete_memory: enter mid={mid!r}")
        if not self._store:
            self._delete_log("delete_memory: no store")
            return
        try:
            mid = int(mid)
        except Exception as exc:
            self._delete_log(f"delete_memory: bad id {mid!r} ({exc})")
            self._flash_status(f"Delete: bad id {mid!r}")
            return

        # 1) Optimistic UI yank — make the row vanish immediately.
        self._yank_row_from_view(mid)

        # 2) Hard delete + tombstone in SQLite.
        try:
            removed = bool(self._store.delete_memory(mid))
        except Exception as exc:
            self._delete_log(f"delete_memory: store.delete_memory raised {exc!r}")
            self._flash_status(f"Delete failed: {exc}")
            return
        self._delete_log(f"delete_memory: store.delete_memory removed={removed}")

        # 3) Evict the in-memory vector cache.
        if self._cache is not None:
            try:
                evicted = self._cache.remove(mid)
                self._delete_log(f"delete_memory: cache.remove={evicted}")
            except Exception as exc:
                self._delete_log(f"delete_memory: cache.remove raised {exc!r}")

        # 4) Close detail view if it was showing this memory.
        if (self._current_detail_result
                and self._current_detail_result.get("id") == mid
                and self._detail_view):
            try:
                self._hide_detail()
            except Exception:
                pass

        # 5) Full refresh of the active tab.
        try:
            self._switch_tab(getattr(self, "_tab_mode", "search"))
        except Exception as exc:
            self._delete_log(f"delete_memory: switch_tab raised {exc!r}")

        self._flash_status("Memory deleted" if removed else "Already gone")
        self._delete_log("delete_memory: done")

    @objc.python_method
    def _compose_detail_body(self, row: dict, full_text: str, heading: str) -> str:
        """Build the detail-panel body. Bullets (cached in ``narrative``) are
        the primary surface; while they generate we show a clear placeholder.
        Never dumps raw OCR — that's accessible via the Summarize toggle.
        """
        import json as _json
        narrative = (row.get("narrative") or "").strip()

        if narrative and narrative.lower() == heading.lower():
            narrative = ""
        # If the first bullet just repeats the heading, drop it.
        if narrative and heading:
            h_norm = re.sub(r"[^a-z0-9 ]+", " ", heading.lower()).strip()
            out_lines: list[str] = []
            for ln in narrative.splitlines():
                s = ln.strip()
                if not s:
                    out_lines.append(ln)
                    continue
                if s.startswith(("•", "-")):
                    b = re.sub(r"[^a-z0-9 ]+", " ", s.lstrip("•- ").lower()).strip()
                    if b and (b == h_norm or h_norm in b or b in h_norm):
                        continue
                out_lines.append(ln)
            narrative = "\n".join(out_lines).strip()

        # ── Structured metadata header ────────────────────────────────────────
        meta_lines: list[str] = []
        app_name = (row.get("app_name") or "").strip()
        activity = (row.get("activity") or "").strip()
        window_title = (row.get("window_title") or "").strip()
        source = (row.get("source") or "").strip()
        ts = float(row.get("created_at") or 0.0)
        summary_r = (row.get("summary") or "").strip()

        if app_name:
            meta_lines.append(f"App       {app_name}")
        if activity and activity.lower() not in (app_name.lower(), "screen", ""):
            meta_lines.append(f"Activity  {activity}")
        if window_title and len(window_title) > 6:
            wt = window_title
            for sep in (" - ", " — ", " | ", " · "):
                if app_name and wt.lower().endswith((sep + app_name).lower()):
                    wt = wt[:-(len(sep) + len(app_name))].strip()
                    break
            if wt and wt.lower() not in (app_name.lower(), heading.lower()):
                meta_lines.append(f"Window    {wt[:80]}")
        if ts:
            meta_lines.append(f"Captured  {time.strftime('%a %b %d  %H:%M', time.localtime(ts))}")
        if source and source not in ("screen", "clipboard", ""):
            meta_lines.append(f"Source    {source}")

        # ── Entities (topic, people, places) ─────────────────────────────────
        facts_text = ""
        ents_raw = (row.get("entities") or "").strip()
        if ents_raw:
            try:
                ents = _json.loads(ents_raw)
            except Exception:
                ents = {}
            if isinstance(ents, dict):
                fact_rows: list[str] = []
                topic = (ents.get("topic") or "").strip()
                if topic and topic.lower() not in heading.lower():
                    fact_rows.append(f"Topic     {topic}")
                who = ents.get("who") or []
                if isinstance(who, list) and who:
                    who_str = ", ".join(str(x).strip() for x in who if str(x).strip())
                    if who_str:
                        fact_rows.append(f"People    {who_str}")
                where = (ents.get("where") or "").strip()
                if where:
                    fact_rows.append(f"Where     {where}")
                if fact_rows:
                    facts_text = "\n".join(fact_rows)

        parts: list[str] = []
        char_count = len(full_text.strip()) if full_text else 0

        # ── AI summary hint (if available and not already in narrative) ───────
        if summary_r and (not narrative or summary_r.lower() not in narrative.lower()):
            if summary_r.lower() != heading.lower():
                parts.append(f"• {summary_r}")

        if narrative:
            parts.append(narrative)
        elif char_count >= 40:
            model_ready = False
            try:
                from ..ai.llm import _ready as _ai_ready
                model_ready = _ai_ready.is_set()
            except Exception:
                pass
            if model_ready:
                parts.append(
                    "Crafting a focused recap now.\n\n"
                    f"• Reading {char_count:,} captured characters with the local model.\n"
                    "• Building meaningful bullets from what you actually did.\n"
                    "• Saving this recap so the next open is instant."
                )
            else:
                parts.append(
                    "The local model is still loading.\n\n"
                    f"• This capture has {char_count:,} characters ready to summarize.\n"
                    "• Once the model is ready, tap Regenerate for a polished bullet recap."
                )
        elif char_count > 0:
            parts.append(
                f"Short capture ({char_count} chars). Not enough text for a rich bullet recap yet."
            )
        else:
            parts.append("This moment was captured but contains no readable text.")

        if facts_text:
            parts.append(facts_text)

        # ── Metadata footer ───────────────────────────────────────────────────
        if meta_lines:
            meta_bullets = []
            for ln in meta_lines:
                meta_bullets.append("• " + re.sub(r"\s{2,}", ": ", ln, count=1))
            parts.append("Context\n" + "\n".join(meta_bullets))

        return "\n\n".join(parts) if parts else heading

    @objc.python_method
    def _delete_log(self, msg: str):
        """Append a timestamped line to data/delete.log. Best-effort; never raise."""
        try:
            import time as _t
            from pathlib import Path as _P
            log = _P(self._data_dir or ".") / "delete.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a") as fh:
                fh.write(f"{_t.strftime('%H:%M:%S')} {msg}\n")
        except Exception:
            pass

    @objc.python_method
    def _yank_row_from_view(self, mid: int):
        """Remove any visible _Row whose ``_mid`` matches ``mid`` from the
        scroll document immediately. Keeps subsequent rows in place; the
        downstream ``_switch_tab`` does a full re-layout, so leftover gaps
        are temporary."""
        try:
            doc = getattr(self, "_doc", None)
            if doc is None:
                return
            for sv in list(doc.subviews()):
                row_mid = getattr(sv, "_mid", None)
                if row_mid is not None and int(row_mid) == int(mid):
                    sv.removeFromSuperview()
            # Drop it from the keyboard-focus index too.
            self._visible_rows = [
                r for r in getattr(self, "_visible_rows", [])
                if int(getattr(r, "_mid", -1) or -1) != int(mid)
            ]
            if self._focus_idx >= len(self._visible_rows):
                self._focus_idx = max(0, len(self._visible_rows) - 1)
        except Exception:
            pass

    def _refresh_greeting(self):
        if not self._g_lbl:
            return
        self._g_lbl.setStringValue_(_psychology_fact())
