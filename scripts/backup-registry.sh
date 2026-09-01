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
TARGET_DIR="$DATA_BASE_DIR/wang-knowledge-platform/staging/viewpoint-backfill/registry-backup-$STAMP-$LABEL"
[[ ! -e "$TARGET_DIR/smart_answer_knowledge.dump" ]] || fail "backup already exists: $TARGET_DIR"
mkdir -p "$TARGET_DIR"

pg_dump --format=custom --file="$TARGET_DIR/smart_answer_knowledge.dump" "$DB_URL"
SIZE="$(du -h "$TARGET_DIR/smart_answer_knowledge.dump" | cut -f1)"
printf 'backup-registry: wrote %s (%s)\n' "$TARGET_DIR/smart_answer_knowledge.dump" "$SIZE"
printf 'restore with: pg_restore --clean --if-exists -d "<db-url>" "%s"\n' "$TARGET_DIR/smart_answer_knowledge.dump"
