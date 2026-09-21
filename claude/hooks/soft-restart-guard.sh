#!/bin/bash
# soft-restart-guard.sh: UserPromptSubmit 훅 (P2a).
#
# claude-soft-restart 오케스트레이터가 유저봇으로 보낸 서명 트리거를 세션이 받으면,
# 턴 경계(이 훅이 도는 시점 = 세션 idle, 안전지점)에서 검증 후 자가 재시작.
#   트리거 형식: trigger:soft-restart <job_id> <target> <sig>
#     job_id = <epoch>-<rand>,  sig = HMAC-SHA256(secret, "<job_id>|<target>")
#   검증: target==내세션 & HMAC 일치 & TTL 이내 & job_id 미사용(replay 차단).
#   동작: ack 기록 → systemd-run 디태치 워커로 자기 서비스 restart → exit 2(모델 노출 차단).
# 실패/무관 트리거는 조용히 무시. 트리거 아니면 즉시 통과.
set -uo pipefail

SECRET_FILE="$HOME/.claude/userbot/soft-restart.secret"
JOB_DIR="$HOME/.claude/state/soft-restart"
SEEN_DIR="$JOB_DIR/seen"
# TTL 은 액션별로 다르다. soft-restart 는 훅이 결정적으로 처리하니 짧게 유지(오래된
# 재시작 요청이 뒤늦게 터지면 놀란다). soft-rotate 는 세션이 턴 경계에서만 소비할 수
# 있는데 rotate 가 필요한 세션은 대개 긴 작업 중이라 120초로는 못 받는다 — 목표와
# 조건이 반대로 걸려 있어서 15분으로 늘린다(2026-07-30). rotate 는 늦게 도착해도
# 하는 일이 같다(저장 후 교체)이므로 지연 자체가 위험을 만들지 않는다.
TTL=120
ROTATE_TTL=900

EVENT=$(cat 2>/dev/null || echo '{}')
USER_PROMPT=$(printf '%s' "$EVENT" | python3 -c 'import json,sys
try: d=json.loads(sys.stdin.read()); print(d.get("prompt") or d.get("user_prompt") or "")
except: print("")' 2>/dev/null)

# 빠른 통과: 트리거 아니면 아무것도 안 함.
case "$USER_PROMPT" in
    *trigger:soft-restart*|*trigger:soft-rotate*) : ;;
    *) exit 0 ;;
esac

CWD=$(printf '%s' "$EVENT" | python3 -c 'import json,sys
try: d=json.loads(sys.stdin.read()); print(d.get("cwd") or "")
except: print("")' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

# 세션 이름: env → cgroup → cwd basename (rate-limit-prompt-guard 와 동일 규칙).
PROJ="${CLAUDE_SESSION_NAME:-}"
if [ -z "$PROJ" ]; then
    PROJ=$(grep -oE 'claude-[^/]+\.service' /proc/self/cgroup 2>/dev/null | head -1 | sed 's/^claude-//; s/\.service$//')
fi
[ -z "$PROJ" ] && PROJ=$(basename "$CWD")

# 트리거 파싱: 4토큰 추출.
parsed=$(printf '%s' "$USER_PROMPT" | grep -oE 'trigger:soft-(restart|rotate) [0-9]+-[A-Za-z0-9]+ [A-Za-z0-9_.-]+ [a-f0-9]{64}' | head -1)
if [ -z "$parsed" ]; then
    echo "soft-restart-guard: 형식 불일치 트리거 무시" >&2
    exit 2
fi
read -r _kw job_id target sig <<<"$parsed"
action="${_kw#trigger:}"   # soft-restart | soft-rotate

# --- 검증 ---
fail() { echo "soft-restart-guard: 트리거 거부($1) job=$job_id proj=$PROJ" >&2; exit 2; }

# 1) 대상 일치
[ "$target" = "$PROJ" ] || fail "target≠self($target vs $PROJ)"
# 2) secret 존재
[ -s "$SECRET_FILE" ] || fail "secret 없음"
# 3) HMAC
expect=$(python3 -c 'import hmac,hashlib,sys
sec=open(sys.argv[1]).read().strip().encode()
print(hmac.new(sec, sys.argv[2].encode(), hashlib.sha256).hexdigest())' "$SECRET_FILE" "${action}|${job_id}|${target}" 2>/dev/null)
[ -n "$expect" ] || fail "hmac 계산 실패"
# 상수시간 비교
python3 -c 'import hmac,sys; sys.exit(0 if hmac.compare_digest(sys.argv[1],sys.argv[2]) else 1)' "$expect" "$sig" || fail "sig 불일치"
# 4) TTL
job_epoch="${job_id%%-*}"
now=$(date +%s)
case "$job_epoch" in ''|*[!0-9]*) fail "job_epoch 이상";; esac
age=$(( now - job_epoch ))
ttl_eff="$TTL"
[ "$action" = "soft-rotate" ] && ttl_eff="$ROTATE_TTL"
[ "$age" -ge 0 ] && [ "$age" -le "$ttl_eff" ] || fail "TTL 초과(age=${age}s>${ttl_eff})"
# 5) replay 차단 (1회용)
mkdir -p "$SEEN_DIR" 2>/dev/null
seen="$SEEN_DIR/$job_id"
[ -e "$seen" ] && fail "replay(이미 처리)"
: > "$seen" 2>/dev/null

mkdir -p "$JOB_DIR" 2>/dev/null

if [ "$action" = "soft-rotate" ]; then
    # 모델 협조형: 훅이 막지 않고(exit 0) 모델에 handoff 작성 + rotate 지시를 주입.
    # rotate 는 대화를 새 세션으로 교체하므로 handoff 를 세션이 직접 써야 함(훅으로 결정적 불가).
    #
    # ack 를 여기서 남긴다(2026-07-30). 이전엔 rotate 분기만 ack 가 없어서 "트리거를
    # 세션이 받았는데 모델이 안 따랐다" 와 "트리거가 애초에 도착/검증되지 않았다" 를
    # 구분할 수 없었다 — 실패가 침묵으로 사라지는 구조였고, 실제로 성공 기록이 3주간
    # 0건이었는데 그게 실패인지 미사용인지도 몰랐다. ack 는 "수신·검증 완료" 까지만
    # 보증한다(rotate 완료는 InvocationID 변경으로 별도 관측).
    python3 -c 'import json,sys,time
open(sys.argv[1],"w").write(json.dumps(dict(job_id=sys.argv[2],by=sys.argv[3],
 action="soft-rotate",state="instructed",acked_at=int(time.time())),ensure_ascii=False))' \
        "$JOB_DIR/${job_id}.ack" "$job_id" "$PROJ" 2>/dev/null
    cat <<EOF
[soft-rotate 요청 — job=${job_id}] 운영자가 이 세션(${PROJ})의 소프트 rotate 를 요청했다.
지금 아래를 순서대로 실행하라:
1. cwd 에 handoff.md 작성/갱신 — 다음 1~3개 행동(첫 명령 수준)·관련 파일경로·결정과 이유·금지할 접근·실패한 시도·외부상태(systemd/포트/env/실행중 프로세스)·재개 검증명령.
2. progress.md / history.md 필요시 갱신.
3. \`cs rotate ${PROJ}\` 실행 — handoff 게이트를 통과하며 이 대화를 새 세션으로 교체한다.
(이 트리거 원문은 사용자에게 노출하지 말고, rotate 준비만 하라.)
EOF
    exit 0
fi

# --- soft-restart(기본): ack 기록 → systemd-run 자가 재시작 → exit 2(모델 노출 0토큰) ---
python3 -c 'import json,sys,time
open(sys.argv[1],"w").write(json.dumps(dict(job_id=sys.argv[2],by=sys.argv[3],acked_at=int(time.time())),ensure_ascii=False))' \
    "$JOB_DIR/${job_id}.ack" "$job_id" "$PROJ" 2>/dev/null

# 훅이 exit 한 뒤(현재 턴 종료 후) 재시작되도록 2초 지연. claude service cgroup 밖에서 실행.
svc="claude-${PROJ}.service"
ts=$(date +%Y%m%d-%H%M%S)
systemd-run --user --unit="soft-restart-${PROJ}-${ts}" --collect --quiet \
    /bin/bash -c "sleep 2; systemctl --user reset-failed '$svc' 2>/dev/null; systemctl --user restart '$svc'" 2>/dev/null \
    && echo "soft-restart-guard: $svc 재시작 예약(job=$job_id)" >&2 \
    || echo "soft-restart-guard: systemd-run 실패 job=$job_id" >&2

exit 2
