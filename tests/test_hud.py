from voiceinput import hud


def test_hud_frame_centers_horizontally():
    x, y = hud.hud_frame(1440, 900, 900, 60, margin_bottom=120)
    assert x == (1440 - 900) / 2
    assert y == 120


def test_hud_frame_handles_narrow_screen():
    x, _ = hud.hud_frame(800, 600, 900, 60)
    assert x == (800 - 900) / 2       # negative is fine; AppKit clamps on screen
