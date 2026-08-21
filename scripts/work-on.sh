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

# None of these are in git, so a fresh worktree has none of them and cannot run
# anything -- not the app, not even pytest. Installing per worktree would cost
# 2.3 GB each, so they are linked: the code under test is the worktree's, the
# interpreter and the packages are shared.
#
#   backend/.venv      the interpreter `.python-version` pins, and the one
#                      production runs; the root `.venv` is an older 3.11 that
#                      failed a test production passes
#   web/node_modules   491 MB
#   node_modules        25 MB
#
# `.env` is here too: without it every runner dies on `DATA_BASE_DIR is
# required`.
#
# `web/.env.local` and `web/next-env.d.ts` are here for the same reason and are
# equally invisible: without the first, anything that renders a page dies on
# `FULL_ARTICLE_SERVICE_URL is not configured`; without the second, the very
# first `tsc --noEmit` in a new worktree invents a missing-module error that
# disappears the moment a build has run.
#
# One limit comes with sharing them, and AGENTS.md says so rather than
# pretending otherwise: a branch that changes `requirements.txt` or
# `package.json` is running against the wrong install until it makes its own.
# (Ports are no longer a limit -- `dev.sh` derives them from the card number.)
for shared in .env web/.env.local backend/.venv .venv node_modules web/next-env.d.ts; do
  if [[ -e "$SOURCE_REPO/$shared" ]]; then
    mkdir -p "$(dirname "$TARGET/$shared")"
    ln -sfn "$SOURCE_REPO/$shared" "$TARGET/$shared"
  else
    printf 'work-on: note — %s does not exist in %s, not linked\n' "$shared" "$SOURCE_REPO" >&2
  fi
done

# `web/node_modules` is the exception, and has to be. Turbopack takes the
# working directory as its root and rejects a node_modules that symlinks out of
# it -- "Symlink [project]/node_modules is invalid, it points out of the
# filesystem root" -- so with a link neither `npm run dev` nor `npm run build`
# starts at all. A hardlink clone costs about ten seconds and no meaningful
# disk: every file is the same inode as the source until something rewrites it.
#
# Which is also the one caveat. `npm install` here replaces files rather than
# editing them, so it breaks the sharing safely for what it touches -- but a
# branch that changes `package.json` should still run its own install rather
# than trust this copy.
if [[ -d "$SOURCE_REPO/web/node_modules" && ! -e "$TARGET/web/node_modules" ]]; then
  printf 'work-on: cloning web/node_modules (hardlinks, ~10s)…\n' >&2
  cp -al "$SOURCE_REPO/web/node_modules" "$TARGET/web/node_modules"
elif [[ ! -d "$SOURCE_REPO/web/node_modules" ]]; then
  printf 'work-on: note — web/node_modules does not exist in %s, not cloned\n' "$SOURCE_REPO" >&2
fi

printf '%s\n' "$TARGET"
printf 'branch %s · cd %s\n' "$BRANCH" "$TARGET" >&2
