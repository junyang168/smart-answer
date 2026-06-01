#!/usr/bin/env bash
# Code deploy: pull -> backend (restart) -> frontend (build+restart).
# Ordered so the new API is live before the new UI serves pages that call it.
#
# Deploys CODE only. It does NOT rebuild the topic index and does NOT reindex
# search — those are data operations handled by scripts/build_topic_index.sh,
# run only when manuscript content or topics change.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PLIST="$HOME/Library/LaunchAgents/com.smart_answer.fullarticleservice.plist"
PM2_APP="smart-answer"
BACKEND_HEALTH="http://127.0.0.1:8555/sermon_search/status"
cd "$REPO_ROOT"

echo "==> Pulling latest main"
git pull --ff-only origin main

# ---- Backend first: the new API must exist before the new UI calls it ----
echo "==> Reloading backend service"
launchctl unload "$BACKEND_PLIST" 2>/dev/null || true
launchctl load "$BACKEND_PLIST"

echo "==> Waiting for backend health"
for _ in $(seq 1 30); do
  if curl -fsS -m 2 "$BACKEND_HEALTH" >/dev/null 2>&1; then echo "   backend healthy"; break; fi
  sleep 1
done

# ---- Frontend: install deps only if the lockfile changed, then build+restart ----
echo "==> Building frontend"
cd "$REPO_ROOT/web"
if ! git diff --quiet 'HEAD@{1}' HEAD -- package-lock.json 2>/dev/null; then
  echo "   package-lock.json changed -> npm ci"
  npm ci
fi
# '&&' guard: a failed build never reaches the restart, so the old build stays up.
npm run build && pm2 restart "$PM2_APP" --update-env

echo "==> Deploy complete"
