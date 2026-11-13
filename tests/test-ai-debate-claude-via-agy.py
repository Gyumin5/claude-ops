#!/usr/bin/env python3
"""토론의 클로드 자리를 어느 통로로 부르는지 고정한다.

지금은 클로드 자리가 전부 클로드 코드 통로다. 2026-11-05 에 오푸스 자리를 agy 로 옮겼다가
(운영자 8229·8242) 2026-11-07 에 되돌렸다(운영자 8663) — agy 쪽 클로드 한도가 하루를 못
갔고, 그 거절이 엿새를 막는다. 통로를 고르는 자리는 그대로 남아 있어서 집합에 이름을 넣으면
다시 agy 로 간다.

고정하는 것 여섯.

  1. 통로 집합이 비어 있다 — 클로드 자리는 클로드 코드로 간다.
  2. 집합에 이름을 넣으면 그 자리가 agy 로 가고, 넘기는 이름은 모델 + 추론 강도다. 강도
     접미사가 없으면 agy 가 --effort 를 따로 요구하며 거절해 그 자리가 통째로 빠진다.
  3. 호출 갈래를 agy_model(agent) 하나가 가른다. 갈래가 둘로 갈리면 한쪽만 고쳐진다.
  4. 지금은 평소 회차에도 깊은 회차에도 클로드 코드 자격이 필요하다.
  5. 재인증 안내가 자리별로 갈린다. agy 자리에 클로드 코드 안내를 주면 엉뚱한 데를 고친다.
  6. 통로가 기록에 남는다. 이름만 적으면 "그 회차는 공유 한도를 썼나" 를 되짚을 근거가 없다.

agy 한도 거절도 같이 고정한다. 그 거절은 종료코드 0 에 본문 한 줄로 와서 안 가리면 "파싱
실패" 로 분류되고, 그러면 부를 곳이 없는 상태로 한 번 더 부른다 — 되돌리기 전 구간에서
클로드 자리 거절 39 중 19 가 그 헛호출이었다.
"""
import importlib.machinery
import importlib.util
import sys
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


mod = load("ai_debate_claude_via_agy", REPO / "bin" / "ai-debate")
src = (REPO / "bin" / "ai-debate").read_text(encoding="utf-8")

# --- 1. 통로 집합 -----------------------------------------------------------
check("클로드 자리는 전부 클로드 코드 통로다", mod.CLAUDE_VIA_AGY == set(),
      f"{mod.CLAUDE_VIA_AGY} — 2026-11-07 에 agy 통로를 되돌렸다")
check("오푸스 자리에 agy 이름이 안 붙는다",
      mod.agy_model("claude-opus") is None, mod.agy_model("claude-opus"))
check("페이블 자리도 클로드 코드다",
      mod.agy_model("claude-fable") is None, mod.agy_model("claude-fable"))
check("코덱스 자리는 이 갈래와 무관하다",
      mod.agy_model("codex") is None and mod.agy_model("codex-astra") is None)

# 통로 설명의 정본은 ai-collaborate 스킬이다. 코드를 되돌린 뒤 그 문서가 "오푸스 자리는 agy"
# 로 남아 있어서, 세션이 그걸 읽고 운영자에게 "토론은 클로드 한도와 무관하다" 고 틀리게
# 답했다(2026-11-11). 집합이 비어 있는 동안 문서에 agy 통로 문구가 다시 들어오면 잡는다.
skill_path = REPO / "claude" / "skills" / "ai-collaborate" / "SKILL.md"
if not skill_path.exists():
    # 그 스킬이 없는 체크아웃(공개판 등)에는 잴 문서가 없다.
    print("SKIP  스킬 문서 통로 문구 — 이 체크아웃에 ai-collaborate 스킬이 없다")
else:
    skill = skill_path.read_text(encoding="utf-8")
    stale = [s for s in ("claude-opus-5-5-high", "오푸스 자리는 아예 agy", "오푸스 5.5 는 agy",
                         "클로드 자리는 agy 로 부른다")
             if s in skill]
    check("스킬 문서가 클로드 자리를 agy 통로로 적지 않는다",
          bool(mod.CLAUDE_VIA_AGY) or not stale, stale)

# --- 2. 다시 옮길 때 넘기는 이름 --------------------------------------------
# 통로를 쓰는 기계는 그대로 둔다. 지워 버리면 다시 옮길 때 처음부터 만들어야 하고,
# 제미나이 자리가 같은 갈래를 쓰므로 그 코드는 어차피 살아 있어야 한다.
_saved_via = set(mod.CLAUDE_VIA_AGY)
mod.CLAUDE_VIA_AGY = {"claude-opus"}
check("집합에 이름을 넣으면 그 자리가 agy 로 간다",
      mod.agy_model("claude-opus") == f"{mod.CLAUDE_MODELS['claude-opus']}-{mod.claude_effort()}",
      mod.agy_model("claude-opus"))
check("그 이름에 추론 강도 접미사가 붙어 있다",
      mod.agy_model("claude-opus").rsplit("-", 1)[-1] in ("high", "medium", "low"),
      f"{mod.agy_model('claude-opus')} — 접미사가 없으면 agy 가 --effort 를 요구하며 거절한다")
_saved_effort = mod.CLAUDE_EFFORT
mod.CLAUDE_EFFORT = "medium"
check("강도를 바꾸면 넘기는 이름도 따라간다",
      mod.agy_model("claude-opus").endswith("-medium"), mod.agy_model("claude-opus"))
mod.CLAUDE_EFFORT = _saved_effort
check("집합에 적을 수 있는 이름이 실제 클로드 자리다",
      set(mod.CLAUDE_VIA_AGY) <= set(mod.CLAUDE_MODELS),
      f"{mod.CLAUDE_VIA_AGY} 와 {sorted(mod.CLAUDE_MODELS)}")
mod.CLAUDE_VIA_AGY = _saved_via

# --- 3. 호출 갈래가 한 자리에서 갈린다 --------------------------------------
check("호출 갈래를 agy_model 하나가 가른다",
      "_agy_model = agy_model(agent)" in src and "if _agy_model:" in src,
      "갈래가 둘로 갈리면 제미나이 쪽만 고쳐지는 자리가 생긴다")

check("agy 갈래가 클로드 코드 갈래보다 먼저다",
      src.index("_agy_model = agy_model(agent)")
      < src.index('elif agent.startswith("claude")'),
      "뒤에 오면 클로드 코드 갈래가 오푸스 자리를 먼저 가져간다")

check("클로드 코드 갈래는 매핑에 없는 자리를 실패로 돌린다",
      "모르는 클로드 자리" in src,
      "옛 기본값(claude-opus-5-5)을 그대로 두면 통로를 비워도 조용히 클로드 코드가 답한다")

# --- 4. 어느 자리가 클로드 코드로 가나 ---------------------------------------
def cli_seats(pool):
    """클로드 코드 로그인을 쓰는 자리들. agy 로 가는 자리는 그 로그인을 안 쓴다."""
    return [a for a in pool if a.startswith("claude") and a not in mod.CLAUDE_VIA_AGY]


_saved_pool = list(mod.POOL)
check("평소 회차에 오푸스 자리가 클로드 코드로 선다",
      cli_seats(mod.POOL) == ["claude-opus"], cli_seats(mod.POOL))
mod.deep_on(True)
check("깊은 회차에는 둘 다 클로드 코드다",
      cli_seats(mod.POOL) == ["claude-opus", "claude-fable"], cli_seats(mod.POOL))
mod.deep_on(False)
mod.POOL = _saved_pool

# --- 5. 재인증 안내 ---------------------------------------------------------
check("agy 자리의 재인증 안내가 따로 남아 있다",
      "agy" in mod._REAUTH_FIX.get("claude-opus", ""), mod._REAUTH_FIX.get("claude-opus"))
check("그 안내가 클로드 코드 안내와 다르다",
      mod._REAUTH_FIX.get("claude-opus") != mod._REAUTH_FIX.get("claude"))

# --- 6. 기록 ---------------------------------------------------------------
check("회차 기록·캐시 키·메타 셋 다 통로를 적는다",
      src.count('"claude_via_agy"') == 3,
      f"지금 {src.count('claude_via_agy')}군데 — 캐시 키에 없으면 통로를 바꿔도 옛 결론이 나온다")

# --- agy 한도 거절은 재시도하지 않는다 ---------------------------------------
QUOTA = ("\n[stderr]\nerror: Individual quota reached. Please upgrade your subscription "
         "to increase your limits. Resets in 147h10m42s.\n"
         'AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429)"}')

mod.CLAUDE_VIA_AGY = {"claude-opus"}
why = mod.agy_quota_error("claude-opus", "", QUOTA)
check("agy 한도 거절을 사유로 적는다", bool(why) and "한도 소진" in why, why)
check("그 사유에 풀리는 시각이 같이 적힌다", bool(why) and "147h10m42s" in why, why)
check("그 사유가 재시도 제외 낱말을 갖는다",
      bool(why) and any(s in why.lower() for s in mod._RETRY_SKIP),
      f"{why} / {mod._RETRY_SKIP} — 안 걸리면 부를 곳이 없는데 한 번 더 부른다")
check("평범한 실패는 이 사유로 안 바뀐다",
      mod.agy_quota_error("claude-opus", "그냥 답", "") is None)
mod.CLAUDE_VIA_AGY = _saved_via
check("클로드 코드 자리는 이 판정을 안 탄다",
      mod.agy_quota_error("claude-opus", "", QUOTA) is None,
      "agy 로 안 부르는 자리의 출력에 같은 글자가 인용돼도 실패로 돌리지 않는다")
check("두 실패 자리가 다 이 판정을 먼저 본다",
      src.count("agy_quota_error(agent, proc.stdout, proc.stderr)") == 2,
      "종료코드 0 쪽만 보면 비정상 종료 회차가 그대로 재시도를 간다")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
