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
- Finishing: commit, then open a PR declaring `Closes #N` BEFORE it merges (GitHub cannot
  backfill the link), then set the card to Done on the board.
- Every card goes on the board. `--ops` means epic E10 plus the `infrastructure`
  label, which is how operations cards are filtered -- not by being left off.
CONTEXT
