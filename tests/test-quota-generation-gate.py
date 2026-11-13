#!/usr/bin/env python3
"""계정을 갈아끼운 뒤에 받아온 수치만 차단의 근거가 되는지 본다.

왜 계정 표만으로는 모자란가(2026-11-09 홈 실측). 계정은 요청마다 다시 읽히지만 세션이
들고 있는 한도 수치는 그 세션이 마지막으로 받은 응답의 것이다. 교체 직전에 응답을 받아
둔 세션이 교체 뒤에 상태줄을 그리면, 그때 읽히는 계정은 이미 새 계정이라 옛 수치에 새
계정 표가 붙는다. 계정 비교가 통과하니 other-account 가 안 걸리고, 높은 값 유지 합치기가
그 수치를 되살린다. 그날 옛 계정의 88퍼센트가 그렇게 눌러앉았고 실제 계정은 14퍼센트
였다 — 다섯시간 차단 임계 90까지 2퍼센트포인트였다. 거기 닿았으면 그 기계의 모든 세션이
남의 한도로 막힌다.

가르는 값은 교체 세대 하나다. `bin/claude-accounts` 가 갈아끼울 때마다 세대를 올리고,
상태줄은 그 세션이 이번 세대에서 이미 한 번 그렸을 때만 수치를 공유 칸에 넣는다. 교체
전에 받아 둔 응답은 교체 뒤 첫 렌더 한 번에만 실리므로, 그 한 번을 빼면 남는 것은 새
계정이 돌려준 수치뿐이다.

여기서 고정하는 것 넷.
  1 세대 파일이 없는 기계(교체를 한 번도 안 한 기계)는 예전 그대로 돈다.
  2 교체 뒤 첫 렌더의 수치는 공유 칸에 안 들어가고 진단 칸에만 남는다.
  3 두 번째 렌더부터 들어가고, 높은 값 유지 합치기는 같은 세대끼리만 한다.
  4 세대가 확인 안 된 수치로는 가드 둘 다 차단하지 않는다(stale-gen).
"""
import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
import claude_session_lib as csl   # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "statusline_gate", REPO / "claude" / "statusline.py")
statusline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(statusline)

_loader = importlib.machinery.SourceFileLoader(
    "claude_accounts_gen", str(REPO / "bin" / "claude-accounts"))
_aspec = importlib.util.spec_from_loader(_loader.name, _loader)
accounts = importlib.util.module_from_spec(_aspec)
_loader.exec_module(accounts)

fails = []


def check(name, cond, detail=""):
    print(("PASS  " + name) if cond
          else ("FAIL  " + name + (f"\n      {str(detail)[:300]}" if detail else "")))
    if not cond:
        fails.append(name)


NOW = time.time()
R5, R7 = NOW + 3000, NOW + 200000


def win(pct, reset):
    return {"used_percentage": pct, "resets_at": reset}


def rl(pct):
    return {"five_hour": win(pct, R5), "seven_day": win(pct, R7)}


def render(session, rate, acct="acct-uuid"):
    """상태줄 한 번. 하네스가 넘기는 모양 그대로 들어간다."""
    statusline.csl.current_account_id = lambda path=None: acct
    statusline._write_cache({"session_id": session, "_cached_at": time.time(),
                             "rate_limits": rate})


def cache():
    return json.loads(Path(statusline.CACHE_FILE).read_text(encoding="utf-8"))


def setup(td, generation=None):
    """임시 캐시·세대·세션 기록. 세 파일 다 이 기계의 것과 안 섞인다."""
    statusline.CACHE_FILE = str(Path(td) / "statusline_cache.json")
    statusline.SEEN_GEN_FILE = str(Path(td) / "statusline-generation.json")
    gen_path = Path(td) / "account-generation"
    if generation is None:
        statusline.csl.ACCOUNT_GENERATION = str(Path(td) / "no-such-generation")
    else:
        gen_path.write_text("%d\n" % generation, encoding="utf-8")
        statusline.csl.ACCOUNT_GENERATION = str(gen_path)
    return gen_path


# --- 1. 세대 파일이 없으면 예전 그대로 --------------------------------------
# 교체 도구를 한 번도 안 쓴 기계가 있다. 거기서 이 판정이 걸리면 수치가 영영 안 쌓인다.
with tempfile.TemporaryDirectory() as td:
    setup(td)
    render("sess-a", rl(30))
    got = cache()
    check("세대가 없으면 첫 렌더부터 수치가 들어간다",
          got.get("rate_limits", {}).get("five_hour", {}).get("used_percentage") == 30,
          json.dumps(got, ensure_ascii=False))
    check("세대가 없으면 세대 표를 안 단다", "_generation" not in got, got.keys())
    render("sess-a", rl(45))
    check("세대가 없어도 합치기는 돈다",
          cache()["rate_limits"]["five_hour"]["used_percentage"] == 45,
          json.dumps(cache(), ensure_ascii=False))

# --- 2·3. 교체 뒤 첫 렌더는 못 믿고, 두 번째부터 믿는다 ----------------------
with tempfile.TemporaryDirectory() as td:
    setup(td, generation=7)
    render("sess-a", rl(88))          # 교체 전에 받아 둔 응답일 수 있다
    got = cache()
    check("교체 뒤 첫 렌더의 수치는 공유 칸에 안 들어간다",
          "rate_limits" not in got, json.dumps(got, ensure_ascii=False))
    check("그 수치는 진단 칸에 남는다",
          got.get("_unverified_rate_limits", {}).get("five_hour", {})
          .get("used_percentage") == 88, json.dumps(got, ensure_ascii=False))
    check("못 믿는 렌더는 세대 표를 안 단다", "_generation" not in got, got.keys())

    render("sess-a", rl(14))          # 교체 뒤에 보낸 요청의 응답이다
    got = cache()
    check("두 번째 렌더부터 수치가 들어간다",
          got.get("rate_limits", {}).get("five_hour", {}).get("used_percentage") == 14,
          json.dumps(got, ensure_ascii=False))
    check("그때 세대 표를 단다", got.get("_generation") == 7, got.get("_generation"))
    check("믿는 렌더에는 진단 칸을 안 남긴다",
          "_unverified_rate_limits" not in got, got.keys())

    render("sess-a", rl(20))
    check("같은 세대끼리는 높은 값 유지 합치기가 돈다",
          cache()["rate_limits"]["five_hour"]["used_percentage"] == 20,
          json.dumps(cache(), ensure_ascii=False))

# --- 2b. 그 사고의 모양 그대로 ------------------------------------------------
# 옛 계정 88 이 캐시에 있고, 교체 뒤 첫 렌더가 그 88 을 다시 들고 온다. 세대 표가 안
# 맞으므로 공유 칸은 안 건드린다 — 앞 값도 그대로고 새 값도 안 들어간다.
with tempfile.TemporaryDirectory() as td:
    setup(td, generation=7)
    render("sess-a", rl(88))
    render("sess-a", rl(88))          # 여기까지가 교체 전 세대라고 가정
    Path(td, "account-generation").write_text("8\n", encoding="utf-8")   # 교체
    before = cache()["rate_limits"]["five_hour"]["used_percentage"]
    render("sess-a", rl(88))          # 교체 전 응답을 든 첫 렌더
    got = cache()
    check("교체 직후 첫 렌더가 앞 세대 수치를 못 갱신한다",
          got["rate_limits"]["five_hour"]["used_percentage"] == before
          and got.get("_generation") == 7, json.dumps(got, ensure_ascii=False))

    render("sess-a", rl(14))          # 새 계정이 돌려준 실제 수치
    got = cache()
    check("새 계정 수치는 앞 세대의 높은 값에 안 눌린다",
          got["rate_limits"]["five_hour"]["used_percentage"] == 14
          and got.get("_generation") == 8, json.dumps(got, ensure_ascii=False))

# --- 2c. 세션마다 따로 센다 ---------------------------------------------------
# 한 세션이 확인됐다고 다른 세션까지 확인된 것이 아니다 — 교체 전 응답을 든 쪽은 그
# 세션이다.
with tempfile.TemporaryDirectory() as td:
    setup(td, generation=7)
    render("sess-a", rl(10))
    render("sess-a", rl(11))
    render("sess-b", rl(99))
    got = cache()
    check("다른 세션의 첫 렌더는 따로 못 믿는다",
          got["rate_limits"]["five_hour"]["used_percentage"] == 11,
          json.dumps(got, ensure_ascii=False))

    statusline.csl.current_account_id = lambda path=None: "acct-uuid"
    statusline._write_cache({"_cached_at": time.time(), "rate_limits": rl(99)})
    check("세션 이름이 없는 렌더도 못 믿는다",
          cache()["rate_limits"]["five_hour"]["used_percentage"] == 11,
          json.dumps(cache(), ensure_ascii=False))

# --- 4. 가드 판정 -------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    gen = base / "account-generation"
    gen.write_text("7\n", encoding="utf-8")
    cf = base / "cache.json"

    def put(doc):
        cf.write_text(json.dumps(doc), encoding="utf-8")

    fresh_doc = {"_cached_at": time.time(), "_account": "me", "_generation": 7,
                 "rate_limits": rl(95)}
    put(fresh_doc)
    obs = csl.account_quota_observation(str(cf), "me", generation=str(gen))
    check("세대가 맞으면 fresh", obs["status"] == "fresh", obs["status"])
    check("그때는 임계를 넘으면 막는다",
          csl.quota_blocked(str(cf), account="me", generation=str(gen))[0] is True,
          csl.quota_blocked(str(cf), account="me", generation=str(gen)))

    no_gen = dict(fresh_doc)
    no_gen.pop("_generation")
    put(no_gen)
    obs = csl.account_quota_observation(str(cf), "me", generation=str(gen))
    check("세대 표가 없으면 stale-gen", obs["status"] == "stale-gen", obs["status"])
    check("stale-gen 으로는 턴을 안 막는다",
          csl.quota_blocked(str(cf), account="me", generation=str(gen))[0] is False,
          csl.quota_blocked(str(cf), account="me", generation=str(gen)))
    check("stale-gen 으로는 깨우기도 안 보류한다",
          csl.windows_blocked(str(cf), account="me", generation=str(gen))[0] is False,
          csl.windows_blocked(str(cf), account="me", generation=str(gen)))

    put(dict(fresh_doc, _generation=6))
    check("세대가 다르면 stale-gen",
          csl.account_quota_observation(str(cf), "me",
                                        generation=str(gen))["status"] == "stale-gen")

    put(no_gen)
    check("세대 파일이 없으면 이 판정 자체를 건너뛴다",
          csl.account_quota_observation(
              str(cf), "me", generation=str(base / "none"))["status"] == "fresh")

    # 앞선 판정을 가리지 않는다 — 맨 뒤에 둔 이유다.
    put(dict(no_gen, _account="someone-else"))
    check("남의 계정 수치는 그대로 other-account",
          csl.account_quota_observation(str(cf), "me",
                                        generation=str(gen))["status"] == "other-account")
    put(dict(no_gen, _cached_at=time.time() - 3600))
    check("얼어붙은 수치는 그대로 stale",
          csl.account_quota_observation(str(cf), "me",
                                        generation=str(gen))["status"] == "stale")

    # 수치가 아예 없으면 불신할 대상이 없다. 복구 장치가 한도 칸을 들어낸 직후가 이
    # 모양이고, 여기에 stale-gen 이 붙으면 대기 큐가 안 풀린다(_has_used_number).
    put({"_cached_at": time.time(), "_account": "me"})
    check("수치가 없으면 stale-gen 을 안 붙인다",
          csl.account_quota_observation(str(cf), "me",
                                        generation=str(gen))["status"] == "fresh")

# --- 5. 교체가 세대를 올리고 보관분에는 세대 표를 안 단다 ---------------------
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    accounts.GENERATION = base / "account-generation"
    check("세대 파일이 없으면 1 부터", accounts.bump_generation() == 1)
    check("부를 때마다 하나씩 오른다", accounts.bump_generation() == 2)
    accounts.GENERATION.write_text("깨짐\n", encoding="utf-8")
    check("깨진 세대 파일은 1 로 다시 센다", accounts.bump_generation() == 1)

    accounts.CACHE = base / "live.json"
    accounts.ROOT = base / "homes"
    stash = accounts.ROOT / "acct-a" / ".claude"
    stash.mkdir(parents=True)
    (stash / "statusline_cache.json").write_text(
        json.dumps({"_account": "a-uuid", "rate_limits": rl(42)}), encoding="utf-8")

    accounts.CACHE.write_text(json.dumps(
        {"_account": "b-uuid", "_generation": 9, "rate_limits": rl(88), "model": "x"}),
        encoding="utf-8")
    accounts.adopt_quota("acct-a", None, 10)
    live = json.loads(accounts.CACHE.read_text(encoding="utf-8"))
    check("보관해 둔 수치는 도로 달린다",
          live["rate_limits"]["five_hour"]["used_percentage"] == 42,
          json.dumps(live, ensure_ascii=False))
    check("그때 세대 표는 떼고 단다", "_generation" not in live, live.keys())
    check("다른 칸은 그대로", live.get("model") == "x", live)

    accounts.CACHE.write_text(json.dumps(
        {"_account": "b-uuid", "_generation": 9, "rate_limits": rl(88)}),
        encoding="utf-8")
    accounts.adopt_quota("acct-a", None, None)
    live = json.loads(accounts.CACHE.read_text(encoding="utf-8"))
    check("세대를 못 올렸으면 수치를 비운다",
          "rate_limits" not in live and "_generation" not in live,
          json.dumps(live, ensure_ascii=False))

print(f"\n{'실패: ' + ', '.join(fails) if fails else '모두 통과'}")
sys.exit(1 if fails else 0)
