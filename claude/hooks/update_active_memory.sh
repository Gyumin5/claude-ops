#!/bin/bash
# 배포 대상 경로: ~/dotfiles/claude/hooks/update_active_memory.sh (+ ~/.claude/hooks/ 심링크)
#
# update_active_memory: history/active.md (SessionStart 주입용 compact view, 30~80줄) 재생성.
#   소스 = (1) 활성 토픽 파일들의 frontmatter+summary, (2) progress.md 상단 상태,
#          (3) history.md 최근 결정 몇 개.
#   LLM 호출 없음. 순수 파일 병합. progress.md 는 절대 쓰지 않는다(guard/updater 소유).
#
# 사용: update_active_memory.sh [cwd]   (인자 없으면 $PWD)
#   Stop 훅이 동기 호출하거나 수동 실행. (PreCompact 는 더 이상 호출하지 않는다 — 리뷰 #2)
#   호출자가 TOPIC_MEMORY_DIR 환경변수로 memory 디렉토리를 넘기면 그걸 신뢰(프로젝트 키 일관성 #10).
#
# defensive: 실패해도 exit 0. active.md 를 같은 FS 임시파일 후 원자적 mv(#7).
#   flock 로 Stop/수동 동시실행 직렬화(#7). 큰 파일 통째 read 안 함. macOS 이식성 방어(#12).
#
# 외부쓰기 차단 (2차 리뷰 High-2 B): history 디렉토리/active.md 가 symlink 로 프로젝트 밖을
#   가리키면 쓰기 거부. 대상 realpath 가 CWD realpath 하위인지 확인. 단독 실행 시에도 방어.

set -uo pipefail

CWD="${1:-$PWD}"
[ -d "$CWD" ] || CWD="$PWD"

ULOG="$HOME/.claude/logs/stop-memory.log"
mkdir -p "$(dirname "$ULOG")" 2>/dev/null
uts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)

# --- 이식성 헬퍼 (#12) ---
# 파일 mtime(epoch). GNU: stat -c %Y, BSD/macOS: stat -f %m.
file_mtime() {
    stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || echo 0
}

# path_under BASE TARGET: TARGET 의 realpath 가 BASE 의 realpath 와 같거나 그 하위면 0.
#   TARGET 이 없으면(아직 생성 전) 그 존재하는 상위 조상으로 판정(심링크 조상 방어).
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

# --- 프로젝트 memory 디렉토리 해석 (세 훅 공유 로직 #10) ---
# 우선순위: (1) 호출자가 넘긴 TOPIC_MEMORY_DIR, (2) cwd 의 Claude Code 디렉토리 규칙(/→-).
#   transcript 역산은 호출자(Stop 훅)에서 하고 TOPIC_MEMORY_DIR 로 전달한다.
resolve_mem_dir() {
    local cwd="$1" key rp
    if [ -n "${TOPIC_MEMORY_DIR:-}" ]; then
        printf '%s' "$TOPIC_MEMORY_DIR"
        return 0
    fi
    rp=$(realpath "$cwd" 2>/dev/null || echo "$cwd")
    # Claude Code 프로젝트 디렉토리 규칙: 절대경로의 '/' 를 '-' 로. (확인된 예: $HOME/dotfiles → -home-USER-dotfiles)
    key=$(printf '%s' "$rp" | sed -e 's|/|-|g')
    [ -z "$key" ] && return 1
    printf '%s' "$HOME/.claude/projects/${key}/memory"
}

MEMDIR=$(resolve_mem_dir "$CWD") || exit 0
TOPICS_DIR="$MEMDIR/topics"

PROG="$CWD/progress.md"
HIST="$CWD/history.md"
ADIR="$CWD/history"
ACTIVE="$ADIR/active.md"

# git 이 추적 중인 파일은 안 건드린다. 이 스크립트는 매 턴 도는데, 추적되는 파일을
# 매 턴 고치면 그 저장소는 늘 dirty 상태가 되고 checkout·merge 가 막힌다 —
# session-f 가 worktree 에서 실제로 겪었다. 2026-08-20 실측으로 17개 중 4개가
# 추적 중이다(peer, proj-b, proj-b-wt/camera, proj-b-wt/leg).
# 추적을 풀지 말지는 그 저장소 판단이라 여기서 대신 정하지 않는다. 대신 조용히
# 지나가지 않고 로그에 남긴다 — 안 그러면 "켰는데 왜 안 되지"가 된다.
# 락·임시파일·사본보다 먼저 본다. 뒤에 두면 생성은 거부하면서 .active.md.lock 과
# 수기본 사본만 남겨 그 저장소를 더럽힌다(그렇게 짰다가 실기 검증에서 잡았다).
if (cd "$CWD" 2>/dev/null && git ls-files --error-unmatch history/active.md >/dev/null 2>&1); then
    echo "[$uts] REFUSE: history/active.md 가 git 추적 중 → 생성 건너뜀(매 턴 dirty·merge 차단 방지). 자동 생성을 원하면 그 저장소에서 git rm --cached history/active.md 후 .gitignore 등록. CWD=$CWD" >> "$ULOG"
    exit 3
fi

# --- 외부쓰기 차단 (High-2 B): history 디렉토리·active.md 가 프로젝트 밖을 가리키면 거부 ---
if ! path_under "$CWD" "$ADIR"; then
    echo "[$uts] REFUSE: history 디렉토리가 프로젝트 밖을 가리킴(symlink?) CWD=$CWD ADIR=$ADIR → 쓰기 거부" >> "$ULOG"
    exit 0
fi

mkdir -p "$ADIR" 2>/dev/null || exit 0

# ADIR 생성/해석 후 active.md 경로도 재확인 (active.md 자체가 외부로 향하는 symlink 인 경우 방어)
if ! path_under "$CWD" "$ACTIVE"; then
    echo "[$uts] REFUSE: active.md 가 프로젝트 밖을 가리킴(symlink?) CWD=$CWD ACTIVE=$ACTIVE → 쓰기 거부" >> "$ULOG"
    exit 0
fi
ts=$(date '+%Y-%m-%d %H:%M KST' 2>/dev/null)

# --- flock 직렬화 (#7). flock 없으면(macOS 기본) 락 없이 진행(방어적). ---
# 락은 저장소 밖에 둔다. 프로젝트 안(history/.active.md.lock)에 만들면 추적을 푼
# 저장소마다 untracked 로 남아 git status 를 영구히 더럽힌다 — proj-b 가 추적을 풀자마자
# 그렇게 나왔다. 락은 실행 시 부산물이지 프로젝트 파일이 아니다. 같은 FS 여야 하는 건
# 원자적 mv 대상인 TMP 뿐이고 락은 아무 데나 있어도 된다.
LOCKDIR="$HOME/.claude/state/active-md-locks"
mkdir -p "$LOCKDIR" 2>/dev/null
LOCK="$LOCKDIR/$(printf '%s' "$CWD" | sed 's|/|-|g').lock"
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK" 2>/dev/null
    flock -w 5 9 2>/dev/null || true   # 5s 안에 못 잡아도 진행(원자 mv 로 최악의 경우도 손상 없음)
fi

# 같은 디렉토리(=같은 FS)에 임시파일 → mv 원자성 보장 (#7)
TMP=$(mktemp "$ADIR/.active.md.tmp.XXXXXX" 2>/dev/null) || exit 0
trap 'rm -f "$TMP" 2>/dev/null' EXIT
MAXLINES=120
# 주입 예산. session-start-context.sh 의 ACTIVE_CAP_BYTES 와 같은 값이어야 한다 —
# 거기서 앞 6000바이트만 실으므로, 이걸 넘겨 만들면 넘긴 만큼은 어느 세션에도 안 보인다.
# 줄 수만으로는 이걸 못 지킨다. 한글은 문자당 3바이트라 120줄이 쉽게 8000바이트를 넘고,
# 실제로 dotfiles active.md 가 43줄·8127바이트로 규약을 지킨 채 26%가 잘려 있었다.
MAXBYTES=6000

# history.md 에서 "최근 N건" 을 뽑을 때 쓸 명령(head 또는 tail)을 정렬 방향으로 정한다.
#   규약은 append(오래된 것이 위)지만 실제로 prepend(최신이 위)로 쓰는 프로젝트가 있다
#   — 2026-08-20 실측: session-j 는 첫 헤더 2026-08-20 / 끝 헤더 2026-04-21 로 역순이고,
#   나머지 여덟은 append 였다. 방향을 가정하고 tail 을 쓰면 그 프로젝트에서는
#   가장 오래된 것을 집는다. 최신 기각이 조용히 빠지는 쪽이라 침묵 실패다.
#   판정 불가(헤더 0~1개, 날짜 없음)면 규약 기본인 append 로 본다.
_recent_cmd() {
    local f="$1" first last
    first=$(grep -oE '^## \[[0-9]{4}-[0-9]{2}-[0-9]{2}\]' "$f" 2>/dev/null | head -1)
    last=$(grep -oE '^## \[[0-9]{4}-[0-9]{2}-[0-9]{2}\]' "$f" 2>/dev/null | tail -1)
    if [ -n "$first" ] && [ -n "$last" ] && [ "$first" \> "$last" ]; then
        echo head
    else
        echo tail
    fi
}
RECENT=tail
[ -f "$HIST" ] && RECENT=$(_recent_cmd "$HIST")

# 수기 관리본을 말없이 덮지 않는다. 이 스크립트는 기존 active.md 를 읽지 않고 통째로
# 갈아치우는데, 지금 신뢰 allowlist 에는 dotfiles 한 줄뿐이라 나머지 트리의 active.md 는
# 전부 사람이(세션이) 손으로 써온 것이다 — 2026-08-20 실측으로 proj-b 363줄,
# session_c_work 592줄. 그 트리를 allowlist 에 넣는 순간 그게 25줄 자동요약으로
# 바뀌고, history/ 를 gitignore 한 저장소에서는 되돌릴 방법이 없다.
# 그래서 자동 헤더가 없는 파일은 첫 실행에서 사본을 남기고, 생성본 머리에 그 경로를 적는다.
MANUAL_BAK=""
if [ -f "$ACTIVE" ] && ! grep -q 'auto: update_active_memory.sh' "$ACTIVE" 2>/dev/null; then
    _cand="$ACTIVE.manual-$(date +%Y%m%d-%H%M%S)"
    if cp -p "$ACTIVE" "$_cand" 2>/dev/null; then
        MANUAL_BAK="$_cand"
    else
        # 사본을 못 남겼으면 덮지 않는다. 여기서 진행하면 사람이 써온 요약을 지워놓고
        # "사본 실패" 한 줄만 남기는 꼴이고, 그건 이 가드가 막으려던 바로 그 손실이다.
        echo "[$uts] REFUSE: 수기본 사본 실패 → 덮어쓰지 않고 중단 ACTIVE=$ACTIVE" >> "$ULOG"
        exit 0
    fi
fi

{
    echo "# history/active.md"
    echo "updated: $ts  (auto: update_active_memory.sh — 수동 편집 시 다음 실행에 덮어써짐)"
    if [ -n "$MANUAL_BAK" ]; then
        echo ""
        echo "> 이 파일은 이번 턴부터 자동 생성으로 바뀌었다. 직전까지의 수기 관리본은 $(basename "$MANUAL_BAK") 에 있다."
        echo "> 거기서 아직 유효한 결정은 history.md 로 옮겨라 — 이 파일에 적으면 다음 턴에 지워진다. 다 옮겼으면 사본을 지운다."
    fi
    echo ""

    # 0-a) 고정 서문. history/active-preamble.md 가 있으면 그대로 맨 앞에 붙인다.
    #    이 파일은 생성기가 안 건드린다 — 프로젝트가 손으로 관리하는 상시 제약용이다.
    #    필요한 이유: 생성기는 history.md 에서 뽑아 쓰는 것만 실을 수 있는데, 프로젝트마다
    #    "매 세션 맨 위에 반드시 보여야 하는" 안전 제약이 따로 있다(2026-08-20 peer:
    #    sudo 금지·공용 계정·실행 중 so 제자리수정 금지 등). 그게 재생산이 안 돼서 peer 은
    #    자동생성을 못 켠다고 했고, 그 판단이 맞다 — 안전 제약이 사라지는 건 요약이 얇아지는
    #    것과 다른 종류의 손실이다. 기각 목록보다도 앞에 둔다.
    #    상한을 두는 이유는 서문이 예산을 다 먹으면 기각 목록이 밀려나기 때문이다.
    PREAMBLE="$ADIR/active-preamble.md"
    if [ -f "$PREAMBLE" ]; then
        _plines=$(wc -l < "$PREAMBLE" 2>/dev/null); _plines=${_plines:-0}
        head -n 40 "$PREAMBLE"
        if [ "${_plines:-0}" -gt 40 ] 2>/dev/null; then
            echo "- ... (서문이 ${_plines}줄이라 앞 40줄만 실었다. 상시 제약은 40줄 안에 넣어라 — history/active-preamble.md)"
        fi
        echo ""
    fi

    # 0) 이미 기각된 실험. 이 절이 이 파일에서 제일 중요하다 — 같은 실험을 다시
    #    도는 걸 막는 유일한 줄이고, 그래서 절단(아래 MAXLINES)에 먹히지 않게 맨 앞에 둔다.
    #    history.md 의 `실험/실패` 필드는 규약상 "다시 시도하면 시간 낭비하는 실패" 전용이라
    #    따로 거를 필요가 없다 — 문구로 거르면 표현이 다른 항목이 조용히 빠진다.
    #    줄 자체는 자르지 않는다. 바이트로 자르면 한글이 반토막 나고, 그게 오늘
    #    SessionStart 주입을 통째로 날려먹던 원인이었다(03c0042). 줄 수로만 줄인다.
    #    실측 배경(2026-08-20): 여섯 프로젝트에 이 기재가 171건 있는데 이 파일에는
    #    한 건도 안 실리고 있었다. 기록은 있었고 조회가 끊겨 있었다.
    #    출처는 루트 history.md 하나가 아니다. 결정 로그를 history/<이름>.md 로 쪼개는
    #    프로젝트가 있다 — proj-b 는 센서별로 나눠 history/lidar.md 에 결정 477건·기각
    #    103건이 있는데, 루트만 읽던 동안 그게 통째로 시야 밖이었다(2026-08-20
    #    session-f 보고, 실측 확인). 형식을 아무리 지켜도 안 실리는 종류의 사각지대다.
    #    월별 archive(history/YYYY-MM*.md)는 뺀다 — 규약상 lazy load 대상이고, proj-b 만
    #    해도 137건이라 최근 기각을 밀어낸다. 뺐다는 사실은 아래 꼬리줄에 적는다.
    #    조용히 빼면 그게 다시 "있는 줄 알았는데 없던" 항목이 된다.
    neg_sources=""
    [ -f "$HIST" ] && neg_sources="$HIST"
    if [ -d "$ADIR" ]; then
        for _f in "$ADIR"/*.md; do
            [ -f "$_f" ] || continue
            case "$(basename "$_f")" in
                active.md) continue ;;
                active-preamble.md) continue ;;   # 결정 로그가 아니라 상시 제약 서문이다
                [0-9][0-9][0-9][0-9]-[0-9][0-9]*.md) continue ;;
            esac
            neg_sources="$neg_sources
$_f"
        done
    fi

    negtotal=0
    negbody=""
    nsrc=0
    while IFS= read -r _f; do
        [ -n "$_f" ] && [ -f "$_f" ] || continue
        # grep -c 는 0건일 때도 "0" 을 출력하면서 exit 1 이다. `|| echo 0` 을 붙이면
        # 값이 "0\n0" 이 되고, 그걸 $(( )) 에 넣는 순간 산술 오류로 이 블록 전체가
        # 죽는다(2026-08-20 테스트에서 재현 — active.md 가 머리말만 남았다).
        _n=$(grep -cE '^- *실험/실패' "$_f" 2>/dev/null)
        _n=${_n:-0}
        [ "${_n:-0}" -eq 0 ] 2>/dev/null && continue
        negtotal=$((negtotal + _n))
        nsrc=$((nsrc + 1))
        _pick=$(_recent_cmd "$_f")
        _lines=$(grep -E '^- *실험/실패' "$_f" 2>/dev/null | "$_pick" -10)
        negbody="${negbody}### $(basename "$_f") (${_n}건 중 최근 $(printf '%s\n' "$_lines" | wc -l)건)
${_lines}
"
    done <<EOF
$neg_sources
EOF

    if [ "$negtotal" -gt 0 ] 2>/dev/null; then
        echo "## 이미 기각됨 — 다시 하면 시간낭비 (총 ${negtotal}건, 출처 ${nsrc}개)"
        printf '%s' "$negbody"
        echo "(월별 archive 는 제외했다. 전체는 history.md 와 history/*.md 에서 '실험/실패' grep. 실험 착수 전에 반드시 본다.)"
        echo ""
    fi

    # 1) 활성 토픽 요약 (status: active 인 토픽 우선, 최근 갱신순)
    if [ -d "$TOPICS_DIR" ]; then
        echo "## 활성 토픽"
        # find -print0 로 공백/특수문자 파일명 안전 처리 (#11), mtime 내림차순 정렬(#12 이식성 헬퍼).
        sorted=$(
            while IFS= read -r -d '' f; do
                printf '%s\t%s\n' "$(file_mtime "$f")" "$f"
            done < <(find "$TOPICS_DIR" -maxdepth 1 -type f -name '*.md' ! -name '_SCHEMA.md' -print0 2>/dev/null) \
            | sort -rn | cut -f2-
        )
        count=0
        while IFS= read -r f; do
            [ -z "$f" ] && continue
            [ "$count" -ge 6 ] && break
            tid=$(basename "$f" .md)
            # frontmatter status 확인 (active/paused 만 노출, archived 스킵)
            st=$(awk -F': *' '/^status:/{print $2; exit}' "$f" 2>/dev/null | tr -d ' \r')
            case "$st" in
                archived|abandoned|done) continue ;;
            esac
            # summary 첫 줄 (## summary 아래 첫 비어있지 않은 줄)
            summ=$(awk '/^#+ *summary/{f=1;next} f&&NF{print;exit}' "$f" 2>/dev/null | head -c 160)
            [ -z "$summ" ] && summ="(요약 없음)"
            echo "- ${tid} [${st:-?}]: ${summ}"
            count=$((count+1))
        done <<< "$sorted"
        [ "$count" -eq 0 ] && echo "- (활성 토픽 없음)"
        echo ""
    fi

    # 2) progress.md 요약은 넣지 않는다. SessionStart 훅이 progress.md 를 이미 통째로
    #    따로 주입한다 — 여기 다시 넣으면 같은 내용을 두 번 싣고 6000바이트 예산만 먹는다.
    #    실제로 dotfiles 에서 예산 초과로 잘려나간 19줄이 전부 이 절이었다. 기각 목록과
    #    최근 결정은 이 파일 말고는 실릴 자리가 없으니 예산은 그쪽에 준다.

    # 3) history.md 최근 결정 (## [날짜] 헤더 라인만).
    #    3개는 너무 얕았다 — 결정이 428개인 저장소에서 마지막 3개는 "현재 유효한 결정"이
    #    아니라 "그저께 무슨 일이 있었나"다. 규약이 말하는 10~20개에 맞춘다.
    if [ -f "$HIST" ]; then
        htotal=$(grep -cE '^## \[' "$HIST" 2>/dev/null)   # 위와 같은 이유로 || echo 0 금지
        htotal=${htotal:-0}
        # 0건이면 절 자체를 만들지 않는다. "총 0건" 머리말만 남기면 아래 빈약판정을
        # 통과해버려서, 규약 형식이 없는 일지를 가진 프로젝트가 history.md 꼬리 폴백
        # 대신 빈 껍데기를 주입받는다.
        if [ "${htotal:-0}" -gt 0 ] 2>/dev/null; then
            echo "## 최근 결정 (history.md — 총 ${htotal}건 중 최근 12건)"
            grep -E '^## \[' "$HIST" 2>/dev/null | "$RECENT" -12 | sed 's/^## /- /'
            echo ""
            echo "(전체 결정/토픽 상세는 history.md / memory/topics/<id>.md lazy load)"
        fi
    fi
} > "$TMP" 2>/dev/null

# 줄 수 상한 강제. 몇 줄을 버렸는지 같이 적는다 — "상세는 원본 참조"만 남기면
# 무엇이 안 보이는지 알 수 없고, 그 침묵이 이 파일이 몇 달간 방치된 이유다.
_have=$(wc -l < "$TMP" 2>/dev/null || echo 0)
if [ "${_have:-0}" -gt "$MAXLINES" ] 2>/dev/null; then
    _dropped=$((_have - MAXLINES + 1))
    { head -n $((MAXLINES-1)) "$TMP"
      echo "- ... (${_dropped}줄 절단됨 — 이 파일이 상한 ${MAXLINES}줄을 넘었다. 원본 파일 참조)"
    } > "${TMP}.cut" 2>/dev/null && mv "${TMP}.cut" "$TMP" 2>/dev/null
fi

# 바이트 상한 강제. 자를 때는 줄 단위로만 자른다 — head -c 로 바이트를 자르면 한글이
# 반토막 나고, 그러면 주입 쪽 decode 가 터져 세션 맥락이 통째로 사라진다(03c0042).
# LC_ALL=C 는 awk length() 를 바이트로 고정한다. mawk 는 원래 바이트지만 gawk 는
# UTF-8 로케일에서 문자 수를 세서 예산을 3배로 잘못 잡는다.
_bytes=$(wc -c < "$TMP" 2>/dev/null || echo 0)
if [ "${_bytes:-0}" -gt "$MAXBYTES" ] 2>/dev/null; then
    _keep=$(LC_ALL=C awk -v cap="$((MAXBYTES - 240))" \
        '{ n += length($0) + 1; if (n > cap) exit; keep = NR } END { print keep + 0 }' \
        "$TMP" 2>/dev/null)
    _keep=${_keep:-1}
    [ "${_keep:-0}" -lt 1 ] 2>/dev/null && _keep=1
    _total=$(wc -l < "$TMP" 2>/dev/null || echo 0)
    _dropped=$(( _total - _keep ))
    if [ "${_dropped:-0}" -gt 0 ] 2>/dev/null; then
        { head -n "$_keep" "$TMP"
          echo "- ... (${_dropped}줄 절단됨 — 이 파일이 주입 예산 ${MAXBYTES}바이트를 넘었다(${_bytes}바이트). 잘린 부분은 어느 세션에도 안 보인다. 원본 파일 참조)"
        } > "${TMP}.cut" 2>/dev/null && mv "${TMP}.cut" "$TMP" 2>/dev/null
    fi
fi

# 실을 게 없으면 파일을 만들지 않는다. 기각도 결정도 토픽도 없으면 생성본은 머리말
# 두 줄뿐인데, 그걸 써버리면 주입기가 history.md 꼬리 4000바이트로 떨어지던 폴백을
# 잃는다 — 아무것도 없는 요약이 일지 꼬리보다 낫다고 볼 근거가 없다.
if ! grep -q '^## ' "$TMP" 2>/dev/null; then
    echo "[$uts] SKIP: 실을 내용 없음(기각·결정·토픽 0) → active.md 안 만듦(history.md 폴백 유지) CWD=$CWD" >> "$ULOG"
    exit 3
fi

# 내용이 그대로면 다시 쓰지 않는다. 헤더의 updated: 시각 때문에 매 턴 파일이 달라져서,
# 이 훅이 도는 저장소는 실제로 바뀐 게 없어도 계속 mtime 이 갱신됐다. 시각 줄만 빼고
# 비교한다 — 시각이 바뀌는 건 내용이 바뀐 게 아니다.
# rc 3 = "일부러 안 썼다(파일은 이미 최신)". 호출자(Stop 훅)는 mtime 이 안 움직인 걸
# 실패로 보고 매 턴 재시도하므로, 안 쓴 이유를 종료코드로 구분해준다.
if [ -f "$ACTIVE" ] \
   && diff -q <(grep -v '^updated: ' "$ACTIVE" 2>/dev/null) \
              <(grep -v '^updated: ' "$TMP" 2>/dev/null) >/dev/null 2>&1; then
    # 이 줄이 없으면 rc 3 의 사유가 어디에도 안 남는다. Stop 훅 스탬프 문구는
    # "사유는 위 REFUSE/SKIP 줄" 이라고 가리키는데 그게 없으면 읽는 사람은 원인을
    # 못 찾는다 — 조용한 성공도 조용한 실패와 같은 값으로 보인다.
    echo "[$uts] NOCHANGE: 입력이 그대로라 active.md 재작성 안 함(파일은 최신) CWD=$CWD" >> "$ULOG"
    exit 3
fi

# 원자적 교체 (같은 FS 이므로 원자적). 성공하면 trap 이 지울 대상 없음.
if mv "$TMP" "$ACTIVE" 2>/dev/null; then
    trap - EXIT
    exit 0
fi
exit 0
