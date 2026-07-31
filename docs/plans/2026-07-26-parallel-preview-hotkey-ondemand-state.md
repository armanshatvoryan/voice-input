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
