#!/bin/bash
# postcompact-mark: 압축 직후 그 세션의 표식 둘을 만든다.
# - <세션>.warn  : UserPromptSubmit 훅이 다음 메시지 한 번에 소비하고 지운다.
# - <세션>.block : PreToolUse 가드가 자기 창(5분) 동안 본다.
# 경로를 세션별로 가르는 이유와 둘로 나누는 이유는 compact-flag-lib.sh 에 있다.

set -uo pipefail

INPUT=$(cat 2>/dev/null || true)
"$HOME/.claude/hooks/hook-identity-log.sh" PostCompact "$INPUT" 2>/dev/null || true

# shellcheck source=/dev/null
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/compact-flag-lib.sh" 2>/dev/null || exit 0

LOG="$HOME/.claude/logs/precompact.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

if ! SID=$(compact_flag_sid "$INPUT"); then
    compact_flag_nosid postcompact-mark
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN postcompact mark 세션 식별자 없음 — 표식 안 만듦" >> "$LOG"
    exit 0
fi

mkdir -p "$COMPACT_FLAG_DIR" || exit 0
compact_flag_prune
touch "$(compact_flag_path "$SID" warn)" "$(compact_flag_path "$SID" block)"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] postcompact mark created sid=$SID" >> "$LOG"
exit 0
