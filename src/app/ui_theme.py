"""Theme layer for the Corenous overlay: palette, design tokens, theme state,
fonts, and SF Symbol helpers. Split out of overlay.py unchanged."""
from __future__ import annotations

import objc
import AppKit
from Foundation import NSUserDefaults

from .ui_constants import CORNER

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


def _reduce_motion() -> bool:
    """True when the user has enabled Reduce Motion (System Settings >
    Accessibility > Display). Single source of truth for every animation gate
    in this module (WCAG 2.3.3). Prefers the canonical NSWorkspace API and
    falls back to the global defaults key only when the API is unavailable."""
    try:
        ws = AppKit.NSWorkspace.sharedWorkspace()
        if hasattr(ws, "accessibilityDisplayShouldReduceMotion"):
            return bool(ws.accessibilityDisplayShouldReduceMotion())
    except Exception:
        pass
    try:
        dom = NSUserDefaults.standardUserDefaults().persistentDomainForName_("NSGlobalDomain")
        if isinstance(dom, dict):
            v = dom.get("AppleReduceMotionEnabled")
            if v is not None:
                return bool(v)
    except Exception:
        pass
    return False


def _anim_dur(d: float) -> float:
    """Animation duration that collapses to 0 (instant) under Reduce Motion."""
    return 0.0 if _reduce_motion() else d


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


def _futura(size, weight=None):
    """Geometric display face for headings and section kickers — the editorial
    signature of the minimal redesign."""
    heavy = weight is not None and weight >= AppKit.NSFontWeightSemibold
    f = AppKit.NSFont.fontWithName_size_("Futura-Bold" if heavy else "Futura-Medium", size)
    if f:
        return f
    return _avenir(size, weight)


def _avenir(size, weight=None):
    """Humanist-geometric sans for body and UI chrome — the minimal workhorse."""
    name = "AvenirNext-Regular"
    if weight is not None:
        if weight <= AppKit.NSFontWeightUltraLight:
            name = "AvenirNext-UltraLight"
        elif weight < AppKit.NSFontWeightMedium:
            name = "AvenirNext-Regular"
        elif weight < AppKit.NSFontWeightSemibold:
            name = "AvenirNext-Medium"
        elif weight < AppKit.NSFontWeightBold:
            name = "AvenirNext-DemiBold"
        else:
            name = "AvenirNext-Bold"
    f = AppKit.NSFont.fontWithName_size_(name, size)
    if f:
        return f
    return _sf(size, weight)


def _tabular(font):
    """Same font with monospaced (tabular) digits so in-place count updates
    don't jitter the surrounding text. Falls back to the input font."""
    try:
        d = font.fontDescriptor().fontDescriptorByAddingAttributes_({
            AppKit.NSFontFeatureSettingsAttribute: [{
                AppKit.NSFontFeatureTypeIdentifierKey: 6,      # kNumberSpacingType
                AppKit.NSFontFeatureSelectorIdentifierKey: 0,  # kMonospacedNumbersSelector
            }],
        })
        out = AppKit.NSFont.fontWithDescriptor_size_(d, font.pointSize())
        if out:
            return out
    except Exception:
        pass
    return font


ROW_META_FONT = _avenir(11)
ROW_TITLE_FONT = _avenir(14, AppKit.NSFontWeightSemibold)
ROW_SUBJECT_FONT = _avenir(12)
ROW_TAG_FONT = _avenir(9, AppKit.NSFontWeightSemibold)
ROW_ACTIVITY_FONT = _avenir(9)
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


