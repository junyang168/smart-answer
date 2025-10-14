#!/usr/bin/env bash
set -euo pipefail

PLIST_PATH="$HOME/Library/LaunchAgents/com.smart_answer.fullarticleservice.plist"

if [[ ! -f "$PLIST_PATH" ]]; then
  echo "LaunchAgent not found: $PLIST_PATH" >&2
  exit 1
fi

launchctl unload "$PLIST_PATH"
launchctl load "$PLIST_PATH"

echo "Full article service reloaded."
