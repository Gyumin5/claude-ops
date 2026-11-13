#!/usr/bin/env python3
"""콜 예산이 라운드 확장 뒤에도 지켜지는지 고정한다.

시작 전 예산 검사는 기본 라운드만 보고 통과시킨다(`--deep` 이면 3). 그런데 깊은 회차는
미합의일 때 5라운드까지 한 번 늘어나고, 그 확장 시점에는 아무것도 다시 안 셌다. 그래서
역할 넷·풀 넷짜리 회차가 예상 50 으로 통과한 뒤 실제로는 82 콜까지 갔다 — 적어 둔 예산이
실제 상한이 아니라 시작 전 예상일 뿐이었다(2026-10-01 운영자 결정으로 실제 상한으로 바꿈).

고정하는 것 셋.

  1. 확장 뒤 콜 수를 세는 식이 시작 전 검사와 같은 단위다(planner + 역할 출력 + 남은
     라운드 × 역할 × 살아있는 provider + arbiter 1).
  2. 죽어서 빠진 provider 의 콜은 안 센다. 그 콜은 앞으로 안 일어난다.
  3. 예산을 넘으면 확장하지 않는다 — 호출 자리가 그 비교를 실제로 하고,
     넘을 때 effective_max 를 안 올린다.
"""
import importlib.machinery
import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEBATE = REPO / "bin" / "ai-debate"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


# ai-debate 는 확장자가 없어 보통 import 로는 안 올라온다. 모듈 최상단은 상수·정규식뿐이고
# main() 은 __main__ 가드 안이라 올려도 아무 일도 안 일어난다.
loader = importlib.machinery.SourceFileLoader("ai_debate_mod", str(DEBATE))
spec = importlib.util.spec_from_loader("ai_debate_mod", loader)
assert spec is not None
mod = importlib.util.module_from_spec(spec)
sys.modules["ai_debate_mod"] = mod
loader.exec_module(mod)
f = mod.escalation_projected_calls

# --- 1. 실제로 터졌던 숫자 ---------------------------------------------------
# 깊은 회차: planner 1, 역할 4, 풀 4, 3라운드를 돌아 출력 48개, 5라운드까지 2라운드 남음.
# 1 + 48 + 2×4×4 + 1 = 82. 적어 둔 예산은 50 이었다.
check("확장 뒤 콜 수가 실측대로 82 다", f(1, 48, 2, 4, 4) == 82, f"{f(1, 48, 2, 4, 4)}")

# --- 2. 시작 전 검사와 같은 단위 ---------------------------------------------
# 1라운드도 안 돌았다고 치면(출력 0, 남은 라운드 = 전체) 시작 전 식과 같아야 한다.
for n_roles in (2, 4):
    for rounds in (3, 5):
        pre = 1 + n_roles * 4 * rounds + 1
        check(f"시작 전 식과 같다 (역할 {n_roles}, {rounds}라운드)",
              f(1, 0, rounds, n_roles, 4) == pre, f"{f(1, 0, rounds, n_roles, 4)} != {pre}")

# --- 3. 죽은 provider 는 안 센다 ---------------------------------------------
check("provider 하나가 빠지면 남은 라운드 몫이 줄어든다",
      f(1, 48, 2, 4, 3) == 1 + 48 + 2 * 4 * 3 + 1,
      "살아있는 수로 곱해야 안 할 콜을 예산에 안 넣는다")
check("빠진 쪽이 더 싸다", f(1, 48, 2, 4, 3) < f(1, 48, 2, 4, 4))

# --- 4. planner 를 안 불렀으면 안 센다 ---------------------------------------
check("planner 0 이면 하나 적다", f(0, 48, 2, 4, 4) == 81)

# --- 원문 확인: 호출 자리가 실제로 비교하고 안 올린다 -------------------------
src = DEBATE.read_text(encoding="utf-8")
m = re.search(r"if \(args\.deep and not escalated.*?escalation\.txt\"\)\.write_text\(\s*\n\s*f\"escalated",
              src, re.S)
block = m.group(0) if m else ""
check("원문: 확장 자리가 escalation_projected_calls 로 센다",
      "escalation_projected_calls(" in block, block[:600])
check("원문: 예산을 넘는지 비교한다",
      "projected > args.max_total_calls" in block, block[:600])
check("원문: 넘으면 effective_max 를 안 올린다 — 상향이 else 갈래에 있다",
      re.search(r"else:\s*\n\s*effective_max = deep_escalate_max", block) is not None,
      block[-700:])
check("원문: 살아있는 provider 만 센다",
      "a for a in POOL if a not in skip_agents" in block, block[:600])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
