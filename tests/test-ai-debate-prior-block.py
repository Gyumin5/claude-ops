#!/usr/bin/env python3
"""앞 라운드를 다음 라운드에 넘길 때 발언을 통째로 잃지 않는지 고정한다.

옛 판본은 앞 라운드 출력 전부를 json 으로 만든 뒤 앞에서 1만 자만 남겼다. 넘치는
순간 뒤쪽 참가자 발언이 통째로 사라졌고, 실측 5회차가 전부 잘렸다
(run-20260929T023534Z 는 1라운드 여섯 발언 중 하나만 2라운드로 넘어갔다).
그래서 자르는 방식을 "앞에서 잘라내기" 에서 "참가자별로 몫을 나눠 줄이기" 로 바꿨다.

고정하는 것 여섯.

  1. 예산 안에 들어가면 한 글자도 안 건드린다.
  2. 넘치면 모든 발언이 남는다 — 누가 무슨 역할로 말했는지가 전부 보인다.
  3. 줄인 자리에는 표시가 남는다. 읽는 쪽이 요약본임을 알아야 한다.
  4. 결과가 여전히 유효한 json 이다. 문자열 중간에서 끊어 구조를 깨지 않는다.
  5. 남는 몫은 모자란 쪽에 되돌아간다 — 짧은 발언은 통째로 살고 긴 쪽이 깎인다.
  6. 참가자가 많아 바닥값 합이 예산을 넘으면 예산 쪽을 늘린다. 발언을 버리지 않는다.
"""
import importlib.machinery
import importlib.util
import json
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


mod = load("ai_debate_prior", REPO / "bin" / "ai-debate")

missing = [n for n in ("build_prior_block", "_fair_shares", "_shrink_entry",
                       "PRIOR_BLOCK_LIMIT", "PRIOR_ENTRY_FLOOR", "PRIOR_CUT_MARK")
           if not hasattr(mod, n)]
check("몫 배분 층이 존재한다", not missing, f"없는 이름: {missing}")
if missing:
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(1)

ROLE = "debater"
KEYS = mod.SCHEMAS.get(ROLE, [])
check("검사 전제: debater 스키마에 문자열 칸이 있다", bool(KEYS), f"{KEYS}")


def out(agent, size, role=ROLE, rnd=1):
    """한 발언을 만든다. 첫 칸에 size 글자를 채운다."""
    o = {"_round": rnd, "_agent": agent, "_role": role}
    for i, k in enumerate(mod.SCHEMAS.get(role, [])):
        o[k] = ("가" * size) if i == 0 else "짧은 값"
    return o


def agents_in(block, outs):
    seen = 0
    for o in outs:
        if f'"agent": "{o["_agent"]}"' in block:
            seen += 1
    return seen


# --- 1. 예산 안이면 안 건드린다 ------------------------------------------------
small = [out("codex", 50), out("gemini", 50), out("claude-opus", 50)]
blk = mod.build_prior_block(small)
check("예산 안: 자르기 표시가 없다", mod.PRIOR_CUT_MARK not in blk)
check("예산 안: 전원 들어간다", agents_in(blk, small) == 3, blk[:200])
check("예산 안: 원문 길이가 그대로다",
      ("가" * 50) in blk, "긴 칸이 손상됐다")

# --- 2~4. 넘치면 모두 남기고 줄인다 --------------------------------------------
big = [out("codex", 9000), out("gemini", 9000), out("claude-opus", 9000),
       out("claude-fable", 9000)]
blk = mod.build_prior_block(big)
check("초과: 발언이 하나도 안 사라진다", agents_in(blk, big) == 4,
      f"{agents_in(blk, big)}/4 — 옛 판본은 여기서 뒤쪽이 통째로 사라졌다")
check("초과: 자른 자리에 표시가 남는다", mod.PRIOR_CUT_MARK.strip() in blk)
body = blk.split("\n", 1)[1]
try:
    parsed = json.loads(body)
    ok_json = isinstance(parsed, list) and len(parsed) == 4
except Exception as exc:  # noqa: BLE001
    parsed, ok_json = [], False
    print(f"      json 파싱 실패: {exc}")
check("초과: 결과가 유효한 json 이다", ok_json)
if ok_json:
    check("초과: 각 발언이 누가·무슨 역할인지 들고 있다",
          all(e.get("agent") and e.get("role") for e in parsed),
          f"{[(e.get('agent'), e.get('role')) for e in parsed]}")
check("초과: 예산을 크게 넘지 않는다",
      len(blk) <= mod.PRIOR_BLOCK_LIMIT * 1.2,
      f"{len(blk)} > {mod.PRIOR_BLOCK_LIMIT} * 1.2")

# --- 5. 남는 몫이 모자란 쪽에 되돌아간다 ---------------------------------------
mixed = [out("codex", 30), out("gemini", 30), out("claude-opus", 20000)]
blk = mod.build_prior_block(mixed)
# 머리말에도 표시 글자가 한 번 나오므로 본문만 센다.
mixed_body = blk.split("\n", 1)[1]
check("되돌림: 짧은 발언은 통째로 산다",
      mixed_body.count(mod.PRIOR_CUT_MARK.strip()) == 1,
      f"자른 자리 {mixed_body.count(mod.PRIOR_CUT_MARK.strip())}곳 — 짧은 쪽까지 깎였다")
check("되돌림: 짧은 쪽 원문이 온전하다", ("가" * 30) in blk)

shares = mod._fair_shares([10, 10, 1000], 300)
check("배분: 짧은 쪽은 필요한 만큼 다 받는다", shares[0] == 10 and shares[1] == 10, f"{shares}")
check("배분: 남은 몫이 긴 쪽으로 간다", shares[2] == 280, f"{shares}")
check("배분: 합이 예산을 안 넘는다", sum(shares) <= 300, f"{shares}")
check("배분: 예산이 남으면 모두 필요한 만큼 받는다",
      mod._fair_shares([10, 20, 30], 1000) == [10, 20, 30],
      f"{mod._fair_shares([10, 20, 30], 1000)}")

# --- 6. 참가자가 많으면 예산을 늘린다 ------------------------------------------
many = [out(f"a{i}", 5000) for i in range(40)]
blk = mod.build_prior_block(many)
check("많은 참가자: 하나도 안 사라진다", agents_in(blk, many) == 40,
      f"{agents_in(blk, many)}/40")
check("많은 참가자: 바닥값 합만큼 예산이 늘어난다",
      len(blk) >= mod.PRIOR_ENTRY_FLOOR * 40 * 0.5,
      f"{len(blk)} — 바닥값을 못 지켰다")

# --- 경계 ---------------------------------------------------------------------
check("빈 입력은 빈 문자열", mod.build_prior_block([]) == "")
check("실패한 호출은 건너뛴다",
      mod.build_prior_block([{"_error": "x", "_agent": "codex"}]) == "")
one = [out("codex", 40000)]
blk = mod.build_prior_block(one)
check("혼자여도 남는다", agents_in(blk, one) == 1)
check("혼자여도 예산을 지킨다", len(blk) <= mod.PRIOR_BLOCK_LIMIT * 1.2, f"{len(blk)}")

# --- 원문 계약 ----------------------------------------------------------------
src = (REPO / "bin" / "ai-debate").read_text(encoding="utf-8")
check("원문: 통째 앞자르기가 남아 있지 않다",
      "indent=2)[:10000]" not in src,
      "앞에서 1만 자만 남기는 옛 경로가 그대로면 이 고침이 무의미하다")
check("원문: 라운드 호출이 build_prior_block 을 쓴다", "build_prior_block(" in src)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
