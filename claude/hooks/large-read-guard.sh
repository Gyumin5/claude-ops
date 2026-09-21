#!/bin/bash
# large-read-guard.sh: PreToolUse hook (matcher: Read).
#
# 목적: Prompt is too long 사고의 주요 원인인 대형 파일 통째 read 차단.
# Read tool 호출에서 file_path 의 byte/line 검사:
# - offset/limit 명시 → 통과 (이미 청크화 중)
# - 5MB 초과 또는 10K 줄 초과 → 차단 + ctx_read 안내
# - jsonl/log/min.js/css 등 위험 확장자는 임계 절반 적용
#
# 출력: 허용 시 exit 0. 차단 시 hookSpecificOutput.permissionDecision="deny" + reason.

set -uo pipefail

LIMIT_BYTES=$((5 * 1024 * 1024))
LIMIT_LINES=10000

INPUT=$(cat 2>/dev/null || echo '{}')

INPUT_JSON="$INPUT" LIMIT_BYTES="$LIMIT_BYTES" LIMIT_LINES="$LIMIT_LINES" python3 <<'PYEOF'
import json, os, sys

LIMIT_BYTES = int(os.environ.get("LIMIT_BYTES", 5*1024*1024))
LIMIT_LINES = int(os.environ.get("LIMIT_LINES", 10000))

# 위험 확장자: 한 줄이 매우 길거나 minified 가능성
RISKY_EXT = {".jsonl", ".log", ".ndjson", ".min.js", ".min.css", ".map"}

def allow():
    sys.exit(0)

def deny(reason):
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0)

try:
    data = json.loads(os.environ.get("INPUT_JSON", "{}"))
except Exception:
    allow()

if data.get("tool_name") != "Read":
    allow()

ti = data.get("tool_input", {}) or {}
fp = ti.get("file_path") or ""
if not fp:
    allow()

# offset/limit 이미 지정 → 청크화 중이므로 통과
if ti.get("offset") is not None or ti.get("limit") is not None:
    allow()
# pages 옵션 (PDF) 도 청크화로 간주
if ti.get("pages"):
    allow()

# 파일 없거나 디렉토리면 통과 (Read tool 자체가 처리)
if not os.path.isfile(fp):
    allow()

# 위험 확장자면 임계 절반
threshold_bytes = LIMIT_BYTES
threshold_lines = LIMIT_LINES
fname_lower = fp.lower()
if any(fname_lower.endswith(ext) for ext in RISKY_EXT):
    threshold_bytes //= 2
    threshold_lines //= 2

try:
    size = os.path.getsize(fp)
except Exception:
    allow()

# byte 검사
if size > threshold_bytes:
    mb = size / (1024 * 1024)
    deny(
        f"large-read-guard 차단: '{fp}' 크기 {mb:.1f}MB (임계 {threshold_bytes/(1024*1024):.1f}MB). "
        "통째 read 금지. 다음 중 하나로 다시 시도: "
        "(1) Read tool에 offset/limit 지정, "
        "(2) ctx_read mode='lines:N-M' 또는 mode='signatures', "
        "(3) ctx_search 로 패턴 좁힌 grep, "
        "(4) PDF면 Read pages='1-5' 식. "
        "참고: ~/.claude/CLAUDE.md '큰 입력 방어'."
    )

# line 검사 (binary file이면 wc -l이 의미 없을 수 있어 sample로 추정)
# 빠른 추정: 처음 1MB 기준 newline 비율로 전체 라인 수 추정
try:
    with open(fp, "rb") as f:
        sample = f.read(min(1 * 1024 * 1024, size))
    nl = sample.count(b"\n")
    if len(sample) > 0:
        est_lines = int(nl * size / len(sample))
    else:
        est_lines = 0
except Exception:
    est_lines = 0

if est_lines > threshold_lines:
    deny(
        f"large-read-guard 차단: '{fp}' 추정 {est_lines:,} 줄 (임계 {threshold_lines:,}). "
        "통째 read 금지. offset/limit 또는 ctx_read mode='lines:N-M', ctx_search 사용. "
        "참고: ~/.claude/CLAUDE.md '큰 입력 방어'."
    )

allow()
PYEOF

# (위 python에서 모든 분기 처리 후 exit. 여기 도달 시 허용으로 간주)
exit 0
