#!/usr/bin/env bash
# Build voice-input.app — a real macOS bundle so TCC (Microphone / Accessibility)
# attaches to *this app's identity* instead of to a terminal or a tmux server.
#
# It is a thin wrapper: the bundle's executable launches the repo venv running the
# menu-bar app. Not self-contained (needs this repo + its .venv), which is exactly
# right for a personal, single-machine tool — it sidesteps the py2app / native-ext
# packaging swamp while still giving a stable, grantable app identity.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
BUNDLE_ID="studio.arag.voice-input"
APP_DEST="${1:-$HOME/Applications/voice-input.app}"
VERSION="0.1.0"

[[ -x "$PY" ]] || { echo "no venv at $VENV — run ./scripts/setup.sh first" >&2; exit 1; }

echo "ensuring rumps is installed…"
if command -v uv >/dev/null 2>&1; then
  (cd "$ROOT" && uv pip install -q rumps)
else
  "$PY" -m pip install -q rumps
fi

echo "building bundle at $APP_DEST"
rm -rf "$APP_DEST"
mkdir -p "$APP_DEST/Contents/MacOS" "$APP_DEST/Contents/Resources"

# --- Info.plist ---------------------------------------------------------------
# LSUIElement=1 keeps it out of the Dock (menu-bar only). NSMicrophoneUsageDescription
# is mandatory — without it macOS kills the process instead of showing the mic prompt.
cat > "$APP_DEST/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>voice-input</string>
  <key>CFBundleDisplayName</key><string>Voice Input</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>voice-input</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>voice-input records your voice so it can be transcribed locally on this Mac.</string>
</dict>
</plist>
PLIST

# --- launcher executable ------------------------------------------------------
# Runs the venv python against the repo (via PYTHONPATH, no install step needed).
# App stdout/stderr go to a user log for post-mortem.
cat > "$APP_DEST/Contents/MacOS/voice-input" <<LAUNCH
#!/bin/bash
export PYTHONPATH="$ROOT\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$PY" -m voiceinput.menubar >> "\$HOME/Library/Logs/voice-input.log" 2>&1
LAUNCH
chmod +x "$APP_DEST/Contents/MacOS/voice-input"

# --- ad-hoc code signature ----------------------------------------------------
# A stable identifier lets TCC remember the grant. Ad-hoc (-) signing keys the
# grant to the bundle's cdhash, so re-running this script changes the hash and you
# may have to re-approve Microphone/Accessibility once. That is the price of not
# paying for a Developer ID; documented in the README.
echo "signing (ad-hoc, id=$BUNDLE_ID)…"
codesign --force --deep --sign - --identifier "$BUNDLE_ID" "$APP_DEST"
codesign --verify --deep --strict "$APP_DEST" && echo "signature OK"

# Register with LaunchServices so `open` finds it immediately.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f "$APP_DEST" 2>/dev/null || true

echo
echo "built: $APP_DEST"
echo "first launch:  open \"$APP_DEST\""
echo "then grant Microphone + Accessibility to \"Voice Input\" when prompted (or in"
echo "System Settings > Privacy & Security), and relaunch it once."
