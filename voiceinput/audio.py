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


def _reset_portaudio() -> None:
    """Re-initialise PortAudio so it re-reads the CoreAudio device table.

    PortAudio snapshots devices (and the default input) once per process. If
    the device it cached disappears afterwards — a Bluetooth mic disconnecting
    is the common case — every later open fails with paInternalError (-9986,
    kAudioHardwareBadObjectError underneath) until the library is torn down
    and brought back up. Only safe while no stream is open, which is how
    MicStream uses it.
    """
    import sounddevice as sd

    sd._terminate()
    sd._initialize()


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
    def __init__(self, samplerate=16000, device=None, max_s=180, stream_factory=None,
                 reset_portaudio=None):
        self.samplerate = samplerate
        self.device = device
        self.max_frames = int(max_s * samplerate)
        self._lock = threading.Lock()
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._stream = None
        self.overflowed = False
        self._stream_factory = stream_factory or self._default_factory
        self._reset_portaudio = reset_portaudio or _reset_portaudio

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

    def _open_started_stream(self):
        """One factory + start attempt; the stream never leaks on failure."""
        stream = self._stream_factory()
        try:
            stream.start()
        except Exception:
            for step in (stream.stop, stream.close):
                try:
                    step()
                except Exception:
                    pass
            raise
        return stream

    def _open_with_retry(self):
        """Open the stream, retrying once on a fresh PortAudio device table.

        PortAudio's table is a per-process snapshot; if the cached device (or
        cached default input) has since vanished — Bluetooth mic disconnected —
        the open fails -9986 forever. Re-initialising the library re-reads the
        table, so the retry lands on a device that actually exists. Safe here
        because this MicStream is the process's only PortAudio user and holds
        no open stream at this point.
        """
        try:
            return self._open_started_stream()
        except Exception:
            self._reset_portaudio()
            return self._open_started_stream()

    def probe(self) -> None:
        """Open + close the device once to trigger the macOS mic-permission prompt.

        Records nothing (recording flag stays False, so the callback is a no-op);
        its only job is to make the app appear in Privacy > Microphone up front
        instead of mid-dictation.
        """
        stream = self._open_with_retry()
        stream.stop()
        stream.close()

    # ---- capture ----------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            self._frames = []
            self.overflowed = False
            self._recording = True
        try:
            stream = self._open_with_retry()
        except Exception:
            with self._lock:
                self._recording = False
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
