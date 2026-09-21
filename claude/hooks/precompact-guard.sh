#!/bin/bash
# precompact-guard: 압축 직전 progress.md 신선도 점검 + recover-then-allow.
# 룰 (2026-05-21 개편):
#   1. progress.md 없으면 통과.
#   2. age <= 7일 (604800s) 이면 통과.
#   3. stale 면 progress-updater 동기 실행 (bounded mode 자동 적용).
#   4. updater 후에도 stale 면, LLM 호출 없이 jsonl tail 200줄을 progress.md 의
#      AUTO_TAIL_SNAPSHOT 섹션에 원자적으로 교체 기록 → 압축 통과 (liveness 보장).
#   5. 어떤 경우에도 압축은 차단하지 않는다 (deadlock 영구 차단).
# 기존 24h fail-closed → 168h recover-then-allow 변환.
# 사고 가드 (cross-session leak) 는 mtime 이 아니라 별도 session_id 검증으로 이관 예정.

set -uo pipefail

LOG=$HOME/.claude/logs/precompact.log
STALE_THRESHOLD=$((7*24*3600))   # 7일
SNAPSHOT_LINES=200               # jsonl tail 줄 수
SNAPSHOT_MARK_START="<!-- AUTO_TAIL_SNAPSHOT begin -->"
SNAPSHOT_MARK_END="<!-- AUTO_TAIL_SNAPSHOT end -->"
mkdir -p "$(dirname "$LOG")"

INPUT=$(cat 2>/dev/null || echo '{}')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"
JSONL_HINT=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)

ts=$(date '+%Y-%m-%d %H:%M:%S')
PROG="$CWD/progress.md"

if [ ! -f "$PROG" ]; then
    echo "[$ts] cwd=$CWD progress.md 없음, 통과" >> "$LOG"
    exit 0
fi

now=$(date +%s)
mtime=$(stat -c %Y "$PROG" 2>/dev/null || echo 0)
age=$(( now - mtime ))

if [ "$age" -le "$STALE_THRESHOLD" ]; then
    echo "[$ts] cwd=$CWD progress.md fresh (age=${age}s), 통과" >> "$LOG"
    exit 0
fi

echo "[$ts] cwd=$CWD progress.md stale (age=${age}s) → updater 동기 실행" >> "$LOG"
if command -v claude-progress-updater >/dev/null 2>&1; then
    timeout 90 $HOME/.local/bin/claude-progress-updater "$CWD" >> "$LOG" 2>&1 || true
    new_mtime=$(stat -c %Y "$PROG" 2>/dev/null || echo 0)
    if [ "$(( now - new_mtime ))" -le "$STALE_THRESHOLD" ]; then
        echo "[$ts] updater 갱신 성공, 압축 통과" >> "$LOG"
        exit 0
    fi
fi

# Fallback: LLM 호출 없이 jsonl tail 200줄을 AUTO_TAIL_SNAPSHOT 섹션에 원자적 교체.
echo "[$ts] updater 실패 → tail snapshot fallback" >> "$LOG"
JSONL=""
if [ -n "$JSONL_HINT" ] && [ -f "$JSONL_HINT" ]; then
    JSONL="$JSONL_HINT"
else
    PROJ_KEY=$(echo "$CWD" | sed -e 's|/|-|g' -e 's|_|-|g')
    JSONL=$(ls -t "$HOME/.claude/projects/${PROJ_KEY}"/*.jsonl 2>/dev/null | head -1)
fi

SNAPSHOT_BODY=""
if [ -n "$JSONL" ] && [ -f "$JSONL" ]; then
    SNAPSHOT_BODY=$(tail -n "$SNAPSHOT_LINES" "$JSONL" 2>/dev/null \
        | jq -r 'select(.type=="user" or .type=="assistant") | (.timestamp // "?") + " " + .type + " " + ((.message.content // .message)|tostring|.[:200])' 2>/dev/null \
        | tail -n 80)
fi

TMP=$(mktemp)
{
    awk -v s="$SNAPSHOT_MARK_START" -v e="$SNAPSHOT_MARK_END" '
        BEGIN{skip=0}
        $0==s{skip=1; next}
        $0==e{skip=0; next}
        !skip{print}
    ' "$PROG"
    echo ""
    echo "$SNAPSHOT_MARK_START"
    echo "updated by precompact-guard at $ts (updater failed)"
    echo ""
    echo "$SNAPSHOT_BODY"
    echo "$SNAPSHOT_MARK_END"
} > "$TMP" && mv "$TMP" "$PROG"

echo "[$ts] AUTO_TAIL_SNAPSHOT 기록 후 압축 통과" >> "$LOG"
exit 0
