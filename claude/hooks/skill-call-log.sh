#!/bin/bash
# skill-call-log.sh: log Skill tool invocations to telemetry jsonl.
# PostToolUse hook. Receives JSON event on stdin from Claude Code.

set -uo pipefail

LOG=~/.claude/telemetry/skill-calls.jsonl
mkdir -p "$(dirname "$LOG")" 2>/dev/null
: >> "$LOG"

# Read full event from stdin (best-effort; never block tool flow)
EVENT=$(cat 2>/dev/null || echo "{}")

python3 - "$EVENT" "$LOG" <<'PY' 2>/dev/null || true
import json, sys, os, time
try:
    event = json.loads(sys.argv[1])
except Exception:
    sys.exit(0)
log = sys.argv[2]

tool = event.get("tool_name") or event.get("tool") or ""
if tool != "Skill":
    sys.exit(0)

inp = event.get("tool_input") or event.get("input") or {}
skill = inp.get("skill") or ""
args = inp.get("args") or ""
session_id = event.get("session_id") or event.get("sessionId") or ""

rec = {
    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "project": os.path.basename(os.getcwd()),
    "skill": skill,
    "args_len": len(str(args)),
    "session_id": session_id[:16],
}
with open(log, "a") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
PY
