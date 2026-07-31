import threading

import numpy as np

from voiceinput import streaming


def pcm(ms, samplerate=16000):
    return np.zeros(int(samplerate * ms / 1000), dtype=np.int16)


def test_next_delay_pads_short_ticks():
    assert streaming.next_delay_ms(400, 120) == 280


def test_next_delay_never_negative_when_decode_overruns():
    assert streaming.next_delay_ms(400, 900) == 0.0


def test_tick_skips_snapshots_below_min_ms():
    calls = []
    s = streaming.PreviewStreamer(
        snapshot=lambda: pcm(50),           # 50ms < 200ms min
        decode=lambda p: calls.append(p) or "text",
        emit=lambda t: None,
        min_ms=200,
    )
    assert s.tick() is False
    assert calls == []                       # decode never invoked on a tiny clip


def test_tick_emits_partial_for_long_enough_audio():
    emitted = []
    s = streaming.PreviewStreamer(
        snapshot=lambda: pcm(600),
        decode=lambda p: "hello world",
        emit=emitted.append,
        min_ms=200,
    )
    assert s.tick() is True
    assert emitted == ["hello world"]


def test_tick_swallows_decode_errors():
    def boom(_):
        raise RuntimeError("server down")

    s = streaming.PreviewStreamer(
        snapshot=lambda: pcm(600), decode=boom, emit=lambda t: None, min_ms=200
    )
    assert s.tick() is False                 # error is cosmetic, not raised


def test_tick_does_not_emit_empty_partial():
    emitted = []
    s = streaming.PreviewStreamer(
        snapshot=lambda: pcm(600), decode=lambda p: "", emit=emitted.append, min_ms=200
    )
    assert s.tick() is False
    assert emitted == []


def test_run_stops_promptly_when_event_is_set():
    ticks = []
    stop = threading.Event()

    def snapshot():
        ticks.append(1)
        if len(ticks) >= 3:
            stop.set()                       # end after a few ticks
        return pcm(600)

    s = streaming.PreviewStreamer(
        snapshot=snapshot,
        decode=lambda p: "x",
        emit=lambda t: None,
        interval_ms=0,                       # no artificial delay in the test
        min_ms=200,
        sleep=lambda s: None,
    )
    s.run(stop)
    assert len(ticks) >= 3
