"""Floating caption HUD for the live transcript.

A borderless, non-activating panel pinned bottom-center. Non-activating is the
crucial part: it must never become the key window, or the ⌘V paste would land in
the HUD instead of the app you're dictating into. All AppKit calls must run on the
main thread — the menu-bar app drives show/update/hide from a rumps timer, which
is the main Cocoa thread. AppKit is imported lazily so `hud_frame` (pure geometry)
and the rest of the package import fine anywhere.
"""

from __future__ import annotations


def hud_frame(screen_w: float, screen_h: float, panel_w: float, panel_h: float,
              margin_bottom: float = 120.0) -> tuple[float, float]:
    """Bottom-centered origin (x, y) for the panel on a screen of the given size."""
    x = (screen_w - panel_w) / 2
    y = margin_bottom
    return x, y


class CaptionHUD:
    """Lazily-built AppKit panel. Safe to construct off the main thread; the actual
    window is created on first show()."""

    HEIGHT = 60.0
    FONT_SIZE = 22.0
    MARGIN_BOTTOM = 120.0

    def __init__(self):
        self._panel = None
        self._label = None
        self._verified_at = 0.0

    # ---- construction (main thread) --------------------------------------

    @staticmethod
    def _screen_frame():
        """Current main-screen frame; falls back to the first screen.

        mainScreen() can return None (screens asleep, display reconfiguring) —
        raising here is fine, the caller's timer retries next tick.
        """
        from AppKit import NSScreen

        screen = NSScreen.mainScreen()
        if screen is None:
            screens = NSScreen.screens()
            if not screens:
                raise RuntimeError("no screens attached")
            screen = screens[0]
        return screen.frame()

    def _ensure(self):
        if self._panel is not None:
            return
        from AppKit import (
            NSPanel, NSTextField, NSView, NSColor, NSFont,
            NSBackingStoreBuffered, NSStatusWindowLevel,
            NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
            NSTextAlignmentCenter, NSLineBreakByTruncatingHead,
        )
        from Foundation import NSMakeRect

        screen = self._screen_frame()
        width = min(screen.size.width - 160, 900.0)
        x, y = hud_frame(screen.size.width, screen.size.height, width, self.HEIGHT,
                         self.MARGIN_BOTTOM)
        rect = NSMakeRect(x, y, width, self.HEIGHT)

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setIgnoresMouseEvents_(True)          # clicks pass through
        panel.setFloatingPanel_(True)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setHidesOnDeactivate_(False)
        # AFTER setFloatingPanel_: that setter silently resets the level to
        # floating (3), which would leave the HUD under fullscreen overlays.
        panel.setLevel_(NSStatusWindowLevel)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )

        background = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, self.HEIGHT))
        background.setWantsLayer_(True)
        background.layer().setBackgroundColor_(
            NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.72).CGColor()
        )
        background.layer().setCornerRadius_(14.0)

        inset = 18.0
        label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(inset, 0, width - 2 * inset, self.HEIGHT)
        )
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setTextColor_(NSColor.whiteColor())
        label.setFont_(NSFont.systemFontOfSize_(self.FONT_SIZE))
        label.setAlignment_(NSTextAlignmentCenter)
        label.cell().setLineBreakMode_(NSLineBreakByTruncatingHead)  # keep newest words
        label.cell().setWraps_(False)
        label.setStringValue_("")

        background.addSubview_(label)
        panel.setContentView_(background)

        self._panel = panel
        self._label = label

    # ---- main-thread API --------------------------------------------------

    def sync(self, want: bool, text: str = "") -> None:
        """Reconcile the panel with what the daemon wants, idempotently.

        Called every timer tick. Deliberately stateless: the 2026-08-01 "HUD
        down" incident was a shadow visibility bool diverging from real AppKit
        state — one stranded flag and the panel never got another orderFront
        for the life of the process. Re-asserting visibility each tick means
        anything that knocks the panel out behind our back (a Space/display
        change, a half-run hide, one raised AppKit call) heals on the next tick.
        """
        if want:
            self.show(text)
        else:
            self.hide()

    def show(self, text: str = "") -> None:
        self._ensure()
        self._label.setStringValue_(text or "…")
        self._reposition()
        self._panel.orderFrontRegardless()
        self._verify_server()

    def _server_onscreen(self):
        """Ask the window server — not AppKit — whether the panel is ordered in.

        Returns True/False, or None when the question can't be answered (no
        panel, no window number yet, Quartz unavailable). AppKit's isVisible()
        is this process's belief; CGWindowList is the server's truth, and the
        2026-08-13 wedge proved they can diverge for a process's lifetime.
        """
        if self._panel is None:
            return None
        try:
            import Quartz

            num = self._panel.windowNumber()
            if num <= 0:
                return None
            info = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionIncludingWindow, num)
            if not info:
                return False
            return bool(info[0].get("kCGWindowIsOnscreen"))
        except Exception:
            return None     # diagnostics must never break the HUD itself

    def _verify_server(self) -> None:
        """Heal the 2026-08-13 wedge class: after an overnight sleep the window
        server silently stopped honoring orderFrontRegardless for the existing
        panel (AppKit visible=True, server onscreen=False, every take, until
        the process was relaunched). Re-ordering the same window can't cure
        that, so on divergence discard the panel — the next 0.12s tick rebuilds
        a fresh window with a new server-side identity. Checked at most 1/s."""
        import time

        now = time.monotonic()
        if now - self._verified_at < 1.0:
            return
        self._verified_at = now
        if self._server_onscreen() is False:
            from .daemon import log

            log("HUD desync: window server dropped the panel — rebuilding")
            try:
                self._panel.orderOut_(None)
            except Exception:
                pass
            self._panel = None
            self._label = None

    def _reposition(self) -> None:
        """Re-center on the current screen; displays may have changed since
        the panel was built, and a stale origin can place it off every screen —
        'visible' to AppKit, no pixels anywhere."""
        from Foundation import NSMakePoint

        screen = self._screen_frame()
        width = self._panel.frame().size.width
        x, y = hud_frame(screen.size.width, screen.size.height, width, self.HEIGHT,
                         self.MARGIN_BOTTOM)
        self._panel.setFrameOrigin_(NSMakePoint(x, y))

    def hide(self) -> None:
        if self._panel is not None:
            self._panel.orderOut_(None)
