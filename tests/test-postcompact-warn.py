#!/usr/bin/env python3
"""압축 표식을 만드는 훅과 소비하는 훅(postcompact-mark · postcompact-warn).

고정하는 것 넷.

1. 표식은 그 세션 이름으로 만든다. 기계에 하나뿐이면 한 세션의 압축 경고를 다른
   세션의 평범한 메시지가 가져가 버리고, 원래 받아야 할 세션은 아무것도 못 받는다.
2. 경고 표식과 차단 표식은 따로다. 경고는 다음 메시지 한 번에 소비되지만 차단 창은
   그 메시지와 상관없이 제 시간만큼 열려 있어야 한다 — 한 파일이면 아무 메시지나
   하나가 차단 창까지 닫는다.
3. 남의 세션 표식은 건드리지 않는다.
4. 세션을 모르면 전역 표식으로 조용히 내려가지 않는다. 아무것도 안 만들고 그 사실을
   로그에 남긴다.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "claude" / "hooks"
MARK = str(HOOKS / "postcompact-mark.sh")
WARN = str(HOOKS / "postcompact-warn.sh")

A = "sess-aaaaaaaa-1111"
B = "sess-bbbbbbbb-2222"

results = []


def check(name, cond, detail=None):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail!r}" if not cond and detail else ""))


def call(hook, home, sid):
    payload = {"session_id": sid} if sid is not None else {}
    env = dict(os.environ)
    env["HOME"] = str(home)
    p = subprocess.run([hook], input=json.dumps(payload), text=True,
                       capture_output=True, env=env, timeout=60)
    return p.returncode, p.stdout


def flag(home, sid, kind):
    return Path(home) / ".claude" / "state" / "compact-flags" / f"{sid}.{kind}"


with tempfile.TemporaryDirectory() as td:
    home = Path(td) / "home"
    (home / ".claude" / "state").mkdir(parents=True)

    print("=== 표식 만들기 ===")
    rc, _ = call(MARK, home, A)
    check("mark 가 정상 종료한다", rc == 0, rc)
    check("경고 표식을 그 세션 이름으로 만든다", flag(home, A, "warn").exists())
    check("차단 표식도 같이 만든다", flag(home, A, "block").exists())

    print("\n=== 경고는 자기 세션만 받는다 ===")
    rc, out = call(WARN, home, B)
    check("남의 세션 메시지에는 아무것도 안 준다", rc == 0 and out.strip() == "",
          (rc, out[:160]))
    check("남의 세션 메시지가 표식을 안 지운다", flag(home, A, "warn").exists())

    rc, out = call(WARN, home, A)
    check("자기 세션 메시지에 경고를 준다",
          rc == 0 and "POSTCOMPACT WARNING" in out, (rc, out[:160]))
    check("경고는 한 번만 — 표식을 지운다", not flag(home, A, "warn").exists())
    check("차단 표식은 그대로 남는다", flag(home, A, "block").exists())

    rc, out = call(WARN, home, A)
    check("두 번째 메시지에는 경고가 없다", rc == 0 and out.strip() == "",
          (rc, out[:160]))

    print("\n=== 묵은 경고는 버린다 ===")
    call(MARK, home, A)
    stale = flag(home, A, "warn")
    t = time.time() - 700
    os.utime(stale, (t, t))
    rc, out = call(WARN, home, A)
    check("10분 넘은 경고는 주입하지 않는다", rc == 0 and out.strip() == "",
          (rc, out[:160]))
    check("묵은 경고 표식은 지운다", not stale.exists())

    print("\n=== 세션을 모르면 가르지 못한 것을 드러낸다 ===")
    before = set(p.name for p in (home / ".claude" / "state"
                                  / "compact-flags").iterdir())
    rc, _ = call(MARK, home, None)
    after = set(p.name for p in (home / ".claude" / "state"
                                 / "compact-flags").iterdir())
    check("식별자가 없으면 표식을 안 만든다", rc == 0 and before == after,
          sorted(after - before))
    nosid = home / ".claude" / "logs" / "compact-flag-nosid.log"
    check("그 사실을 로그에 남긴다",
          nosid.exists() and "postcompact-mark" in nosid.read_text(),
          nosid.read_text()[-200:] if nosid.exists() else "로그 없음")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
