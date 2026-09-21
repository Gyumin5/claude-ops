#!/bin/bash
# inject-briefing: UserPromptSubmit 훅. dotfiles 세션에서만 동작.
# ~/.claude/state/briefings/YYYY-MM-DD.md 가 unread 상태면 (read 마크보다 새로움) 1회 주입 후 read 마크 갱신.

set -uo pipefail

# 자동 작업은 브리핑 주입 스킵.
[ "${CLAUDE_AUTOMATED:-0}" = "1" ] && exit 0

INPUT=$(cat 2>/dev/null || echo '{}')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

# dotfiles에서만 동작
[ "$CWD" = "$HOME/dotfiles" ] || exit 0

BRIEFING_DIR="$HOME/.claude/state/briefings"
READ_MARK="$HOME/.claude/state/briefings-read.flag"
[ -d "$BRIEFING_DIR" ] || exit 0

# 가장 최신 브리핑 파일 찾기
LATEST=$(ls -1t "$BRIEFING_DIR"/*.md 2>/dev/null | head -1)
[ -z "$LATEST" ] && exit 0
[ -f "$LATEST" ] || exit 0

# read mark보다 새로운 파일만 주입
if [ -f "$READ_MARK" ]; then
    [ "$LATEST" -nt "$READ_MARK" ] || exit 0
fi

# 주입할 내용 (8000자 이내로 자름)
content=$(head -c 8000 "$LATEST")
date_part=$(basename "$LATEST" .md)

# 주입 후 read mark 갱신
touch "$READ_MARK"

# JSON으로 additionalContext 출력
python3 <<EOF
import json
ctx = """## 매일 브리핑 ($date_part)

$content"""
out = {
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": ctx
    }
}
print(json.dumps(out, ensure_ascii=False))
EOF

exit 0
