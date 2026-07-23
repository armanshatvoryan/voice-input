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

MODEL="$HOME/.cache/whisper-cpp/ggml-large-v3-turbo.bin"
if [[ ! -f "$MODEL" ]]; then
  echo "downloading whisper large-v3-turbo (~1.6 GB)…"
  mkdir -p "$(dirname "$MODEL")"
  curl -L --fail -o "$MODEL" \
    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
fi

if command -v uv >/dev/null 2>&1; then
  uv venv --python 3.13 .venv
  uv pip install -r requirements.txt
else
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi

echo
echo "setup done. Start with:  ./scripts/voice-input"
echo "macOS will ask for Microphone and Accessibility permission for your terminal —"
echo "grant both, then restart the terminal."
