#!/bin/bash
# route-nudge.sh: Stop 훅 — observe-only 1단계 (2026-07-06 ai-debate 결론 반영).
#
# 서브에이전트 라우팅 정책(CLAUDE.md ## 라우팅) 준수를 관측만 한다. 이 턴에 heavy 도구호출이
# 많은데 Agent/Task 위임이 하나도 없으면 "위임후보"로 JSONL에 조용히 기록만 하고 절대
# 차단·경고하지 않는다(exit 0 고정, stderr 없음). 1주치 로그를 사람이 라벨링해서 오탐률
# 20% 이하면 그 다음 단계(소프트 리마인더)를 켠다 — 이 파일은 아직 관측만 담당.
#
# 위임 적절성은 텔레그램 답장 여부처럼 이분법이 아니라 판단 영역이라, 근거 데이터 없이
# 바로 경고를 켜면 오탐 누적으로 alert fatigue만 생길 위험이 크다고 판단(ai-debate
# run-20260706T010800Z arbiter 결론).

set -uo pipefail

# 자동 작업(claude -p 등)은 사용자 세션 아님 → 관측 대상 아님.
[ "${CLAUDE_AUTOMATED:-0}" = "1" ] && exit 0

LOG_DIR="$HOME/.claude/state/route-nudge"
LOG_FILE="$LOG_DIR/log.jsonl"
mkdir -p "$LOG_DIR" 2>/dev/null

EVENT=$(cat 2>/dev/null || echo '{}')

# shellcheck disable=SC2016
EVENT_JSON="$EVENT" LOG_FILE="$LOG_FILE" python3 <<'PYEOF' 2>/dev/null
import json, sys, os, time

try:
    e = json.loads(os.environ.get("EVENT_JSON", "{}"))
except Exception:
    sys.exit(0)

if e.get("stop_hook_active"):
    sys.exit(0)

tp = e.get("transcript_path")
if not tp or not os.path.exists(tp):
    sys.exit(0)

try:
    with open(tp, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 1_000_000))
        raw = f.read().decode("utf-8", errors="ignore")
        lines = raw.splitlines()
except Exception:
    sys.exit(0)

parsed = []
for line in lines:
    try: parsed.append(json.loads(line))
    except Exception: parsed.append(None)

def is_real_user(d):
    if not d or d.get("type") != "user":
        return False
    if d.get("isCompactSummary"):
        return False
    msg = d.get("message", {}) or {}
    c = msg.get("content")
    if isinstance(c, str):
        return True
    if isinstance(c, list):
        for it in c:
            if isinstance(it, dict):
                if it.get("type") == "tool_result" or "tool_use_id" in it:
                    return False
                if it.get("type") == "text" or "text" in it:
                    return True
        return True
    return False

last_user_idx = -1
for i in range(len(parsed) - 1, -1, -1):
    if is_real_user(parsed[i]):
        last_user_idx = i
        break

if last_user_idx < 0:
    sys.exit(0)

current_turn = [d for d in parsed[last_user_idx:] if d]

HEAVY_1 = {"Bash", "Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch"}
HEAVY_HALF = {"Grep", "Glob", "LS"}

heavy_score = 0.0
tool_counts = {}
distinct_files = set()
bash_count = 0
edit_or_write_count = 0
read_or_edit_count = 0
agent_calls = 0
route_declared = False

for d in current_turn:
    if d.get("type") != "assistant":
        continue
    msg = d.get("message", {}) or {}
    content = msg.get("content", [])
    if not isinstance(content, list):
        continue
    for it in content:
        if not isinstance(it, dict):
            continue
        if it.get("type") == "text":
            txt = it.get("text", "") or ""
            if "[route" in txt:
                route_declared = True
        if it.get("type") != "tool_use":
            continue
        name = it.get("name", "")
        tool_counts[name] = tool_counts.get(name, 0) + 1
        if name in ("Task", "Agent"):
            agent_calls += 1
            continue
        inp = it.get("input", {}) or {}
        if name in HEAVY_1:
            heavy_score += 1.0
        elif name in HEAVY_HALF:
            heavy_score += 0.5
        if name == "Bash":
            bash_count += 1
        if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            edit_or_write_count += 1
        if name in ("Read", "Edit", "MultiEdit"):
            read_or_edit_count += 1
        fp = inp.get("file_path")
        if isinstance(fp, str) and fp:
            distinct_files.add(fp)

if agent_calls > 0:
    sys.exit(0)  # 이미 위임했으니 후보 아님

candidate = (
    heavy_score >= 8
    or len(distinct_files) >= 4
    or edit_or_write_count >= 3
    or (bash_count >= 4 and read_or_edit_count >= 3)
)

if not candidate:
    sys.exit(0)

reasons = []
if heavy_score >= 8: reasons.append("heavy_score>=8")
if len(distinct_files) >= 4: reasons.append("distinct_files>=4")
if edit_or_write_count >= 3: reasons.append("edit_or_write>=3")
if bash_count >= 4 and read_or_edit_count >= 3: reasons.append("bash+read_edit combo")

record = {
    "ts": int(time.time()),
    "cwd": e.get("cwd", ""),
    "session_id": e.get("session_id", ""),
    "heavy_score": heavy_score,
    "tool_counts": tool_counts,
    "distinct_files": len(distinct_files),
    "bash_count": bash_count,
    "edit_or_write_count": edit_or_write_count,
    "agent_calls": agent_calls,
    "route_declared": route_declared,
    "suppressed": True,  # 1단계 observe-only — 사용자에게 노출 안 됨
    "candidate_reason": ",".join(reasons),
}

try:
    with open(os.environ["LOG_FILE"], "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
except Exception:
    pass
PYEOF

exit 0
