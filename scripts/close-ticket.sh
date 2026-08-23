#!/usr/bin/env bash
# Close a tracked issue and immediately reconcile its local worktree.
#
#   scripts/close-ticket.sh 199 --reason not-planned
#   scripts/close-ticket.sh 202 --reason completed
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${SMART_ANSWER_SOURCE_REPO:-}" ]]; then
  REPO_ROOT="$(cd "$SMART_ANSWER_SOURCE_REPO" && pwd)"
else
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
GH_BIN="${SMART_ANSWER_GH:-gh}"

fail() { printf 'close-ticket: %s\n' "$*" >&2; exit 1; }
command -v "$GH_BIN" >/dev/null 2>&1 || fail "gh is required"

NUMBER="${1:-}"
[[ "$NUMBER" =~ ^[0-9]+$ ]] || fail "usage: close-ticket.sh <issue-number> --reason completed|not-planned"
shift
REASON=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reason) REASON="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done
[[ "$REASON" == "completed" || "$REASON" == "not-planned" ]] \
  || fail "--reason must be completed or not-planned"

branch="$(git -C "$REPO_ROOT" branch --show-current 2>/dev/null || true)"
if [[ "$branch" =~ ^(wkp|ops)-([0-9]+)- ]] && [[ "${BASH_REMATCH[2]}" != "$NUMBER" ]]; then
  fail "current branch $branch belongs to #${BASH_REMATCH[2]}, not #$NUMBER"
fi

state="$("$GH_BIN" issue view "$NUMBER" --json state --jq .state)" \
  || fail "cannot read issue #$NUMBER"
if [[ "$state" == "OPEN" ]]; then
  gh_reason="$REASON"
  [[ "$REASON" == "not-planned" ]] && gh_reason="not planned"
  "$GH_BIN" issue close "$NUMBER" --reason "$gh_reason"
else
  printf 'close-ticket: issue #%s is already %s; reconciling local state\n' \
    "$NUMBER" "$state" >&2
fi

exec "$SCRIPT_DIR/reconcile-closed-worktrees.sh" --issue "$NUMBER"
