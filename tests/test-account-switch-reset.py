#!/usr/bin/env python3
"""로그인을 갈아끼운 순간 한도 수치와 bypass 표시가 스스로 초기화되는지 본다.

공유 한도 캐시(~/.claude/statusline_cache.json)에는 그동안 계정 표시가 없었다. 그래서
계정을 갈아끼워도 옛 계정의 수치가 그대로 근거로 남았고, 가드는 그것을 신선한 값으로
읽어 전 세션을 가뒀다(2026-09-20 14:43, 옛 계정의 7일 99퍼센트). 빠져나오는 길은
사람이 bypass 표시를 손으로 켜고 세션마다 "이제 써도 된다" 를 알리는 것뿐이었다.

그래서 고정하는 것 셋.
  1 캐시에 계정 표시를 같이 적는다. 표시가 지금 계정과 다르면 그 수치는 근거가 아니다
    (상태 other-account) — 가드가 즉시 열린다.
  2 계정이 다르면 합치지도 않는다. 남의 계정 수치는 이 계정의 하한이 아니다.
  3 복구 장치가 계정 교체를 본 그 한 번에 수치를 들어내고 bypass 표시를 지우고
    재개 대기열을 처음으로 되돌린다. 처음 보는 계정(표시 파일 없음)은 교체가 아니다.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
import claude_session_lib as L  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return str(path)


def claude_json(path, uuid):
    return write(path, {"oauthAccount": {"accountUuid": uuid, "emailAddress": "x@y"}})


def cache_file(path, account, five=None, seven=None, cached_at=None):
    import time
    rl = {}
    if five is not None:
        rl["five_hour"] = {"used_percentage": five, "resets_at": time.time() + 3600}
    if seven is not None:
        rl["seven_day"] = {"used_percentage": seven, "resets_at": time.time() + 86400}
    d = {"_cached_at": cached_at if cached_at is not None else time.time(), "rate_limits": rl}
    if account is not None:
        d["_account"] = account
    return write(path, d)


# ── 1. 계정 식별 ────────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory(prefix="acct.") as td:
    tmp = Path(td)
    check("로그인된 계정 식별자를 읽는다",
          L.current_account_id(claude_json(tmp / "a.json", "uuid-A")) == "uuid-A")
    check("파일이 없으면 모름(None)", L.current_account_id(str(tmp / "없다.json")) is None)
    check("oauthAccount 가 없으면 모름", L.current_account_id(write(tmp / "b.json", {})) is None)
    check("식별자가 빈 문자열이면 모름",
          L.current_account_id(write(tmp / "c.json",
                                     {"oauthAccount": {"accountUuid": ""}})) is None)
    check("json 이 깨졌으면 모름",
          L.current_account_id(str(write(tmp / "d.json", {}))) is None
          or (tmp / "d.json").write_text("{깨짐", encoding="utf-8") is None
          and L.current_account_id(str(tmp / "d.json")) is None)

# ── 1b. 갈아끼운 뒤의 계정은 이름표에서 읽는다 ──────────────────────────────
# 설정 파일(.claude.json)의 번호는 로그인할 때만 바뀌어서 갈아끼운 뒤에도 옛 계정을
# 가리킨다(2026-11-10 실측, 14분 넘게). 그래서 이름표와 자격 지문을 먼저 본다.
with tempfile.TemporaryDirectory(prefix="acct.") as td:
    tmp = Path(td)
    root = tmp / "seats"

    def seat(name, uuid, token):
        (root / name / ".claude").mkdir(parents=True, exist_ok=True)
        (root / name / ".claude" / ".credentials.json").write_text(token, encoding="utf-8")
        claude_json(root / name / ".claude.json", uuid)
        return root / name

    seat("day", "uuid-DAY", '{"t":"day-token"}')
    seat("night", "uuid-NIGHT", '{"t":"night-token"}')
    marker = tmp / "account-current"
    live = tmp / "live-credentials.json"
    stale = claude_json(tmp / "stale.json", "uuid-NIGHT")   # 설정 파일은 안 따라왔다

    marker.write_text("day\n", encoding="utf-8")
    live.write_text('{"t":"day-token"}', encoding="utf-8")
    check("이름표와 자격이 맞으면 그 자리의 번호를 쓴다",
          L.current_account_id(marker=str(marker), root=str(root),
                               live_cred=str(live)) == "uuid-DAY")
    check("설정 파일만 보는 옛 길은 옛 계정을 가리킨다(이 결함을 고친 것이다)",
          L.current_account_id(stale) == "uuid-NIGHT")

    live.write_text('{"t":"갱신된-토큰"}', encoding="utf-8")
    check("자격 지문이 다르면 그 자리 번호를 안 쓴다",
          L.seat_account_id(str(marker), str(root), str(live)) is None)
    # 여기가 토론(run-20261109T232244Z)이 막은 자리다. 지문이 다른 것은 계정이
    # 바뀌었다는 증거가 아니라 모른다는 뜻이다 — 토큰이 저절로 갱신되기만 해도
    # 달라진다. 그때 설정 파일의 낡은 번호로 내려가면 아무도 아무것도 안 한 회차에
    # 번호가 바뀌고, 복구 장치가 그걸 계정 교체로 읽어 캐시를 비우고 전 세션을 깨운다.
    src, uid = L.account_identity(marker=str(marker), root=str(root),
                                  live_cred=str(live))
    check("그때 판정은 '모름' 이다", src == "unverified" and uid is None, f"{src}/{uid}")
    check("모름일 때 설정 파일의 낡은 번호로 내려가지 않는다",
          L.current_account_id(marker=str(marker), root=str(root),
                               live_cred=str(live)) is None)

    check("이름표가 아예 없는 기계는 예전처럼 설정 파일을 믿는다",
          L.account_identity(marker=str(tmp / "이름표-없다"))[0] == "config")

    live.write_text('{"t":"day-token"}', encoding="utf-8")
    marker.write_text("없는자리\n", encoding="utf-8")
    check("이름표가 없는 자리를 가리키면 모름",
          L.seat_account_id(str(marker), str(root), str(live)) is None)
    marker.write_text("../밖\n", encoding="utf-8")
    check("이름표에 경로 구분자가 있으면 안 따라간다",
          L.seat_account_id(str(marker), str(root), str(live)) is None)
    marker.write_text("\n", encoding="utf-8")
    check("이름표가 비면 모름", L.seat_account_id(str(marker), str(root), str(live)) is None)
    check("이름표 파일이 아예 없으면 모름",
          L.seat_account_id(str(tmp / "없다"), str(root), str(live)) is None)

    marker.write_text("day\n", encoding="utf-8")
    check("path 를 직접 주면 이름표를 안 본다",
          L.current_account_id(stale, marker=str(marker), root=str(root),
                               live_cred=str(live)) == "uuid-NIGHT")

# ── 2. 관측이 남의 계정 수치를 근거로 안 쓴다 ───────────────────────────────
with tempfile.TemporaryDirectory(prefix="acct.") as td:
    tmp = Path(td)
    # 세대 판정은 이 절의 주제가 아니다. 이 기계의 세대 파일을 읽으면 교체를 한 번이라도
    # 한 기계에서만 빨갛게 뜬다(2026-11-10 홈에서 실제로 그랬다) — 없는 경로를 준다.
    nogen = str(tmp / "세대-없음")
    same = cache_file(tmp / "same.json", "uuid-A", five=10, seven=20)
    other = cache_file(tmp / "other.json", "uuid-A", five=95, seven=99)
    none = cache_file(tmp / "none.json", None, five=10, seven=20)

    obs = L.account_quota_observation(same, account="uuid-A", generation=nogen)
    check("같은 계정 수치는 신선하다", obs["status"] == "fresh", obs["status"])
    check("관측이 그 수치의 계정을 같이 알려준다", obs["account"] == "uuid-A", str(obs))

    obs = L.account_quota_observation(other, account="uuid-B", generation=nogen)
    check("계정이 다르면 신선이 아니다", obs["status"] == "other-account", obs["status"])

    blocked, why = L.quota_blocked(other, account="uuid-B", generation=nogen)
    check("남의 계정 99퍼센트로는 안 막는다", not blocked, why)
    blocked, why = L.quota_blocked(other, account="uuid-A", generation=nogen)
    check("같은 계정이면 그 수치로 막는다", blocked and "95" in why, why)
    seven_only = cache_file(tmp / "s7.json", "uuid-A", seven=99)
    blocked, why = L.quota_blocked(seven_only, account="uuid-A", generation=nogen)
    check("같은 계정 7일 99퍼센트도 막는다", blocked and "7d" in why, why)
    blocked, why = L.quota_blocked(seven_only, account="uuid-B", generation=nogen)
    check("남의 계정 7일 99퍼센트로는 안 막는다", not blocked, why)

    obs = L.account_quota_observation(none, account="uuid-A", generation=nogen)
    check("계정 표시가 없는 옛 캐시는 지금까지대로 판정한다",
          obs["status"] == "fresh" and obs["account"] is None, str(obs))

    import time
    stale_other = cache_file(tmp / "so.json", "uuid-A", five=95,
                             cached_at=time.time() - 3 * 3600)
    obs = L.account_quota_observation(stale_other, account="uuid-B", generation=nogen)
    check("낡았고 계정도 다르면 계정 쪽을 말한다", obs["status"] == "other-account", obs["status"])

    # 계정이 안 서는 상태의 소비 규칙. 번호만 None 으로 바꾸고 끝내면 이 캐시가
    # 그대로 fresh 로 통해 "누구 것인지 모르는 수치" 가 차단 근거로 올라간다.
    saved_marker, saved_root, saved_live = L.ACCOUNT_MARKER, L.ACCOUNTS_ROOT, L.LIVE_CRED
    try:
        unv_marker = tmp / "이름표"
        unv_marker.write_text("day\n", encoding="utf-8")
        L.ACCOUNT_MARKER = str(unv_marker)
        L.ACCOUNTS_ROOT = str(tmp / "없는자리들")
        L.LIVE_CRED = str(tmp / "없는자격")
        obs = L.account_quota_observation(other, generation=nogen)
        check("계정이 안 서면 그 수치는 신선이 아니다",
              obs["status"] == "account-unverified", obs["status"])
        blocked, why = L.quota_blocked(other, generation=nogen)
        check("계정이 안 서는 수치로는 안 막는다", not blocked, why)
        empty = cache_file(tmp / "비었다.json", "uuid-A", five=None, seven=None)
        obs = L.account_quota_observation(empty, generation=nogen)
        check("믿을 수치가 없으면 이 표시를 안 붙인다",
              obs["status"] != "account-unverified", obs["status"])
    finally:
        L.ACCOUNT_MARKER, L.ACCOUNTS_ROOT, L.LIVE_CRED = saved_marker, saved_root, saved_live

    missing = L.account_quota_observation(str(tmp / "없다.json"), account="uuid-A",
                                         generation=nogen)
    check("캐시가 없으면 missing 이고 계정은 모름",
          missing["status"] == "missing" and missing["account"] is None, str(missing))

# ── 3. statusline 쓰기: 계정 표시와 계정 경계 ───────────────────────────────
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("statusline", REPO / "claude" / "statusline.py")
statusline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(statusline)

with tempfile.TemporaryDirectory(prefix="acct.") as td:
    tmp = Path(td)
    cpath = tmp / "cache.json"
    statusline.CACHE_FILE = str(cpath)
    # 이 절은 계정 표시와 경계만 본다. 이름표와 세대 파일을 이 기계 것으로 두면
    # 지금 이 기계가 어느 계정인지·몇 번 갈아끼웠는지에 따라 결과가 갈린다.
    L.ACCOUNT_MARKER = str(tmp / "이름표-없음")
    L.ACCOUNT_GENERATION = str(tmp / "세대-없음")
    L.CLAUDE_JSON = claude_json(tmp / "claude.json", "uuid-A")

    statusline._write_cache({"_cached_at": 1.0, "rate_limits":
                             {"five_hour": {"used_percentage": 93, "resets_at": 100}}})
    d = json.loads(cpath.read_text(encoding="utf-8"))
    check("쓸 때 계정 표시를 같이 남긴다", d.get("_account") == "uuid-A", str(d))

    statusline._write_cache({"_cached_at": 2.0, "rate_limits":
                             {"five_hour": {"used_percentage": 24, "resets_at": 100}}})
    d = json.loads(cpath.read_text(encoding="utf-8"))
    check("같은 계정 안에서는 거꾸로 가는 값을 막는다",
          d["rate_limits"]["five_hour"]["used_percentage"] == 93, str(d))

    L.CLAUDE_JSON = claude_json(tmp / "claude.json", "uuid-B")
    statusline._write_cache({"_cached_at": 3.0, "rate_limits":
                             {"five_hour": {"used_percentage": 24, "resets_at": 100}}})
    d = json.loads(cpath.read_text(encoding="utf-8"))
    check("계정이 바뀌면 옛 수치와 합치지 않는다",
          d["rate_limits"]["five_hour"]["used_percentage"] == 24, str(d))
    check("바뀐 계정 표시가 남는다", d.get("_account") == "uuid-B", str(d))

# ── 4. 복구 장치가 계정 교체를 처리한다 ─────────────────────────────────────
RECOVERY = REPO / "bin" / "rate-limit-recovery"


def run_recovery(home, uuid, seen=None, bypass=(), five=None, seven=None, account=None,
                 login_applied=None, login_seen=None, bypass_mtime=None):
    """가짜 HOME 으로 복구 장치를 한 번 돌린다. 큐는 비워 둔다(깨우기·알림 경로 배제)."""
    home.mkdir(parents=True, exist_ok=True)
    state = home / ".claude" / "state"
    (state / "rate-limit-queue").mkdir(parents=True, exist_ok=True)
    claude_json(home / ".claude.json", uuid)
    cache_file(home / ".claude" / "statusline_cache.json",
               account, five=five, seven=seven)
    if seen is not None:
        (state / "rate-limit-account-seen").write_text(seen + "\n", encoding="utf-8")
    if login_applied is not None:
        (state / "rate-limit-login-applied").write_text(login_applied + "\n", encoding="utf-8")
    if login_seen is not None:
        (state / "rate-limit-login-seen").write_text(login_seen + "\n", encoding="utf-8")
    for name in bypass:
        (state / name).write_text("", encoding="utf-8")
        # 로그인보다 먼저 켰나 나중에 켰나로 보존 여부가 갈린다 — 그래서 시각을 정한다.
        if bypass_mtime is not None:
            os.utime(state / name, (bypass_mtime, bypass_mtime))
    # 깨우기를 흩는 무작위 대기는 여기서 끈다. 이 검사가 보는 것은 상태 파일이
    # 어떻게 바뀌나지 몇 초 뒤에 나가나가 아니고, 켜 두면 한 회차가 실제로 잔다
    # (실측 4분 32초). 흩는 동작 자체는 tests/test-flush-jitter.py 가 본다.
    env = dict(os.environ, HOME=str(home), CLAUDE_FLUSH_JITTER_MAX="0")
    env.pop("CLAUDE_BG_GUARD_OFF", None)
    subprocess.run(["bash", str(RECOVERY)], env=env, capture_output=True, timeout=120)
    return state


with tempfile.TemporaryDirectory(prefix="acct.") as td:
    home = Path(td) / "home"
    state = run_recovery(home, "uuid-A", seen=None,
                         bypass=["rate-limit-bypass.flag"], five=10, account="uuid-A")
    seen = (state / "rate-limit-account-seen").read_text(encoding="utf-8").strip()
    check("처음 본 계정은 표시만 적는다", seen == "uuid-A", seen)
    check("처음 보는 것은 교체가 아니라 bypass 를 안 지운다",
          (state / "rate-limit-bypass.flag").exists())

with tempfile.TemporaryDirectory(prefix="acct.") as td:
    home = Path(td) / "home"
    state = run_recovery(home, "uuid-B", seen="uuid-A",
                         bypass=["rate-limit-bypass.flag", "rate-limit-bypass-delta.flag"],
                         five=95, seven=99, account="uuid-A")
    cache = json.loads((home / ".claude" / "statusline_cache.json").read_text(encoding="utf-8"))
    check("계정이 바뀌면 남의 계정 수치를 캐시에서 들어낸다",
          "rate_limits" not in cache, str(cache))
    check("전역 bypass 표시를 지운다", not (state / "rate-limit-bypass.flag").exists())
    check("프로젝트별 bypass 표시도 지운다",
          not (state / "rate-limit-bypass-delta.flag").exists())
    check("새 계정을 표시에 적는다",
          (state / "rate-limit-account-seen").read_text(encoding="utf-8").strip() == "uuid-B")
    log = (state / "rate-limit-queue" / "recovery.log")
    check("무엇을 했는지 기록에 남긴다",
          log.exists() and "계정 교체 감지" in log.read_text(encoding="utf-8"),
          log.read_text(encoding="utf-8") if log.exists() else "기록 없음")
    check("사람이 보던 차단 표시도 내린다",
          not (state / "rate-limit-blocked.flag").exists())

with tempfile.TemporaryDirectory(prefix="acct.") as td:
    home = Path(td) / "home"
    state = run_recovery(home, "uuid-A", seen="uuid-A",
                         bypass=["rate-limit-bypass.flag"], five=10, account="uuid-A")
    check("계정이 그대로면 bypass 를 건드리지 않는다",
          (state / "rate-limit-bypass.flag").exists())
    cache = json.loads((home / ".claude" / "statusline_cache.json").read_text(encoding="utf-8"))
    check("계정이 그대로면 수치도 그대로 둔다", "rate_limits" in cache, str(cache))
    log = (state / "rate-limit-queue" / "recovery.log")
    check("계정이 그대로면 교체 기록을 안 남긴다",
          not log.exists() or "계정 교체 감지" not in log.read_text(encoding="utf-8"))

# ── 4-b. 릴레이 로그인(식별자가 아직 안 바뀐 경우)도 잡는다 ────────────────
# 통제 봇 릴레이는 격리 HOME 에서 로그인하고 자격증명만 옮긴다. 그래서 그 순간
# ~/.claude.json 의 계정 식별자는 아직 옛 값이다 — 식별자만 보면 못 잡는 경로다.
with tempfile.TemporaryDirectory(prefix="acct.") as td:
    home = Path(td) / "home"
    state = run_recovery(home, "uuid-A", seen="uuid-A", login_applied="1790000000",
                         bypass=["rate-limit-bypass.flag"], bypass_mtime=1789999000,
                         five=95, account="uuid-A")
    check("식별자가 그대로여도 로그인 적용 표시를 보면 교체로 본다",
          not (state / "rate-limit-bypass.flag").exists())
    check("소비한 로그인 표시를 적어 둔다",
          (state / "rate-limit-login-seen").read_text(encoding="utf-8").strip() == "1790000000")

# 로그인 뒤에 켠 bypass 는 남긴다. 사람이 로그인하고도 안 풀려서 손으로 연 문이라,
# 로그인 표시 하나로 닫으면 방금 연 문을 곧바로 잠근다(2026-09-29 12:23 실사고).
with tempfile.TemporaryDirectory(prefix="acct.") as td:
    home = Path(td) / "home"
    state = run_recovery(home, "uuid-A", seen="uuid-A", login_applied="1790000000",
                         bypass=["rate-limit-bypass.flag"], bypass_mtime=1790000100,
                         five=95, account="uuid-A")
    check("로그인 뒤에 켠 bypass 는 남긴다",
          (state / "rate-limit-bypass.flag").exists())
    log = (state / "rate-limit-queue" / "recovery.log")
    check("그래도 교체 처리는 했다",
          log.exists() and "계정 교체 감지" in log.read_text(encoding="utf-8"),
          log.read_text(encoding="utf-8") if log.exists() else "기록 없음")
    check("무엇을 남겼는지 기록에 적는다",
          "유지(rate-limit-bypass.flag" in log.read_text(encoding="utf-8"),
          log.read_text(encoding="utf-8"))

with tempfile.TemporaryDirectory(prefix="acct.") as td:
    home = Path(td) / "home"
    state = run_recovery(home, "uuid-A", seen="uuid-A", login_applied="1790000000",
                         login_seen="1790000000", bypass=["rate-limit-bypass.flag"],
                         five=95, account="uuid-A")
    check("같은 로그인 표시를 두 번 세지 않는다",
          (state / "rate-limit-bypass.flag").exists())

check("로그인 적용 경로가 그 표시를 실제로 남긴다",
      "rate-limit-login-applied" in (REPO / "bin" / "claude-accounts")
      .read_text(encoding="utf-8"))

# ── 5. 재무장이 계정 교체에서 간격까지 되돌린다 ─────────────────────────────
with tempfile.TemporaryDirectory(prefix="acct.") as td:
    home = Path(td) / "home"
    qdir = home / ".claude" / "state" / "rate-limit-queue"
    qdir.mkdir(parents=True, exist_ok=True)
    sfile = qdir / "delta.recovery-state.json"
    write(sfile, {"signature": "3:abc", "attempts": 3, "next_due_at": 9999999999,
                  "post_wake_block_count": 2, "breaker": True, "wake_id": "w1"})
    run_recovery(home, "uuid-B", seen="uuid-A", five=95, account="uuid-A")
    st = json.loads(sfile.read_text(encoding="utf-8"))
    check("교체 뒤 차단기가 풀린다",
          "breaker" not in st and "post_wake_block_count" not in st, str(st))
    check("교체 뒤 다시 시도할 간격도 처음으로 돌아간다",
          st.get("attempts") == 0 and st.get("next_due_at") == 0, str(st))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
