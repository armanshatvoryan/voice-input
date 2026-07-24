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

from . import audio, cleanup, config, hotkeys, inject, permissions, server, text, transcribe

LOG_DIR = Path.home() / ".local" / "state" / "voice-input"
# macOS-conventional app log. The .app (py2app) has no console, so a file sink is
# the only way its state is observable; also shows up in Console.app.
APP_LOG = Path.home() / "Library" / "Logs" / "voice-input.log"


def log(message: str) -> None:
    line = f"[voice-input] {message}"
    # stderr, so `--transcribe` can be piped without the chatter
    print(line, file=sys.stderr, flush=True)
    try:
        APP_LOG.parent.mkdir(parents=True, exist_ok=True)
        with APP_LOG.open("a") as fh:
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
    def __init__(self, cfg: dict, spawn_server: bool = True, on_status=None):
        self.cfg = cfg
        self.spawn_server = spawn_server and cfg["server"]["spawn"]
        self.url = config.server_url(cfg)
        self.jobs: queue.Queue = queue.Queue()
        self.server_proc: subprocess.Popen | None = None
        self.mic: audio.MicStream | None = None
        self.listener = None
        self.worker: threading.Thread | None = None
        self.mode = "raw"
        self.stopping = threading.Event()
        # Coarse lifecycle state the menu-bar app polls for its glyph. Plain string
        # write is atomic under the GIL, so threads can set it without a lock.
        self.state = "starting"
        self.on_status = on_status

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

    def shutdown(self, *_args) -> None:
        if self.stopping.is_set():
            return
        self.stopping.set()
        log("shutting down")
        if self.listener is not None:
            self.listener.stop()
        if self.mic is not None:
            self.mic.close()
        if self.server_proc is not None:
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
        self.state = "stopped"

    # ---- hotkey events ---------------------------------------------------

    def on_event(self, event: str) -> None:
        if event in (hotkeys.START, hotkeys.START_CLEANUP):
            self.mode = "cleanup" if event == hotkeys.START_CLEANUP else "raw"
            self.mic.start()
            chime(self.cfg, "start")
            self.set_state("recording")
            log(f"recording ({self.mode})…")
        elif event == hotkeys.STOP:
            pcm = self.mic.stop()
            self.jobs.put((pcm, self.mode))

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
        combo = "+".join([*self.cfg["hotkey"]["modifiers"], self.cfg["hotkey"]["key"]])
        cleanup_combo = "+".join(
            [*self.cfg["hotkey"]["modifiers"], self.cfg["hotkey"]["cleanup_modifier"],
             self.cfg["hotkey"]["key"]]
        )
        return combo, cleanup_combo

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

        self.mic = audio.MicStream(
            samplerate=self.cfg["audio"]["samplerate"],
            device=self.cfg["audio"]["device"],
            preroll_ms=self.cfg["audio"]["preroll_ms"],
            max_s=self.cfg["audio"]["max_s"],
        )
        try:
            self.mic.open()
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
        )
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
