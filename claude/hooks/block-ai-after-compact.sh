#!/bin/bash
# block-ai-after-compact: PreToolUse 훅. 압축 직후 AI 자발 재호출만 차단.
# - 압축 플래그가 살아 있는 동안, 실행 명령 자리에 선 AI 진입 명령만 검사한다
#   (ai-debate, ai-debate-detach, ai-collaborate, gemini-ask, codex-ask).
#   같은 이름이 경로나 인자로만 나오면(토론 결과 파일을 읽는 명령) 차단하지 않는다.
# - 사용자 최신 메시지에 AI 요청 키워드가 있으면 허용(사용자 명시 요청).
#   그 메시지는 훅 입력의 transcript_path 가 가리킨 기록에서만 읽는다 —
#   작업디렉토리로 기록 파일을 추측하면 남의 세션 기록을 읽는다.
# - 없으면 차단(모델 자발 재호출로 간주). 플래그 5분 지나면 자동 해제.
# - 보는 표식은 자기 세션 것 하나다. 남의 세션이 압축했다고 이 세션의 제출을
#   막지 않는다(경로는 compact-flag-lib.sh).

set -uo pipefail

BLOCK_WINDOW_SEC=300  # 5분 fallback

INPUT=$(cat 2>/dev/null || echo '{}')

# shellcheck source=/dev/null
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/compact-flag-lib.sh" 2>/dev/null || exit 0

if ! SID=$(compact_flag_sid "$INPUT"); then
    compact_flag_nosid block-ai-after-compact
    exit 0
fi

FLAG=$(compact_flag_path "$SID" block)
[ -f "$FLAG" ] || exit 0

# 세션 식별자 관측만 한다(차단 판정은 그대로). 표식이 살아 있는 회차에서만 적히므로
# 평소 Bash 호출에는 아무 비용이 안 붙는다.
"$HOME/.claude/hooks/hook-identity-log.sh" PreToolUse "$INPUT" 2>/dev/null || true

read -r -d '' PYCODE <<'PY'
import json
import os
import re
import shlex
import sys
import time

flag = sys.argv[1]
window = int(sys.argv[2])

# 외부 AI 를 실제로 부르는 진입 명령. 이름이 바뀌면 여기만 고친다.
ENTRY = {
    "ai-debate", "ai-debate-detach",
    "ai-collab", "ai-collaborate",
    "gemini-ask", "codex-ask",
}
# 다음 토큰이 다시 명령 시작 자리가 되는 것들. 기호는 글자 집합으로 본다 —
# 어휘 분석기가 이어진 기호를 한 토큰으로 묶어 내놓기 때문이다(");" 같은 모양).
SEP_WORDS = {"then", "do", "else", "elif", "{", "}", "!"}
SEP_CHARS = set("|&;()")
# 자기 뒤의 토큰을 명령으로 실행하는 래퍼
WRAPPERS = {"env", "nohup", "setsid", "timeout", "stdbuf", "command",
            "exec", "time", "xargs"}
SHELLS = {"bash", "sh", "zsh", "dash"}
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
ALLOW = re.compile(
    "gemini|codex|ai-debate|ai-collab|크로스체크|세컨드 ?오피니언|"
    "ai ?토론|ai ?협업|ai한테|다른 ?ai|ai ?의견|"
    "토론해|토론 ?돌려|토론 ?시켜|토론을 ?돌",
    re.IGNORECASE)


def is_sep(t):
    return t in SEP_WORDS or (bool(t) and all(c in SEP_CHARS for c in t))


def tokenize(cmd):
    """셸 토큰. 연산자는 따로 떼되 따옴표 안은 쪼개지 않는다.

    쪼개 놓고 글자로 `|&;` 를 다시 가르면 따옴표로 보호된 인자까지 갈려서 그 조각이
    명령 시작 자리로 승격된다 — 실제로 grep 패턴 안의 "…|…/ai-debate" 가 명령으로
    읽혀 멀쩡한 조회가 막혔다. 어휘 분석기 안에서 가르면 따옴표를 존중한다.
    """
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def scan(cmd, depth=0):
    """실행 명령 자리에 선 진입 이름만 모은다. 인자·경로로 나온 것은 세지 않는다."""
    if depth > 1:
        return []
    try:
        toks = tokenize(cmd)
    except ValueError:
        toks = cmd.split()

    found = []
    expect = True   # 다음 토큰이 명령 시작 자리인가
    i = 0
    while i < len(toks):
        t = toks[i]
        if is_sep(t):
            expect = True
            i += 1
            continue
        if not expect:
            i += 1
            continue
        if ASSIGN.match(t):      # FOO=bar cmd ... 접두
            i += 1
            continue
        base = os.path.basename(t)
        if base in WRAPPERS:
            i += 1
            continue
        if base in SHELLS:
            for j in range(i + 1, len(toks)):
                if toks[j] == "-c" and j + 1 < len(toks):
                    found.extend(scan(toks[j + 1], depth + 1))
                    break
            expect = False
            i += 1
            continue
        if base in ENTRY:
            found.append(base)
        expect = False
        i += 1
    return found


def last_user_text(path, tail_bytes=512 * 1024):
    """기록 끝에서부터 거슬러 올라가 마지막 사용자 메시지를 찾는다."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            chunk = f.read()
    except OSError:
        return ""
    lines = chunk.split(b"\n")
    if size > tail_bytes and lines:
        lines = lines[1:]        # 중간에서 잘린 첫 줄은 버린다
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        if d.get("type") != "user":
            continue
        msg = d.get("message") or {}
        if msg.get("role") != "user":
            continue
        c = msg.get("content", "")
        if isinstance(c, str):
            return c[:4000]
        if isinstance(c, list):
            s = "".join(x.get("text", "") for x in c
                        if isinstance(x, dict) and x.get("type") == "text")
            if s:
                return s[:4000]
    return ""


def drop_flag():
    try:
        os.unlink(flag)
    except OSError:
        pass


raw = sys.stdin.read()
try:
    data = json.loads(raw) if raw.strip() else {}
except Exception:
    data = {}
if not isinstance(data, dict):
    data = {}

cmd = (data.get("tool_input") or {}).get("command") or ""
if not cmd or not scan(cmd):
    sys.exit(0)

try:
    age = int(time.time() - os.stat(flag).st_mtime)
except OSError:
    sys.exit(0)
if age > window:
    drop_flag()
    sys.exit(0)

tpath = data.get("transcript_path") or ""
msg = last_user_text(tpath) if tpath else ""
if msg and ALLOW.search(msg):
    drop_flag()          # 사용자가 명시로 요청함
    sys.exit(0)

sys.stderr.write(
    "[block-ai-after-compact] 압축 %d초 지남, 사용자 AI 요청 없음 → 차단 "
    "(%d초 후 자동 해제). 압축 요약에 남은 호출 흔적을 다시 돌리지 마라. "
    "지금 필요하면 운영자가 '토론해' 또는 'gemini'·'codex' 가 든 요청을 보내면 통과한다.\n"
    % (age, window - age))
sys.exit(2)
PY

printf '%s' "$INPUT" | python3 -c "$PYCODE" "$FLAG" "$BLOCK_WINDOW_SEC"
exit $?
