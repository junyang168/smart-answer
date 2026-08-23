#!/usr/bin/env bash
# Finish a card: open the pull request, and later clean the worktree away.
#
#   scripts/wrap-up.sh                      # open the PR for this branch
#   scripts/wrap-up.sh --title "..." --body-file pr.md
#   scripts/wrap-up.sh --cleanup            # after it merges
#
# The `Closes #N` line is the whole reason this is a script. GitHub will not
# create that link after the merge, and `deployed-issues.sh` reports only real
# links, so a ticket connected afterwards is invisible to whoever is asking
# what shipped. Deriving it from the branch name means it cannot be forgotten.
#
# Nothing here sets the board to Done. The board does that itself, from the
# issue closing, and `Closes #N` closes the issue on merge -- #100, #102 and
# #105 all went Done with nobody touching them.
set -Eeuo pipefail

fail() { printf 'wrap-up: %s\n' "$*" >&2; exit 1; }
command -v gh >/dev/null 2>&1 || fail "gh is required"

CLEANUP=0 TITLE="" BODY_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cleanup)   CLEANUP=1; shift ;;
    --title)     TITLE="${2:-}"; shift 2 ;;
    --body-file) BODY_FILE="${2:-}"; shift 2 ;;
    -h|--help)   sed -n '2,8p' "$0"; exit 0 ;;
    *)           fail "unknown argument: $1" ;;
  esac
done

BRANCH="$(git branch --show-current)" || fail "not a git checkout"
[[ -n "$BRANCH" ]] || fail "detached HEAD: check out the card's branch first"
[[ "$BRANCH" != "main" ]] || fail "on main — work happens on a card's branch, in its own worktree"
[[ "$BRANCH" =~ ^(wkp|ops)-([0-9]+)- ]] \
  || fail "branch '$BRANCH' does not name a card; expected wkp-<n>-… or ops-<n>-…"
NUMBER="${BASH_REMATCH[2]}"

if (( CLEANUP )); then
  state="$(gh pr view "$BRANCH" --json state -q .state 2>/dev/null || echo NONE)"
  [[ "$state" == "MERGED" ]] || fail "pull request for $BRANCH is $state, not MERGED — nothing to clean up yet"
  root="$(git rev-parse --show-toplevel)"
  [[ -x "$root/scripts/reconcile-closed-worktrees.sh" ]] \
    || fail "lifecycle reconciler is missing from $BRANCH"
  issue_state="$(gh issue view "$NUMBER" --json state -q .state 2>/dev/null || echo UNKNOWN)"
  [[ "$issue_state" == "CLOSED" ]] \
    || fail "issue #$NUMBER is $issue_state, not CLOSED — cleanup would hide a tracking error"
  exec "$root/scripts/reconcile-closed-worktrees.sh" --issue "$NUMBER"
fi

[[ -z "$(git status --porcelain)" ]] || fail "uncommitted changes — commit them before opening the PR"
git push --quiet -u origin "$BRANCH"

if url="$(gh pr view "$BRANCH" --json url -q .url 2>/dev/null)" && [[ -n "$url" ]]; then
  printf '%s\n' "$url"
  printf 'already open · pushed %s\n' "$BRANCH" >&2
  exit 0
fi

[[ -n "$TITLE" ]] || TITLE="$(git log -1 --pretty=%s)"
body="Closes #${NUMBER}"
[[ -n "$BODY_FILE" ]] && body="$(printf 'Closes #%s\n\n%s' "$NUMBER" "$(cat "$BODY_FILE")")"
gh pr create --base main --head "$BRANCH" --title "$TITLE" --body "$body"
