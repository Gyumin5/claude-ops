#!/usr/bin/env python3
"""코덱스 전용 작업위치 인자(--cd)가 필요한지를 가르는 관문.

토론 도구는 참여자를 부를 때 프로세스 작업 디렉토리를 사본 뿌리로 두고, 코덱스에만
--cd 로 같은 값을 한 번 더 준다. 같은 값이라 실회차 기록으로는 어느 쪽이 실제로 쓰였는지
구분이 안 된다 — 원본 파일 해시가 그대로라는 것도 근거가 못 된다. 읽었는지를 못 가리기
때문이다.

그래서 둘을 일부러 어긋나게 둔다. 같은 이름 다른 값의 표식 파일을 디렉토리 두 곳에 두고
상대경로로 읽게 하면, 읽어온 값이 어느 디렉토리에서 돌았는지를 말해 준다.

  갈래 1  작업 디렉토리 A, 인자 없음   → A 값이 나오면 코덱스가 상속한 위치를 쓴다
  갈래 2  작업 디렉토리 B, 인자 없음   → 갈래 1 과 값이 달라야 상속이 진짜다
  갈래 3  작업 디렉토리 A, 인자로 B    → B 값이 나오면 인자가 상속을 덮는다

판정은 셋을 합쳐야 선다. 1 과 2 가 갈리면 상속만으로 충분하다는 뜻이고, 그때 3 이 B 면
인자는 같은 일을 두 번 하는 것이다. 1 과 2 가 안 갈리면 상속이 안 먹는다는 뜻이라
인자를 빼면 안 된다.

실호출이 필요해서 기본 실행에서는 안 돈다. 돌리려면 --live 를 준다(최대 6콜).
인자 없이 돌리면 구조 검사만 한다 — 그쪽은 호출 없이 확인되는 계약이다.
"""
import importlib.machinery
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIVE = "--live" in sys.argv[1:]
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


# ── 1. 구조 — 호출 없이 확인되는 계약 ──────────────────────────────────
ask = (REPO / "bin" / "codex-ask").read_text(encoding="utf-8")
check("인자를 안 주면 코덱스에 위치 옵션을 안 넘긴다",
      'cd_args=()' in ask and '[ -n "$CD_DIR" ] && cd_args=(-C "$CD_DIR")' in ask)
check("인자는 절대경로만 받고 없는 디렉토리는 거절한다",
      "--cd 는 절대경로여야 한다" in ask and "--cd 디렉토리가 없다" in ask)

mod = load("ai_debate_cdgate", REPO / "bin" / "ai-debate")
real_run = subprocess.run
calls = []


class FakeProc:
    returncode = 0
    stdout = '{"position": "좋다", "reasoning": "근거"}'
    stderr = ""


def fake_run(cmd, **kw):
    calls.append({"cmd": list(cmd), "cwd": kw.get("cwd", "_없음")})
    return FakeProc()


mod.subprocess.run = fake_run
try:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run-20260915T000000Z"
        (run_dir / "snapshot" / "r1.debater.codex").mkdir(parents=True)
        mod.set_brief_paths("경로 없는 브리프")
        mod._call_agent_once("codex", "debater", "브리프", run_dir, "r1.debater.codex")
        c = calls[-1]
        snap = str(run_dir / "snapshot" / "r1.debater.codex")
        check("토론은 코덱스에 작업 디렉토리와 인자를 같은 값으로 준다",
              c["cwd"] == snap and "--cd" in c["cmd"]
              and c["cmd"][c["cmd"].index("--cd") + 1] == snap, str(c))
        check("같은 값이라 실회차 기록만으로는 어느 쪽이 쓰였는지 못 가른다",
              c["cwd"] == c["cmd"][c["cmd"].index("--cd") + 1])
finally:
    mod.subprocess.run = real_run


# ── 2. 실호출 갈래 ─────────────────────────────────────────────────────
def ask_marker(cwd, cd_dir):
    """그 위치에서 상대경로로 표식을 읽게 하고 값과 코덱스가 말한 위치를 돌려준다."""
    cmd = ["codex-ask", "--new"]
    if cd_dir:
        cmd += ["--cd", cd_dir]
    cmd.append("marker.txt 를 상대경로 그대로 읽어라. 출력은 두 줄만: "
               "첫 줄 MARKER=<파일 안의 값>, 둘째 줄 PWD=<네가 실제로 일한 디렉토리 절대경로>. "
               "못 읽으면 MARKER=READ_FAILED 로 적어라.")
    p = subprocess.run(cmd, input="", capture_output=True, text=True, timeout=600,
                       cwd=cwd, env={**os.environ, "AI_OUT_RAW": "1"})
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    m = re.search(r"MARKER=([A-Za-z0-9_-]+)", out)
    w = re.search(r"PWD=(\S+)", out)
    return (m.group(1) if m else "없음"), (w.group(1) if w else "없음"), out


if not LIVE:
    print("\n(실호출 갈래는 건너뛴다 — 돌리려면 --live)")
else:
    tag = uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        a = Path(tmp) / "alpha"
        b = Path(tmp) / "bravo"
        a.mkdir()
        b.mkdir()
        (a / "marker.txt").write_text(f"ALPHA_{tag}\n", encoding="utf-8")
        (b / "marker.txt").write_text(f"BRAVO_{tag}\n", encoding="utf-8")

        m1, w1, _ = ask_marker(str(a), None)
        m2, w2, _ = ask_marker(str(b), None)
        m3, w3, _ = ask_marker(str(a), str(b))
        print(f"\n갈래1 작업디렉토리=alpha 인자없음   → MARKER={m1} PWD={w1}")
        print(f"갈래2 작업디렉토리=bravo 인자없음   → MARKER={m2} PWD={w2}")
        print(f"갈래3 작업디렉토리=alpha 인자=bravo → MARKER={m3} PWD={w3}")

        inherits = (m1 == f"ALPHA_{tag}" and m2 == f"BRAVO_{tag}")
        check("코덱스는 상속한 작업 디렉토리를 쓴다", inherits, f"{m1} / {m2}")
        check("인자는 상속한 위치를 덮는다", m3 == f"BRAVO_{tag}", m3)

        print("\n[판정]")
        if inherits and m3 == f"BRAVO_{tag}":
            print("  상속만으로 충분하다. 인자는 같은 값을 두 번 주는 것이라 빼도 동작이 안 바뀐다.")
        elif not inherits:
            print("  상속이 안 먹는다. 인자를 빼면 코덱스가 사본 밖에서 시작한다 — 유지해야 한다.")
        else:
            print("  인자가 상속을 못 덮는다. 둘이 어긋나는 회차가 생기면 사본 밖에서 돈다 — 조사 필요.")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
