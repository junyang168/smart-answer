#!/usr/bin/env bash
# Open a ticket the way this repo tracks work -- in one command.
#
# `gh issue create` writes the issue and stops. A WKP card also has to reach
# the project board and hang under its epic, and those are separate calls that
# nothing reminds you about. Five existing cards (#81, #83, #84, #85, #88) were
# never added to the board, so this is not a lapse anyone can be trusted out
# of; it is a missing entry point.
#
#   scripts/ticket.sh --epic E01 --title "WKP-F01.10 — ..." --body-file card.md
#   scripts/ticket.sh --ops --title "OPS-13 — ..." --body-file card.md
#   scripts/ticket.sh --epic E02 --title ... --body-file ... --dry-run
#
# `--ops` is shorthand for "E10 plus the infrastructure label", not an escape
# from the board. Operations cards used to be repo-only, and the twelve of them
# were invisible in the one place the work is read: nobody could answer what
# OPS covered without opening the issue list, and three of the twelve were not
# operations at all. Excluding something belongs in a saved view, which the
# board already supports through that label; excluded at write time it can only
# be found again by someone who remembers it exists.
set -Eeuo pipefail

OWNER="junyang168"
PROJECT=3

# Epic issue numbers. An epic is an issue like any other; a card is linked to
# one through GitHub's sub-issue relation, which is what the board's
# "Parent issue" column reads.
#
# E10 is where `--ops` lands. Its own scope statement already names this work:
# "GitHub Project、issue hierarchy、WIP control" and "branch／commit／PR 邊界與
# dirty-worktree recovery".
epic_issue() {
  case "$1" in
    E01) echo 3  ;;  # Source Corpus & Provenance
    E02) echo 4  ;;  # Shared Knowledge & Argument Graph
    E03) echo 5  ;;  # Editorial Governance & Review Workbench
    E04) echo 6  ;;  # Composition, Authoring & Publication Engine
    E05) echo 7  ;;  # Matthew Exposition Collection
    E06) echo 8  ;;  # Topic Essays & Scholarly Thought Reconstruction
    E07) echo 9  ;;  # Evidence-backed Search & QA
    E08) echo 10 ;;  # Micro-sermons & Learning Products
    E09) echo 11 ;;  # Public Repository & Reader Experience
    E10) echo 12 ;;  # Platform Operations, Integrity & Delivery Control
    *)   return 1 ;;
  esac
}

fail() { printf 'ticket: %s\n' "$*" >&2; exit 1; }
command -v gh >/dev/null 2>&1 || fail "gh is required"

EPIC="" TITLE="" BODY_FILE="" OPS=0 DRY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --epic)      EPIC="${2:-}"; shift 2 ;;
    --title)     TITLE="${2:-}"; shift 2 ;;
    --body-file) BODY_FILE="${2:-}"; shift 2 ;;
    --ops)       OPS=1; shift ;;
    --dry-run)   DRY=1; shift ;;
    -h|--help)   sed -n '2,14p' "$0"; exit 0 ;;
    *)           fail "unknown argument: $1" ;;
  esac
done

[[ -n "$TITLE" ]] || fail "--title is required"
[[ -n "$BODY_FILE" ]] || fail "--body-file is required"
[[ -f "$BODY_FILE" ]] || fail "body file not found: $BODY_FILE"

if (( OPS )); then
  [[ -z "$EPIC" ]] || fail "--ops already means --epic E10; pass one or the other"
  EPIC="E10"
fi
[[ -n "$EPIC" ]] || fail "--epic is required (E01..E10), or pass --ops for an operations ticket"
EPIC_ISSUE="$(epic_issue "$EPIC")" || fail "unknown epic: $EPIC (expected E01..E10)"

if (( DRY )); then
  printf 'would create: %s\n' "$TITLE"
  printf '  repo issue, board %s, sub-issue of #%s (%s)%s\n' \
    "$PROJECT" "$EPIC_ISSUE" "$EPIC" "$( ((OPS)) && printf ', label "infrastructure"')"
  exit 0
fi

if (( OPS )); then
  url="$(gh issue create --title "$TITLE" --label infrastructure --body-file "$BODY_FILE")"
else
  url="$(gh issue create --title "$TITLE" --body-file "$BODY_FILE")"
fi
number="${url##*/}"

gh project item-add "$PROJECT" --owner "$OWNER" --url "$url" >/dev/null

node_id() {
  gh api graphql -f query="{repository(owner:\"$OWNER\",name:\"smart-answer\"){issue(number:$1){id}}}" \
    -q '.data.repository.issue.id'
}
gh api graphql -f query="mutation{addSubIssue(input:{issueId:\"$(node_id "$EPIC_ISSUE")\",subIssueId:\"$(node_id "$number")\"}){subIssue{number}}}" >/dev/null

printf '%s\n' "$url"
printf 'board: %s (Todo) · epic: %s (#%s)%s\n' \
  "$PROJECT" "$EPIC" "$EPIC_ISSUE" "$( ((OPS)) && printf ' · label: infrastructure')"
