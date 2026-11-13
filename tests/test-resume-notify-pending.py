#!/usr/bin/env python3
"""재시작 알림의 대기 목록이 '### 완료(최근)' 를 대기로 올리지 않는지 고정한다.

옛 판본은 섹션 종결자가 /^## / 였다. '### 완료(최근)'(R51 이 정한 완료 구역)는
세 번째 글자가 공백이 아니라 # 이라 안 맞아서, Blockers 아래 완료 구역 전체가
대기로 긁혔다. velocity 재시작 알림이 대기 18건을 띄웠고 그중 17건이 완료 항목이다.

겉보기에 정상이라 조용하다 — 알림은 나가고 목록도 그럴듯하다. 끝난 일이 매 재시작마다
할 일로 돌아오는 것만 사람이 눈으로 걸러야 한다.

고정하는 것 넷.
  1. 완료 구역 줄은 대기에 안 들어간다.
  2. Blockers 의 번호형·대시형 항목은 둘 다 들어간다.
  3. 줄 앞머리 ✅ 는 여전히 빠진다.
  4. 다음 ## 제목에서도 섹션이 닫힌다.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "claude" / "hooks" / "session-resume-notify.sh"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"\n      {detail}" if not cond and detail else ""))


src = HOOK.read_text(encoding="utf-8")
m = re.search(r"PENDING=\$\(awk '(.+?)' \"\$PROG\"", src, re.S)
check("훅에서 대기 추출 awk 를 찾는다", m is not None)
if not m:
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(1)
awk_prog = m.group(1)

FIXTURE = """# progress.md
updated: 2026-01-01 00:00 KST
status: active
task: 고정용 가짜 파일

## Current State
- 이건 Blockers 가 아니라 안 잡혀야 한다

## Blockers
1. 대기 하나
2. 대기 둘
- 대시형 대기 셋
- ✅ 이미 끝난 것
3. ✅ 번호형으로 끝난 것

### 완료(최근)
- 끝난 일 가 (10-08·abc1234)
- 끝난 일 나 (10-07·def5678)
- 끝난 일 다 (10-06·0123abc)

## Do Not Repeat
- 이것도 안 잡혀야 한다
"""

with tempfile.TemporaryDirectory() as td:
    prog = Path(td) / "progress.md"
    prog.write_text(FIXTURE, encoding="utf-8")
    raw = subprocess.run(["awk", awk_prog, str(prog)],
                         capture_output=True, text=True).stdout.splitlines()
    kept = [l for l in raw if not re.match(r"^(- |[0-9]+\. )✅", l)]

check("완료 구역 줄이 대기에 없다",
      not any("끝난 일" in l for l in kept),
      f"샌 줄: {[l for l in kept if '끝난 일' in l]}")
check("번호형 대기가 들어간다", "1. 대기 하나" in kept and "2. 대기 둘" in kept, f"{kept}")
check("대시형 대기가 들어간다", "- 대시형 대기 셋" in kept, f"{kept}")
check("앞머리 ✅ 는 빠진다", not any("끝난 것" in l for l in kept), f"{kept}")
check("다른 절(Current State·Do Not Repeat)은 안 들어간다",
      not any("안 잡혀야 한다" in l for l in kept), f"{kept}")
check("대기는 정확히 셋이다", len(kept) == 3, f"{len(kept)}건: {kept}")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
