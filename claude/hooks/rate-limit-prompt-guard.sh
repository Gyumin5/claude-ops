#!/bin/bash
# rate-limit-prompt-guard.sh: UserPromptSubmit hook.
#
# 기능:
# 1. rate-limit이 차단 상태면 사용자 prompt를 큐에 저장하고 prompt 처리 자체를 차단(exit 2).
#    - 클로드 모델 호출 안 됨 → 토큰 0
#    - 텔레그램으로 "큐잉됨 (N번째)" 알림 1회 (cooldown 5분)
# 2. 차단 풀린 상태에서 큐 파일이 있으면 → additionalContext에 큐 내용 prepend → exit 0으로 정상 진행.
#    - 클로드가 큐된 메시지들 + 현재 prompt를 같이 받아서 차례로 답변.
#    - 큐 파일 비움.
# 3. 특수 trigger 메시지 ("trigger:queue-flush") 감지 시 큐만 flush하고 trigger 자체는 사용자에 노출 X.
#    - userbot이 보내는 깨우기 신호용. (userbot 미도입 상태에서는 사용자가 아무 메시지나 보내면 큐 flush됨.)

set -uo pipefail

THRESHOLD_5H=90
THRESHOLD_7D=99
CACHE=~/.claude/statusline_cache.json
QUEUE_DIR=~/.claude/state/rate-limit-queue
ALERT_FLAG_PREFIX=~/.claude/state/rate-limit-prompt-alert
COOLDOWN_SEC=300
FALLBACK_TELEGRAM_ENV=~/dotfiles/.claude/telegram/.env

mkdir -p "$QUEUE_DIR"

# 자동 작업(progress-updater 등 claude -p)은 큐잉 대상 아님.
[ "${CLAUDE_AUTOMATED:-0}" = "1" ] && exit 0

# 1. event JSON 읽기
EVENT=$(cat 2>/dev/null || echo '{}')
USER_PROMPT=$(printf '%s' "$EVENT" | python3 -c 'import json,sys
try:
  d=json.loads(sys.stdin.read())
  print(d.get("prompt") or d.get("user_prompt") or "")
except: print("")' 2>/dev/null)
CWD=$(printf '%s' "$EVENT" | python3 -c 'import json,sys
try:
  d=json.loads(sys.stdin.read())
  print(d.get("cwd") or "")
except: print("")' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"
# 세션 키 결정: 1) env, 2) cgroup으로 systemd unit 추적, 3) cwd basename
PROJ="${CLAUDE_SESSION_NAME:-}"
if [ -z "$PROJ" ]; then
    PROJ=$(grep -oE 'claude-[^/]+\.service' /proc/self/cgroup 2>/dev/null | head -1 | sed 's/^claude-//; s/\.service$//')
fi
[ -z "$PROJ" ] && PROJ=$(basename "$CWD")
QUEUE_FILE="$QUEUE_DIR/${PROJ}.jsonl"
# 동시 invocation(append vs flush) 간 큐파일 레이스 방지용 lock.
LOCK_FILE="$QUEUE_DIR/${PROJ}.lock"
# flush 로 큐에서 제거되는 항목의 durable 백업(무기록 유실 방지, 최근 200줄 유지).
FLUSHED_ARCHIVE="$QUEUE_DIR/${PROJ}.flushed.jsonl"

# trigger 판정: 텔레그램 메시지는 prompt 가 <channel ...>trigger:queue-flush</channel>
# 형태로 감싸져 오므로 == 가 아니라 "포함(contains)" 으로 본다.
IS_TRIGGER=false
case "$USER_PROMPT" in *trigger:queue-flush*) IS_TRIGGER=true ;; esac

# 보조 서비스(progress-updater 등)는 큐잉/차단 대상 아님. 사용자 세션이 아니므로 통과.
case "$PROJ" in
    progress-updater|rate-limit-recovery|daily-research|control-bot|watchdog|"")
        exit 0 ;;
esac
CWD_FILE="$QUEUE_DIR/${PROJ}.cwd"
# 프로젝트별 telegram bot로 라우팅. recovery script가 큐 비어도 cwd 알 수 있게 매번 갱신.
echo "$CWD" > "$CWD_FILE" 2>/dev/null
PROJECT_TELEGRAM_ENV="${CWD}/.claude/telegram/.env"
if [ ! -f "$PROJECT_TELEGRAM_ENV" ] && [ -n "$PROJ" ]; then
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

# 1.5. bypass flag — 사용자가 임시로 90% 가드 무시하고 싶을 때.
# 단 그냥 exit 0 으로 빠지면 안 된다: 그러면 아래 4번(큐 flush)에 도달 못 해
# 차단 중 쌓였던 큐가 방치되고, 나중에 리밋이 풀려 flush 될 때는 이미 대화가
# 뒤에 이어진 뒤라 순서가 꼬인다. bypass 는 "차단 아님"으로만 처리하고 flush 는
# 반드시 거치게 한다 — 대화를 이어갈 때는 항상 큐를 먼저 소화한다
# (2026-07-23 사용자 요청).
BYPASS_GLOBAL=~/.claude/state/rate-limit-bypass.flag
BYPASS_PROJ=~/.claude/state/rate-limit-bypass-${PROJ}.flag

BYPASS=false
if [ -f "$BYPASS_GLOBAL" ] || [ -f "$BYPASS_PROJ" ]; then
    BYPASS=true
fi

# 2. rate-limits 조회 (bypass 면 조회 없이 차단 아님으로 간주 → 4번 flush 로 흐름)
BLOCKED=false
if [ "$BYPASS" = true ]; then
    # cache 가 없더라도 flush 블록에는 도달해야 하므로 여기서 exit 하지 않는다.
    :
elif [ ! -f "$CACHE" ]; then
    exit 0
else
    # 판정 규칙은 lib 한 벌(claude-quota-observe)에 있다. 여기서 캐시를 다시 파싱하던
    # 사본은 cached_at 을 안 봐서 얼어붙은 수치로 계속 차단했다(2026-08-28 옆 기계 6시간).
    OBSERVE="$(dirname "$(realpath "${BASH_SOURCE[0]}")")/../../bin/claude-quota-observe"
    [ -x "$OBSERVE" ] || OBSERVE="$HOME/.local/bin/claude-quota-observe"
    eval "$("$OBSERVE" --sh 2>/dev/null)"
    [ "${QUOTA_BLOCKED:-}" = "true" ] && BLOCKED=true
fi

# 3. 차단 상태: 큐에 저장하고 prompt 차단
if [ "$BLOCKED" = true ]; then
    # trigger 메시지면 그냥 묵살 (큐 처리는 차단 풀린 다음 분기에서)
    if [ "$IS_TRIGGER" = true ]; then
        echo "rate-limit-guard: trigger 무시 (아직 차단 중)" >&2
        exit 2
    fi

    # 큐에 append (jsonl: id, ts, ts_unix, project, cause, state, retry_count, prompt)
    # flush(consume)와 동시 실행 시 레이스 방지 위해 같은 lock 사용.
    (
        flock -x 200
        python3 -c '
import json, sys, datetime, hashlib, time
now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
ts_iso = now.isoformat(timespec="seconds")
ts_unix = int(time.time())
prompt = sys.argv[1]
proj = sys.argv[2]
cause = sys.argv[3]
# id = ts_unix + sha8(prompt) → 중복 적재 방지
qid = "{}-{}".format(ts_unix, hashlib.sha1(prompt.encode("utf-8", errors="ignore")).hexdigest()[:8])
rec = {"id": qid, "ts": ts_iso, "ts_unix": ts_unix, "project": proj,
       "cause": cause, "state": "pending", "retry_count": 0, "prompt": prompt}
with open(sys.argv[4], "a") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
' "$USER_PROMPT" "$PROJ" "5h_or_7d" "$QUEUE_FILE" 2>/dev/null
    ) 200>"$LOCK_FILE"

    # 큐 길이 계산
    QUEUE_LEN=$(wc -l < "$QUEUE_FILE" 2>/dev/null || echo 0)

    # 텔레그램 알림 — 큐잉될 때마다 매번 (cooldown 제거). 사용자가 어떤 메시지가 어디로 큐잉됐는지 추적 가능하도록 prompt 미리보기 포함.
    ALERT_FLAG="${ALERT_FLAG_PREFIX}-${PROJ}.flag"
    # 프로젝트별 봇으로 직접 알림.
    if [ -f "$TELEGRAM_ENV" ]; then
        . "$TELEGRAM_ENV"
        if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
            preview=$(printf '%s' "$USER_PROMPT" | head -c 200)
            msg="[${PROJ}] rate-limit 차단 중 — 메시지 큐잉됨 (큐 ${QUEUE_LEN}개). 풀리면 자동 처리.

> ${preview}"
            curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
                --data-urlencode "chat_id=${TELEGRAM_CHAT_ID:-}" \
                --data-urlencode "text=${msg}" >/dev/null 2>&1
            touch "$ALERT_FLAG"
        fi
    fi

    echo "rate-limit-guard: 차단 중. 메시지 큐잉됨 (queue=${QUEUE_LEN}). 풀리면 처리됨." >&2
    exit 2
fi

# 4. 차단 해제 상태 + 큐 파일 존재: 큐 prepend + 큐 비우기
# 안전장치:
#   - TTL 6시간: 그보다 오래된 항목은 STALE 태그(자동 실행 금지, 사용자 확인 요청)
#   - batch limit 20: 한 번에 20개만 flush, 초과분은 큐에 잔존
#   - dedup id: 동일 id 중복 제거
FLUSHED=false
FLUSH_DIR=$(mktemp -d)
trap 'rm -rf "$FLUSH_DIR"' EXIT
# 읽기(non-empty 판정)+가공+큐교체(원자적 mv)만 lock 안에서 수행 — append와의 레이스 방지.
# 메시지 조립은 FLUSH_DIR 로컬 파일만 읽으므로 lock 밖에서 해도 안전.
(
    flock -x 200
    if [ -s "$QUEUE_FILE" ]; then
        touch "$FLUSH_DIR/was_nonempty"
        python3 - "$QUEUE_FILE" "$FLUSH_DIR" <<'PYEOF' 2>/dev/null
import json, sys, time
TTL_SEC = 6 * 3600
BATCH = 20
src, out_dir = sys.argv[1], sys.argv[2]
now = int(time.time())
seen_ids = set()
fresh, stale = [], []
with open(src) as f:
    for line in f:
        line = line.rstrip("\n")
        if not line.strip(): continue
        try: d = json.loads(line)
        except: d = {"prompt": line, "ts": "?", "ts_unix": now}
        qid = d.get("id") or "{}-{}".format(d.get("ts_unix", now), hash(d.get("prompt", "")) & 0xffffffff)
        if qid in seen_ids: continue
        seen_ids.add(qid)
        ts_unix = d.get("ts_unix") or now
        try: ts_unix = int(ts_unix)
        except: ts_unix = now
        d["_age_sec"] = now - ts_unix
        (stale if d["_age_sec"] > TTL_SEC else fresh).append(d)
to_flush = fresh[:BATCH]
remainder = fresh[BATCH:]
def fmt(d, i):
    s = d.get("_age_sec", 0)
    return "[큐{} id={} {} (age {}h{:02d}m)] {}".format(
        i, d.get("id", "?"), d.get("ts", "?"), s//3600, (s%3600)//60, d.get("prompt", ""))
open(out_dir+"/flush.txt","w").write("\n\n".join(fmt(d, i+1) for i, d in enumerate(to_flush)))
open(out_dir+"/stale.txt","w").write("\n\n".join(fmt(d, i+1) for i, d in enumerate(stale)))
# remainder만 큐에 다시 적재. stale은 1회 노출 후 제거(무한 재노출 방지).
with open(out_dir+"/keep.jsonl","w") as f:
    for d in remainder:
        d2 = {k:v for k,v in d.items() if not k.startswith("_")}
        f.write(json.dumps(d2, ensure_ascii=False) + "\n")
# 큐에서 사라지는 항목(to_flush + stale)을 durable archive 로 남긴다. flush 는 모델이
# 그 턴을 실제로 처리했는지 확인하기 전에 큐를 비우므로, prepend 된 턴이 하드리밋/
# 크래시로 실패하면 원문이 영구 유실될 수 있다(ai-debate run-20260723T060110Z).
# 자동 재큐잉(lease/dead-letter)까지는 과하다는 판단으로, 최소한 무기록 유실만 막는다:
# 여기 append 해두면 실패해도 사람이/세션이 복구할 수 있다. flush 시각을 함께 찍는다.
with open(out_dir+"/archive.jsonl","w") as f:
    for d in (to_flush + stale):
        d2 = {k:v for k,v in d.items() if not k.startswith("_")}
        d2["flushed_at"] = now
        f.write(json.dumps(d2, ensure_ascii=False) + "\n")
open(out_dir+"/counts.txt","w").write("{} {} {}".format(len(to_flush), len(stale), len(remainder)))
PYEOF
        # 사라지는 항목을 durable archive 에 append (큐 비우기 전, lock 안에서).
        # 무한 성장 방지: 최근 200줄만 유지.
        if [ -s "$FLUSH_DIR/archive.jsonl" ]; then
            cat "$FLUSH_DIR/archive.jsonl" >> "$FLUSHED_ARCHIVE"
            tail -n 200 "$FLUSHED_ARCHIVE" > "$FLUSHED_ARCHIVE.tmp" 2>/dev/null \
                && mv "$FLUSHED_ARCHIVE.tmp" "$FLUSHED_ARCHIVE"
        fi
        # 큐 갱신: remainder만 다시 적재. mv는 동일 파일시스템 내 원자적 rename(cp는 아님).
        if [ -f "$FLUSH_DIR/keep.jsonl" ]; then
            mv "$FLUSH_DIR/keep.jsonl" "$QUEUE_FILE"
        else
            : > "$QUEUE_FILE"
        fi
    fi
) 200>"$LOCK_FILE"

if [ -f "$FLUSH_DIR/was_nonempty" ]; then
    FLUSHED=true
    if [ -f "$FLUSH_DIR/counts.txt" ]; then
        read FLUSH_LEN STALE_LEN REMAINDER_LEN < "$FLUSH_DIR/counts.txt"
    else
        FLUSH_LEN=0; STALE_LEN=0; REMAINDER_LEN=0
    fi
    QUEUE_CONTENT=$(cat "$FLUSH_DIR/flush.txt" 2>/dev/null)
    STALE_CONTENT=$(cat "$FLUSH_DIR/stale.txt" 2>/dev/null)
    QUEUE_LEN=$FLUSH_LEN

    # STALE 항목 안내 블록
    STALE_BLOCK=""
    if [ "$STALE_LEN" -gt 0 ]; then
        STALE_BLOCK="

[STALE — 6시간 이상 경과한 큐 ${STALE_LEN}개. 자동 실행 금지. 이 목록은 1회만 노출되고 큐에서 제거됨. 아직 유효하면 사용자에게 확인받아 다시 요청하라:]

${STALE_CONTENT}"
    fi

    REMAINDER_BLOCK=""
    if [ "$REMAINDER_LEN" -gt 0 ]; then
        REMAINDER_BLOCK="

(추가 ${REMAINDER_LEN}개 큐에 잔존. 다음 flush 라운드에서 처리됨.)"
    fi

    # trigger 메시지면 noise 없이 큐만 처리 — 사용자에게 trigger 자체는 안 보이게
    if [ "$IS_TRIGGER" = true ]; then
        cat <<EOF
[rate-limit 해제 — fresh ${QUEUE_LEN}개 flush, stale ${STALE_LEN}개, 잔여 ${REMAINDER_LEN}개]

다음 메시지들이 차단 중에 큐잉됐었음.
[처리 규칙 — 반드시 지켜라]
- 큐 번호 순서(큐1 → 큐2 → …) 그대로, 하나씩, 빠짐없이 처리한다.
- 순서 재배열 금지. 건너뛰기 금지. "이건 바로 답 가능하니 먼저" 식 우선순위 변경 금지.
- 각 큐 항목은 그 항목 자체에 답하고, 그게 끝난 뒤에만 다음 항목으로 넘어간다.
- 짧은 명령(예: "진행해줘", "텔레그램으로 답해")이 뒤 큐에 있어도, 그 앞 큐 항목을 먼저 완결한 뒤 처리한다.
- 도구가 막혀 작업을 못 하더라도, 진행상황 보고로 대체하지 말고 각 큐 항목의 실제 질문에 순서대로 답한다.

${QUEUE_CONTENT}${STALE_BLOCK}${REMAINDER_BLOCK}

(이 trigger 메시지는 userbot이 깨우기 위해 보낸 것. 실제 사용자 새 입력은 없음.)
EOF
    else
        cat <<EOF
[rate-limit 해제 — fresh ${QUEUE_LEN}개 flush, stale ${STALE_LEN}개, 잔여 ${REMAINDER_LEN}개]

다음 메시지들이 차단 중에 큐잉됐었음.
[처리 규칙 — 반드시 지켜라]
- 큐 번호 순서(큐1 → 큐2 → …) 그대로, 하나씩, 빠짐없이 처리한다.
- 순서 재배열 금지. 건너뛰기 금지. "이건 바로 답 가능하니 먼저" 식 우선순위 변경 금지.
- 각 큐 항목은 그 항목 자체에 답하고, 그게 끝난 뒤에만 다음 항목으로 넘어간다.
- 짧은 명령(예: "진행해줘", "텔레그램으로 답해")이 뒤 큐에 있어도, 그 앞 큐 항목을 먼저 완결한 뒤 처리한다.
- 도구가 막혀 작업을 못 하더라도, 진행상황 보고로 대체하지 말고 각 큐 항목의 실제 질문에 순서대로 답한다.
- 위 큐를 전부 순서대로 처리한 뒤, 마지막에 사용자의 현재 메시지에 답한다.

${QUEUE_CONTENT}${STALE_BLOCK}${REMAINDER_BLOCK}
EOF
    fi

    rm -f "${ALERT_FLAG_PREFIX}-${PROJ}.flag"
fi

# userbot trigger 인데 flush 할 큐가 없었으면 (이미 처리됨/레이스) 모델 노출 없이 조용히 차단.
if [ "$IS_TRIGGER" = true ] && [ "$FLUSHED" != true ]; then
    echo "rate-limit-guard: 빈 큐 trigger 무시" >&2
    exit 2
fi

exit 0
