#!/usr/bin/env python3
"""공유 한도 캐시에 오래된 수치가 새 수치를 덮어쓰지 못하게 막았는지 본다.

한도 수치는 전 세션이 파일 하나(~/.claude/statusline_cache.json)를 같이 쓴다.
세션은 자기가 마지막으로 계정에서 받아온 값을 들고 있다가 상태줄을 그릴 때 그대로
쓰고, 그 순간의 시각을 새로 찍는다. 그래서 오래 쉰 세션이 한 번 깨어나면 몇십 분 전
값이 방금 잰 값처럼 저장된다. 우리 신선 판정은 언제 쟀는지가 아니라 언제 썼는지만 본다.

실측(2026-09, 기록 2,829건): 같은 창에서 5시간 사용량이 10퍼센트포인트 이상 거꾸로 간
자리 36곳, 5시간 창이 통째로 빠진 채 저장된 기록 160건. 제일 큰 것이 09-21 17:40 의
93퍼센트가 18:21 에 24퍼센트로 찍힌 자리이고, 그 시각대가 20분마다 거짓 해제가 나던
구간이다.

여기서 보는 것 넷.
  1 같은 창이면 낮은 값이 높은 값을 못 덮는다. 사용량은 초기화 전까지 줄지 않는다.
  2 창이 빠진 채 들어와도 아직 안 끝난 창이면 앞 값을 안 지운다.
  3 창이 초기화되거나 계정이 바뀌어 초기화 시각이 달라지면 새 값을 그대로 받는다.
    2026-09-20 옛 계정 값에 갇힌 사고가 되살아나면 안 된다.
  4 이어받기는 초기화 시각이 안 지난 창에만 한다. 끝난 창을 이어받으면 다섯시간 창이
    아예 없는 계정에서 남의 값이 영원히 산다.

창 시각을 지금 시각 기준으로 잡는 이유: 2·4 가 "그 창이 아직 도는가" 로 갈리므로
고정 상수를 쓰면 파일이 묵는 만큼 2 의 상황이 4 로 변한다.
"""
import importlib.util
import io
import json
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("statusline", REPO / "claude" / "statusline.py")
statusline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(statusline)

# 교체 세대 판정은 여기서 보는 대상이 아니다(tests/test-quota-generation-gate.py).
# 이 기계에 세대 파일이 있고 없고에 따라 결과가 갈리지 않게 없는 경로로 못 박는다 —
# 세대가 없으면 합치기만 도는 길이라 아래 기대값이 그대로 선다.
statusline.csl.ACCOUNT_GENERATION = "/nonexistent/account-generation"

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


def win(pct, reset):
    return {"used_percentage": pct, "resets_at": reset}


NOW = time.time()
R5, R7 = NOW + 3000, NOW + 200000       # 아직 도는 창
R5_DONE, R7_DONE = NOW - 3000, NOW - 200000   # 이미 끝난 창

# 1. 실측 회귀 — 09-21 17:40 의 93퍼센트를 18:21 의 24퍼센트가 덮었다.
old = {"five_hour": win(93, R5), "seven_day": win(65, R7)}
new = {"five_hour": win(24, R5), "seven_day": win(58, R7)}
m = statusline._merge_rate_limits(old, new)
check("같은 창에서 낮은 5시간 값이 높은 값을 못 덮는다",
      m["five_hour"]["used_percentage"] == 93, json.dumps(m, ensure_ascii=False))
check("같은 창에서 낮은 7일 값도 못 덮는다",
      m["seven_day"]["used_percentage"] == 65, json.dumps(m, ensure_ascii=False))

# 2. 올라가는 값은 그대로 받는다. 안 그러면 사용량이 영원히 안 오른다.
m = statusline._merge_rate_limits(old, {"five_hour": win(95, R5), "seven_day": win(66, R7)})
check("올라간 값은 그대로 받는다",
      (m["five_hour"]["used_percentage"], m["seven_day"]["used_percentage"]) == (95, 66),
      json.dumps(m, ensure_ascii=False))

# 3. 창이 초기화되면 초기화 시각이 바뀐다 — 0으로 떨어지는 것이 정상이다.
m = statusline._merge_rate_limits(old, {"five_hour": win(0, R5 + 18000), "seven_day": win(65, R7)})
check("창이 초기화되면 0으로 떨어진 값을 그대로 받는다",
      m["five_hour"]["used_percentage"] == 0, json.dumps(m, ensure_ascii=False))

# 4. 계정을 바꾸면 초기화 시각이 같이 갈린다. 옛 계정 값에 갇히면 안 된다(2026-09-20).
m = statusline._merge_rate_limits({"seven_day": win(99, R7)},
                                  {"seven_day": win(34, R7 + 140000)})
check("계정 교체로 초기화 시각이 갈리면 낮은 값을 그대로 받는다",
      m["seven_day"]["used_percentage"] == 34, json.dumps(m, ensure_ascii=False))

# 5. 창이 통째로 빠진 채 들어오는 기록이 160건 있었다. 그걸 읽으면 기준이 사라진다.
#    아직 도는 창에만 해당한다 — 끝난 창은 아래 10 에서 지운다.
m = statusline._merge_rate_limits(old, {"seven_day": win(66, R7)})
check("아직 도는 5시간 창이 빠진 채 들어와도 앞 값을 안 지운다",
      m["five_hour"]["used_percentage"] == 93, json.dumps(m, ensure_ascii=False))
m = statusline._merge_rate_limits(old, {})
check("한도 칸이 통째로 비어 들어와도 두 창이 남는다",
      m.get("five_hour", {}).get("used_percentage") == 93
      and m.get("seven_day", {}).get("used_percentage") == 65,
      json.dumps(m, ensure_ascii=False))
m = statusline._merge_rate_limits(old, {"five_hour": {"resets_at": R5}, "seven_day": win(66, R7)})
check("값 없는 5시간 창이 들어와도 앞 값을 안 지운다",
      m["five_hour"]["used_percentage"] == 93, json.dumps(m, ensure_ascii=False))

# 6. 앞 캐시가 없거나 깨졌으면 새 값을 그대로 쓴다. 첫 기록을 막으면 안 된다.
check("앞 캐시가 없으면 새 값을 그대로 쓴다",
      statusline._merge_rate_limits(None, new) == new)

# 7. 파일까지 통과하는지. 두 번째 쓰기가 첫 값을 못 낮춰야 한다.
with tempfile.TemporaryDirectory() as td:
    statusline.CACHE_FILE = str(Path(td) / "statusline_cache.json")
    statusline._write_cache({"_cached_at": 1, "rate_limits": dict(old)})
    statusline._write_cache({"_cached_at": 2, "rate_limits": dict(new)})
    saved = json.loads(Path(statusline.CACHE_FILE).read_text(encoding="utf-8"))
    check("파일에 남은 값이 낮은 쪽으로 안 내려간다",
          saved["rate_limits"]["five_hour"]["used_percentage"] == 93,
          json.dumps(saved, ensure_ascii=False))
    check("쓴 시각은 늦게 쓴 쪽으로 갱신된다", saved["_cached_at"] == 2)
    check("갈아끼우기 뒤 임시 파일이 안 남는다",
          [p.name for p in Path(td).iterdir()] == ["statusline_cache.json"],
          str([p.name for p in Path(td).iterdir()]))

# 8. 칸이 null 로 와도 상태줄이 죽지 않는다.
# 2026-09-26 실사고 — 캐시 파일을 지운 직후 상태줄이
# "'NoneType' object has no attribute 'get'" 로 죽었다. 하네스가 넘기는 칸은 빠지는 게
# 아니라 null 로 오는데, 캐시가 있을 때는 합치기가 그 null 을 앞 값으로 채워 가려 왔다.
# 캐시가 없는 첫 회에만 터지므로 평소엔 안 보인다. 상태줄이 죽으면 이 파일이 안 갱신되고,
# 그러면 한도 가드가 낡은 수치에 갇혀 세션이 안 풀린다.
with tempfile.TemporaryDirectory() as td:
    statusline.CACHE_FILE = str(Path(td) / "statusline_cache.json")
    for label, payload in (
        ("한도 칸이 null", {"rate_limits": None}),
        ("5시간 창이 null", {"rate_limits": {"five_hour": None, "seven_day": None}}),
        ("맥락·비용 칸이 null", {"context_window": None, "cost": None}),
    ):
        sys.stdin = io.StringIO(json.dumps(payload))
        sys.stdout = io.StringIO()
        try:
            statusline.main()
            crashed = ""
        except Exception as exc:  # noqa: BLE001 — 무엇으로 죽었는지 그대로 보여야 한다
            crashed = f"{type(exc).__name__}: {exc}"
        finally:
            sys.stdout = sys.__stdout__
            sys.stdin = sys.__stdin__
        check(f"{label} 이어도 상태줄이 안 죽는다", not crashed, crashed)

# 9. 사용량 초기화권은 여기서 자동으로 못 가려낸다 — 그 예외를 다시 넣지 마라.
# 그 초기화는 주간 창 끝 시각을 그대로 두고 수치만 내리므로(2026-10-22 실사고: 실제 9를
# 래칫이 매번 99로 되돌렸고 주간 차단 임계가 정확히 99라 두 호스트 전 세션이 막힌 것으로
# 읽혔다) "다섯시간 창이 뒤로 갈리면 그 payload 는 더 나중 것이니 주간 값도 받자" 는
# 예외를 한 번 넣었다가 되돌렸다(fb34d57 → 이 커밋). 되돌린 근거 둘.
#   1 공유 캐시는 한 관측의 결과가 아니다. 다섯시간 칸이 빠진 기록이 170건 있고(실측),
#     그런 쓰기는 주간만 갱신하므로 캐시의 두 칸이 서로 다른 시점에서 온다. 그러면
#     "다섯시간 창이 뒤" 가 "주간 값도 더 나중" 을 뜻하지 않는다.
#   2 끝 시각 자체가 후퇴한다 — 기록 27,984건에서 다섯시간 창 후퇴 750건(10-22 09:23 에
#     110분 후퇴). 전제였던 단조성이 성립하지 않는다.
# 초기화를 반영하는 길은 사람이 알려 주고 bin/claude-quota-cache-clear 로 한도 칸을 비우는 것이다.
R5_NEXT = R5 + 6600  # 끝 시각이 뒤로 간 다섯시간 창
held = {"five_hour": win(21, R5), "seven_day": win(99, R7)}

m = statusline._merge_rate_limits(held, {"five_hour": win(0, R5_NEXT), "seven_day": win(9, R7)})
check("다섯시간 창이 뒤로 갈려도 같은 주간 창에서 내려온 값은 못 받는다",
      m["seven_day"]["used_percentage"] == 99, json.dumps(m, ensure_ascii=False))
check("그때 다섯시간 창은 갈렸으니 내려온 값을 그대로 받는다",
      m["five_hour"]["used_percentage"] == 0, json.dumps(m, ensure_ascii=False))

m = statusline._merge_rate_limits(held, {"five_hour": win(5, R5 - 6600), "seven_day": win(9, R7)})
check("더 이른 다섯시간 창을 든 payload 도 주간 값을 못 낮춘다",
      m["seven_day"]["used_percentage"] == 99, json.dumps(m, ensure_ascii=False))

m = statusline._merge_rate_limits(held, {"five_hour": win(0, R5_NEXT)})
check("다섯시간 창만 든 payload 가 와도 주간 값은 앞 값으로 남는다",
      m.get("seven_day", {}).get("used_percentage") == 99, json.dumps(m, ensure_ascii=False))

# 10. 이어받기는 아직 도는 창에만 한다.
# 2026-11-05 실사고 — 울트라 등급 계정에는 다섯시간 창이 아예 없다. 그러면 매 렌더가
# "창이 빠진 채 들어옴" 이라서 9 의 이어받기가 매번 옛 값을 되살렸고, 그 값은 초기화
# 시각이 한참 지나도 캐시에 그대로 남았다. 다른 계정에서 온 수치가 영원히 살아 도구
# 차단까지 간 자리다. 초기화 시각이 다를 때만 도는 비교(3·4)는 새 값이 있어야 걸리므로
# 이 경우에 닿지 못한다.
old_done = {"five_hour": win(93, R5_DONE), "seven_day": win(65, R7)}

m = statusline._merge_rate_limits(old_done, {"seven_day": win(66, R7)})
check("끝난 5시간 창은 빠진 채 들어오면 지워진다",
      "five_hour" not in m, json.dumps(m, ensure_ascii=False))
check("그때 같이 든 7일 창은 그대로 남는다",
      m["seven_day"]["used_percentage"] == 66, json.dumps(m, ensure_ascii=False))

m = statusline._merge_rate_limits({"seven_day": win(99, R7_DONE)}, {})
check("끝난 7일 창도 같은 규칙으로 지워진다",
      "seven_day" not in m, json.dumps(m, ensure_ascii=False))

m = statusline._merge_rate_limits({"five_hour": {"used_percentage": 93}},
                                  {"seven_day": win(66, R7)})
check("초기화 시각이 없어 끝났는지 못 가리면 기존대로 이어받는다",
      m.get("five_hour", {}).get("used_percentage") == 93, json.dumps(m, ensure_ascii=False))

m = statusline._merge_rate_limits(old_done, {"five_hour": win(12, R5), "seven_day": win(66, R7)})
check("끝난 창이어도 새 값이 같이 오면 그 값을 쓴다",
      m["five_hour"]["used_percentage"] == 12, json.dumps(m, ensure_ascii=False))

# 울트라 모양 — 다섯시간 창이 매번 빠진 채로 렌더가 반복돼도 옛 값이 안 되살아난다.
with tempfile.TemporaryDirectory() as td:
    statusline.CACHE_FILE = str(Path(td) / "statusline_cache.json")
    statusline._write_cache({"_cached_at": 1, "rate_limits": dict(old_done)})
    for i in range(2, 5):
        statusline._write_cache({"_cached_at": i, "rate_limits": {"seven_day": win(66, R7)}})
    saved = json.loads(Path(statusline.CACHE_FILE).read_text(encoding="utf-8"))
    check("다섯시간 창이 없는 계정에서 끝난 옛 값이 캐시에 안 남는다",
          "five_hour" not in saved["rate_limits"], json.dumps(saved, ensure_ascii=False))
    check("그동안 7일 창은 계속 갱신된다",
          saved["rate_limits"]["seven_day"]["used_percentage"] == 66,
          json.dumps(saved, ensure_ascii=False))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
