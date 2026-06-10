"""Widget layer for the Corenous overlay: ObjC view subclasses, rows, chips,
inputs, the onboarding card, and measurement/format helpers. Split out of
overlay.py unchanged."""
from __future__ import annotations

import random
import re
import threading
import time
from datetime import date

import objc
import AppKit
from Foundation import NSInsetRect
from PyObjCTools import AppHelper

from .overlay_content import onboarding_pages
from .overlay_text import (
    catchy_title as _catchy_title,
    clean_subject_display as _clean_subject_display,
    clip_timeline_words as _clip_timeline_words,
    context_line as _context_line,
    subject as _subject,
    trim_redundant_subject as _trim_redundant_subject,
)
from .ui_constants import ROW_H
from .ui_theme import (
    ACCENT_MINT,
    ACCENT_MINT_DIM,
    DANGER,
    HOVER,
    HOVER_EDGE,
    ROW_STAR_FONT,
    ROW_SUBJECT_FONT,
    ROW_TITLE_FONT,
    SEP,
    SRC_SLATE,
    STAR_COL,
    W14,
    W32,
    W60,
    W94,
    _T,
    _anim_dur,
    _avenir,
    _c,
    _draw_sf_symbol,
    _is_dark,
    _round,
    _sf,
    _src_col,
)
from ..memory.summaries import (
    clean_text,
    memory_title,
    short_subject,
    truncate_text,
)

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
        self.setAccessibilityRole_(AppKit.NSAccessibilityButtonRole)
        self.setAccessibilityLabel_(title)
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
        self.setAccessibilityRole_(AppKit.NSAccessibilityButtonRole)
        self.setAccessibilityLabel_(title)
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
        attrs = {AppKit.NSFontAttributeName: _avenir(11, wt),
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
        self.setAccessibilityRole_(AppKit.NSAccessibilityButtonRole)
        self.setAccessibilityLabel_(title)
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

    def setTitle_(self, t):
        self._title = t
        self.setAccessibilityLabel_(t)
        self.setNeedsDisplay_(True)

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
        a = {AppKit.NSFontAttributeName: _avenir(12, AppKit.NSFontWeightMedium),
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
        self.setAccessibilityRole_(AppKit.NSAccessibilityButtonRole)
        self.setAccessibilityLabel_("Unstar memory" if self._starred else "Star memory")
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
        self.setAccessibilityLabel_("Unstar memory" if self._starred else "Star memory")
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
        # Quiet source dot on every row — no walls, no rails, no separators.
        if self._acc:
            self._acc.setFill()
            dy = (hgt - 6.0) / 2.0
            AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                AppKit.NSMakeRect(20.0, dy, 6.0, 6.0)).fill()

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
            AppKit.NSFontAttributeName: _avenir(size)}
    tf = _FocusTextField.alloc().initWithFrame_(
        AppKit.NSMakeRect(lpad, (h-size-4)/2, w-lpad-14, size+6))
    tf.setFont_(_avenir(size)); tf.setTextColor_(_T("input_text"))
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


class _HoverZone(AppKit.NSView):
    """Invisible strip that reveals footer chrome only while hovered.

    Owns a tracking area over its bounds and calls a Python callback with
    True on mouse-enter / False on mouse-exit. Click-through (``hitTest_``
    returns nil) so the shortcut chips beneath stay clickable."""

    _on_hover = objc.ivar()

    def initWithFrame_onHover_(self, frame, on_hover):
        self = objc.super(_HoverZone, self).initWithFrame_(frame)
        if self is None:
            return None
        self._on_hover = on_hover
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
                self, None,
            )
        )

    def updateTrackingAreas(self):
        self._track()

    def hitTest_(self, _pt):
        return None  # click-through: chips beneath stay interactive

    def mouseEntered_(self, _):
        cb = self._on_hover
        if cb is not None:
            try:
                cb(True)
            except Exception:
                pass

    def mouseExited_(self, _):
        cb = self._on_hover
        if cb is not None:
            try:
                cb(False)
            except Exception:
                pass


class _DetailLinkDelegate(AppKit.NSObject):
    """NSTextView delegate that turns ``corenous-memory:<id>`` links in the
    detail body (the Related Memories list) into navigation. On click it pulls
    the id back out of the URL and hands it to a Python callback."""

    _on_open = objc.ivar()

    def initWithCallback_(self, on_open):
        self = objc.super(_DetailLinkDelegate, self).init()
        if self is None:
            return None
        self._on_open = on_open
        return self

    def textView_clickedOnLink_atIndex_(self, _tv, link, _idx):
        try:
            s = link.absoluteString() if hasattr(link, "absoluteString") else str(link)
            if s and s.startswith("corenous-memory:"):
                cb = self._on_open
                if cb is not None:
                    cb(int(s.split(":", 1)[1]))
                return True
        except Exception:
            pass
        return False


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
        AppKit.NSAnimationContext.currentContext().setDuration_(_anim_dur(0.32))
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
        AppKit.NSAnimationContext.currentContext().setDuration_(_anim_dur(0.24))
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
            _round(20, AppKit.NSFontWeightSemibold),
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
        if r._title != catchy:           # show full title on hover when clipped
            r.setToolTip_(catchy)
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
    # Reveal the full title/subject on hover when either was clipped (never raw text).
    if r._title != catchy or (s_clean and r._subject != s_clean):
        tip = catchy
        if s_clean:
            tip += "\n" + s_clean
        r.setToolTip_(tip)
    return r


