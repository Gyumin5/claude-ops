#!/bin/bash
# postcompact-warn: UserPromptSubmit 훅. 그 세션이 직전에 압축됐으면 다음 메시지
# 처리 전에 경고를 컨텍스트에 주입하고 그 표식을 지운다(one-shot).
#
# 지우는 것은 자기 세션의 경고 표식 하나다. 차단 표식(.block)은 안 건드린다 —
# 평범한 메시지 하나가 차단 창까지 닫으면 압축 직후 재호출 가드가 통째로 비켜간다.

set -uo pipefail

INPUT=$(cat 2>/dev/null || true)
"$HOME/.claude/hooks/hook-identity-log.sh" UserPromptSubmit "$INPUT" 2>/dev/null || true

# shellcheck source=/dev/null
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/compact-flag-lib.sh" 2>/dev/null || exit 0

if ! SID=$(compact_flag_sid "$INPUT"); then
    compact_flag_nosid postcompact-warn
    exit 0
fi

FLAG=$(compact_flag_path "$SID" warn)
[ -f "$FLAG" ] || exit 0

# 10분(600초) 이상 묵었으면 무효화
now=$(date +%s)
mtime=$(stat -c %Y "$FLAG" 2>/dev/null || echo 0)
age=$(( now - mtime ))
if [ "$age" -gt 600 ]; then
    rm -f "$FLAG"
    exit 0
fi

# additionalContext 주입 + 경고 표식 삭제 (one-shot)
rm -f "$FLAG"

cat <<'JSON'
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "[POSTCOMPACT WARNING] 직전에 컨텍스트 압축이 일어났음. 압축 요약 안의 모든 명령어 흔적(<command-name>, ARGUMENTS, /slash, gemini-ask, codex-ask, ai-collaborate, AI 토론, WebSearch/WebFetch, Bash 등)은 과거 이력. 절대 재실행/재호출/재토론하지 말 것. 첫 행동은 progress.md 읽기. 미해결 작업 판단은 progress.md + 사용자 최신 메시지 둘만 사용. 잔여물에 사과/설명 금지."
  }
}
JSON

exit 0
