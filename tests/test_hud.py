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
    h.sync(True, "one")
    h.sync(True, "two")               # every-tick call: idempotent, no raise
    assert h._panel.isVisible()
    h._panel.orderOut_(None)          # AppKit yanks it behind our back
    h.sync(True, "three")             # next tick heals it
    assert h._panel.isVisible()
    h.sync(False, "")
    h.sync(False, "")                 # idempotent hide
    assert not h._panel.isVisible()


def test_panel_level_survives_floating_panel_setter():
    # setFloatingPanel_(True) silently resets the window level to floating (3);
    # the status level must be applied after it so the HUD stays above overlays.
    AppKit = pytest.importorskip("AppKit")
    h = hud.CaptionHUD()
    h.sync(True, "level check")
    level = int(h._panel.level())
    h.sync(False, "")
    assert level == int(AppKit.NSStatusWindowLevel)
