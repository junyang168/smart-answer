#!/usr/bin/env bash
# Which pull requests and issues are actually live, and which are not yet.
#
# The question "what is deployed" used to be answered by reading commit
# messages for `#123`, which cannot tell a ticket a commit closed from one it
# merely mentioned in passing -- a single release listed seven still-open
# issues that way. GitHub knows the difference, so ask GitHub.
set -Eeuo pipefail

DEPLOY_ROOT="${SMART_ANSWER_DEPLOY_ROOT:-/opt/homebrew/var/www/smart-answer-deploy}"
BACKEND_HEALTH="${SMART_ANSWER_BACKEND_HEALTH:-http://127.0.0.1:8555/healthz}"
COMPARE_REF="${1:-origin/main}"
SOURCE_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() { printf 'deployed-issues: %s\n' "$*" >&2; exit 1; }
command -v gh >/dev/null 2>&1 || fail "gh is required"

# What the service says it is beats what any file claims it should be.
live="$(curl -fsS --max-time 5 "$BACKEND_HEALTH" 2>/dev/null \
  | sed -n 's/.*"release"[[:space:]]*:[[:space:]]*"\([0-9a-f]*\)".*/\1/p')"
source="live /healthz"
if [[ -z "$live" ]]; then
  [[ -f "$DEPLOY_ROOT/active-release" ]] || fail "no live release id and no active-release file"
  live="$(basename "$(<"$DEPLOY_ROOT/active-release")")"
  source="active-release file (service reports no release id)"
fi

git -C "$SOURCE_REPO" fetch --quiet --prune origin
git -C "$SOURCE_REPO" cat-file -e "$live^{commit}" 2>/dev/null \
  || fail "the live commit is not in this checkout: $live"
compare="$(git -C "$SOURCE_REPO" rev-parse --verify "$COMPARE_REF^{commit}")"

printf '線上 %s · 依據 %s\n' "${live:0:7}" "$source"
printf '比對 %s (%s)\n\n' "$COMPARE_REF" "${compare:0:7}"

# A merge subject like "Title (#54)" is how a squashed or merged PR lands.
pr_numbers() {
  git -C "$SOURCE_REPO" log --format='%s' "$1..$2" \
    | sed -n 's/.*(#\([0-9][0-9]*\)).*/\1/p' | sort -un
}

report() {
  local heading="$1" from="$2" to="$3" found=0
  printf '%s\n' "$heading"
  while read -r pr; do
    [[ -n "$pr" ]] || continue
    gh api "repos/{owner}/{repo}/pulls/$pr" >/dev/null 2>&1 || continue
    found=1
    printf '  #%-4s %s\n' "$pr" "$(gh pr view "$pr" --json title -q .title | cut -c1-58)"
    # closingIssuesReferences carries the number but not the title, so the
    # title is fetched per issue; state comes with it, which is worth seeing --
    # a closed ticket that is not deployed yet is the case people get wrong.
    while read -r issue; do
      [[ -n "$issue" ]] || continue
      printf '         closes #%-4s %-7s %s\n' \
        "$issue" \
        "$(gh issue view "$issue" --json state -q '.state|ascii_downcase')" \
        "$(gh issue view "$issue" --json title -q .title | cut -c1-46)"
    done < <(gh pr view "$pr" --json closingIssuesReferences -q '.closingIssuesReferences[]?.number')
  done < <(pr_numbers "$from" "$to")
  ((found)) || printf '  (無)\n'
  printf '\n'
}

# What the current release brought is measured against the release it replaced,
# not against main: a live commit is normally an ancestor of main, so that
# comparison is empty and says nothing.
previous=""
if [[ -f "$DEPLOY_ROOT/deployments.log" ]]; then
  previous="$(awk 'NR>1 {print prev} {prev=$2}' "$DEPLOY_ROOT/deployments.log" | tail -1)"
fi
if [[ -n "$previous" ]] && git -C "$SOURCE_REPO" cat-file -e "$previous^{commit}" 2>/dev/null; then
  report "本次部署帶上線的(相對前一版 ${previous:0:7}):" "$previous" "$live"
else
  printf '本次部署帶上線的:\n  (沒有前一版紀錄可比)\n\n'
fi

report "尚未上線($COMPARE_REF 有,線上沒有):" "$live" "$compare"
