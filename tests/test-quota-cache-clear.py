#!/usr/bin/env python3
"""한도 캐시 비우기 명령이 비울 것만 비우고 나머지는 안 건드리는지 본다.

이 명령은 사용량 초기화권을 쓴 뒤의 복구 경로다. 초기화는 주간 창 끝 시각을 그대로 두고
수치만 내리므로 합치기의 래칫이 그 값을 매번 버린다 — 자동으로 가려낼 신호가 없어서
(끝 시각 후퇴 750건, 다섯시간 칸 빠진 쓰기 170건) 사람이 알려 줄 때만 기준을 버린다.

여기서 보는 것 넷.
  1 한도 칸만 사라지고 다른 칸은 남는다.
  2 이미 비어 있으면 성공으로 끝낸다.
  3 캐시가 없어도 성공으로 끝낸다 — 비울 것이 없는 것은 실패가 아니다.
  4 깨진 파일은 덮어쓰지 않고 실패로 끝낸다. 조용히 새로 쓰면 다른 세션의 상태가 사라진다.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CMD = REPO / "bin" / "claude-quota-cache-clear"

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


def run(path):
    return subprocess.run([sys.executable, str(CMD), str(path)],
                          capture_output=True, text=True)


FULL = {
    "_account": "acct-1",
    "_cached_at": 1792634000,
    "cwd": "/home/u/dotfiles",
    "rate_limits": {
        "five_hour": {"used_percentage": 40, "resets_at": 1792644000},
        "seven_day": {"used_percentage": 99, "resets_at": 1792900800},
    },
}

with tempfile.TemporaryDirectory() as td:
    # 1. 한도 칸만 비운다.
    p = Path(td) / "cache.json"
    p.write_text(json.dumps(FULL), encoding="utf-8")
    r = run(p)
    saved = json.loads(p.read_text(encoding="utf-8"))
    check("한도 칸이 사라진다", "rate_limits" not in saved, p.read_text(encoding="utf-8"))
    check("나머지 칸은 그대로 남는다",
          saved.get("_account") == "acct-1" and saved.get("cwd") == "/home/u/dotfiles",
          json.dumps(saved, ensure_ascii=False))
    check("rc=0 으로 끝난다", r.returncode == 0, r.stderr)
    check("비우기 전 수치를 보여준다", "99퍼센트" in r.stdout, r.stdout)
    check("갈아끼우기 뒤 임시 파일이 안 남는다",
          [q.name for q in Path(td).iterdir()] == ["cache.json"],
          str([q.name for q in Path(td).iterdir()]))

    # 2. 이미 비어 있으면 성공으로 끝낸다.
    r = run(p)
    check("이미 비어 있으면 rc=0", r.returncode == 0, r.stderr)
    check("이미 비어 있다고 알린다", "이미 비어 있다" in r.stdout, r.stdout)

    # 3. 파일이 없어도 성공이다 — 비울 것이 없는 것은 실패가 아니다.
    r = run(Path(td) / "없는파일.json")
    check("캐시가 없으면 rc=0", r.returncode == 0, r.stderr)

    # 4. 깨진 파일은 덮어쓰지 않는다. 다른 세션의 상태를 날리면 안 된다.
    broken = Path(td) / "broken.json"
    broken.write_text("{이건 사전이 아니다", encoding="utf-8")
    r = run(broken)
    check("깨진 파일은 rc=1", r.returncode == 1, r.stdout + r.stderr)
    check("깨진 파일을 덮어쓰지 않는다",
          broken.read_text(encoding="utf-8") == "{이건 사전이 아니다",
          broken.read_text(encoding="utf-8"))

    # 사전이 아닌 정상 JSON 도 같다.
    arr = Path(td) / "arr.json"
    arr.write_text("[1, 2, 3]", encoding="utf-8")
    r = run(arr)
    check("사전이 아니면 rc=1", r.returncode == 1, r.stdout + r.stderr)
    check("사전이 아닌 파일도 그대로 남는다",
          arr.read_text(encoding="utf-8") == "[1, 2, 3]", arr.read_text(encoding="utf-8"))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
