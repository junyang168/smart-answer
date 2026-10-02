#!/usr/bin/env bash
# Create the LaunchAgent for the reference-commentary scan inbox (WKP-F11.02).
#
# Run once on the production Mac, before the first deploy that contains the
# inbox. It only writes the plist; `scripts/deploy.sh` binds it to each release
# and loads it, the same way it handles the fellowship reminder.
#
# The plist carries no secrets: DATA_BASE_DIR and the Vertex service-account
# path are copied from the backend agent (both are paths); API keys stay in the
# release's .env, which the job loads itself.
#
#   scripts/install-reference-inbox-agent.sh [--inbox DIR] [--force]
set -Eeuo pipefail

LABEL="com.smart_answer.referencecommentaryinbox"
PLIST="${SMART_ANSWER_REFERENCE_INBOX_PLIST:-$HOME/Library/LaunchAgents/$LABEL.plist}"
BACKEND_PLIST="${SMART_ANSWER_BACKEND_PLIST:-$HOME/Library/LaunchAgents/com.smart_answer.fullarticleservice.plist}"
ACTIVE_RELEASE_FILE="${SMART_ANSWER_DEPLOY_ROOT:-/opt/homebrew/var/www/smart-answer-deploy}/active-release"
PLIST_BUDDY=/usr/libexec/PlistBuddy
INBOX="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Carson"
LOG_DIR="$HOME/Library/Logs/smart-answer"
INTERVAL=180
FORCE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --inbox) INBOX="$2"; shift 2 ;;
    --force) FORCE=true; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 64 ;;
  esac
done

fail() { printf 'install-reference-inbox-agent: %s\n' "$*" >&2; exit 1; }

[[ -f "$PLIST" && "$FORCE" != true ]] && fail "$PLIST exists; pass --force to rewrite it"
[[ -f "$BACKEND_PLIST" ]] || fail "backend LaunchAgent not found: $BACKEND_PLIST"
[[ -f "$ACTIVE_RELEASE_FILE" ]] || fail "no active release recorded at $ACTIVE_RELEASE_FILE"
release="$(<"$ACTIVE_RELEASE_FILE")"

env_from_backend() {
  "$PLIST_BUDDY" -c "Print :EnvironmentVariables:$1" "$BACKEND_PLIST" 2>/dev/null \
    || fail "backend LaunchAgent has no $1"
}
data_base_dir="$(env_from_backend DATA_BASE_DIR)"
credentials="$(env_from_backend GOOGLE_APPLICATION_CREDENTIALS)"

mkdir -p "$INBOX/Matthew" "$LOG_DIR"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$release/backend/.venv/bin/python3</string>
    <string>$release/backend/reference_commentary_inbox_job.py</string>
  </array>
  <key>WorkingDirectory</key><string>$release</string>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>DATA_BASE_DIR</key><string>$data_base_dir</string>
    <key>GOOGLE_APPLICATION_CREDENTIALS</key><string>$credentials</string>
    <key>REFERENCE_COMMENTARY_INBOX</key><string>$INBOX</string>
  </dict>
  <key>StandardOutPath</key><string>$LOG_DIR/reference-commentary-inbox.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/reference-commentary-inbox.log</string>
</dict>
</plist>
EOF
plutil -lint "$PLIST" >/dev/null || fail "generated plist is invalid: $PLIST"
printf 'wrote %s\n  inbox: %s\n  log:   %s\nThe next scripts/deploy.sh binds it to the release and loads it.\n' \
  "$PLIST" "$INBOX" "$LOG_DIR/reference-commentary-inbox.log"
