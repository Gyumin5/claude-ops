#!/usr/bin/env python3
"""계정 한도 관측 계약 검사 — 2026-08-28 옆 기계 6시간 갇힘의 재현과 방지.

지키는 것:
  1. 낡은 캐시(cached_at 이 오래된)의 수치는 차단 근거가 아니다. 얼어붙은 7일 창
     99% 가 세션을 영구히 가두면 안 된다 — 그게 그 사고였다.
  2. 신선한 캐시가 임계를 넘으면 차단이다.
  3. 파일이 없거나 깨졌으면 모름이고, 모름은 차단이 아니다.
  4. 창의 리셋이 지났으면 그 창의 수치를 안 쓴다.

옛 계산(cached_at 을 안 보고 resets_at 지난 창만 0 으로 만드는)도 같이 돌려서,
이 검사가 실제로 그 결함을 잡는지 보인다. 잡히지 않으면 검사가 헛것이다.
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.realpath(__file__)), "..", "lib"))
import claude_session_lib as csl   # noqa: E402

FAILED = []


def cache_file(cached_at, fh, sd):
    d = {"_cached_at": cached_at,
         "rate_limits": {"five_hour": fh, "seven_day": sd}}
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(d, f)
    f.close()
    return f.name


def old_effective(w, now):
    """2026-08-28 까지 세 소비자에 복제돼 있던 계산. 비교용으로만 둔다."""
    rt = w.get("resets_at")
    if rt and now > rt:
        return 0
    v = w.get("used_percentage")
    return int(v) if isinstance(v, (int, float)) else -1


def old_blocked(fh, sd):
    now = time.time()
    return old_effective(fh, now) >= 90 or old_effective(sd, now) >= 99


def check(name, got, want, detail=""):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s (%r, 기대 %r) %s" % (name, got, want, detail))
        FAILED.append(name)


def main():
    now = time.time()
    print("얼어붙은 캐시 (2026-08-28 옆 기계 재현)")
    # 6시간 전에 언 캐시. 5시간 창은 리셋이 지났고, 7일 창은 리셋이 아직 미래다.
    fh = {"used_percentage": 99, "resets_at": now - 3600}
    sd = {"used_percentage": 99, "resets_at": now + 2 * 86400}
    p = cache_file(now - 6 * 3600, fh, sd)
    try:
        check("옛 계산은 차단으로 본다(결함 재현)", old_blocked(fh, sd), True)
        obs = csl.account_quota_observation(cache=p)
        check("관측이 낡음으로 표시된다", obs["status"], "stale")
        blocked, why = csl.quota_blocked(cache=p)
        check("낡은 수치로 차단하지 않는다", blocked, False, why)
        check("사유에 낡음이 드러난다", "stale" in why, True, why)
    finally:
        os.unlink(p)

    print("신선한 캐시")
    p = cache_file(now, {"used_percentage": 95, "resets_at": now + 600},
                   {"used_percentage": 2, "resets_at": now + 86400})
    try:
        blocked, why = csl.quota_blocked(cache=p)
        check("5시간 창 초과는 차단", blocked, True, why)
        check("사유가 어느 창인지 밝힌다", why.startswith("5h"), True, why)
    finally:
        os.unlink(p)

    p = cache_file(now, {"used_percentage": 10, "resets_at": now + 600},
                   {"used_percentage": 99, "resets_at": now + 86400})
    try:
        blocked, why = csl.quota_blocked(cache=p)
        check("7일 창 초과도 차단", blocked, True, why)
    finally:
        os.unlink(p)

    p = cache_file(now, {"used_percentage": 38, "resets_at": now + 600},
                   {"used_percentage": 2, "resets_at": now + 86400})
    try:
        blocked, _ = csl.quota_blocked(cache=p)
        check("임계 아래는 차단 아님", blocked, False)
    finally:
        os.unlink(p)

    print("창의 리셋이 지난 경우")
    p = cache_file(now, {"used_percentage": 99, "resets_at": now - 60},
                   {"used_percentage": 2, "resets_at": now + 86400})
    try:
        obs = csl.account_quota_observation(cache=p)
        check("그 창은 expired 로 표시", obs["five_hour"]["status"], "expired")
        blocked, _ = csl.quota_blocked(cache=p)
        check("지난 창의 수치로 차단하지 않는다", blocked, False)
    finally:
        os.unlink(p)

    print("읽을 수 없는 경우")
    obs = csl.account_quota_observation(cache="/nonexistent/quota.json")
    check("없으면 missing", obs["status"], "missing")
    check("없으면 차단 아님", csl.quota_blocked(cache="/nonexistent/quota.json")[0], False)

    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    f.write("{깨진 json")
    f.close()
    try:
        check("깨졌으면 error", csl.account_quota_observation(cache=f.name)["status"], "error")
        check("깨졌으면 차단 아님", csl.quota_blocked(cache=f.name)[0], False)
    finally:
        os.unlink(f.name)

    print("수치가 없는 창")
    p = cache_file(now, {"resets_at": now + 600}, {"resets_at": now + 86400})
    try:
        obs = csl.account_quota_observation(cache=p)
        check("invalid 로 표시", obs["five_hour"]["status"], "invalid")
        check("차단 아님", csl.quota_blocked(cache=p)[0], False)
    finally:
        os.unlink(p)

    if FAILED:
        print("\n실패 %d건: %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("\n전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
