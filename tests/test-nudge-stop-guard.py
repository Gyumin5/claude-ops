"""claude-session-nudge --stop 이 실수로 전체를 끄지 않는지 검증.

인자 없는 --stop 은 전에 등록 전체를 조용히 껐다. 오타 한 번으로 그 호스트의 넛지가
통째로 사라지는 경로이고, 그 상태는 아무 알림도 안 난다 — 점검기(claude-nudge-audit)는
살아 있는 등록만 보기 때문이다. 전체를 끌 때는 --all 로 그 뜻을 적게 한다.

  1) 인자 없음        → rc=2, 어떤 등록도 안 꺼진다
  2) --sessions 문법  → rc=2 (--start 문법을 옮긴 것, 2026-07-31 제보)
  3) 모르는 세션      → rc=3 (오타를 성공으로 넘기지 않는다)
  4) 세션 하나 지정   → rc=0, 그 세션만 꺼진다
  5) --all            → rc=0, 남은 등록이 전부 꺼진다
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NUDGE = str(REPO / "bin" / "claude-session-nudge")
tmp = Path(tempfile.mkdtemp(prefix="tstop."))
fails = []

state_dir = tmp / "state"
nudge_dir = state_dir / "session-nudge"
nudge_dir.mkdir(parents=True)

# systemctl 을 가짜로 둔다 — 검사가 진짜 유닛을 건드리면 돌고 있는 넛지를 끈다.
fake_bin = tmp / "bin"
fake_bin.mkdir()
fake_systemctl = fake_bin / "systemctl"
fake_systemctl.write_text("#!/bin/bash\nexit 0\n")
fake_systemctl.chmod(fake_systemctl.stat().st_mode | stat.S_IEXEC)

ENV = dict(os.environ,
           CLAUDE_NUDGE_STATE_DIR=str(state_dir),
           CLAUDE_NUDGE_LOG=str(tmp / "nudge.log"),
           PATH="%s:%s" % (fake_bin, os.environ["PATH"]))

SESSIONS = ["alpha", "beta"]


def reset():
    for s in SESSIONS:
        (nudge_dir / (s + ".json")).write_text(json.dumps(
            {"session": s, "enabled": True, "interval_min": 10,
             "end_ts": int(time.time()) + 3600, "message": "x"},
            ensure_ascii=False))


def enabled():
    return {s: json.loads((nudge_dir / (s + ".json")).read_text()).get("enabled")
            for s in SESSIONS}


def run(*args):
    return subprocess.run([NUDGE, "--stop", *args], capture_output=True,
                          text=True, env=ENV).returncode


def check(label, got, want_rc, want_enabled):
    ok = got == want_rc and enabled() == want_enabled
    print("%-24s rc=%s (기대 %s) 등록=%s  %s"
          % (label, got, want_rc, enabled(), "OK" if ok else "FAIL"))
    if not ok:
        fails.append("%s: rc=%s(기대 %s) 등록=%s(기대 %s)"
                     % (label, got, want_rc, enabled(), want_enabled))


reset()
check("1 인자 없음", run(), 2, {"alpha": True, "beta": True})
check("2 --sessions 문법", run("--sessions", "alpha"), 2,
      {"alpha": True, "beta": True})
check("3 모르는 세션", run("gamma"), 3, {"alpha": True, "beta": True})
check("4 세션 하나", run("alpha"), 0, {"alpha": False, "beta": True})
check("5 전체", run("--all"), 0, {"alpha": False, "beta": False})

print()
if fails:
    for f in fails:
        print("FAIL:", f)
    sys.exit(1)
print("PASS — 인자 없는 중지는 아무것도 안 끄고 rc=2 로 거절하며, 전체 중지는 --all "
      "에서만 돌고, 세션 하나 지정은 그 세션만 끈다")
