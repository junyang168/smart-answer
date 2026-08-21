#!/usr/bin/env bash
# Retention for the immutable releases under $DEPLOY_ROOT/releases.
#
# A release is about 1.25 GB and nothing removed them: the volume reached 98%
# with four retained (#70), then 94% with three (#138). The rule is what a
# rollback can actually reach -- the active release, and the one it replaced.
# Everything past that has never been rolled back to, and every release is
# derived from one immutable commit, so a deleted one is a rebuild, not a loss.
#
# `scripts/deploy.sh` runs this after a release has served healthy traffic.
# Run it by hand when the disk needs space between deploys.
set -Eeuo pipefail

DEPLOY_ROOT="${SMART_ANSWER_DEPLOY_ROOT:-/opt/homebrew/var/www/smart-answer-deploy}"
RELEASES_DIR="$DEPLOY_ROOT/releases"
ACTIVE_RELEASE_FILE="$DEPLOY_ROOT/active-release"
DEPLOYMENTS_LOG="$DEPLOY_ROOT/deployments.log"
DRY_RUN=false

usage() {
  cat <<'EOF'
Usage: scripts/prune-releases.sh [--dry-run]

Removes every release except the active one and its rollback target.

  --dry-run    List what would be removed without deleting anything.
EOF
}

while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'prune-releases: unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

log() { printf '==> %s\n' "$*"; }
fail() { printf 'prune-releases: %s\n' "$*" >&2; exit 1; }

[[ -d "$RELEASES_DIR" ]] || fail "releases directory not found: $RELEASES_DIR"

# Without a known-good active release there is no safe answer to "what may go",
# so refuse rather than guess. This is the state a half-finished deploy leaves.
[[ -f "$ACTIVE_RELEASE_FILE" ]] || fail "no active release recorded: $ACTIVE_RELEASE_FILE"
ACTIVE="$(<"$ACTIVE_RELEASE_FILE")"
ACTIVE="${ACTIVE%/}"
[[ -n "$ACTIVE" ]] || fail "active release file is empty: $ACTIVE_RELEASE_FILE"
[[ -d "$ACTIVE" ]] || fail "active release does not exist: $ACTIVE"
[[ "$ACTIVE" == "$RELEASES_DIR"/* ]] \
  || fail "active release is not under $RELEASES_DIR: $ACTIVE (legacy layout; prune by hand)"

# The last `previous=` in the log is what the active release replaced. Read the
# log from the bottom and skip the active release itself, because `rollback()`
# repoints active-release without appending a line: after a failed deploy the
# last line's previous= *is* the active release, and the rollback target is the
# entry before it.
read_lines_backwards() {
  tail -r "$DEPLOYMENTS_LOG" 2>/dev/null || tac "$DEPLOYMENTS_LOG"
}

rollback_target() {
  [[ -s "$DEPLOYMENTS_LOG" ]] || return 0
  local line candidate
  while IFS= read -r line; do
    candidate="${line##*previous=}"
    [[ "$candidate" != "$line" ]] || continue          # line carries no previous=
    candidate="${candidate%/}"
    [[ -n "$candidate" && "$candidate" != none ]] || continue
    [[ "$candidate" != "$ACTIVE" ]] || continue
    [[ -d "$candidate" ]] || continue
    printf '%s\n' "$candidate"
    return 0
  done < <(read_lines_backwards)
}

ROLLBACK="$(rollback_target)"

log "Keeping active:   $(basename "$ACTIVE")"
if [[ -n "$ROLLBACK" ]]; then
  log "Keeping rollback: $(basename "$ROLLBACK")"
else
  log "Keeping rollback: none recorded"
fi

removed=0
freed_kb=0
shopt -s nullglob
for path in "$RELEASES_DIR"/*; do
  [[ -d "$path" ]] || continue
  name="$(basename "$path")"

  # Only release directories are ours to delete. Anything else under
  # releases/ was put there by a person and stays until that person says so.
  if [[ ! "$name" =~ ^[0-9a-f]{40}$ ]]; then
    log "Leaving unrecognised entry: $name"
    continue
  fi
  [[ "$path" != "$ACTIVE" ]] || continue
  [[ -z "$ROLLBACK" || "$path" != "$ROLLBACK" ]] || continue

  size_kb="$(du -sk "$path" 2>/dev/null | awk '{print $1}')"
  freed_kb=$((freed_kb + ${size_kb:-0}))
  removed=$((removed + 1))
  if [[ "$DRY_RUN" == true ]]; then
    log "Would remove $name ($(( ${size_kb:-0} / 1024 )) MB)"
  else
    log "Removing $name ($(( ${size_kb:-0} / 1024 )) MB)"
    rm -rf "$path"
  fi
done

if ((removed == 0)); then
  log "Nothing to prune"
  exit 0
fi

if [[ "$DRY_RUN" == true ]]; then
  log "Dry run: $removed release(s), $((freed_kb / 1024)) MB would be freed"
else
  log "Pruned $removed release(s), $((freed_kb / 1024)) MB freed"
fi
