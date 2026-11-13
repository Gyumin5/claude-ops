#!/bin/bash
# hook-identity-log: 훅 입력에 세션 식별자가 들어오는지 한 줄로 적는다.
#
# 쓰는 자리: 압축 표식을 만들고 보고 지우는 훅 셋(postcompact-mark·
# block-ai-after-compact·postcompact-warn). 그 표식 파일이 기계에 하나뿐이라
# 한 세션의 압축이 다른 세션의 토론 제출을 막는데, 세션별로 가르려면 세 훅이
# 모두 자기 세션을 알아야 한다. 안다는 것을 먼저 재고 나서 가른다 —
# 식별자가 없는 자리에서 가르면 표식이 아무 세션에도 안 걸린다.
#
# 인자: $1 = 훅 이름, $2 = 그 훅이 받은 입력(JSON 문자열)
# 언제나 exit 0. 이 파일이 실패해도 부르는 훅의 동작을 바꾸지 않는다.

set -uo pipefail

EVENT="${1:-unknown}"
RAW="${2:-}"
LOG="$HOME/.claude/logs/hook-identity.log"

mkdir -p "$(dirname "$LOG")" 2>/dev/null || exit 0

SID=$(printf '%s' "$RAW" | jq -r '.session_id // "없음"' 2>/dev/null)
[ -n "$SID" ] || SID="없음"

# 기록 경로는 그 자체가 세션 식별자다(파일 이름이 세션 아이디). 경로 전체를
# 적지 않고 이름만 적는다 — 어느 세션인지는 그걸로 선다.
TP=$(printf '%s' "$RAW" | jq -r '.transcript_path // ""' 2>/dev/null)
if [ -n "$TP" ]; then
    TP=$(basename "$TP" .jsonl)
else
    TP="없음"
fi

printf '[%s] %s sid=%s tpath=%s\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$EVENT" "$SID" "$TP" >> "$LOG"
exit 0
