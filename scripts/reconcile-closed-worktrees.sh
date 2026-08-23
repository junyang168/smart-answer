#!/usr/bin/env bash
# Reconcile local card worktrees with GitHub issue state.
#
# A closed issue makes a clean worktree disposable. It never makes
# uncommitted files or an unmerged branch disposable.
#
#   scripts/reconcile-closed-worktrees.sh
#   scripts/reconcile-closed-worktrees.sh --issue 202
set -Eeuo pipefail

SCRIPT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${SMART_ANSWER_SOURCE_REPO:-}" ]]; then
  PRIMARY_REPO="$(cd "$SMART_ANSWER_SOURCE_REPO" && pwd)"
else
  common_dir="$(git -C "$SCRIPT_REPO" rev-parse --path-format=absolute --git-common-dir)"
  PRIMARY_REPO="$(cd "$(dirname "$common_dir")" && pwd)"
fi
GH_BIN="${SMART_ANSWER_GH:-gh}"
DEV_SCRIPT="${SMART_ANSWER_DEV_SCRIPT:-$SCRIPT_REPO/scripts/dev.sh}"

fail() { printf 'worktree-reconcile: %s\n' "$*" >&2; exit 1; }
command -v "$GH_BIN" >/dev/null 2>&1 || fail "gh is required"

ONLY_ISSUE=""
EXCLUDE_ROOT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --issue)        ONLY_ISSUE="${2:-}"; shift 2 ;;
    --exclude-root) EXCLUDE_ROOT="${2:-}"; shift 2 ;;
    -h|--help)      sed -n '2,9p' "$0"; exit 0 ;;
    *)              fail "unknown argument: $1" ;;
  esac
done
[[ -z "$ONLY_ISSUE" || "$ONLY_ISSUE" =~ ^[0-9]+$ ]] \
  || fail "--issue must be an issue number"
[[ -d "$PRIMARY_REPO/.git" ]] || fail "primary checkout not found: $PRIMARY_REPO"

if [[ -n "$EXCLUDE_ROOT" ]]; then
  EXCLUDE_ROOT="$(cd "$EXCLUDE_ROOT" && pwd)"
fi

fetch_ok=0
if git -C "$PRIMARY_REPO" fetch --quiet origin; then
  fetch_ok=1
else
  printf 'worktree-reconcile: warning — origin fetch failed; local branches will be preserved\n' >&2
fi

# Remove registrations whose directories/gitdirs have already disappeared.
git -C "$PRIMARY_REPO" worktree prune --verbose

removed=0
blocked=0
matched=0

reconcile_one() {
  local wt_root="$1" branch="$2" issue_number="$3"
  local issue_state dirty_output

  [[ -z "$ONLY_ISSUE" || "$issue_number" == "$ONLY_ISSUE" ]] || return 0
  ((matched += 1))

  if ! issue_state="$("$GH_BIN" issue view "$issue_number" --json state --jq .state 2>/dev/null)"; then
    printf 'worktree-reconcile: blocked #%s — cannot read issue state (%s)\n' \
      "$issue_number" "$branch" >&2
    ((blocked += 1))
    return 0
  fi
  [[ "$issue_state" == "CLOSED" ]] || return 0

  if [[ "$wt_root" == "$PRIMARY_REPO" ]]; then
    printf 'worktree-reconcile: blocked #%s — primary checkout is never removable\n' \
      "$issue_number" >&2
    ((blocked += 1))
    return 0
  fi
  if [[ -n "$EXCLUDE_ROOT" && "$wt_root" == "$EXCLUDE_ROOT" ]]; then
    return 0
  fi
  if ! dirty_output="$(git -C "$wt_root" status --porcelain --untracked-files=all 2>&1)"; then
    printf 'worktree-reconcile: blocked #%s — cannot inspect %s\n%s\n' \
      "$issue_number" "$wt_root" "$dirty_output" >&2
    ((blocked += 1))
    return 0
  fi
  if [[ -n "$dirty_output" ]]; then
    printf 'worktree-reconcile: blocked #%s — uncommitted files in %s:\n%s\n' \
      "$issue_number" "$wt_root" "$dirty_output" >&2
    ((blocked += 1))
    return 0
  fi

  # dev.sh derives the card's ports and refuses to stop a listener whose cwd
  # is outside this worktree. A failure leaves the worktree in place.
  if [[ -x "$DEV_SCRIPT" ]] \
     && ! SMART_ANSWER_DEV_REPO_ROOT="$wt_root" "$DEV_SCRIPT" stop; then
    printf 'worktree-reconcile: blocked #%s — could not safely stop owned dev servers\n' \
      "$issue_number" >&2
    ((blocked += 1))
    return 0
  fi

  if ! (cd "$PRIMARY_REPO" && git worktree remove "$wt_root"); then
    printf 'worktree-reconcile: blocked #%s — git refused to remove %s\n' \
      "$issue_number" "$wt_root" >&2
    ((blocked += 1))
    return 0
  fi

  if (( fetch_ok )) \
     && git -C "$PRIMARY_REPO" merge-base --is-ancestor \
          "refs/heads/$branch" refs/remotes/origin/main 2>/dev/null; then
    git -C "$PRIMARY_REPO" update-ref -d "refs/heads/$branch"
    printf 'worktree-reconcile: removed #%s %s · deleted merged local branch %s\n' \
      "$issue_number" "$wt_root" "$branch"
  else
    printf 'worktree-reconcile: removed #%s %s · preserved unmerged local branch %s\n' \
      "$issue_number" "$wt_root" "$branch"
  fi
  ((removed += 1))
}

current_root=""
while IFS= read -r line; do
  case "$line" in
    "worktree "*) current_root="${line#worktree }" ;;
    "branch refs/heads/"*)
      current_branch="${line#branch refs/heads/}"
      if [[ "$current_branch" =~ ^(wkp|ops)-([0-9]+)- ]]; then
        reconcile_one "$current_root" "$current_branch" "${BASH_REMATCH[2]}"
      fi
      ;;
  esac
done < <(git -C "$PRIMARY_REPO" worktree list --porcelain)

if [[ -n "$ONLY_ISSUE" && "$matched" -eq 0 ]]; then
  printf 'worktree-reconcile: no local worktree for #%s\n' "$ONLY_ISSUE"
fi
if [[ -n "$ONLY_ISSUE" && "$blocked" -gt 0 ]]; then
  exit 2
fi
exit 0
