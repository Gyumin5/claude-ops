#!/usr/bin/env python3
"""정리자 판정문의 언어 계약 검사.

2026-09-17 옆 기계 3회차에서 정리자의 final_decision 이 일본어로 나왔다. 프롬프트에
한국어로 쓰라는 지시가 이미 있었는데도 그랬다. 운영자가 그대로 읽는 자리라 지시 하나에
맡기지 않고 셋을 고정한다.

  1. 지시 — 정리자 프롬프트가 언어를 별도 문장으로 못박는다.
  2. 판정 — 답의 어느 칸에 일본어 글자가 섞였는지 이름으로 집어낸다. 한국어 답과
     영문 식별자가 섞인 답은 잡지 않는다.
  3. 처리 — 걸리면 정리자를 한 번 더 부른다. 두 번째가 한국어면 그것을 쓰고, 두 번째도
     아니면 답을 버리지 않고 표시를 남겨 사람이 보게 한다. 답을 버리면 회차가 결론
     없이 끝나므로 그게 더 나쁘다.
"""
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
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


mod = load("ai_debate_lang", REPO / "bin" / "ai-debate")

# 고치기 전 판본에는 이 이름이 없다 — 크래시 대신 실패로 떨어지게 기본값으로 받는다.
_fields = getattr(mod, "non_korean_answer_fields", None)

# 실제 계측 로그 크기. 검사가 여기에 한 줄도 안 보태야 한다.
_real_log = getattr(mod, "LOG_PATH", "")
_log_size0 = os.path.getsize(_real_log) if _real_log and os.path.exists(_real_log) else 0


def kana_fields(out):
    return _fields(out) if _fields else []


# ── 1. 지시 ────────────────────────────────────────────────────────────
p = mod.role_prompt("arbiter", "무엇을 할까", "", "")
check("정리자 프롬프트가 한국어로 쓰라고 적는다", "한국어" in p, p[-300:])
check("다른 언어로 답하지 말라고 못박는다", "일본어" in p, p[-300:])

# ── 2. 판정 ────────────────────────────────────────────────────────────
JA = "この変更は妥当だと判断する"
KO = "이 변경은 타당하다고 판단한다"

check("일본어 칸을 이름으로 집어낸다",
      kana_fields({"final_decision": JA, "rationale": KO}) == ["final_decision"],
      str(kana_fields({"final_decision": JA, "rationale": KO})))
check("여러 칸이면 전부 집어낸다",
      kana_fields({"final_decision": JA, "rationale": JA}) == ["final_decision", "rationale"],
      str(kana_fields({"final_decision": JA, "rationale": JA})))
check("목록 안에 섞여 있어도 잡는다",
      kana_fields({"action_steps": [KO, JA]}) == ["action_steps"],
      str(kana_fields({"action_steps": [KO, JA]})))
check("한국어 답은 안 잡는다", kana_fields({"final_decision": KO, "rationale": KO}) == [],
      str(kana_fields({"final_decision": KO, "rationale": KO})))
check("영문 식별자가 섞여도 안 잡는다",
      kana_fields({"final_decision": "bin/ai-debate 의 role_prompt 를 고친다"}) == [],
      str(kana_fields({"final_decision": "bin/ai-debate 의 role_prompt 를 고친다"})))
check("계측용 칸은 안 본다", kana_fields({"_raw": JA, "final_decision": KO}) == [],
      str(kana_fields({"_raw": JA, "final_decision": KO})))
# 한자는 일부러 안 센다. 한국어 문장에도 인용으로 섞이고, 일본어 판정의 필요조건도
# 아니라 오탐만 는다. 한자 자체를 막는 것은 이 가드가 아니라 응답 원칙의 몫이다.
check("한자만 있는 답은 이 가드가 안 잡는다",
      kana_fields({"final_decision": "妥当 하다고 본다"}) == [] if _fields else False,
      str(kana_fields({"final_decision": "妥当 하다고 본다"})))

# ── 3. 처리 ────────────────────────────────────────────────────────────
real_call = mod.call_agent
outs = [{"_agent": "codex", "_role": "debater", "_round": 1, "position": "의견"}]


def run_with(answers):
    """정리자 호출을 answers 로 갈아끼우고 run_arbiter 를 돌린다. (결과, 호출기록)"""
    seen = []

    def fake(agent, role, prompt, run_dir, label):
        seen.append({"label": label, "prompt": prompt})
        return dict(answers[len(seen) - 1])

    mod.call_agent = fake
    keep = mod.LOG_PATH
    try:
        with tempfile.TemporaryDirectory() as tmp:
            # run_arbiter 는 계측을 남긴다. 기본 경로로 두면 검사가 실제 계측 로그에
            # 가짜 줄을 쌓는다 — 홈에서 28건이 그렇게 들어갔다.
            mod.LOG_PATH = str(Path(tmp) / "calls.jsonl")
            return mod.run_arbiter("무엇을 할까", "", outs, Path(tmp)), seen
    finally:
        mod.call_agent = real_call
        mod.LOG_PATH = keep


got, seen = run_with([{"final_decision": KO, "rationale": KO}])
check("한국어면 한 번만 부른다", len(seen) == 1, str([s["label"] for s in seen]))
check("한국어면 그 답을 그대로 쓴다", got.get("final_decision") == KO, str(got))

got, seen = run_with([{"final_decision": JA}, {"final_decision": KO}])
check("일본어면 한 번 더 부른다", len(seen) == 2, str([s["label"] for s in seen]))
check("다시 부를 때 한국어로 쓰라고 덧붙인다",
      len(seen) == 2 and "다시" in seen[1]["prompt"] and "한국어로 다시" in seen[1]["prompt"],
      seen[-1]["prompt"][-200:] if seen else "")
check("두 번째가 한국어면 그것을 쓴다", got.get("final_decision") == KO, str(got))
check("다시 불렀다는 표시를 남긴다", got.get("_lang_retried") is True, str(got))

got, seen = run_with([{"final_decision": JA, "rationale": KO}, {"final_decision": JA}])
check("두 번째도 일본어면 세 번은 안 부른다", len(seen) == 2, str([s["label"] for s in seen]))
check("그래도 결론을 버리지 않는다", got.get("final_decision") == JA, str(got))
check("못 고쳤다는 표시를 남긴다", got.get("_lang_bad") == ["final_decision"], str(got))

got, seen = run_with([{"_error": "실패"}])
check("실패한 정리자는 언어 판정에 안 걸린다",
      len(seen) == 1 and "_lang_bad" not in got, str(got))

# 표시가 사람 눈에 닿는가 — 결론은 그대로 찍히고 경고가 앞에 선다.
lines = []
with tempfile.TemporaryDirectory() as tmp:
    ok = mod.render_arbiter({"final_decision": JA, "_lang_bad": ["final_decision"]},
                            Path(tmp), outs, lines.append)
text = "\n".join(lines)
check("표시가 있으면 경고를 찍는다", "한국어가 아니다" in text, text[:300])
check("경고를 찍어도 결론은 실패가 아니다", ok is True and JA in text, text[:300])

# 정리자 재호출은 계측을 남긴다. 그 줄이 실제 로그로 새면 집계 표본이 가짜로 부푼다.
_log_size1 = os.path.getsize(_real_log) if _real_log and os.path.exists(_real_log) else 0
check("검사가 실제 계측 로그에 한 줄도 안 보탠다", _log_size1 == _log_size0,
      f"{_real_log} {_log_size0} -> {_log_size1}")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
