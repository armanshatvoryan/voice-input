# voice-input

Hold a hotkey, talk, release — the text lands at your cursor in whatever app is
focused. Runs entirely on-device via `whisper.cpp`. No API key, no cloud, no
per-minute cost.

```
HOLD  right-⌥  ─────────────────────►  release
      [mic opens]                      [final whisper decode]
      live transcript in a HUD              ↓
      while you hold                 pasted at the cursor
```

- **Hold right-Option (⌥)** — dictate; a floating HUD shows a live transcript as you
  speak, and the accurate final text is pasted when you release.
- **Right-Option + Shift** — same, then run it through `claude -p` to fix punctuation
  and drop filler before pasting (+2–4s).

Auto-detects language per utterance. English and Russian both verified verbatim.
The left Option key is left alone, so you can still type special characters with it.
Prefer a chord? Set `[hotkey] modifiers = ["ctrl","alt"]`, `key = "space"`.

## Setup

```bash
./scripts/setup.sh      # brew deps + venv + model (~1.6 GB on first run)
./scripts/voice-input --doctor
```

### Permissions (the part that actually bites)

macOS does not error when these are missing — it fails *silently*. The mic returns
digital silence, hotkeys never fire, `⌘V` posts into the void. `--doctor` checks
each one against the OS rather than guessing:

| Permission | Needed for | Symptom when missing |
|---|---|---|
| **Microphone** | recording | transcribes nothing, every take is silent |
| **Accessibility** | global hotkey + paste | hotkey does nothing at all |

Grant both to **the terminal app you launch the daemon from** (Terminal, iTerm,
Ghostty…), not to Python. Then **fully quit and reopen it** — permissions attach to
the process at launch, so a new window in the old process still has the old rights.

## Use

```bash
./scripts/voice-input                 # start the daemon, leave it running
./scripts/voice-input --doctor        # check model, deps, permissions
./scripts/voice-input --list-devices  # pick a mic for [audio] device
./scripts/voice-input --transcribe f.wav   # headless, prints to stdout
```

The daemon starts `whisper-server` itself and shuts it down on exit. The model
stays hot between utterances — that is the whole latency story. Point it at an
already-running server with `--no-spawn`.

Config lives in `config.toml`; copy it to `~/.config/voice-input/config.toml` to
override without touching the repo.

## Run as a menu-bar app (recommended)

Instead of parking a terminal, build a real `.app` bundle. The point is **identity**:
macOS grants Microphone / Accessibility to an app, and a terminal or a tmux server is
a fuzzy, shared, inheritable identity. A dedicated bundle is a stable one — grant it
once and it works everywhere, including apps that spawn shells over tmux (e.g. an
editor/cockpit whose panes would otherwise inherit some *other* app's grants).

```bash
./scripts/setup.sh                      # once, if you haven't
./scripts/build_app.sh                  # builds ~/Applications/voice-input.app
open ~/Applications/voice-input.app     # launches it (menu-bar glyph, no Dock icon)
```

Grant **Microphone** and **Accessibility** to **Voice Input** when prompted (or add it
in System Settings › Privacy & Security), then relaunch it once. The menu-bar glyph
tells you the state:

| glyph | meaning |
|---|---|
| `…` | starting (model loading) |
| `🎙` | listening — hold the hotkey to dictate |
| `🔴` | recording |
| `✦` | transcribing |
| `⚠︎` | problem — click **Run doctor…** |

The menu has Restart, **Run doctor…** (shows the same checks as the CLI), Open log, Quit.

### Autostart at login

```bash
./scripts/install_launchagent.sh        # opens the app at login, starts it now
./scripts/uninstall_launchagent.sh      # removes it (leaves the app + grants alone)
```

The LaunchAgent launches the app with `open`, so LaunchServices runs it as a normal
GUI app and the TCC grant still attaches to the **bundle** — launchd does not bypass
the app identity.

### Re-granting after a rebuild

The bundle is **ad-hoc signed** (no paid Developer ID). TCC keys the grant to the
bundle's code hash, so re-running `build_app.sh` changes the hash and you may have to
re-approve Microphone / Accessibility **once**. A Developer ID signature would make the
grant survive rebuilds; not worth it for a personal tool.

## Live preview (the HUD)

While you hold the key, a small caption bar at the bottom-center of the screen shows
a running transcript so you can *see* what's being heard. This is driven by a second,
smaller whisper model on its own server (`ggml-small`, port 8179) that decodes the
audio-so-far every ~400ms — fast enough to feel live, readable enough to trust. On
release, the **large** model does one accurate full re-decode and *that* is what gets
pasted; the partials are feedback only, never the final text.

Turn it off with `[preview] enabled = false` (one server, no HUD). `ggml-base` is
2× faster for partials but garbles words, so `small` is the default; if the preview
model is missing the app just disables the HUD and dictation still works.

## How it works

- **On-demand mic.** The input device is opened when you press the key and closed when
  you release, so the mic is genuinely off (no recording indicator) between takes.
  Cost is ~100–200ms of open latency — press, then start speaking a beat later.
- **Persistent server.** A cold `whisper-cli` pays ~2s of model load (plus a one-off
  ~15s Metal shader compile) *per utterance*. One warm server pays it once. Both the
  large (final) and small (preview) servers stay hot for the life of the daemon.
- **Clipboard paste, not synthetic typing.** One event instead of hundreds, and it
  survives Armenian/Russian text that per-character injection mangles under
  non-US layouts. Your previous clipboard is restored afterwards, unless you copied
  something else in the meantime.
- **`claude -p` over stdin.** The transcript never touches the command line — it is
  untrusted text and must not be able to shape a command.

## Performance

Measured on an M2 Air, `ggml-large-v3-turbo`, warm server:

| audio length | time to paste |
|---|---|
| 2.3s | ~1.4s |
| 5.8s | ~2.2s |
| 16.0s | ~2.2s |

Cost is roughly flat: whisper always encodes a padded 30s window, so a 2s clip
costs about what a 25s clip does. Decoder knobs (`best_of`, `beam_size`,
`no_fallback`) changed nothing measurable — the time is encoder-bound on the GPU.

## Why `audio_ctx` is off by default

Shrinking whisper's encoder context (`audio_ctx`) roughly halves latency and is the
usual advice for realtime use. **It was measured here and rejected as a default.**

It fails *silently* — wrong words, not an error — and not monotonically, so no
formula can pick a safe value. Same 5.8s clip, same server:

| audio_ctx | result |
|---|---|
| 512 | exact match |
| 576 | `"We group.com E.W to with. 89 E, I.My, Ad care"` |
| 640 | `"... ... ... ..."` (13.7s) |
| 704 | wrong |
| 768 | exact match |
| 832, 896 | wrong |
| 960, 1024, 1280 | exact match |

Note 896 is *larger* than the working 768 and still corrupts, so "bigger is safer"
does not hold. Undersizing also triggers whisper's repetition loop — a 9.7s clip at
512 returned `"Codort banana. Codort banana. Codort banana."` — and the retries make
it **slower** as well as wrong.

For a tool whose output goes straight into your documents, ~1s is not worth a class
of bug that quietly rewrites your words. Default is the full window.

If you dictate only short bursts you can opt in with `[audio] context = 512`. Only
512/768/1024/1280 were measured safe. Two guards stay on regardless: anything longer
than the value covers is auto-promoted to full context, and a repetition detector
redoes the take at full context if whisper stutters.

## Layout

```
voiceinput/
  daemon.py       wiring, dual-server boot, CLI, --doctor, PATH augmentation
  audio.py        on-demand mic capture + snapshot() for live preview
  hotkeys.py      hold-to-talk state machine (side-specific keys) + pynput adapter
  streaming.py    live-preview loop (sample → fast decode → emit), pure + testable
  hud.py          floating caption panel (AppKit, non-activating)
  transcribe.py   whisper-server HTTP client
  text.py         artifact stripping, repetition-loop detection
  inject.py       clipboard + ⌘V
  cleanup.py      claude -p pass
  permissions.py  macOS privacy probes
  server.py       whisper-server lifecycle
```

`pytest` — 74 tests, all pure logic (no mic, no server, no permissions needed).

## Troubleshooting

**Everything transcribes as nothing.** Microphone permission. `--doctor` reports the
actual peak sample; 0 means the grant is missing.

**Hotkey does nothing.** Accessibility, granted to the terminal app, then fully quit
and reopen it.

**The hotkey collides with something.** Change `[hotkey] modifiers` / `key`. Right
option reports as `alt_r` (some layouts `alt_gr`); both count as the right side.

**No live HUD while holding.** The preview model (`ggml-small`) is missing or
`[preview] enabled = false`. `--doctor` shows whether the preview model is found;
dictation works either way.

**Armenian is poor.** Expected — whisper's Armenian WER is high. Russian and English
are solid.

**Wrong mic.** `--list-devices`, then set `[audio] device = N`.
