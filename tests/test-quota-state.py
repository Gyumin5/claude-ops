#!/usr/bin/env python3
"""한도 판정(quota_5h_state / rate_limited) 테스트.

왜 있나: 2026-08-06 밤, 5시간 40분 차단 내내 사용률이 38% 로 읽혀 "한도 중" 가드가
한 번도 안 걸렸다. statusline 캐시는 세션이 턴을 돌 때만 갱신되는데 차단된 세션은
턴이 없어서 캐시가 차단 직전 값에 언다 — 가장 확실히 차단된 순간에 가장 확실하게
"한도 아님" 이라고 답한다. 게다가 옛 구현은 resets_at 이 지나면 0.0 을 돌려줘,
얼어붙은 창이 만료되는 순간부터는 0% 로 위조까지 했다.

그래서 고정하는 것: 모름을 숫자로 뭉개지 않는다, 큐가 차단의 양성 증거다,
모름은 차단 아님으로 실패한다(fail-open).
"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import claude_session_lib as L  # noqa: E402


def cache(tmp, cached_at, used, resets_at):
    p = os.path.join(tmp, "statusline_cache.json")
    with open(p, "w") as f:
        json.dump({"_cached_at": cached_at,
                   "rate_limits": {"five_hour": {"used_percentage": used, "resets_at": resets_at}}}, f)
    return p


def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + (("  — " + detail) if detail and not cond else ""))
    return bool(cond)


def main():
    now = time.time()
    tmp = tempfile.mkdtemp(prefix="quota-test.")
    qdir = os.path.join(tmp, "queue")
    os.makedirs(qdir)
    ok = True
    try:
        print("quota_5h_state")
        c = cache(tmp, now - 60, 38, now + 3600)
        st = L.quota_5h_state(c)
        ok &= check("신선하면 fresh + 수치", st["status"] == "fresh" and st["used"] == 38, str(st))

        # 어젯밤 그 상태: 캐시가 21:22 에 얼었고 수치는 38% 였다.
        c = cache(tmp, now - 3 * 3600, 38, now + 3600)
        st = L.quota_5h_state(c)
        ok &= check("얼어붙은 캐시는 stale", st["status"] == "stale", str(st))
        ok &= check("stale 이면 quota_5h 는 -1(모름)", L.quota_5h(c) == -1, str(L.quota_5h(c)))

        # 옛 구현이 0.0 을 돌려주던 자리. 0 은 "한도 아님" 의 적극적 근거가 돼버린다.
        c = cache(tmp, now - 60, 95, now - 10)
        st = L.quota_5h_state(c)
        ok &= check("만료된 창은 expired(0% 아님)", st["status"] == "expired", str(st))
        ok &= check("expired 도 -1", L.quota_5h(c) == -1)

        c = os.path.join(tmp, "없는파일.json")
        ok &= check("파일 없으면 missing", L.quota_5h_state(c)["status"] == "missing")

        print("rate_limited")
        fresh_low = cache(tmp, now - 60, 38, now + 3600)
        b, why = L.rate_limited("proj", qdir, fresh_low)
        ok &= check("큐 비고 사용률 낮으면 한도 아님", not b, why)

        with open(os.path.join(qdir, "proj.jsonl"), "w") as f:
            f.write('{"queued": 1}\n')
        b, why = L.rate_limited("proj", qdir, fresh_low)
        ok &= check("큐가 차 있으면 사용률 38%여도 한도", b and "큐" in why, why)

        open(os.path.join(qdir, "proj.jsonl"), "w").close()   # flush 된 상태 = 0바이트
        b, why = L.rate_limited("proj", qdir, fresh_low)
        ok &= check("flush 되면 한도 해제", not b, why)

        b, why = L.rate_limited("proj", qdir, cache(tmp, now - 60, 95, now + 3600))
        ok &= check("fresh 95% 는 보조 양성 신호", b and "95" in why, why)

        stale_high = cache(tmp, now - 3 * 3600, 95, now + 3600)
        b, why = L.rate_limited("proj", qdir, stale_high)
        ok &= check("stale 수치는 판정 근거가 아니다", not b, why)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("ALL PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
