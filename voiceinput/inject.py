"""Insert text at the cursor of whatever app has focus.

Clipboard + ⌘V rather than synthetic per-character typing: it is one event instead
of hundreds, and it survives non-Latin text (Armenian, Russian) that character
injection mangles under non-US keyboard layouts.
"""

from __future__ import annotations

import subprocess
import threading
import time


def get_clipboard() -> str:
    try:
        return subprocess.run(["pbpaste"], capture_output=True, timeout=5).stdout.decode(
            "utf-8", "replace"
        )
    except Exception:
        return ""


def set_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=5)


def press_paste() -> None:
    from pynput.keyboard import Controller, Key

    keyboard = Controller()
    with keyboard.pressed(Key.cmd):
        keyboard.press("v")
        keyboard.release("v")


def inject(text: str, cfg: dict) -> None:
    """Paste `text`, then put the user's previous clipboard back."""
    if not text:
        return
    inject_cfg = cfg["inject"]
    previous = get_clipboard() if inject_cfg["restore_clipboard"] else None

    set_clipboard(text)
    time.sleep(inject_cfg["paste_delay_ms"] / 1000)
    press_paste()

    if previous is not None:
        # Restoring too early races the paste; do it off-thread so we stay responsive.
        def restore():
            time.sleep(inject_cfg["restore_delay_ms"] / 1000)
            try:
                if get_clipboard() == text:  # don't clobber a copy the user made since
                    set_clipboard(previous)
            except Exception:
                pass

        threading.Thread(target=restore, daemon=True).start()
