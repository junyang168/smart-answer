#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PYTHON="$REPO_ROOT/backend/.venv/bin/python"
WEB_DIR="$REPO_ROOT/web"
BACKEND_PORT=8222
FRONTEND_PORT=3003

backend_pid=""
frontend_pid=""

port_in_use() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

show_port_owner() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

cleanup() {
  trap - EXIT INT TERM

  if [[ -n "$frontend_pid" ]] && kill -0 "$frontend_pid" 2>/dev/null; then
    kill "$frontend_pid" 2>/dev/null || true
  fi
  if [[ -n "$backend_pid" ]] && kill -0 "$backend_pid" 2>/dev/null; then
    kill "$backend_pid" 2>/dev/null || true
  fi

  if [[ -n "$frontend_pid" ]]; then
    wait "$frontend_pid" 2>/dev/null || true
  fi
  if [[ -n "$backend_pid" ]]; then
    wait "$backend_pid" 2>/dev/null || true
  fi
}

if [[ ! -x "$BACKEND_PYTHON" ]]; then
  echo "Backend virtual environment not found: $BACKEND_PYTHON" >&2
  echo "Create it from backend/requirements.txt before starting development." >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is not installed or not available on PATH." >&2
  exit 1
fi

if [[ ! -d "$WEB_DIR/node_modules" ]]; then
  echo "Frontend dependencies are missing. Run: cd \"$WEB_DIR\" && npm install" >&2
  exit 1
fi

for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  if port_in_use "$port"; then
    echo "Development port $port is already in use:" >&2
    show_port_owner "$port" >&2
    echo "Stop that process and run this script again." >&2
    exit 1
  fi
done

trap cleanup EXIT
trap 'exit 130' INT TERM

cd "$REPO_ROOT"

echo "Starting backend: http://127.0.0.1:$BACKEND_PORT"
"$BACKEND_PYTHON" -m uvicorn backend.api.main:app \
  --reload \
  --host 127.0.0.1 \
  --port "$BACKEND_PORT" &
backend_pid=$!

echo "Starting frontend: http://localhost:$FRONTEND_PORT"
(
  cd "$WEB_DIR"
  exec npm run dev
) &
frontend_pid=$!

echo "Wang admin: http://localhost:$FRONTEND_PORT/admin/wang"
echo "Press Ctrl+C to stop both development servers."

while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  sleep 1
done

if ! kill -0 "$backend_pid" 2>/dev/null; then
  wait "$backend_pid" || backend_status=$?
  echo "Backend stopped unexpectedly (exit ${backend_status:-0})." >&2
fi

if ! kill -0 "$frontend_pid" 2>/dev/null; then
  wait "$frontend_pid" || frontend_status=$?
  echo "Frontend stopped unexpectedly (exit ${frontend_status:-0})." >&2
fi

exit 1
