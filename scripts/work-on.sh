#!/usr/bin/env bash
# Start work on a card in a worktree of its own.
#
#   scripts/work-on.sh 116 worktree-per-session
#   scripts/work-on.sh 116            # slug defaults to "issue"
#
# Every session shares one checkout, and git's HEAD, index and stash are all
# per-directory state, so two agents in one directory collide. It has happened
# twice: a session switched HEAD and two commits landed on someone else's
# branch, and a `git stash` on a clean tree saved nothing, returned 0, and the
# following `pop` restored another session's three-day-old stash over 20 files.
#
# A worktree gives each session its own HEAD, index and working tree over the
# same object database, and git enforces the part that matters -- one branch
# cannot be checked out twice:
#
#   $ git worktree add /tmp/dup main
#   fatal: 'main' is already used by worktree at '/Users/junyang/app/smart-answer'
set -Eeuo pipefail

SOURCE_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Deliberately not under /tmp: `test_matthew_exposition_authoring` asserts that
# no packet source path contains "tmp", so a worktree there fails a test that
# has nothing to do with the change being made.
WORKTREE_ROOT="${SMART_ANSWER_WORKTREES:-$HOME/app/smart-answer-worktrees}"

fail() { printf 'work-on: %s\n' "$*" >&2; exit 1; }
command -v gh >/dev/null 2>&1 || fail "gh is required"

NUMBER="${1:-}"
SLUG="${2:-issue}"
[[ "$NUMBER" =~ ^[0-9]+$ ]] || fail "usage: work-on.sh <issue-number> [slug]"
[[ "$WORKTREE_ROOT" != */tmp/* && "$WORKTREE_ROOT" != /tmp/* ]] \
  || fail "worktrees must not live under /tmp (a test asserts no source path contains it)"

read -r state labels < <(
  gh issue view "$NUMBER" --json state,labels \
    -q '[.state, ([.labels[].name] | join(","))] | @tsv'
) || fail "no such issue: #$NUMBER"
[[ "$state" == "OPEN" ]] || printf 'work-on: note — issue #%s is %s\n' "$NUMBER" "$state" >&2

# The prefix says which kind of card this is at a glance, and matches what the
# branches in this repo already look like: wkp-105-…, ops-13-….
prefix="wkp"
[[ ",$labels," == *,infrastructure,* ]] && prefix="ops"
BRANCH="${prefix}-${NUMBER}-${SLUG}"
TARGET="$WORKTREE_ROOT/$BRANCH"

# Idempotent: asking twice is how you find the path again, not an error.
if existing="$(git -C "$SOURCE_REPO" worktree list --porcelain \
     | awk -v b="refs/heads/$BRANCH" '/^worktree /{p=$2} /^branch /{if ($2==b) print p}')" \
   && [[ -n "$existing" ]]; then
  printf '%s\n' "$existing"
  printf 'already open · cd %s\n' "$existing" >&2
  exit 0
fi

git -C "$SOURCE_REPO" fetch --quiet origin
mkdir -p "$WORKTREE_ROOT"
if git -C "$SOURCE_REPO" show-ref --quiet "refs/heads/$BRANCH"; then
  git -C "$SOURCE_REPO" worktree add --quiet "$TARGET" "$BRANCH"
else
  git -C "$SOURCE_REPO" worktree add --quiet -b "$BRANCH" "$TARGET" origin/main
fi

# `.env` is gitignored, so a fresh worktree has none and every runner dies with
# `RuntimeError: DATA_BASE_DIR is required`. Linked, not copied: one file to
# keep current.
[[ -f "$SOURCE_REPO/.env" ]] && ln -sf "$SOURCE_REPO/.env" "$TARGET/.env"

printf '%s\n' "$TARGET"
printf 'branch %s · cd %s\n' "$BRANCH" "$TARGET" >&2
