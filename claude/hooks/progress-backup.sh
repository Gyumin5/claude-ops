#!/bin/bash
# progress-backup: PostToolUse 훅. progress.md / handoff.md 의 권위 사본을 저장소 밖에 둔다.
#
# 계기 (2026-08-07 proj-b-wt/lidar 실사고):
#   progress.md 가 lidar 브랜치에선 .gitignore 대상인데 fusion 브랜치에선 추적 파일이었다.
#   git 은 추적 안 되는 파일은 checkout 때 덮어쓰기를 거부하지만 ★무시된 파일은 조용히 덮어쓴다.
#   `git checkout -b <새브랜치> <fusion커밋>` 한 방에 progress.md 가 사라졌다. rc 0, 경고 없음,
#   git status 도 깨끗. 몇 시간 뒤에야 발견했다.
#
# 왜 차단 훅이 아니라 사본인가 (ai-debate run-20260807T020401Z, 만장일치):
#   checkout 은 파괴 경로 중 하나일 뿐이다. reset --hard, clean -x, merge/rebase, 외부 git
#   클라이언트가 전부 같은 짓을 한다. 게다가 셸 문자열을 파싱해 checkout 의미를 판정하는 건
#   신뢰할 수 없다(git -C, alias, switch, 경로형 checkout). 그래서 "명령을 막는" 대신
#   "원본을 저장소 밖에 둔다" 로 간다 — 무엇이 지웠든 상관없어진다.
#
# 성질: 절대 차단하지 않는다(PostToolUse, 항상 exit 0). 실패해도 조용히 통과 = fail-open.
#       비용은 파일 두 개 cmp 한 번. progress.md 는 상한 180줄이라 무시 수준.
#
# 저장: ~/.claude/state/progress-backup/<slug>/{latest.md, snap-<ts>.md, MISSING}
# 보존: 스냅샷 최근 20개.

set -uo pipefail

INPUT=$(cat 2>/dev/null || echo '{}')

CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"
[ -d "$CWD" ] || exit 0

# slug: 전체 경로 기반. basename 만 쓰면 워크트리끼리 겹칠 수 있다.
slug=$(echo "${CWD#$HOME/}" | tr '/' '-' | tr -cd 'A-Za-z0-9._-')
[ -z "$slug" ] && slug="root"

BK_ROOT="$HOME/.claude/state/progress-backup/$slug"

for name in progress.md handoff.md; do
    SRC="$CWD/$name"
    DIR="$BK_ROOT/$name"
    LATEST="$DIR/latest.md"

    if [ -s "$SRC" ]; then
        mkdir -p "$DIR" 2>/dev/null || continue
        # 사라졌다 돌아온 경우 경보 해제.
        rm -f "$DIR/MISSING" 2>/dev/null
        # 내용이 같으면 아무것도 안 한다.
        if [ -f "$LATEST" ] && cmp -s "$SRC" "$LATEST"; then
            continue
        fi
        ts=$(date +%Y%m%dT%H%M%S)
        cp -p "$SRC" "$DIR/snap-${ts}.md" 2>/dev/null
        cp -p "$SRC" "$LATEST" 2>/dev/null
        # 보존: 최근 20개.
        ls -1t "$DIR"/snap-*.md 2>/dev/null | tail -n +21 | while read -r old; do
            rm -f "$old" 2>/dev/null
        done
    else
        # 있었는데 없어졌다 = 사고 서명. 사본은 이미 latest.md 에 있다.
        # 사람·세션이 몇 시간 뒤가 아니라 지금 알도록 stderr 로 올린다.
        if [ -s "$LATEST" ] && [ ! -f "$DIR/MISSING" ]; then
            touch "$DIR/MISSING" 2>/dev/null
            echo "[progress-backup] $SRC 가 사라졌다. 마지막 사본: $LATEST" >&2
            echo "[progress-backup] 복구: cp $LATEST $SRC" >&2
            echo "[progress-backup] 흔한 원인 — 그 파일이 이 브랜치에선 무시 대상인데 갈아탄 브랜치에선 추적 파일이라 git 이 조용히 덮어썼다." >&2
        fi
    fi
done

exit 0
