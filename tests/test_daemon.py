import os

from voiceinput import daemon


def test_augment_path_prepends_homebrew_before_system(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    daemon.augment_path()
    parts = os.environ["PATH"].split(os.pathsep)
    assert "/opt/homebrew/bin" in parts
    assert parts.index("/opt/homebrew/bin") < parts.index("/usr/bin")


def test_augment_path_is_idempotent(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    daemon.augment_path()
    daemon.augment_path()
    assert os.environ["PATH"].split(os.pathsep).count("/opt/homebrew/bin") == 1


def test_ignored_tap_returns_state_to_idle():
    # A sub-min_ms tap must not leave the daemon stuck in "recording",
    # or the HUD (which mirrors state) never goes away.
    import numpy as np

    from voiceinput import config

    d = daemon.Daemon(config.load(paths=[]), spawn_server=False)
    d.set_state("recording")
    d.handle(np.zeros(100, dtype=np.int16), "raw")
    assert d.state == "idle"


def test_late_partial_after_recording_is_dropped():
    # A preview thread that outlives its join timeout must not scribble a stale
    # partial into the next take's HUD text.
    from voiceinput import config

    d = daemon.Daemon(config.load(paths=[]), spawn_server=False)
    d.set_state("recording")
    d.emit_partial("live words")
    assert d.partial_text == "live words"
    d.set_state("idle")
    d.emit_partial("stale words")
    assert d.partial_text == "live words"
