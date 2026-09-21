#!/bin/bash
# prompt-length-guard.sh: UserPromptSubmit hook.
#
# 목적: session-p 같은 "Prompt is too long" 영구 stuck 사고 방지.
# 사용자 prompt + 자동 주입(progress/active/CLAUDE.md) + 현재 컨텍스트 사용량을
# 합산 추정해서 1M 토큰 한계 근처면 경고/차단.
#
# 동작:
# - 추정 합계 >= BLOCK_TOKENS (800K): exit 2로 차단 + 텔레그램 알림 + stderr 안내
# - 추정 합계 >= WARN_TOKENS (600K): additionalContext에 경고 주입, 진행은 허용
# - 미만: 통과

set -uo pipefail

WARN_TOKENS=600000
BLOCK_TOKENS=800000
CONTEXT_LIMIT=1000000

# progress-updater 같은 자동 호출은 통과 (자식 claude -p)
[ "${CLAUDE_AUTOMATED:-0}" = "1" ] && exit 0

INPUT=$(cat 2>/dev/null || echo '{}')
USER_PROMPT=$(printf '%s' "$INPUT" | python3 -c "import json,sys
try:
    d=json.load(sys.stdin); print(d.get('prompt',''),end='')
except Exception:
    pass" 2>/dev/null)
CWD=$(printf '%s' "$INPUT" | python3 -c "import json,sys
try:
    d=json.load(sys.stdin); print(d.get('cwd',''),end='')
except Exception:
    pass" 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

# 세션 키: env > cgroup > cwd basename
PROJ="${CLAUDE_SESSION_NAME:-}"
if [ -z "$PROJ" ]; then
    PROJ=$(grep -oE 'claude-[^/]+\.service' /proc/self/cgroup 2>/dev/null | head -1 | sed 's/^claude-//; s/\.service$//')
fi
[ -z "$PROJ" ] && PROJ=$(basename "$CWD")

# byte→token 보수적 추정 (≈ byte/3.5)
estimate_tokens() {
    local b="${1:-0}"
    echo $(( b * 10 / 35 ))
}

# 1) 사용자 prompt
PROMPT_BYTES=$(printf '%s' "$USER_PROMPT" | wc -c)
PROMPT_TOK=$(estimate_tokens "$PROMPT_BYTES")

# 2) 자동 주입 추정: progress.md + history/active.md + 프로젝트/글로벌 CLAUDE.md
INJ_BYTES=0
for f in \
    "$CWD/progress.md" \
    "$CWD/history/active.md" \
    "$CWD/CLAUDE.md" \
    "$HOME/.claude/CLAUDE.md"; do
    [ -f "$f" ] && INJ_BYTES=$(( INJ_BYTES + $(wc -c < "$f") ))
done
INJ_TOK=$(estimate_tokens "$INJ_BYTES")

# 3) 현재 누적 컨텍스트 사용량 (statusline cache)
CACHE_CTX_TOK=0
if [ -f "$HOME/.claude/statusline_cache.json" ]; then
    CACHE_CTX_TOK=$(python3 - <<'PYEOF' 2>/dev/null
import json, os
try:
    with open(os.path.expanduser('~/.claude/statusline_cache.json')) as f:
        d = json.load(f)
    cw = d.get('context_window', {}) or {}
    pct = cw.get('used_percentage', 0) or 0
    sz = cw.get('context_window_size', 1000000) or 1000000
    print(int(pct * sz / 100))
except Exception:
    print(0)
PYEOF
)
fi
[ -z "$CACHE_CTX_TOK" ] && CACHE_CTX_TOK=0

TOTAL_TOK=$(( PROMPT_TOK + INJ_TOK + CACHE_CTX_TOK ))

# 차단
if [ "$TOTAL_TOK" -ge "$BLOCK_TOKENS" ]; then
    PROJECT_TELEGRAM_ENV="${CWD}/.claude/telegram/.env"
    if [ -f "$PROJECT_TELEGRAM_ENV" ]; then
        TELEGRAM_ENV="$PROJECT_TELEGRAM_ENV"
    else
        TELEGRAM_ENV="$HOME/dotfiles/.claude/telegram/.env"
    fi
    if [ -f "$TELEGRAM_ENV" ]; then
        . "$TELEGRAM_ENV" 2>/dev/null || true
        if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
            MSG="[${PROJ}] prompt-length-guard 차단: 추정 ${TOTAL_TOK} 토큰 (한계 ${CONTEXT_LIMIT}, 차단선 ${BLOCK_TOKENS}). 입력+주입 합산이 너무 큼.

내역: 사용자 ${PROMPT_TOK} + 주입 ${INJ_TOK} + 누적 ${CACHE_CTX_TOK}

대처: 큰 PDF/로그는 ctx_read mode=lines:N-M 또는 offset/limit 사용. 통째 paste 금지, 파일 path만. 본문 요약 후 재전송도 가능."
            curl -s --max-time 5 \
                -d "chat_id=${TELEGRAM_CHAT_ID}" \
                --data-urlencode "text=${MSG}" \
                "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" >/dev/null 2>&1 || true
        fi
    fi
    echo "[prompt-length-guard] BLOCK: 추정 ${TOTAL_TOK}/${BLOCK_TOKENS} 토큰 (사용자 ${PROMPT_TOK} + 주입 ${INJ_TOK} + 누적 ${CACHE_CTX_TOK}). ctx_read lines:N-M 또는 분할로 재전송." >&2
    exit 2
fi

# 경고 (모델한테 주의 환기)
if [ "$TOTAL_TOK" -ge "$WARN_TOKENS" ]; then
    python3 - <<PYEOF
import json
out = {
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "[prompt-length-guard 경고] 추정 컨텍스트 ${TOTAL_TOK} 토큰 (한계 ${CONTEXT_LIMIT}, 차단선 ${BLOCK_TOKENS}). 이번 턴은 큰 read/tool_result 자제. ctx_read mode=lines:N-M 또는 ctx_search로 좁혀서 호출. 답변 마치면 progress.md 갱신 권고."
    }
}
print(json.dumps(out, ensure_ascii=False))
PYEOF
    exit 0
fi

exit 0
