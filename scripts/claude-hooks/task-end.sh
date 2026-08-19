#!/usr/bin/env bash
# Speak only when work is left dangling, so the reminder keeps its meaning.
#
# A stop hook that fires on every stop is noise and gets switched off; this one
# stays quiet unless there is something uncommitted or unpushed to act on.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

dirty="$(git status --porcelain 2>/dev/null | grep -cv '^?? ' || true)"
ahead="$(git rev-list --count '@{upstream}..HEAD' 2>/dev/null || echo 0)"
[[ "$dirty" -eq 0 && "$ahead" -eq 0 ]] && exit 0

note="工作尚未收尾："
[[ "$dirty" -gt 0 ]] && note="$note ${dirty} 個檔案未提交。"
[[ "$ahead" -gt 0 ]] && note="$note ${ahead} 個 commit 未推送。"
note="$note 收工要 commit、開 PR 並在合併前宣告 Closes #N、把看板卡改成 Done。"
jq -n --arg m "$note" '{systemMessage:$m}'
