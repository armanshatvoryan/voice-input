#!/usr/bin/env bash
# Autostart voice-input.app at login via a per-user LaunchAgent.
#
# It launches the app with `open`, so LaunchServices runs it as a normal GUI app
# and TCC still attaches to the *bundle* — the LaunchAgent does not bypass the app
# identity. No KeepAlive: `open` exits once the app is up; the app keeps itself
# running, and relaunching `open` in a loop would be wrong.
set -euo pipefail

LABEL="studio.arag.voice-input"
APP="${1:-$HOME/Applications/voice-input.app}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

[[ -d "$APP" ]] || { echo "app not found: $APP — run ./scripts/build_app.sh first" >&2; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/open</string>
    <string>$APP</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/voice-input.launchagent.log</string>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/voice-input.launchagent.log</string>
</dict>
</plist>
PLIST

# Reload cleanly (modern bootstrap; bootout first so re-installs are idempotent).
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "installed LaunchAgent: $PLIST"
echo "it will open $APP at login, and just started it now."
echo "remove with: ./scripts/uninstall_launchagent.sh"
