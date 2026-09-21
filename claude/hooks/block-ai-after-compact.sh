#!/bin/bash
# block-ai-after-compact: PreToolUse 훅. 압축 직후 AI 자발 재호출만 차단.
# - 압축 플래그 있을 때 gemini-ask/codex-ask/ai-collab 패턴 Bash 명령 검사
# - 사용자 최신 메시지에 AI 요청 키워드가 있으면 허용 (사용자 명시 요청)
# - 없으면 차단 (모델 자발 재호출로 간주)
# fallback: 플래그 5분 지나면 자동 해제

set -uo pipefail

FLAG="$HOME/.claude/state/just-compacted.flag"
BLOCK_WINDOW_SEC=300  # 5분 fallback

INPUT=$(cat 2>/dev/null || echo '{}')
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

[ -z "$CMD" ] && exit 0

# AI 호출 패턴
if ! echo "$CMD" | grep -qE 'gemini-ask|codex-ask|ai-collab'; then
    exit 0
fi

# 압축 플래그
[ ! -f "$FLAG" ] && exit 0

now=$(date +%s)
mtime=$(stat -c %Y "$FLAG" 2>/dev/null || echo 0)
age=$(( now - mtime ))

if [ "$age" -gt "$BLOCK_WINDOW_SEC" ]; then
    rm -f "$FLAG"
    exit 0
fi

# 사용자 최신 메시지 확인 — 명시적 AI 요청이면 허용
proj_key=$(echo "$CWD" | sed 's|/|-|g')
latest_jsonl=$(ls -t "$HOME/.claude/projects/${proj_key}"/*.jsonl 2>/dev/null | head -1)

if [ -n "$latest_jsonl" ]; then
    # 최근 50줄에서 마지막 user message 추출
    last_user_msg=$(python3 -c "
import json, re
last=''
try:
    with open('$latest_jsonl') as f:
        for line in f:
            try:
                d=json.loads(line)
                if d.get('type')=='user' and d.get('message',{}).get('role')=='user':
                    c=d.get('message',{}).get('content','')
                    if isinstance(c,str):
                        last=c
                    elif isinstance(c,list):
                        s=''
                        for x in c:
                            if isinstance(x,dict) and x.get('type')=='text':
                                s+=x.get('text','')
                        last=s or last
            except: pass
    print(last[:2000])
except: print('')
" 2>/dev/null)

    # AI 요청 키워드 검사 (한국어+영어)
    if echo "$last_user_msg" | grep -qiE 'gemini|codex|크로스체크|세컨드 오피니언|ai 토론|ai 협업|ai한테|다른 ai|ai 의견'; then
        # 사용자가 명시적으로 AI 요청함 → 허용 + 플래그 해제
        rm -f "$FLAG"
        exit 0
    fi
fi

# 사용자 명시 요청 없음 → 차단
remaining=$(( BLOCK_WINDOW_SEC - age ))
echo "[block-ai-after-compact] 압축 ${age}초 지남, 사용자 AI 요청 없음 → 차단 (${remaining}초 후 자동 해제). 필요하면 텔레그램으로 'gemini' 또는 'codex' 포함 요청 보내면 통과." >&2

exit 2
