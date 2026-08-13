import pytest

from voiceinput import hud


def test_hud_frame_centers_horizontally():
    x, y = hud.hud_frame(1440, 900, 900, 60, margin_bottom=120)
    assert x == (1440 - 900) / 2
    assert y == 120


def test_hud_frame_handles_narrow_screen():
    x, _ = hud.hud_frame(800, 600, 900, 60)
    assert x == (800 - 900) / 2       # negative is fine; AppKit clamps on screen


def test_sync_refronts_a_panel_appkit_ordered_out():
    """The 08-01 'HUD down' class: real panel visibility must be re-asserted
    every tick from daemon state, never tracked in a shadow bool — if AppKit
    orders the panel out behind our back (Space/display change, or a hide()
    that half-ran), the next tick must put it back on screen."""
    AppKit = pytest.importorskip("AppKit")
    h = hud.CaptionHUD()
    h._verified_at = float("inf")     # pytest windows are never server-onscreen
    h.sync(True, "one")
    h.sync(True, "two")               # every-tick call: idempotent, no raise
    assert h._panel.isVisible()
    h._panel.orderOut_(None)          # AppKit yanks it behind our back
    h.sync(True, "three")             # next tick heals it
    assert h._panel.isVisible()
    h.sync(False, "")
    h.sync(False, "")                 # idempotent hide
    assert not h._panel.isVisible()


def test_show_rebuilds_panel_when_window_server_drops_it(monkeypatch):
    """The 08-13 wedge: after overnight sleep the window server stopped
    honoring orderFrontRegardless for the existing panel — AppKit reported
    visible=True while CGWindowList never listed the window onscreen, for the
    rest of the process (daemon restart didn't cure; process relaunch did).
    In-process orderFront retries can't revive a defunct server-side window;
    the only in-process cure is a fresh window (new window number). show()
    must cross-check server truth and discard the panel on divergence so the
    next tick rebuilds it."""
    AppKit = pytest.importorskip("AppKit")
    h = hud.CaptionHUD()
    h.sync(True, "one")
    first = h._panel
    monkeypatch.setattr(hud.CaptionHUD, "_server_onscreen", lambda self: False)
    h._verified_at = -100.0            # bypass the 1s verify rate limit
    h.sync(True, "two")                # detects desync, discards dead window
    monkeypatch.undo()
    h.sync(True, "three")              # next tick rebuilds fresh
    assert h._panel is not None
    assert h._panel is not first
    assert h._panel.isVisible()
    h.sync(False, "")


def test_server_onscreen_is_none_safe():
    """Diagnostics must never break the HUD: no panel → None (not a raise),
    and a Quartz failure → None. (True can only be asserted inside a real
    running .app — a bare pytest process's windows never actually display,
    so the server legitimately reports them off-screen.)"""
    AppKit = pytest.importorskip("AppKit")
    h = hud.CaptionHUD()
    assert h._server_onscreen() is None    # no panel yet
    h._verified_at = float("inf")
    h.sync(True, "x")
    assert h._server_onscreen() in (True, False)   # real answer, no raise
    h.sync(False, "")


def test_panel_level_survives_floating_panel_setter():
    # setFloatingPanel_(True) silently resets the window level to floating (3);
    # the status level must be applied after it so the HUD stays above overlays.
    AppKit = pytest.importorskip("AppKit")
    h = hud.CaptionHUD()
    h._verified_at = float("inf")     # pytest windows are never server-onscreen
    h.sync(True, "level check")
    level = int(h._panel.level())
    h.sync(False, "")
    assert level == int(AppKit.NSStatusWindowLevel)
