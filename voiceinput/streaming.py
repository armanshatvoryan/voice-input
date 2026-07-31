"""Live-preview streaming loop.

While the key is held, this samples the audio captured so far every `interval_ms`,
runs it through the fast preview model, and emits the partial transcript for the
HUD to show. It never touches the final result — on release the daemon does one
accurate full re-decode with the large model. All dependencies (snapshot, decode,
emit, clock, sleep) are injected so the loop is testable without audio or a server.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from . import audio


def next_delay_ms(interval_ms: int, elapsed_ms: float) -> float:
    """Time to wait before the next tick so ticks land ~interval_ms apart.

    If a decode already took longer than the interval, don't wait — the cadence
    just stretches to however fast the model can keep up.
    """
    return max(0.0, interval_ms - elapsed_ms)


class PreviewStreamer:
    def __init__(
        self,
        snapshot: Callable[[], "audio.np.ndarray"],
        decode: Callable[[object], str],
        emit: Callable[[str], None],
        interval_ms: int = 400,
        samplerate: int = 16000,
        min_ms: int = 200,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.snapshot = snapshot
        self.decode = decode
        self.emit = emit
        self.interval_ms = interval_ms
        self.samplerate = samplerate
        self.min_ms = min_ms
        self.sleep = sleep
        self.clock = clock

    def tick(self) -> bool:
        """One sample→decode→emit cycle. Returns True if a partial was emitted."""
        pcm = self.snapshot()
        if audio.duration_ms(pcm, self.samplerate) < self.min_ms:
            return False
        try:
            partial = self.decode(pcm)
        except Exception:
            return False  # a dropped partial is cosmetic; never break the take
        if partial:
            self.emit(partial)
            return True
        return False

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            start = self.clock()
            self.tick()
            elapsed_ms = (self.clock() - start) * 1000
            # Interruptible wait: releasing the key sets `stop` and we exit at once.
            stop.wait(next_delay_ms(self.interval_ms, elapsed_ms) / 1000)
