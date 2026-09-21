#!/bin/bash
# session-resume-notify: SessionStart 훅 (자동재개 Phase 1 = 반자동 알림).
#
# 재시작 시 cwd 의 progress.md 에 미완 작업(status=active)이 있으면 텔레그램으로 1회 알림.
# 순수 알림 — 작업을 자동으로 착수하지 않는다(완전자동은 Phase 2, 별도). 오발동 위험 없음.
#
# ai-debate run-20260703T015042Z 검토 반영: 무인 환경선 오발동 손실 > 재개누락 손실 →
# 기본은 반자동(알림+대기), LLM 자동판정 금지. 완전자동은 기계판독 필드/lease/백오프 갖춘
# Phase 2 에서만.
#
# 안전장치:
#  - kill-switch: ~/.claude/state/auto-resume.disabled 존재 시 무동작.
#  - 쿨다운(세션 cwd 별 10분): 재시작 폭주 스팸/루프 차단.
#  - status != active / progress.md 없음 / task 빈 값이면 무알림(스팸 방지).
#  - 항상 exit 0 (세션 시작을 절대 방해하지 않음, defensive).

set -uo pipefail

# 헤드리스 자동화(claude-progress-updater 등)가 CLAUDE_AUTOMATED=1 로 자식 claude -p 를
# 띄우는 기존 관례 — 다른 6개 훅(telegram-reply-injector 등)은 전부 이 체크가 있는데
# 이 훅만 빠져있어서, 그 헤드리스 자식의 SessionStart 를 진짜 재시작으로 오인해
# "재시작 감지" 알림을 오발동했음(2026-07-05).
[ "${CLAUDE_AUTOMATED:-0}" = "1" ] && exit 0

INPUT=$(cat 2>/dev/null || echo '{}')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

# 홈 루트($HOME)는 프로젝트가 아님 — 여기서 발동 금지. control-bot·유틸 서비스들이
# WorkingDirectory=$HOME 라 SessionStart 훅이 홈 루트의 stray progress.md 를 읽고
# "USER" 같은 잘못된 알림을 내던 버그 차단. progress.md 는 프로젝트 WD 에만 있어야 함.
[ "$CWD" = "$HOME" ] && exit 0

# kill-switch (전역 즉시 비활성화)
[ -f "$HOME/.claude/state/auto-resume.disabled" ] && exit 0

# 2026-07-08 사용자 신고: systemd 유닛 없이 터미널에서 직접 띄운 임시 claude
# 세션(인바운드 경로 없음, 예: peer의 session_c_work cwd)이 재시작
# 감지 알림을 운영 챗으로 쏴서 스팸이 됨 — ai-debate run-20260708T071716Z
# 결론(안1): CLAUDE_SESSION_NAME env도 cgroup claude-*.service 매치도 없으면
# 유닛 밖 임시 세션으로 보고 조용히 skip. rate-limit-guard.sh와 동일 판정 재활용.
UNIT_MATCH=$(grep -oE 'claude-[^/]+\.service' /proc/self/cgroup 2>/dev/null | head -1)
if [ -z "${CLAUDE_SESSION_NAME:-}" ] && [ -z "$UNIT_MATCH" ]; then
    exit 0
fi

# SessionStart 는 startup(진짜 신규 프로세스) 외에 resume/clear/compact 에서도 발동한다.
# 실측(2026-07-03): 이 세션들은 claude-service-exec 가 항상 `claude -c`(resume) 로 띄우므로
# 진짜 프로세스 재시작(하드/소프트 재시작 전부)도 source="resume" 으로 온다 — "startup 전용"
# 게이트였을 때는 모든 재시작에서 조용히 스킵돼 알림이 한 번도 안 나갔음(버그, #073).
# /clear·자동 compact 는 프로세스 재시작 없이 같은 프로세스 안에서 발동하므로 source 가
# "clear"/"compact" 로 구분됨 — 이 둘만 걸러내면 스팸 방지 목적은 그대로 유지됨.
# source 필드가 없는 구버전은 게이트 통과(하위호환).
SOURCE=$(echo "$INPUT" | jq -r '.source // empty' 2>/dev/null)
case "$SOURCE" in clear|compact) exit 0 ;; esac

PROG="$CWD/progress.md"

# ai-debate run-20260708T071716Z 2단계(경고형): 세션 루트에 progress.md가
# 없는데 하위 디렉토리(서브레포 등)에 잘못 놓인 후보가 있으면 경고만 하고
# 넘어간다(자동 이동·삭제 안 함) — peer 세션이 sub-repo/
# 밑에 써서 재시작 때마다 stale 컨텍스트를 물려받은 사고 재발 방지용 진단.
if [ ! -f "$PROG" ]; then
    STRAY=$(find "$CWD" -mindepth 2 -maxdepth 4 -name progress.md -not -path '*/.git/*' 2>/dev/null | head -3)
    if [ -n "$STRAY" ]; then
        WENV="$CWD/.claude/telegram/.env"
        [ -f "$WENV" ] || WENV="$HOME/dotfiles/.claude/telegram/.env"
        if [ -f "$WENV" ]; then
            (
                # shellcheck disable=SC1090
                . "$WENV"
                [ -n "${TELEGRAM_BOT_TOKEN:-}" ] || exit 0
                WMSG="세션 루트($CWD)에 progress.md가 없는데 하위 경로에서 발견됨(경로 오배치 의심, 자동 이동 안 함):
$(printf '%s' "$STRAY" | sed 's/^/· /')"
                curl -s --max-time 10 -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
                    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID:-}" \
                    --data-urlencode "text=${WMSG}" >/dev/null 2>&1
            )
        fi
    fi
    exit 0
fi

# progress.md 상단 status/task 파싱 (상단 30줄 이내만 스캔)
STATUS=$(head -30 "$PROG" | grep -m1 '^status:' | sed 's/^status:[[:space:]]*//' | tr -d '\r')
TASK=$(head -30 "$PROG" | grep -m1 '^task:' | sed 's/^task:[[:space:]]*//' | tr -d '\r')
[ "$STATUS" = "active" ] || exit 0
[ -n "$TASK" ] || exit 0

# 쿨다운/dedupe (세션 cwd 별)
COOLDOWN=600  # 10분
STATE_DIR="$HOME/.claude/state/resume-notify"
mkdir -p "$STATE_DIR" 2>/dev/null
KEY=$(printf '%s' "$CWD" | sed -e 's|/|-|g' -e 's|_|-|g')
FLAG="$STATE_DIR/${KEY}.flag"
now=$(date +%s)
if [ -f "$FLAG" ]; then
    last=$(cat "$FLAG" 2>/dev/null || echo 0)
    [ "$((now - last))" -lt "$COOLDOWN" ] 2>/dev/null && exit 0
fi

# telegram env 해석 (rate-limit-recovery 규약: cwd 우선 → fallback)
ENV_FILE="$CWD/.claude/telegram/.env"
[ -f "$ENV_FILE" ] || ENV_FILE="$HOME/dotfiles/.claude/telegram/.env"
[ -f "$ENV_FILE" ] || exit 0

CHAT_ID="${TELEGRAM_CHAT_ID:-}"
PROJ=$(basename "$CWD")

# progress.md ## Blockers 섹션에서 미완료 항목 전부 추출(각 줄 120자 컷).
# "(대기)" 문자열 리터럴 매칭이었던 예전 버전은 버그였음(2026-07-03) — CLAUDE.md 의
# "(대기)"는 예시 표기일 뿐인데 정규식이 그걸 필수 태그처럼 취급해, "(대기)" 없이
# ✅/진행/할일 등 다른 마커를 쓰는 (이 표기 이전에 작성된) progress.md 에서는 Blockers
# 에 실제 항목이 있어도 [대기] 섹션이 항상 비어보였다(peer 에서 재현). 완료 표시
# (줄 앞머리 ✅)가 없는 모든 Blockers 줄을 태스크로 취급하도록 수정. "완료" 문자열
# 자체로 거르면 "텍스처 우회 완료 → 다음 단계 필요" 같은 하위절 언급까지 오탈락하므로
# 반드시 줄 앞머리 ✅ 만으로 판정.
# 개수 제한 없음 — 사용자가 재시작 시 전체 목록을 보고 진행/삭제 triage 하도록.
# (텔레그램 4096자 한도는 API 가 자체 처리; 항목은 통상 소수라 문제 없음.)
# 2026-07-03 태스크 목록 규칙으로 Blockers 각 줄 맨앞에 "N. " 순번이 붙게 됐는데
# 이 awk가 대시 불릿("- ")만 매칭해 번호형 줄은 전부 0건으로 누락됐다(peer 세션이
# 실측 발견·보고, 2026-07-08). 대시/번호 둘 다 최상위 항목으로 인정하도록 수정 —
# 들여쓴 하위불릿은 앞머리가 공백이라 여전히 자연히 제외됨.
PENDING=$(awk '/^## Blockers/{f=1;next} /^## /{f=0} f && (/^- /||/^[0-9]+\. /){print}' "$PROG" \
    | grep -Ev '^(- |[0-9]+\. )✅' \
    | sed -E -e 's/^- *//' -e 's/^[0-9]+\. *//' | cut -c1-240)

# task: 한 줄이 " + " 로 여러 항목을 이어붙인 경우 항목별 줄바꿈(가독성).
TASK_LINES=$(printf '%s' "$TASK" | sed 's/ + /\n· /g')
case "$TASK_LINES" in *$'\n'*) TASK_LINES="· ${TASK_LINES}" ;; esac

# ## Current State 섹션 상세 bullet(최대 5개, 각 240자 컷) — [현재] 한 줄보다 자세한 맥락.
DETAIL=$(awk '/^## Current State/{f=1;next} /^## /{f=0} f && /^- /{print}' "$PROG" \
    | sed -e 's/^- *//' | cut -c1-240 | head -5)

MSG="재시작 감지 — 쌓여있는 작업이 있어요.
[현재]
${TASK_LINES}"
if [ -n "$DETAIL" ]; then
    MSG="${MSG}
[상세]
$(printf '%s' "$DETAIL" | sed 's/^/· /')"
fi
if [ -n "$PENDING" ]; then
    MSG="${MSG}
[대기]
$(printf '%s' "$PENDING" | sed 's/^/· /')"
fi

# 봇 토큰은 이 머신에 머물고 알림만 발송. 실패해도 조용히 통과.
(
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    [ -n "${TELEGRAM_BOT_TOKEN:-}" ] || exit 0
    curl -s --max-time 10 -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${CHAT_ID}" \
        --data-urlencode "text=${MSG}" >/dev/null 2>&1
)

echo "$now" > "$FLAG" 2>/dev/null
exit 0
