#!/usr/bin/env python3
"""사용자가 직접 적은 콜 예산을 깊은 회차가 덮어쓰지 않는지 고정한다.

깊은 회차는 예산을 24 에서 80 으로 올린다. 그 상향이 `max(args.max_total_calls, 50)`
한 줄이었는데, 인자 기본값이 24 로 박혀 있어서 사용자가 적은 값인지 안 적어서 24 인지를
가릴 수 없었다. 그래서 `--deep --max-total-calls 30` 이 말없이 그 상향값으로 돌았다 —
상한을 낮추려는 의도가 통째로 사라진다(2026-10-01 실측 확인, 회차 비교 보고서
reports/2026-10-01-코덱스-두-모델-짝지어-재기.html).

고정하는 것 넷.

  1. 아무것도 안 적으면 평소 24.
  2. 아무것도 안 적고 --deep 이면 80.
  3. 직접 적으면 그 값 그대로. 깊은 회차라도 안 올라간다.
  4. 적은 값보다 예상 콜이 많으면 시작 전에 거절한다(종료코드 3).

--dry-run 은 예산 검사 뒤에 서고 AI 를 한 번도 안 부른다. 그래서 이 검사는 실제 호출
없이 돈다.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEBATE = REPO / "bin" / "ai-debate"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


def run(extra):
    """--dry-run 으로 돌려 (종료코드, stdout, stderr) 반환.

    회차 폴더 경로는 모듈 상수라 바깥에서 못 바꾼다. 그래서 만들어진 폴더를 출력에서
    읽어 그 자리에서 지운다 — 안 지우면 검사를 돌릴 때마다 빈 회차가 쌓인다.
    """
    p = subprocess.run(
        [sys.executable, str(DEBATE), "예산 검사용 질문", "--dry-run", "--no-cache"] + extra,
        capture_output=True, text=True, env=dict(os.environ), timeout=120,
    )
    m = re.search(r"^run_dir:\s*(\S+)", p.stdout, re.M)
    if m:
        d = Path(m.group(1))
        if d.is_dir() and "ai-debate" in str(d):
            shutil.rmtree(d, ignore_errors=True)
    return p.returncode, p.stdout, p.stderr


def budget_of(out):
    m = re.search(r"estimated_calls:\s*(\d+)\s*\(max\s*(\d+)\)", out)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


# --- 1. 평소 기본값 ---------------------------------------------------------
rc, out, err = run([])
est, mx = budget_of(out)
check("평소: 인자를 안 적으면 예산 24", mx == 24, f"rc={rc} max={mx}\n{out}{err}")

# --- 2. 깊은 회차 기본값 ----------------------------------------------------
rc, out, err = run(["--deep"])
est_deep, mx = budget_of(out)
check("깊게: 인자를 안 적으면 예산 80", mx == 80, f"rc={rc} max={mx}\n{out}{err}")

# --- 3. 직접 적은 값은 그대로 ----------------------------------------------
# 깊은 회차의 예상 콜(역할 둘 × 풀 다섯 × 3라운드 + 1 = 31)보다 크게 잡아 거절을 피한다.
rc, out, err = run(["--deep", "--max-total-calls", "40"])
est2, mx = budget_of(out)
check("깊게: 직접 적은 40 이 65 로 안 올라간다", mx == 40,
      f"rc={rc} max={mx}\n{out}{err}")
check("깊게: 예상 콜은 그대로다", est2 == est_deep,
      f"{est2} != {est_deep} — 예산만 바뀌어야 하는데 계산까지 달라졌다")

# --- 4. 적은 값보다 예상이 크면 거절 ----------------------------------------
rc, out, err = run(["--deep", "--max-total-calls", "5"])
check("깊게: 적은 값보다 예상이 크면 종료코드 3", rc == 3, f"rc={rc}\n{out}{err}")
check("깊게: 거절 사유에 적은 값이 그대로 나온다",
      "max-total-calls 5" in err, err[-400:])

# --- 원문 확인 -------------------------------------------------------------------
src = DEBATE.read_text(encoding="utf-8")
check("원문: 인자 기본값이 None 이다",
      'ap.add_argument("--max-total-calls", type=int, default=None' in src,
      "24 로 박아 두면 사용자가 적은 24 와 안 적은 24 를 가릴 수 없다")
check("원문: 상향이 max() 가 아니라 미지정일 때만이다",
      "max(args.max_total_calls, 65)" not in src
      and "if args.max_total_calls is None:" in src,
      "max() 로 올리면 사용자가 적은 낮은 값을 덮는다")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
