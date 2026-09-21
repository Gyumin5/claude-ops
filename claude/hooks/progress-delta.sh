#!/bin/bash
# progress-delta: PostToolUse 훅. tool_use 발생할 때마다 작은 jsonl 한 줄 append.
# 의도: text turn 없이 도구 호출만 많은 세션도 활동 흔적을 progress-updater 가 볼 수 있게.
# LLM 호출 X, 작은 stdio 처리만. 비용 무시 수준.
#
# 저장: ~/.claude/state/progress-delta/<proj-basename>.jsonl
# 포맷: {"ts":"...","tool":"...","kind":"...","hint":"..."}

set -uo pipefail

INPUT=$(cat 2>/dev/null || echo '{}')
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
[ -z "$TOOL" ] && exit 0

CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"
proj=$(basename "$CWD")

# tool 별 hint 추출 (사람 읽기 좋은 짧은 단서).
hint=""
kind="tool"
case "$TOOL" in
    Bash)
        kind="bash"
        hint=$(echo "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null | head -c 80 | tr '\n' ' ')
        ;;
    Edit|Write|NotebookEdit)
        kind="write"
        hint=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""' 2>/dev/null | head -c 80)
        ;;
    Read)
        kind="read"
        hint=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""' 2>/dev/null | head -c 80)
        ;;
    WebFetch|WebSearch)
        kind="web"
        hint=$(echo "$INPUT" | jq -r '.tool_input.url // .tool_input.query // ""' 2>/dev/null | head -c 80)
        ;;
    Agent)
        kind="agent"
        hint=$(echo "$INPUT" | jq -r '.tool_input.description // ""' 2>/dev/null | head -c 80)
        ;;
    *)
        hint=""
        ;;
esac

# 작은 hint 만 저장. 노이즈 큰 도구 (ToolSearch 등) 는 카운트만.
case "$TOOL" in
    ToolSearch|TaskCreate|TaskUpdate|TaskList|TaskGet|TaskOutput)
        hint=""
        ;;
esac

DELTA_DIR="$HOME/.claude/state/progress-delta"
mkdir -p "$DELTA_DIR"
DELTA_FILE="$DELTA_DIR/${proj}.jsonl"

# 동시성: append 만 하므로 race 안전. flock 생략 (성능 우선).
ts=$(date -Iseconds)
jq -nc \
    --arg ts "$ts" \
    --arg tool "$TOOL" \
    --arg kind "$kind" \
    --arg hint "$hint" \
    '{ts:$ts, tool:$tool, kind:$kind, hint:$hint}' \
    >> "$DELTA_FILE" 2>/dev/null

# 크기 관리: 1000줄 넘으면 tail 500 만 유지 (오래된 활동 자연 폐기).
lines=$(wc -l < "$DELTA_FILE" 2>/dev/null || echo 0)
if [ "$lines" -gt 1000 ]; then
    tail -n 500 "$DELTA_FILE" > "${DELTA_FILE}.tmp" && mv "${DELTA_FILE}.tmp" "$DELTA_FILE"
fi

exit 0
