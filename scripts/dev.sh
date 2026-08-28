#!/usr/bin/env bash
#
# Run this worktree's app on ports no other worktree will take.
#
#   scripts/dev.sh                 # this card's pair, in the foreground
#   scripts/dev.sh --staging       # 3003/8222, from the primary checkout on main
#   scripts/dev.sh status          # every dev server on this machine
#   scripts/dev.sh stop            # stop this worktree's pair
#   scripts/dev.sh stop --all      # stop every dev server this script started
#
# **The port is the card number.** `ops-124-dev-ports` gets web 3124 and api
# 9124; `wkp-149-health-view` gets 3149 and 9149. Six worktrees can run at once,
# nobody coordinates, and the URL says which card you are looking at.
#
# It used to be one hardcoded pair, 3003/8222, for every worktree. First to
# start won and the rest got a bare PID to go and kill. On 2026-08-21 the
# winner was `wkp-141-pipeline-orchestration`, whose pull request had merged two
# days earlier: the dev site served a branch that no longer existed for 43
# hours, and finding that out meant walking `lsof -d cwd` by hand. Hence both
# halves of this script -- ports that cannot collide, and a conflict message
# that names the worktree, the branch, and whether it has already merged.
#
# API ports are 9xxx, not 8xxx: 8000 (legacy backend), 8008, 8222 and 8555
# (production) are all taken on this machine, and card #8 would have landed on
# 8008.
#
# nginx `:8888` proxies to 3003 (`holylogos_dev.conf`), so whatever holds that
# port is published. That pair is `--staging` only, and `--staging` refuses to
# run off main -- the public URL shows what is merged, never whoever started
# first.
set -Eeuo pipefail

if [[ -n "${SMART_ANSWER_DEV_REPO_ROOT:-}" ]]; then
  REPO_ROOT="$(cd "$SMART_ANSWER_DEV_REPO_ROOT" && pwd)"
else
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
BACKEND_PYTHON="$REPO_ROOT/backend/.venv/bin/python"
WEB_DIR="$REPO_ROOT/web"

STAGING_WEB_PORT=3003
STAGING_API_PORT=8222

#: Ports that belong to something else on this machine and must never be
#: derived onto. 3000 is production's pm2 app; 3003/8222 are staging's.
RESERVED_PORTS=(3000 3003 8222 8555 8000 8008)

#: Pidfiles live outside the worktree so `stop` and `status` work from any
#: shell, and outlive the worktree being deleted. Not /tmp: #77 is the ticket
#: about dev logs vanishing from there.
STATE_DIR="${SMART_ANSWER_DEV_STATE:-$HOME/.local/state/smart-answer-dev}"

fail() { printf 'dev: %s\n' "$*" >&2; exit 1; }

# -- who is on a port -------------------------------------------------------

# `|| true` is load-bearing: lsof exits 1 when nothing is listening, and under
# `set -o pipefail` that failure propagates out of the command substitution and
# kills the script -- silently, in the middle of printing a status table.
pid_on_port() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1 || true
}

cwd_of_pid() {
  lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1 || true
}

# The whole point of the message: not a PID, but which card is in the way and
# whether anyone still needs it.
describe_port() {
  local port="$1" pid cwd branch state
  pid="$(pid_on_port "$port")"
  [[ -n "$pid" ]] || { printf '  %s  free\n' "$port"; return; }
  cwd="$(cwd_of_pid "$pid")"
  branch=""
  [[ -n "$cwd" ]] && branch="$(git -C "$cwd" branch --show-current 2>/dev/null || true)"
  printf '  %s  pid %s' "$port" "$pid"
  [[ -n "$cwd" ]] && printf '  %s' "${cwd/#$HOME/~}"
  if [[ -n "$branch" ]]; then
    printf '  [%s]' "$branch"
    if command -v gh >/dev/null 2>&1 && [[ "$branch" != "main" ]]; then
      state="$(gh pr view "$branch" --json state -q .state 2>/dev/null || true)"
      [[ "$state" == "MERGED" ]] && printf '  ** already merged — safe to stop **'
    fi
  fi
  printf '\n'
}

# -- ports from the branch --------------------------------------------------

derive_ports() {
  local branch card
  branch="$(git -C "$REPO_ROOT" branch --show-current 2>/dev/null || true)"
  [[ -n "$branch" ]] || fail "detached HEAD: check out the card's branch, or use --staging"
  if [[ "$branch" == "main" ]]; then
    fail "on main — main is staging, so run: scripts/dev.sh --staging"
  fi
  [[ "$branch" =~ ^(wkp|ops)-([0-9]+)- ]] \
    || fail "branch '$branch' does not name a card; expected wkp-<n>-… or ops-<n>-…, or pass --web-port/--api-port"
  card="${BASH_REMATCH[2]}"
  (( card >= 1 && card <= 999 )) \
    || fail "card #$card is outside 1-999, so the port cannot be the card number; pass --web-port/--api-port"
  WEB_PORT=$((3000 + card))
  API_PORT=$((9000 + card))
  # Only card #3 can collide (3003), and epics are not branched from -- but a
  # silent landing on the staging port is exactly the failure this replaces.
  local reserved
  for reserved in "${RESERVED_PORTS[@]}"; do
    if (( WEB_PORT == reserved || API_PORT == reserved )); then
      WEB_PORT=$((WEB_PORT + 500))
      API_PORT=$((API_PORT + 500))
      printf 'dev: card #%s lands on a reserved port; shifted to %s/%s\n' "$card" "$WEB_PORT" "$API_PORT" >&2
      break
    fi
  done
  CARD="$card"
  BRANCH="$branch"
}

# -- subcommands ------------------------------------------------------------

do_status() {
  printf 'dev servers, by card:\n'
  local found=0 pidfile port
  shopt -s nullglob
  for pidfile in "$STATE_DIR"/*.pid; do
    port="$(basename "$pidfile" .pid)"
    if [[ -s "$pidfile" ]] && kill -0 "$(<"$pidfile")" 2>/dev/null; then
      describe_port "$port"
      found=1
    else
      rm -f "$pidfile"
    fi
  done
  shopt -u nullglob
  (( found )) || printf '  (none started by this script)\n'
  printf '\nstaging (nginx :8888 proxies the web port):\n'
  describe_port "$STAGING_WEB_PORT"
  describe_port "$STAGING_API_PORT"
}

stop_port() {
  local port="$1" pidfile="$STATE_DIR/$1.pid" pid cwd recorded_pid=""
  pid="$(pid_on_port "$port")"
  [[ -s "$pidfile" ]] && recorded_pid="$(<"$pidfile")"
  if [[ -n "$pid" ]]; then
    # Card-specific cleanup may kill only a process positively attributed to
    # this worktree. A port number or stale pidfile is not ownership proof.
    if (( STOP_ALL )); then
      if [[ -z "$recorded_pid" || "$recorded_pid" != "$pid" ]]; then
        printf 'dev: refusing to stop port %s: listener pid %s is not the recorded owner (%s)\n' \
          "$port" "$pid" "${recorded_pid:-none}" >&2
        return 1
      fi
    else
      cwd="$(cwd_of_pid "$pid")"
      if [[ -z "$cwd" || ( "$cwd" != "$REPO_ROOT" && "$cwd" != "$REPO_ROOT"/* ) ]]; then
        fail "refusing to stop port $port: pid $pid cwd '${cwd:-unknown}' is outside $REPO_ROOT"
      fi
    fi
    kill "$pid" 2>/dev/null || true
    printf 'dev: stopped %s (pid %s)\n' "$port" "$pid"
  fi
  rm -f "$pidfile"
}

do_stop() {
  if [[ "${1:-}" == "--all" ]]; then
    shopt -s nullglob
    local pidfile blocked=0
    for pidfile in "$STATE_DIR"/*.pid; do
      stop_port "$(basename "$pidfile" .pid)" || blocked=1
    done
    shopt -u nullglob
    return "$blocked"
  fi
  stop_port "$WEB_PORT"
  stop_port "$API_PORT"
}

# -- start ------------------------------------------------------------------

backend_pid=""
frontend_pid=""

cleanup() {
  trap - EXIT INT TERM
  local pid
  for pid in "$frontend_pid" "$backend_pid"; do
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && kill "$pid" 2>/dev/null || true
  done
  for pid in "$frontend_pid" "$backend_pid"; do
    [[ -n "$pid" ]] && wait "$pid" 2>/dev/null || true
  done
  rm -f "$STATE_DIR/$WEB_PORT.pid" "$STATE_DIR/$API_PORT.pid"
}

do_start() {
  [[ -x "$BACKEND_PYTHON" ]] \
    || fail "backend virtual environment not found: $BACKEND_PYTHON"
  command -v npm >/dev/null 2>&1 || fail "npm is not installed or not on PATH"
  [[ -d "$WEB_DIR/node_modules" ]] \
    || fail "frontend dependencies are missing. Run: cd \"$WEB_DIR\" && npm install"
  # Turbopack takes the working directory as its root and refuses a
  # node_modules that symlinks out of it, so a worktree whose dependencies were
  # linked cannot run `next dev` at all. `work-on.sh` clones it instead; this
  # says so rather than letting Turbopack fail with a message about filesystem
  # roots.
  [[ ! -L "$WEB_DIR/node_modules" ]] \
    || fail "web/node_modules is a symlink, which Turbopack rejects. Run: cd \"$WEB_DIR\" && mv node_modules node_modules.link && cp -al \"\$(readlink node_modules.link)\" node_modules && rm node_modules.link"

  local port
  for port in "$API_PORT" "$WEB_PORT"; do
    if [[ -n "$(pid_on_port "$port")" ]]; then
      printf 'dev: port %s is already in use:\n' "$port" >&2
      describe_port "$port" >&2
      printf "dev: stop it with 'scripts/dev.sh stop' in that worktree, or 'kill <pid>'\n" >&2
      exit 1
    fi
  done

  mkdir -p "$STATE_DIR"
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  cd "$REPO_ROOT"

  printf 'dev: %s  ·  web http://localhost:%s  ·  api http://127.0.0.1:%s\n' \
    "${LABEL}" "$WEB_PORT" "$API_PORT"

  "$BACKEND_PYTHON" -m uvicorn backend.api.main:app \
    --reload --host 127.0.0.1 --port "$API_PORT" \
    > >(tee -a "$STATE_DIR/$API_PORT.log") 2>&1 &
  backend_pid=$!
  echo "$backend_pid" > "$STATE_DIR/$API_PORT.pid"

  # Keep both frontend request paths on this worktree's backend:
  # `next.config.mjs` reads DEV_BACKEND_ORIGIN for /api proxies, while server
  # components and route handlers read the two service URL variables directly.
  # Leaving either path on the shared .env.local default silently connects the
  # page to staging (or to whichever process happens to own that port).
  (
    cd "$WEB_DIR"
    export DEV_BACKEND_ORIGIN="http://127.0.0.1:$API_PORT"
    export FULL_ARTICLE_SERVICE_URL="$DEV_BACKEND_ORIGIN"
    export SC_API_SERVICE_URL="$DEV_BACKEND_ORIGIN"
    export PORT="$WEB_PORT"
    exec npm run dev
  ) > >(tee -a "$STATE_DIR/$WEB_PORT.log") 2>&1 &
  frontend_pid=$!
  echo "$frontend_pid" > "$STATE_DIR/$WEB_PORT.pid"

  printf 'dev: Wang admin http://localhost:%s/admin/wang  ·  Ctrl+C stops both\n' "$WEB_PORT"

  while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
    sleep 1
  done
  kill -0 "$backend_pid" 2>/dev/null || printf 'dev: backend stopped unexpectedly; see %s\n' "$STATE_DIR/$API_PORT.log" >&2
  kill -0 "$frontend_pid" 2>/dev/null || printf 'dev: frontend stopped unexpectedly; see %s\n' "$STATE_DIR/$WEB_PORT.log" >&2
  exit 1
}

# -- arguments --------------------------------------------------------------

MODE=start
STAGING=0
WEB_PORT="" API_PORT="" CARD="" BRANCH="" LABEL=""
ALLOW_NON_MAIN=0
STOP_ALL=0
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    start|stop|status) MODE="$1"; shift ;;
    --staging)         STAGING=1; shift ;;
    --allow-non-main)  ALLOW_NON_MAIN=1; shift ;;
    --web-port)        WEB_PORT="${2:-}"; shift 2 ;;
    --api-port)        API_PORT="${2:-}"; shift 2 ;;
    -h|--help)         sed -n '3,10p' "$0"; exit 0 ;;
    *)                 ARGS+=("$1"); shift ;;
  esac
done

if (( STAGING )); then
  branch="$(git -C "$REPO_ROOT" branch --show-current 2>/dev/null || true)"
  if [[ "$branch" != "main" ]] && (( ! ALLOW_NON_MAIN )); then
    fail "--staging is served publicly on nginx :8888, so it runs main only (this is '$branch'). Use --allow-non-main to override."
  fi
  WEB_PORT="${WEB_PORT:-$STAGING_WEB_PORT}"
  API_PORT="${API_PORT:-$STAGING_API_PORT}"
  LABEL="staging [$branch]"
elif [[ -n "$WEB_PORT" && -n "$API_PORT" ]]; then
  LABEL="ports given on the command line"
elif [[ "$MODE" == "status" ]]; then
  : # status needs no ports of its own
else
  derive_ports
  [[ -n "$WEB_PORT" ]] || WEB_PORT=$((3000 + CARD))
  [[ -n "$API_PORT" ]] || API_PORT=$((9000 + CARD))
  LABEL="card #$CARD [$BRANCH]"
fi

mkdir -p "$STATE_DIR"
case "$MODE" in
  status) do_status ;;
  stop)
    [[ "${ARGS[0]:-}" == "--all" ]] && STOP_ALL=1
    do_stop "${ARGS[@]:-}"
    ;;
  start)  do_start ;;
esac
