#!/usr/bin/env python3
"""토론의 제미나이 자리 모델 이름을 고정한다.

고정하는 것 셋.

  1. 이름이 gemini-4-argon-high 다. 2026-10-14 운영자 지시로 3.8 플래시에서 올렸고,
     두 호스트 구글 계정을 AI 울트라로 올린 뒤에야 목록에 뜨는 이름이다.
  2. 이름에 추론 강도 접미사가 붙어 있다. 접미사 없는 이름을 넘기면 agy 가 --effort 를
     따로 요구하며 거절하고, 그러면 제미나이 자리가 통째로 빠진다.
  3. 호출이 그 자리의 모델 이름을 --model 로 넘긴다. 안 넘기면 agy 설정 기본값을
     따라가 머신마다 다른 세대로 토론이 돌고, 회차 기록의 모델 이름이 실제와 어긋난다.
     이름은 agy_model(agent) 하나가 정한다 — 제미나이 자리와 agy 로 옮긴 클로드 자리가
     같은 갈래를 쓰므로 그 자리가 둘로 갈리면 한쪽만 고쳐진다.
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


mod = load("ai_debate_gemini_model", REPO / "bin" / "ai-debate")
src = (REPO / "bin" / "ai-debate").read_text(encoding="utf-8")

check("제미나이 자리는 제미나이 4 아르곤 상이다",
      mod.GEMINI_MODEL == "gemini-4-argon-high", mod.GEMINI_MODEL)

check("모델 이름에 추론 강도 접미사가 붙어 있다",
      mod.GEMINI_MODEL.rsplit("-", 1)[-1] in ("high", "medium", "low"),
      f"{mod.GEMINI_MODEL} — 접미사가 없으면 agy 가 --effort 를 요구하며 거절한다")

check("호출이 그 자리의 모델 이름을 --model 로 넘긴다",
      '"--model", _agy_model' in src,
      "안 넘기면 agy 설정 기본값을 따라가 머신마다 다른 세대로 돈다")

check("제미나이 자리의 그 이름이 상수 그대로다",
      mod.agy_model("gemini") == mod.GEMINI_MODEL, mod.agy_model("gemini"))

# 깊은 회차에는 아르곤 옆에 카본 자리를 더한다(운영자 9314). 평소 회차는 아르곤 하나.
check("카본 자리는 gemini-4-carbon-high 로 부른다",
      mod.agy_model("gemini-carbon") == "gemini-4-carbon-high", mod.agy_model("gemini-carbon"))
check("깊은 회차에만 카본 자리가 더해진다",
      "gemini-carbon" in mod.DEEP_POOL_EXTRA and "gemini-carbon" not in mod.POOL, repr(mod.POOL))


# --- 모델 미인식 거절을 사유로 남기고 재시도에서 빼는가 ----------------------
# agy 는 모르는 이름을 조용히 대체하지 않고 거절하면서 쓸 수 있는 목록을 길게 찍는다.
# 그 목록에 사유가 묻히면 읽는 쪽이 "파싱 실패" 로 읽고, 재시도까지 하면 같은 벽에
# 다시 부딪히며 런 재시도 예산만 태운다.
REJECT = ('Error: model "gemini-4-argon-high" is not recognized as a known model.\n'
          "Available models:\n" + "\n".join("  gemini-3.8-flash-%s" % s
                                            for s in ("low", "medium", "high")) * 3)

err = mod.gemini_model_error("gemini", "", REJECT)
check("거절이 표준오류로 올 때 사유를 남긴다",
      err and "미인식" in err and mod.GEMINI_MODEL in err, repr(err))

err_out = mod.gemini_model_error("gemini", REJECT, "")
check("거절이 표준출력으로 올 때도 같다", err_out == err, repr(err_out))

check("원인을 계정 등급 하나로 단정하지 않는다",
      err and "등급" in err and "판본" in err, repr(err))

check("평범한 실패는 모델 미인식으로 분류하지 않는다",
      mod.gemini_model_error("gemini", "", "connection reset by peer") is None)

check("성공한 응답이 그 문구를 인용만 한 것은 거절이 아니다",
      mod.gemini_model_error("gemini", "", "") is None,
      "성공 경로는 이 함수를 아예 안 탄다 — 실패한 호출의 출력만 본다")

check("다른 자리의 실패에는 안 걸린다",
      mod.gemini_model_error("codex", "", REJECT) is None)

check("미인식은 재시도에서 빠진다",
      any(s in err.lower() for s in mod._RETRY_SKIP),
      f"_RETRY_SKIP={mod._RETRY_SKIP}")

check("두 실패 경로가 다 이 분류를 먼저 본다",
      src.count("gemini_model_error(agent, proc.stdout, proc.stderr)") == 2,
      "비정상 종료와 파싱 실패 둘 다에서 보지 않으면 한쪽이 그냥 'parse failed' 로 남는다")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
