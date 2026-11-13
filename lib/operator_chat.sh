#!/bin/bash
# 운영자 텔레그램 방 번호를 한 곳에서 읽는다. 셸 쪽 정본 — 파이썬 쪽은 lib/operator_chat.py.
#
# 쓰는 법:
#   source "$(dirname "$(realpath "$0")")/../lib/operator_chat.sh"
#   CHAT_ID="$(operator_chat_id)" || exit 1
#
# 기본값을 안 둔다. 설정이 없으면 거절하고 고치는 법을 stderr 로 남긴다 —
# 기본값이 있으면 설정을 안 만든 기계가 조용히 남의 방으로 보낸다.

operator_chat_id() {
  local cfg="$HOME/.config/dotfiles-private/telegram.json"
  local value="${CLAUDE_OPERATOR_CHAT_ID:-}"
  if [[ -z "$value" && -r "$cfg" ]]; then
    value="$(sed -n 's/.*"operator_chat_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$cfg" | head -1)"
  fi
  if [[ -z "$value" ]]; then
    echo "운영자 텔레그램 방 번호가 없다. $cfg 에 {\"operator_chat_id\": \"<방 번호>\"} 를 적어라 (권한 600)." >&2
    echo "환경변수 CLAUDE_OPERATOR_CHAT_ID 로도 넘길 수 있다." >&2
    return 1
  fi
  printf '%s' "$value"
}
