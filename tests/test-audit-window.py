#!/usr/bin/env python3
"""집계기 둘이 조회 기간 밖 레코드를 세지 않는지 본다.

대상은 bin/claude-token-audit 과 bin/claude-ctx-metrics 다. 둘 다 세션 파일을
수정 시각으로 고르는데, 세션 파일 하나가 몇 달을 담으므로 최근에 한 줄만 덧붙어도
그 파일의 과거가 같이 들어온다. 그래서 레코드 시각으로 한 번 더 걸러야 한다.
bin/claude-turn-audit 은 원래 그렇게 하고 있어 이 검사의 대상이 아니다.

돌리는 법은 HOME 을 임시 디렉토리로 바꿔 그 밑에 합성 세션 파일을 두는 것이다 —
두 집계기 모두 홈 밑 .claude/projects 를 보므로 실제 기록을 건드리지 않는다.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOKEN_AUDIT = REPO / "bin" / "claude-token-audit"
CTX_METRICS = REPO / "bin" / "claude-ctx-metrics"

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch)) + ".000Z"


def records(ts, tag):
    """같은 시각의 assistant(usage + tool_use)와 user(tool_result) 한 쌍."""
    return [
        {
            "type": "assistant",
            "timestamp": ts,
            "message": {
                "role": "assistant",
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cache_read_input_tokens": 10,
                    "cache_creation_input_tokens": 0,
                },
                "content": [{"type": "tool_use", "name": "Bash", "id": tag}],
            },
        },
        {
            "type": "user",
            "timestamp": ts,
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tag, "content": "x" * 50}
                ],
            },
        },
    ]


def write_home(home, rows):
    proj = home / ".claude" / "projects" / "-home-gmoh-testproj"
    proj.mkdir(parents=True)
    with open(proj / "sess.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def run(script, home, args=()):
    env = dict(os.environ)
    env["HOME"] = str(home)
    p = subprocess.run([sys.executable, str(script), *args],
                       capture_output=True, text=True, env=env)
    return p.returncode, p.stdout


def turns_of(out):
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0] == "합계":
            return int(parts[1])
    return -1


def results_of(out):
    for line in out.splitlines():
        if line.startswith("전체 tool_result 수:"):
            return int(line.split(":", 1)[1])
    return -1


now = time.time()
cutoff = now - 7 * 86400
ts_in = iso(now - 3600)            # 기간 안
ts_edge = iso(cutoff - 1)          # 경계 바로 바깥 — 날짜 비교로는 안 걸리는 자리
ts_old = iso(now - 30 * 86400)     # 한참 바깥
edge_same_day = ts_edge[:10] == iso(cutoff)[:10]

rows = records(ts_in, "a") + records(ts_edge, "b") + records(ts_old, "c")

print("=== 집계기 기간 경계 ===")

with tempfile.TemporaryDirectory() as td:
    home = Path(td)
    write_home(home, rows)

    rc, out = run(TOKEN_AUDIT, home)
    check("토큰 집계기가 정상 종료한다", rc == 0, f"rc={rc}")
    check("토큰 집계기가 기간 안 턴만 센다", turns_of(out) == 1,
          f"턴={turns_of(out)} (기대 1)")

    rc, out = run(CTX_METRICS, home)
    check("크기 집계기가 정상 종료한다", rc == 0, f"rc={rc}")
    check("크기 집계기가 기간 안 결과만 센다", results_of(out) == 1,
          f"건수={results_of(out)} (기대 1)")

    # 기간을 넓히면 바깥 것도 들어와야 한다 — 필터가 무조건 거르는 게 아님을 본다.
    rc, out = run(TOKEN_AUDIT, home, ("60",))
    check("토큰 집계기는 기간을 넓히면 과거도 센다", turns_of(out) == 3,
          f"턴={turns_of(out)} (기대 3)")
    rc, out = run(CTX_METRICS, home, ("--days", "60"))
    check("크기 집계기는 기간을 넓히면 과거도 센다", results_of(out) == 3,
          f"건수={results_of(out)} (기대 3)")

# 변별력: 필터를 수정 전으로 되돌린 사본은 같은 입력에서 더 많이 센다.
# 이게 안 갈리면 위 검사가 무엇도 지키지 않는 것이다.
TOKEN_SRC = TOKEN_AUDIT.read_text()
CTX_SRC = CTX_METRICS.read_text()

old_token = TOKEN_SRC.replace(
    "if ts and ts < CUTOFF_TS:",
    'if ts and ts < time.strftime("%Y-%m-%dT", time.gmtime(cutoff)):')
old_ctx = CTX_SRC.replace(
    "                    if ts and ts < cutoff_ts:\n                        continue\n",
    "")

check("토큰 집계기 되돌림 사본이 실제로 달라졌다", old_token != TOKEN_SRC)
check("크기 집계기 되돌림 사본이 실제로 달라졌다", old_ctx != CTX_SRC)

with tempfile.TemporaryDirectory() as td:
    home = Path(td)
    write_home(home, rows)
    old_dir = home / "olds"
    old_dir.mkdir()
    (old_dir / "token").write_text(old_token)
    (old_dir / "ctx").write_text(old_ctx)

    rc, out = run(old_dir / "token", home)
    if edge_same_day:
        check("수정 전 토큰 집계기는 경계 바깥 레코드를 센다", turns_of(out) == 2,
              f"턴={turns_of(out)} (기대 2)")
    else:
        # cutoff 가 자정 직후라 경계 레코드의 날짜가 갈린 회차. 날짜 비교로도 걸리므로
        # 이 주장은 성립하지 않는다 — 건너뛰고 그 사실을 남긴다.
        print("  SKIP  수정 전 토큰 집계기 변별 (경계 레코드가 전날로 넘어간 회차)")

    rc, out = run(old_dir / "ctx", home)
    check("수정 전 크기 집계기는 기간 밖 레코드를 센다", results_of(out) == 3,
          f"건수={results_of(out)} (기대 3)")

print(f"\nPASS {PASS}  FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
