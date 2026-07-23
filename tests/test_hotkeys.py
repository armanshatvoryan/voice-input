from voiceinput.hotkeys import START, START_CLEANUP, STOP, ComboWatcher


def watcher():
    return ComboWatcher(["ctrl", "alt"], "space", "shift")


def test_full_combo_starts_and_release_stops():
    w = watcher()
    assert w.press("ctrl_l") is None
    assert w.press("alt_l") is None
    assert w.press("space") == START
    assert w.release("space") == STOP


def test_partial_combo_does_not_start():
    w = watcher()
    w.press("ctrl_l")
    assert w.press("space") is None
    assert w.release("space") is None


def test_left_right_modifiers_are_equivalent():
    w = watcher()
    w.press("ctrl_r")
    w.press("alt_r")
    assert w.press("space") == START


def test_shift_selects_cleanup_mode():
    w = watcher()
    w.press("ctrl_l")
    w.press("alt_l")
    w.press("shift_l")
    assert w.press("space") == START_CLEANUP


def test_releasing_shift_mid_hold_does_not_stop():
    w = watcher()
    for key in ("ctrl_l", "alt_l", "shift_l"):
        w.press(key)
    assert w.press("space") == START_CLEANUP
    assert w.release("shift_l") is None      # must not truncate the take
    assert w.release("space") == STOP


def test_releasing_a_modifier_stops():
    w = watcher()
    w.press("ctrl_l")
    w.press("alt_l")
    w.press("space")
    assert w.release("ctrl_l") == STOP


def test_key_autorepeat_does_not_restart():
    w = watcher()
    w.press("ctrl_l")
    w.press("alt_l")
    assert w.press("space") == START
    assert w.press("space") is None          # held keys repeat on macOS
    assert w.release("space") == STOP


def test_stop_is_emitted_once():
    w = watcher()
    w.press("ctrl_l")
    w.press("alt_l")
    w.press("space")
    assert w.release("space") == STOP
    assert w.release("ctrl_l") is None
    assert w.release("alt_l") is None


def test_unrelated_keys_are_ignored():
    w = watcher()
    w.press("ctrl_l")
    w.press("alt_l")
    w.press("space")
    assert w.press("a") is None
    assert w.release("a") is None
    assert w.release("space") == STOP


def test_reset_clears_state():
    w = watcher()
    w.press("ctrl_l")
    w.press("alt_l")
    w.press("space")
    w.reset()
    assert not w.active
    assert w.press("space") is None
