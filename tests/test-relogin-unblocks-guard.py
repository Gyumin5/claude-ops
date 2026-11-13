#!/usr/bin/env python3
"""재로그인이 한도 차단을 그 자리에서 푸는지 고정한다.

같은 계정으로 다시 로그인하면 계정 식별자가 그대로라 other-account 가 안 잡힌다.
그래서 로그인 이전 자격증명이 받아온 수치가 fresh 로 남고, 도구를 막는 가드가 그걸
근거로 계속 막았다. 해제는 5분짜리 복구 타이머 한 곳에만 걸려 있었다 — 2026-09-29
홈에서 12시 20분 로그인이 12시 23분에야 반영됐고 그 사이 도구는 계속 막혔다.

고정하는 것 다섯.

  1. 로그인 표시가 수치보다 나중이면 관측 status 가 relogin 이고 차단이 아니다.
  2. 그 상태에서 한도가 99퍼센트여도 차단이 아니다 — 그 수치가 근거가 아니기 때문이다.
  3. 로그인 표시가 수치보다 먼저면 평소대로 fresh 이고 임계를 넘으면 차단이다.
  4. 로그인 표시가 아예 없으면 예전과 똑같이 동작한다.
  5. 다른 계정 판정이 재로그인 판정보다 앞선다 — 남의 수치는 더 나쁜 근거다.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
results = []


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


csl = load("csl_relogin", REPO / "lib" / "claude_session_lib.py")

TMP = Path(tempfile.mkdtemp(prefix="relogin-test-"))
ACCT = "acct-aaa"
NOW = time.time()

# 이 검사는 이 기계의 계정·교체 상태를 보면 안 된다. 셋을 없는 경로로 돌려
# 두면 판정이 아래 fixture 만 보고 선다 — 안 돌리면 기계에 교체 세대 파일이
# 생기는 날 전부 stale-gen 으로 떨어지고, 이름표가 생기는 날 other-account 로
# 떨어진다. 둘 다 실제로 났다(11-10).
csl.ACCOUNT_GENERATION = str(TMP / "세대-없음")
csl.ACCOUNT_MARKER = str(TMP / "이름표-없음")
csl.CLAUDE_JSON = str(TMP / "설정-없음")


def write_cache(used_5h, used_7d, cached_at, account=ACCT, name="cache.json"):
    p = TMP / name
    p.write_text(json.dumps({
        "_cached_at": cached_at,
        "_account": account,
        "rate_limits": {
            "five_hour": {"used_percentage": used_5h, "resets_at": NOW + 3600},
            "seven_day": {"used_percentage": used_7d, "resets_at": NOW + 86400},
        },
    }), encoding="utf-8")
    return str(p)


def write_login(ts, name="login"):
    p = TMP / name
    p.write_text(f"{int(ts)}\n", encoding="utf-8")
    return str(p)


# --- 1~2. 로그인이 수치보다 나중 ------------------------------------------------
cache = write_cache(99, 99, NOW - 60)
login = write_login(NOW - 10)

obs = csl.account_quota_observation(cache, ACCT, login)
check("로그인이 수치보다 나중이면 status=relogin", obs["status"] == "relogin", obs["status"])

blocked, why = csl.quota_blocked(cache, account=ACCT, login_applied=login)
check("그 수치로는 차단하지 않는다", blocked is False, f"{blocked} / {why}")
check("사유에 relogin 이 남는다", "relogin" in why, why)

# --- 3. 로그인이 수치보다 먼저면 평소대로 ---------------------------------------
login_old = write_login(NOW - 600, name="login-old")
obs = csl.account_quota_observation(cache, ACCT, login_old)
check("로그인이 수치보다 먼저면 status=fresh", obs["status"] == "fresh", obs["status"])

blocked, why = csl.quota_blocked(cache, account=ACCT, login_applied=login_old)
check("그때는 임계 초과를 그대로 차단한다", blocked is True, f"{blocked} / {why}")

# 임계 아래는 로그인 표시가 있어도 차단 아님 (관계없는 경로가 안 열렸는지)
cache_low = write_cache(10, 10, NOW - 60, name="cache-low.json")
blocked, _ = csl.quota_blocked(cache_low, account=ACCT, login_applied=login_old)
check("임계 아래는 그대로 통과", blocked is False)

# --- 4. 로그인 표시 없음 = 예전 동작 --------------------------------------------
missing = str(TMP / "no-such-login")
obs = csl.account_quota_observation(cache, ACCT, missing)
check("로그인 표시가 없으면 status=fresh", obs["status"] == "fresh", obs["status"])
blocked, _ = csl.quota_blocked(cache, account=ACCT, login_applied=missing)
check("로그인 표시가 없으면 예전처럼 차단", blocked is True)

check("표시 파일이 없으면 시각은 None", csl.login_applied_at(missing) is None)
check("표시 파일이 있으면 시각을 읽는다",
      csl.login_applied_at(login) == float(int(NOW - 10)))

# 내용이 망가진 표시는 없는 것으로 본다 — 못 읽는 값으로 가드를 열지 않는다.
broken = TMP / "login-broken"
broken.write_text("어제쯤\n", encoding="utf-8")
check("읽을 수 없는 표시는 None", csl.login_applied_at(str(broken)) is None)
blocked, _ = csl.quota_blocked(cache, account=ACCT, login_applied=str(broken))
check("읽을 수 없는 표시로는 안 열린다", blocked is True)

# --- 5. 다른 계정 판정이 우선 ----------------------------------------------------
obs = csl.account_quota_observation(cache, "acct-bbb", login)
check("계정이 다르면 relogin 보다 other-account 가 먼저",
      obs["status"] == "other-account", obs["status"])

# --- 낡은 캐시 판정이 살아 있는지 (기존 동작 회귀) -------------------------------
cache_stale = write_cache(99, 99, NOW - (csl.CACHE_FRESH_SEC + 60), name="cache-stale.json")
obs = csl.account_quota_observation(cache_stale, ACCT, missing)
check("낡은 캐시는 여전히 stale", obs["status"] == "stale", obs["status"])

# 낡음이 재로그인보다 앞선다 — 얼어붙었다는 사실을 relogin 이 덮으면 안 된다.
obs = csl.account_quota_observation(cache_stale, ACCT, login)
check("낡은 캐시는 로그인이 더 나중이어도 stale", obs["status"] == "stale", obs["status"])

# --- 6. 미래 시각 표시는 안 믿는다 ----------------------------------------------
# 이 값이 미래면 캐시가 갱신돼도 계속 그보다 먼저라 relogin 에서 못 빠져나온다.
# 시계가 틀어졌거나 손으로 쓴 값 하나로 가드가 영구히 열리면 안 된다.
future = write_login(NOW + csl.LOGIN_FUTURE_SLACK_SEC + 60, name="login-future")
check("한참 미래인 표시는 None", csl.login_applied_at(future) is None)
obs = csl.account_quota_observation(cache, ACCT, future)
check("한참 미래인 표시로는 안 열린다", obs["status"] == "fresh", obs["status"])

# 한 뼘 안의 미래는 봐준다 — 로그인 직후 시계 흔들림까지 막으면 고치려던 자리가 안 열린다.
near = write_login(NOW + 5, name="login-near")
check("한 뼘 안의 미래는 믿는다", csl.login_applied_at(near) is not None)

# --- 7. 같은 초면 재로그인으로 안 본다 -------------------------------------------
same = write_login(NOW - 60, name="login-same")
cache_same = write_cache(99, 99, float(int(NOW - 60)), name="cache-same.json")
obs = csl.account_quota_observation(cache_same, ACCT, same)
check("로그인과 수치가 같은 초면 fresh", obs["status"] == "fresh", obs["status"])

# --- 8. 수치가 없으면 relogin 을 안 붙인다 ---------------------------------------
# relogin 은 "이 수치를 믿지 마라" 는 표시다. 복구 장치가 로그인을 보고 캐시에서
# rate_limits 를 들어내면 믿을 수치 자체가 없어진다 — 그때까지 relogin 이면 들어낸
# 쪽이 보낸 해제 신호를 이 표시가 덮어서 대기 큐가 안 풀린다.
stripped = TMP / "cache-stripped.json"
stripped.write_text(json.dumps({"_cached_at": NOW - 60, "_account": ACCT}), encoding="utf-8")
obs = csl.account_quota_observation(str(stripped), ACCT, login)
check("수치를 들어낸 캐시는 relogin 이 아니다", obs["status"] == "fresh", obs["status"])
blocked, _ = csl.quota_blocked(str(stripped), account=ACCT, login_applied=login)
check("그 상태는 차단도 아니다", blocked is False)

# --- 9. 프롬프트 가드를 가짜 홈에서 실제로 돌린다 --------------------------------
# 큐를 푸는 쪽은 재로그인만으로 안 연다. 그렇다고 새 입력을 그냥 통과시키면 밀려 있던
# 큐보다 방금 온 말이 먼저 답을 받아 순서가 뒤집힌다 — 큐를 지킨다면서 순서를 깨는
# 것이라 안 푸는 이유 자체가 무너진다. 깨우기 신호는 사람이 쓴 말이 아니라 통과시키면
# 그 글자가 운영자 화면에 그대로 뜬다. 넷 다 실제 훅 실행으로 고정한다.
HOOK = REPO / "claude" / "hooks" / "rate-limit-prompt-guard.sh"
FAKE = TMP / "fakehome"
PROJ = "relogintest"


def fake_home_reset(used_5h, cached_at, login_ts=None, queue_lines=(), strip=False):
    for sub in (".claude/state/rate-limit-queue",):
        (FAKE / sub).mkdir(parents=True, exist_ok=True)
    body = {"_cached_at": cached_at, "_account": ACCT}
    if not strip:
        body["rate_limits"] = {
            "five_hour": {"used_percentage": used_5h, "resets_at": NOW + 3600},
            "seven_day": {"used_percentage": 10, "resets_at": NOW + 86400},
        }
    (FAKE / ".claude/statusline_cache.json").write_text(json.dumps(body), encoding="utf-8")
    marker = FAKE / ".claude/state/rate-limit-login-applied"
    if login_ts is None:
        marker.unlink(missing_ok=True)
    else:
        marker.write_text(f"{int(login_ts)}\n", encoding="utf-8")
    q = FAKE / f".claude/state/rate-limit-queue/{PROJ}.jsonl"
    q.write_text("".join(
        json.dumps({"id": f"q{i}", "ts": "t", "ts_unix": int(NOW - 30), "project": PROJ,
                    "cause": "5h_or_7d", "state": "pending", "retry_count": 0,
                    "prompt": line}, ensure_ascii=False) + "\n"
        for i, line in enumerate(queue_lines)), encoding="utf-8")
    return q


def run_hook(prompt):
    env = dict(os.environ, HOME=str(FAKE), CLAUDE_SESSION_NAME=PROJ)
    env.pop("CLAUDE_AUTOMATED", None)
    p = subprocess.run(["bash", str(HOOK)], input=json.dumps({"prompt": prompt, "cwd": str(FAKE)}),
                       capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, p.stdout, p.stderr


TRIGGER = "<channel>trigger:queue-flush</channel>"

q = fake_home_reset(10, NOW - 60, login_ts=NOW - 10, queue_lines=["밀려 있던 말"])
rc, out, err = run_hook("방금 온 말")
check("재로그인 + 큐 있음: 새 입력을 통과시키지 않는다", rc == 2, f"rc={rc}")
lines = [json.loads(x) for x in q.read_text(encoding="utf-8").splitlines() if x.strip()]
check("새 입력을 큐 뒤에 넣는다", len(lines) == 2 and lines[-1]["prompt"] == "방금 온 말",
      str(lines)[:200])
check("무엇 때문에 들어갔는지 남긴다", lines[-1].get("cause") == "relogin_hold",
      str(lines[-1])[:200])
check("밀려 있던 말이 먼저다", lines[0]["prompt"] == "밀려 있던 말")

q = fake_home_reset(10, NOW - 60, login_ts=NOW - 10, queue_lines=["밀려 있던 말"])
rc, out, err = run_hook(TRIGGER)
check("재로그인 + 큐 있음: 깨우기 신호는 버린다", rc == 2, f"rc={rc}")
check("깨우기 신호 글자가 화면으로 안 샌다", "trigger:queue-flush" not in out, out[:200])
check("그때 큐는 그대로다",
      len(q.read_text(encoding="utf-8").strip().splitlines()) == 1)

q = fake_home_reset(10, NOW - 60, login_ts=NOW - 10, queue_lines=[])
rc, out, err = run_hook("큐가 없을 때")
check("재로그인 + 큐 없음: 평소대로 통과", rc == 0, f"rc={rc}")

q = fake_home_reset(10, NOW - 60, login_ts=NOW - 600, queue_lines=["밀려 있던 말"])
rc, out, err = run_hook("평소 입력")
check("로그인이 수치보다 먼저면 큐를 푼다", rc == 0 and "큐 비우기" in out, f"rc={rc} {out[:120]}")
check("푼 뒤 큐가 비어 있다", q.read_text(encoding="utf-8").strip() == "")

# 복구 장치가 수치를 들어낸 뒤 — 그 자리에서 풀려야 한다(로그인 표시는 그대로 남아 있다).
q = fake_home_reset(10, NOW - 60, login_ts=NOW - 10, queue_lines=["밀려 있던 말"], strip=True)
rc, out, err = run_hook("수치 들어낸 뒤")
check("수치를 들어내면 그 자리에서 큐가 풀린다", rc == 0 and "큐 비우기" in out,
      f"rc={rc} {out[:120]}")

q = fake_home_reset(97, NOW - 60, login_ts=NOW - 600, queue_lines=[])
rc, out, err = run_hook("차단 중 입력")
check("진짜 차단은 그대로 막는다", rc == 2, f"rc={rc}")
check("그때도 큐에 쌓인다",
      len(q.read_text(encoding="utf-8").strip().splitlines()) == 1)

# --- 10. 로그인 뒤에 켠 bypass 는 복구 장치가 안 지운다 --------------------------
rec = (REPO / "bin" / "rate-limit-recovery").read_text(encoding="utf-8")
check("bypass 삭제가 로그인 시각과 견준다", "SWITCH_SINCE" in rec)
check("나중에 켠 것은 건너뛴다", 'SWITCH_KEPT' in rec and "continue" in rec)
check("유지한 것도 기록에 남는다", "유지(${SWITCH_KEPT" in rec)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
