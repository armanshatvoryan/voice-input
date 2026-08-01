import os
import threading

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


def test_second_stop_of_a_take_is_ignored():
    # The watchdog stops the take, then the swallowed key-up finally arrives. The
    # second STOP must not stop the mic again — that queued a zero-length take,
    # which surfaced as a phantom "ignored 0ms tap" after every watchdog stop.
    import numpy as np

    from voiceinput import config, hotkeys

    d = daemon.Daemon(config.load(paths=[]), spawn_server=False)
    d.cfg["sound"]["enabled"] = False
    d.preview_enabled = False          # no preview server in a unit test

    class StubMic:
        def __init__(self):
            self.starts = 0
            self.stops = 0
            self.frames = np.ones(8000, dtype=np.int16)

        def start(self):
            self.starts += 1

        def stop(self):
            self.stops += 1
            frames, self.frames = self.frames, np.zeros(0, dtype=np.int16)
            return frames

        def snapshot(self):
            return self.frames

    d.mic = StubMic()

    d.on_event(hotkeys.START)
    d.on_event(hotkeys.STOP)
    d.on_event(hotkeys.STOP)           # the late key-up

    assert d.mic.stops == 1
    assert d.jobs.qsize() == 1
    assert d.state != "error"


def test_two_threads_stopping_the_preview_do_not_race():
    # The release watchdog and a late key-up can both deliver STOP, so two threads
    # can be inside stop_preview() at once. One must not tear down preview_thread
    # while the other is parked in join() — that raised
    # "'NoneType' object has no attribute 'is_alive'", which the on_event handler
    # turned into a failed take (state=error, mic never stopped, HUD torn down).
    from voiceinput import config

    d = daemon.Daemon(config.load(paths=[]), spawn_server=False)

    first_join = threading.Event()   # thread A has parked inside join()
    b_done = threading.Event()       # thread B finished its own stop_preview()

    class SlowThread:
        """Stands in for a preview thread still mid-decode: the first join() parks
        long enough for another STOP to run, later joins return at once."""

        def __init__(self):
            self.alive = True
            self.joins = 0

        def join(self, timeout=None):
            self.joins += 1
            if self.joins == 1:
                first_join.set()
                b_done.wait(2.0)
            self.alive = False

        def is_alive(self):
            return self.alive

    d.preview_stop = threading.Event()
    d.preview_thread = SlowThread()

    errors = []

    def stop(tag):
        try:
            d.stop_preview()
        except Exception as exc:            # noqa: BLE001 - the assertion is the point
            errors.append((tag, exc))

    a = threading.Thread(target=stop, args=("A",))
    a.start()
    assert first_join.wait(2.0), "thread A never reached join()"

    def b():
        stop("B")
        b_done.set()

    thread_b = threading.Thread(target=b)
    thread_b.start()
    thread_b.join(5.0)
    a.join(5.0)

    assert not errors, f"stop_preview raced: {errors}"
    assert d.preview_thread is None
    assert d.preview_stop is None
