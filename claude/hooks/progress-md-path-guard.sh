#!/bin/bash
# progress-md-path-guard.sh: PreToolUse 경고형 훅 — progress.md 를 세션 루트가 아닌
# 곳에 쓰려는 Write/Edit/MultiEdit 을 감지해 경고만 한다(차단 안 함). 항상 exit 0.
#
# 왜 있나: 2026-07-08 에 어떤 세션이 progress.md 를 세션 루트가 아니라 그 아래 하위
# 저장소에 썼다. 세션 시작 때 주입되는 것은 루트 파일뿐이라(session-start-context.sh),
# 재시작 때 20일 묵은 파일을 물려받고 인계가 실패했다.
#
# 세션 루트는 CLAUDE_PROJECT_DIR 로 판정한다. 현재 작업디렉토리(cwd)와 비교하지
# 않는다 — 하네스가 작업 도중 작업디렉토리를 옮기기 때문이다. 2026-09-23~10-12 로그
# 27건이 전부 그 때문에 난 오탐이었다(대상이 실제로는 루트인데 cwd 가 그 아래로
# 내려가 있었다). 2026-10-12 실측: 훅 환경에는 CLAUDE_PROJECT_DIR 가 들어오고
# (도구 셸 환경에는 안 들어온다) cwd 를 하위로 옮겨도 루트 값 그대로 유지된다.
#
# 변수가 없는 환경에서는 근사 판정으로 내려간다 — 대상이 cwd 자신이거나 그 상위면
# 경고하지 않는다. 이건 정확한 루트 판정이 아니라서, 그 회차 기록에 root=미확인 으로
# 남긴다. 변수가 없다고 cwd 를 루트로 대입하지 않는다.
#
# 차단형(deny)으로 올리지 않는다 — 2026-10-12 토론 run-20261012T121643Z 결론이고,
# 정당한 하위 progress.md 까지 막는 오탐 비용이 크다는 도입 당시 판단 그대로다.
set -uo pipefail

# 파이썬 본문은 heredoc 으로 변수에 담아 -c 로 넘긴다. `python3 - <<'PYEOF'` 로 쓰면
# 그 heredoc 이 stdin 을 차지해서 훅이 받은 JSON 을 읽을 수 없다 — 입력이 비면 조용히
# 종료하므로 모든 회차가 무경고로 보인다(2026-10-12 테스트에서 잡았다).
PYSRC=$(cat <<'PYEOF'
import json, os, sys

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not isinstance(d, dict):
    sys.exit(0)

tool = d.get("tool_name") or ""
if tool not in ("Write", "Edit", "MultiEdit"):
    sys.exit(0)
ti = d.get("tool_input") or {}
fp = (ti.get("file_path") or "") if isinstance(ti, dict) else ""
if not fp or os.path.basename(fp) != "progress.md":
    sys.exit(0)

target = os.path.dirname(os.path.realpath(fp))
root = (os.environ.get("PROJECT_ROOT") or "").strip()
cwd = (d.get("cwd") or "").strip()

if root:
    root = os.path.realpath(root)
    warn = target != root
    approx = False
else:
    # 근사 판정. 루트를 모르므로 "대상이 cwd 자신이거나 그 상위" 를 침묵 조건으로 쓴다.
    if not cwd:
        sys.exit(0)
    cwd_real = os.path.realpath(cwd)
    warn = not (target == cwd_real or cwd_real.startswith(target + os.sep))
    approx = True

if not warn:
    sys.exit(0)

if approx:
    msg = ("[progress-md-path-guard] 경고: progress.md 를 %s 에 쓰려 합니다. 세션 루트를 "
           "확인하지 못해(CLAUDE_PROJECT_DIR 없음) 현재 작업디렉토리 %s 기준으로 근사 "
           "판정했습니다 — 정확한 루트 판정이 아닙니다. 세션 시작 때 주입되는 것은 세션 "
           "루트의 progress.md 뿐이라, 다른 곳에 쓴 파일은 다음 세션이 못 읽습니다. "
           "의도한 별개 파일이면 그대로 두고, 세션 진행상황 파일이면 R05 기준에 맞는지 "
           "확인하세요." % (target, cwd_real))
else:
    msg = ("[progress-md-path-guard] 경고: progress.md 를 세션 루트 %s 가 아니라 %s 에 "
           "쓰려 합니다. 세션 시작 때 주입되는 것은 세션 루트의 progress.md 뿐이라, 이 "
           "파일은 다음 세션이 못 읽습니다. 의도한 별개 파일이면 그대로 두고, 세션 "
           "진행상황 파일이면 R05 기준에 맞는지 확인하세요." % (root, target))
print(msg, file=sys.stderr)

# 경고는 그 세션 화면에만 뜨고 사라진다. 나중에 "위반이 몇 건이고 그중 오탐이 몇
# 건인가" 를 물으면 답할 자료가 있어야 하므로 경고한 회차만 한 줄 남긴다.
log = os.environ.get("GUARD_LOG") or ""
try:
    os.makedirs(os.path.dirname(log), exist_ok=True)
    from datetime import datetime
    line = "%s tool=%s cwd=%s target=%s root=%s\n" % (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tool,
        cwd or "-", target, "미확인" if approx else root)
    with open(log, "a", encoding="utf-8") as f:
        f.write(line)
    with open(log, encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) > 200:
        with open(log, "w", encoding="utf-8") as f:
            f.writelines(lines[-200:])
except Exception:
    pass
sys.exit(0)
PYEOF
)

GUARD_LOG="${PROGRESS_MD_PATH_GUARD_LOG:-$HOME/.claude/state/progress-md-path-guard.log}" \
PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-}" \
python3 -c "$PYSRC"
exit 0
