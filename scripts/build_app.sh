#!/usr/bin/env bash
# Build voice-input.app with py2app — a self-contained bundle whose own signed
# executable loads Python in-process.
#
# Why not a shell wrapper: a launcher that `exec`s Homebrew Python makes the running
# process identity `org.python.python`, so a TCC (Accessibility / Microphone) grant
# on the bundle never applies — proven the hard way. py2app's bootstrap IS the bundle
# identity, so grants actually stick.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
BUNDLE_ID="studio.arag.voice-input"
APP_DEST="${1:-/Applications/voice-input.app}"

[[ -x "$PY" ]] || { echo "no venv at $VENV — run ./scripts/setup.sh first" >&2; exit 1; }

echo "installing build deps (menubar + py2app)…"
if command -v uv >/dev/null 2>&1; then
  (cd "$ROOT" && uv pip install -q ".[menubar]" py2app)
else
  (cd "$ROOT" && "$PY" -m pip install -q ".[menubar]" py2app)
fi

echo "building with py2app…"
cd "$ROOT"
rm -rf build dist
# py2app 0.28 errors on PEP 621 `[project]` metadata; hide pyproject for the build
# (the package is already installed in the venv, so py2app doesn't need it). Always
# restore it, even on failure.
moved=0
if [[ -f pyproject.toml ]]; then mv pyproject.toml .pyproject.toml.hidden; moved=1; fi
restore() { [[ $moved -eq 1 && -f .pyproject.toml.hidden ]] && mv .pyproject.toml.hidden pyproject.toml; }
trap restore EXIT
"$PY" setup_app.py py2app >/dev/null
restore; trap - EXIT

[[ -d dist/voice-input.app ]] || { echo "py2app did not produce dist/voice-input.app" >&2; exit 1; }

echo "signing (ad-hoc, id=$BUNDLE_ID)…"
codesign --force --deep --sign - --identifier "$BUNDLE_ID" dist/voice-input.app
codesign --verify --deep --strict dist/voice-input.app && echo "signature OK"

# Confirm the running-identity fix at build time: the executable must be the bundle,
# not org.python.python.
got_id=$(codesign -dvvv dist/voice-input.app/Contents/MacOS/voice-input 2>&1 | sed -n 's/^Identifier=//p')
[[ "$got_id" == "$BUNDLE_ID" ]] || { echo "identity check FAILED: got '$got_id'" >&2; exit 1; }
echo "executable identity = $got_id ✓"

echo "installing to $APP_DEST"
pkill -f "voice-input.app/Contents/MacOS" 2>/dev/null || true
sleep 1
rm -rf "$APP_DEST"
if ! cp -R dist/voice-input.app "$APP_DEST" 2>/dev/null; then
  echo "could not write $APP_DEST (permissions?) — leaving it in $ROOT/dist/voice-input.app" >&2
  APP_DEST="$ROOT/dist/voice-input.app"
fi

# Fresh cdhash → clear any stale TCC grant for this id so the re-grant is clean.
tccutil reset Accessibility "$BUNDLE_ID" >/dev/null 2>&1 || true
tccutil reset Microphone "$BUNDLE_ID" >/dev/null 2>&1 || true
tccutil reset ListenEvent "$BUNDLE_ID" >/dev/null 2>&1 || true

/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f "$APP_DEST" 2>/dev/null || true

echo
echo "built + installed: $APP_DEST"
echo "1) open \"$APP_DEST\""
echo "2) grant Microphone + Accessibility to \"voice-input\" (System Settings > Privacy),"
echo "   then quit + relaunch it once."
echo "Do NOT move the bundle after granting — a move changes its signature and drops the grant."
