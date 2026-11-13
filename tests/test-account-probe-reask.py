#!/usr/bin/env python3
"""bin/claude-accounts 의 probe_one 이 묻는 말을 한 번만 넣고 포기하지 않는지 본다.

막은 사고: 2026-11-09 둘째 기계에서 공용 계정 넷이 전부 빈손으로 돌아왔다. 계정은
멀쩡했고, 세션이 뜨면서 띄운 새 모델 안내창이 첫 회차의 글자를 먹은 것이었다.
밤 전환은 그걸 "공용이 다 막혔다" 로 읽어 안 갈아 끼운 채 사람에게 알렸다.

보는 것은 둘이다. 첫 글자를 먹히면 다시 넣어 받아 오나, 그리고 이미 들어간 회차에는
다시 안 넣나(같은 계정을 두 번 쓰면 재기만으로 한도가 깎인다).
"""

import importlib.util
import os
import sys
import tempfile
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "bin" / "claude-accounts"

fails = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("  — " + str(detail) if detail else ""))
    if not cond:
        fails.append(name)


def load():
    spec = importlib.util.spec_from_loader("accounts", SourceFileLoader("accounts", str(TOOL)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 가짜 claude. 들어온 줄을 세어 자취로 남기고, 정해진 줄 수에 닿으면 그 홈의 수치
# 파일에 한도 칸을 적는다. eat=1 이면 첫 줄을 먹는 안내창 흉내다.
FAKE = '''#!/usr/bin/env python3
import json, os, sys, time
home = os.environ["HOME"]
cache = os.path.join(home, ".claude", "statusline_cache.json")
trace = os.environ["PROBE_TRACE"]
eat = int(os.environ["PROBE_EAT"])
touch = os.environ.get("PROBE_TOUCH") == "1"
os.makedirs(os.path.dirname(cache), exist_ok=True)
# 세션이 서자마자 상태줄이 한 번 그린다 — 한도 칸은 아직 없다.
json.dump({"cost": {"total_api_duration_ms": 0}}, open(cache, "w"))
seen = 0
while True:
    line = sys.stdin.readline()
    if not line:
        time.sleep(0.05)
        continue
    if not line.strip():
        continue
    seen += 1
    open(trace, "a").write(line.strip() + "\\n")
    if seen <= eat:
        continue
    if touch:
        json.dump({"cost": {"total_api_duration_ms": 1200}}, open(cache, "w"))
        time.sleep(30)
        continue
    json.dump({"cost": {"total_api_duration_ms": 1200},
               "rate_limits": {"five_hour": {"used_percentage": 7},
                               "seven_day": {"used_percentage": 11}}},
              open(cache, "w"))
    time.sleep(30)
'''


def run(eat, touch=False, timeout=12.0):
    """(상태, 넣은 횟수). eat 만큼 먹히는 가짜 세션으로 한 번 재 본다."""
    mod = load()
    mod.RE_ASK_GAP = 1.0          # 검사에서까지 15초를 기다릴 이유가 없다
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        home = base / "acct"
        (home / ".claude").mkdir(parents=True)
        fake = base / "fake-claude"
        fake.write_text(FAKE, encoding="utf-8")
        fake.chmod(0o755)
        trace = base / "trace"
        os.environ["PROBE_TRACE"] = str(trace)
        os.environ["PROBE_EAT"] = str(eat)
        os.environ["PROBE_TOUCH"] = "1" if touch else "0"
        try:
            state, _secs, _lim = mod.probe_one(
                home, str(fake), timeout=timeout, ask="hi", ask_after=0.5, model=None)
        finally:
            for k in ("PROBE_TRACE", "PROBE_EAT", "PROBE_TOUCH"):
                os.environ.pop(k, None)
        asked = len(trace.read_text(encoding="utf-8").splitlines()) if trace.exists() else 0
        return state, asked


print("=== 첫 글자를 먹히는 회차 ===")
t0 = time.time()
state, asked = run(eat=0)
check("안 먹히면 한 번에 받는다", state == "받았다" and asked == 1, f"{state!r} 넣음={asked}")

state, asked = run(eat=1)
check("첫 글자를 먹혀도 다시 넣어 받는다", state == "받았다", f"{state!r} 넣음={asked}")
check("그 회차는 두 번 넣는다", asked == 2, f"넣음={asked}")

print("=== 이미 들어간 회차 ===")
# 말은 들어갔는데(모델을 부른 시간이 0 보다 크다) 수치가 늦게 오는 경우다. 여기서
# 또 넣으면 같은 계정을 두 번 쓴다 — 재기만으로 한도가 깎인다.
state, asked = run(eat=0, touch=True, timeout=6.0)
check("말이 들어갔으면 다시 안 넣는다", asked == 1, f"{state!r} 넣음={asked}")
check("수치가 안 오면 그렇다고 적는다", state.startswith("수치가 안 왔다"), repr(state))

print("=== 상수 ===")
mod = load()
check("넣는 횟수는 셋을 안 넘는다", mod.ASK_TRIES == 3, mod.ASK_TRIES)
check("다시 넣기까지 충분히 기다린다", mod.RE_ASK_GAP >= 10, mod.RE_ASK_GAP)
check("한 회차가 길어지지 않는다", time.time() - t0 < 120, f"{time.time() - t0:.0f}초")

print()
print("실패 {}건".format(len(fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
