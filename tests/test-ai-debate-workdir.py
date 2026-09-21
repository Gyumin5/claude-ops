#!/usr/bin/env python3
"""토론 참여자의 시작 작업 디렉토리와 브리프 경로 경고 계약 검사.

고정하는 것 둘.

첫째, 참여자 셋을 다른 방식으로 두지 않는다(2026-09-15 운영자 지시). 전에는 codex 만
전용 --cd 를 받고, claude 는 cwd="/tmp", gemini 은 아무것도 없이 세션의 작업 디렉토리를
상속했다. 그래서 제미나이만 원본 트리 안에서 시작했고 상대경로로 움직이면 원본에 닿았다.
이제 셋 다 자기 호출의 사본에서 시작한다. 사본이 없는 회차는 넘길 디렉토리가 없으므로
옛 동작을 유지한다 — 없는 경로를 주면 프로세스가 시작도 못 한다.

둘째, 브리프에 절대경로가 없으면 경고 한 줄을 남긴다. 사본은 브리프에서 찾은 경로로만
뜨므로, 경로가 없으면 세 모델이 파일을 한 줄도 못 읽고 브리프 글만 읽고 답한다
(2026-09-15 옆 기계 run-20260915T045627Z). 막지는 않는다 — 순수 정책 토론은 경로가
없는 게 정상이라 차단하면 오탐이다.
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


mod = load("ai_debate_mod", REPO / "bin" / "ai-debate")


class FakeProc:
    """agent 호출 한 번. JSON 한 줄만 돌려주면 파서가 통과한다."""
    returncode = 0
    stdout = '{"position": "좋다", "should_proceed": true}'
    stderr = ""


calls = []


def fake_run(cmd, **kw):
    calls.append({"cmd": list(cmd), "cwd": kw.get("cwd", "_없음")})
    return FakeProc()


# mod.subprocess 는 진짜 subprocess 모듈이라 여기서 갈아끼우면 이 검사 자신의 호출까지
# 가짜가 된다. 아래 CLI 검사 전에 원래 것으로 되돌린다.
real_run = subprocess.run
mod.subprocess.run = fake_run
mod.sync_iso_credentials = lambda: (True, "")

AGENTS = ["codex", "gemini", "claude-fable"]

with tempfile.TemporaryDirectory() as tmp:
    run_dir = Path(tmp) / "run-20260915T000000Z"
    run_dir.mkdir(parents=True)

    # 사본이 있는 회차 — 셋 다 그 사본에서 시작해야 한다.
    got = {}
    for agent in AGENTS:
        label = f"r1.debater.{agent}"
        snap = run_dir / "snapshot" / label
        snap.mkdir(parents=True)
        calls.clear()
        mod._call_agent_once(agent, "debater", "브리프 본문", run_dir, label)
        got[agent] = (calls[-1]["cwd"], calls[-1]["cmd"])
        check(f"{agent}: 사본에서 시작한다", calls[-1]["cwd"] == str(snap),
              f"실제 {calls[-1]['cwd']}")
    check("셋의 시작 위치를 정하는 방식이 같다",
          len({("사본" if c == str(run_dir / "snapshot" / f"r1.debater.{a}") else c)
               for a, (c, _) in got.items()}) == 1,
          str({a: c for a, (c, _) in got.items()}))
    # 동작상 상속만으로 충분하지만(0d49f73 관문), --cd 는 절대경로·존재 검사로
    # 잘못된 위치를 호출 전에 끊어주므로 유지다. 그 계약이 살아 있는지를 본다.
    check("codex 는 fail-fast 관문용으로 --cd 도 같이 받는다",
          "--cd" in got["codex"][1]
          and got["codex"][1][got["codex"][1].index("--cd") + 1] == got["codex"][0],
          str(got["codex"][1]))

    # 사본이 없는 회차 — 옛 동작 유지. 없는 경로를 주면 프로세스가 시작도 못 한다.
    for agent, want in [("codex", None), ("gemini", None), ("claude-fable", "/tmp")]:
        calls.clear()
        mod._call_agent_once(agent, "debater", "브리프 본문", run_dir, f"nosnap.{agent}")
        check(f"{agent}: 사본이 없으면 옛 동작", calls[-1]["cwd"] == want,
              f"실제 {calls[-1]['cwd']}")
    calls.clear()
    mod._call_agent_once("codex", "debater", "브리프 본문", run_dir, "nosnap2.codex")
    check("사본이 없으면 codex 에 --cd 를 안 준다", "--cd" not in calls[-1]["cmd"],
          str(calls[-1]["cmd"]))

# 브리프 경로 경고. 실제 실행 경로로 본다 — dry-run 은 유료 호출을 0회 쓴다.
# 브리프에 난수를 섞는 이유는 캐시다. 같은 문장을 24시간 안에 다시 물으면 옛 회차가
# 그대로 돌아와서, 이 검사가 만들지도 않은 폴더를 보게 된다.
subprocess.run = real_run
MARK = "브리프에 절대경로가 없다"


def dry_run(brief):
    """회차 하나를 돌리고 (결과, 회차 폴더) 를 준다. 폴더는 곧바로 확인하고 지운다.

    회차 이름은 초 단위라 두 회차가 같은 초에 끝나면 폴더를 공유한다. 그래서 다음
    회차를 돌리기 전에 자기 것을 보고 치운다 — 몰아서 보면 앞 회차의 표식을 뒷 회차
    것으로 읽는다.
    """
    r = real_run([sys.executable, str(REPO / "bin" / "ai-debate"), "--dry-run", brief],
                 capture_output=True, text=True, env=dict(os.environ), timeout=120)
    d = None
    for line in r.stdout.splitlines():
        if line.startswith("run_dir:"):
            d = Path(line.split(":", 1)[1].strip())
    return r, d


def drop(d):
    for f in sorted(d.rglob("*"), reverse=True):
        f.rmdir() if f.is_dir() else f.unlink()
    d.rmdir()


# 브리프에 난수를 섞는 이유는 캐시다. 같은 문장을 24시간 안에 다시 물으면 옛 회차가
# 그대로 돌아와서, 이 검사가 만들지도 않은 폴더를 보게 된다.
nonce = os.urandom(6).hex()

r1, d1 = dry_run(f"경로 없는 정책 토론 {nonce}")
check("경로 없는 브리프는 경고한다", MARK in r1.stderr, r1.stderr[-200:])
check("경고해도 막지 않는다", r1.returncode == 0, f"rc={r1.returncode}")
if d1 is not None and d1.is_dir():
    check("경고를 회차 기록에도 남긴다", (d1 / "no-brief-path.txt").is_file(), str(d1))
    drop(d1)
else:
    check("경고를 회차 기록에도 남긴다", False, f"회차 폴더를 못 찾았다: {d1}")

r2, d2 = dry_run(f"이 파일을 봐라 {REPO / 'bin' / 'ai-debate'} {nonce}")
check("경로 있는 브리프는 조용하다", MARK not in r2.stderr, r2.stderr[-200:])
if d2 is not None and d2.is_dir():
    check("경로가 있으면 기록에 표식이 없다",
          not (d2 / "no-brief-path.txt").is_file(), str(d2))
    drop(d2)
else:
    check("경로가 있으면 기록에 표식이 없다", False, f"회차 폴더를 못 찾았다: {d2}")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
