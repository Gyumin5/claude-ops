#!/bin/bash
# Desktop notification when Claude finishes a response
# Works on Linux (notify-send) and macOS (osascript)
if command -v notify-send &>/dev/null; then
  notify-send "Claude Code" "작업이 완료되었습니다" --icon=dialog-information 2>/dev/null
elif command -v osascript &>/dev/null; then
  osascript -e 'display notification "작업이 완료되었습니다" with title "Claude Code"' 2>/dev/null
fi
exit 0
