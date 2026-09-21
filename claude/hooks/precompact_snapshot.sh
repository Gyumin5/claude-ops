#!/bin/bash
# 배포 대상 경로: ~/dotfiles/claude/hooks/precompact_snapshot.sh (+ ~/.claude/hooks/ 심링크)
#
# precompact_snapshot: PreCompact 훅. 압축 경계 기록(side-effect)만 한다.
#
# 리뷰 반영 (#1, #2):
#   - PreCompact 은 hookSpecificOutput.additionalContext 주입 대상으로 문서화돼 있지 않다.
#     따라서 stdout JSON 주입 경로는 완전히 제거했다(무효 주입 방지). (#1)
#   - active.md 최신화는 "실제 파일 갱신 이후" 경로(Stop 훅 / 수동)로 일원화한다.
#     PreCompact 에서 active.md 를 재생성하면 모델이 상태를 저장하기 전의 stale 내용으로
#     덮어써 "최신 active.md 보장"이 거짓이 된다. 그래서 여기선 update_active_memory 를
#     호출하지 않는다. (#2)
#   - 결과적으로 이 훅은 압축 경계를 로그에 남기는 side-effect 만 수행한다.
#
# 압축 자체를 막아 모델에게 progress/topic 갱신을 요구하고 싶다면 유일하게 문서화된 수단은
#   top-level {"decision":"block","reason":"..."} 다. 매 압축을 막으면 UX 저하·루프 위험이 커서
#   기본 비활성. 아래 PRECOMPACT_BLOCK=1 옵트인 블록 참조(권장 안 함, 실측 후 결정 — APPLY open question).
#
# defensive: 항상 exit 0. 블로킹/ sudo / 자식 claude 금지. jq 없으면 로그만 남기고 통과(#9).

set -uo pipefail

LOG="$HOME/.claude/logs/precompact-snapshot.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null
ts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)

INPUT=$(cat 2>/dev/null || echo '{}')

CWD=""
if command -v jq >/dev/null 2>&1; then
    CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
    trigger=$(echo "$INPUT" | jq -r '.trigger // empty' 2>/dev/null)   # "manual" | "auto"
else
    echo "[$ts] jq 없음 → 경계 로그만" >> "$LOG"
    trigger=""
fi
[ -z "$CWD" ] && CWD="(unknown)"

echo "[$ts] precompact 경계 cwd=$CWD trigger=${trigger:-?} (active.md 재생성은 Stop/수동 담당 — 여기선 미실행)" >> "$LOG"

# 옵트인(기본 OFF, 권장 안 함): 압축 전 상태 저장을 강제하려면 PRECOMPACT_BLOCK=1.
#   PreCompact 의 block 지원/동작은 실측 필요(APPLY open question). auto 압축까지 막으면 방해되므로
#   manual 트리거일 때만 1회 유도. (재진입 가드: block 후 모델이 저장하고 다시 압축하면 통과)
if [ "${PRECOMPACT_BLOCK:-0}" = "1" ] && [ "$trigger" = "manual" ] && command -v python3 >/dev/null 2>&1; then
    python3 <<'PYEOF' 2>/dev/null || exit 0
import json
print(json.dumps({
    "decision": "block",
    "reason": "[압축 전 상태 저장] 압축으로 세부가 유실되기 전에 progress.md(상태/다음행동/막힌것)와 활성 memory/topics/<id>.md 를 규약대로 간결히 갱신하라(중복 금지). 갱신 후 다시 압축하면 진행된다."
}, ensure_ascii=False))
PYEOF
    exit 0
fi

exit 0
