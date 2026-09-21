#!/bin/bash
# session-start-context: SessionStart 훅. 프로젝트 cwd의 progress.md + history.md 내용을
# claude에게 additionalContext로 주입해서 새 세션이 즉시 작업 맥락 파악.

set -uo pipefail

INPUT=$(cat 2>/dev/null || echo '{}')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

PROG="$CWD/progress.md"
ACTIVE="$CWD/history/active.md"
HIST="$CWD/history.md"

parts=""
# 기계가 관측한 기록 검증 실패를 progress.md 보다 먼저 읽힌다. 세션이 쓴 글을 세션이
# 물려받는 구조라, 자동 갱신기가 근거 없다고 버린 제안은 로그에만 남고 아무도 안 봤다.
# 사실 판정도 자동 정정도 안 한다 — 종류와 횟수만 전한다.
# 주입 자리를 새로 늘리지 않는다. 이 글이 쓴 바이트만큼 아래 본문 예산에서 뺀다.
PROG_CAP_BYTES=8000
DIAG_CAP_BYTES=1024
diag=""
if command -v claude-progress-diag >/dev/null 2>&1; then
    diag=$(claude-progress-diag render --project "$CWD" --progress "$PROG" \
           --cap "$DIAG_CAP_BYTES" 2>/dev/null || true)
fi
if [ -n "$diag" ]; then
    dbytes=$(printf '%s' "$diag" | wc -c)
    parts+="${diag}"
    PROG_CAP_BYTES=$(( PROG_CAP_BYTES - dbytes ))
    [ "$PROG_CAP_BYTES" -lt 1000 ] && PROG_CAP_BYTES=1000
fi
if [ -f "$PROG" ]; then
    # progress는 hard limit 180줄이라 통째 주입 가능
    pcontent=$(head -c "$PROG_CAP_BYTES" "$PROG")
    parts+="## progress.md (현재 진행 상황)\n\n${pcontent}\n\n"
fi
# 직전 세션 handoff (cs rotate 게이트가 신선본 보장). 가장 먼저 읽도록 상단 배치.
HANDOFF="$CWD/handoff.md"
if [ -f "$HANDOFF" ]; then
    hmtime=$(stat -c %Y "$HANDOFF" 2>/dev/null || echo 0)
    hage=$(( $(date +%s) - hmtime ))
    hcontent=$(head -c 6000 "$HANDOFF")
    parts+="## handoff.md (직전 세션 인계 — 가장 먼저 확인, ${hage}s 전 작성)\n\n${hcontent}\n\n"
    parts+="이 handoff 를 읽고 다음 행동을 이어가라. progress.md 에 반영했고 더 필요 없으면 'mv handoff.md handoff.archived' 로 치워 다음 부팅 때 stale 주입 방지.\n\n"
fi
# 우선순위: history/active.md (compact view) > history.md tail (fallback)
#
# 주입하면서 주입 상태를 같이 잰다. active.md 는 "착수 시점에 실제로 보이는 것"이라
# 이게 없거나 부으면 과거 결론이 조회되지 않고, 그러면 이미 기각한 실험을 다시 돈다.
# 그런데 지금까지 그 고장이 조용했다 — 상한을 넘는 부분은 말없이 잘려나갔다.
# (2026-08-20 실측: session-a 는 결정 428항목·"재시도 시 시간낭비" 75건을 갖고도
#  active.md 가 없어 7568줄 일지의 꼬리만 주입받고 있었다. session_c_work
#  592줄, proj-b 는 요약본 363줄이 본체 184줄보다 컸다.)
ACTIVE_CAP_BYTES=6000
ACTIVE_CAP_LINES=120
hygiene=""
if [ -f "$ACTIVE" ]; then
    abytes=$(stat -c %s "$ACTIVE" 2>/dev/null || echo 0)
    alines=$(wc -l < "$ACTIVE" 2>/dev/null || echo 0)
    acontent=$(head -c "$ACTIVE_CAP_BYTES" "$ACTIVE")
    parts+="## history/active.md (현재 유효한 결정)\n\n${acontent}\n\n"
    parts+="(전체 결정 로그는 history.md / history/YYYY-MM.md 에서 lazy load)\n"
    if [ "${abytes:-0}" -gt "$ACTIVE_CAP_BYTES" ] 2>/dev/null; then
        hygiene+="- 위 주입이 잘렸다: active.md 는 ${abytes}바이트인데 앞 ${ACTIVE_CAP_BYTES}바이트만 실렸다. 뒷부분은 이 세션에 안 보인다.\n"
    fi
    if [ "${alines:-0}" -gt "$ACTIVE_CAP_LINES" ] 2>/dev/null; then
        hygiene+="- active.md 가 ${alines}줄이다. 규약은 현재 유효한 결정 10~20개의 compact view(약 ${ACTIVE_CAP_LINES}줄 이내)다.\n"
    fi
    hbytes=$(stat -c %s "$HIST" 2>/dev/null || echo 0)
    if [ -f "$HIST" ] && [ "${abytes:-0}" -ge "${hbytes:-0}" ] 2>/dev/null; then
        hygiene+="- 요약본이 원본보다 크다: active.md ${abytes}바이트 >= history.md ${hbytes}바이트.\n"
    fi
elif [ -f "$HIST" ]; then
    hcontent=$(tail -c 4000 "$HIST")
    hlines=$(wc -l < "$HIST" 2>/dev/null || echo 0)
    parts+="## history.md (최근 결정 로그 — active.md 없음)\n\n${hcontent}\n"
    hygiene+="- history/active.md 가 없다. 그래서 위는 결정 요약이 아니라 history.md ${hlines}줄의 꼬리 4000바이트다 — 그 앞의 결론은 직접 grep 하기 전엔 안 보인다.\n"
fi
if [ -n "$hygiene" ]; then
    parts+="\n## [자동 알림] 결정 조회 층 점검\n\n"
    parts+="${hygiene}"
    parts+="\n과거 결론이 착수 시점에 조회되지 않는다는 뜻이다 — 같은 실험·같은 기각을 다시 하게 되는 경로다. history/active.md 를 현재 유효한 결정의 compact view 로 정리해라. 특히 \"재시도 시 시간낭비\"로 기록된 항목은 빠뜨리지 마라(history.md 에서 grep 하면 나온다). 본문을 옮겨 적지 말고 결론 한 줄과 경로만 남긴다.\n\n"
fi

# 이전 세션 archived jsonl 가장 최근 1개를 hint로 주입.
# cs rotate가 ~/.claude/projects/<key>/archive/ 로 옮겨둠. key는 cwd의 / 와 _ 를 - 로.
PROJ_KEY=$(echo "$CWD" | sed -e 's|/|-|g' -e 's|_|-|g')
ARCHIVE_DIR="$HOME/.claude/projects/${PROJ_KEY}/archive"
LAST_ARCHIVED=""
if [ -d "$ARCHIVE_DIR" ]; then
    LAST_ARCHIVED=$(ls -t "$ARCHIVE_DIR"/*.jsonl 2>/dev/null | head -1)
fi
if [ -n "$LAST_ARCHIVED" ]; then
    asize=$(stat -c %s "$LAST_ARCHIVED" 2>/dev/null || echo 0)
    alines=$(wc -l < "$LAST_ARCHIVED" 2>/dev/null || echo 0)
    parts+="## 이전 세션 archive\n\n"
    parts+="이전 세션의 transcript jsonl이 아래 경로에 보존되어 있다. 필요하면 직접 read/grep 해서 최근 작업 맥락을 가져와라. progress.md 가 stale 하면 이걸 우선 참고.\n\n"
    parts+="- path: ${LAST_ARCHIVED}\n"
    parts+="- size: $((asize / 1024))KB, ${alines} lines\n\n"
    parts+="권장 절차:\n"
    parts+="1) tail -n 200 \"${LAST_ARCHIVED}\" | jq -r 'select(.type==\"assistant\" or .type==\"user\") | .message.content' 로 마지막 turn 확인\n"
    parts+="2) 필요시 grep으로 특정 키워드 (이전 결정/작업 단위) 추적\n"
    parts+="3) 오래되지 않은(<7일) 결정·작업·미해결 작업이 있으면 progress.md 머리에 한두 줄 요약 추가\n\n"
fi

# [자동 트리거] CLAUDE.md @import 분리 검토 시점 자동 감지 (읽기전용).
# 글로벌 가드 파일이 임계 이상이면 세션 시작 시 알림 주입. 본문 자동수정은 안 함.
CLAUDEMD="$HOME/.claude/CLAUDE.md"
CLAUDEMD_THRESHOLD=400
if [ -f "$CLAUDEMD" ]; then
    cmlines=$(wc -l < "$CLAUDEMD" 2>/dev/null || echo 0)
    if [ "${cmlines:-0}" -ge "$CLAUDEMD_THRESHOLD" ] 2>/dev/null; then
        parts+="## [자동 알림] CLAUDE.md @import 분리 검토 시점\n\n"
        parts+="글로벌 CLAUDE.md 가 ${cmlines}줄(임계 ${CLAUDEMD_THRESHOLD}) 이상. 비-가드 섹션을 @import/rules 로 분리해 본문 축소 검토 권장.\n"
        parts+="가드 줄 분리는 먼저 센티넬 1줄 테스트(@import 파일에 유니크 문자열→subagent/압축에서 보이는지, 모델턴 1회)로 확인 후에만. 무인 자동 본문 재배치 금지(추가만·바이트동일 검증·git revert 가능).\n\n"
    fi
fi

[ -z "$parts" ] && exit 0

# 본문은 파일로 넘긴다. 예전엔 heredoc 안의 python 문자열 리터럴로 보간했는데
# 그러면 두 경로로 주입 전체가 조용히 빈 문자열이 됐다:
#   (1) 위 head/tail -c 가 UTF-8 문자를 바이트 중간에서 자르면 decode 가 터진다.
#       한글은 문자당 3바이트라 경계에 걸릴 확률이 높다. 2026-08-20 재현 확인 —
#       6000바이트 지점이 문자 중간이면 progress.md·handoff.md·history 까지
#       통째로 사라지고 세션은 아무 맥락 없이 시작한다.
#   (2) 본문에 따옴표 세 개나 역슬래시가 들어가면 같은 결과가 된다.
# 둘 다 "맥락이 통째로 없어졌는데 아무도 모른다"로 끝난다 — 훅이 항상 exit 0 이라
# 실패조차 안 보인다. 파일로 넘기고 invalid 바이트만 버리면 두 경로가 다 막힌다.
TMP=$(mktemp) || exit 0
trap 'rm -f "$TMP"' EXIT
printf '%s' "$parts" > "$TMP"

python3 - "$TMP" <<'PYEOF'
import json, sys

raw = open(sys.argv[1], "rb").read().decode("utf-8", "ignore")
ctx = raw.replace("\\n", "\n")
out = {
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "[세션 시작 맥락 — progress.md/history.md]\n\n" + ctx,
    }
}
print(json.dumps(out, ensure_ascii=False))
PYEOF

exit 0
