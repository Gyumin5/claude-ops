#!/bin/bash
# progress-md-path-guard.sh: PreToolUse 경고형 훅 — progress.md를 세션 WD 루트가
# 아닌 다른 경로(예: 하위 서브레포)에 쓰려는 Write/Edit/MultiEdit을 감지해 경고만
# 한다(차단 안 함). exit 0 항상 — 정당한 하위 progress.md 편집(사용자가 명시
# 요청한 별개 파일 등)까지 막으면 오탐 비용이 크다는 게 토론 결론.
#
# 배경: 2026-07-08 peer 로컬 dotfiles세션이 실측 발견 — peer R&D 세션이
# progress.md를 WD 루트($HOME/peer)가 아니라 하위 서브레포
# (sub-repo/)에 쓰고 있었음. SessionStart 컨텍스트 주입은
# WD 루트만 읽으므로 재시작 시 20일 묵은 stale 파일을 물려받고 실제 인계
# 실패가 발생함. CLAUDE.md에 이미 "WD 루트에만 쓴다" 규칙이 있었는데도
# enforcement가 없어 위반됨 — ai-debate run-20260708T071716Z 결론(차단형은
# 정당한 하위 progress.md와 세션상태 파일을 구분 못해 오탐 위험 크므로 보류,
# 경고형+관측 먼저 → 2~4주 후 위반/오탐 로그로 차단 승격 여부 재검토).
set -uo pipefail

read -r TOOL FILE_PATH CWD <<<"$(python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print('', '', '')
    sys.exit(0)
tool = d.get('tool_name', '') or ''
ti = d.get('tool_input') or {}
fp = ti.get('file_path', '') or ''
cwd = d.get('cwd', '') or ''
print(tool, fp, cwd)
" 2>/dev/null)"

case "$TOOL" in Write|Edit|MultiEdit) ;; *) exit 0 ;; esac
[ -n "$FILE_PATH" ] && [ "$(basename "$FILE_PATH")" = "progress.md" ] || exit 0
[ -n "$CWD" ] || exit 0

FILE_DIR=$(dirname "$(realpath -m "$FILE_PATH" 2>/dev/null || echo "$FILE_PATH")")
CWD_REAL=$(realpath -m "$CWD" 2>/dev/null || echo "$CWD")

if [ "$FILE_DIR" != "$CWD_REAL" ]; then
    echo "[progress-md-path-guard] 경고: progress.md를 세션 루트(${CWD_REAL})가 아닌 ${FILE_DIR}에 쓰려 합니다. SessionStart 컨텍스트 주입은 세션 루트만 읽으므로, 재시작 시 이 내용이 무시되고 stale 파일을 물려받을 수 있습니다. 의도한 하위 파일이면 무시해도 되지만, 세션 진행상황 파일이면 루트로 옮기세요." >&2
fi
exit 0
