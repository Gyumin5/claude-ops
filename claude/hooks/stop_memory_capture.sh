#!/bin/bash
# 배포 대상 경로: ~/dotfiles/claude/hooks/stop_memory_capture.sh (+ ~/.claude/hooks/ 심링크)
#
# stop_memory_capture: Stop 훅(턴 종료 시). 조건부로만 동작. 매턴 LLM 요약 절대 금지.
#   트리거(하나라도 참일 때만 동작):
#     (a) 마지막 기록 이후 30분 경과, 또는
#     (b) 마지막 어시스턴트 메시지에 [DECISION] 또는 <<<TOPIC-UPDATE 마커(엄격 경계), 또는
#     (c) progress.md / memory/topics 가 마지막 스탬프 이후 변경됨.
#   동작: update_active_memory.sh 로 active.md 재생성(LLM 없음). updater 성공+active.md 갱신
#         확인 후에만 스탬프 기록(#5). 실패면 스탬프 안 써서 다음 턴 재시도.
#   기본은 stop 을 차단하지 않는다(리마인더 강제 continue 는 STOP_MEMORY_NUDGE=1 일 때만, 권장 안 함).
#
# 신뢰 게이트 (2차 리뷰 High-2 A): SessionStart 와 동일한 allowlist 판정을 Stop 에도 적용.
#   (신뢰 통과) 또는 (해당 프로젝트에 기존 topic-memory 산출물 history/active.md·memory/ 가 이미 존재)
#   일 때만 stale/change/marker 처리. 아니면 임의 CWD 에 active.md 신규 생성 금지 → no-op.
#
# 절대 금지: 자식 claude 세션 spawn, 매턴 LLM 호출, sudo, 블로킹 명령, 큰 파일 통째 read.
# defensive: 어떤 실패도 세션을 죽이지 않는다(항상 exit 0). jq 없으면 no-op+로그(#9). macOS 방어(#12).

set -uo pipefail

LOG="$HOME/.claude/logs/stop-memory.log"
STATE_DIR="$HOME/.claude/state/topic-memory"
mkdir -p "$(dirname "$LOG")" "$STATE_DIR" 2>/dev/null
ts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)

# --- 이식성 헬퍼 (#12) ---
file_mtime() {
    stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || echo 0
}

# path_under BASE TARGET: TARGET 의 realpath 가 BASE 의 realpath 와 같거나 그 하위면 0.
#   update_active_memory.sh 와 동일 규칙(High-2 B 일관). TARGET 이 아직 없으면 존재하는 상위 조상으로 판정.
path_under() {
    local base="$1" target="$2" rp_base rp_t probe
    rp_base=$(realpath "$base" 2>/dev/null) || return 1
    [ -n "$rp_base" ] || return 1
    probe="$target"
    while [ -n "$probe" ] && [ ! -e "$probe" ]; do
        probe=$(dirname "$probe" 2>/dev/null)
        [ "$probe" = "/" ] && break
    done
    rp_t=$(realpath "$probe" 2>/dev/null) || return 1
    [ -n "$rp_t" ] || return 1
    case "$rp_t" in
        "$rp_base"|"$rp_base"/*) return 0 ;;
        *) return 1 ;;
    esac
}

# --- 신뢰 allowlist 게이트 (세 훅 공통 판정 — High-1/High-2, session_start_rehydrate 와 동일) ---
# 반환: 0=신뢰, 1=비신뢰.
is_trusted() {
    local target="$1" line rp
    if [ -n "${TOPIC_MEMORY_TRUSTED:-}" ]; then
        local IFS=':'
        for line in $TOPIC_MEMORY_TRUSTED; do
            [ -n "$line" ] || continue
            rp=$(realpath "$line" 2>/dev/null || echo "$line")
            [ "$target" = "$rp" ] && return 0
        done
        return 1
    fi
    local AL="$HOME/.claude/topic-memory-trusted.txt"
    if [ -f "$AL" ]; then
        # 마지막 줄에 개행 없어도 처리되게 `|| [ -n "$line" ]` (Medium).
        while IFS= read -r line || [ -n "$line" ]; do
            case "$line" in ''|\#*) continue ;; esac
            rp=$(realpath "$line" 2>/dev/null || echo "$line")
            [ "$target" = "$rp" ] && return 0
        done < "$AL"
        return 1
    fi
    if [ "${TOPIC_MEMORY_ALLOW_HOME_DEFAULT:-0}" = "1" ]; then
        case "$target" in
            "$HOME"|"$HOME"/*) return 0 ;;
            *) return 1 ;;
        esac
    fi
    return 1
}

# jq 필수. 없으면 엉뚱한 프로젝트 갱신 위험 → no-op (#9)
if ! command -v jq >/dev/null 2>&1; then
    echo "[$ts] jq 없음 → no-op" >> "$LOG"
    exit 0
fi

INPUT=$(cat 2>/dev/null || echo '{}')

# 무한루프 방지: Stop 훅이 이미 continue 를 유발한 상태면 재작동 안 함.
STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null)
[ "$STOP_ACTIVE" = "true" ] && exit 0

CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
# cwd 파싱 실패 시 PWD 로 엉뚱한 프로젝트 갱신 금지 → no-op+로그 (#9)
if [ -z "$CWD" ] || [ ! -d "$CWD" ]; then
    echo "[$ts] cwd 파싱 실패(cwd='$CWD') → no-op" >> "$LOG"
    exit 0
fi
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)

# --- 프로젝트 memory 디렉토리 해석 (세 훅 공유 로직 #10) ---
# 우선순위: (1) transcript_path 역산(Claude Code 의 실제 프로젝트 디렉토리), (2) cwd 규칙(/→-).
# High 보강: transcript_path 는 realpath 가 $HOME/.claude/projects 하위일 때만 신뢰.
#   /tmp/projects/<key>/... 등 조작 경로면 역산 거부하고 안전 fallback(cwd 규칙, 명시 로그).
resolve_mem_dir() {
    local cwd="$1" tp="$2" key rp rp_tp rest projroot
    projroot=$(realpath "$HOME/.claude/projects" 2>/dev/null || printf '%s' "$HOME/.claude/projects")
    if [ -n "$tp" ]; then
        rp_tp=$(realpath "$tp" 2>/dev/null)
        # 파일이 아직 없을 수 있음 → 존재하는 상위 디렉토리 realpath 로 판정
        [ -z "$rp_tp" ] && rp_tp=$(realpath "$(dirname "$tp" 2>/dev/null)" 2>/dev/null)
        case "$rp_tp" in
            "$projroot"/*)
                # realpath 로 확정된 <projroot>/<key>[/<session>.jsonl] → 첫 세그먼트 = <key>
                rest="${rp_tp#"$projroot"/}"
                key="${rest%%/*}"
                if [ -n "$key" ] && [ "$key" != "." ] && [ "$key" != ".." ]; then
                    printf '%s' "$HOME/.claude/projects/${key}/memory"
                    return 0
                fi
                ;;
            *)
                echo "[$ts] transcript_path realpath 가 \$HOME/.claude/projects 밖(rp='$rp_tp') → 역산 거부, cwd 규칙 fallback" >> "$LOG"
                ;;
        esac
    fi
    rp=$(realpath "$cwd" 2>/dev/null || echo "$cwd")
    key=$(printf '%s' "$rp" | sed -e 's|/|-|g')
    [ -z "$key" ] && return 1
    printf '%s' "$HOME/.claude/projects/${key}/memory"
}
MEMDIR=$(resolve_mem_dir "$CWD" "$TRANSCRIPT")
if [ -z "$MEMDIR" ]; then
    echo "[$ts] project key 계산 실패 → no-op" >> "$LOG"
    exit 0
fi
TOPICS_DIR="$MEMDIR/topics"

# --- 신뢰 게이트 (3차 리뷰 High): 기본은 SessionStart 와 동일 — is_trusted 통과 시에만 동작.
#   기존 산출물(HAS_ARTIFACT) 로 allowlist 를 우회하지 않는다(비신뢰 CWD 우회 방지).
#   기존 산출물 마이그레이션이 필요한 경우에만 별도 명시 옵트인:
#     TOPIC_MEMORY_ALLOW_EXISTING_ARTIFACTS=1 + 산출물이 CWD realpath 하위(history/ 와 active.md 둘 다
#     symlink 로 프로젝트 밖을 가리키지 않음). MEMDIR/TOPICS_DIR 는 ~/.claude/projects 하위(전역)라
#     CWD 소유 신호가 아니므로 우회 근거로 쓰지 않는다. ---
RP=$(realpath "$CWD" 2>/dev/null || echo "$CWD")
if ! is_trusted "$RP"; then
    if [ "${TOPIC_MEMORY_ALLOW_EXISTING_ARTIFACTS:-0}" = "1" ] \
       && [ -f "$CWD/history/active.md" ] \
       && path_under "$CWD" "$CWD/history" \
       && path_under "$CWD" "$CWD/history/active.md"; then
        echo "[$ts] 비신뢰이나 ALLOW_EXISTING_ARTIFACTS 옵트인 + CWD 내부 산출물 확인 → 마이그레이션 허용: $RP" >> "$LOG"
    else
        echo "[$ts] 비신뢰 → no-op(HAS_ARTIFACT 우회 제거; 마이그레이션은 TOPIC_MEMORY_ALLOW_EXISTING_ARTIFACTS=1 + CWD 내부 산출물 필요): $RP" >> "$LOG"
        exit 0
    fi
fi

# --- 스탬프 키: realpath 의 sha256 (동명 프로젝트 충돌 방지 #6) ---
proj_hash() {
    local rp
    rp=$(realpath "$1" 2>/dev/null || echo "$1")
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%s' "$rp" | sha256sum 2>/dev/null | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        printf '%s' "$rp" | shasum -a 256 2>/dev/null | cut -d' ' -f1
    else
        # 해시 도구 없으면 규칙 키로 fallback (동명 충돌은 남지만 no-op 보단 낫다)
        printf '%s' "$rp" | sed -e 's|/|-|g'
    fi
}
STAMP="$STATE_DIR/$(proj_hash "$CWD").stamp"
now=$(date +%s)

# --- 스탬프 값 읽기 + 숫자 검증 (#4) ---
last=0
if [ -f "$STAMP" ]; then
    raw=$(cat "$STAMP" 2>/dev/null || echo 0)
    if printf '%s' "$raw" | grep -qE '^[0-9]+$'; then
        last="$raw"
    else
        echo "[$ts] 스탬프 비숫자('$raw') → 0 리셋" >> "$LOG"
        last=0
    fi
fi

# --- 트리거 판정 ---
trigger=""

# (a) 30분 경과
if [ $(( now - last )) -ge 1800 ]; then
    trigger="stale"
fi

# (b) 마커 감지 — 마지막 어시스턴트 메시지만 엄격 검사 (#8, 부수-3). transcript tail grep 오탐 제거.
#   우선순위(명시): 1차 = 공식 Stop input 의 .last_assistant_message (문서화된 신호).
#                 2차 = 그게 없거나 null 일 때만 transcript tail 을 jq 로 파싱(fallback).
#   .last_assistant_message 는 null 가능 → `// empty` 로 null-safe 처리, "null" 리터럴 방어.
if [ -z "$trigger" ]; then
    last_msg=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null)
    [ "$last_msg" = "null" ] && last_msg=""
    if [ -z "$last_msg" ] && [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
        # 2차 fallback: 1차 신호 부재 시에만 transcript 의 마지막 assistant 텍스트 추출(null-safe).
        #   불완전/비JSON 줄이 섞여도 전체가 실패하지 않게 라인별 파싱: 1단계 `jq -R 'fromjson? // empty'`
        #   로 유효 JSON 줄만 통과(깨진 줄 스킵), 2단계에서 slurp. 빈 배열/null 방어(`last // {}`).
        last_msg=$(tail -n 200 "$TRANSCRIPT" 2>/dev/null \
            | jq -R 'fromjson? // empty' 2>/dev/null \
            | jq -rs 'map(select(.type=="assistant"))
                      | last // {}
                      | (.message.content // [])
                      | map(select(.type=="text") | .text) | join("\n")' 2>/dev/null)
        [ "$last_msg" = "null" ] && last_msg=""
    fi
    # 엄격 sentinel: [DECISION] 리터럴 또는 TOPIC-UPDATE 블록 여는 <<<TOPIC-UPDATE 만.
    if printf '%s' "$last_msg" | grep -qF -e '[DECISION]' -e '<<<TOPIC-UPDATE'; then
        trigger="marker"
    fi
fi

# (c) progress.md / topics 변경 감지
if [ -z "$trigger" ]; then
    PROG="$CWD/progress.md"
    changed=""
    if [ -f "$PROG" ]; then
        pm=$(file_mtime "$PROG")
        [ "$pm" -gt "$last" ] 2>/dev/null && changed="progress"
    fi
    if [ -z "$changed" ] && [ -d "$TOPICS_DIR" ]; then
        newest_mt=0
        while IFS= read -r -d '' f; do
            mt=$(file_mtime "$f")
            [ "$mt" -gt "$newest_mt" ] 2>/dev/null && newest_mt="$mt"
        done < <(find "$TOPICS_DIR" -maxdepth 1 -type f -name '*.md' ! -name '_SCHEMA.md' -print0 2>/dev/null)
        [ "$newest_mt" -gt "$last" ] 2>/dev/null && changed="topic"
    fi
    [ -n "$changed" ] && trigger="change:$changed"
fi

# 트리거 없으면 조용히 통과 (과잉기록 방지)
[ -z "$trigger" ] && exit 0

# --- 동작: active.md 재생성 (LLM 없음). 성공+active.md 갱신 확인 후에만 스탬프 기록 (#5) ---
#   주의: update_active_memory 는 방어적으로 항상 exit 0 이라 종료코드는 성공 신호가 아니다.
#   독립 신호 = "active.md 가 방금(이 호출 시각 이후) 실제로 쓰였는가". start 이후 mtime 이면 OK.
#   (동일 초 재생성도 mtime>=start 로 잡히므로 1초 해상도 문제 없음.)
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
UPDATER="$HOOK_DIR/update_active_memory.sh"
ACTIVE="$CWD/history/active.md"

start=$(date +%s)
urc=0
if [ -x "$UPDATER" ]; then
    # 프로젝트 키 일관성 위해 해석한 MEMDIR 를 넘긴다 (#10).
    if command -v timeout >/dev/null 2>&1; then
        TOPIC_MEMORY_DIR="$MEMDIR" timeout 30 "$UPDATER" "$CWD" >> "$LOG" 2>&1 || urc=$?
    else
        TOPIC_MEMORY_DIR="$MEMDIR" "$UPDATER" "$CWD" >> "$LOG" 2>&1 || urc=$?
    fi
else
    echo "[$ts] updater 실행불가($UPDATER) → 스탬프 미기록" >> "$LOG"
fi

# rc 3 = updater 가 "일부러 안 썼다"(내용 동일·실을 것 없음·git 추적 중). 파일이 이미
#   최신이거나 애초에 만들면 안 되는 경우라 성공으로 친다. 아래 mtime 판정만 두면
#   mtime 이 안 움직여서 매 턴 실패로 보고 영원히 재시도한다 — 실제로 그렇게 나왔다.
after_mt=$(file_mtime "$ACTIVE")
# active.md 가 존재 + 비어있지 않음 + 이번 호출 시각 이후에 쓰였을 때만 스탬프 기록 (#5)
if [ "$urc" = "3" ]; then
    stamp_now=$(date +%s)
    echo "$stamp_now" > "$STAMP" 2>/dev/null
    echo "[$ts] trigger=$trigger cwd=$CWD → updater 의도적 미작성(rc=3, 사유는 위 REFUSE/SKIP/NOCHANGE 줄), 스탬프 갱신($stamp_now)" >> "$LOG"
elif [ -s "$ACTIVE" ] && [ "$after_mt" -ge "$start" ] 2>/dev/null; then
    # 부수-2: 스탬프는 훅 시작시각(now)이 아니라 갱신 성공 확인 직후의 시각으로 기록.
    #   시작시각을 쓰면 updater 소요시간 동안의 변경을 다음 경계 비교에서 놓칠 수 있음.
    stamp_now=$(date +%s)
    echo "$stamp_now" > "$STAMP" 2>/dev/null
    echo "[$ts] trigger=$trigger cwd=$CWD → active.md 재생성 OK(mt=$after_mt), 스탬프 갱신($stamp_now)" >> "$LOG"
else
    echo "[$ts] trigger=$trigger cwd=$CWD → active.md 미갱신(mt=$after_mt start=$start) → 스탬프 미기록(다음 턴 재시도)" >> "$LOG"
fi

# 선택: 리마인더 nudge (기본 OFF — 매턴 continue 강제 위험). 켜려면 STOP_MEMORY_NUDGE=1.
#   stop_hook_active 가드는 위에서 처리. block 은 1회만 유도되도록 marker 트리거로 제한.
if [ "${STOP_MEMORY_NUDGE:-0}" = "1" ] && [ "$trigger" = "marker" ]; then
    python3 <<'PYEOF' 2>/dev/null || exit 0
import json
print(json.dumps({
    "decision": "block",
    "reason": "[기억 리마인더] 방금 결정/토픽 갱신 신호가 감지됐다. 종료 전 관련 memory/topics/<id>.md 와 progress.md 를 규약대로 갱신했는지 1회 확인하라(중복 금지). 확인/반영 후 종료."
}, ensure_ascii=False))
PYEOF
    exit 0
fi

exit 0
