"""Hold-to-talk combo detection.

`ComboWatcher` is pure state: it takes normalised key names and returns events, so
the whole trigger logic is testable without a keyboard or Accessibility permission.
`listen()` is the thin pynput adapter around it.
"""

from __future__ import annotations

from typing import Callable

START = "start"
START_CLEANUP = "start_cleanup"
STOP = "stop"
CANCEL = "cancel"

# pynput reports left/right variants separately; a *generic* token collapses both
# sides so a combo asking for "alt" accepts either. A *side-specific* token
# (alt_r, cmd_l, …) stays exact so "hold right-option" ignores the left one.
_ALIASES = {
    "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "shift_l": "shift", "shift_r": "shift",
    "cmd_l": "cmd", "cmd_r": "cmd",
}

# Raw pynput names a side-specific token is willing to match. Right-option shows up
# as alt_r on most Macs but alt_gr on some layouts — accept both for the right side.
_SIDE_VARIANTS = {
    "alt_r": {"alt_r", "alt_gr"}, "alt_l": {"alt_l"},
    "cmd_r": {"cmd_r"}, "cmd_l": {"cmd_l"},
    "ctrl_r": {"ctrl_r"}, "ctrl_l": {"ctrl_l"},
    "shift_r": {"shift_r"}, "shift_l": {"shift_l"},
}

_SYMBOLS = {"ctrl": "⌃", "alt": "⌥", "shift": "⇧", "cmd": "⌘", "space": "Space"}
_SIDE = {"_l": " (left)", "_r": " (right)", "_gr": " (right)"}


def normalise(name: str) -> str:
    return _ALIASES.get(name, name)


def _satisfies(token: str, raw: str) -> bool:
    """Does a pressed raw pynput name satisfy a configured token?"""
    if token in _SIDE_VARIANTS:            # side-specific: exact side only
        return raw in _SIDE_VARIANTS[token]
    return raw == token or normalise(raw) == token   # generic: either side


def describe(modifiers, key: str, cleanup_modifier: str | None = None) -> str:
    """Human-readable label like '⌥ (right)' or '⌃ + ⌥ + Space'."""
    def one(tok: str) -> str:
        for suffix, word in _SIDE.items():
            if tok.endswith(suffix):
                base = tok[: -len(suffix)]
                return _SYMBOLS.get(base, base) + word
        return _SYMBOLS.get(tok, tok)

    tokens = [*modifiers]
    if cleanup_modifier:
        tokens.append(cleanup_modifier)
    tokens.append(key)
    return " + ".join(one(t) for t in tokens)


class ComboWatcher:
    def __init__(self, modifiers, key: str, cleanup_modifier: str | None = None,
                 cancel_key: str | None = "backspace"):
        # Tokens are kept verbatim (side-specific ones must stay exact); matching
        # against pressed raw names happens in _satisfies.
        self.modifiers = list(modifiers)
        self.key = key
        self.cleanup_modifier = cleanup_modifier or None
        self.cancel_key = cancel_key or None
        self.pressed: set[str] = set()   # raw pynput names, as received
        self.active = False

    def _is_down(self, token: str) -> bool:
        return any(_satisfies(token, raw) for raw in self.pressed)

    def press(self, name: str) -> str | None:
        self.pressed.add(name)
        if self.active:
            # Cancel key aborts the take; the eventual key release is then a no-op.
            if self.cancel_key and _satisfies(self.cancel_key, name):
                self.active = False
                return CANCEL
            return None
        # The combo may be completed by ANY of its keys — people press two-key
        # chords in either order, and requiring the main key last made roughly
        # half of the holds start nothing.
        if not (self._is_down(self.key) and all(self._is_down(m) for m in self.modifiers)):
            return None
        # ...but only a press that belongs to the combo may trigger it; an
        # unrelated key while the chord happens to be down (e.g. right after a
        # cancel) must not re-start the take.
        if not (_satisfies(self.key, name) or any(_satisfies(m, name) for m in self.modifiers)):
            return None
        self.active = True
        if self.cleanup_modifier and self._is_down(self.cleanup_modifier):
            return START_CLEANUP
        return START

    def release(self, name: str) -> str | None:
        self.pressed.discard(name)
        if not self.active:
            return None
        # Releasing the key or any required modifier ends the hold. The cleanup
        # modifier is excluded: letting go of shift mid-hold must not truncate.
        if _satisfies(self.key, name) or any(_satisfies(m, name) for m in self.modifiers):
            self.active = False
            return STOP
        return None

    def reset(self) -> None:
        self.pressed.clear()
        self.active = False


# Physical keys we must recognise regardless of layout or held modifiers: with
# cmd/alt down (or a RU layout active) the reported char for the Z key is not
# "z", but its virtual keycode is stable.
_VK_NAMES = {6: "z"}


def _key_name(key) -> str:
    """Map a pynput key object to a lowercase name ComboWatcher understands."""
    name = getattr(key, "name", None)
    if name:
        return name.lower()
    vk = getattr(key, "vk", None)
    if vk in _VK_NAMES:
        return _VK_NAMES[vk]
    char = getattr(key, "char", None)
    if char:
        return char.lower()
    return str(key).lower()


def listen(watcher: ComboWatcher, on_event: Callable[[str], None]):
    """Start a global pynput listener. Returns the listener (already started)."""
    from pynput import keyboard

    def handle(fn, key):
        try:
            event = fn(_key_name(key))
        except Exception as exc:  # never let a handler kill the listener thread
            print(f"[voice-input] hotkey error: {exc}")
            return
        if event:
            on_event(event)

    listener = keyboard.Listener(
        on_press=lambda k: handle(watcher.press, k),
        on_release=lambda k: handle(watcher.release, k),
    )
    listener.start()
    return listener


# Physical key-state probes for the release watchdog (pynput key-up events are
# occasionally dropped on fast taps, which would otherwise strand a hold forever).
# Modifiers must be read from the event-source FLAGS (device-dependent bits) —
# CGEventSourceKeyState does not reliably report held modifier keys.
_MOD_FLAG_BITS = {
    "ctrl_l": 0x0001, "ctrl_r": 0x2000, "ctrl": 0x2001,
    "shift_l": 0x0002, "shift_r": 0x0004, "shift": 0x0006,
    "cmd_l": 0x0008, "cmd_r": 0x0010, "cmd": 0x0018,
    "alt_l": 0x0020, "alt_r": 0x0040, "alt_gr": 0x0040, "alt": 0x0060,
}

# Virtual keycodes for the non-modifier keys a combo can use.
_KEYCODES = {"space": [49], "backspace": [51], "esc": [53]}


def combo_tokens(modifiers, key: str) -> list[str]:
    return [*modifiers, key]


def combo_physically_down(tokens: list[str], flags: int, key_state) -> bool | None:
    """Is every token of the combo still physically held?

    `flags` is the current event-source flags word; `key_state(code)` answers for
    plain keys. Returns None when any token is unknown — the caller must then
    treat the state as unknowable and never force a stop.
    """
    for token in tokens:
        bits = _MOD_FLAG_BITS.get(token)
        if bits is not None:
            if not flags & bits:
                return False
            continue
        codes = _KEYCODES.get(token)
        if not codes:
            return None
        if not any(key_state(code) for code in codes):
            return False
    return True


def quartz_flags() -> int:
    import Quartz

    return int(Quartz.CGEventSourceFlagsState(
        Quartz.kCGEventSourceStateCombinedSessionState))


def quartz_key_state(keycode: int) -> bool:
    import Quartz

    return bool(Quartz.CGEventSourceKeyState(
        Quartz.kCGEventSourceStateCombinedSessionState, keycode))
