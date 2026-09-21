#!/bin/bash
# completion-notify.sh: Stop hook.
# 크로스세션으로 --notify-done 주입된 작업이 끝나면, 받은 세션의 자기 봇으로
# 운영자 챗에 "작업완료" 핑 1회. 마커 [[xnotify:from=...]] 가 현재 턴 프롬프트에
# 있을 때만 발동. 일반 대화 턴은 영향 없음(스팸 방지).
# 발송은 운영자 단일 챗(${TELEGRAM_CHAT_ID:-})으로만 — 세션 재주입 없음 → 무한핑퐁 0.
# 항상 exit 0 (종료 차단 안 함, 기존 telegram-reply-enforcer 와 공존).

set -uo pipefail

# 자동 작업(claude -p 등)은 스킵.
[ "${CLAUDE_AUTOMATED:-0}" = "1" ] && exit 0

EVENT=$(cat 2>/dev/null || echo '{}')

# python: 마커 감지 + from / 결과꼬리 추출. 결과는 첫 줄에 SKIP 또는 NOTIFY.
RESULT=$(EVENT_JSON="$EVENT" python3 <<'PYEOF' 2>/dev/null
import json, os, re, sys

def out(*a):
    print("\t".join(a)); sys.exit(0)

try:
    e = json.loads(os.environ.get("EVENT_JSON", "{}"))
except Exception:
    out("SKIP")

if e.get("stop_hook_active"):
    out("SKIP")

tp = e.get("transcript_path")
if not tp or not os.path.exists(tp):
    out("SKIP")

try:
    with open(tp, "rb") as f:
        f.seek(0, 2); size = f.tell()
        f.seek(max(0, size - 1_000_000))
        lines = f.read().decode("utf-8", errors="ignore").splitlines()
except Exception:
    out("SKIP")

parsed = []
for line in lines:
    try: parsed.append(json.loads(line))
    except Exception: parsed.append(None)

def user_text(d):
    """type=user 의 실제 입력 텍스트 (tool_result 제외). 아니면 None."""
    if not d or d.get("type") != "user":
        return None
    c = (d.get("message", {}) or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        for it in c:
            if isinstance(it, dict):
                if it.get("type") == "tool_result" or "tool_use_id" in it:
                    return None
        txts = [it.get("text", "") for it in c if isinstance(it, dict) and (it.get("type") == "text" or "text" in it)]
        return "\n".join(t for t in txts if t) or None
    return None

# 마지막 실제 user prompt 인덱스 = 현재 턴 시작.
last_user_idx = -1
last_user_txt = None
for i in range(len(parsed) - 1, -1, -1):
    t = user_text(parsed[i])
    if t is not None:
        last_user_idx = i; last_user_txt = t; break

if last_user_idx < 0 or not last_user_txt:
    out("SKIP")

# 출처 마커 [[xsrc:from=<origin>[:notify]]]. :notify 가 있을 때만 완료 핑.
m = re.search(r"\[\[xsrc:from=([^\]:\s]+)(:notify)?\]\]", last_user_txt)
if not m or not m.group(2):
    out("SKIP")
origin = m.group(1) or "?"

# 결과 꼬리: 실제 운영자에게 보낸 telegram reply 도구의 text 우선,
# 없으면 마지막 assistant 텍스트 블록(모델 혼잣말일 수 있음) fallback.
reply_text = ""
text_block = ""
for d in parsed[last_user_idx:]:
    if d and d.get("type") == "assistant":
        c = (d.get("message", {}) or {}).get("content", [])
        if isinstance(c, list):
            for it in c:
                if not isinstance(it, dict):
                    continue
                if it.get("type") == "tool_use" and it.get("name") == "mcp__plugin_telegram_telegram__reply":
                    t = (it.get("input") or {}).get("text")
                    if t:
                        reply_text = t
                elif it.get("type") == "text" and it.get("text"):
                    text_block = it["text"]
tail = re.sub(r"\s+", " ", (reply_text or text_block)).strip()[:400]

# dedup 키: transcript + user 인덱스 (같은 턴 중복 발송 방지).
import hashlib
key = hashlib.sha1(f"{tp}:{last_user_idx}".encode()).hexdigest()[:16]
out("NOTIFY", origin, key, tail)
PYEOF
)

DECISION=$(printf '%s' "$RESULT" | cut -f1)
[ "$DECISION" = "NOTIFY" ] || exit 0

ORIGIN=$(printf '%s' "$RESULT" | cut -f2)
KEY=$(printf '%s' "$RESULT" | cut -f3)
TAIL=$(printf '%s' "$RESULT" | cut -f4)

# dedup
SENT_DIR=~/.claude/state/xnotify-sent
mkdir -p "$SENT_DIR"
FLAG="${SENT_DIR}/${KEY}.flag"
[ -f "$FLAG" ] && exit 0

# 세션/머신 식별.
CWD=$(printf '%s' "$EVENT" | python3 -c 'import json,sys
try: print(json.loads(sys.stdin.read()).get("cwd") or "")
except: print("")' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"
PROJ="${CLAUDE_SESSION_NAME:-}"
if [ -z "$PROJ" ]; then
    PROJ=$(grep -oE 'claude-[^/]+\.service' /proc/self/cgroup 2>/dev/null | head -1 | sed 's/^claude-//; s/\.service$//')
fi
[ -z "$PROJ" ] && PROJ=$(basename "$CWD")
MACHINE=$(cat ~/.config/claude-machine-id 2>/dev/null || hostname)

# 봇 토큰 해석 (rate-limit-guard 와 동일 규칙).
PROJECT_TELEGRAM_ENV="${CWD}/.claude/telegram/.env"
if [ ! -f "$PROJECT_TELEGRAM_ENV" ] && [ -n "$PROJ" ]; then
    SD_WD=$(systemctl --user show "claude-${PROJ}.service" -p WorkingDirectory 2>/dev/null | sed 's/^WorkingDirectory=//')
    [ -n "$SD_WD" ] && [ -f "$SD_WD/.claude/telegram/.env" ] && PROJECT_TELEGRAM_ENV="$SD_WD/.claude/telegram/.env"
fi
[ -f "$PROJECT_TELEGRAM_ENV" ] || PROJECT_TELEGRAM_ENV=~/dotfiles/.claude/telegram/.env
[ -f "$PROJECT_TELEGRAM_ENV" ] || exit 0

. "$PROJECT_TELEGRAM_ENV"
[ -n "${TELEGRAM_BOT_TOKEN:-}" ] || exit 0

NOW=$(date '+%Y-%m-%d %H:%M KST')
MSG="✅ [${PROJ}@${MACHINE}] 크로스세션 작업 완료
요청: ${ORIGIN}
시각: ${NOW}"
[ -n "$TAIL" ] && MSG="${MSG}
결과: ${TAIL}"

curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID:-}" \
    --data-urlencode "text=${MSG}" >/dev/null 2>&1 && touch "$FLAG"

exit 0
