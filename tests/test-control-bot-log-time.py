#!/usr/bin/env python3
"""컨트롤봇 로그의 모든 줄에 시각이 붙는다.

왜 고정하나: 시각이 없으면 "버튼을 눌렀는데 반응이 없다" 와 로그의 전송 실패를
맞댈 방법이 없다. 2026-11-11 에 운영자가 그 증상을 물었을 때 7,965줄짜리 로그로
어느 실패가 언제인지 한 줄도 못 댔다.

왜 지역 시각인가: 운영자가 보는 다른 기록이 전부 KST 라, 다른 시계로 적으면
맞대는 그 순간 아홉 시간이 어긋난다(R07 과 같은 자리).
"""
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BOT = REPO / "bin" / "claude-control-bot"

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"\n      {detail}" if not cond and detail else ""))


check("봇이 있다", BOT.exists(), str(BOT))
if not BOT.exists():
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(1)

src = BOT.read_text(encoding="utf-8")

m = re.search(r"^def log\(msg\):.*?^    print\(.*?\n", src, re.S | re.M)
check("시각을 붙이는 log 함수가 있다", m is not None)
if not m:
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(1)
func = m.group(0)

# 로그로 나가는 줄이 log() 를 거치지 않고 바로 찍히면 그 줄만 시각이 없다.
# 괄호 안의 print 는 log 정의 자신 하나뿐이어야 한다.
check("로그 줄이 log 를 거치지 않고 찍히는 자리가 없다",
      src.count("print(") == 1,
      f"print 호출 {src.count('print(')}개 — log 정의 하나만 남아야 한다")

check("찍는 자리가 실제로 여러 곳이다", src.count("log(f\"") >= 10,
      f"log(f\" {src.count('log(f')}곳 — 바꾸다 만 상태일 수 있다")

# 실제로 돌려 모양을 본다. 모듈을 통째로 불러오면 설정 파일을 찾다가 죽으므로
# 함수 본문만 떼어 돌린다.
ns = {"time": time}
exec(func, ns)
out = subprocess.run(
    [sys.executable, "-c",
     "import time\n" + func + "\nlog('[시험] 한 줄')"],
    capture_output=True, text=True, timeout=20)
line = out.stdout.strip()
check("시각이 줄 맨 앞에 붙는다",
      re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[시험\] 한 줄$", line) is not None,
      repr(line) + out.stderr)

# 지역 시각인지는 지금 시각과 맞대어 본다. UTC 로 찍으면 KST 와 9시간 어긋난다.
if line[:19]:
    try:
        got = time.mktime(time.strptime(line[:19], "%Y-%m-%d %H:%M:%S"))
        check("지역 시각으로 찍는다 — 지금과 1분 안이다",
              abs(got - time.time()) < 60,
              f"차이 {abs(got - time.time()):.0f}초 — UTC 로 찍으면 32400 초가 난다")
    except ValueError as e:
        check("지역 시각으로 찍는다 — 지금과 1분 안이다", False, str(e))

check("출력을 바로 흘린다 — 로그가 버퍼에 남아 사라지지 않는다",
      "flush=True" in func)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
