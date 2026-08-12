# voice-input — parallel preview + right-option + on-demand mic

Build started 2026-07-26. Repo: `~/voice-input` (canonical clone, `git push` direct).

## Locked spec (user-approved via AskUserQuestion)
1. **Hotkey** → hold **Right-Option (⌥ right, `alt_r`)** = dictate; `alt_r + shift` = dictate then `claude -p` tidy.
   - Cmd+` rejected (macOS window-cycle conflict).
2. **Mic** → **on-demand**: open PortAudio stream on hotkey-down, close on release. Drop always-open + 400ms preroll. (Privacy: mic off when idle. Cost: ~100-200ms open lag, accepted.)
3. **Live preview** ("parallel input, want to *see* it"):
   - **Bottom-center floating HUD** caption bar.
   - **400ms** refresh cadence.
   - **Dual-model**: `ggml-base.bin` (multilingual, ~148MB) on a 2nd whisper-server (port 8179) drives fast partials; `large-v3-turbo` (port 8178) does the accurate FINAL full re-decode pasted on release.
   - On release: **final full re-decode** (large model) is what gets pasted, not the last partial.

## Key design decisions
- **Right vs left option**: pynput aliases collapse `alt_l/alt_r`→`alt`. New `ComboWatcher` keeps side-specific tokens exact (`alt_r` matches only right, incl. `alt_gr` variant) while generic `alt` still matches either side (backward-compat for old combos + tests).
- **HUD thread-safety**: Cocoa UI must run on main thread. Reuse the existing glyph-polling pattern — daemon writes `partial_text`/state (atomic under GIL), menu-bar app polls via `@rumps.timer` on main thread and updates an AppKit `NSPanel` (non-activating, never steals focus so paste lands in the focused app). No cross-thread Cocoa calls.
- **Dual server**: keep both whisper-servers warm (servers don't touch mic, so "stop constant listening" is unaffected). Preview server optional via `[preview] enabled`.
- **CLI mode**: no HUD (no Cocoa); daemon emits partials via callback that the CLI ignores/logs.

## Files
- `voiceinput/hotkeys.py` — side-specific matching + `pretty()` label helper. [tests]
- `voiceinput/audio.py` — on-demand MicStream + `snapshot()`. [tests rewritten]
- `voiceinput/config.py` + `config.toml` — hotkey defaults → right-option, `[preview]` section, drop preroll.
- `voiceinput/server.py` — parametrize model/port for preview server.
- `voiceinput/streaming.py` (NEW) — pure cadence/loop logic. [tests]
- `voiceinput/hud.py` (NEW) — AppKit caption panel (menu-bar only, not unit-tested).
- `voiceinput/daemon.py` — dual-server boot, on-demand wiring, streaming thread, partial callback, labels.
- `voiceinput/menubar.py` — HUD poll+render, right-option labels.

## Status
- [x] base model downloaded; **base rejected (garbles words) → preview model = `ggml-small` (user-approved)**
- [x] hotkeys.py + tests (side-specific alt_r + describe())
- [x] audio.py + tests (on-demand + snapshot)
- [x] config + toml (right-option default, [preview]=small, preroll gone)
- [x] server.py preview (via config.preview_server_cfg — no server.py change needed)
- [x] streaming.py + tests (PreviewStreamer, adaptive cadence)
- [x] hud.py + frame test (AppKit non-activating panel)
- [x] daemon.py wiring (dual-server, on-demand, preview thread, partial state, PATH augment, labels)
- [x] menubar.py wiring (HUD poll timer, right-option labels)
- [x] full pytest green — **74 passed**
- [x] headless dual-model smoke GREEN: preview(small) + final(large) both correct, config-driven
- [x] menu-bar boot smoke GREEN (venv): alive, no import/attr errors, bailed at Accessibility as expected
- [ ] ONE .app rebuild (build_app.sh) → user re-grants Accessibility once (ad-hoc cdhash reset)
- [ ] USER live smoke: hold right-⌥, see HUD live text, release → accurate paste; ⌥+shift = cleanup
- [ ] commit + push (ask first)

## Robustness fix found mid-build
- **.app minimal PATH**: a bundle launched via `open` has no `/opt/homebrew/bin`, so `whisper-server` (esp. the NEW 2nd preview server) can't spawn — old app only worked by reusing a terminal-started server. Added `daemon.augment_path()` (prepends homebrew + ~/.local/bin), called in both entry points. Tested.
- **py2app lazy imports**: AppKit/Foundation/Quartz/ApplicationServices imported inside functions → added to setup_app.py `includes` so they're bundled.

## Finding 2026-07-26: base model too weak for a *readable* preview
Headless dual-model smoke (say→wav, both servers up):
- FINAL (large-v3-turbo): `'The Quick Brown Fox jumps over the Lazy Dog.'` ✓ 2.67s
- PREVIEW (base, auto): `'Зепвік Браунфокс…'` ✗ garbage (Cyrillic mis-ID) 0.73s
- base w/ `en` pinned: `'Zepvik Brown Fox Jump Sover…'` ✗ still garbled — it's base's ACOUSTIC accuracy, not just language auto-detect. base decode ~0.25s (very fast).
→ base gives an unreadable live preview (defeats the feature). Testing `small` (~488MB) as the preview model: better accuracy, still far faster than large. Cadence adapts (next_delay), so slightly-over-400ms is fine. **Plan value change (preview model base→small) — surface to user before locking.**

## Gotchas
- Rebuild changes cdhash → TCC grant drops → user re-grants Accessibility (batch all changes → 1 rebuild).
- Editing `voiceinput/*.py` needs venv reinstall to take effect in the .app (non-editable). `build_app.sh` reinstalls.
- Right-option may report `alt_gr` on some setups — matcher accepts both.

## Session 2026-08-01 — "HUD down again" (systematic-debugging)

### Done
- **Root-caused + fixed the duplicate-STOP race** (`voiceinput/daemon.py`, uncommitted).
  The release watchdog and a key-up that merely arrived *late* both ended the same take:
  the loser stopped an already-stopped mic (queuing a zero-length take — every phantom
  `ignored 0ms/6ms tap` in the log) and re-entered `stop_preview()` while the winner was
  parked in `join()`, clearing `preview_thread` under it →
  `'NoneType' object has no attribute 'is_alive'` → `on_event` caught it, set `state=error`,
  and skipped `mic.stop()` (leaking the InputStream into the next take).
  Fix = `Daemon.claim_take_end()` (first ender wins, flag reset on START) + `_preview_lock`
  around the preview-thread handover + local-ref read inside `stop_preview()`.
- **2 regression tests** (`tests/test_daemon.py`): `test_second_stop_of_a_take_is_ignored`,
  `test_two_threads_stopping_the_preview_do_not_race`. Both verified RED with only
  `voiceinput/daemon.py` reverted (`git stash push` on the single file). **86 passed.**
- Fix deployed into `/Applications/voice-input.app` via the `cp *.py` path; app restarted.

### HUD — root cause NOT found (do not mark this done)
Ruled out with evidence, not inspection:
- preview whisper-server healthy (:8179, 0.57s decode on a real wav); `preview_enabled=True`
- app main thread alive and rumps timers firing (`sample` → `__CFRunLoopDoTimers` → Python)
- exactly one daemon (one pynput listener thread + one worker thread) — not a stale daemon
  left over from a menu-bar Restart
- panel exists with correct geometry (900x60 @ x=285 y=776), alpha 1.0, single display
- live bundle sources byte-identical to repo HEAD before the fix

🔑 **Decisive measurement:** polling `CGWindowListCreateDescriptionFromArray` at 20 Hz, the
panel **never** became on-screen during `recording` periods lasting ≥0.45s. So
`orderFrontRegardless()` is never reached — this is not a rendering/level/space problem.

Leading hypothesis, UNCONFIRMED: `CaptionHUD.show()` raises *after* `_ensure()` has built the
panel. `_hud_visible = True` is assigned on the line *after* `show()` returns, so an exception
strands it `False`, and every subsequent 0.12s tick retries and re-raises. Sticky for the
process lifetime; a restart clears it — which matches the symptom being "down **again**".

🔑 A dead process cannot be autopsied here: no LaunchAgent, stderr goes nowhere, and the
unified log has nothing, so an uncaught rumps-timer traceback is lost. Capture it LIVE.

### Next (blocked on the human)
- [ ] Hold the chord ~6s on the instrumented build, then read `~/Library/Logs/voice-input.log`
      for `DIAG _preview …`, `DIAG show -> visible=… level=…`, `DIAG _preview raised: …`.
      Two 15-minute monitor windows elapsed with zero holds.
- [ ] 🔴 **Remove the temporary instrumentation.** `/Applications/voice-input.app/…/voiceinput/`
      `menubar.py` + `hud.py` carry `DIAG` logging that is NOT in the repo. Restore with
      `cp ~/voice-input/voiceinput/*.py <bundle>/…/voiceinput/ && rm -rf <bundle>/…/voiceinput/__pycache__`.
- [ ] Commit + push the race fix (left uncommitted this session — on `main`, so branch first).

---

## 2026-08-06 — mic wedge (PaErrorCode -9986) after a watchdog-ended take

Symptom: `--doctor` reported `FAIL microphone — Error opening InputStream: Internal PortAudio
error [PaErrorCode -9986]` and `FAIL whisper-server reachable`. Neither FAIL meant what the
doctor's printed advice said.

### Root cause (evidence, not inspection)
One `.app` process had been alive since Aug-5 (`ps -o etime` = 1d 02:27). Its log shows the
sequence: a successful 33.3s dictation → `key release event was dropped — watchdog stopping the
take` → from that point on **every** `InputStream` open in that process fails `-9986`, including
across menu-bar Restart cycles (Restart reuses the same process).

Ruled out:
- **Not TCC.** `log show --predicate 'subsystem == "com.apple.TCC" … kTCCServiceMicrophone'`
  over the failure window shows **zero** requests attributable to `studio.arag.voice-input`.
- **Not the device.** A *fresh* process (`.venv/bin/python`, `sd.rec` 1s @16kHz) opened the
  same default device (`0 Микрофон MacBook Air`) with no exception.

So: process-scoped PortAudio/CoreAudio state corruption that survives the daemon's own internal
restart. The `ac6ac0a` serialise-teardown fix does **not** close this path.

### Fix applied (recovery, not a code fix)
`kill <pid>` + `open -a /Applications/voice-input.app`. Verified after relaunch: boot mic probe
passes (no `microphone unavailable` line), `state=idle`, `ready` banner, both whisper-servers
answer 200 on :8178 and :8179. The second FAIL was benign — the daemon spawns the server.

### Hardening candidate — NOT built, needs approval
On `-9986`, re-exec the whole process (`os.execv`) instead of the internal daemon restart, since
the internal restart demonstrably cannot clear the wedged audio state.

### 🔑 Two traps that cost time here
- **`--doctor`'s mic check false-FAILs from any CC Cockpit / claude-code shell.** The responsible
  app resolves to `studio.arag.cc-cockpit`, whose Info.plist has no `NSMicrophoneUsageDescription`,
  so tccd logs `Refusing authorization request … without NSMicrophoneUsageDescription key` — no
  prompt, no Settings entry possible, no way to grant. Trust only the menu-bar "Run doctor…" or a
  real dictation. The printed SETTINGS_HELP is actively misleading in that context.
- **`open -a` on an already-running app is a no-op.** Check `ps -o etime` before believing a
  relaunch happened. (Also: zsh shadows `/usr/bin/log` with a builtin — `log show` returns
  "too many arguments"; call `/usr/bin/log` by absolute path.)

### HUD — one new data point
This session's log contains `DIAG show -> visible=True level=3` followed by
`DIAG _preview want=True hud_visible=True … state=recording` during the successful 33.3s take.
So on that take `show()` did **not** raise and the HUD path completed. Consistent with the
"sticky per-process after a failure" hypothesis; still not a root cause. Instrumentation is
still in the bundle — removal remains owed (see the Next list above).

---

## 2026-08-12 — -9986 ROOT CAUSE FOUND, FIXED, PROVEN (scripted, no live holds needed)

**The watchdog correlation was spurious.** Real mechanism, proven end-to-end:

1. PortAudio snapshots the CoreAudio device table (and default input) **once per process**
   (`Pa_Initialize` at sounddevice import).
2. The app booted while a Bluetooth mic was default input (unified log 13:20:28: input device 93,
   24 kHz, BT UID `30-82-16-A1-1B-86`).
3. The BT device disconnected mid-lifetime → CoreAudio deleted object 93.
4. Every later open resolved to the cached dead ID → HAL returned `kAudioHardwareBadObjectError`
   (`'!obj'` = 560947818, visible in unified log at wedge onset 14:14:50:
   `AudioObjectGetPropertyData: no object with given ID 93`) → PortAudio wraps any unknown
   OSStatus as **paInternalError -9986**. Forever, in that process.
5. Menubar Restart shares the process → same stale table → cannot cure. Fresh process
   re-enumerates → cures. Matches every prior observation, including zero tccd activity.
   The "0-frame takes" phase before full failure = half-dead BT route still listed but silent.

**Repro + proof (no human, no BT hardware):** `scripts/repro_9986_selfheal.py` — creates an
aggregate device wrapping the built-in mic, makes it the system default input, lets PortAudio
cache it, records a take, destroys the aggregate (the "AirPods disconnect"), records again.
Unfixed code: exact `PaErrorCode -9986`. Note: a *private* aggregate cannot become default
input (the set silently no-ops) — the harness asserts the default actually moved.

**Fix (audio.py):** `MicStream._open_with_retry()` — on any open failure, `sd._terminate()` +
`sd._initialize()` (re-reads the device table + default), retry once. Applied to both `start()`
and `probe()`. Safe because MicStream is the process's only PortAudio user and holds no open
stream at that point. 3 new unit tests (verified RED first); integration harness passes
(`SELF-HEAL PASS`); suite 88 green.

**Still open, unrelated to -9986:** HUD root cause; DIAG instrumentation still in the bundle.
