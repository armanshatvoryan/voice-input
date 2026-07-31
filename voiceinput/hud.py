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

    # ---- construction (main thread) --------------------------------------

    def _ensure(self):
        if self._panel is not None:
            return
        from AppKit import (
            NSPanel, NSTextField, NSView, NSColor, NSFont, NSScreen,
            NSBackingStoreBuffered, NSStatusWindowLevel,
            NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
            NSTextAlignmentCenter, NSLineBreakByTruncatingHead,
        )
        from Foundation import NSMakeRect

        screen = NSScreen.mainScreen().frame()
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
        panel.setLevel_(NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setIgnoresMouseEvents_(True)          # clicks pass through
        panel.setFloatingPanel_(True)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setHidesOnDeactivate_(False)
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

    def show(self, text: str = "") -> None:
        self._ensure()
        self._label.setStringValue_(text or "…")
        self._panel.orderFrontRegardless()

    def update(self, text: str) -> None:
        if self._panel is None:
            return self.show(text)
        self._label.setStringValue_(text or "…")

    def hide(self) -> None:
        if self._panel is not None:
            self._panel.orderOut_(None)
