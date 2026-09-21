#!/usr/bin/env python3
"""claude-mcp-recover --clear 계약 테스트.

왜 있나: 2026-08-25 이전에는 알림마커(~/.claude/state/mcp-alerted) 제거를 증분 프롬프트가
Bash 로 직접 했다. `.claude` 경로를 건드리는 삭제라 매 tick 승인창이 떠서 대화가 잠겼다.
제거를 이 헬퍼 안으로 옮겼으므로, 누가 나중에 이 줄을 지우면 프롬프트는 rm 을 안 하는데
마커도 안 지워져서 "이미 알렸다" 상태가 영원히 남는다 — 다음 만료 국면에서 사람이
아무 알림도 못 받는다. 조용히 깨지는 종류라 테스트로 고정한다.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import datetime

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "claude-mcp-recover")
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("  PASS " if cond else "  FAIL ") + name + (("  — " + str(detail)) if detail and not cond else ""))


def env(tmp, machine="peer", alerted=True, active=True, count=2):
    os.makedirs(os.path.join(tmp, ".config"), exist_ok=True)
    state = os.path.join(tmp, ".claude", "state")
    os.makedirs(state, exist_ok=True)
    with open(os.path.join(tmp, ".config", "claude-machine-id"), "w") as f:
        f.write(machine + "\n")
    today = datetime.date.today().isoformat()
    with open(os.path.join(state, "mcp-incident.json"), "w") as f:
        json.dump({"active": active, "day": today, "count": count}, f)
    if alerted:
        open(os.path.join(state, "mcp-alerted"), "w").close()
    return state


def run(tmp, *args):
    e = dict(os.environ, HOME=tmp)
    return subprocess.run(["bash", SCRIPT, *args], env=e, capture_output=True, text=True)


def case(fn):
    tmp = tempfile.mkdtemp(prefix="mcprec.")
    try:
        fn(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    def t1(tmp):
        state = env(tmp)
        r = run(tmp, "--clear")
        check("마커를 지운다", not os.path.exists(os.path.join(state, "mcp-alerted")), r.stdout)
        check("지웠다고 출력에 남긴다", "알림마커 지움" in r.stdout, r.stdout)
        d = json.load(open(os.path.join(state, "mcp-incident.json")))
        check("래치는 해제", d["active"] is False, d)
        check("count 는 보존", d["count"] == 2, d)
    case(t1)

    def t2(tmp):
        env(tmp, alerted=False)
        r = run(tmp, "--clear")
        check("마커가 없어도 성공한다", r.returncode == 0 and "알림마커 없음" in r.stdout,
              str((r.returncode, r.stdout)))
    case(t2)

    def t3(tmp):
        state = env(tmp, machine="home")
        r = run(tmp, "--clear")
        check("peer 이 아니면 아무것도 안 한다",
              "WRONG_MACHINE" in r.stdout and os.path.exists(os.path.join(state, "mcp-alerted")),
              r.stdout)
    case(t3)

    def t4(tmp):
        state = env(tmp)
        r = run(tmp, "--status")
        check("status 는 마커를 지우지 않는다",
              os.path.exists(os.path.join(state, "mcp-alerted")) and "count=2" in r.stdout, r.stdout)
    case(t4)

    ok = all(results)
    print("\n%d/%d PASS" % (sum(results), len(results)))
    print("ALL PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
