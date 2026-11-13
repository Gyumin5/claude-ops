"""secrets-scan-guard 가 검사 실패를 통과로 끝내지 않는지 검증.

이 훅은 git commit·push 직전에 변경분을 읽어 자격증명 패턴을 찾는다. 전에는 변경분을
못 읽어도 통과를 반환해서, 못 읽은 것과 읽고 깨끗한 것이 바깥에서 같아 보였다 — git 은
저장소가 아닌 곳에서도 빈 출력을 주므로 종료코드를 안 보면 둘이 구분되지 않는다.

가르는 선 셋:
  - 훅 입력 자체가 깨진 것은 경고하고 통과한다. 깨진 입력에서 글자로 커밋인지 되짚어
    막지 않는다 — 인용문과 실제 명령을 가를 수 없다.
  - 변경분 조회가 실패하면(비정상 종료·실행 불가·시간 초과) 막는다.
  - 조회가 정상으로 끝나고 변경분이 비면 통과한다. 깨끗한 작업 트리의 push 가 여기다.

같이 고정하는 것 셋:
  - 대상 저장소는 명령이 가리킨 곳이다. `git -C <경로> commit` 은 그 경로에 커밋하므로
    세션 작업디렉토리를 저장소로 가정하면 엉뚱한 저장소를 검사하거나, 작업디렉토리가
    저장소가 아니라는 이유만으로 멀쩡한 커밋이 통째로 막힌다.
  - 커밋 여부는 글자가 아니라 토큰으로 가른다. `echo "git commit"` 은 커밋이 아니고
    `git -C <경로> commit` 은 커밋인데, 정규식으로는 둘을 못 가른다.
  - 스테이징분과 작업 트리를 둘 다 본다. 스테이징분이 비었을 때만 작업 트리를 보면
    `commit -a` 에 스테이징된 변경이 같이 있을 때 작업 트리 쪽 비밀을 건너뛴다.

합성 비밀은 글자를 이어 붙여 만든다 — 이 파일 자체가 그 패턴을 담으면 이 검사를 커밋할
때 훅이 막는다.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = str(REPO / "claude" / "hooks" / "secrets-scan-guard.sh")
tmp = Path(tempfile.mkdtemp(prefix="tsecrets."))
fails = []

# 합성 비밀 — 패턴 글자가 이 파일에 그대로 안 남게 이어 붙인다.
FAKE_AWS = "AK" + "IA" + "Q" * 16
FAKE_PEM = "-----BEGIN " + "RSA" + " PRIVATE KEY-----"


def run(payload, env=None, text=None):
    """훅을 돌리고 (rc, 표준출력) 을 준다."""
    data = text if text is not None else json.dumps(payload)
    p = subprocess.run(["bash", HOOK], input=data, capture_output=True,
                       text=True, env=env or os.environ.copy())
    return p.returncode, p.stdout


def decision(out):
    """훅 출력에서 권한 판단을 뽑는다. 출력이 없으면 통과다."""
    if not out.strip():
        return "allow"
    try:
        o = json.loads(out)
    except Exception:
        return "PARSE_FAIL"
    hs = o.get("hookSpecificOutput") or {}
    if hs.get("permissionDecision"):
        return hs["permissionDecision"]
    if o.get("systemMessage"):
        return "warn"
    return "allow"


def check(label, got, want, extra_ok=True, note=""):
    ok = got == want and extra_ok
    print("%-34s %-5s (기대 %-5s) %s %s"
          % (label, got, want, "OK" if ok else "FAIL", note))
    if not ok:
        fails.append("%s: %s (기대 %s) %s" % (label, got, want, note))


def fake_git(script):
    """PATH 앞에 둘 가짜 git 을 만들고 그 환경을 준다."""
    d = Path(tempfile.mkdtemp(prefix="fakegit.", dir=tmp))
    g = d / "git"
    g.write_text(script)
    g.chmod(g.stat().st_mode | stat.S_IEXEC)
    return dict(os.environ, PATH="%s:%s" % (d, os.environ["PATH"]))


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True)


def new_repo(name):
    r = tmp / name
    r.mkdir()
    git(r, "init", "-q")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    return r


def bash_call(cmd, cwd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(cwd)}


# ── 검사 대상이 아닌 호출 ────────────────────────────────────────────────
rc, out = run({"tool_name": "Read", "tool_input": {"file_path": "/x"}})
check("1 비대상 도구", decision(out), "allow", rc == 0)

rc, out = run(bash_call("ls -al", tmp))
check("2 일반 셸", decision(out), "allow", rc == 0)

# ── 훅 입력이 깨진 경우 — 경고하고 통과 ──────────────────────────────────
rc, out = run(None, text="not json at all")
check("3 깨진 JSON", decision(out), "warn", rc == 0)

rc, out = run(None, text='["배열"]')
check("4 입력이 객체가 아님", decision(out), "warn", rc == 0)

rc, out = run({"tool_name": "Bash", "tool_input": "문자열", "cwd": str(tmp)})
check("5 tool_input 이 객체가 아님", decision(out), "warn", rc == 0)

rc, out = run({"tool_name": "Bash", "tool_input": {"command": 7}, "cwd": str(tmp)})
check("6 command 가 문자열이 아님", decision(out), "warn", rc == 0)

# 깨진 입력 안에 git commit 글자가 있어도 막지 않는다 — 인용문과 명령을 못 가른다.
rc, out = run(None, text='{"tool_name":"Bash","tool_input":{"command":"git commit -m \\"')
check("7 깨진 입력 속 커밋 글자", decision(out), "warn", rc == 0)

# ── 변경분 조회 실패 — 막는다 ────────────────────────────────────────────
rc, out = run(bash_call("git commit -m x", tmp))
check("8 저장소 아닌 곳 커밋", decision(out), "deny", rc == 0)

env = fake_git('#!/bin/bash\necho "fatal: 못 읽음" >&2\nexit 128\n')
rc, out = run(bash_call("git commit -m x", tmp), env=env)
no_stderr = "못 읽음" not in out
check("9 git 비정상 종료", decision(out), "deny", no_stderr,
      "git 오류 원문 미포함" if no_stderr else "git stderr 가 사유에 샜다")

env = fake_git('#!/bin/bash\nexit 1\n')
rc, out = run(bash_call("git push", tmp), env=env)
check("10 푸시도 같은 처분", decision(out), "deny", rc == 0)

# 실패한 staged 조회를 HEAD 조회로 대체하지 않는다.
env = fake_git(
    '#!/bin/bash\n'
    'for a in "$@"; do [ "$a" = "--cached" ] && exit 128; done\n'
    'echo "+%s"\n' % "깨끗함"
)
rc, out = run(bash_call("git commit -a -m x", tmp), env=env)
check("11 staged 실패를 HEAD 로 대체 안 함", decision(out), "deny", rc == 0)

env = fake_git('#!/bin/bash\nsleep 20\n')
rc, out = run(bash_call("git commit -m x", tmp), env=env)
check("12 조회 시간 초과", decision(out), "deny", rc == 0)

# git 만 없는 PATH — bash·python3 는 있어야 훅 자체가 돈다.
nogit = tmp / "nogit"
nogit.mkdir()
for exe in ("bash", "python3"):
    (nogit / exe).symlink_to(subprocess.run(["which", exe], capture_output=True,
                                            text=True).stdout.strip())
env = dict(os.environ, PATH=str(nogit))
rc, out = run(bash_call("git commit -m x", tmp), env=env)
check("13 git 실행 불가", decision(out), "deny", rc == 0)

# ── 정상 경로 ───────────────────────────────────────────────────────────
empty = new_repo("empty")
rc, out = run(bash_call("git commit -m x", empty))
check("14 최초 커밋 전 빈 저장소", decision(out), "allow", rc == 0)

clean = new_repo("clean")
(clean / "a.txt").write_text("안녕\n")
git(clean, "add", "a.txt")
git(clean, "commit", "-q", "-m", "first")
rc, out = run(bash_call("git push", clean))
check("15 깨끗한 작업 트리 푸시", decision(out), "allow", rc == 0)

# ── 실제로 비밀이 있을 때 ────────────────────────────────────────────────
staged = new_repo("staged")
(staged / "k.txt").write_text("key = %s\n" % FAKE_AWS)
git(staged, "add", "k.txt")
rc, out = run(bash_call("git commit -m x", staged))
leaked = FAKE_AWS in out
check("16 staged 합성 비밀", decision(out), "deny", not leaked,
      "값 미노출" if not leaked else "자격증명 값이 사유에 샜다")

unstaged = new_repo("unstaged")
(unstaged / "a.txt").write_text("안녕\n")
git(unstaged, "add", "a.txt")
git(unstaged, "commit", "-q", "-m", "first")
(unstaged / "a.txt").write_text("안녕\n%s\n" % FAKE_PEM)
rc, out = run(bash_call("git commit -a -m x", unstaged))
check("17 commit -a 의 작업 트리 비밀", decision(out), "deny", rc == 0)

# ── 우회 ────────────────────────────────────────────────────────────────
rc, out = run(bash_call("BYPASS_SECRETS_SCAN=1 git commit -m x", staged))
check("18 진짜 환경변수 우회", decision(out), "allow", rc == 0)

rc, out = run(bash_call('git commit -m "BYPASS_SECRETS_SCAN=1 라고 적음"', staged))
check("19 인용문 속 우회 글자", decision(out), "deny", rc == 0)

# ── 대상 저장소는 명령이 가리킨 곳 ──────────────────────────────────────
# cwd 쪽은 깨끗하고 -C 가 가리킨 쪽에 비밀이 있다. 작업디렉토리를 저장소로
# 가정하면 이 커밋이 통과한다.
rc, out = run(bash_call("git -C %s commit -m x" % staged, clean))
check("20 git -C 가 그 저장소를 본다", decision(out), "deny", rc == 0)

# 반대 방향 — 비밀은 cwd 쪽에 있고 커밋은 깨끗한 저장소에 한다.
rc, out = run(bash_call("git -C %s commit -m x" % clean, staged))
check("21 cwd 쪽 비밀에 끌리지 않는다", decision(out), "allow", rc == 0)

# 작업디렉토리가 저장소가 아니어도 -C 로 가리킨 저장소를 읽는다. 전에는 cwd 로
# diff 를 불러 git 2.25 가 --no-index 모드로 떨어지며 모든 커밋이 막혔다.
outside = tmp / "notrepo"
outside.mkdir()
rc, out = run(bash_call("git -C %s commit -m x" % clean, outside))
check("22 저장소 밖에서 -C 커밋", decision(out), "allow", rc == 0)

rc, out = run(bash_call("git -C%s commit -m x" % staged, clean))
check("23 -C경로 붙여 쓴 꼴", decision(out), "deny", rc == 0)

rc, out = run(bash_call(
    "git -c user.email=t@example.invalid -C %s commit -m x" % staged, clean))
check("24 -c 설정이 끼어도 대상을 읽는다", decision(out), "deny", rc == 0)

# 저장소가 아니면 왜 막혔는지 사람 말로 적는다 — 전에는 git 종료코드 숫자만 나왔다.
rc, out = run(bash_call("git commit -m x", outside))
said = "저장소가 아니다" in out
check("25 저장소 아님을 사유에 적는다", decision(out), "deny", said,
      "사유 명확" if said else "사유에 저장소 아님이 없다")

# ── 커밋인지 토큰으로 가른다 ────────────────────────────────────────────
for n, cmd in [("26 글자로만 쓴 커밋", 'echo "git commit -m x"'),
               ("27 상태 조회", "git status --short"),
               ("28 dry-run", "git commit --dry-run -m x")]:
    rc, out = run(bash_call(cmd, staged))
    check(n, decision(out), "allow", rc == 0)

rc, out = run(bash_call("bash -c 'git commit -m x'", staged))
check("29 bash -c 안의 커밋", decision(out), "deny", rc == 0)

rc, out = run(bash_call("cat /dev/null && git commit -m x", staged))
check("30 연산자 뒤의 커밋", decision(out), "deny", rc == 0)

# ── 따옴표 안은 쪼개지 않는다 ───────────────────────────────────────────
# 쪼개 놓고 글자로 `|&;` 를 다시 가르면 따옴표로 보호된 인자가 갈려서 그 조각이
# 명령 시작 자리로 승격된다. 인용문 속 커밋이 진짜로 읽히고, 반대로 세로줄이
# 든 커밋 메시지가 엉뚱한 자리를 커밋으로 만든다.
rc, out = run(bash_call('echo "a|git commit -m x"', staged))
check("32 따옴표 안의 세로줄과 커밋", decision(out), "allow", rc == 0)

rc, out = run(bash_call("grep -n 'a|git' /dev/null", staged))
check("33 따옴표 안의 세로줄이 든 검색", decision(out), "allow", rc == 0)

rc, out = run(bash_call('git commit -m "쟀다|고쳤다"', staged))
check("34 커밋 메시지에 세로줄이 있어도 커밋으로 본다",
      decision(out), "deny", rc == 0)

rc, out = run(bash_call("x=$(git commit -m x); echo done", staged))
check("35 명령 치환 안의 커밋", decision(out), "deny", rc == 0)

# ── 스테이징분과 작업 트리를 둘 다 본다 ──────────────────────────────────
# 스테이징에는 평범한 변경, 작업 트리에는 비밀. 전에는 스테이징이 비었을 때만
# 작업 트리를 봐서 이 조합이 통째로 빠져나갔다.
both = new_repo("both")
(both / "a.txt").write_text("안녕\n")
(both / "b.txt").write_text("안녕\n")
git(both, "add", "a.txt", "b.txt")
git(both, "commit", "-q", "-m", "first")
(both / "a.txt").write_text("안녕 고침\n")   # 스테이징분 — 평범한 변경
git(both, "add", "a.txt")
(both / "b.txt").write_text("key = %s\n" % FAKE_PEM)   # 작업 트리에만 — 비밀
rc, out = run(bash_call("git commit -a -m x", both))
check("31 스테이징분과 작업 트리 동시", decision(out), "deny", rc == 0)

print()
if fails:
    for f in fails:
        print("FAIL:", f)
    sys.exit(1)
print("PASS — 변경분을 못 읽으면 막고, 입력이 깨졌을 때만 경고 후 통과하며, 조회가 "
      "정상으로 끝나고 비었을 때만 통과한다. 차단 사유에 자격증명 값도 git 오류 원문도 "
      "안 싣는다")
