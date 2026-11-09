"""Paste must survive non-Latin keyboard layouts and the .app's bare environment."""

import os
import subprocess

from voiceinput import inject

# What the launched .app really has: no LANG, and a Russian-system text encoding.
APP_ENV = {"PATH": "/usr/bin", "__CF_USER_TEXT_ENCODING": "0x1F5:0x7:0x31"}


def _keycode_with_layout_lacking_v(key) -> int:
    from Quartz import CGEventGetIntegerValueField, kCGKeyboardEventKeycode

    # Russian / Armenian layouts have no key producing "v" -> mapping has no entry.
    event = key._event(set(), {"м": 9, "ф": 0}, True)
    return CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)


class FakeKeyboard:
    def __init__(self):
        self.pressed_keys = []

    def pressed(self, *_keys):
        import contextlib

        return contextlib.nullcontext()

    def press(self, key):
        self.pressed_keys.append(key)

    def release(self, key):
        pass


def test_paste_sends_the_physical_v_key_on_a_layout_without_v(monkeypatch):
    import pynput.keyboard

    fake = FakeKeyboard()
    monkeypatch.setattr(pynput.keyboard, "Controller", lambda: fake)

    inject.press_paste()

    assert len(fake.pressed_keys) == 1
    assert _keycode_with_layout_lacking_v(fake.pressed_keys[0]) == 9  # kVK_ANSI_V


def test_clipboard_round_trips_cyrillic_and_armenian_in_app_env(monkeypatch):
    monkeypatch.setattr(os, "environ", dict(APP_ENV))
    for sample in ("Тест русской речи.", "Փորձարկում հայերեն"):
        # The scratch "ruler" pasteboard keeps the user's real clipboard untouched.
        subprocess.run(
            ["pbcopy", "-pboard", "ruler"], input=sample.encode("utf-8"),
            check=True, timeout=5, env=inject._pb_env(),
        )
        out = subprocess.run(
            ["pbpaste", "-pboard", "ruler"], capture_output=True, timeout=5,
            env={"PATH": "/usr/bin", "LANG": "en_US.UTF-8"},
        ).stdout.decode("utf-8")
        assert out == sample


def test_set_and_get_clipboard_force_utf8(monkeypatch):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(kwargs.get("env") or {})
        return subprocess.CompletedProcess(cmd, 0, stdout=b"")

    monkeypatch.setattr(inject.subprocess, "run", fake_run)
    inject.set_clipboard("Тест")
    inject.get_clipboard()

    assert len(seen) == 2
    assert all("UTF-8" in env.get("LANG", "") for env in seen)
