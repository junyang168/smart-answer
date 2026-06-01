#!/usr/bin/env bash
# Rebuild the topic index, then refresh search so new topic terms are searchable.
# Run this when manuscript content or topics change — NOT on every code deploy.
# It calls the LLM (Anthropic) for any new/changed manuscripts; unchanged ones
# are served from cache.
#
# Usage:
#   scripts/build_topic_index.sh                 # whole corpus (cache-aware)
#   scripts/build_topic_index.sh --project 16章  # one project
#   scripts/build_topic_index.sh --force         # ignore cache, re-extract all
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

: "${DATA_BASE_DIR:?set DATA_BASE_DIR}"
: "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY}"

PY="${PYTHON:-backend/.venv/bin/python}"

echo "==> Building topic index"
"$PY" -m backend.pipeline.topic_index.pipeline "$@"

# Reindex WITH embeddings to preserve semantic search (production has them on).
echo "==> Reindexing sermon search (with embeddings)"
curl -fsS -m 600 -X POST "http://127.0.0.1:8555/sermon_search/reindex" \
  -H "content-type: application/json" \
  -d '{"project_types":["sermon_note"],"include_embeddings":true}' && echo ""

echo "==> Topic index rebuild complete"
