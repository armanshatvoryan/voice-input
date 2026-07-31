#!/usr/bin/env bash
# One-shot setup: deps, venv, model check.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1 — $2" >&2; exit 1; }; }
need brew "install Homebrew first"

for pkg in whisper-cpp portaudio; do
  brew list --formula "$pkg" >/dev/null 2>&1 || { echo "installing $pkg…"; brew install "$pkg"; }
done

CACHE="$HOME/.cache/whisper-cpp"
mkdir -p "$CACHE"
fetch_model() {  # name, size-label
  local dest="$CACHE/$1"
  [[ -f "$dest" ]] && return 0
  echo "downloading $1 ($2)…"
  curl -L --fail -o "$dest.part" \
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$1" && mv "$dest.part" "$dest"
}
fetch_model ggml-large-v3-turbo.bin "~1.6 GB — accurate final transcript"
# Preview model for the live HUD. Optional: if this is missing the app just disables
# the live preview and dictation still works.
fetch_model ggml-small.bin "~488 MB — fast live-preview partials"

# Install the package itself (non-editable, with the menubar extra) rather than just
# the requirements — the console entry points and the .app both import it from
# site-packages, which keeps them working regardless of the current git branch.
if command -v uv >/dev/null 2>&1; then
  uv venv --python 3.13 .venv
  uv pip install ".[menubar]"
else
  python3 -m venv .venv
  ./.venv/bin/pip install -q ".[menubar]"
fi

echo
echo "setup done. Start with:  ./scripts/voice-input"
echo "macOS will ask for Microphone and Accessibility permission for your terminal —"
echo "grant both, then restart the terminal."
