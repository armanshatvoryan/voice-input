from voiceinput.hotkeys import CANCEL, START, START_CLEANUP, STOP, ComboWatcher, describe


def watcher():
    return ComboWatcher(["ctrl", "alt"], "space", "shift")


def right_option():
    """The new default: hold right-option alone, right-option+shift = cleanup."""
    return ComboWatcher([], "alt_r", "shift")


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


# ---- right-option (side-specific) default -------------------------------

def test_right_option_alone_starts_and_release_stops():
    w = right_option()
    assert w.press("alt_r") == START
    assert w.release("alt_r") == STOP


def test_left_option_does_not_trigger_right_option_hotkey():
    # left option is used for typing special chars; it must NOT fire dictation.
    w = right_option()
    assert w.press("alt_l") is None
    assert w.release("alt_l") is None
    assert not w.active


def test_alt_gr_counts_as_right_option():
    # some layouts report the right option key as alt_gr.
    w = right_option()
    assert w.press("alt_gr") == START
    assert w.release("alt_gr") == STOP


def test_right_option_plus_shift_is_cleanup():
    w = right_option()
    assert w.press("shift_l") is None
    assert w.press("alt_r") == START_CLEANUP
    assert w.release("shift_l") is None      # letting go of shift must not truncate
    assert w.release("alt_r") == STOP


def test_generic_alt_still_matches_either_side():
    # backward-compat: a combo asking for generic "alt" accepts left OR right.
    w = ComboWatcher(["alt"], "space")
    assert w.press("alt_r") is None
    assert w.press("space") == START


def test_describe_renders_symbols():
    assert describe([], "alt_r") == "⌥ (right)"
    assert describe(["shift"], "alt_r") == "⇧ + ⌥ (right)"
    assert describe(["ctrl", "alt"], "space") == "⌃ + ⌥ + Space"


def test_backspace_mid_hold_cancels():
    w = right_option()
    assert w.press("alt_r") == START
    assert w.press("backspace") == CANCEL
    assert not w.active
    assert w.release("alt_r") is None        # releasing after a cancel is a no-op


def test_backspace_when_idle_does_nothing():
    w = right_option()
    assert w.press("backspace") is None
    assert w.press("alt_r") == START         # a fresh hold still works after stray backspace


def test_cancel_key_configurable():
    w = ComboWatcher([], "alt_r", "shift", cancel_key=None)
    assert w.press("alt_r") == START
    assert w.press("backspace") is None      # cancel disabled
    assert w.release("alt_r") == STOP
