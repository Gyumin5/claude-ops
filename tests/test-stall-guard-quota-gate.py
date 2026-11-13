#!/usr/bin/env python3
"""stall-guard 의 한도 관문과 주입 상한을 고정한다.

왜 있나 — 옛 관문은 "5시간 사용률 수치를 모르면 아무것도 안 한다" 였다. 그런데 그
수치는 세션이 턴을 돌아야 신선해지고, 정체란 턴이 안 도는 상태다. 그래서 이 장치는
두 달 동안 판정 단계에 한 번도 도달하지 못했다(누적 quota-unknown 7,084회, 세션을
실제로 본 회차 0). 바꾼 규칙은 이렇다 — 계정이 일치하는 신선한 관측이 임계를 넘을
때만 보류하고, 관측이 낡았거나 없으면 수치를 지어내지 않고 턴과 무관한 조건으로만
판정하되 주입을 유계로 만든다.

이 검사가 고정하는 것 넷:
  1. 보류는 신선하고 계정이 맞는 관측에서만 나온다(낡음·없음·남의 계정은 통과).
  2. 낡은 관측에서도 정체 세션에 도달해 연속 2회 확인 뒤 한 번 깨운다.
  3. 같은 한도 공지에 대해서는 두 번까지만 깨운다. 공지가 바뀌면 칸이 새로 열린다.
  4. 정상 복구 장치의 차단기가 서 있으면 깨우지 않는다.
"""
import json
import os
import sys
import tempfile
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
g = SourceFileLoader("stallguard_quota", str(REPO / "bin" / "claude-stall-guard")).load_module()
results = []


def check(name, cond, detail: object = ""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name +
          (("\n      " + str(detail)) if detail and not cond else ""))


TMP = Path(tempfile.mkdtemp(prefix="stallguard-quota."))
ACCOUNT = "acct-0000-1111"

# --- 가짜 환경 ---------------------------------------------------------------
g.STATE_DIR = str(TMP)
g.LOG_FILE = str(TMP / "stall-guard.log")
g.HEARTBEAT = str(TMP / "stall-guard.heartbeat")
g.STATE_FILE = str(TMP / "stall-guard.json")
g._ONCE_FILE = str(TMP / "once.json")
g.OFF_FLAG = str(TMP / "off")
g.STATUSLINE_CACHE = str(TMP / "statusline_cache.json")
g.QUEUE_DIR = str(TMP / "rate-limit-queue")
os.makedirs(g.QUEUE_DIR, exist_ok=True)
g.csl.CLAUDE_JSON = str(TMP / "claude.json")
(TMP / "claude.json").write_text(
    json.dumps({"oauthAccount": {"accountUuid": ACCOUNT}}), encoding="utf-8")
# 이름표와 교체 세대는 이 기계 것을 보면 안 된다 — 없는 경로로 돌려 두면
# 계정 판정이 위 claude.json 만 보고, 세대 판정은 "교체한 적 없음" 으로 선다.
g.csl.ACCOUNT_MARKER = str(TMP / "이름표-없음")
g.csl.ACCOUNT_GENERATION = str(TMP / "세대-없음")


def write_cache(*, cached_at=None, five=10.0, seven=10.0, account=ACCOUNT,
                resets_in=3600):
    now = time.time()
    d = {"_cached_at": now if cached_at is None else cached_at,
         "_account": account,
         "rate_limits": {
             "five_hour": {"used_percentage": five, "resets_at": now + resets_in},
             "seven_day": {"used_percentage": seven, "resets_at": now + 7 * 86400}}}
    Path(g.STATUSLINE_CACHE).write_text(json.dumps(d), encoding="utf-8")


def drop_cache():
    try:
        os.unlink(g.STATUSLINE_CACHE)
    except OSError:
        pass


print("[1] 보류는 신선하고 계정이 맞는 관측에서만 나온다")
write_cache(five=95.0)
hold, why = g.quota_hold()
check("신선한 5시간 95%% 면 보류", hold is True, why)
check("그때 관측 상태는 fresh", g.quota_status() == "fresh", g.quota_status())
write_cache(five=10.0, seven=99.0)
check("신선한 7일 99%% 면 보류", g.quota_hold()[0] is True)
write_cache(five=10.0, seven=10.0)
check("임계 아래면 안 보류", g.quota_hold()[0] is False)
write_cache(cached_at=time.time() - 3600, five=95.0)
check("낡은 관측은 보류 근거가 아니다", g.quota_hold()[0] is False, g.quota_hold()[1])
check("낡은 관측 상태는 stale", g.quota_status() == "stale", g.quota_status())
write_cache(five=95.0, account="acct-남의것")
check("남의 계정 수치는 보류 근거가 아니다", g.quota_hold()[0] is False)
check("그 상태는 other-account", g.quota_status() == "other-account", g.quota_status())
drop_cache()
check("캐시가 없어도 보류하지 않는다", g.quota_hold()[0] is False)
check("그 상태는 missing", g.quota_status() == "missing", g.quota_status())


# --- 정체 세션 하나를 만든다 --------------------------------------------------
def transcript(limit_ago_min=180, reset_ago_min=60, queued=2, progressed=False):
    """한도 공지 + 그 뒤 큐 적재만 있는 transcript 를 만든다."""
    now = time.time()
    limit_ts = now - limit_ago_min * 60
    reset_lt = time.localtime(now - reset_ago_min * 60)
    notice = ("You've hit your session limit · resets %d:%02d"
              % (reset_lt.tm_hour, reset_lt.tm_min))

    def iso(e):
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(e))

    rows = [{"type": "assistant", "timestamp": iso(limit_ts - 600),
             "message": {"content": "일하는 중"}},
            {"type": "assistant", "timestamp": iso(limit_ts),
             "message": {"content": notice}}]
    for i in range(queued):
        rows.append({"type": "queue-operation", "timestamp": iso(limit_ts + 60 + i)})
    if progressed:
        rows.append({"type": "assistant", "timestamp": iso(limit_ts + 300),
                     "message": {"content": "다시 일한다"}})
    p = TMP / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return str(p)


SESSION = "잠든세션"
sent = []


def fake_inject(session, det, quota="?"):
    sent.append({"session": session, "quota": quota, "notice": det.get("limit_ts")})
    g.log("inject-ok session=%s quota=%s" % (session, quota))
    return True


g.live_sessions = lambda: [(SESSION, str(TMP / "proj"))]
g.inject = fake_inject
TRANSCRIPT = transcript()
g.newest_transcript = lambda pdir: TRANSCRIPT

print("\n[2] 낡은 관측에서도 정체 세션에 도달해 연속 2회 확인 뒤 한 번 깨운다")
write_cache(cached_at=time.time() - 3600, five=95.0)   # 낡음 = 수치 안 믿음
sent.clear()
g.main()
check("1회차에는 확인만 하고 안 깨운다", sent == [], sent)
g.main()
check("2회차에 한 번 깨운다", len(sent) == 1, sent)
check("깨울 때 관측 상태를 같이 남긴다", sent and sent[0]["quota"] == "stale", sent)
beat = Path(g.HEARTBEAT).read_text()
check("heartbeat 에 관측 상태와 검사 건수가 남는다",
      "quota=stale" in beat and "checked=1" in beat, beat.strip())

print("\n[3] 신선한 차단 관측이면 세션을 보지도 않는다")
write_cache(five=95.0)
sent.clear()
Path(g.LOG_FILE).write_text("", encoding="utf-8")
g.main()
check("주입 없음", sent == [], sent)
log = Path(g.LOG_FILE).read_text()
check("사유가 quota-blocked 로 남는다", "skip-all reason=quota-blocked" in log, log.strip())
check("5시간 수치가 사유에 들어간다", "5h 95%" in log, log.strip())

print("\n[4] 같은 한도 공지에는 두 번까지만 — 공지가 바뀌면 칸이 새로 열린다")
drop_cache()   # 관측 없음 = 통과
st = json.loads(Path(g.STATE_FILE).read_text())
check("공지 식별자가 상태에 남아 있다", int(st[SESSION].get("notice_ts") or 0) > 0, st)
check("공지당 주입 횟수가 1", int(st[SESSION].get("notice_injects") or 0) == 1, st)
# 간격·시간당 상한을 지나간 상태로 만들고 같은 공지로 계속 두드려 본다.
for want in (2, 2, 2):
    st = json.loads(Path(g.STATE_FILE).read_text())
    st[SESSION]["injects"] = []
    st[SESSION]["confirm"] = g.CONFIRM_TICKS - 1
    Path(g.STATE_FILE).write_text(json.dumps(st), encoding="utf-8")
    sent.clear()
    Path(g.LOG_FILE).write_text("", encoding="utf-8")
    g.main()
    st = json.loads(Path(g.STATE_FILE).read_text())
    got = int(st[SESSION].get("notice_injects") or 0)
    if got == want and want == 2 and not sent:
        check("공지당 상한에 걸려 안 깨운다", "reason=notice-cap" in Path(g.LOG_FILE).read_text(),
              Path(g.LOG_FILE).read_text().strip())
    else:
        check("공지당 누적이 %d" % want, got == want, st[SESSION])

# 공지가 새로 떴다 = 그 세션이 한 번은 돌았다는 뜻이므로 칸을 새로 연다.
TRANSCRIPT = transcript(limit_ago_min=100, reset_ago_min=40)
st = json.loads(Path(g.STATE_FILE).read_text())
st[SESSION]["injects"] = []
st[SESSION]["confirm"] = g.CONFIRM_TICKS - 1
Path(g.STATE_FILE).write_text(json.dumps(st), encoding="utf-8")
sent.clear()
g.main()
check("새 공지면 다시 깨운다", len(sent) == 1, sent)

print("\n[5] 복구 차단기가 서 있으면 안 깨운다")
Path(g.QUEUE_DIR, "%s.recovery-state.json" % SESSION).write_text(
    json.dumps({"breaker": True, "post_wake_block_count": 2}), encoding="utf-8")
check("차단기를 읽는다", g.breaker_tripped(SESSION) is True)
st = json.loads(Path(g.STATE_FILE).read_text())
st[SESSION]["injects"] = []
st[SESSION]["notice_injects"] = 0
st[SESSION]["confirm"] = g.CONFIRM_TICKS - 1
Path(g.STATE_FILE).write_text(json.dumps(st), encoding="utf-8")
sent.clear()
Path(g.LOG_FILE).write_text("", encoding="utf-8")
g.main()
check("차단기가 서 있으면 주입 0", sent == [], sent)
check("사유를 로그에 남긴다", "reason=recovery-breaker" in Path(g.LOG_FILE).read_text(),
      Path(g.LOG_FILE).read_text().strip())
Path(g.QUEUE_DIR, "%s.recovery-state.json" % SESSION).unlink()

print("\n[6] 진행이 있었으면 여전히 정체가 아니다")
TRANSCRIPT = transcript(progressed=True)
sent.clear()
g.main()
g.main()
check("한도 공지 뒤 진행이 있으면 안 깨운다", sent == [], sent)

ok = sum(1 for r in results if r)
print("\n%d/%d passed" % (ok, len(results)))
sys.exit(0 if ok == len(results) else 1)
