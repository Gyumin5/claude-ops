#!/bin/bash
# git-commit-post: PostToolUse Bash 훅. `git commit` 성공 시:
#   1) 그 커밋이 들어간 저장소의 progress.md 강제 갱신
#      (claude-progress-updater 백그라운드 실행, 임계 우회)
#   2) 결정 신호가 있으면 ★세션 소유 스테이징 파일
#      ~/.claude/state/history-stubs/<세션>.md 에 스텁 append. ★history.md 에는 안 쓴다 —
#      번호와 본문은 그 세션이 합칠 때 정한다(아래 2) 절의 주석이 그 이유다).
#   결정 신호는 둘 다다: 제목의 [DECISION]·DECISION:, 그리고 본문의 `근거:` 줄.
#   전역 규칙이 "마커 또는 근거 줄" 로 정의하므로 한쪽만 보면 절반을 놓친다.
# 실패한 commit / commit 아닌 git 명령 / git 없는 디렉토리 → no-op, 빠르게 exit.
#
# 대상 저장소는 ★명령이 가리킨 곳이다. `git -C <경로> commit` 은 세션 작업디렉토리가
# 아니라 그 경로에 커밋하므로, 작업디렉토리를 저장소로 가정하면 엉뚱한 저장소의
# HEAD 를 읽는다. 하네스가 작업 도중 작업디렉토리를 하위로 옮기기도 한다(R06).

set -uo pipefail

INPUT=$(cat 2>/dev/null || echo '{}')
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
[ "$TOOL" = "Bash" ] || exit 0

CMD=$(echo "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null)
[ -z "$CMD" ] && exit 0

CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

# 성공 판정: stdout 파싱에 의존하지 않는다 — `git commit -q` 는 성공줄
# ("[branch hash] subject")을 출력하지 않아 예전 정규식 게이트가 매번 걸러
# history.md 자동기록이 조용히 누락됐다(2026-07-23 규명). 권위는 stdout 이 아니라
# 저장소 상태여야 한다(ai-debate run-20260723T060110Z): exit_code 로 실패만 걸러내고
# 실제 판정은 아래 HEAD 해시 + 스텁 dedup 으로 한다.
EXIT=$(echo "$INPUT" | jq -r '.tool_response.exit_code // .tool_response.returncode // 0' 2>/dev/null)
# 알려진 실패(pre-commit hook fail, nothing-to-commit 등)는 exit !=0 → 스킵.
if [ "$EXIT" != "0" ] && [ "$EXIT" != "null" ]; then
    exit 0
fi

# 커밋 명령인가, 그리고 어느 디렉토리에 커밋했나.
# 문자열 매칭이 아니라 토큰으로 본다 — `echo "git commit"` 은 커밋이 아니고,
# `git -C <경로> commit` 은 커밋이다. 둘 다 정규식으로는 못 가른다.
read -r -d '' FIND_TARGET <<'PY'
import os
import re
import shlex
import sys

cmd = sys.stdin.read()
cwd = os.environ.get("HOOK_CWD") or os.getcwd()

# 다음 토큰이 다시 명령 시작 자리가 되는 것들. 기호는 글자 집합으로 본다 —
# 어휘 분석기가 이어진 기호를 한 토큰으로 묶어 내놓기 때문이다(");" 같은 모양).
SEP_WORDS = {"then", "do", "else", "elif", "{", "}", "!"}
SEP_CHARS = set("|&;()")
WRAPPERS = {"env", "nohup", "setsid", "timeout", "stdbuf", "command",
            "exec", "time", "xargs"}
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
# 뒤에 값을 따로 받는 git 전역 옵션
VALUE_OPTS = {"-c", "--git-dir", "--work-tree", "--namespace",
              "--exec-path", "--config-env"}


def is_sep(t):
    return t in SEP_WORDS or (bool(t) and all(c in SEP_CHARS for c in t))


try:
    # 쪼개 놓고 글자로 `|&;` 를 다시 가르면 따옴표로 보호된 인자까지 갈려서 그
    # 조각이 명령 시작 자리로 승격된다 — 커밋 메시지 안의 세로줄 하나로 엉뚱한
    # 조각이 커밋으로 읽힌다. 어휘 분석기 안에서 가르면 따옴표를 존중한다.
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    toks = list(lex)
except ValueError:
    sys.exit(1)

i = 0
expect = True
while i < len(toks):
    t = toks[i]
    if is_sep(t):
        expect = True
        i += 1
        continue
    if not expect:
        i += 1
        continue
    if ASSIGN.match(t):
        i += 1
        continue
    base = os.path.basename(t)
    if base in WRAPPERS:
        i += 1
        continue
    if base != "git":
        expect = False
        i += 1
        continue

    # git 전역 옵션을 넘기며 -C 를 누적한다. git 은 -C 를 여러 번 받으면 차례로 적용한다.
    target = cwd
    j = i + 1
    while j < len(toks):
        a = toks[j]
        if a == "-C" and j + 1 < len(toks):
            target = os.path.join(target, toks[j + 1])
            j += 2
            continue
        if a.startswith("-C") and len(a) > 2:
            target = os.path.join(target, a[2:])
            j += 1
            continue
        if a in VALUE_OPTS and j + 1 < len(toks):
            j += 2
            continue
        if a.startswith("-"):
            j += 1
            continue
        break

    if j < len(toks) and toks[j] == "commit":
        rest = []
        for k in range(j + 1, len(toks)):
            if is_sep(toks[k]):
                break
            rest.append(toks[k])
        # --dry-run 은 커밋을 만들지 않는다. 그 git 명령의 인자 안에서만 본다 —
        # 명령줄 전체에서 찾으면 뒤에 붙은 다른 명령의 --dry-run 에 걸린다.
        if "--dry-run" in rest:
            expect = False
            i = j + 1
            continue
        print(os.path.normpath(target))
        sys.exit(0)

    expect = False
    i += 1

sys.exit(1)
PY

TARGET=$(printf '%s' "$CMD" | HOOK_CWD="$CWD" python3 -c "$FIND_TARGET")
[ $? -eq 0 ] || exit 0
[ -n "$TARGET" ] || exit 0

# ★생존 카운터. 결과와 무관하게 "불렸다" 를 남긴다 — 이게 없으면 "기록이 없다" 가
#   ★"기록할 일이 없었다" 인지 ★"훅이 안 돌았다" 인지 구분되지 않는다. 2026-07-29 사고의 정체가
#   정확히 그것이었다(워크트리에서 이 훅이 영구 무동작인데 기록할 일이 없던 것과 구분 불가).
#   ★맥락(저장소)별로 남긴다 — 한 맥락의 생존은 다른 맥락을 보증하지 않는다.
ALIVE="$HOME"/.claude/logs/git-commit-post-alive.tsv
mkdir -p "$(dirname "$ALIVE")" 2>/dev/null; touch "$ALIVE" 2>/dev/null
awk -v k="$TARGET" -v now="$(date '+%F %T')" '
  BEGIN{FS=OFS="\t"; found=0}
  $1==k{ $2=$2+1; $3=now; found=1 }
  {print}
  END{ if(!found) print k, 1, now }
' "$ALIVE" > "$ALIVE.tmp" 2>/dev/null && mv "$ALIVE.tmp" "$ALIVE"

# ★`.git` 은 워크트리에서 ★파일이다(gitdir 포인터) ⟹ 옛 `-d "$CWD/.git"` 검사는 워크트리
#   전체를 조용히 건너뛰었다. camera·lidar·leg 에서 이 훅은 도달조차 못 했다(4경로 실측).
#   ★저장소 루트는 git 에게 묻는다. 커밋한 자리가 하위 디렉토리여도 루트를 찾아 쓴다 —
#   progress.md 와 결정 기록은 루트에 있고, 하위에서 커밋했다고 건너뛸 이유가 없다.
REPO=$(git -C "$TARGET" rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO" ]; then
    echo "$(date '+%F %T') git-commit-post: 저장소 아님 — 생략 cwd=$CWD target=$TARGET" \
        >> "$HOME"/.claude/logs/git-commit-post.log
    exit 0
fi

# 최근 commit info
HASH=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null)
[ -z "$HASH" ] && exit 0
SUBJ=$(git -C "$REPO" log -1 --format='%s' 2>/dev/null)
BODY=$(git -C "$REPO" log -1 --format='%b' 2>/dev/null)
DATE=$(date '+%Y-%m-%d %H:%M KST')

# 1) progress.md 강제 갱신 (백그라운드, 사용자 응답 지연 방지)
if [ -f "$REPO/progress.md" ] && command -v claude-progress-updater >/dev/null 2>&1; then
    CLAUDE_PROGRESS_FORCE=1 nohup "$HOME"/.local/bin/claude-progress-updater "$REPO" \
        >> "$HOME"/.claude/logs/progress-updater.log 2>&1 &
fi

# 2) 결정 신호 → 세션 스텁 append
#    제목과 본문을 ★개행으로 이어서 본다. 붙여 쓰면 본문 첫 줄이 제목 뒤에 이어져
#    행 시작 앵커(^DECISION:·^근거:)가 영영 안 걸린다.
if printf '%s\n%s\n' "$SUBJ" "$BODY" \
        | grep -qiE '\[DECISION\]|^DECISION:|^근거:|^reason:|^why:'; then
    # ★기록 대상은 ★저장소가 아니라 ★세션이다. 저장소로 잡으면 A 세션의 커밋이 B 세션의
    #   결정 로그와 ★번호 공간까지 쓴다(2026-07-29 실측 — 둘째 기계 쪽 스텁 제거 8~9회).
    #   ⟹ 세션 소유 스테이징 파일에만 쌓고, 각 세션이 자기 history.md 로 ★스스로 합친다.
    SESS="${CLAUDE_SESSION_NAME:-}"
    if [ -z "$SESS" ]; then
        SESS=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null | cut -c1-16)
    fi
    # ★fail-closed: 세션을 특정할 수 없으면 ★아무 곳에도 쓰지 않는다. 못 쓰는 것이 남의
    #   파일에 쓰는 것보다 낫다. 대신 흔적을 남겨 침묵을 신호로 바꾼다.
    if [ -z "$SESS" ]; then
        echo "$(date '+%F %T') git-commit-post: 세션 미특정 — 기록 생략 repo=$REPO hash=$HASH" \
            >> "$HOME"/.claude/logs/git-commit-post.log
        exit 0
    fi
    STAGE_DIR="$HOME"/.claude/state/history-stubs
    mkdir -p "$STAGE_DIR" 2>/dev/null
    HIST="$STAGE_DIR/$SESS.md"
    touch "$HIST" 2>/dev/null
    # dedup: 같은 커밋 해시가 이미 기록돼 있으면 재append 금지. 훅이 커밋 아닌 git
    # 명령에서도 발동할 수 있는데(HEAD 는 직전 결정 커밋), 해시 dedup 이 있으면 그
    # 경우에도 중복 없이 최대 1회만 기록된다(그 1회는 원래 누락됐어야 할 항목을
    # 자가치유하는 셈이라 무해).
    if [ -f "$HIST" ] && grep -qF "hash: $HASH" "$HIST" 2>/dev/null; then
        exit 0
    fi
    # 본문에서 근거 줄 추출 (없으면 subject 만 사용)
    REASON=$(echo "$BODY" | grep -iE '^근거:|^reason:|^why:' | head -1 | sed 's/^[^:]*: *//')
    [ -z "$REASON" ] && REASON="$SUBJ"
    # ★번호를 훅이 매기지 않는다 — 번호는 ★합칠 때 그 세션이 정한다. 자동 번호와 수동 번호가
    #   같은 자원을 다투던 구조가 여기서 사라진다.
    {
        echo ""
        echo "## [$(date '+%Y-%m-%d')] (미배번) $SUBJ"
        echo "tags: commit, 미합침"
        echo "- repo: $REPO"
        echo "- session: $SESS"
        echo "- 결정: $SUBJ"
        echo "- 근거: $REASON"
        echo "- hash: $HASH"
        echo "- updated: $DATE"
    } >> "$HIST"
fi

exit 0
