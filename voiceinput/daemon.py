"""Daemon: hold hotkey -> record -> whisper -> paste at cursor."""

from __future__ import annotations

import argparse
import queue
import signal
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

from . import (
    audio, cleanup, config, hotkeys, inject, permissions, server, streaming, text,
    transcribe,
)

LOG_DIR = Path.home() / ".local" / "state" / "voice-input"
# macOS-conventional app log. The .app (py2app) has no console, so a file sink is
# the only way its state is observable; also shows up in Console.app.
APP_LOG = Path.home() / "Library" / "Logs" / "voice-input.log"


def augment_path() -> None:
    """A .app launched via `open` inherits a minimal PATH (/usr/bin:/bin:…) without
    Homebrew, so `whisper-server` and `claude` aren't found. Prepend the dirs they
    live in, once, so shutil.which/subprocess resolve them from inside the bundle."""
    import os

    extra = ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local" / "bin")]
    parts = os.environ.get("PATH", "").split(os.pathsep)
    for directory in reversed(extra):
        if directory not in parts:
            parts.insert(0, directory)
    os.environ["PATH"] = os.pathsep.join(parts)


def log(message: str) -> None:
    line = f"[voice-input] {message}"
    # stderr, so `--transcribe` can be piped without the chatter
    print(line, file=sys.stderr, flush=True)
    try:
        APP_LOG.parent.mkdir(parents=True, exist_ok=True)
        # Explicit utf-8: the .app runs under a POSIX locale where the default
        # codec is ASCII, and a non-ASCII message would silently vanish here.
        with APP_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def chime(cfg: dict, which: str) -> None:
    sound = cfg["sound"]
    if not sound["enabled"]:
        return
    path = sound.get(which)
    if not path or not Path(path).exists():
        return
    try:
        subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


class Daemon:
    def __init__(self, cfg: dict, spawn_server: bool = True, on_status=None,
                 on_partial=None):
        self.cfg = cfg
        self.spawn_server = spawn_server and cfg["server"]["spawn"]
        self.url = config.server_url(cfg)
        self.jobs: queue.Queue = queue.Queue()
        self.server_proc: subprocess.Popen | None = None
        self.mic: audio.MicStream | None = None
        self.listener = None
        self.watcher: hotkeys.ComboWatcher | None = None
        self.worker: threading.Thread | None = None
        self.mode = "raw"
        self.stopping = threading.Event()
        # Coarse lifecycle state the menu-bar app polls for its glyph. Plain string
        # write is atomic under the GIL, so threads can set it without a lock.
        self.state = "starting"
        self.on_status = on_status
        self.on_partial = on_partial
        # Live-preview state, polled by the HUD. Atomic-string writes, no lock.
        self.partial_text = ""
        self.preview_url: str | None = None
        self.preview_enabled = False
        self.preview_proc: subprocess.Popen | None = None
        self.preview_stop: threading.Event | None = None
        self.preview_thread: threading.Thread | None = None

    def set_state(self, state: str) -> None:
        self.state = state
        if self.on_status is not None:
            try:
                self.on_status(state)
            except Exception:
                pass

    # ---- lifecycle -------------------------------------------------------

    def ensure_server(self) -> bool:
        if server.is_up(self.url):
            log(f"whisper-server already running at {self.url}")
            return True
        if not self.spawn_server:
            log(f"no whisper-server at {self.url} and spawning is disabled")
            return False

        model = config.model_path(self.cfg)
        if not model.is_file():
            log(f"model not found: {model}")
            return False

        log_path = LOG_DIR / "whisper-server.log"
        log(f"starting whisper-server ({model.name}); first run compiles Metal shaders")
        self.server_proc = server.spawn(self.cfg, log_path)
        if not server.wait_until_up(
            self.url, self.cfg["server"]["startup_timeout_s"], self.server_proc
        ):
            log(f"whisper-server failed to come up — see {log_path}")
            return False
        log(f"whisper-server ready at {self.url}")
        return True

    def ensure_preview_server(self) -> bool:
        """Bring up the small fast model on its own port for live partials.

        Best-effort: if the preview model is missing or the server won't start,
        live preview is silently disabled and dictation still works fully.
        """
        if not self.cfg["preview"]["enabled"]:
            return False
        view = config.preview_server_cfg(self.cfg)
        model = config.model_path(view)
        url = config.server_url(view)
        if not model.is_file():
            log(f"preview model not found ({model}); live preview disabled")
            return False
        if server.is_up(url):
            self.preview_url = url
            return True
        if not self.spawn_server:
            log("no preview whisper-server and spawning disabled; live preview off")
            return False
        log_path = LOG_DIR / "whisper-server-preview.log"
        log(f"starting preview whisper-server ({model.name})")
        self.preview_proc = server.spawn(view, log_path)
        if not server.wait_until_up(url, view["server"]["startup_timeout_s"],
                                    self.preview_proc):
            log("preview whisper-server failed to come up; live preview disabled")
            return False
        self.preview_url = url
        log(f"preview whisper-server ready at {url}")
        return True

    def shutdown(self, *_args) -> None:
        if self.stopping.is_set():
            return
        self.stopping.set()
        log("shutting down")
        self.stop_preview()
        if self.listener is not None:
            self.listener.stop()
        if self.mic is not None:
            self.mic.close()
        for proc in (self.server_proc, self.preview_proc):
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self.state = "stopped"

    # ---- live preview ----------------------------------------------------

    def preview_decode(self, pcm) -> str:
        """Fast partial transcript from the preview (base) model."""
        try:
            wav = audio.to_wav(pcm, self.cfg["audio"]["samplerate"])
            raw = transcribe.transcribe(
                wav, self.preview_url, language=self.cfg["language"],
                audio_ctx=transcribe.FULL_CONTEXT, timeout=15,
            )
            return text.clean(raw)
        except Exception as exc:
            log(f"preview decode failed: {exc}")
            raise

    def emit_partial(self, partial: str) -> None:
        # A preview thread that outlived its join timeout may still emit one late
        # partial; only accept them while an actual take is in progress.
        if self.state != "recording":
            return
        self.partial_text = partial
        if self.on_partial is not None:
            try:
                self.on_partial(partial)
            except Exception:
                pass

    def start_preview(self) -> None:
        if not self.preview_enabled:
            return
        # Reap a previous thread that outlived its stop_preview() join timeout,
        # so two streamers never run at once.
        if self.preview_thread is not None and self.preview_thread.is_alive():
            self.preview_thread.join(timeout=2.0)
        self.preview_stop = threading.Event()
        streamer = streaming.PreviewStreamer(
            snapshot=self.mic.snapshot,
            decode=self.preview_decode,
            emit=self.emit_partial,
            interval_ms=self.cfg["preview"]["interval_ms"],
            samplerate=self.cfg["audio"]["samplerate"],
        )
        self.preview_thread = threading.Thread(
            target=streamer.run, args=(self.preview_stop,), daemon=True
        )
        self.preview_thread.start()

    def stop_preview(self) -> None:
        if self.preview_stop is not None:
            self.preview_stop.set()
        if self.preview_thread is not None:
            self.preview_thread.join(timeout=1.0)
            if self.preview_thread.is_alive():
                # Still mid-decode; keep the reference so start_preview can reap it.
                self.preview_stop = None
                return
        self.preview_thread = None
        self.preview_stop = None

    # ---- release watchdog ------------------------------------------------

    def start_release_watchdog(self) -> None:
        """Backstop for dropped key-up events: macOS occasionally swallows the
        release of a fast/gentle tap, which would leave the take (and the HUD)
        running forever. While recording, poll the OS for the combo's physical
        key state and force a STOP when the keys are actually up."""
        threading.Thread(target=self._release_watchdog, daemon=True).start()

    def _release_watchdog(self) -> None:
        hk = self.cfg["hotkey"]
        tokens = hotkeys.combo_tokens(hk["modifiers"], hk["key"])
        up_polls = 0
        while self.state == "recording" and not self.stopping.is_set():
            time.sleep(0.15)
            if self.state != "recording":
                return
            try:
                down = hotkeys.combo_physically_down(
                    tokens, hotkeys.quartz_flags(), hotkeys.quartz_key_state)
            except Exception:
                return                 # Quartz unavailable; never break the take
            if down is None:
                return                 # unmapped custom key; watchdog unavailable
            if down:
                up_polls = 0
                continue
            # Debounced: require several consecutive "up" reads before forcing a
            # stop, so one glitchy poll can't truncate a live take.
            up_polls += 1
            if up_polls < 3:
                continue
            log("key release event was dropped — watchdog stopping the take")
            if self.watcher is not None:
                self.watcher.reset()
            self.on_event(hotkeys.STOP)
            return

    # ---- hotkey events ---------------------------------------------------

    def on_event(self, event: str) -> None:
        # Guarded: this runs on the pynput listener thread; an escaped exception
        # would kill hotkey handling for the rest of the session.
        try:
            if event in (hotkeys.START, hotkeys.START_CLEANUP):
                self.mode = "cleanup" if event == hotkeys.START_CLEANUP else "raw"
                self.partial_text = ""
                self.set_state("recording")
                self.mic.start()               # on-demand: opens the mic now
                self.start_preview()
                self.start_release_watchdog()
                chime(self.cfg, "start")
                log(f"recording ({self.mode})…")
            elif event == hotkeys.STOP:
                self.stop_preview()
                pcm = self.mic.stop()          # closes the mic
                self.jobs.put((pcm, self.mode))
            elif event == hotkeys.CANCEL:
                self.stop_preview()
                self.mic.stop()                # discard the take
                self.set_state("idle")
                log("cancelled — nothing pasted")
        except Exception as exc:
            chime(self.cfg, "error")
            self.set_state("error")
            log(f"recording error: {exc}")

    # ---- worker ----------------------------------------------------------

    def work(self) -> None:
        while not self.stopping.is_set():
            try:
                pcm, mode = self.jobs.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                self.handle(pcm, mode)
            except Exception as exc:
                chime(self.cfg, "error")
                log(f"error: {exc}")

    def speech_to_text(self, wav: bytes, duration_s: float) -> str:
        """Transcribe, redoing at full context if the reduced one made whisper stutter."""
        ctx = self.cfg["audio"]["context"]
        if ctx != transcribe.FULL_CONTEXT and duration_s > transcribe.max_safe_duration(ctx):
            log(f"{duration_s:.1f}s exceeds what audio_ctx={ctx} covers; using full context")
            ctx = transcribe.FULL_CONTEXT

        raw = transcribe.transcribe(
            wav, self.url, language=self.cfg["language"], audio_ctx=ctx
        )
        spoken = text.clean(raw)
        if ctx != transcribe.FULL_CONTEXT and text.has_repetition_loop(spoken):
            log(f"repetition at audio_ctx={ctx}; redoing at full context")
            raw = transcribe.transcribe(
                wav, self.url, language=self.cfg["language"],
                audio_ctx=transcribe.FULL_CONTEXT,
            )
            spoken = text.clean(raw)
        return spoken

    def handle(self, pcm, mode: str) -> None:
        sr = self.cfg["audio"]["samplerate"]
        length = audio.duration_ms(pcm, sr)
        if length < self.cfg["audio"]["min_ms"]:
            self.set_state("idle")   # else the HUD, which mirrors state, never hides
            log(f"ignored {length:.0f}ms tap")
            return

        if not pcm.any():
            chime(self.cfg, "error")
            self.set_state("error")
            log("microphone returned pure silence — Microphone permission is missing "
                "(run --doctor)")
            return

        self.set_state("working")
        started = time.monotonic()
        spoken = self.speech_to_text(audio.to_wav(pcm, sr), length / 1000)
        if not spoken:
            chime(self.cfg, "error")
            self.set_state("error")
            log(f"nothing recognised in {length / 1000:.1f}s of audio")
            return

        if mode == "cleanup":
            spoken, error = cleanup.polish(spoken, self.cfg)
            if error:
                log(f"cleanup failed ({error}) — pasting raw transcript")

        inject.inject(spoken, self.cfg)
        chime(self.cfg, "done")
        self.set_state("idle")
        log(f"{length / 1000:.1f}s audio -> {time.monotonic() - started:.1f}s: {spoken}")

    # ---- run -------------------------------------------------------------

    def combo_labels(self) -> tuple[str, str]:
        """(dictate, dictate+cleanup) hotkey strings for display."""
        hk = self.cfg["hotkey"]
        dictate = hotkeys.describe(hk["modifiers"], hk["key"])
        cleanup = hotkeys.describe(hk["modifiers"], hk["key"], hk["cleanup_modifier"])
        return dictate, cleanup

    def start_background(self, install_signals: bool = False) -> bool:
        """Bring up server, mic, hotkeys and worker without blocking or looping.

        Everything runs on its own thread (pynput listener, worker, PortAudio
        callback), so the caller keeps its thread free — that's what lets the
        menu-bar app own the main Cocoa run loop. `install_signals` is only safe
        from the main thread, so the CLI sets it and the menu-bar app does not.
        """
        # Without Accessibility the listener starts and simply never fires, which
        # looks identical to a broken hotkey. Refuse instead of pretending to work.
        if permissions.has_accessibility() is False:
            log("Accessibility permission missing — global hotkeys would never fire.")
            log(permissions.SETTINGS_HELP)
            log("run --doctor after granting it")
            self.set_state("error")
            return False

        if not self.ensure_server():
            self.set_state("error")
            return False

        self.preview_enabled = self.ensure_preview_server()
        log(f"live preview {'on' if self.preview_enabled else 'off'} (HUD partials)")

        self.mic = audio.MicStream(
            samplerate=self.cfg["audio"]["samplerate"],
            device=self.cfg["audio"]["device"],
            max_s=self.cfg["audio"]["max_s"],
        )
        try:
            self.mic.probe()   # opens+closes once to raise the mic-permission prompt early
        except Exception as exc:
            log(f"microphone unavailable: {exc}")
            log("grant Microphone permission in System Settings > Privacy > Microphone")
            self.set_state("error")
            self.shutdown()
            return False

        watcher = hotkeys.ComboWatcher(
            self.cfg["hotkey"]["modifiers"],
            self.cfg["hotkey"]["key"],
            self.cfg["hotkey"]["cleanup_modifier"],
            cancel_key=self.cfg["hotkey"].get("cancel_key", "backspace"),
        )
        self.watcher = watcher
        self.listener = hotkeys.listen(watcher, self.on_event)

        if install_signals:
            signal.signal(signal.SIGINT, self.shutdown)
            signal.signal(signal.SIGTERM, self.shutdown)

        self.worker = threading.Thread(target=self.work, daemon=True)
        self.worker.start()

        combo, cleanup_combo = self.combo_labels()
        log(f"ready — hold {combo} to dictate, {cleanup_combo} to dictate + clean up")
        self.set_state("idle")
        return True

    def run(self) -> int:
        if not self.start_background(install_signals=True):
            self.shutdown()
            return 1
        log("ctrl+c here to quit")
        try:
            while not self.stopping.is_set():
                time.sleep(0.2)
                if self.listener is not None and not self.listener.running:
                    log("hotkey listener died — is Accessibility permission granted?")
                    break
        finally:
            self.shutdown()
        return 0


def doctor(cfg: dict) -> int:
    """Check every prerequisite and say exactly which one is missing."""
    import shutil

    ok = True

    def report(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        print(f"  {'PASS' if passed else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")

    print("voice-input doctor\n")

    model = config.model_path(cfg)
    report("whisper model", model.is_file(), str(model))
    if cfg["preview"]["enabled"]:
        pv_model = config.model_path(config.preview_server_cfg(cfg))
        report("preview model (live HUD)", pv_model.is_file(),
               str(pv_model) + ("" if pv_model.is_file() else ", live preview disabled"))
    report("whisper-server binary", shutil.which(cfg["server"]["binary"]) is not None)
    report("clipboard tools", all(shutil.which(t) for t in ("pbcopy", "pbpaste")))
    report("claude CLI (cleanup hotkey)", shutil.which(cfg["cleanup"]["claude_bin"]) is not None,
           "optional")

    # macOS hands back a working stream full of zeros when the grant is missing, so
    # silence — not an exception — is the real signal.
    try:
        peak = permissions.microphone_peak(cfg)
        report("microphone (Privacy > Microphone)", peak > 0,
               f"peak={peak}" + ("" if peak > 0 else ", i.e. digital silence"))
    except Exception as exc:
        report("microphone (Privacy > Microphone)", False, str(exc))

    trusted = permissions.has_accessibility()
    report("global hotkeys (Privacy > Accessibility)", trusted is not False,
           "process is not trusted, hotkeys will never fire" if trusted is False else "")

    can_post = permissions.can_post_events()
    report("paste at cursor (Privacy > Accessibility)", can_post is not False,
           "cannot synthesise Cmd+V" if can_post is False else "")

    report("whisper-server reachable", server.is_up(config.server_url(cfg)),
           "not running is fine — the daemon starts it")

    print()
    print("all good — run ./scripts/voice-input" if ok
          else "fix the FAIL lines above.\n  " + permissions.SETTINGS_HELP)
    return 0 if ok else 1


def list_devices() -> int:
    import sounddevice as sd

    print(sd.query_devices())
    return 0


def transcribe_file(path: str, cfg: dict, spawn_server: bool) -> int:
    """Headless path: transcribe a wav without touching mic or hotkeys."""
    daemon = Daemon(cfg, spawn_server=spawn_server)
    if not daemon.ensure_server():
        return 1
    try:
        wav = Path(path).read_bytes()
        with wave.open(path) as handle:
            duration_s = handle.getnframes() / handle.getframerate()
        print(daemon.speech_to_text(wav, duration_s))
        return 0
    finally:
        daemon.shutdown()


def main(argv: list[str] | None = None) -> int:
    augment_path()
    parser = argparse.ArgumentParser(prog="voice-input", description=__doc__)
    parser.add_argument("--no-spawn", action="store_true",
                        help="use an already-running whisper-server instead of starting one")
    parser.add_argument("--list-devices", action="store_true", help="list input devices and exit")
    parser.add_argument("--doctor", action="store_true",
                        help="check model, permissions and dependencies, then exit")
    parser.add_argument("--transcribe", metavar="WAV",
                        help="transcribe a wav file and exit (no mic, no hotkeys)")
    parser.add_argument("--model", help="override the ggml model path")
    parser.add_argument("--language", help="override language (default: auto)")
    parser.add_argument("--port", type=int, help="override whisper-server port")
    args = parser.parse_args(argv)

    if args.list_devices:
        return list_devices()

    overrides: dict = {}
    if args.model:
        overrides["model"] = args.model
    if args.language:
        overrides["language"] = args.language
    if args.port:
        overrides["server"] = {"port": args.port}
    cfg = config.load(overrides)

    if args.doctor:
        return doctor(cfg)

    if args.transcribe:
        return transcribe_file(args.transcribe, cfg, spawn_server=not args.no_spawn)

    return Daemon(cfg, spawn_server=not args.no_spawn).run()


if __name__ == "__main__":
    sys.exit(main())
