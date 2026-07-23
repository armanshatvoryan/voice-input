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

import rumps

from . import config, permissions
from .daemon import Daemon, doctor

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

        hk = self.cfg["hotkey"]
        combo = "+".join([*hk["modifiers"], hk["key"]])
        cleanup_combo = "+".join([*hk["modifiers"], hk["cleanup_modifier"], hk["key"]])

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
    VoiceInputApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
