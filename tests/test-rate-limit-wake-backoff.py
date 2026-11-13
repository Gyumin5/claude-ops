#!/usr/bin/env python3
"""한도 차단 구간에서 세션을 헛되이 깨우는 고리를 막는 두 자리를 본다.

복구 장치에는 되풀이를 줄이는 장치가 원래 있었다 — 같은 큐가 그대로면 10분, 20분,
40분, 60분으로 간격을 늘린다. 그런데 "같은 큐인가" 를 파일 크기와 수정 시각으로
판별했다. 재개 신호(autoresume-<프로젝트>)는 도구가 막힐 때마다 새 시각으로 다시
쓰이므로 내용이 똑같은데도 매번 다른 큐로 보였고, 그래서 간격 늘리기가 한 번도
작동하지 못했다. delta 가 2026-09-18 부터 사흘 동안 44회 깨워진 것이 이 결함이다
(recovery.log, 판정 run-20260921T104121Z).

그리고 큐를 비울 때 세션 머리에 들어가는 머리글이 한도 상태와 무관하게 "해제" 라고
적혀 있었다. 아직 막혀 있는데 풀렸다고 읽은 세션이 도구를 부르고 또 막히면서 고리가
한 바퀴 더 돈다.

여기서 보는 것 둘.
  1 큐 판별이 시각이 아니라 내용을 본다. 같은 신호가 다시 쓰여도 같은 큐로 본다.
  2 큐 비우기 머리글이 해제를 단정하지 않는다.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RECOVERY = REPO / "bin" / "rate-limit-recovery"
PROMPT_GUARD = REPO / "claude" / "hooks" / "rate-limit-prompt-guard.sh"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


def signature(queue_path):
    """복구 장치에서 큐 판별 함수만 떼어 내 돌린다."""
    script = (f'eval "$(awk "/^recovery_queue_signature\\(\\)/,/^}}/" {RECOVERY})"\n'
              'recovery_queue_signature "$1"\n')
    proc = subprocess.run(["bash", "-c", script, "bash", str(queue_path)],
                          capture_output=True, text=True, timeout=60)
    return proc.stdout.strip()


def write_queue(path, entries):
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
                    encoding="utf-8")


with tempfile.TemporaryDirectory() as td:
    q = Path(td) / "dotfiles.jsonl"

    # 같은 재개 신호가 시각만 바뀌어 다시 쓰인 경우. 가드는 막을 때마다 이렇게 쓴다.
    write_queue(q, [{"id": "autoresume-dotfiles", "ts_unix": 1789980000,
                     "content": "재개 검토 신호"}])
    first = signature(q)
    write_queue(q, [{"id": "autoresume-dotfiles", "ts_unix": 1789983600,
                     "content": "재개 검토 신호"}])
    second = signature(q)
    check("시각만 바뀐 같은 신호는 같은 큐로 본다", first and first == second,
          f"{first!r} vs {second!r}")

    # 줄 길이까지 달라져도 같다. 크기를 보면 여기서 갈린다.
    write_queue(q, [{"id": "autoresume-dotfiles", "ts_unix": 1789990000000,
                     "content": "재개 검토 신호", "extra": "길이를 바꾸는 칸"}])
    third = signature(q)
    check("같은 신호면 줄 길이가 달라도 같은 큐로 본다", first == third,
          f"{first!r} vs {third!r}")

    # 진짜 새 항목이 들어오면 달라야 한다. 안 그러면 새 사용자 메시지가 묻힌다.
    write_queue(q, [{"id": "1789981251-97819c29", "ts_unix": 1789981251,
                     "content": "사용자 메시지"}])
    other = signature(q)
    check("다른 항목이면 다른 큐로 본다", other and other != first,
          f"{other!r} vs {first!r}")

    # 항목이 늘어난 것도 새 큐다.
    write_queue(q, [{"id": "autoresume-dotfiles", "ts_unix": 1789980000},
                    {"id": "1789981251-97819c29", "ts_unix": 1789981251}])
    both = signature(q)
    check("항목이 늘면 다른 큐로 본다", both not in ("", first, other),
          f"{both!r}")

    # 순서가 바뀌어도 같은 모음이다.
    write_queue(q, [{"id": "1789981251-97819c29", "ts_unix": 1789981251},
                    {"id": "autoresume-dotfiles", "ts_unix": 1789980000}])
    swapped = signature(q)
    check("같은 항목이면 순서가 바뀌어도 같은 큐로 본다", swapped == both,
          f"{swapped!r} vs {both!r}")

    check("없는 큐 파일은 빈 값으로 답한다", signature(Path(td) / "없다.jsonl") == "")

# 머리글이 해제를 단정하면 안 된다. 막힌 채로 깨어난 세션이 그 글을 먼저 읽는다.
guard_src = PROMPT_GUARD.read_text(encoding="utf-8")
check("큐 비우기 머리글이 한도 해제를 단정하지 않는다",
      "[rate-limit 해제 — fresh" not in guard_src,
      "머리글에 해제 단정이 남아 있다")
check("큐 비우기 머리글이 아직 막혀 있을 수 있음을 말한다",
      guard_src.count("한도가 풀렸다는 뜻은 아니다") == 2,
      f"발견 {guard_src.count('한도가 풀렸다는 뜻은 아니다')}회 (trigger·일반 두 갈래여야 한다)")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
