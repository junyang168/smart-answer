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
WEB_RUNTIME_DATA_DIR="${SMART_ANSWER_WEB_DATA_DIR:-$LEGACY_RELEASE/web/data}"
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
  # PM2 restart/startOrRestart preserves the original cwd for an existing app.
  # Delete and recreate the process so the immutable release path really takes
  # effect. A failed start is handled by switch_services -> rollback.
  pm2 delete "$PM2_APP" 2>/dev/null || true
  SMART_ANSWER_WEB_ROOT="$release/web" SMART_ANSWER_PM2_APP="$PM2_APP" \
    pm2 start "$PM2_CONFIG" --only "$PM2_APP" --update-env
}

link_web_runtime_data() {
  local release="$1"
  local link_path="$release/web/data"

  [[ -d "$WEB_RUNTIME_DATA_DIR" ]] || {
    printf 'deploy: frontend runtime data directory missing: %s\n' "$WEB_RUNTIME_DATA_DIR" >&2
    return 1
  }
  [[ -f "$WEB_RUNTIME_DATA_DIR/config/config.json" ]] || {
    printf 'deploy: Google login configuration missing: %s/config/config.json\n' "$WEB_RUNTIME_DATA_DIR" >&2
    return 1
  }

  if [[ -L "$link_path" ]]; then
    [[ "$(readlink "$link_path")" == "$WEB_RUNTIME_DATA_DIR" ]] || {
      printf 'deploy: frontend runtime data link points elsewhere: %s\n' "$link_path" >&2
      return 1
    }
  elif [[ -e "$link_path" ]]; then
    printf 'deploy: refusing to replace existing frontend data path: %s\n' "$link_path" >&2
    return 1
  else
    ln -s "$WEB_RUNTIME_DATA_DIR" "$link_path"
  fi
}

audit_frontend_release() {
  local release="$1"
  local audit_json critical_count

  # npm exits non-zero whenever findings meet its threshold, so capture JSON
  # explicitly and make our production policy decision from the counts.
  audit_json="$(npm --prefix "$release/web" audit --omit=dev --json 2>/dev/null || true)"
  [[ -n "$audit_json" ]] || {
    printf 'deploy: npm audit returned no result for %s\n' "$release" >&2
    return 1
  }
  critical_count="$(printf '%s' "$audit_json" | node -e '
    let input = "";
    process.stdin.on("data", chunk => input += chunk);
    process.stdin.on("end", () => {
      const report = JSON.parse(input);
      process.stdout.write(String(report.metadata?.vulnerabilities?.critical ?? 0));
    });
  ')" || return 1
  if ((critical_count > 0)); then
    printf 'deploy: blocked by %s critical production dependency finding(s)\n' "$critical_count" >&2
    printf 'deploy: run npm --prefix %s/web audit --omit=dev for details\n' "$release" >&2
    return 1
  fi
  log "Production dependency audit passed (0 critical)"
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
  link_web_runtime_data "$release" || return 1

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

for command_name in git tar python3 node npm pm2 curl launchctl; do
  require_command "$command_name"
done

# The release venv's interpreter is whatever built it, and `python3 -m venv`
# takes whatever `python3` the deploying shell resolves to. This machine has
# three: 3.9.6 at /usr/bin, 3.12.8 at /usr/local/bin, 3.13.2 under Homebrew. A
# deploy from the wrong shell built a 3.9.6 venv; the build succeeded, and the
# service died at import on `MicroSermon | None`, which 3.9 cannot evaluate.
#
# `.python-version` is the contract, and it names the version the test suite
# runs on. The path is resolved on the machine rather than committed, so this
# stays portable; SMART_ANSWER_PYTHON overrides it for an unusual host.
python_minor() {
  "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true
}

resolve_python() {
  local candidate
  if [[ -n "${SMART_ANSWER_PYTHON:-}" ]]; then
    [[ -x "$SMART_ANSWER_PYTHON" ]] || fail "SMART_ANSWER_PYTHON is not executable: $SMART_ANSWER_PYTHON"
    [[ "$(python_minor "$SMART_ANSWER_PYTHON")" == "$REQUIRED_PYTHON" ]] \
      || fail "SMART_ANSWER_PYTHON is not Python $REQUIRED_PYTHON: $SMART_ANSWER_PYTHON"
    printf '%s\n' "$SMART_ANSWER_PYTHON"
    return 0
  fi
  for candidate in \
    "python$REQUIRED_PYTHON" \
    python3 \
    "/Library/Frameworks/Python.framework/Versions/$REQUIRED_PYTHON/bin/python3" \
    "/opt/homebrew/bin/python$REQUIRED_PYTHON" \
    "/usr/local/bin/python$REQUIRED_PYTHON"
  do
    candidate="$(command -v "$candidate" 2>/dev/null || printf '%s' "$candidate")"
    [[ -x "$candidate" ]] || continue
    if [[ "$(python_minor "$candidate")" == "$REQUIRED_PYTHON" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

[[ -x /usr/libexec/PlistBuddy ]] || fail "PlistBuddy is unavailable"
[[ -f "$BACKEND_PLIST" ]] || fail "backend LaunchAgent not found: $BACKEND_PLIST"
[[ -f "$PM2_CONFIG" ]] || fail "PM2 config not found: $PM2_CONFIG"

log "Fetching Git refs"
git -C "$SOURCE_REPO" fetch --prune origin
TARGET_SHA="$(git -C "$SOURCE_REPO" rev-parse --verify "$TARGET_REF^{commit}")" \
  || fail "cannot resolve Git ref: $TARGET_REF"
MAIN_SHA="$(git -C "$SOURCE_REPO" rev-parse --verify 'origin/main^{commit}')"

# Read the version contract out of the commit being deployed, not out of
# whatever branch the source checkout happens to be on.
REQUIRED_PYTHON="$(git -C "$SOURCE_REPO" show "$TARGET_SHA:.python-version" 2>/dev/null || true)"
REQUIRED_PYTHON="${REQUIRED_PYTHON//[[:space:]]/}"
[[ -n "$REQUIRED_PYTHON" ]] || fail "$TARGET_SHA carries no .python-version"
PYTHON_BIN="$(resolve_python)" \
  || fail "no Python $REQUIRED_PYTHON interpreter found; install it or set SMART_ANSWER_PYTHON"

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
printf '   python:           %s (%s)\n' "$REQUIRED_PYTHON" "$PYTHON_BIN"
printf '   web runtime data: %s\n' "$WEB_RUNTIME_DATA_DIR"
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

# A release is about 1.5 GB of venv, node_modules and .next build. Running the
# disk out mid-build leaves a half-written tree and takes down everything else
# sharing the volume, PostgreSQL included, so refuse before starting rather
# than fail somewhere inside npm.
REQUIRED_FREE_MB="${SMART_ANSWER_MIN_FREE_MB:-6144}"
available_mb="$(df -m "$DEPLOY_ROOT" | awk 'NR==2 {print $4}')"
if [[ -z "$available_mb" ]]; then
  fail "cannot determine free space on $DEPLOY_ROOT"
fi
if ((available_mb < REQUIRED_FREE_MB)); then
  fail "only ${available_mb} MB free on $DEPLOY_ROOT; need ${REQUIRED_FREE_MB} MB. Remove old releases under $RELEASES_DIR (keep the active one and the rollback target)."
fi
log "Free space: ${available_mb} MB"

if ! mkdir "$DEPLOY_ROOT/.deploy-lock" 2>/dev/null; then
  fail "another deployment appears to be running: $DEPLOY_ROOT/.deploy-lock"
fi
trap 'rmdir "$DEPLOY_ROOT/.deploy-lock" 2>/dev/null || true' EXIT

# `.deploy-complete` now means "this release has served healthy traffic", not
# "the build finished". A release that builds and then fails its health check
# used to be marked complete anyway, so every retry reused the broken tree and
# could never recover -- the 3.9.6 venv had to be deleted by hand. Everything
# under a release is derived from one immutable commit, so rebuilding an
# unverified one is always safe and always cheaper than a person diagnosing it.
if [[ -d "$RELEASE_DIR" && ! -f "$RELEASE_DIR/.deploy-complete" ]]; then
  log "Discarding an unverified release and rebuilding: $RELEASE_DIR"
  rm -rf "$RELEASE_DIR"
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

  log "Creating isolated backend environment (Python $REQUIRED_PYTHON via $PYTHON_BIN)"
  "$PYTHON_BIN" -m venv "$RELEASE_DIR/backend/.venv"
  venv_minor="$(python_minor "$RELEASE_DIR/backend/.venv/bin/python3")"
  [[ "$venv_minor" == "$REQUIRED_PYTHON" ]] \
    || fail "release venv reports Python $venv_minor, expected $REQUIRED_PYTHON"
  "$RELEASE_DIR/backend/.venv/bin/pip" install --disable-pip-version-check \
    -r "$RELEASE_DIR/backend/requirements.txt"
  "$RELEASE_DIR/backend/.venv/bin/python3" -m compileall -q "$RELEASE_DIR/backend"

  log "Installing and building frontend"
  npm --prefix "$RELEASE_DIR/web" ci
  npm --prefix "$RELEASE_DIR/web" run build
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
if ! audit_frontend_release "$RELEASE_DIR"; then
  fail "release did not pass the production dependency policy; services were not changed"
fi
if ! switch_services "$RELEASE_DIR"; then
  printf 'deploy: new release failed; starting rollback\n' >&2
  rollback "$PREVIOUS_RELEASE"
  exit 1
fi

touch "$RELEASE_DIR/.deploy-complete"
printf '%s\n' "$RELEASE_DIR" > "$ACTIVE_RELEASE_FILE"
printf '%s %s previous=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TARGET_SHA" "${PREVIOUS_RELEASE:-none}" \
  >> "$DEPLOY_ROOT/deployments.log"

log "Deploy complete: $TARGET_SHA"
log "Old releases were retained for rollback; cleanup is intentionally manual"
