#!/bin/bash
# git-commit-post: PostToolUse Bash 훅. `git commit` 성공 시:
#   1) cwd 의 progress.md 강제 갱신 (claude-progress-updater 백그라운드 실행, 임계 우회)
#   2) commit message 에 [DECISION] 마커 있으면 ★세션 소유 스테이징 파일
#      ~/.claude/state/history-stubs/<세션>.md 에 스텁 append. ★history.md 에는 안 쓴다 —
#      번호와 본문은 그 세션이 합칠 때 정한다(아래 2) 절의 주석이 그 이유다).
# 실패한 commit / commit 아닌 git 명령 / git 없는 디렉토리 → no-op, 빠르게 exit.

set -uo pipefail

INPUT=$(cat 2>/dev/null || echo '{}')
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
[ "$TOOL" = "Bash" ] || exit 0

CMD=$(echo "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null)
# `git commit` 토큰 단독 매칭 (echo "git commit" 같은 거 무시 위해 단순 substring)
echo "$CMD" | grep -qE '(^|[;&|]| )git +(commit|-c\s+\S+\s+commit)' || exit 0
# --dry-run 은 실제 커밋을 만들지 않으므로 제외 (HEAD 는 이전 커밋 그대로라 오탐 원인)
echo "$CMD" | grep -qE '(^|[[:space:]])--dry-run([[:space:]]|$)' && exit 0

# 성공 판정: stdout 파싱에 의존하지 않는다 — `git commit -q` 는 성공줄
# ("[branch hash] subject")을 출력하지 않아 예전 정규식 게이트가 매번 걸러
# history.md 자동기록이 조용히 누락됐다(2026-07-23 규명). 권위는 stdout 이 아니라
# 저장소 상태여야 한다(ai-debate run-20260723T060110Z): exit_code 로 실패만 걸러내고
# 실제 판정은 아래 HEAD 해시 + history.md dedup 으로 한다.
EXIT=$(echo "$INPUT" | jq -r '.tool_response.exit_code // .tool_response.returncode // 0' 2>/dev/null)
# 알려진 실패(pre-commit hook fail, nothing-to-commit 등)는 exit !=0 → 스킵.
if [ "$EXIT" != "0" ] && [ "$EXIT" != "null" ]; then
    exit 0
fi

CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

# ★생존 카운터. 결과와 무관하게 "불렸다" 를 남긴다 — 이게 없으면 "기록이 없다" 가
#   ★"기록할 일이 없었다" 인지 ★"훅이 안 돌았다" 인지 구분되지 않는다. 2026-07-29 사고의 정체가
#   정확히 그것이었다(워크트리에서 이 훅이 영구 무동작인데 기록할 일이 없던 것과 구분 불가).
#   ★맥락(저장소)별로 남긴다 — 한 맥락의 생존은 다른 맥락을 보증하지 않는다.
ALIVE=$HOME/.claude/logs/git-commit-post-alive.tsv
mkdir -p "$(dirname "$ALIVE")" 2>/dev/null; touch "$ALIVE" 2>/dev/null
awk -v k="$CWD" -v now="$(date '+%F %T')" '
  BEGIN{FS=OFS="\t"; found=0}
  $1==k{ $2=$2+1; $3=now; found=1 }
  {print}
  END{ if(!found) print k, 1, now }
' "$ALIVE" > "$ALIVE.tmp" 2>/dev/null && mv "$ALIVE.tmp" "$ALIVE"

# ★`.git` 은 워크트리에서 ★파일이다(gitdir 포인터) ⟹ 옛 `-d "$CWD/.git"` 검사는 워크트리
#   전체를 조용히 건너뛰었다. camera·lidar·leg 에서 이 훅은 도달조차 못 했다(4경로 실측).
#   ★묻고 싶은 것은 "여기가 저장소 ★루트인가" 이므로 파일 모양이 아니라 ★git 에게 묻는다.
#   ★새 검사를 추가하는 게 아니라 같은 의도를 올바른 도구로 쓴다 — 새 실패모드가 없다.
TOP=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)
if [ "$TOP" != "$CWD" ]; then
    # ★저장소 안이지만 루트가 아니면 건너뛴다(루트 검사 보존). 단 ★조용히 넘기지 않는다 —
    #   기록이 없는 것과 기록할 일이 없던 것을 구분할 수 없게 되기 때문이다.
    [ -n "$TOP" ] && echo "$(date '+%F %T') git-commit-post: 루트 아님 — 생략 cwd=$CWD top=$TOP" \
        >> $HOME/.claude/logs/git-commit-post.log
    exit 0
fi

# 최근 commit info
HASH=$(git -C "$CWD" rev-parse --short HEAD 2>/dev/null)
[ -z "$HASH" ] && exit 0
SUBJ=$(git -C "$CWD" log -1 --format='%s' 2>/dev/null)
BODY=$(git -C "$CWD" log -1 --format='%b' 2>/dev/null)
DATE=$(date '+%Y-%m-%d %H:%M KST')

# 1) progress.md 강제 갱신 (백그라운드, 사용자 응답 지연 방지)
if [ -f "$CWD/progress.md" ] && command -v claude-progress-updater >/dev/null 2>&1; then
    CLAUDE_PROGRESS_FORCE=1 nohup $HOME/.local/bin/claude-progress-updater "$CWD" \
        >> $HOME/.claude/logs/progress-updater.log 2>&1 &
fi

# 2) [DECISION] 마커 → history.md append
if echo "$SUBJ$BODY" | grep -qE '\[DECISION\]|^DECISION:'; then
    # ★기록 대상은 ★저장소가 아니라 ★세션이다. 저장소로 잡으면 A 세션의 커밋이 B 세션의
    #   결정 로그와 ★번호 공간까지 쓴다(2026-07-29 실측 — peer 쪽 스텁 제거 8~9회).
    #   ⟹ 세션 소유 스테이징 파일에만 쌓고, 각 세션이 자기 history.md 로 ★스스로 합친다.
    SESS="${CLAUDE_SESSION_NAME:-}"
    if [ -z "$SESS" ]; then
        SESS=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null | cut -c1-16)
    fi
    # ★fail-closed: 세션을 특정할 수 없으면 ★아무 곳에도 쓰지 않는다. 못 쓰는 것이 남의
    #   파일에 쓰는 것보다 낫다. 대신 흔적을 남겨 침묵을 신호로 바꾼다.
    if [ -z "$SESS" ]; then
        echo "$(date '+%F %T') git-commit-post: 세션 미특정 — 기록 생략 repo=$CWD hash=$HASH" \
            >> $HOME/.claude/logs/git-commit-post.log
        exit 0
    fi
    STAGE_DIR=$HOME/.claude/state/history-stubs
    mkdir -p "$STAGE_DIR" 2>/dev/null
    HIST="$STAGE_DIR/$SESS.md"
    touch "$HIST" 2>/dev/null
    # dedup: 같은 커밋 해시가 이미 기록돼 있으면 재append 금지. stdout 게이트를 뺀
    # 지금은 훅이 커밋 아닌 git 명령에서도 발동할 수 있는데(HEAD 는 직전 [DECISION]
    # 커밋), 해시 dedup 이 있으면 그 경우에도 중복 없이 최대 1회만 기록된다
    # (게다가 그 1회는 원래 누락됐어야 할 항목을 자가치유하는 셈이라 무해).
    if [ -f "$HIST" ] && grep -qF "hash: $HASH" "$HIST" 2>/dev/null; then
        exit 0
    fi
    # 본문에서 마커 다음 줄들 추출 (없으면 subject 만 사용)
    REASON=$(echo "$BODY" | grep -iE '^근거:|^reason:|^why:' | head -1 | sed 's/^[^:]*: *//')
    [ -z "$REASON" ] && REASON="$SUBJ"
    # 번호 자동 (당일 #NN). grep -c 는 0건일 때 "0" 을 출력하고 exit 1 을 내므로
    # `|| echo 0` 을 붙이면 "0\n0" 이 돼 산술오류가 난다(당일 첫 기록에서 항상 발동).
    # grep 출력만 받아 빈 값만 0 으로 보정한다.
    # ★번호를 훅이 매기지 않는다 — 번호는 ★합칠 때 그 세션이 정한다. 자동 번호와 수동 번호가
    #   같은 자원을 다투던 구조가 여기서 사라진다.
    {
        echo ""
        echo "## [$(date '+%Y-%m-%d')] (미배번) $SUBJ"
        echo "tags: commit, 미합침"
        echo "- repo: $CWD"
        echo "- session: $SESS"
        echo "- 결정: $SUBJ"
        echo "- 근거: $REASON"
        echo "- hash: $HASH"
        echo "- updated: $DATE"
    } >> "$HIST"
fi

exit 0
