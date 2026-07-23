import io
import wave

import numpy as np

from voiceinput import audio


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


def test_preroll_blocks_rounds_up():
    # 400ms at 16kHz = 6400 frames = 6.25 blocks of 1024 -> 7
    assert audio.preroll_blocks(400, 16000, 1024) == 7
    assert audio.preroll_blocks(0, 16000, 1024) == 1  # never zero


def test_preroll_is_prepended_to_the_take():
    stream = audio.MicStream(preroll_ms=400)
    before = np.full(1024, 7, dtype=np.int16)
    during = np.full(1024, 9, dtype=np.int16)

    stream._callback(before, 1024, None, None)   # mic idle: fills pre-roll
    stream.start()
    stream._callback(during, 1024, None, None)   # hotkey held
    captured = stream.stop()

    assert len(captured) == 2048
    assert captured[0] == 7 and captured[-1] == 9


def test_preroll_ring_buffer_is_bounded():
    stream = audio.MicStream(preroll_ms=400)
    for _ in range(50):
        stream._callback(np.zeros(1024, dtype=np.int16), 1024, None, None)
    stream.start()
    assert len(stream.stop()) == 7 * 1024


def test_max_duration_caps_the_take():
    stream = audio.MicStream(max_s=0.1)  # 1600 frames
    stream.start()
    for _ in range(10):
        stream._callback(np.zeros(1024, dtype=np.int16), 1024, None, None)
    assert len(stream.stop()) == 2048  # stopped as soon as the cap was passed
    assert stream.overflowed


def test_stop_without_audio_returns_empty():
    stream = audio.MicStream()
    stream.start()
    assert len(stream.stop()) == 0
