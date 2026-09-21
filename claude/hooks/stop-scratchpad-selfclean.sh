#!/bin/bash
# stop-scratchpad-selfclean: 매 턴(Stop) 끝날 때 이 세션 자신의
# scratchpad/tasks 안에서 STALE_HOURS(기본 24h) 이상 안 건드려진 개별 파일만
# 정리. 세션이 몇 주씩 재부팅 없이 떠 있어도 "끝날 때"가 아니라 "일하는
# 도중에도" 계속 자기 tmp를 스스로 비워내는 게 목적 (2026-07-20 사용자 요청 —
# SessionEnd/외부 daily timer 두 안 모두 반려, "진행중에 정리"만 채택).
# 최근에 만들었거나 건드린 파일은 다음 턴에서 또 쓸 수 있으니 그대로 둠 —
# 개별 파일 mtime 기준이라 활성 작업물은 자연히 보호됨. 항상 exit 0.

set -uo pipefail

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
STALE_HOURS="${STALE_HOURS:-24}"
LOG_FILE="$HOME/.claude/logs/scratchpad-cleanup.log"

[ -n "$SESSION_ID" ] && [ -n "$CWD" ] || exit 0

SANITIZED_CWD=$(echo "$CWD" | tr '/' '-')
SESSION_DIR="/tmp/claude-$(id -u)/${SANITIZED_CWD}/${SESSION_ID}"

[ -d "$SESSION_DIR" ] || exit 0

removed=$(find "$SESSION_DIR" -type f -mmin "+$(( STALE_HOURS * 60 ))" -print -delete 2>/dev/null)
[ -n "$removed" ] || exit 0

mkdir -p "$(dirname "$LOG_FILE")"
{
    echo "[$(date '+%Y-%m-%d %H:%M:%S KST')] Stop 자기청소($SESSION_ID, ${STALE_HOURS}h+):"
    echo "$removed" | sed 's/^/  /'
} >> "$LOG_FILE"

# 파일 지우고 남은 빈 디렉토리 정리(scratchpad/tasks 자체는 남겨둠 — 다음 턴에 재사용).
find "$SESSION_DIR" -mindepth 2 -type d -empty -delete 2>/dev/null

exit 0
