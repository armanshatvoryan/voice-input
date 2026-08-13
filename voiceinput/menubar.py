"""Menu-bar front end for the dictation daemon.

Runs as a tiny status glyph (no dock icon — the .app sets LSUIElement). rumps owns
the main Cocoa run loop; the daemon is brought up on a background thread and every
piece of it (hotkey listener, worker, audio callback) runs on its own thread, so
nothing here blocks the UI. The glyph is driven by polling `daemon.state` — no
cross-thread Cocoa calls, which would be unsafe.
"""

from __future__ import annotations

import contextlib
import io
import threading
import time

import rumps

from . import config, hotkeys, hud
from .daemon import Daemon, doctor, log

# Menu-bar glyph per daemon state. Kept to a single character — the menu bar is
# tiny and a wide emoji jitters the layout.
GLYPH = {
    "starting": "…",
    "idle": "🎙",
    "recording": "🔴",
    "working": "✦",
    "error": "⚠︎",
    "stopped": "○",
}

STATE_LABEL = {
    "starting": "Starting…",
    "idle": "Listening",
    "recording": "Recording…",
    "working": "Transcribing…",
    "error": "Problem — see Run doctor",
    "stopped": "Stopped",
}


class VoiceInputApp(rumps.App):
    def __init__(self):
        super().__init__("voice-input", title=GLYPH["starting"], quit_button=None)
        self.cfg = config.load()
        self.daemon: Daemon | None = None
        self._last_state = None
        self.hud = hud.CaptionHUD()
        self._hud_error = None
        self._hud_error_at = 0.0
        self._hud_want = None

        hk = self.cfg["hotkey"]
        combo = hotkeys.describe(hk["modifiers"], hk["key"])
        cleanup_combo = hotkeys.describe(hk["modifiers"], hk["key"], hk["cleanup_modifier"])

        self.status_item = rumps.MenuItem("Starting…")
        self.status_item.set_callback(None)  # non-clickable status line
        self.menu = [
            self.status_item,
            None,
            rumps.MenuItem(f"Dictate:  {combo}"),
            rumps.MenuItem(f"Clean up: {cleanup_combo}"),
            None,
            rumps.MenuItem("Restart", callback=self.on_restart),
            rumps.MenuItem("Run doctor…", callback=self.on_doctor),
            rumps.MenuItem("Open log", callback=self.on_open_log),
            None,
            rumps.MenuItem("Quit", callback=self.on_quit),
        ]
        # Two menu lines above are labels, not actions.
        self.menu["Dictate:  " + combo].set_callback(None)
        self.menu["Clean up: " + cleanup_combo].set_callback(None)

        self._boot()

    # ---- lifecycle -------------------------------------------------------

    def _boot(self) -> None:
        """(Re)create the daemon and bring it up off the main thread."""
        self.daemon = Daemon(self.cfg)
        threading.Thread(target=self.daemon.start_background,
                         kwargs={"install_signals": False}, daemon=True).start()

    @rumps.timer(0.3)
    def _refresh(self, _timer) -> None:
        state = self.daemon.state if self.daemon else "stopped"
        if state == self._last_state:
            return
        self._last_state = state
        self.title = GLYPH.get(state, "🎙")
        self.status_item.title = STATE_LABEL.get(state, state)

    # The HUD is AppKit; this timer runs on the main Cocoa thread, so it is the only
    # safe place to touch the panel. It just mirrors the daemon's live-preview state,
    # which the daemon writes from its own threads (plain string/bool, no lock).
    # Stateless on purpose: real panel visibility is re-asserted from daemon state
    # every tick (see CaptionHUD.sync), so no failure can strand the HUD for the
    # rest of the process — the 2026-08-01 incident.
    @rumps.timer(0.12)
    def _preview(self, _timer) -> None:
        d = self.daemon
        want = bool(d and d.preview_enabled and d.state in ("recording", "working"))
        if want != self._hud_want:
            # Two lines per take, forever: when the HUD wedges again (08-01,
            # 08-13), the log must already show whether the intent was there.
            self._hud_want = want
            log(f"HUD {'show' if want else 'hide'} (state={d.state if d else '-'})")
        try:
            self.hud.sync(want, d.partial_text if d else "")
        except Exception as exc:
            # The .app has no stderr; an unlogged timer exception is invisible.
            # Rate-limited so a persistent failure can't flood the log at 8Hz.
            now = time.monotonic()
            if repr(exc) != self._hud_error or now - self._hud_error_at > 60:
                self._hud_error = repr(exc)
                self._hud_error_at = now
                log(f"HUD error (retrying every tick): {exc!r}")

    # ---- menu actions ----------------------------------------------------

    def on_restart(self, _sender) -> None:
        if self.daemon is not None:
            self.daemon.shutdown()
        self.title = GLYPH["starting"]
        self._last_state = None
        self._boot()

    def on_doctor(self, _sender) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            doctor(self.cfg)
        rumps.alert(title="voice-input doctor", message=buffer.getvalue().strip(),
                    ok="Close")

    def on_open_log(self, _sender) -> None:
        import subprocess
        from .daemon import LOG_DIR

        log_path = LOG_DIR / "whisper-server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)
        subprocess.Popen(["open", str(log_path)])

    def on_quit(self, _sender) -> None:
        if self.daemon is not None:
            self.daemon.shutdown()
        rumps.quit_application()


def main() -> int:
    from .daemon import augment_path

    augment_path()   # .app PATH lacks Homebrew; needed before spawning whisper-server
    VoiceInputApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
