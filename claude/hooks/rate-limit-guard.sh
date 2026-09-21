#!/bin/bash
# rate-limit-guard.sh: PreToolUse hook. 5h 토큰 사용량 ≥ 90% 도달 시 거의 모든 tool 차단.
#
# 사용자 결정 (2026-04-29):
# - 단순 정책 (option A): 5h 사용량 ≥ 90%면 차단
# - 모든 tool 차단 — 예측 못 한 폭증으로 세션 lockup 방지
# - 예외 (claude가 응답 못 하면 안 되므로): telegram reply/edit/react 만 허용
#
# 데이터 소스: ~/.claude/statusline_cache.json
# - statusline 렌더링할 때마다 갱신. 글로벌 계정 수치라 신선함.
#
# 차단 시:
# - exit 2 (PreToolUse decision: block)
# - 첫 차단 시 텔레그램으로 1회 알림 (cooldown 30분)
#
# 비차단 시 exit 0 (정상 진행)

set -uo pipefail

THRESHOLD_5H=90
THRESHOLD_7D=99
CACHE=~/.claude/statusline_cache.json
ALERT_FLAG_PREFIX=~/.claude/state/rate-limit-alerted
COOLDOWN_SEC=$((30 * 60))
FALLBACK_TELEGRAM_ENV=~/dotfiles/.claude/telegram/.env

# 1. event JSON 읽기 (tool_name + cwd 추출). 못 읽으면 안전하게 통과.
EVENT=$(cat 2>/dev/null || echo '{}')
TOOL=$(printf '%s' "$EVENT" | python3 -c 'import json,sys
try:
  d=json.loads(sys.stdin.read())
  print(d.get("tool_name") or d.get("tool") or "")
except: print("")' 2>/dev/null)
CWD=$(printf '%s' "$EVENT" | python3 -c 'import json,sys
try:
  d=json.loads(sys.stdin.read())
  print(d.get("cwd") or "")
except: print("")' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"
# 세션 키 결정: 1) env, 2) cgroup으로 systemd unit 추적, 3) cwd 상향 탐색, 4) cwd basename
# 상향 탐색: 실험 작업트리(예: .../session-p/bridge01)에서 실행된 프로세스도 부모 프로젝트로 귀속.
# (2026-06-10 bridge01 오귀속 — cwd basename 이 세션명으로 잡혀 fallback 봇으로 알림 간 사고 후 추가)
PROJ="${CLAUDE_SESSION_NAME:-}"
UNIT_MATCH=$(grep -oE 'claude-[^/]+\.service' /proc/self/cgroup 2>/dev/null | head -1)
if [ -z "$PROJ" ]; then
    PROJ=$(printf '%s' "$UNIT_MATCH" | sed 's/^claude-//; s/\.service$//')
fi
# 2026-07-08 사용자 신고: systemd 유닛 없이 터미널에서 직접 띄운 임시 claude
# 세션(인바운드 경로 없음)이 rate-limit 차단 알림을 운영 챗으로 쏴서 스팸이
# 됨 — ai-debate run-20260708T071716Z 결론(안1 채택): CLAUDE_SESSION_NAME env도
# cgroup claude-*.service 매치도 없으면 유닛 밖 임시 세션으로 보고 텔레그램
# 알림만 skip(차단 판정 자체는 그대로 유지). fallback(.env 없는 유닛 세션들)은
# 그대로 유지 — 유닛 밖 세션만 조용해짐.
IS_UNIT_SESSION=false
{ [ -n "${CLAUDE_SESSION_NAME:-}" ] || [ -n "$UNIT_MATCH" ]; } && IS_UNIT_SESSION=true
ANCHOR_DIR=""
_d="$CWD"
while [ -n "$_d" ] && [ "$_d" != "/" ] && [ "$_d" != "$HOME" ]; do
    if [ -f "$_d/.claude/telegram/.env" ]; then ANCHOR_DIR="$_d"; break; fi
    _d=$(dirname "$_d")
done
if [ -z "$PROJ" ] && [ -n "$ANCHOR_DIR" ]; then
    PROJ=$(basename "$ANCHOR_DIR" | tr '_' '-')
fi
[ -z "$PROJ" ] && PROJ=$(basename "$CWD")
ALERT_FLAG="${ALERT_FLAG_PREFIX}-${PROJ}.flag"
PROJECT_TELEGRAM_ENV="${CWD}/.claude/telegram/.env"
if [ ! -f "$PROJECT_TELEGRAM_ENV" ] && [ -n "$ANCHOR_DIR" ]; then
    # cwd 자체엔 봇 설정이 없어도 상위 프로젝트 봇으로 라우팅
    PROJECT_TELEGRAM_ENV="$ANCHOR_DIR/.claude/telegram/.env"
fi
if [ ! -f "$PROJECT_TELEGRAM_ENV" ] && [ -n "$PROJ" ]; then
    # CWD가 빈/잘못된 경로일 수 있어 systemd WorkingDirectory로 재시도
    SD_WD=$(systemctl --user show "claude-${PROJ}.service" -p WorkingDirectory 2>/dev/null | sed 's/^WorkingDirectory=//')
    if [ -n "$SD_WD" ] && [ -f "$SD_WD/.claude/telegram/.env" ]; then
        PROJECT_TELEGRAM_ENV="$SD_WD/.claude/telegram/.env"
    fi
fi
if [ -f "$PROJECT_TELEGRAM_ENV" ]; then
    TELEGRAM_ENV="$PROJECT_TELEGRAM_ENV"
else
    TELEGRAM_ENV="$FALLBACK_TELEGRAM_ENV"
fi

# 1.4. 보조 서비스는 차단 대상 아님 (progress-updater 등은 백그라운드 작업).
case "$PROJ" in
    progress-updater|rate-limit-recovery|daily-research|control-bot|watchdog|"")
        exit 0 ;;
esac

# 1.5. bypass flag — 사용자가 임시로 90% 가드 무시하고 싶을 때
BYPASS_GLOBAL=~/.claude/state/rate-limit-bypass.flag
BYPASS_PROJ=~/.claude/state/rate-limit-bypass-${PROJ}.flag
if [ -f "$BYPASS_GLOBAL" ] || [ -f "$BYPASS_PROJ" ]; then
    exit 0
fi

# 2. 허용 목록 — 차단 중에도 통과시킬 tools (claude가 텔레그램 응답 못 하면 사용자 무시 상태됨)
case "$TOOL" in
    mcp__plugin_telegram_telegram__reply|\
    mcp__plugin_telegram_telegram__edit_message|\
    mcp__plugin_telegram_telegram__react|\
    mcp__plugin_telegram_telegram__download_attachment)
        exit 0
        ;;
esac

# 2.5. Bash 호출이 외부 AI (codex/agy/ai-debate) 면 통과.
#      이 명령들은 Claude API 한도와 무관 — 차단 중에도 토론/조사 가능해야 함.
#      ai-debate 내부에 claude pool 이 있으나 자식 프로세스라 우리 훅 대상 아님 (각 child claude 가 별도 429 핸들).
if [ "$TOOL" = "Bash" ]; then
    CMD=$(printf '%s' "$EVENT" | python3 -c 'import json,sys
try:
  d=json.loads(sys.stdin.read())
  inp=d.get("tool_input") or {}
  print(inp.get("command",""))
except: print("")' 2>/dev/null)
    # 명령 첫 토큰 추출 (env VAR=... prefix 무시).
    FIRST=$(printf '%s' "$CMD" | awk '{for(i=1;i<=NF;i++){if($i !~ /=/){print $i; exit}}}')
    case "$FIRST" in
        ai-debate|ai-debate-detach|ai-collaborate|codex-ask|gemini-ask|agy)
            exit 0
            ;;
    esac
fi

# 3. 5h / 7d 사용량 체크 (둘 중 하나라도 임계 초과면 차단)
# 판정 규칙은 여기서 만들지 않는다 — lib 한 벌(claude-quota-observe)을 부른다.
# 옛 방식은 이 계산을 여기·prompt-guard·recovery 에 복제해 뒀고 셋 다 캐시가 언 것을
# 못 봐서, 얼어붙은 7일 창 99% 가 한도 해제 뒤에도 세션을 계속 가뒀다(2026-08-28 옆 기계
# 6시간). 낡은 캐시는 이제 차단 근거가 아니다 — 실제로 막혀 있으면 그 턴이 429 로
# 실패하며 캐시를 새로 채우므로 열어두는 비용은 한 턴이다.
[ -f "$CACHE" ] || exit 0
# 저장소 안 경로를 먼저 본다(훅은 심링크라 realpath 로 원본을 찾는다). 없으면 PATH 설치본.
OBSERVE="$(dirname "$(realpath "${BASH_SOURCE[0]}")")/../../bin/claude-quota-observe"
[ -x "$OBSERVE" ] || OBSERVE="$HOME/.local/bin/claude-quota-observe"
eval "$("$OBSERVE" --sh 2>/dev/null)"
[ -z "${QUOTA_BLOCKED:-}" ] && exit 0     # 관측 실패 = fail-open

PCT_5H="${QUOTA_FH_PCT:--1}"
PCT_7D="${QUOTA_SD_PCT:--1}"
RESET_5H_AT="${QUOTA_FH_RESET_AT:-}"
RESET_5H_REMAIN="${QUOTA_FH_RESET_REMAIN:-}"
RESET_7D_AT="${QUOTA_SD_RESET_AT:-}"
RESET_7D_REMAIN="${QUOTA_SD_RESET_REMAIN:-}"

# 차단 사유 결정 — blocked=true 일 때만 사유를 만든다.
TRIGGER=""
if [ "$QUOTA_BLOCKED" = "true" ]; then
    if [ "$PCT_5H" -ge "$THRESHOLD_5H" ] 2>/dev/null; then
        TRIGGER="5h ${PCT_5H}% (>= ${THRESHOLD_5H}%) — 풀리는 시각 ${RESET_5H_AT} (앞으로 ${RESET_5H_REMAIN})"
    fi
    if [ "$PCT_7D" -ge "$THRESHOLD_7D" ] 2>/dev/null; then
        if [ -n "$TRIGGER" ]; then
            TRIGGER="$TRIGGER + 7d ${PCT_7D}% (>= ${THRESHOLD_7D}%) — 풀리는 시각 ${RESET_7D_AT} (앞으로 ${RESET_7D_REMAIN})"
        else
            TRIGGER="7d ${PCT_7D}% (>= ${THRESHOLD_7D}%) — 풀리는 시각 ${RESET_7D_AT} (앞으로 ${RESET_7D_REMAIN})"
        fi
    fi
fi

[ -z "$TRIGGER" ] && exit 0

# 4. 차단 — 텔레그램 1회 알림 (cooldown 30분)
mkdir -p "$(dirname "$ALERT_FLAG")"
need_alert=true
if [ -f "$ALERT_FLAG" ]; then
    age=$(( $(date +%s) - $(stat -c %Y "$ALERT_FLAG" 2>/dev/null || echo 0) ))
    [ "$age" -lt "$COOLDOWN_SEC" ] && need_alert=false
fi

# 프로젝트별 봇으로 직접 알림 (각 세션 채팅에 자기 알림만 보이게).
# 유닛 밖 임시 세션은 텔레그램 알림 skip(위 IS_UNIT_SESSION 판정 참고).
if [ "$need_alert" = true ] && [ "$IS_UNIT_SESSION" = true ] && [ -f "$TELEGRAM_ENV" ]; then
    . "$TELEGRAM_ENV"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
        msg="[${PROJ}] rate-limit-guard 작동. 모든 tool 차단 (telegram reply/edit/react/download만 허용).
사유: ${TRIGGER}
첫 차단 tool: ${TOOL:-?}"
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${TELEGRAM_CHAT_ID:-}" \
            --data-urlencode "text=${msg}" >/dev/null 2>&1
        touch "$ALERT_FLAG"
    fi
fi

# 4.5. 차단 시 재개 검토 신호를 큐에 한 칸 남긴다.
# 문제: 세션을 밖에서 깨우는 길은 텔레그램뿐인데, recovery(5분 timer)는 차단→해제 전환에서
#       큐가 비어있지 않은 프로젝트에만 trigger:queue-flush 를 보낸다. 차단된 세션의 큐가
#       비어 있으면 한도가 풀려도 아무도 깨우지 않는다.
#       (2026-09-20 session-b: 첫 차단 tool=Bash → 마커 없음 → 04:53 부터 정지.)
# 범위: 도구 이름을 가리지 않는다. 어떤 도구가 막혔는지는 그 세션이 이어갈 일이 있는지와
#       무관하다. 다만 유닛 밖 임시 세션은 제외한다 — 수신 경로(전용 봇)가 없어서
#       깨울 수 없고, 마커만 큐에 남아 recovery 가 매번 헛돈다(위 IS_UNIT_SESSION 판정).
# 내용: 실행 명령이 아니라 재개 검토 신호다. 자율루프 여부·progress.md 존재·해제 사실을
#       단정하지 않고, 취소 지시 확인을 먼저 시키고, 이어갈 일이 없으면 턴을 끝내게 한다.
# 한도/토큰 우회 아님 — 도구는 그대로 차단(아래 exit 2), 빵부스러기만 남긴다.
if [ "$IS_UNIT_SESSION" = true ]; then
    QUEUE_DIR=~/.claude/state/rate-limit-queue
    QUEUE_FILE="$QUEUE_DIR/${PROJ}.jsonl"
    LOCK_FILE="$QUEUE_DIR/${PROJ}.lock"
    MARKER_ID="autoresume-${PROJ}"
    mkdir -p "$QUEUE_DIR"
    # prompt-guard 의 적재·flush, recovery 의 정리와 같은 락. 검사와 적재를 한 락 안에서 한다.
    (
        flock -x 200
        # 프로젝트당 고정 id — 한 차단 구간에서 도구가 몇 번 막히든 마커는 하나다.
        if ! grep -q "\"id\": *\"$MARKER_ID\"" "$QUEUE_FILE" 2>/dev/null; then
            # recovery 가 봇 라우팅에 쓰는 cwd 기록 (큐 비어도 .cwd 가 최신이도록 prompt-guard 와 동일).
            echo "$CWD" > "$QUEUE_DIR/${PROJ}.cwd" 2>/dev/null || true
            python3 - "$MARKER_ID" "$PROJ" "$QUEUE_FILE" <<'PY' 2>/dev/null
import json, sys, time, datetime
mid, proj, qf = sys.argv[1], sys.argv[2], sys.argv[3]
now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
prompt = ("[한도 차단 후 재개 검토 신호] 이 세션의 도구 호출이 한도 가드에 막힌 적이 있다. "
          "이 신호는 새 사용자 요청도 추가 실행 승인도 아니다. 지금 들어온 입력과 대기 중인 "
          "사용자 메시지 전체에서 취소·중지·변경 지시를 먼저 확인하라. 기존 대화에서 여전히 "
          "유효하고 승인된 미완료 작업이 확인될 때만 이어가라. 이미 끝났거나 취소됐거나 "
          "맥락이 부족하면 할 일을 추측하거나 새로 만들지 말고 턴을 끝내라. 예약이 필요한 "
          "작업이었다면 현재 예약 상태를 확인해 복원하되 새 자율루프나 중복 예약을 만들지 "
          "마라. 다시 한도에 막히면 같은 턴에서 호출을 되풀이하지 마라.")
rec = {"id": mid, "ts": now.isoformat(timespec="seconds"), "ts_unix": int(time.time()),
       "project": proj, "cause": "rate_limit_blocked", "state": "pending",
       "retry_count": 0, "prompt": prompt}
with open(qf, "a") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
PY
            echo "rate-limit-guard: ${TOOL:-?} 차단 — 재개 검토 신호 큐 적재 (${PROJ})" >&2
        fi
    ) 200>"$LOCK_FILE"
fi

# 5. PreToolUse decision: block
echo "rate-limit-guard: ${TRIGGER}. tool=${TOOL} 차단" >&2
exit 2
