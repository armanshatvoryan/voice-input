import io
import wave

import numpy as np

from voiceinput import audio


class FakeStream:
    """Stand-in for sd.InputStream so buffer logic is testable without a device."""

    def __init__(self):
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


def block(value, n=1024):
    return np.full(n, value, dtype=np.int16)


def test_to_wav_roundtrip():
    pcm = np.array([0, 100, -100, 32767, -32768], dtype=np.int16)
    with wave.open(io.BytesIO(audio.to_wav(pcm, 16000))) as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16000
        assert np.array_equal(
            np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16), pcm
        )


def test_duration_ms():
    assert audio.duration_ms(np.zeros(16000, dtype=np.int16), 16000) == 1000.0
    assert audio.duration_ms(np.zeros(0, dtype=np.int16), 16000) == 0.0


def test_start_opens_stream_and_stop_closes_it():
    fake = FakeStream()
    mic = audio.MicStream(stream_factory=lambda: fake)
    mic.start()
    assert fake.started and mic.recording
    mic._callback(block(5), 1024, None, None)
    out = mic.stop()
    assert len(out) == 1024 and out[0] == 5
    assert fake.closed and not mic.recording          # device released, not just muted


def test_no_capture_before_start():
    # a stray callback while idle (e.g. from a probe) must not accumulate audio.
    mic = audio.MicStream(stream_factory=FakeStream)
    mic._callback(block(9), 1024, None, None)
    assert len(mic.snapshot()) == 0


def test_snapshot_grows_while_recording_without_stopping():
    mic = audio.MicStream(stream_factory=FakeStream)
    mic.start()
    mic._callback(block(3), 1024, None, None)
    assert len(mic.snapshot()) == 1024
    mic._callback(block(4), 1024, None, None)
    assert len(mic.snapshot()) == 2048               # still recording
    assert mic.recording
    assert len(mic.stop()) == 2048                    # final take has everything


def test_max_duration_caps_the_take():
    mic = audio.MicStream(max_s=0.1, stream_factory=FakeStream)  # 1600 frames
    mic.start()
    for _ in range(10):
        mic._callback(block(0), 1024, None, None)
    assert len(mic.stop()) == 2048                    # stopped once cap was passed
    assert mic.overflowed


def test_stop_without_audio_returns_empty():
    mic = audio.MicStream(stream_factory=FakeStream)
    mic.start()
    assert len(mic.stop()) == 0


def test_probe_opens_and_closes_without_recording():
    fake = FakeStream()
    mic = audio.MicStream(stream_factory=lambda: fake)
    mic.probe()
    assert fake.closed and not mic.recording
    assert len(mic.snapshot()) == 0


def test_start_failure_closes_stream_and_resets_recording():
    class BoomStream:
        def __init__(self):
            self.closed = False

        def start(self):
            raise RuntimeError("device busy")

        def stop(self):
            pass

        def close(self):
            self.closed = True

    boom = BoomStream()
    mic = audio.MicStream(stream_factory=lambda: boom)
    try:
        mic.start()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
    assert boom.closed
    assert not mic.recording
    assert mic._stream is None
