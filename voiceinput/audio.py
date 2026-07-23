"""Always-on microphone stream with a pre-roll ring buffer.

The stream stays open for the life of the daemon, so pressing the hotkey costs no
device-open latency, and the pre-roll buffer recovers the syllables spoken in the
moment *before* the key registered.
"""

from __future__ import annotations

import io
import math
import threading
import wave

import numpy as np
import sounddevice as sd

BLOCKSIZE = 1024  # 64 ms at 16 kHz


def to_wav(pcm: np.ndarray, samplerate: int) -> bytes:
    """Wrap mono int16 samples in a RIFF container (what whisper-server expects)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(samplerate)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def duration_ms(pcm: np.ndarray, samplerate: int) -> float:
    return len(pcm) / samplerate * 1000.0


def preroll_blocks(preroll_ms: int, samplerate: int, blocksize: int = BLOCKSIZE) -> int:
    return max(1, math.ceil(preroll_ms / 1000 * samplerate / blocksize))


class MicStream:
    def __init__(self, samplerate=16000, device=None, preroll_ms=400, max_s=180):
        self.samplerate = samplerate
        self.device = device
        self.max_frames = int(max_s * samplerate)
        self._lock = threading.Lock()
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._preroll: list[np.ndarray] = []
        self._preroll_max = preroll_blocks(preroll_ms, samplerate)
        self._stream: sd.InputStream | None = None
        self.overflowed = False

    def _callback(self, indata, frames, time_info, status):
        block = indata.copy().reshape(-1)
        with self._lock:
            if self._recording:
                if sum(len(b) for b in self._frames) < self.max_frames:
                    self._frames.append(block)
                else:
                    self.overflowed = True
            else:
                self._preroll.append(block)
                if len(self._preroll) > self._preroll_max:
                    del self._preroll[0]

    def open(self) -> None:
        self._stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="int16",
            blocksize=BLOCKSIZE,
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def start(self) -> None:
        with self._lock:
            self._frames = list(self._preroll)  # keep the pre-hotkey audio
            self._recording = True
            self.overflowed = False

    def stop(self) -> np.ndarray:
        with self._lock:
            self._recording = False
            frames, self._frames = self._frames, []
        if not frames:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(frames)

    @property
    def recording(self) -> bool:
        return self._recording

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False
