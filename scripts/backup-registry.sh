#!/usr/bin/env bash
# Snapshot the knowledge registry before any master-data write.
#
# The owner's rule (2026-08-31): changesets give per-record rollback, but a
# batch that goes wrong in ways the conflict checks don't see needs a whole-
# registry restore. Prior sessions did this by hand before batch operations
# (registry-backup-2026-08-26-*); this makes it a command so it cannot be
# skipped by forgetting.
#
#   scripts/backup-registry.sh before-lane-repair
#
# Writes $DATA_BASE_DIR/wang-knowledge-platform/staging/viewpoint-backfill/
#   registry-backup-<utc-date>-<label>/smart_answer_knowledge.dump
set -Eeuo pipefail

fail() { printf 'backup-registry: %s\n' "$*" >&2; exit 1; }

LABEL="${1:-}"
[[ -n "$LABEL" ]] || fail "usage: backup-registry.sh <label>  (e.g. before-lane-repair)"
[[ "$LABEL" =~ ^[a-z0-9][a-z0-9-]{0,60}$ ]] || fail "label must be lowercase words and hyphens"

SOURCE_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# .env is not guaranteed shell-sourceable; read just the keys we need.
env_value() { grep -E "^$1=" "$SOURCE_REPO/.env" | head -1 | cut -d= -f2-; }
DATA_BASE_DIR="${DATA_BASE_DIR:-$(env_value DATA_BASE_DIR)}"
[[ -n "$DATA_BASE_DIR" ]] || fail "DATA_BASE_DIR is required (from .env)"
DB_URL="${KNOWLEDGE_DATABASE_URL:-$(env_value KNOWLEDGE_DATABASE_URL)}"
[[ -n "$DB_URL" ]] || fail "KNOWLEDGE_DATABASE_URL is required (from .env)"

STAMP="$(date -u +%Y-%m-%d)"
TARGET_PARENT="$DATA_BASE_DIR/wang-knowledge-platform/staging/viewpoint-backfill"
TARGET_DIR="$TARGET_PARENT/registry-backup-$STAMP-$LABEL"
FINAL_DUMP="$TARGET_DIR/smart_answer_knowledge.dump"
[[ ! -e "$FINAL_DUMP" ]] || fail "backup already exists: $TARGET_DIR"
mkdir -p "$TARGET_PARENT"
if ! mkdir "$TARGET_DIR"; then
    fail "backup target already exists or another backup is running: $TARGET_DIR"
fi

TEMP_DUMP=""
cleanup() {
    status=$?
    if [[ -n "$TEMP_DUMP" && -e "$TEMP_DUMP" ]]; then
        rm -f -- "$TEMP_DUMP"
    fi
    if [[ $status -ne 0 && ! -e "$FINAL_DUMP" ]]; then
        rmdir "$TARGET_DIR" 2>/dev/null || true
    fi
    trap - EXIT
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

TEMP_DUMP="$(mktemp "$TARGET_DIR/.smart_answer_knowledge.dump.partial.XXXXXX")"
pg_dump --format=custom --file="$TEMP_DUMP" "$DB_URL"
[[ -s "$TEMP_DUMP" ]] || fail "pg_dump produced an empty archive"

if ! ARCHIVE_LISTING="$(pg_restore --list "$TEMP_DUMP")"; then
    fail "pg_restore could not read the completed archive"
fi
[[ -n "${ARCHIVE_LISTING//[[:space:]]/}" ]] || fail "pg_restore returned an empty archive listing"

SIZE="$(du -h "$TEMP_DUMP" | cut -f1)"
mv "$TEMP_DUMP" "$FINAL_DUMP"
TEMP_DUMP=""
printf 'backup-registry: wrote %s (%s)\n' "$FINAL_DUMP" "$SIZE"
printf 'restore with: pg_restore --clean --if-exists -d "<db-url>" "%s"\n' "$FINAL_DUMP"
