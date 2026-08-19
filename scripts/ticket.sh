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
#   scripts/ticket.sh --ops --title "OPS-12 — ..." --body-file card.md
#   scripts/ticket.sh --epic E02 --title ... --body-file ... --dry-run
#
# OPS cards are deliberately repo-only: operations work is not tracked on the
# platform board and belongs to no epic.
set -Eeuo pipefail

OWNER="junyang168"
PROJECT=3

# Epic issue numbers. An epic is an issue like any other; a card is linked to
# one through GitHub's sub-issue relation, which is what the board's
# "Parent issue" column reads.
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
  [[ -z "$EPIC" ]] || fail "--ops and --epic are mutually exclusive: OPS cards belong to no epic"
else
  [[ -n "$EPIC" ]] || fail "--epic is required (E01..E10), or pass --ops for an operations ticket"
  EPIC_ISSUE="$(epic_issue "$EPIC")" || fail "unknown epic: $EPIC (expected E01..E10)"
fi

if (( DRY )); then
  printf 'would create: %s\n' "$TITLE"
  if (( OPS )); then
    printf '  repo issue with label "infrastructure"; no board, no epic\n'
  else
    printf '  repo issue, board %s, sub-issue of #%s (%s)\n' "$PROJECT" "$EPIC_ISSUE" "$EPIC"
  fi
  exit 0
fi

if (( OPS )); then
  url="$(gh issue create --title "$TITLE" --label infrastructure --body-file "$BODY_FILE")"
  printf '%s\n' "$url"
  exit 0
fi

url="$(gh issue create --title "$TITLE" --body-file "$BODY_FILE")"
number="${url##*/}"

gh project item-add "$PROJECT" --owner "$OWNER" --url "$url" >/dev/null

node_id() {
  gh api graphql -f query="{repository(owner:\"$OWNER\",name:\"smart-answer\"){issue(number:$1){id}}}" \
    -q '.data.repository.issue.id'
}
gh api graphql -f query="mutation{addSubIssue(input:{issueId:\"$(node_id "$EPIC_ISSUE")\",subIssueId:\"$(node_id "$number")\"}){subIssue{number}}}" >/dev/null

printf '%s\n' "$url"
printf 'board: %s (Todo) · epic: %s (#%s)\n' "$PROJECT" "$EPIC" "$EPIC_ISSUE"
