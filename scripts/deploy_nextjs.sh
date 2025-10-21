#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_NAME="smart_answer"
WEB_DIR="${REPO_ROOT}/web"

if ! command -v pm2 >/dev/null 2>&1; then
  echo "[deploy] pm2 is not installed or not on PATH" >&2
  exit 1
fi

echo "[deploy] Switching to repository root: ${REPO_ROOT}"
cd "${REPO_ROOT}"

echo "[deploy] Fetching latest code"
git fetch --all --prune
git pull --ff-only

echo "[deploy] Installing dependencies and building Next.js app"
cd "${WEB_DIR}"
npm ci

if ! npm run build; then
  echo "[deploy] Build failed; skipping PM2 restart" >&2
  exit 1
fi

echo "[deploy] Restarting ${APP_NAME} via pm2"
if pm2 describe "${APP_NAME}" >/dev/null 2>&1; then
  pm2 restart "${APP_NAME}" --update-env
else
  pm2 start npm --name "${APP_NAME}" -- run start -- -p 3000
fi

echo "[deploy] Deployment complete"
