#!/usr/bin/env bash
# Remove the voice-input LaunchAgent (does not touch the .app or your TCC grants).
set -euo pipefail

LABEL="studio.arag.voice-input"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "removed $PLIST (if it existed). The app itself is untouched."
echo "quit the running app from its menu-bar icon if it's still up."
