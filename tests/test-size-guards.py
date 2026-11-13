#!/usr/bin/env python3
"""큰 입력 가드 둘(large-read-guard · prompt-length-guard)의 분기와 안내 문구.

고정하는 것 셋.

1. 차단·경고 분기가 실제로 돌고, 그 안내가 지금 있는 도구만 가리킨다.
   lean-ctx 는 2026-06-05 에 통째로 지웠는데(R54) 두 가드의 안내 문구가
   ctx_read·ctx_search 를 계속 가리키고 있었다. 세션이 그 안내를 따라가면
   없는 도구를 부르고, 그 자리에서 큰 읽기를 줄일 방법을 못 찾는다.
2. 임계와 종료코드는 그대로다. 문구를 고치면서 정책이 같이 움직이지 않게 한다.
3. 길이 가드는 검사 중에 텔레그램을 쏘지 않는다. 차단 분기에 발송이 들어 있어서
   임시 HOME 으로만 가린다고 믿지 않고, 가짜 curl 로 호출 자체를 센다.

문구 검사는 분기를 실제로 실행해 얻은 출력에만 건다 — 파일을 문자열로 훑는
방식은 분기가 안 도는 경우를 통과시킨다.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "claude" / "hooks"
LARGE = str(HOOKS / "large-read-guard.sh")
PLEN = str(HOOKS / "prompt-length-guard.sh")

DEAD = ("ctx_read", "ctx_search", "lean-ctx")

results = []


def check(name, cond, detail=None):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail!r}" if not cond and detail else ""))


def run(cmd, payload, env=None, cwd=None):
    p = subprocess.run(cmd, input=json.dumps(payload), text=True,
                       capture_output=True, env=env, cwd=cwd, timeout=60)
    return p.returncode, p.stdout, p.stderr


def deny_reason(out):
    try:
        d = json.loads(out)
    except Exception:
        return None
    h = d.get("hookSpecificOutput", {}) or {}
    if h.get("permissionDecision") != "deny":
        return None
    return h.get("permissionDecisionReason", "")


# ---------------------------------------------------------------- large-read
print("=== 큰 파일 읽기 가드 ===")
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    big = base / "big.txt"
    big.write_bytes(b"x" * (6 * 1024 * 1024))          # 임계 5MB 초과
    small = base / "small.txt"
    small.write_bytes(b"x" * 1024)
    lines = base / "many.txt"
    lines.write_bytes(b"a\n" * 20000)                   # 임계 10K 줄 초과
    risky = base / "half.log"
    risky.write_bytes(b"x" * 3 * 1024 * 1024)           # 위험 확장자는 임계 절반

    def ask(path, **ti):
        ti["file_path"] = str(path)
        return run([LARGE], {"tool_name": "Read", "tool_input": ti})

    rc, out, _ = ask(big)
    reason = deny_reason(out)
    check("임계를 넘는 파일은 차단한다", reason is not None, out)
    check("차단해도 종료코드는 0", rc == 0, rc)
    check("안내에 없어진 도구가 없다",
          reason is not None and not any(d in reason for d in DEAD), reason)
    check("안내가 Read 의 부분 읽기를 가리킨다",
          reason is not None and "offset/limit" in reason, reason)
    check("안내가 좁혀 찾는 길도 준다",
          reason is not None and ("Grep" in reason or "rg" in reason), reason)

    rc, out, _ = ask(lines)
    reason = deny_reason(out)
    check("줄 수가 임계를 넘어도 차단한다", reason is not None, out)
    check("줄 수 안내에도 없어진 도구가 없다",
          reason is not None and not any(d in reason for d in DEAD), reason)

    rc, out, _ = ask(risky)
    check("위험 확장자는 임계 절반으로 본다", deny_reason(out) is not None, out)

    rc, out, _ = ask(small)
    check("작은 파일은 통과한다", rc == 0 and deny_reason(out) is None, out)

    rc, out, _ = ask(big, offset=1, limit=100)
    check("구간을 지정했으면 통과한다", deny_reason(out) is None, out)

    rc, out, _ = ask(big.with_name("없는파일.txt"))
    check("없는 파일은 통과한다", deny_reason(out) is None, out)

    rc, out, _ = run([LARGE], {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    check("다른 도구 호출은 건드리지 않는다", deny_reason(out) is None, out)


# ---------------------------------------------------------------- prompt-len
print("\n=== 프롬프트 길이 가드 ===")
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    home = base / "home"
    (home / ".claude").mkdir(parents=True)
    work = base / "work"
    work.mkdir()
    fakebin = base / "bin"
    fakebin.mkdir()
    sent = base / "curl-called"
    (fakebin / "curl").write_text(
        "#!/bin/bash\necho called >> %s\n" % sent, encoding="utf-8")
    (fakebin / "curl").chmod(0o755)

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
    env.pop("CLAUDE_AUTOMATED", None)
    env["CLAUDE_SESSION_NAME"] = "검사"

    # 추정식은 바이트 x 10 / 35. 임계는 경고 600K · 차단 800K 토큰.
    def prompt_of(tokens):
        return "가" * 0 + "x" * (tokens * 35 // 10 + 1024)

    def submit(tokens):
        return run([PLEN], {"prompt": prompt_of(tokens), "cwd": str(work)},
                   env=env, cwd=str(work))

    rc, out, err = submit(100)
    check("작은 프롬프트는 통과한다", rc == 0 and out.strip() == "", (rc, out))

    rc, out, err = submit(650000)
    check("경고선을 넘으면 맥락을 주입한다", rc == 0 and "prompt-length-guard 경고" in out,
          (rc, out[:200]))
    check("경고 안내에 없어진 도구가 없다", not any(d in out for d in DEAD), out[:300])
    check("경고 안내가 Read 의 부분 읽기를 가리킨다", "offset/limit" in out, out[:300])

    rc, out, err = submit(850000)
    check("차단선을 넘으면 종료코드 2 로 막는다", rc == 2, (rc, err[:200]))
    check("차단 안내에 없어진 도구가 없다", not any(d in err for d in DEAD), err[:300])
    check("차단 안내가 Read 의 부분 읽기를 가리킨다", "offset/limit" in err, err[:300])

    check("검사 중에 텔레그램을 쏘지 않았다", not sent.exists(),
          sent.read_text() if sent.exists() else "")

    env2 = dict(env)
    env2["CLAUDE_AUTOMATED"] = "1"
    rc, out, err = run([PLEN], {"prompt": prompt_of(850000), "cwd": str(work)},
                       env=env2, cwd=str(work))
    check("자동 호출은 차단하지 않는다", rc == 0 and out.strip() == "", (rc, out[:120]))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
