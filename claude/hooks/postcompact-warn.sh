#!/bin/bash
# postcompact-warn: UserPromptSubmit 훅. 직전에 압축이 일어났으면 다음 메시지 처리 전에 강한 경고를 컨텍스트에 주입 (one-shot).

set -uo pipefail
FLAG="$HOME/.claude/state/just-compacted.flag"

if [ ! -f "$FLAG" ]; then
    exit 0
fi

# 10분(600초) 이상 묵었으면 무효화
now=$(date +%s)
mtime=$(stat -c %Y "$FLAG" 2>/dev/null || echo 0)
age=$(( now - mtime ))
if [ "$age" -gt 600 ]; then
    rm -f "$FLAG"
    exit 0
fi

# additionalContext 주입 + 플래그 삭제 (one-shot)
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
