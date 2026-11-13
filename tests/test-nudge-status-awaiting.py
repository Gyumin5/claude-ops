"""claude-session-nudge --status 가 끝난 등록을 응답대기로 표시하지 않는지 검증.

대기 창은 등록을 끄거나 종료시각이 지나도 상태 파일에 열린 채로 남는다. 그 값을 그대로
찍으면 멈춘 지 오래인 등록이 "응답대기(10036분째)" 로 보여서, 사람이 그 세션을 지금
응답하지 않는 세션으로 읽는다(2026-10-29 velocity 실측 — enabled=False·종료시각 사흘 전).
대기 표시는 넛지가 실제로 돌고 있는 등록에만 붙는다.

  1) 켜짐 + 기한 남음 + 대기  → 응답대기 표시 있음
  2) 꺼짐 + 대기              → 표시 없음
  3) 켜짐 + 기한 지남 + 대기  → 표시 없음
  4) 켜짐 + 기한 남음 + 대기 아님 → 표시 없음
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
tmp = Path(tempfile.mkdtemp(prefix="tstatus."))
fails = []

state_dir = tmp / "state"
nudge_dir = state_dir / "session-nudge"
nudge_dir.mkdir(parents=True)

# systemctl 을 가짜로 둔다 — 검사가 진짜 유닛 상태를 묻지 않게 한다.
fake_bin = tmp / "bin"
fake_bin.mkdir()
fake_systemctl = fake_bin / "systemctl"
fake_systemctl.write_text("#!/bin/bash\necho inactive\nexit 0\n")
fake_systemctl.chmod(fake_systemctl.stat().st_mode | stat.S_IEXEC)

ENV = dict(os.environ,
           CLAUDE_NUDGE_STATE_DIR=str(state_dir),
           CLAUDE_NUDGE_LOG=str(tmp / "nudge.log"),
           PATH="%s:%s" % (fake_bin, os.environ["PATH"]))

now = int(time.time())
CASES = [
    ("1 켜짐·기한남음·대기", "alive", True, now + 3600, True, True),
    ("2 꺼짐·대기", "stopped", False, now + 3600, True, False),
    ("3 켜짐·기한지남·대기", "expired", True, now - 3600, True, False),
    ("4 켜짐·대기아님", "idle", True, now + 3600, False, False),
]

for _, name, enabled, end_ts, awaiting, _ in CASES:
    (nudge_dir / (name + ".json")).write_text(json.dumps(
        {"session": name, "enabled": enabled, "interval_min": 10,
         "end_ts": end_ts, "awaiting": awaiting,
         "await_since": now - 600, "message": "x"},
        ensure_ascii=False))

out = subprocess.run([NUDGE, "--status"], capture_output=True, text=True,
                     env=ENV).stdout
lines = {l.split(":", 1)[0]: l for l in out.splitlines() if ":" in l}

for label, name, _, _, _, want in CASES:
    line = lines.get(name, "")
    got = "응답대기" in line
    ok = got == want and line
    print("%-22s 응답대기=%s (기대 %s)  %s" % (label, got, want,
                                              "OK" if ok else "FAIL"))
    if not ok:
        fails.append("%s: 응답대기=%s(기대 %s) 줄=%r" % (label, got, want, line))

print()
if fails:
    for f in fails:
        print("FAIL:", f)
    sys.exit(1)
print("PASS — 응답대기 표시는 켜져 있고 기한이 남은 등록에만 붙는다")
