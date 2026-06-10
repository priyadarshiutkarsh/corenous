"""
Corenous overlay: command palette, timeline, starred, agent, settings.
Design: midnight ink + teal–copper accent (distinct from generic SaaS purple).

The theme layer (palette, fonts, symbols) lives in ui_theme.py and the widget
layer (view subclasses, rows, chips, onboarding) in ui_widgets.py; this module
keeps the SearchOverlay controller itself.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable

import objc
import AppKit
from PyObjCTools import AppHelper

from .overlay_content import footer_shortcut_defs
from .overlay_text import catchy_title as _catchy_title
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
from .ui_theme import (
    ACCENT_MINT,
    ACCENT_MINT_DIM,
    DANGER,
    GOLD,
    SRC_VIOLET,
    STAR_COL,
    W32,
    W60,
    W94,
    _PanelBg,
    _T,
    _anim_dur,
    _avenir,
    _c,
    _didot,
    _futura,
    _is_dark,
    _reduce_motion,
    _round,
    _set_theme,
    _sf,
    _sym,
    _tabular,
)
from .ui_widgets import (
    _ActionBtn,
    _DetailLinkDelegate,
    _FieldDelegate,
    _GoldBtn,
    _HoverZone,
    _InputBg,
    _MintHairline,
    _OnboardingCard,
    _OverlayPanel,
    _ResultsScrollView,
    _ShortcutChip,
    _SignupHeroCard,
    _TabBtn,
    _ThemeToggle,
    _WinDelegate,
    _card,
    _date_header,
    _input,
    _kern_lbl,
    _lbl,
    _make_chip,
    _make_row,
    _measure_wrapped_text_height,
    _psychology_fact,
    _rel,
    _scroll_to_top,
)
from ..memory.summaries import memory_title, truncate_text
from ..monitor.permissions import (
    all_required_permissions,
    check_accessibility,
    check_screen_recording,
    open_accessibility_settings,
    open_screen_recording_settings,
)

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
        # Polls the DB while a just-captured memory's detail is open, so its
        # daemon-generated summary appears without the user reopening the page.
        self._summary_poll_timer = None
        self._summary_poll_mid = None
        self._summary_poll_ticks = 0
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
        if _reduce_motion():
            self._panel.setAlphaValue_(1.0)
        else:
            self._panel.setAlphaValue_(0.0)
            def _fade_panel_in(ctx):
                ctx.setDuration_(_anim_dur(0.16))
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
            if _reduce_motion():
                self._panel.orderOut_(None)
            else:
                def _fade_panel_out(ctx):
                    ctx.setDuration_(_anim_dur(0.11))
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

    @objc.python_method
    def _on_footer_hover(self, entered: bool):
        self._footer_hovered = bool(entered)
        self._reveal_footer(entered)

    @objc.python_method
    def _reveal_footer(self, shown: bool):
        """Fade the memory count + shortcut chips in (hover) or out (rest)."""
        targets = [self._st_lbl, *(getattr(self, "_footer_chips", None) or ())]
        alpha = 1.0 if shown else 0.0
        if _reduce_motion():
            for v in targets:
                if v is not None:
                    v.setAlphaValue_(alpha)
            return

        def _fade(ctx):
            ctx.setDuration_(_anim_dur(0.16))
            for v in targets:
                if v is not None:
                    v.animator().setAlphaValue_(alpha)

        AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(_fade, None)

    @objc.python_method
    def _flash_status(self, text: str, hold: float = 3.0):
        """Show a transient message in the footer status label and reveal the
        footer briefly, even when the pointer is not over the bottom strip.

        The footer status label rests at alpha 0 until hovered, so handlers
        that report a result (e.g. Settings actions) must flash it or the user
        sees no feedback. Auto-hides after `hold` seconds unless the pointer is
        still on the footer."""
        if not self._st_lbl:
            return
        self._st_lbl.setStringValue_(text)
        self._reveal_footer(True)
        prev = getattr(self, "_flash_timer", None)
        if prev is not None:
            try:
                prev.cancel()
            except Exception:
                pass

        def _hide():
            if not getattr(self, "_footer_hovered", False):
                self._reveal_footer(False)

        t = threading.Timer(hold, lambda: AppHelper.callAfter(_hide))
        t.daemon = True
        t.start()
        self._flash_timer = t

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
                    _sf(10), W32(), AppKit.NSTextAlignmentCenter)
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
        # Quiet brand voice, not a headline: the quote must never compete with
        # the search field below it for attention.
        g_lbl = _lbl(_psychology_fact(), _didot(13), W60(), AppKit.NSTextAlignmentCenter)
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
        st.setFont_(_tabular(_round(11)))
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

        # Footer chrome (memory count + shortcut chips) stays hidden until the
        # pointer enters the bottom strip — the list reads cleaner without
        # persistent chrome. A click-through hover zone over the strip toggles
        # it; status flashes (_flash_status) reveal it briefly too.
        self._footer_hovered = False
        st.setAlphaValue_(0.0)
        for ch in getattr(self, "_footer_chips", None) or ():
            ch.setAlphaValue_(0.0)
        hz = _HoverZone.alloc().initWithFrame_onHover_(
            AppKit.NSMakeRect(0, 0, PANEL_W, MAIN_FOOTER_H),
            self._on_footer_hover,
        )
        mv.addSubview_positioned_relativeTo_(hz, AppKit.NSWindowAbove, None)
        self._footer_hover_zone = hz

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

        # Top-center eyebrow: provenance at a glance (app · when). The full
        # heading is rendered big inside the scroll body, so this stays small
        # and subordinate rather than duplicating it.
        title_lbl = _lbl("", _avenir(11), W60(), AppKit.NSTextAlignmentCenter)
        title_lbl.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        title_lbl.setFrame_(AppKit.NSMakeRect(96, PANEL_H - 44, PANEL_W - 192, 22))
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
        # Scroll bottom is raised to leave a footer band for the centered
        # dateline byline that sits just above the action bar.
        tv_h = PANEL_H - 52 - 100  # header + dateline band + action bar
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 92, PANEL_W, tv_h))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(AppKit.NSNoBorder)
        scroll.setDrawsBackground_(False)
        scroll.contentView().setDrawsBackground_(False)

        tv = AppKit.NSTextView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, PANEL_W, tv_h))
        tv.setTextContainerInset_(AppKit.NSMakeSize(44, 22))
        tv.setFont_(_avenir(14))
        tv.setTextColor_(W94())
        tv.setBackgroundColor_(AppKit.NSColor.clearColor())
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setRichText_(False)
        # Related-memory links navigate to that memory's detail page. Style
        # them with the accent colour (no default blue underline) and route
        # clicks through a tiny delegate retained on self.
        self._detail_link_delegate = _DetailLinkDelegate.alloc().initWithCallback_(
            self._show_detail)
        tv.setDelegate_(self._detail_link_delegate)
        tv.setLinkTextAttributes_({
            AppKit.NSForegroundColorAttributeName: ACCENT_MINT(),
            AppKit.NSCursorAttributeName: AppKit.NSCursor.pointingHandCursor(),
        })
        scroll.setDocumentView_(tv)
        dv.addSubview_(scroll)
        self._detail_tv    = tv
        self._detail_scroll = scroll

        # No spine, no masthead rule — the reading column is set off by its
        # generous inset and the whitespace beneath the eyebrow alone.

        # Dateline byline — centered in the footer band between the reading
        # column and the action bar (source · date · time).
        dateline = _lbl("", _tabular(_avenir(11)),
                        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
                            0.62, 0.68, 0.74, 1.0),
                        AppKit.NSTextAlignmentCenter)
        dateline.setFrame_(AppKit.NSMakeRect(0, 68, PANEL_W, 18))
        dv.addSubview_(dateline)
        self._detail_dateline_lbl = dateline

        # Action bar rests on the content; no rule. Provenance (app · when)
        # lives in the top eyebrow now, so there is no separate meta row.

        # ── Action buttons (min ~32pt height for interaction comfort) ────────────
        # Bullets are now auto-generated when the detail opens, so the legacy
        # "Summarize" button is gone. Edit and Save share the same slot.
        btn_y = 30; bw = 86; bh = 32; gap = 9
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
        st2 = _lbl("", _avenir(10), W32(), AppKit.NSTextAlignmentCenter)
        st2.setFont_(_tabular(_avenir(10)))
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
        self._detail_heading = heading
        cached_narrative = (row.get("narrative") or "").strip()
        # The daemon stores summaries as prose, not "• " bullets, so a narrative
        # of any form means the summary is ready — only a genuinely empty one is
        # still pending. (Requiring a "•" prefix made every daemon-written
        # summary look unfinished and hang on "Writing your summary".)
        needs_bullets = not cached_narrative
        summary_pending = needs_bullets and len(full.strip()) >= 40
        body = self._compose_detail_body(row, full, heading)
        self._apply_detail_body_text(body, heading=heading,
                                     summary_loading=summary_pending)
        self._detail_tv.setEditable_(False)

        if summary_pending:
            self._auto_generate_bullets(int(mid), row, full, heading)
            self._start_summary_poll(int(mid))
        else:
            self._stop_summary_poll()

        self._detail_title_lbl.setStringValue_(self._detail_eyebrow_text(row))
        self._detail_dateline_lbl.setStringValue_(self._detail_dateline_text(row))

        star_label = "Starred" if starred else "Star"
        self._detail_star_btn.setTitle_(star_label)

        self._detail_st_lbl.setStringValue_("")

        self._detail_edit_btn.setHidden_(False)
        self._detail_save_btn.setHidden_(True)

        # Slide in — quick spring-out for a Mac-feel "swipe-from-right".
        if _reduce_motion():
            self._detail_view.setFrameOrigin_(AppKit.NSMakePoint(0, 0))
            self._main.setFrameOrigin_(AppKit.NSMakePoint(-PANEL_W, 0))
        else:
            spring = AppKit.CAMediaTimingFunction.functionWithControlPoints____(
                0.22, 1.0, 0.36, 1.0)
            def _slide_detail_in(ctx):
                ctx.setDuration_(_anim_dur(0.22))
                ctx.setTimingFunction_(spring)
                self._detail_view.animator().setFrameOrigin_(AppKit.NSMakePoint(0, 0))
                self._main.animator().setFrameOrigin_(AppKit.NSMakePoint(-PANEL_W, 0))

            AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
                _slide_detail_in, None)

    def _hide_detail(self):
        if not self._detail_view: return
        if _reduce_motion():
            self._detail_view.setFrameOrigin_(AppKit.NSMakePoint(PANEL_W, 0))
            self._main.setFrameOrigin_(AppKit.NSMakePoint(0, 0))
        else:
            spring = AppKit.CAMediaTimingFunction.functionWithControlPoints____(
                0.32, 0.0, 0.78, 1.0)
            def _slide_detail_out(ctx):
                ctx.setDuration_(_anim_dur(0.18))
                ctx.setTimingFunction_(spring)
                self._detail_view.animator().setFrameOrigin_(
                    AppKit.NSMakePoint(PANEL_W, 0))
                self._main.animator().setFrameOrigin_(AppKit.NSMakePoint(0, 0))

            AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
                _slide_detail_out, None)
        self._current_detail_result = None
        self._is_editing = False
        self._detail_summary_loading = False
        self._stop_summary_poll()

    # ── Summary auto refresh ──────────────────────────────────────────────────

    @objc.python_method
    def _start_summary_poll(self, mid: int):
        """Begin watching the DB for ``mid``'s daemon-written bullets."""
        self._stop_summary_poll()
        self._summary_poll_mid = int(mid)
        self._summary_poll_ticks = 0
        self._summary_poll_timer = (
            AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                2.0, self, b"_pollSummary:", None, True))

    @objc.python_method
    def _stop_summary_poll(self):
        t = getattr(self, "_summary_poll_timer", None)
        if t is not None:
            try:
                t.invalidate()
            except Exception:
                pass
        self._summary_poll_timer = None
        self._summary_poll_mid = None
        self._summary_poll_ticks = 0

    def _pollSummary_(self, timer):
        cur = self._current_detail_result
        mid = getattr(self, "_summary_poll_mid", None)
        # Bail if the page closed, navigated elsewhere, or the user switched to
        # the raw text / edit views (we must not clobber those).
        if (not cur or mid is None or int(cur.get("id") or -1) != int(mid)
                or getattr(self, "_detail_showing_summary", False)
                or getattr(self, "_is_editing", False) or not self._store):
            self._stop_summary_poll()
            return
        # Give the daemon a bounded window (~2 min); some captures never earn a
        # bullet summary, so don't poll forever.
        self._summary_poll_ticks += 1
        if self._summary_poll_ticks > 60:
            self._stop_summary_poll()
            return
        row = self._store.get_memory_by_id(int(mid))
        if not row:
            self._stop_summary_poll()
            return
        narr = (row.get("narrative") or "").strip()
        if not narr:
            return  # not ready yet; keep waiting
        cur["narrative"] = narr
        cur["summary"] = row.get("summary") or cur.get("summary")
        cur["heading"] = row.get("heading") or cur.get("heading")
        full = cur.get("full_text") or cur.get("text_snippet", "")
        heading = cur.get("heading") or ""
        self._apply_detail_body_text(
            self._compose_detail_body(cur, full, heading),
            heading=heading, summary_loading=False)
        if self._detail_st_lbl:
            self._detail_st_lbl.setStringValue_("")
        self._stop_summary_poll()

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
            self._detail_st_lbl.setStringValue_("")
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
        self._detail_st_lbl.setStringValue_("Recap ready: tap Full text to restore the capture.")
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
                AppKit.NSFontAttributeName: _futura(10, AppKit.NSFontWeightBold),
                AppKit.NSForegroundColorAttributeName: muted,
                AppKit.NSKernAttributeName: 2.6,
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
                AppKit.NSFontAttributeName: _futura(16),
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
                AppKit.NSFontAttributeName: _avenir(13),
                AppKit.NSForegroundColorAttributeName: primary,
                AppKit.NSParagraphStyleAttributeName: bullet_p,
            }
            dot_attrs = dict(bullet_attrs)
            dot_attrs[AppKit.NSForegroundColorAttributeName] = accent
            dot_attrs[AppKit.NSFontAttributeName] = _avenir(
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
    def _apply_detail_body_text(self, body: str, heading: str = "",
                                summary_loading: bool = False) -> None:
        """Render the default detail body with richer context chips.

        When ``summary_loading`` is set the capture's bullets have not been
        written by the daemon yet, so in place of Key Insights we show a calm
        "summary on the way" block. The detail page polls and re-renders on its
        own once the daemon finishes, so the user never has to reopen."""
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
            from ..memory.summaries import is_heading_paraphrase
            kept: list[str] = []
            for ln in narrative:
                t = ln.lstrip("• ").strip()
                t_norm = re.sub(r"[^a-z0-9 ]+", " ", t.lower()).strip()
                if not t_norm:
                    continue
                if t_norm == h or h in t_norm or t_norm in h:
                    continue
                # Catch paraphrases (inserted articles, reordered words)
                # that survive the substring check above.
                if is_heading_paraphrase(t, heading or ""):
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
            row = getattr(self, "_current_detail_result", None) or {}

            def _append(s, attrs):
                ts.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(s, attrs))

            def _kicker(text, first=False):
                kp = AppKit.NSMutableParagraphStyle.alloc().init()
                kp.setParagraphSpacing_(13.0)
                if not first:
                    kp.setParagraphSpacingBefore_(34.0)
                _append(text + "\n", {
                    AppKit.NSFontAttributeName: _futura(10, AppKit.NSFontWeightBold),
                    AppKit.NSForegroundColorAttributeName: accent,
                    AppKit.NSKernAttributeName: 2.6,
                    AppKit.NSParagraphStyleAttributeName: kp,
                })

            h_norm = re.sub(r"[^a-z0-9 ]+", " ", (heading or "").lower()).strip()

            # ── [0] Heading (the identity of the thought) ─────────────────────
            head_txt = (heading or "").replace("\n", " ").strip()
            if head_txt:
                h1p = AppKit.NSMutableParagraphStyle.alloc().init()
                h1p.setParagraphSpacing_(4.0)
                h1p.setLineSpacing_(3.0)
                _append(head_txt + "\n", {
                    AppKit.NSFontAttributeName: _futura(30),
                    AppKit.NSForegroundColorAttributeName: primary,
                    AppKit.NSKernAttributeName: -0.3,
                    AppKit.NSParagraphStyleAttributeName: h1p,
                })

            # ── [1] Summary subhead (only when it adds beyond the heading) ────
            summary_r = (row.get("summary") or "").strip()
            s_norm = re.sub(r"[^a-z0-9 ]+", " ", summary_r.lower()).strip()
            hero = summary_r if (summary_r and s_norm and s_norm != h_norm) else ""
            if hero:
                hp = AppKit.NSMutableParagraphStyle.alloc().init()
                hp.setParagraphSpacing_(2.0)
                hp.setParagraphSpacingBefore_(5.0)
                hp.setLineSpacing_(4.0)
                _append(hero + "\n", {
                    AppKit.NSFontAttributeName: _avenir(15),
                    AppKit.NSForegroundColorAttributeName: muted,
                    AppKit.NSParagraphStyleAttributeName: hp,
                })
                # Don't repeat the one-liner down in the bullets.
                narrative = [
                    ln for ln in narrative
                    if re.sub(r"[^a-z0-9 ]+", " ", ln.lstrip("•- ").lower()).strip() != s_norm
                ]

            # ── [2] Key insights (bullets) ────────────────────────────────────
            bullet_p = AppKit.NSMutableParagraphStyle.alloc().init()
            bullet_p.setFirstLineHeadIndent_(0.0)
            bullet_p.setHeadIndent_(20.0)
            bullet_p.setParagraphSpacing_(13.0)
            bullet_p.setLineSpacing_(6.0)
            bullet_attrs = {
                AppKit.NSFontAttributeName: _avenir(13.5),
                AppKit.NSForegroundColorAttributeName: primary,
                AppKit.NSParagraphStyleAttributeName: bullet_p,
            }
            dot_attrs = dict(bullet_attrs)
            dot_attrs[AppKit.NSForegroundColorAttributeName] = accent
            dot_attrs[AppKit.NSFontAttributeName] = _avenir(13.5, AppKit.NSFontWeightBold)

            insight_lines = []
            for ln in narrative:
                txt = re.sub(r"^[\s•*\-]+", "", ln.strip()).strip()
                txt = re.sub(r"\s+", " ", txt.replace("\t", " ")).strip()
                if txt:
                    insight_lines.append(txt)
            if summary_loading:
                # The bullets live in the daemon and arrive shortly; show a
                # calm placeholder rather than the raw "still loading" prose.
                _kicker("SUMMARY")
                lead_p = AppKit.NSMutableParagraphStyle.alloc().init()
                lead_p.setHeadIndent_(18.0)
                lead_p.setParagraphSpacing_(5.0)
                lead_p.setLineSpacing_(3.0)
                _append("•  ", dot_attrs)
                _append("Writing your summary\n", {
                    AppKit.NSFontAttributeName: _avenir(13, AppKit.NSFontWeightMedium),
                    AppKit.NSForegroundColorAttributeName: primary,
                    AppKit.NSParagraphStyleAttributeName: lead_p,
                })
                hint_p = AppKit.NSMutableParagraphStyle.alloc().init()
                hint_p.setHeadIndent_(18.0)
                hint_p.setFirstLineHeadIndent_(18.0)
                hint_p.setParagraphSpacing_(6.0)
                hint_p.setLineSpacing_(3.0)
                _append(
                    "Reading what you captured and pulling out the key points. "
                    "This fills in here on its own in a moment.\n",
                    {
                        AppKit.NSFontAttributeName: _avenir(11.5),
                        AppKit.NSForegroundColorAttributeName: muted,
                        AppKit.NSParagraphStyleAttributeName: hint_p,
                    },
                )
            elif insight_lines:
                _kicker("KEY INSIGHTS")
                for txt in insight_lines:
                    _append("•  ", dot_attrs)
                    _append(f"{txt}\n", bullet_attrs)

            # ── [3] Related memories (semantic neighbours) ────────────────────
            related = self._related_memories(int(row.get("id") or 0)) if row.get("id") else []
            if related:
                _kicker("RELATED MEMORIES")
                rel_p = AppKit.NSMutableParagraphStyle.alloc().init()
                rel_p.setHeadIndent_(20.0)
                rel_p.setParagraphSpacing_(13.0)
                rel_p.setLineSpacing_(2.0)
                for rm in related:
                    # The whole row (arrow + heading) is a link to that memory's
                    # detail page; the delegate parses the id back out on click.
                    link = f"corenous-memory:{int(rm['id'])}"
                    arrow_attrs = {
                        AppKit.NSFontAttributeName: _avenir(12.5, AppKit.NSFontWeightSemibold),
                        AppKit.NSForegroundColorAttributeName: accent,
                        AppKit.NSParagraphStyleAttributeName: rel_p,
                        AppKit.NSLinkAttributeName: link,
                    }
                    rel_attrs = {
                        AppKit.NSFontAttributeName: _avenir(12.5),
                        AppKit.NSForegroundColorAttributeName: primary,
                        AppKit.NSParagraphStyleAttributeName: rel_p,
                        AppKit.NSLinkAttributeName: link,
                    }
                    _append("→  ", arrow_attrs)
                    rh = rm["heading"].replace("\n", " ").strip()
                    if len(rh) > 56:
                        rh = rh[:56].rstrip() + "…"
                    _append(f"{rh}\n", rel_attrs)

            ts.endEditing()
        except Exception:
            tv.setString_(body)

    @objc.python_method
    def _detail_eyebrow_text(self, row: dict) -> str:
        """Compact provenance line for the detail top bar: app · when."""
        app = (row.get("app_name") or row.get("source") or "").strip()
        when = _rel(float(row.get("created_at") or 0.0))
        parts = [p for p in (app[:32] if app else None, when) if p]
        return "  ·  ".join(parts)

    @objc.python_method
    def _detail_dateline_text(self, row: dict) -> str:
        """Closing byline shown centered at the foot of the detail panel:
        source · date · time."""
        parts: list[str] = []
        src = (row.get("source") or "").strip()
        ts_val = float(row.get("created_at") or 0.0)
        if src:
            parts.append({
                "screen": "Screen reading", "clipboard": "Clipboard",
                "browser": "Browser",
            }.get(src.lower(), src.title()))
        if ts_val:
            lt = time.localtime(ts_val)
            parts.append(time.strftime("%b %d, %Y", lt))
            parts.append(time.strftime("%I:%M %p", lt).lstrip("0"))
        return "  ·  ".join(parts)

    @objc.python_method
    def _related_memories(self, mid: int, limit: int = 4) -> list[dict]:
        """Top semantically nearest memories to ``mid`` via the vector cache.

        Each memory already has a stored compressed vector, so we reuse this
        one's vector as the query and score it against every other cached
        vector — no model needed in this process. Returns
        ``[{id, heading, created_at, score}]`` ordered by similarity, excluding the
        memory itself. Empty when the cache is missing/tiny or the row has no
        stored vector."""
        cache = self._cache
        store = self._store
        if cache is None or store is None or len(cache) < 2:
            return []
        try:
            import numpy as _np

            query_cv = None
            for cid, cv in cache.get_all():
                if int(cid) == int(mid):
                    query_cv = cv
                    break
            if query_cv is None:
                return []
            scores = cache.scores(query_cv)
            ids = cache.memory_ids()
            # Only surface genuinely related neighbours. Scores are approximate
            # cosine over normalized embeddings; below ~0.30 they're topical
            # noise, not the same thread. Since argsort is descending we can
            # stop as soon as we drop under the floor.
            min_score = 0.30
            # Drop repeats: the same heading (e.g. "Browsed GitHub" captured a
            # dozen times) adds no value, and the source memory's own heading
            # repeated elsewhere is just a near-duplicate of what's on screen.
            src_row = store.get_memory_by_id(int(mid)) or {}
            seen_headings: set[str] = set()
            src_h = (src_row.get("heading") or "").strip().lower()
            if src_h:
                seen_headings.add(src_h)
            out: list[dict] = []
            for i in _np.argsort(-scores):
                sc = float(scores[int(i)])
                if sc < min_score:
                    break
                rid = int(ids[int(i)])
                if rid == int(mid):
                    continue
                r = store.get_memory_by_id(rid)
                if not r or int(r.get("is_sensitive") or 0):
                    continue
                h = (r.get("heading") or "").strip() or memory_title(
                    r.get("source", ""), r.get("app_name", ""),
                    r.get("activity", ""), r.get("window_title", ""),
                    r.get("full_text", "") or r.get("text_snippet", ""),
                )
                if not h:
                    continue
                hkey = h.lower()
                if hkey in seen_headings:
                    continue
                seen_headings.add(hkey)
                out.append({
                    "id": rid, "heading": h,
                    "created_at": float(r.get("created_at") or 0.0),
                    "score": sc,
                })
                if len(out) >= limit:
                    break
            return out
        except Exception:
            return []

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
            self._detail_st_lbl.setStringValue_("Writing your summary")

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
        pending = len(full.strip()) >= 40
        self._apply_detail_body_text(self._compose_detail_body(cur, full, heading),
                                     heading=heading, summary_loading=pending)
        if pending:
            self._auto_generate_bullets(mid, cur, full, heading)
            self._start_summary_poll(mid)
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
                    # The model lives in the daemon, not this app process, so it
                    # refines captures in the background. The detail page polls
                    # for the result and fills it in on its own, so we just keep
                    # the calm placeholder up rather than asking for a reopen.
                    self._detail_st_lbl.setStringValue_(
                        "Writing your summary"
                    )
                else:
                    self._detail_st_lbl.setStringValue_(
                        "No summary came back. Try Regenerate"
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
        self._stop_summary_poll()
        if self._detail_st_lbl:
            self._detail_st_lbl.setStringValue_("")

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
            and not _reduce_motion()
        ):
            try:
                self._doc.setWantsLayer_(True)
                self._doc.layer().setOpacity_(0.0)
                def _fade_doc_in(ctx):
                    ctx.setDuration_(_anim_dur(0.22))
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
                    self._detail_heading = heading
                    self._detail_title_lbl.setStringValue_(
                        self._detail_eyebrow_text(fresh))
                    if not getattr(self, "_is_editing", False):
                        self._apply_detail_body_text(
                            self._compose_detail_body(fresh, full, heading),
                            heading=heading)

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
        for _hint in getattr(self, "_empty_hints", []) or []:
            try:
                _hint.removeFromSuperview()
            except Exception:
                pass
        self._empty_hints = []

        # System empty-state line, the way Spotlight/Finder/Mail render it:
        # SF Pro at body size, tertiary label color, no italic, no accent
        # tinting. Adapts automatically to light/dark via NSColor system roles.
        # IMPORTANT: with setAttributedStringValue_, NSTextField ignores
        # setAlignment_; alignment must be baked into a paragraph style in
        # the attributes dict, otherwise text left-aligns inside the frame.
        para = AppKit.NSMutableParagraphStyle.alloc().init()
        para.setAlignment_(AppKit.NSTextAlignmentCenter)
        head_attrs = {
            AppKit.NSFontAttributeName: _avenir(16),
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

        # Teach instead of leaving a blank wall: one line of example queries,
        # one line revealing temporal search. Quieter than the headline so the
        # hierarchy reads headline → hints.
        hints = [
            ("Try “that article about neural nets” or “the pricing page I compared”",
             _avenir(13), AppKit.NSColor.tertiaryLabelColor(), head_y - 32.0),
            ("Time words work: “yesterday”, “last week”, or a weekday filter by when you saw it",
             _avenir(12), AppKit.NSColor.quaternaryLabelColor(), head_y - 54.0),
        ]
        self._empty_hints = []
        for text, font, color, hy in hints:
            h_para = AppKit.NSMutableParagraphStyle.alloc().init()
            h_para.setAlignment_(AppKit.NSTextAlignmentCenter)
            h_attrs = {
                AppKit.NSFontAttributeName: font,
                AppKit.NSForegroundColorAttributeName: color,
                AppKit.NSParagraphStyleAttributeName: h_para,
            }
            h_str = AppKit.NSAttributedString.alloc().initWithString_attributes_(
                text, h_attrs,
            )
            h_tf = AppKit.NSTextField.alloc().initWithFrame_(
                AppKit.NSMakeRect(head_x, hy, head_w, 20.0),
            )
            h_tf.setBezeled_(False)
            h_tf.setDrawsBackground_(False)
            h_tf.setSelectable_(False)
            h_tf.setEditable_(False)
            h_tf.setAlignment_(AppKit.NSTextAlignmentCenter)
            h_tf.setAttributedStringValue_(h_str)
            h_tf.setAutoresizingMask_(AppKit.NSViewNotSizable)
            if self._main is not None and self._scroll is not None:
                self._main.addSubview_positioned_relativeTo_(
                    h_tf, AppKit.NSWindowAbove, self._scroll,
                )
            elif self._main is not None:
                self._main.addSubview_(h_tf)
            else:
                self._doc.addSubview_(h_tf)
            self._empty_hints.append(h_tf)

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

    @objc.python_method
    def _timeline_title(self, result) -> str:
        """The bold line _make_row renders for a minimal timeline row.

        Mirrors the common-case title selection (model heading, else summary)
        so dedup keys match what the user actually sees."""
        heading = (getattr(result, "heading", "") or "").strip()
        summary = (getattr(result, "summary", "") or "").strip()
        full = (getattr(result, "full_text", "") or
                getattr(result, "text_snippet", "") or "")
        if heading and not heading.lower().startswith(
            ("copied in ", "worked in ", "viewed in ", "captured in ")
        ):
            title = heading
        else:
            title = summary or heading
        return _catchy_title(
            title, summary, getattr(result, "app_name", ""), full
        ).strip()

    @objc.python_method
    def _dedupe_timeline(self, results):
        """Collapse true duplicates and keep every repeated title unique.

        Rows that render the same title AND share the same summary are the
        same capture seen twice (e.g. OCR jitter on one page) — only the
        newest is kept. When the title repeats but the summary differs, the
        later row is relabeled with its own summary so no two rows ever read
        the same. The stored memories are never touched; this is display only.
        """
        seen_sig: set[tuple[str, str]] = set()
        seen_title: set[str] = set()
        out = []
        for r in results:
            disp = self._timeline_title(r)
            summ = (getattr(r, "summary", "") or "").strip()
            key = disp.lower()
            sig = (key, summ.lower())
            if sig in seen_sig:
                continue  # same title + same summary -> duplicate capture
            if key in seen_title:
                # Title already shown. Try to surface this row's own summary so
                # it reads distinctly; if the summary just echoes the title
                # (the model couldn't distinguish it either), collapse instead
                # of repeating an identical line.
                if summ and summ.lower() != key:
                    r.heading = summ
                    r.summary = ""
                    disp = self._timeline_title(r)
                    key = disp.lower()
                if key in seen_title:
                    seen_sig.add(sig)
                    continue
            seen_sig.add(sig)
            seen_title.add(key)
            out.append(r)
        return out

    def _load_timeline(self):
        if not self._store:
            return
        rows = self._store.get_all_by_date(limit=200)
        results = [self._result_from_row(r) for r in rows]
        results = self._dedupe_timeline(results)
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
        for _hint in getattr(self, "_empty_hints", []) or []:
            try:
                _hint.removeFromSuperview()
            except Exception:
                pass
        self._empty_hints = []

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
        # Placeholder must read as status, not content — bullet lines here
        # looked like a finished digest while the model was still working.
        summary_display = cached_summary if cached_summary else (
            "Composing your session digest…\n"
            "Reading today's captures on-device. This takes a few seconds."
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

        # ── Threads + Today sections ──────────────────────────────────────────
        # A day with nothing in it gets ONE quiet line, not two caps section
        # headers each followed by an apology.
        if not top_threads and not app_usage:
            y -= 48
            em = _lbl(
                "A quiet canvas so far. Threads and app activity fill in here "
                "as you work.",
                _round(12), W60(), AppKit.NSTextAlignmentLeft,
            )
            em.setFrame_(AppKit.NSMakeRect(pad_x, y, PANEL_W - 2 * pad_x, 20))
            self._doc.addSubview_(em)
        else:
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
            _avenir(13, AppKit.NSFontWeightSemibold), W94(),
            AppKit.NSTextAlignmentLeft,
        )
        try:
            tl.setMaximumNumberOfLines_(1)
            tl.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        except Exception:
            pass
        tl.setFrame_(AppKit.NSMakeRect(14, h - 28, card_w - 170, 18))
        tl.setToolTip_(title)
        card.addSubview_(tl)

        pill = _lbl(
            f"{count} hit{'s' if count != 1 else ''}",
            _avenir(10, AppKit.NSFontWeightMedium), ACCENT_MINT_DIM(),
            AppKit.NSTextAlignmentRight,
        )
        pill.setFrame_(AppKit.NSMakeRect(card_w - 132, h - 28, 118, 16))
        card.addSubview_(pill)

        meta = f"{app_n}  ·  last {_rel(last_ts)}" if app_n else f"last {_rel(last_ts)}"
        ml = _lbl(meta[:80], _avenir(10), W32(), AppKit.NSTextAlignmentLeft)
        ml.setFrame_(AppKit.NSMakeRect(14, 10, card_w - 28, 14))
        card.addSubview_(ml)

        if summary:
            sl = _lbl(summary[:110].replace("\n", " "), _avenir(11), W60(), AppKit.NSTextAlignmentLeft)
            try:
                sl.setMaximumNumberOfLines_(1)
                sl.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            except Exception:
                pass
            sl.setFrame_(AppKit.NSMakeRect(14, 28, card_w - 28, 16))
            sl.setToolTip_(summary)
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
            _avenir(13, AppKit.NSFontWeightSemibold), W94(),
            AppKit.NSTextAlignmentLeft,
        )
        title.setFrame_(AppKit.NSMakeRect(14, h - 26, card_w - 180, 20))
        card.addSubview_(title)

        count_lbl = _lbl(
            f"{n} capture{'s' if n != 1 else ''} today",
            _avenir(10, AppKit.NSFontWeightMedium), ACCENT_MINT_DIM(),
            AppKit.NSTextAlignmentRight,
        )
        count_lbl.setFrame_(AppKit.NSMakeRect(card_w - 180, h - 26, 166, 18))
        card.addSubview_(count_lbl)

        # Topic line — curated English heading or summary
        topic_clean = topic[:100].replace("\n", " ")
        topic_lbl = _lbl(
            topic_clean,
            _avenir(11), W60(),
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
            _avenir(10), W32(), AppKit.NSTextAlignmentLeft,
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
            _avenir(13, AppKit.NSFontWeightSemibold), W94(),
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
            _avenir(10, AppKit.NSFontWeightMedium), ACCENT_MINT_DIM(),
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
                _avenir(11), W60(),
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
            _avenir(10), W32(), AppKit.NSTextAlignmentLeft,
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
                AppKit.NSFontAttributeName: _futura(10, AppKit.NSFontWeightBold),
                AppKit.NSForegroundColorAttributeName: muted,
                AppKit.NSKernAttributeName: 2.6,
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
                AppKit.NSFontAttributeName: _futura(16),
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
                AppKit.NSFontAttributeName: _futura(11, AppKit.NSFontWeightBold),
                AppKit.NSForegroundColorAttributeName: accent,
                AppKit.NSKernAttributeName: 1.4,
                AppKit.NSParagraphStyleAttributeName: section_p,
            }
            body_p = AppKit.NSMutableParagraphStyle.alloc().init()
            body_p.setParagraphSpacing_(10.0)
            body_p.setLineSpacing_(2.6)
            body_attrs = {
                AppKit.NSFontAttributeName: _avenir(13),
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
            dot_attrs[AppKit.NSFontAttributeName] = _avenir(13, AppKit.NSFontWeightBold)

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
            self._flash_status(f"Opened {name}")
        except Exception as exc:
            self._flash_status(f"Could not open {name}: {exc}")

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
        for _hint in getattr(self, "_empty_hints", []) or []:
            try:
                _hint.removeFromSuperview()
            except Exception:
                pass
        self._empty_hints = []

        from ..ai.remote_llm import load_remote_config, RECOMMENDED_MODELS
        from ..ai.llm import _PRESETS as LLM_PRESETS  # type: ignore

        rcfg = load_remote_config()
        provider = (rcfg.get("provider") or "local").lower()
        api_key = (rcfg.get("openrouter_api_key") or "").strip()
        cur_or_model = (rcfg.get("openrouter_model") or "").strip()
        cur_local_preset = ""
        try:
            cur_local_preset = (
                self._store.get_config("local_llm_preset", "") or ""
            ).strip().lower()
        except Exception:
            pass
        if not cur_local_preset:
            cur_local_preset = "qwen2.5-vl-3b"

        dh = self._scroll.frame().size.height
        pad_x = 28.0
        card_w = PANEL_W - 2 * pad_x
        header_h = 84.0   # Futura title + hint + spacing
        row_h = 64.0

        # Card heights = header + N rows + bottom padding (must match row count).
        bottom_pad = 28.0
        if provider == "openrouter":
            h_model = header_h + row_h * 4 + bottom_pad  # provider + key + model + actions
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
        local_btn.setSelected_(local_active)
        card.addSubview_(local_btn)
        or_btn = _ActionBtn.alloc().initWithTitle_frame_tintColor_danger_cb_(
            "OpenRouter",
            AppKit.NSMakeRect(cx + seg_w + 6, cy, seg_w, 30),
            ACCENT_MINT() if not local_active else W60(),
            False,
            lambda: self._settings_set_provider("openrouter"),
        )
        or_btn.setSelected_(not local_active)
        card.addSubview_(or_btn)

        if provider != "openrouter":
            # Row 2: Local preset popup with inline save
            cx, cy, rows_top = self._settings_row(
                card, rows_top, card_w,
                "Local model",
                "Runs the on-device vision model. Stays on your Mac.",
                control_w=260.0, row_h=row_h,
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

            # Row 3: model picker
            cx, cy, rows_top = self._settings_row(
                card, rows_top, card_w,
                "Model",
                "Which OpenRouter model answers. Free tiers are rate limited.",
                control_w=300.0, row_h=row_h,
            )
            model_popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
                AppKit.NSMakeRect(cx, cy, 300, 30), False,
            )
            try:
                model_popup.setBezelStyle_(AppKit.NSBezelStyleRounded)
            except Exception:
                pass
            sel_idx = 0
            for i, (mid, label) in enumerate(RECOMMENDED_MODELS):
                model_popup.addItemWithTitle_(label)
                try:
                    model_popup.lastItem().setRepresentedObject_(mid)
                except Exception:
                    pass
                if mid == cur_or_model:
                    sel_idx = i
            model_popup.selectItemAtIndex_(sel_idx)
            self._settings_or_model_popup = model_popup
            card.addSubview_(model_popup)

            # Row 4: action buttons (save + test)
            cx, cy, rows_top = self._settings_row(
                card, rows_top, card_w,
                "Apply changes",
                "Save your key, then verify the route.",
                control_w=300.0, row_h=row_h,
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
        for label, hint, is_on, cb in capture_rows:
            cx, cy, rows_top = self._settings_row(
                card, rows_top, card_w, label, hint,
                control_w=96.0, row_h=row_h,
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
            control_w=96.0, row_h=row_h,
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
            control_w=300.0, row_h=row_h,
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
            control_w=170.0, row_h=row_h,
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
        """Masthead: Futura nameplate above a status strip of three
        label/value columns, set apart by whitespace alone — no rules."""
        doc = self._doc
        title = _lbl(
            "Settings",
            _futura(28), W94(), AppKit.NSTextAlignmentLeft,
        )
        title.setFrame_(AppKit.NSMakeRect(pad_x, y - 42, card_w, 38))
        doc.addSubview_(title)

        # Status strip — three label/value columns spaced across the width.
        info = {}
        try:
            info = self._gather_settings_stats() or {}
        except Exception:
            info = {}
        cells = [
            ("MODEL", str(info.get("model", "Local"))),
            (
                "CAPTURE",
                "Paused" if self._is_capture_paused() else (
                    "Lite" if self._is_lite_mode() else "Live"
                ),
            ),
            (
                "MEMORIES",
                f"{info.get('n_memories', 0):,}"
                if isinstance(info.get("n_memories", 0), int)
                else "—",
            ),
        ]
        strip_y = y - 104
        seg_w = card_w / 3.0
        for i, (cap, val) in enumerate(cells):
            sx = pad_x + i * seg_w
            cap_lbl = _kern_lbl(
                cap, _futura(9, AppKit.NSFontWeightBold), W32(),
                AppKit.NSMakeRect(sx, strip_y + 24, seg_w - 18, 11),
            )
            doc.addSubview_(cap_lbl)
            val_lbl = _lbl(
                val,
                _avenir(14, AppKit.NSFontWeightSemibold), W94(),
                AppKit.NSTextAlignmentLeft,
            )
            val_lbl.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            val_lbl.setFrame_(AppKit.NSMakeRect(sx, strip_y, seg_w - 18, 20))
            doc.addSubview_(val_lbl)
        return y - hero_h

    @objc.python_method
    def _render_settings_card_header(
        self, card, title: str, hint: str, ch: float, card_w: float,
    ) -> float:
        """Card title (Futura) + hint. Returns the y where rows start; the
        title is set off from the rows by whitespace, not a rule."""
        inset = 24.0
        body_w = card_w - inset * 2
        t = _lbl(
            title, _futura(20), W94(), AppKit.NSTextAlignmentLeft,
        )
        t.setFrame_(AppKit.NSMakeRect(inset, ch - 40, body_w, 28))
        card.addSubview_(t)
        if hint:
            ht = _lbl(
                hint, _avenir(11), W60(), AppKit.NSTextAlignmentLeft,
            )
            try:
                ht.setMaximumNumberOfLines_(2)
                ht.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
            except Exception:
                pass
            ht.setFrame_(AppKit.NSMakeRect(inset, ch - 64, body_w, 18))
            card.addSubview_(ht)
        return ch - 84

    @objc.python_method
    def _settings_row(
        self, card, y: float, card_w: float, label: str, hint: str,
        control_w: float = 220.0, row_h: float = 64.0,
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
            label, _avenir(13, AppKit.NSFontWeightSemibold), W94(),
            AppKit.NSTextAlignmentLeft,
        )
        lbl.setFrame_(AppKit.NSMakeRect(inset, block_y + 20, text_w, 18))
        card.addSubview_(lbl)
        if hint:
            hl = _lbl(
                hint, _avenir(11), W60(), AppKit.NSTextAlignmentLeft,
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
            from ..ai.remote_llm import load_remote_config
            from ..ai import vision

            if (load_remote_config().get("provider") or "local").lower() == "openrouter":
                out["model"] = "OpenRouter"
            elif vision.vision_available():
                out["model"] = model_status_label()
            else:
                out["model"] = "Not installed"
        except Exception:
            out["model"] = "Local"
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
        self._load_settings()
        self._flash_status(f"Provider switched to {cfg['provider']}")

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
        popup = getattr(self, "_settings_or_model_popup", None)
        if popup is not None:
            item = popup.selectedItem()
            mid = str(item.representedObject() or "").strip() if item else ""
            if mid:
                cfg["openrouter_model"] = mid
        if not cfg.get("openrouter_model") and RECOMMENDED_MODELS:
            cfg["openrouter_model"] = RECOMMENDED_MODELS[0][0]
        cfg["provider"] = "openrouter"
        save_remote_config(cfg)
        ok = bool(cfg.get("openrouter_api_key"))
        self._flash_status(
            "Saved. OpenRouter is now your AI provider."
            if ok else "Saved, but API key is empty. Paste it above."
        )

    @objc.python_method
    def _settings_test_openrouter(self):
        from ..ai.remote_llm import openrouter_chat, load_remote_config
        # Persist any unsaved field changes first so the test uses the
        # value the user just typed.
        self._settings_save_openrouter()
        self._flash_status("Testing OpenRouter…", hold=25.0)
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
        if ok:
            self._flash_status("OpenRouter OK. Cloud model is live.")
        else:
            self._flash_status(
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
        self._flash_status(f"Preset saved: {preset}. Restart corenous to apply.")

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
        for _hint in getattr(self, "_empty_hints", []) or []:
            try:
                _hint.removeFromSuperview()
            except Exception:
                pass
        self._empty_hints = []

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
            m1 = _lbl(msg,  _avenir(13, AppKit.NSFontWeightMedium), W60(), AppKit.NSTextAlignmentCenter)
            m1.setFrame_(AppKit.NSMakeRect(40, dh/2-6, PANEL_W-80, 18))
            doc.addSubview_(m1)
            m2 = _lbl(hint, _avenir(11), W32(), AppKit.NSTextAlignmentCenter)
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

            sh = _kern_lbl(label, _futura(10, AppKit.NSFontWeightBold), ACCENT_MINT_DIM(),
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
        for _hint in getattr(self, "_empty_hints", []) or []:
            try:
                _hint.removeFromSuperview()
            except Exception:
                pass
        self._empty_hints = []

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
                _avenir(14, AppKit.NSFontWeightMedium), W60(),
                AppKit.NSTextAlignmentCenter,
            )
            m1.setFrame_(AppKit.NSMakeRect(40, dh / 2 - 14, PANEL_W - 80, 20))
            doc.addSubview_(m1)
            m2 = _lbl(
                "Start Corenous (corenous-ai start). Captures get an "
                "AI headline from your screen and clipboard context.",
                _avenir(11), W32(), AppKit.NSTextAlignmentCenter,
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
                hdr, _futura(10, AppKit.NSFontWeightBold),
                _T("section_lbl"),
                AppKit.NSMakeRect(36, y + 4, min(PANEL_W - 72, 360), 22),
            )
            doc.addSubview_(sh)
            count_lbl = _lbl(
                f"{len(grp)}",
                _tabular(_avenir(10, AppKit.NSFontWeightMedium)), W32(),
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

        # No thread rail — days are set apart by their Futura headers and the
        # whitespace between groups; each row keeps its colour-coded source dot.
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

        if _reduce_motion():
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
                ctx.setDuration_(_anim_dur(0.28))
                ctx.setTimingFunction_(
                    AppKit.CAMediaTimingFunction.functionWithName_("easeOut")
                )
                self._main.animator().setAlphaValue_(1.0)

            def _after_fade():
                _after_main_build()

            AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
                _fade_in, _after_fade)

        def _fade_ob_out(ctx):
            ctx.setDuration_(_anim_dur(0.18))
            ob.animator().setAlphaValue_(0.0)

        AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
            _fade_ob_out, _swap)

    def _shake(self, view):
        ox = view.frame().origin.x
        for dx in (8,-8,5,-5,2,-2,0):
            def _shake_step(ctx, d=dx):
                ctx.setDuration_(_anim_dur(0.04))
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
        # The footer is hidden at rest; surface it so the flash is actually seen.
        self._reveal_footer(True)
        # Microanimation: subtle pulse — bright on appearance, soft fade
        # back to the resting tertiary tone. The status label is short
        # enough that the eye picks up the change easily, but the pulse
        # makes "Copied", "Starred", and "Capture paused" feel intentional.
        if not _reduce_motion():
            try:
                self._st_lbl.setTextColor_(W94())
                def _pulse_status(ctx):
                    ctx.setDuration_(_anim_dur(0.4))
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
            def _done():
                self._refresh_count_label()
                if not getattr(self, "_footer_hovered", False):
                    self._reveal_footer(False)
            AppHelper.callAfter(_done)
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
        if _reduce_motion():
            self._panel.setFrameOrigin_(target)
            return
        try:
            def _nudge_panel(ctx):
                ctx.setDuration_(_anim_dur(0.12))
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
