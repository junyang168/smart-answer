#!/usr/bin/env bash
# Production deployment from one immutable Git commit.
#
# Release contents always come from `git archive <commit>`, so uncommitted and
# untracked files in the source checkout cannot leak into production.
set -Eeuo pipefail

SOURCE_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_ROOT="${SMART_ANSWER_DEPLOY_ROOT:-/opt/homebrew/var/www/smart-answer-deploy}"
RELEASES_DIR="$DEPLOY_ROOT/releases"
ACTIVE_RELEASE_FILE="$DEPLOY_ROOT/active-release"
LEGACY_RELEASE="${SMART_ANSWER_LEGACY_ROOT:-/opt/homebrew/var/www/smart-answer}"
BACKEND_PLIST="${SMART_ANSWER_BACKEND_PLIST:-$HOME/Library/LaunchAgents/com.smart_answer.fullarticleservice.plist}"
BACKEND_HEALTH="${SMART_ANSWER_BACKEND_HEALTH:-http://127.0.0.1:8555/healthz}"
FRONTEND_HEALTH="${SMART_ANSWER_FRONTEND_HEALTH:-http://127.0.0.1:3000/}"
PM2_APP="${SMART_ANSWER_PM2_APP:-smart-answer}"
PM2_CONFIG="$SOURCE_REPO/scripts/pm2.production.config.cjs"

TARGET_REF="origin/main"
DRY_RUN=false
ALLOW_NON_MAIN=false

usage() {
  cat <<'EOF'
Usage: scripts/deploy.sh [--ref GIT_REF] [--dry-run] [--allow-non-main]

Defaults to the exact commit currently at origin/main.

  --ref GIT_REF       Deploy a specific commit or remote ref.
  --dry-run           Resolve and validate without changing production.
  --allow-non-main    Permit a commit that is not contained in origin/main.

Examples:
  scripts/deploy.sh --dry-run
  scripts/deploy.sh
  scripts/deploy.sh --ref fcbfc5e --allow-non-main
EOF
}

while (($#)); do
  case "$1" in
    --ref)
      [[ $# -ge 2 ]] || { echo "deploy: --ref requires a value" >&2; exit 2; }
      TARGET_REF="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --allow-non-main)
      ALLOW_NON_MAIN=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "deploy: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

log() { printf '==> %s\n' "$*"; }
fail() { printf 'deploy: %s\n' "$*" >&2; exit 1; }

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

wait_for_health() {
  local name="$1" url="$2"
  local attempt
  for attempt in $(seq 1 30); do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      log "$name healthy: $url"
      return 0
    fi
    sleep 1
  done
  printf 'deploy: %s health check failed: %s\n' "$name" "$url" >&2
  return 1
}

set_backend_release() {
  local release="$1"
  /usr/libexec/PlistBuddy -c "Set :ProgramArguments:0 $release/backend/.venv/bin/python3" "$BACKEND_PLIST" \
    || return 1
  /usr/libexec/PlistBuddy -c "Set :WorkingDirectory $release" "$BACKEND_PLIST" \
    || return 1
}

restart_backend() {
  launchctl unload "$BACKEND_PLIST" 2>/dev/null || true
  launchctl load "$BACKEND_PLIST"
}

restart_frontend() {
  local release="$1"
  SMART_ANSWER_WEB_ROOT="$release/web" SMART_ANSWER_PM2_APP="$PM2_APP" \
    pm2 startOrRestart "$PM2_CONFIG" --only "$PM2_APP" --update-env
}

switch_services() {
  local release="$1"
  [[ -x "$release/backend/.venv/bin/python3" ]] || {
    printf 'deploy: backend runtime missing in %s\n' "$release" >&2
    return 1
  }
  [[ -d "$release/web/.next" ]] || {
    printf 'deploy: frontend build missing in %s\n' "$release" >&2
    return 1
  }

  set_backend_release "$release" || return 1
  restart_backend || return 1
  wait_for_health backend "$BACKEND_HEALTH" || return 1

  restart_frontend "$release" || return 1
  wait_for_health frontend "$FRONTEND_HEALTH" || return 1
}

rollback() {
  local previous="$1"
  [[ -n "$previous" && -d "$previous" ]] || {
    printf 'deploy: automatic rollback unavailable; no previous release recorded\n' >&2
    return 1
  }

  log "Rolling back to $previous"
  if switch_services "$previous"; then
    printf '%s\n' "$previous" > "$ACTIVE_RELEASE_FILE"
    log "Rollback complete"
    return 0
  fi
  printf 'deploy: rollback failed; manual intervention required\n' >&2
  return 1
}

for command_name in git tar python3 npm pm2 curl launchctl; do
  require_command "$command_name"
done
[[ -x /usr/libexec/PlistBuddy ]] || fail "PlistBuddy is unavailable"
[[ -f "$BACKEND_PLIST" ]] || fail "backend LaunchAgent not found: $BACKEND_PLIST"
[[ -f "$PM2_CONFIG" ]] || fail "PM2 config not found: $PM2_CONFIG"

log "Fetching Git refs"
git -C "$SOURCE_REPO" fetch --prune origin
TARGET_SHA="$(git -C "$SOURCE_REPO" rev-parse --verify "$TARGET_REF^{commit}")" \
  || fail "cannot resolve Git ref: $TARGET_REF"
MAIN_SHA="$(git -C "$SOURCE_REPO" rev-parse --verify 'origin/main^{commit}')"

if [[ "$ALLOW_NON_MAIN" != true ]] && ! git -C "$SOURCE_REPO" merge-base --is-ancestor "$TARGET_SHA" "$MAIN_SHA"; then
  fail "$TARGET_SHA is not contained in origin/main; merge it first or explicitly use --allow-non-main"
fi

RELEASE_DIR="$RELEASES_DIR/$TARGET_SHA"
PREVIOUS_RELEASE=""
if [[ -f "$ACTIVE_RELEASE_FILE" ]]; then
  PREVIOUS_RELEASE="$(<"$ACTIVE_RELEASE_FILE")"
elif [[ -d "$LEGACY_RELEASE" ]]; then
  PREVIOUS_RELEASE="$LEGACY_RELEASE"
fi

log "Deployment plan"
printf '   source ref:       %s\n' "$TARGET_REF"
printf '   source commit:    %s\n' "$TARGET_SHA"
printf '   release:          %s\n' "$RELEASE_DIR"
printf '   previous release: %s\n' "${PREVIOUS_RELEASE:-none}"
printf '   backend health:   %s\n' "$BACKEND_HEALTH"
printf '   frontend health:  %s\n' "$FRONTEND_HEALTH"

if [[ "$DRY_RUN" == true ]]; then
  log "Dry run complete; production was not changed"
  exit 0
fi

# The LaunchAgent currently carries production environment variables. Keep it
# private to the deployment account until those secrets move to a keychain or
# dedicated secret manager.
chmod 600 "$BACKEND_PLIST"

mkdir -p "$RELEASES_DIR"
if ! mkdir "$DEPLOY_ROOT/.deploy-lock" 2>/dev/null; then
  fail "another deployment appears to be running: $DEPLOY_ROOT/.deploy-lock"
fi
trap 'rmdir "$DEPLOY_ROOT/.deploy-lock" 2>/dev/null || true' EXIT

if [[ -d "$RELEASE_DIR" && ! -f "$RELEASE_DIR/.deploy-complete" ]]; then
  fail "incomplete release already exists; inspect and remove it manually: $RELEASE_DIR"
fi

if [[ ! -d "$RELEASE_DIR" ]]; then
  mkdir "$RELEASE_DIR"

  log "Exporting immutable source"
  git -C "$SOURCE_REPO" archive "$TARGET_SHA" | tar -x -C "$RELEASE_DIR"

  # Production configuration stays outside Git. These compatibility links use
  # the existing protected files until a dedicated secret store is introduced.
  for relative_env in .env backend/.env web/.env.local; do
    if [[ -f "$LEGACY_RELEASE/$relative_env" && ! -e "$RELEASE_DIR/$relative_env" ]]; then
      ln -s "$LEGACY_RELEASE/$relative_env" "$RELEASE_DIR/$relative_env"
    fi
  done

  log "Creating isolated backend environment"
  python3 -m venv "$RELEASE_DIR/backend/.venv"
  "$RELEASE_DIR/backend/.venv/bin/pip" install --disable-pip-version-check \
    -r "$RELEASE_DIR/backend/requirements.txt"
  "$RELEASE_DIR/backend/.venv/bin/python3" -m compileall -q "$RELEASE_DIR/backend"

  log "Installing and building frontend"
  npm --prefix "$RELEASE_DIR/web" ci
  npm --prefix "$RELEASE_DIR/web" run build

  touch "$RELEASE_DIR/.deploy-complete"
else
  log "Reusing previously built immutable release"
fi

if [[ "$PREVIOUS_RELEASE" == "$RELEASE_DIR" ]]; then
  log "Commit is already active; verifying health only"
  wait_for_health backend "$BACKEND_HEALTH"
  wait_for_health frontend "$FRONTEND_HEALTH"
  exit 0
fi

log "Switching production services"
if ! switch_services "$RELEASE_DIR"; then
  printf 'deploy: new release failed; starting rollback\n' >&2
  rollback "$PREVIOUS_RELEASE"
  exit 1
fi

printf '%s\n' "$RELEASE_DIR" > "$ACTIVE_RELEASE_FILE"
printf '%s %s previous=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TARGET_SHA" "${PREVIOUS_RELEASE:-none}" \
  >> "$DEPLOY_ROOT/deployments.log"

log "Deploy complete: $TARGET_SHA"
log "Old releases were retained for rollback; cleanup is intentionally manual"
