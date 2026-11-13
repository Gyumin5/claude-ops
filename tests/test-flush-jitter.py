#!/usr/bin/env python3
"""큐 깨우기를 세션마다 무작위 초로 흩는 자리를 고정한다.

왜 흩나: 계정을 갈아 끼우면 그 회차에 막혀 있던 세션이 한꺼번에 깨우기를 받는다.
전부 같은 초에 들어가면 열몇 세션이 동시에 깨어나 같은 계정을 동시에 두드린다.

왜 첫 발송은 안 흩나: 혼자뿐인 회차가 대부분이다. 거기에 대기를 넣으면 흩을 것도
없는데 재개만 늦어진다.

왜 총 상한이 있나: 이 유닛은 10분 타이머가 부른다. 세션당 상한만 두면 세션이 늘수록
한 회차가 길어져 다음 발화와 겹친다.

재는 법: 실제로 자지 않는다 — sheell 의 sleep 을 받아 적는 함수로 덮고 그 값만 본다.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "bin" / "rate-limit-recovery"

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


check("복구기가 있다", SRC.exists(), str(SRC))
if not SRC.exists():
    print("\n0/1 passed")
    sys.exit(1)

body = SRC.read_text(encoding="utf-8")
m = re.search(r"^flush_jitter\(\) \{.*?^\}", body, re.S | re.M)
check("flush_jitter 함수가 있다", m is not None)
if not m:
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(1)
func = m.group(0)


def run(sent, slept, max_sec=20, budget=120, seed=1, rounds=1):
    """(쉰 초 목록, 마지막 누적). 실제로 자지 않는다."""
    script = (
        "set -u\n"
        'RECOVERY_LOG=/dev/null\n'
        f"FLUSH_JITTER_MAX={max_sec}\n"
        f"FLUSH_JITTER_BUDGET={budget}\n"
        f"FLUSH_SENT={sent}\n"
        f"FLUSH_SLEPT={slept}\n"
        f"RANDOM={seed}\n"
        'sleep() { echo "SLEPT $1"; }\n'
        f"{func}\n"
        f"for _i in $(seq 1 {rounds}); do flush_jitter; done\n"
        'echo "TOTAL $FLUSH_SLEPT"\n'
    )
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=30)
    slept_list, total = [], None
    for line in p.stdout.splitlines():
        if line.startswith("SLEPT "):
            slept_list.append(int(line.split()[1]))
        elif line.startswith("TOTAL "):
            total = int(line.split()[1])
    return slept_list, total, p.stderr.strip()


got, total, err = run(sent=0, slept=0)
check("첫 발송은 안 쉰다", got == [] and total == 0, f"{got} {total} {err}")

got, total, err = run(sent=1, slept=0, rounds=30)
check("둘째부터는 쉰다", len(got) > 0, f"{got} {err}")
check("세션당 상한을 안 넘는다", all(0 < s <= 20 for s in got), repr(got))
check("누적이 쉰 합과 같다", total == sum(got), f"{total} vs {sum(got)}")

# 0 이 나오는 회차는 그냥 안 쉰다 — sleep 0 을 부르지 않는다.
check("0 초는 부르지 않는다", 0 not in got, repr(got))

got, total, err = run(sent=1, slept=0, rounds=200)
check("총 상한에 닿으면 더 안 쉰다", total <= 120 + 20 and total >= 120,
      f"total={total} 마지막까지 쉬면 상한을 훌쩍 넘는다")

got, total, err = run(sent=1, slept=500, budget=120)
check("이미 상한을 넘겼으면 안 쉰다", got == [] and total == 500, f"{got} {total}")

got, total, err = run(sent=1, slept=0, max_sec=0, rounds=10)
check("세션당 상한이 0 이면 아무도 안 쉰다 — 끄는 길이 있다",
      got == [] and total == 0, f"{got} {total}")

# 설정으로 바꿀 수 있어야 한다. 기계마다 세션 수가 달라서 상한이 하나로 안 맞는다.
check("두 상한을 환경변수로 받는다",
      "CLAUDE_FLUSH_JITTER_MAX" in body and "CLAUDE_FLUSH_JITTER_BUDGET" in body)

# 흩는 자리가 실제 발송 바로 앞이어야 한다. 뒤에 있으면 이미 다 나간 뒤에 쉰다.
seq = re.search(r"flush_jitter\n\s+FLUSH_SENT=.*?\n\s+if trigger_flush", body, re.S)
check("발송 바로 앞에서 흩고 보낸 수를 센다", seq is not None,
      "flush_jitter → FLUSH_SENT 증가 → trigger_flush 순서가 아니다")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
