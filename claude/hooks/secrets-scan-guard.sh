#!/bin/bash
# secrets-scan-guard.sh: PreToolUse hook (matcher: Bash).
#
# 목적: git push / git commit 시 staged diff 안 secrets 자동 검사.
# 발견 시 차단(deny). false positive 줄이기 위해 단순 키워드보단 패턴 위주.

set -uo pipefail

INPUT=$(cat 2>/dev/null || echo '{}')

INPUT_JSON="$INPUT" python3 <<'PYEOF'
import json, os, sys, subprocess, re

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

if data.get("tool_name") not in ("Bash", "bash"):
    allow()

cmd = (data.get("tool_input") or {}).get("command") or ""
# 사용자 명시 우회
if re.search(r"\bBYPASS_SECRETS_SCAN\s*=\s*1\b", cmd):
    allow()
# git commit / git push 만 검사
if not re.search(r"\bgit\s+(commit|push)\b", cmd):
    allow()

# 경로: cwd 추적
cwd = data.get("cwd") or os.getcwd()

# staged diff 가져오기
try:
    diff = subprocess.run(
        ["git", "-C", cwd, "diff", "--cached", "-U0"],
        capture_output=True, text=True, timeout=15
    ).stdout
except Exception:
    allow()

if not diff:
    # commit -a 같이 staged 안 된 변경도 있을 수 있음 — working tree 까지 검사
    try:
        diff = subprocess.run(
            ["git", "-C", cwd, "diff", "HEAD", "-U0"],
            capture_output=True, text=True, timeout=15
        ).stdout
    except Exception:
        allow()

if not diff:
    allow()

# 추가된 라인만 (+ 시작, 헤더 +++ 제외)
added = "\n".join(
    ln[1:] for ln in diff.splitlines()
    if ln.startswith("+") and not ln.startswith("+++")
)

# 패턴: 흔한 토큰/키 형식. 너무 짧은 hex/base64 는 false positive 많아서 제외.
PATTERNS = [
    # AWS
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
    (r"aws_secret_access_key\s*=\s*['\"]?[A-Za-z0-9/+=]{40}", "AWS Secret"),
    # Github tokens
    (r"ghp_[A-Za-z0-9]{36}", "GitHub PAT (classic)"),
    (r"github_pat_[A-Za-z0-9_]{82}", "GitHub PAT (fine-grained)"),
    # Slack
    (r"xox[baprs]-[0-9A-Za-z-]{10,}", "Slack token"),
    # Google API
    (r"AIza[0-9A-Za-z\-_]{35}", "Google API key"),
    # OpenAI / Anthropic
    (r"sk-[A-Za-z0-9]{20,}", "OpenAI/Anthropic-style API key"),
    (r"sk-ant-[A-Za-z0-9_-]{20,}", "Anthropic API key"),
    # Telegram
    (r"\b\d{9,12}:[A-Za-z0-9_-]{30,40}\b", "Telegram bot token"),
    # Generic high-entropy assignments to suspicious keys
    (r"(?i)(api[_-]?key|secret|password|passwd|token|private[_-]?key)\s*[=:]\s*['\"][^'\"]{20,}['\"]", "Suspicious credential assignment"),
    # PEM / SSH
    (r"-----BEGIN (RSA|OPENSSH|DSA|EC|PGP) PRIVATE KEY-----", "Private key block"),
]

hits = []
for pat, label in PATTERNS:
    for m in re.finditer(pat, added):
        snippet = m.group(0)
        if len(snippet) > 60:
            snippet = snippet[:30] + "..." + snippet[-15:]
        hits.append(f"{label}: {snippet}")
        if len(hits) >= 6:
            break
    if len(hits) >= 6:
        break

if hits:
    deny(
        "secrets-scan-guard 차단: git "
        + ("commit" if "commit" in cmd else "push")
        + " 직전 staged/working diff 에서 자격증명 의심 패턴 발견.\n"
        + "\n".join(f"- {h}" for h in hits)
        + "\n\n조치: 해당 라인을 .env 등 .gitignore 대상으로 옮기고 git history 정리. "
        "정말 publish 의도면 다시 실행 시 BYPASS_SECRETS_SCAN=1 환경변수와 함께 호출. "
        "(쉘 명령에 환경변수 prefix 가능)"
    )

allow()
PYEOF
exit 0
