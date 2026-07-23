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

# pynput reports left/right variants separately; we treat them as one logical modifier.
_ALIASES = {
    "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "shift_l": "shift", "shift_r": "shift",
    "cmd_l": "cmd", "cmd_r": "cmd",
}


def normalise(name: str) -> str:
    return _ALIASES.get(name, name)


class ComboWatcher:
    def __init__(self, modifiers, key: str, cleanup_modifier: str | None = None):
        self.modifiers = {normalise(m) for m in modifiers}
        self.key = normalise(key)
        self.cleanup_modifier = normalise(cleanup_modifier) if cleanup_modifier else None
        self.pressed: set[str] = set()
        self.active = False

    def press(self, name: str) -> str | None:
        name = normalise(name)
        self.pressed.add(name)
        if self.active or name != self.key:
            return None
        if not self.modifiers <= self.pressed:
            return None
        self.active = True
        if self.cleanup_modifier and self.cleanup_modifier in self.pressed:
            return START_CLEANUP
        return START

    def release(self, name: str) -> str | None:
        name = normalise(name)
        self.pressed.discard(name)
        if not self.active:
            return None
        # Releasing any member of the chord ends the hold. The cleanup modifier is
        # excluded on purpose: letting go of shift mid-hold must not truncate the take.
        if name == self.key or name in self.modifiers:
            self.active = False
            return STOP
        return None

    def reset(self) -> None:
        self.pressed.clear()
        self.active = False


def _key_name(key) -> str:
    """Map a pynput key object to a lowercase name ComboWatcher understands."""
    name = getattr(key, "name", None)
    if name:
        return name.lower()
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
