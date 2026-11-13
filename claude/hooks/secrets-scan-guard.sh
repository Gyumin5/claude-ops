#!/bin/bash
# secrets-scan-guard.sh: PreToolUse hook (matcher: Bash).
#
# 목적: git commit / git push 직전 변경분 안 secrets 자동 검사.
# 발견 시 차단(deny). false positive 줄이기 위해 단순 키워드보단 패턴 위주.
#
# 대상 저장소는 ★명령이 가리킨 곳이다. `git -C <경로> commit` 은 작업디렉토리가 아니라
# 그 경로에 커밋한다. 작업디렉토리를 저장소로 가정하면 두 가지로 틀린다 — 엉뚱한
# 저장소의 변경분을 검사하거나(검사한 척), 작업디렉토리가 저장소가 아니어서 git 이
# 알 수 없는 종료코드를 내고 모든 커밋이 막힌다.
#
# 실패 처리 계약:
# - 변경분을 못 읽었으면 통과시키지 않고 막는다. 못 읽은 것과 읽고 깨끗한 것이 바깥에서
#   같아 보이면 검사가 거짓 성공을 낸다. git 은 저장소가 아닌 곳에서도 빈 출력을 주므로
#   종료코드를 반드시 본다. 막을 때는 무엇 때문인지 사람 말로 적는다.
# - 훅 입력 자체가 깨졌을 때만 경고하고 통과한다. 깨진 입력에서 글자로 커밋 여부를
#   되짚어 막지 않는다 — 인용문과 실제 명령을 가를 수 없다.
# - 사유에 자격증명 값·git 오류 원문·입력 원문을 싣지 않는다.

set -uo pipefail

# 코드는 파일 디스크립터 3 으로, 훅 입력 JSON 은 표준입력으로 넘긴다.
# 환경변수로 넘기면 명령이 아주 길 때 exec 단계의 크기 제한에 걸린다.
exec 3<<'PYEOF'
import json, os, sys, subprocess, re, shlex

def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(0)

def allow():
    sys.exit(0)

def warn(reason):
    # 검사를 못 했다는 사실만 알리고 권한 판단은 그대로 둔다 (allow 로 강제 승인하지 않는다).
    emit({
        "systemMessage": f"secrets-scan-guard 검사 미실행: {reason}",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": f"secrets-scan-guard 가 이번 호출을 검사하지 못했다: {reason}",
        },
    })

def deny(reason):
    emit({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    })

def deny_unscanned(step):
    deny(
        f"secrets-scan-guard 차단: {step} — 검사를 못 했으므로 통과시키지 않는다.\n"
        "조치: 저장소 경로와 git 상태를 확인하고 다시 실행. 검사를 건너뛰어야 하면 "
        "BYPASS_SECRETS_SCAN=1 을 명령 앞에 붙인다 (사람이 그 호출을 지시했을 때만)."
    )

try:
    data = json.loads(sys.stdin.read() or "{}")
except Exception:
    warn("훅 입력을 JSON 으로 읽지 못했다")

if not isinstance(data, dict):
    warn("훅 입력이 객체가 아니다")

if data.get("tool_name") not in ("Bash", "bash"):
    allow()

tool_input = data.get("tool_input")
if not isinstance(tool_input, dict):
    warn("tool_input 이 객체가 아니다")

cmd = tool_input.get("command")
if not isinstance(cmd, str):
    warn("command 가 문자열이 아니다")

# 사용자 명시 우회 — 명령 앞머리의 실제 환경변수 대입만 인정한다.
# 인용문·커밋 메시지 안의 같은 글자는 우회가 아니다.
if re.search(r"(?:^|[\n;&|(]\s*)BYPASS_SECRETS_SCAN=1(?=\s)", cmd):
    allow()

cwd = data.get("cwd") or os.getcwd()

# ---- 어떤 git 명령이고 어느 저장소인가 -------------------------------------
# 글자가 아니라 토큰으로 본다. `echo "git commit"` 은 커밋이 아니고
# `git -C <경로> commit` 은 커밋인데, 정규식으로는 둘을 못 가른다.
# 다음 토큰이 다시 명령 시작 자리가 되는 것들. 기호는 글자 집합으로 본다 —
# 어휘 분석기가 이어진 기호를 한 토큰으로 묶어 내놓기 때문이다(");" 같은 모양).
SEP_WORDS = {"then", "do", "else", "elif", "{", "}", "!"}
SEP_CHARS = set("|&;()")
WRAPPERS = {"env", "nohup", "setsid", "timeout", "stdbuf", "command",
            "exec", "time", "xargs"}
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
VALUE_OPTS = {"-c", "--git-dir", "--work-tree", "--namespace",
              "--exec-path", "--config-env"}


def is_sep(t):
    return t in SEP_WORDS or (bool(t) and all(c in SEP_CHARS for c in t))


def tokenize(cmd):
    """셸 토큰. 연산자는 따로 떼되 따옴표 안은 쪼개지 않는다.

    쪼개 놓고 글자로 `|&;` 를 다시 가르면 따옴표로 보호된 인자까지 갈려서 그 조각이
    명령 시작 자리로 승격된다 — 커밋 메시지 안의 세로줄 하나로 멀쩡한 커밋이 막히거나,
    반대로 엉뚱한 조각이 커밋으로 읽힌다. 어휘 분석기 안에서 가르면 따옴표를 존중한다.
    """
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def find_git_write(cmd, cwd, depth=0):
    """(하위명령, 대상 디렉토리, 그 명령의 인자들) 또는 None."""
    if depth > 1:
        return None
    try:
        toks = tokenize(cmd)
    except ValueError:
        # 따옴표가 안 닫힌 명령. 글자로 되짚어 막지 않는다 — 인용문과 실제 명령을
        # 가를 수 없고, 셸도 이 명령을 실행하지 못한다.
        return None

    i, expect = 0, True
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
        if base in ("bash", "sh", "zsh", "dash"):
            for j in range(i + 1, len(toks)):
                if toks[j] == "-c" and j + 1 < len(toks):
                    got = find_git_write(toks[j + 1], cwd, depth + 1)
                    if got:
                        return got
                    break
            expect = False
            i += 1
            continue
        if base != "git":
            expect = False
            i += 1
            continue

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

        if j < len(toks) and toks[j] in ("commit", "push"):
            rest = []
            for k in range(j + 1, len(toks)):
                if is_sep(toks[k]):
                    break
                rest.append(toks[k])
            return (toks[j], os.path.normpath(target), rest)

        expect = False
        i += 1
    return None


found = find_git_write(cmd, cwd)
if not found:
    allow()
sub, target, rest = found

if "--dry-run" in rest:
    allow()


def git(args, step, repo, tolerate_fail=False):
    try:
        p = subprocess.run(
            ["git", "-C", repo] + args,
            capture_output=True, text=True, timeout=15
        )
    except subprocess.TimeoutExpired:
        deny_unscanned(f"{step}가 15초 안에 안 끝났다")
    except Exception:
        deny_unscanned(f"{step}를 실행하지 못했다")
    if p.returncode != 0 and not tolerate_fail:
        # git stderr 원문은 싣지 않는다 — 경로·내용이 섞여 나갈 수 있다.
        deny_unscanned(f"{step}가 실패했다 (git 종료코드 {p.returncode})")
    return p


# 저장소인지 먼저 확인한다. 아니면 diff 가 --no-index 모드로 떨어져 알 수 없는
# 종료코드를 내고, 그 숫자가 사유에 그대로 실린다.
# 시간 초과·실행 불가는 git() 안에서 막는다 — 여기서 예외가 나면 훅 자체가 비정상
# 종료하고, 출력이 없다는 것이 바깥에서는 통과로 읽힌다.
probe = git(["rev-parse", "--show-toplevel"], "저장소 확인", target,
            tolerate_fail=True)
if probe.returncode != 0 or not probe.stdout.strip():
    deny(
        f"secrets-scan-guard 차단: git {sub} 대상 경로가 git 저장소가 아니다 — "
        "변경분을 읽을 수 없어 통과시키지 않는다.\n"
        "조치: 저장소 안에서 실행하거나 git -C 로 저장소 경로를 가리킨다. "
        "검사를 건너뛰어야 하면 BYPASS_SECRETS_SCAN=1 을 명령 앞에 붙인다 "
        "(사람이 그 호출을 지시했을 때만)."
    )
repo = probe.stdout.strip()

# 스테이징분과 작업 트리를 ★둘 다 본다. 예전에는 스테이징분이 비었을 때만 작업 트리를
# 봤는데, `commit -a` 에 스테이징된 변경이 같이 있으면 작업 트리 쪽 비밀을 건너뛴다.
# 새로 추가된 파일은 스테이징에만, 추적 파일의 미스테이징 변경은 작업 트리에만 보인다.
chunks = [git(["diff", "--cached", "-U0"], "스테이징 변경분 조회", repo).stdout]
head = git(["rev-parse", "--verify", "-q", "HEAD"], "저장소 상태 확인", repo,
           tolerate_fail=True)
if head.returncode == 0:
    chunks.append(git(["diff", "HEAD", "-U0"], "작업 트리 변경분 조회", repo).stdout)
diff = "\n".join(c for c in chunks if c)

if not diff:
    # 조회가 정상으로 끝나고 변경분이 비었다 — 깨끗한 작업 트리의 push 등이 여기로 온다.
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

labels = []
for pat, label in PATTERNS:
    if re.search(pat, added) and label not in labels:
        labels.append(label)
    if len(labels) >= 6:
        break

if labels:
    # 찾은 값은 출력하지 않는다 — 사유 자체가 로그·채팅으로 흘러간다.
    deny(
        f"secrets-scan-guard 차단: git {sub} 직전 변경분에서 자격증명 의심 패턴 발견.\n"
        + "\n".join(f"- {l}" for l in labels)
        + "\n\n조치: 해당 라인을 .env 등 .gitignore 대상으로 옮기고 git history 정리. "
        "정말 publish 의도면 사람이 그 호출을 지시했을 때만 BYPASS_SECRETS_SCAN=1 환경변수와 "
        "함께 호출한다. (쉘 명령에 환경변수 prefix 가능)"
    )

allow()
PYEOF

python3 /dev/fd/3
