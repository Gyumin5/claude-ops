#!/bin/bash
# telegram-reply-injector.sh: UserPromptSubmit hook.
# 사용자 prompt에 텔레그램 채널 마커가 있으면 additionalContext에 강제 reminder 삽입.
# Stop 훅 enforcer와 짝. 사전 알림 + 사후 강제 = 이중 안전장치.

set -uo pipefail

# 자동 작업(progress-updater 등)에서 호출된 claude -p는 사용자 세션이 아니므로 스킵.
[ "${CLAUDE_AUTOMATED:-0}" = "1" ] && exit 0

EVENT=$(cat 2>/dev/null || echo '{}')
PROMPT=$(printf '%s' "$EVENT" | python3 -c 'import json,sys
try:
  d=json.loads(sys.stdin.read())
  print(d.get("prompt") or d.get("user_prompt") or "")
except: print("")' 2>/dev/null)

# 채널 마커 없으면 통과
case "$PROMPT" in
    *'source="plugin:telegram:telegram"'*) ;;
    *) exit 0 ;;
esac

# chat_id 추출 (응답 가독성용 안내)
CHAT_ID=$(printf '%s' "$PROMPT" | grep -oE 'chat_id="[0-9]+"' | head -1 | tr -dc 0-9)

cat <<EOF
[telegram-reply-injector — 강제 컨텍스트]

이 메시지는 Telegram 채널에서 도착했다.
응답 규칙 (위반 시 Stop 훅이 종료를 차단함):

1. 답변은 반드시 mcp__plugin_telegram_telegram__reply 도구 호출로 전달한다.
2. 텍스트만 출력하고 reply 도구를 호출하지 않으면 사용자는 아무것도 못 본다 = 침묵.
3. chat_id 는 메시지 태그의 chat_id 값 그대로 사용 (현재 메시지: ${CHAT_ID:-?}).
4. 작업이 길면 시작 시 1회 (예: "확인 중") + 완료 시 1회 reply, 최소 2번.
5. 도구 호출 결과는 reply 안에 요약해서 사용자에게 한국어로 전달.

이 안내는 반복 누락을 방지하기 위해 매 텔레그램 메시지마다 자동 주입된다.
EOF
exit 0
