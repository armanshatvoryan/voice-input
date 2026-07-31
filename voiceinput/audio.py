"""On-demand microphone capture.

The device is opened when the hotkey goes down and closed when it comes up, so
the mic is genuinely off (not just muted) between takes — no always-on stream and
no recording indicator when idle. `snapshot()` exposes the audio captured so far
without stopping, which is what the live-preview loop samples every ~400ms.

The buffer logic is kept independent of PortAudio via an injectable
`stream_factory`, so it is unit-testable without a real input device.
"""

from __future__ import annotations

import io
import threading
import wave

import numpy as np

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


class MicStream:
    def __init__(self, samplerate=16000, device=None, max_s=180, stream_factory=None):
        self.samplerate = samplerate
        self.device = device
        self.max_frames = int(max_s * samplerate)
        self._lock = threading.Lock()
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._stream = None
        self.overflowed = False
        self._stream_factory = stream_factory or self._default_factory

    # ---- device -----------------------------------------------------------

    def _default_factory(self):
        import sounddevice as sd

        return sd.InputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="int16",
            blocksize=BLOCKSIZE,
            device=self.device,
            callback=self._callback,
        )

    def _callback(self, indata, frames, time_info, status):
        block = np.asarray(indata).reshape(-1).copy()
        with self._lock:
            if not self._recording:
                return
            if sum(len(b) for b in self._frames) < self.max_frames:
                self._frames.append(block)
            else:
                self.overflowed = True

    def _close_stream(self) -> None:
        if self._stream is not None:
            for step in (self._stream.stop, self._stream.close):
                try:
                    step()
                except Exception:
                    pass
            self._stream = None

    def probe(self) -> None:
        """Open + close the device once to trigger the macOS mic-permission prompt.

        Records nothing (recording flag stays False, so the callback is a no-op);
        its only job is to make the app appear in Privacy > Microphone up front
        instead of mid-dictation.
        """
        stream = self._stream_factory()
        stream.start()
        stream.stop()
        stream.close()

    # ---- capture ----------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            self._frames = []
            self.overflowed = False
            self._recording = True
        stream = self._stream_factory()
        try:
            stream.start()
        except Exception:
            with self._lock:
                self._recording = False
            for step in (stream.stop, stream.close):
                try:
                    step()
                except Exception:
                    pass
            raise
        self._stream = stream

    def snapshot(self) -> np.ndarray:
        """Audio captured so far, without stopping the take (for live preview)."""
        with self._lock:
            if not self._frames:
                return np.zeros(0, dtype=np.int16)
            return np.concatenate(self._frames)

    def stop(self) -> np.ndarray:
        with self._lock:
            self._recording = False
            frames, self._frames = self._frames, []
        self._close_stream()
        if not frames:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(frames)

    def close(self) -> None:
        self._recording = False
        self._close_stream()

    @property
    def recording(self) -> bool:
        return self._recording

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
