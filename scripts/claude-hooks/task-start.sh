#!/usr/bin/env bash
# Put the tracking rules in front of the agent at the start of every task.
#
# The rules are already written down -- in AGENTS.md and in the agent's own
# memory -- and were still missed, because both only work if they happen to be
# read. This runs whether or not anyone remembers to look.
set -Eeuo pipefail
cat <<'CONTEXT' | jq -Rs '{hookSpecificOutput:{hookEventName:"UserPromptSubmit",additionalContext:.}}'
Work tracking for this repo (see AGENTS.md § Tracking work):
- Starting work: open the ticket FIRST with `scripts/ticket.sh --epic E0N --title … --body-file …`
  (or `--ops` for operations). It creates the issue, adds it to the project board, and links
  it to the epic -- doing those by hand has dropped steps 2 and 3 before.
- Working: not in the primary checkout. `scripts/work-on.sh <issue> [slug]` gives the card
  its own worktree; sessions share this directory and git's HEAD/index/stash are per-directory.
  Never `git stash` here -- the stack is shared and pop takes whatever is on top.
- Finishing: commit, then `scripts/wrap-up.sh` opens the PR with `Closes #N` derived from the
  branch. GitHub cannot backfill that link. The board sets Done by itself when the merge
  closes the issue.
- Only OPS/infrastructure tickets stay off the board.
CONTEXT
