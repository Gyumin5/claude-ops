#!/usr/bin/env python3
"""커밋 뒤처리 훅(git-commit-post.sh).

고정하는 것 셋.

1. 대상 저장소는 명령이 가리킨 곳이다. `git -C <경로> commit` 은 세션 작업디렉토리가
   아니라 그 경로에 커밋하는데, 훅이 작업디렉토리를 저장소로 가정하면 엉뚱한 저장소의
   HEAD 를 읽어 남의 커밋을 결정 기록에 적는다. 하위 디렉토리에서 커밋해도 루트를 찾는다.
2. 결정 신호는 둘 다다 — 제목의 [DECISION]·DECISION:, 본문의 `근거:` 줄. 전역 규칙이
   "마커 또는 근거 줄" 로 정의하는데 훅은 마커만 봤다. 그래서 `근거:` 로만 적은 커밋은
   스텁이 안 쌓였고, 세션은 "훅이 빠뜨렸다" 가 아니라 "기록할 게 없었다" 로 읽게 된다.
3. 제목과 본문은 개행으로 이어 본다. 붙여 쓰면 본문 첫 줄이 제목 뒤에 이어져
   행 시작 앵커(^DECISION:·^근거:)가 영영 안 걸린다.

커밋 명령 판정은 문자열이 아니라 토큰으로 한다 — `echo "git commit"` 은 커밋이 아니고
`git -C <경로> commit` 은 커밋인데, 정규식으로는 둘을 못 가른다.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = str(Path(__file__).resolve().parent.parent / "claude" / "hooks"
           / "git-commit-post.sh")

results = []


def check(name, cond, detail=None):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail!r}" if not cond and detail else ""))


def git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], text=True,
                          capture_output=True, env=env, timeout=60)


def make_repo(path, home):
    path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    # `git init -b` 는 2.28 부터다. 이 기계는 2.25.1 이라 기본 브랜치 이름을 그대로 둔다.
    subprocess.run(["git", "init", "-q", str(path)],
                   check=True, env=env, timeout=60, capture_output=True)
    git(path, "config", "user.email", "t@example.invalid", env=env)
    git(path, "config", "user.name", "검사", env=env)
    git(path, "config", "core.hooksPath", str(path / ".nohooks"), env=env)
    return env


_seq = [0]


def commit(repo, message, home):
    env = dict(os.environ)
    env["HOME"] = str(home)
    _seq[0] += 1
    f = Path(repo) / "f.txt"
    f.write_text(f"{_seq[0]}\n", encoding="utf-8")   # 매번 달라야 커밋이 선다
    git(repo, "add", "f.txt", env=env)
    r = git(repo, "commit", "-q", "-m", message, env=env)
    assert r.returncode == 0, r.stderr
    return git(repo, "rev-parse", "--short", "HEAD", env=env).stdout.strip()


def fire(cmd, cwd, home, session="검사세션", exit_code=0):
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd},
               "cwd": str(cwd), "tool_response": {"exit_code": exit_code},
               "session_id": ""}
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("CLAUDE_SESSION_NAME", None)
    if session is not None:
        env["CLAUDE_SESSION_NAME"] = session
    p = subprocess.run([HOOK], input=json.dumps(payload), text=True,
                       capture_output=True, env=env, timeout=60)
    return p.returncode, p.stdout, p.stderr


def stub(home, session="검사세션"):
    f = Path(home) / ".claude" / "state" / "history-stubs" / f"{session}.md"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def clear_stub(home, session="검사세션"):
    f = Path(home) / ".claude" / "state" / "history-stubs" / f"{session}.md"
    if f.exists():
        f.unlink()


with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    home = base / "home"
    (home / ".claude" / "state").mkdir(parents=True)
    repo = base / "repo"
    other = base / "other"
    make_repo(repo, home)
    make_repo(other, home)

    print("=== 결정 신호를 둘 다 본다 ===")
    h = commit(repo, "[DECISION] 마커로 적은 결정", home)
    fire("git commit -m x", repo, home)
    check("제목의 [DECISION] 을 잡는다", f"hash: {h}" in stub(home), stub(home)[-200:])
    clear_stub(home)

    h = commit(repo, "근거 줄로만 적은 변경\n\n근거: 낡은 안내가 없는 도구를 가리켰다", home)
    fire("git commit -m x", repo, home)
    s = stub(home)
    check("본문의 근거 줄만 있어도 잡는다", f"hash: {h}" in s, s[-200:])
    check("근거 줄을 근거로 적는다", "낡은 안내가" in s, s[-200:])
    clear_stub(home)

    h = commit(repo, "제목만 있는 변경\n\nDECISION: 본문 첫 줄에 적은 결정", home)
    fire("git commit -m x", repo, home)
    check("본문 첫 줄의 DECISION: 을 잡는다", f"hash: {h}" in stub(home),
          stub(home)[-200:])
    clear_stub(home)

    commit(repo, "오타 고침", home)
    fire("git commit -m x", repo, home)
    check("결정 신호가 없으면 스텁을 안 만든다", stub(home) == "", stub(home)[-200:])
    clear_stub(home)

    print("\n=== 대상 저장소는 명령이 가리킨 곳 ===")
    h_other = commit(other, "[DECISION] 저쪽 저장소의 결정", home)
    commit(repo, "이쪽 저장소의 평범한 변경", home)
    fire(f"git -C {other} commit -m x", repo, home)
    s = stub(home)
    check("`git -C <경로>` 는 그 경로의 저장소를 읽는다", f"hash: {h_other}" in s,
          s[-200:])
    clear_stub(home)

    h = commit(repo, "[DECISION] 하위에서 커밋한 결정", home)
    sub = repo / "sub" / "deep"
    sub.mkdir(parents=True, exist_ok=True)
    fire("git commit -m x", sub, home)
    check("하위 디렉토리에서 커밋해도 루트를 찾는다", f"hash: {h}" in stub(home),
          stub(home)[-200:])
    check("스텁에 적는 저장소는 루트다", f"- repo: {repo}" in stub(home),
          stub(home)[-200:])
    clear_stub(home)

    h = commit(other, "[DECISION] 붙여 쓴 -C 로 커밋", home)
    fire(f"git -C{other} commit -m x", repo, home)
    check("`-C경로` 붙여 쓴 꼴도 읽는다", f"hash: {h}" in stub(home),
          stub(home)[-200:])
    clear_stub(home)

    h = commit(other, "[DECISION] 설정을 끼운 커밋", home)
    fire(f"git -c user.email=a@b.invalid -C {other} commit -m x", repo, home)
    check("`-c` 설정이 끼어도 대상을 읽는다", f"hash: {h}" in stub(home),
          stub(home)[-200:])
    clear_stub(home)

    print("\n=== 커밋이 아닌 것은 건드리지 않는다 ===")
    commit(repo, "[DECISION] 이 해시는 안 쌓여야 한다", home)
    for label, cmd, kw in [
        ("글자로만 쓴 커밋", 'echo "git commit -m x"', {}),
        ("dry-run", "git commit --dry-run -m x", {}),
        ("상태 조회", "git status --short", {}),
        ("로그 조회", "git log --oneline -1", {}),
        ("실패한 커밋", "git commit -m x", {"exit_code": 1}),
        # 쪼개 놓고 글자로 `|&;` 를 다시 가르면 따옴표로 보호된 인자가 갈려서
        # 그 조각이 명령 시작 자리로 승격된다. 인용문 속 커밋이 진짜로 읽힌다.
        ("따옴표 안의 세로줄과 커밋", 'echo "a|git commit -m x"', {}),
        ("따옴표 안의 세로줄이 든 검색", "grep -n 'a|git' /dev/null", {}),
    ]:
        clear_stub(home)
        fire(cmd, repo, home, **kw)
        check(f"스텁 없음: {label}", stub(home) == "", stub(home)[-160:])

    clear_stub(home)
    fire("git commit -m x && rsync --dry-run a b", repo, home)
    check("뒤에 붙은 다른 명령의 --dry-run 에 끌려가지 않는다", stub(home) != "",
          stub(home)[-160:])
    clear_stub(home)

    h = commit(repo, "[DECISION] 세로줄이 든 메시지로 커밋한다", home)
    fire('git commit -m "쟀다|고쳤다"', repo, home)
    check("커밋 메시지에 세로줄이 있어도 커밋으로 본다", f"hash: {h}" in stub(home),
          stub(home)[-200:])
    clear_stub(home)

    h = commit(repo, "[DECISION] 명령 치환 안에서 커밋한다", home)
    fire("x=$(git commit -m x); echo done", repo, home)
    check("명령 치환 안의 커밋도 잡는다", f"hash: {h}" in stub(home),
          stub(home)[-200:])
    clear_stub(home)

    print("\n=== 중복과 세션 ===")
    h = commit(repo, "[DECISION] 한 번만 쌓여야 하는 결정", home)
    fire("git commit -m x", repo, home)
    fire("git log --oneline -1", repo, home)
    fire("git commit -m x", repo, home)
    check("같은 해시는 한 번만 쌓인다", stub(home).count(f"hash: {h}") == 1,
          stub(home))
    clear_stub(home)

    commit(repo, "[DECISION] 세션을 모를 때", home)
    fire("git commit -m x", repo, home, session=None)
    check("세션을 특정 못 하면 아무 데도 안 쓴다",
          not any((home / ".claude" / "state" / "history-stubs").glob("*.md")),
          list((home / ".claude" / "state" / "history-stubs").glob("*.md")))

    print("\n=== 저장소가 아닌 자리 ===")
    plain = base / "plain"
    plain.mkdir()
    rc, _, _ = fire("git commit -m x", plain, home)
    check("저장소가 아니면 조용히 끝낸다", rc == 0, rc)
    log = home / ".claude" / "logs" / "git-commit-post.log"
    check("그 사실을 로그로 남긴다",
          log.exists() and "저장소 아님" in log.read_text(encoding="utf-8"),
          log.read_text(encoding="utf-8")[-200:] if log.exists() else "없음")

    alive = home / ".claude" / "logs" / "git-commit-post-alive.tsv"
    check("생존 카운터에 대상 저장소가 적힌다",
          alive.exists() and str(repo) in alive.read_text(encoding="utf-8"),
          alive.read_text(encoding="utf-8")[-300:] if alive.exists() else "없음")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
