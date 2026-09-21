#!/usr/bin/env python3
"""브리프가 ai-debate 자신을 지목한 회차에서 도구가 자기 규칙에 걸리지 않는지 검사.

2026-09-15 실회차(run-20260915T055119Z)에서 결함 둘이 같은 뿌리로 터졌다. 참여자가
검토 대상인 이 도구의 소스를 답변에 인용하면, 그 답변 안에 우리 진단 규칙과 스키마
예시가 글자 그대로 들어온다.

하나. 증상 정규식이 자기 정의 줄에 매칭돼 '파일 읽기 불가(샌드박스)' 오탐이 찍혔다.
코덱스는 정상으로 읽은 회차였다. 같은 위험이 인증 실패 정규식에도 있다.

둘. 더 나쁜 쪽. 중재 판정문이 온전히 나왔는데 본문에 인용된 debater 모양 조각이
채택돼 arbiter.normalized.json 이 엉뚱한 모양이 됐고 판정이 통째로 버려졌다.
"""
import importlib.machinery
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


SRC = REPO / "bin" / "ai-debate"
mod = load("ai_debate_selfmatch", SRC)
src_lines = SRC.read_text(encoding="utf-8").splitlines()


def source_line_containing(needle):
    """이 도구 소스에서 그 문자열이 든 첫 줄. 참여자가 인용한 상황을 그대로 만든다."""
    for ln in src_lines:
        if needle in ln:
            return ln
    raise AssertionError(f"소스에 {needle} 이 없다 — 검사가 낡았다")


SANDBOX_DEF = source_line_containing("_SANDBOX_SIG = ")
AUTH_DEF = source_line_containing("invalid_grant|reauth")


# ── 1. 진단 규칙이 자기 정의 줄에 안 걸린다 ────────────────────────────
check("검사가 쓰는 두 줄이 실제 소스에서 왔다",
      "bwrap" in SANDBOX_DEF and "invalid_grant" in AUTH_DEF,
      f"{SANDBOX_DEF[:60]} / {AUTH_DEF[:60]}")

with tempfile.TemporaryDirectory() as tmp:
    run_dir = Path(tmp)
    quoted = ("참여자 답변이다. 검토 대상 소스에서 이 줄을 인용한다.\n"
              f"{SANDBOX_DEF}\n{AUTH_DEF}\n"
              '{"position": "괜찮다", "should_proceed": true}\n')
    (run_dir / "r1.debater.codex.raw.txt").write_text(quoted, encoding="utf-8")
    (run_dir / "r1.debater.codex.prompt.txt").write_text("브리프", encoding="utf-8")

    blob = mod._diag_blob(run_dir, "codex")
    check("인용된 자기 소스 줄은 진단 본문에서 빠진다",
          "_SANDBOX_SIG" not in blob and "invalid_grant" not in blob, blob[:200])
    check("참여자가 실제로 쓴 글은 남는다", "참여자 답변이다" in blob, blob[:200])
    check("자기 정의 인용으로 원인이 잡히지 않는다",
          mod._scan_raw_for_cause(run_dir, "codex") is None,
          str(mod._scan_raw_for_cause(run_dir, "codex")))

    ok_out = [{"_agent": "codex", "_role": "debater"}]
    lines = mod.provider_health_lines(run_dir, ok_out)
    check("자기 정의 인용만으로 샌드박스 결함을 찍지 않는다",
          not any("샌드박스" in ln for ln in lines), str(lines))

    # 진짜 고장은 여전히 잡아야 한다 — 오탐을 없애려다 미탐을 만들면 더 나쁘다.
    (run_dir / "r1.debater.codex.raw.txt").write_text(
        quoted + "bwrap: Creating new namespace failed: Operation not permitted\n",
        encoding="utf-8")
    lines2 = mod.provider_health_lines(run_dir, ok_out)
    check("진짜 샌드박스 고장은 그대로 잡는다",
          any("샌드박스" in ln for ln in lines2), str(lines2))

    (run_dir / "r1.debater.codex.raw.txt").write_text(
        quoted + "error: invalid_grant\n", encoding="utf-8")
    fail_out = [{"_agent": "codex", "_role": "debater", "_error": "codex exit 1"}]
    cause = mod._scan_raw_for_cause(run_dir, "codex")
    check("진짜 인증 실패는 그대로 잡는다", cause and cause[0] == "인증 만료/실패", str(cause))
    check("실패 집계 줄이 그 원인을 쓴다",
          any("인증 만료/실패" in ln for ln in mod.provider_health_lines(run_dir, fail_out)),
          str(mod.provider_health_lines(run_dir, fail_out)))


# ── 2. 기대 스키마로 판정문을 고른다 ───────────────────────────────────
ARB = {"final_decision": "고쳐라", "rationale": "근거", "action_steps": ["하나"],
       "verification_plan": "검사", "dissent": "없음"}
QUOTED_DEBATER = {"position": "인용된 예시", "should_proceed": True, "roles": ["debater"],
                  "reasoning": "이건 참여자 답변을 인용한 조각이다"}

raw_arb = ("중재자 본문이다. 앞 라운드 참여자 출력을 이렇게 인용한다.\n"
           + json.dumps(QUOTED_DEBATER, ensure_ascii=False)
           + "\n\n최종 판정은 아래와 같다.\n"
           + json.dumps(ARB, ensure_ascii=False) + "\n")

got_union = mod.parse_json_loose(raw_arb)
got_arb = mod.parse_json_loose(raw_arb, "arbiter")
check("기대 스키마를 주면 판정문을 고른다",
      got_arb.get("final_decision") == "고쳐라", json.dumps(got_arb, ensure_ascii=False)[:200])
check("판정문을 고르면 인용 조각의 키가 안 섞인다",
      "should_proceed" not in got_arb, json.dumps(got_arb, ensure_ascii=False)[:200])
check("기대 스키마 없이도 최소한 뒤에 나온 것을 고른다",
      got_union.get("final_decision") == "고쳐라",
      json.dumps(got_union, ensure_ascii=False)[:200])

# 순서를 뒤집어도 스키마가 이긴다 — 위치만으로 고르면 판정문이 앞에 온 회차에서 진다.
raw_rev = (json.dumps(ARB, ensure_ascii=False) + "\n덧붙이자면 참여자는 이렇게 적었다.\n"
           + json.dumps(QUOTED_DEBATER, ensure_ascii=False) + "\n")
check("판정문이 먼저 나와도 스키마로 고른다",
      mod.parse_json_loose(raw_rev, "arbiter").get("final_decision") == "고쳐라",
      json.dumps(mod.parse_json_loose(raw_rev, "arbiter"), ensure_ascii=False)[:200])

# 참여자 갈래도 같은 보호를 받는다.
raw_deb = ("예시는 이렇다.\n" + json.dumps(ARB, ensure_ascii=False)
           + "\n내 입장은 이것이다.\n"
           + json.dumps({"position": "반대", "reasoning": "근거", "risks": ["하나"]},
                        ensure_ascii=False) + "\n")
got_deb = mod.parse_json_loose(raw_deb, "debater")
check("debater 를 기대하면 판정문 모양을 안 집는다",
      got_deb.get("position") == "반대", json.dumps(got_deb, ensure_ascii=False)[:200])

# 플래너는 SCHEMAS 에 없는 모양이라 키 목록으로 넘긴다.
raw_plan = ('참고로 예시 출력은 {"position": "x", "should_proceed": true} 이다.\n'
            '{"roles": ["debater", "risk_officer"], "reasoning": "짧게"}\n')
got_plan = mod.parse_json_loose(raw_plan, ["roles", "reasoning"])
check("플래너는 역할 목록 쪽을 고른다", got_plan.get("roles") == ["debater", "risk_officer"],
      json.dumps(got_plan, ensure_ascii=False)[:200])

check("후보가 없으면 그대로 없다", mod.parse_json_loose("JSON 이 없는 글", "arbiter") is None)
check("빈 입력도 견딘다", mod.parse_json_loose("", "arbiter") is None)

# 기대 스키마 이름이 낯설면 합집합으로 돌아간다 — 새 역할이 생겨도 안 깨진다.
check("모르는 역할 이름은 합집합으로 떨어진다",
      mod.parse_json_loose(raw_arb, "없는역할").get("final_decision") == "고쳐라")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
