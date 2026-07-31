"""macOS privacy-permission probes.

Every permission this tool needs fails *quietly* when missing — the mic hands back
digital silence, the hotkey listener starts and never fires, ⌘V posts into the void.
So we ask the OS directly instead of inferring from behaviour.

Each probe returns True/False, or None when the check itself is unavailable.
"""

from __future__ import annotations


def has_accessibility() -> bool | None:
    """Accessibility — required to observe global hotkeys."""
    try:
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())
    except Exception:
        return None


def can_post_events() -> bool | None:
    """Required to synthesise the ⌘V that pastes the transcript."""
    try:
        import Quartz

        return bool(Quartz.CGPreflightPostEventAccess())
    except Exception:
        return None


def can_listen_events() -> bool | None:
    """Input Monitoring; pynput needs this or Accessibility depending on macOS version."""
    try:
        import Quartz

        return bool(Quartz.CGPreflightListenEventAccess())
    except Exception:
        return None


def microphone_peak(cfg: dict, seconds: float = 1.5) -> int:
    """Loudest sample seen in a short capture. 0 means the grant is missing."""
    import time

    import numpy as np

    from .audio import MicStream

    mic = MicStream(samplerate=cfg["audio"]["samplerate"], device=cfg["audio"]["device"])
    try:
        mic.start()          # on-demand: start() opens the device
        time.sleep(seconds)
        pcm = mic.stop()     # and stop() closes it
    finally:
        mic.close()
    return int(np.abs(pcm).max()) if len(pcm) else 0


SETTINGS_HELP = (
    "Open System Settings > Privacy & Security, add the app that runs voice-input\n"
    "  (your terminal, or Voice Input.app) under the listed section, enable it, then fully\n"
    "  quit and relaunch that app — a reopened window is not enough, the permission is\n"
    "  granted to the process at launch."
)
