#!/usr/bin/env python3
"""짝 없는 중괄호 하나가 뒤에 있는 답을 통째로 가리지 않게 한다.

왜 있나: 2026-09-16 옆 기계 run-20260916T001520Z 에서 코덱스가 낸 답 넷이 전부
버려지고 최고노력으로 재호출됐다(코덱스 1,117.5초). 원문 넷 다 끝에 스키마가
정확히 맞는 JSON 이 있고 파이썬 기본 파서로 그대로 읽히는데, 도구 자신의 파서만
None 을 냈다.

원인은 스캐너가 짝 없는 여는 중괄호를 만났을 때의 처리였다. 짝을 찾을 때까지
전진하다 글 끝에 닿으면 거기서 스캔이 끝났다 — 그 뒤는 안 본다. 참여자가 줄번호
붙은 코드 발췌를 인용하면 중괄호가 안 닫히므로 이 상황이 흔하다. 실제로 95,108자
중 70,960자 지점에서 스캔이 멎었고 진짜 답은 87,757자 지점에 있었다.

곁들여 같은 회차에 '인증 만료 — 재로그인 필요' 오진도 났다. 인증 신호로 읽힌
401·429 는 코덱스가 인용한 C++ 소스의 줄번호였다.

그래서 고정하는 것 셋: 짝 없는 중괄호 뒤의 답을 찾는다, 인용된 조각이 진짜 답을
이기지 않는다, 숫자만으로 인증·쿼터를 판정하지 않되 진짜 오류는 그대로 잡는다.
"""
import importlib.machinery
import importlib.util
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
results = []


def check(name, cond, detail: object = ""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name +
          (("\n      " + str(detail)) if detail and not cond else ""))


loader = importlib.machinery.SourceFileLoader("ai_debate_brace", str(REPO / "bin" / "ai-debate"))
spec = importlib.util.spec_from_loader("ai_debate_brace", loader)
assert spec is not None
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)

ANSWER = {"position": "이 갈래로 간다", "reasoning": "근거 한 줄",
          "risks": "없음", "confidence": "높음"}

# 옆 기계 회차의 모양 그대로 — 줄번호 붙은 C++ 발췌라 여는 중괄호가 안 닫힌다.
QUOTED_CODE = """다음 자리가 문제다.

  401  void GridMap::update(const Cell& c) {
  402      if (!c.valid()) return;
  429      for (auto& n : neighbors_) {
  430          n.accumulate(c);

여기서 누수가 난다. 결론은 아래 JSON 이다.
"""


def wrap(answer):
    return QUOTED_CODE + json.dumps(answer, ensure_ascii=False)


print("스캐너")
text = wrap(ANSWER)
check("짝 없는 중괄호 뒤에 있는 답을 찾는다",
      mod.parse_json_loose(text, "debater") == ANSWER,
      mod.parse_json_loose(text, "debater"))
check("파이썬 기본 파서가 읽는 답은 이 파서도 읽는다",
      mod.parse_json_loose(json.dumps(ANSWER), "debater") == ANSWER)

# 짝 없는 것이 여럿이어도 같다.
many = (QUOTED_CODE * 3) + json.dumps(ANSWER, ensure_ascii=False)
check("짝 없는 중괄호가 여러 개여도 답을 찾는다",
      mod.parse_json_loose(many, "debater") == ANSWER)

# 답 뒤에 또 미닫힘이 와도 이미 찾은 것을 잃지 않는다.
check("답 뒤에 미닫힘이 와도 답이 남는다",
      mod.parse_json_loose(text + "\n추신: 다음 회차에 {아직 안 닫은 메모", "debater") == ANSWER)

print("\n어느 조각을 고르나")
# 인용된 남의 역할 모양 조각이 앞에 있고 진짜 답이 뒤에 있는 배치.
quoted_other = json.dumps({"should_proceed": True, "concerns": "인용된 조각"},
                          ensure_ascii=False)
check("기대 스키마가 인용된 남의 모양을 이긴다",
      mod.parse_json_loose(quoted_other + "\n" + wrap(ANSWER), "debater") == ANSWER)
# 답 안에 들어 있는 부분 객체가 답 자체를 이기면 안 된다.
nested = {"position": "간다", "reasoning": "근거",
          "meta": {"position": "이건 답이 아니라 안에 든 것"}}
got = mod.parse_json_loose(QUOTED_CODE + json.dumps(nested, ensure_ascii=False), "debater")
check("답 안에 든 부분 객체가 답을 이기지 않는다", got == nested, got)

print("\n짝 없는 따옴표")
# 한 번 훑기만 넣으면 실패 자리가 중괄호에서 따옴표로 옮겨간다. 앞 구간의 홀수
# 번째 따옴표 하나가 뒤 전체를 문자열 안으로 삼킨다 — 옆 기계가 전수 대조로 잡은
# 퇴행 여섯의 기전이 이것이다.
ODD_QUOTE = '참여자가 말하길 "여기서 따옴표를 안 닫았다\n다음 줄로 넘어간다.\n'
check("앞 구간의 안 닫힌 따옴표가 뒤의 답을 안 삼킨다",
      mod.parse_json_loose(ODD_QUOTE + json.dumps(ANSWER, ensure_ascii=False), "debater") == ANSWER,
      mod.parse_json_loose(ODD_QUOTE + json.dumps(ANSWER, ensure_ascii=False), "debater"))
check("따옴표와 중괄호가 둘 다 안 닫혀도 답을 찾는다",
      mod.parse_json_loose(ODD_QUOTE + wrap(ANSWER), "debater") == ANSWER)
# 줄바꿈으로도 못 푸는 배치. 한 줄 안에서 따옴표가 어긋나 답까지 삼킨다.
ONE_LINE = '로그: "열고 안 닫은 따옴표 ' + json.dumps(ANSWER, ensure_ascii=False)
check("한 줄 안에서 삼켜져도 되짚기로 건진다",
      mod.parse_json_loose(ONE_LINE, "debater") == ANSWER,
      mod.parse_json_loose(ONE_LINE, "debater"))
check("빈 사전은 답으로 안 쓴다",
      mod.parse_json_loose('앞 {} 뒤 ' + json.dumps(ANSWER, ensure_ascii=False), "debater") == ANSWER)
check("빈 사전뿐이면 답이 없다고 본다", mod.parse_json_loose("결과 {} 끝", "debater") is None,
      mod.parse_json_loose("결과 {} 끝", "debater"))

print("\n큰 원문에서도 끝난다")
# 옆 기계 원문과 같은 규모(약 95KB)에서 실용 시간 안에 끝나는지.
big = (QUOTED_CODE * 1200) + json.dumps(ANSWER, ensure_ascii=False)
t0 = time.monotonic()
got = mod.parse_json_loose(big, "debater")
took = time.monotonic() - t0
check(f"{len(big):,}자를 {took:.2f}초에 훑고 답을 찾는다", got == ANSWER and took < 5.0,
      f"{took:.2f}초 / {got}")

print("\n진단 — 숫자만으로 판정하지 않는다")
sigs = mod._HEALTH_SIGS


def label_for(blob):
    import re as _re
    for rx, lab, _fix in sigs:
        if _re.search(rx, blob, _re.IGNORECASE):
            return lab
    return None


check("인용된 코드의 줄번호를 인증 만료로 읽지 않는다", label_for(QUOTED_CODE) is None,
      label_for(QUOTED_CODE))
check("진짜 인증 실패는 그대로 잡는다",
      label_for("stream error: unexpected status 401 Unauthorized") == "인증 만료/실패")
check("토큰 만료 문구도 그대로 잡는다",
      label_for("error: invalid_grant (token expired)") == "인증 만료/실패")
check("진짜 레이트리밋은 그대로 잡는다",
      label_for("HTTP 429 Too Many Requests") == "쿼터/레이트리밋")
check("한도 소진이 인증보다 먼저다",
      label_for("You've hit your usage limit. 401") == "사용 한도 소진")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
