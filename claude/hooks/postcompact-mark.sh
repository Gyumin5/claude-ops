#!/bin/bash
# postcompact-mark: 압축 직후 플래그 파일 생성. UserPromptSubmit 훅이 다음 사용자 메시지 처리 전에 경고를 주입하도록 신호.

set -uo pipefail
FLAG_DIR="$HOME/.claude/state"
mkdir -p "$FLAG_DIR"
touch "$FLAG_DIR/just-compacted.flag"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] postcompact mark created" >> "$HOME/.claude/logs/precompact.log"
exit 0
