#!/usr/bin/env python3
"""깊은 모드(--deep)가 라운드만이 아니라 능력 층까지 올리는지 고정한다.

라운드만 늘리는 깊은 모드는 같은 능력으로 더 오래 말하는 것이라, 능력에서 막힌 문제는
라운드를 늘려도 그대로 막힌다. 그래서 --deep 은 참가자와 모델을 같이 올린다.

고정하는 것 일곱.

  1. 평소 회차는 하나도 안 바뀐다. 참가자 셋, 클로드 강도 high, 코덱스 모델은 설정 파일값,
     코덱스에 모델 인자를 안 넘긴다(설정 파일이 단일 출처로 남는다).
  2. 깊은 회차는 클로드 자리가 둘이다. 평소 자리가 Opus 이므로 깊게 갈 때 Fable 을 더해
     둘을 같이 듣는다 — 공개 시험은 Opus 가 앞서는데 "어렵고 열린 문제에서는 Fable" 이라는
     평이 정확히 이 모드가 쓰이는 자리라, 자료로 못 가르는 갈림을 토론에 넣는다.
  3. 깊은 회차는 코덱스 자리도 둘이다. 평소 자리(설정 파일값)에 gpt-6-astra 를 더한다.
     갈아끼우지 않는 이유는 실측이다 — 같은 브리프 셋을 두 모델에 나란히 돌렸더니 한쪽만
     찾은 검증 가능한 지적이 각각 넷이었다(2026-09-30). 어느 쪽을 고르든 넷을 잃는다.
  4. 깊은 회차도 추론 강도는 평소와 같은 high 다(2026-09-29 운영자 지시). 강도를 올리면
     콜마다 30초쯤 붙고 깊은 회차는 콜이 라운드당 열이라 그게 라운드 수만큼 곱해진다.
     능력은 모델과 참가자 수, 라운드 수로 올린다.
  5. 정리자는 자리가 하나다. 깊은 회차에서도 설정 파일값이고 강도도 라운드와 같다.
     둘이 갈리면 올린 만큼이 정리 단계에서 깎이거나 반대로 정리자만 비싸진다.
  6. 캐시 키가 두 층을 가른다. 라운드 수만으로는 안 갈린다 — `--max-rounds 3` 과 `--deep`
     이 같은 3 이 되어, 평소 회차의 결론이 깊은 회차의 답으로 24시간 동안 나온다.
  7. 코덱스 자리 이름은 앞가지 "codex" 를 지킨다. 한도 판정·사용량 계측·호출 갈래가 전부
     is_codex 로 갈리므로, 다른 이름을 쓰면 새 자리가 코덱스가 아닌 것으로 취급된다.

deep_on() 은 전역을 바꾸므로 검사마다 원상복구한다. 안 그러면 검사 순서가 결과를 바꾼다.
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


mod = load("ai_debate_deep", REPO / "bin" / "ai-debate")
BASE_POOL = list(mod.POOL)


def reset():
    mod.POOL = list(BASE_POOL)
    mod.DEEP = False


# 고치기 전 판본에는 이 이름들이 없다 — 크래시 대신 실패로 떨어지게 먼저 본다.
missing = [n for n in ("deep_on", "claude_effort", "codex_round_effort",
                       "codex_arbiter_effort", "effective_codex_model",
                       "agent_codex_model", "is_codex",
                       "DEEP_POOL_EXTRA", "DEEP_CLAUDE_EFFORT", "CODEX_MODELS")
           if not hasattr(mod, n)]
check("깊은 모드 층이 존재한다", not missing, f"없는 이름: {missing}")
if missing:
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(1)

# --- 1. 평소 회차는 안 바뀐다 -------------------------------------------------
reset()
mod.deep_on(False)
check("평소: 참가자 셋", mod.POOL == BASE_POOL, f"{mod.POOL}")
check("평소: 클로드 자리는 opus 하나",
      [a for a in mod.POOL if a.startswith("claude")] == ["claude-opus"], f"{mod.POOL}")
check("평소: 코덱스 자리는 하나",
      [a for a in mod.POOL if mod.is_codex(a)] == ["codex"], f"{mod.POOL}")
check("평소: 그 자리가 opus 모델로 풀린다",
      mod.CLAUDE_MODELS.get("claude-opus", "").startswith("claude-opus"),
      mod.CLAUDE_MODELS.get("claude-opus"))
check("평소: 클로드 강도 high", mod.claude_effort() == "high", mod.claude_effort())
check("평소: 코덱스 라운드 강도가 상수 그대로",
      mod.codex_round_effort() == mod.CODEX_EFFORT_ROUND, mod.codex_round_effort())
check("평소: 코덱스 모델은 설정 파일값",
      mod.effective_codex_model() == mod.codex_model(),
      f"{mod.effective_codex_model()} != {mod.codex_model()}")
check("평소: 그 자리엔 넘길 모델이 정해져 있지 않다",
      mod.CODEX_MODELS.get("codex") is None,
      "값이 있으면 평소 회차가 설정 파일을 안 따른다")
check("평소: 그래서 자리 모델도 설정 파일값으로 풀린다",
      mod.agent_codex_model("codex") == mod.codex_model(),
      f"{mod.agent_codex_model('codex')}")

# --- 2~5. 깊은 회차 -----------------------------------------------------------
reset()
pool = mod.deep_on(True)
claude_seats = [a for a in pool if a.startswith("claude")]
codex_seats = [a for a in pool if mod.is_codex(a)]
check("깊게: 참가자가 여섯(카본 포함)", len(pool) == 6 and "gemini-carbon" in pool, f"{pool}")
check("깊게: 클로드 자리가 둘", len(claude_seats) == 2, f"{claude_seats}")
check("깊게: fable 과 opus 가 같이 있다",
      "claude-fable" in claude_seats and "claude-opus" in claude_seats, f"{claude_seats}")
check("깊게: 두 클로드 자리의 모델 이름이 다르다",
      len({mod.CLAUDE_MODELS[a] for a in claude_seats}) == 2,
      f"{[mod.CLAUDE_MODELS.get(a) for a in claude_seats]}")
check("깊게: 코덱스 자리도 둘", len(codex_seats) == 2, f"{codex_seats}")
check("깊게: 평소 자리는 그대로 남는다", "codex" in codex_seats, f"{codex_seats}")
check("깊게: 더해진 자리가 astra 다",
      mod.agent_codex_model("codex-astra") == "gpt-6-astra",
      f"{mod.agent_codex_model('codex-astra')}")
check("깊게: 두 코덱스 자리의 모델이 다르다 — 갈아끼운 게 아니라 더한 것이다",
      mod.agent_codex_model("codex") != mod.agent_codex_model("codex-astra"),
      "설정 파일이 이미 astra 면 두 자리가 같은 모델이 된다")
check("깊게: 코덱스 자리 이름이 전부 앞가지 codex 를 지킨다",
      all(mod.is_codex(a) for a in codex_seats)
      and all(a in mod.CODEX_MODELS for a in codex_seats),
      f"{codex_seats} vs {list(mod.CODEX_MODELS)}")
# 2026-11-11 운영자 지시: 깊은 회차의 클로드·코덱스 라운드 자리는 xhigh, 정리자는 high.
check("깊게: 클로드 강도 xhigh", mod.claude_effort() == "xhigh", mod.claude_effort())
check("깊게: 코덱스 라운드 강도 xhigh", mod.codex_round_effort() == "xhigh",
      mod.codex_round_effort())
check("깊게: 정리자는 안 올린다 — high 그대로",
      mod.codex_arbiter_effort() == "high", mod.codex_arbiter_effort())
check("깊게: 기록에 코덱스 모델 이름이 둘 다 들어간다",
      mod.effective_codex_model().count(",") == 1
      and "gpt-6-astra" in mod.effective_codex_model(),
      mod.effective_codex_model())

# 두 번 불러도 참가자가 늘어나지 않는다(재진입 안전).
mod.deep_on(True)
check("깊게: 두 번 켜도 참가자가 안 늘어난다", len(mod.POOL) == 6, f"{mod.POOL}")

# --- 6. 캐시 키가 두 층을 가른다 ----------------------------------------------
import hashlib
import json


def key(deep, rounds):
    reset()
    mod.deep_on(deep)
    src = json.dumps({
        "task": "같은 질문",
        "max_rounds": rounds,
        "with_memory": False,
        "brief_state": "",
        "tier": {"deep": mod.DEEP, "pool": mod.POOL,
                 "claude_effort": mod.claude_effort(),
                 "codex_model": mod.effective_codex_model(),
                 "gemini_model": mod.gemini_model(),
                 "claude_models": [mod.CLAUDE_MODELS[a] for a in mod.POOL
                                   if a in mod.CLAUDE_MODELS]},
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(src.encode("utf-8")).hexdigest()[:32]


check("캐시: 같은 질문이라도 층이 다르면 키가 다르다",
      key(False, 3) != key(True, 3),
      "라운드 수가 같으면 평소 결론이 깊은 회차의 답으로 나온다")
check("캐시: 같은 층·같은 질문은 키가 같다", key(True, 3) == key(True, 3))

# 모델 세대가 바뀌면 키도 바뀌어야 한다. 풀 이름("gemini")은 자리 이름이라 그 자리에
# 어떤 세대가 들어갔는지를 안 가린다 — 모델을 올린 날 같은 브리프를 다시 던지면 옛
# 세대가 답한 결론이 24시간 동안 그대로 나온다.
_before = key(False, 3)
_saved = mod.GEMINI_MODEL
mod.GEMINI_MODEL = "gemini-3.8-flash-high"
_after = key(False, 3)
mod.GEMINI_MODEL = _saved
check("캐시: 제미나이 세대가 바뀌면 키가 바뀐다", _before != _after,
      "자리 이름만 넣으면 옛 세대의 결론이 24시간 그대로 나온다")

_saved_m = dict(mod.CLAUDE_MODELS)
mod.CLAUDE_MODELS = {k: v + "-old" for k, v in _saved_m.items()}
_after_c = key(False, 3)
mod.CLAUDE_MODELS = _saved_m
check("캐시: 클로드 세대가 바뀌면 키가 바뀐다", _before != _after_c)

# 실제 코드가 정말 그 칸을 키에 넣는지 본다. 위 계산은 흉내라 여기서 원문을 확인한다.
src = (REPO / "bin" / "ai-debate").read_text(encoding="utf-8")
check("캐시: 키 계산에 층 칸이 들어 있다", '"tier"' in src and "cache_key_src" in src)
check("캐시: 키에 제미나이 모델 이름 칸이 있다", '"gemini_model": gemini_model()' in src)
check("캐시: 키와 회차 기록이 같은 식으로 클로드 모델을 적는다",
      src.count('[CLAUDE_MODELS[a] for a in POOL if a in CLAUDE_MODELS]') == 2,
      "한 곳은 meta.json, 한 곳은 캐시 키다 — 한쪽만 있으면 그쪽이 안 갈린다")

# --- 호출부가 실제로 그 값을 쓰는지 -------------------------------------------
check("호출부: 클로드 강도가 상수가 아니라 함수에서 온다",
      '"--effort", claude_effort()' in src,
      "CLAUDE_EFFORT 를 그대로 넘기면 깊은 모드가 강도를 못 올린다")
check("호출부: 코덱스 모델을 자리별 매핑에서 꺼내 넘긴다",
      '_seat_model = CODEX_MODELS.get(agent)' in src
      and 'env["CODEX_ASK_MODEL"] = _seat_model' in src,
      "자리마다 다른 모델을 부르려면 매핑에서 꺼내야 한다")
check("호출부: 매핑값이 없으면 안 넘긴다",
      'if _seat_model:' in src,
      "빈 값을 넘기면 평소 자리가 설정 파일을 안 따른다")
check("호출부: 코덱스 모델 환경변수를 한 곳에서만 넘긴다",
      src.count('env["CODEX_ASK_MODEL"]') == 1,
      "여러 곳에서 넘기면 평소 회차까지 덮어쓸 수 있다")
check("호출부: 정리자는 평소 자리로만 부른다 — 그래서 설정 파일값이다",
      'call_agent("codex", "arbiter"' in src,
      "정리자를 astra 자리로 부르면 운영자 지시를 어긴다")
check("호출부: 코덱스 판정이 이름 비교가 아니라 is_codex 다",
      'if is_codex(agent) and rc == CODEX_ASK_RC_LIMIT:' in src
      and 'elif is_codex(agent):' in src,
      "agent == \"codex\" 로 비교하면 새 자리가 한도·호출 갈래에서 빠진다")

# --- 사용량 귀속 --------------------------------------------------------------
# 자리가 둘이면 같은 역할의 두 호출이 글자까지 같은 프롬프트를 받는다. 가름이 프롬프트
# 앞 400자라 그대로 두면 한쪽 사용량이 다른 쪽 것으로 적힌다.
check("계측: 호출 이름을 프롬프트 맨 앞에 얹는다",
      'staged = f"[호출 {lbl}]\\n" + staged' in src,
      "꼬리에 붙이면 앞 400자만 보는 가름에 안 걸린다")
_before = src.split('staged = f"[호출 {lbl}]\\n" + staged')[0]
check("계측: 코덱스 호출에만 얹는다",
      "if is_codex(agent):" in _before[-700:],
      "다른 provider 프롬프트까지 바꿀 이유가 없다")

# --- 되돌림 감지 --------------------------------------------------------------
check("기록: 예비 모델 전환을 새 자리 파일에서도 찾는다",
      '"*.codex-*.raw.txt"' in src and '"*.codex-*.retry.raw.txt"' in src,
      "codex 로 끝나는 이름만 보면 새 자리의 전환이 기록에서 사라진다")

# --- 기록 ---------------------------------------------------------------------
check("기록: 회차 기록에 층 표시가 들어간다", '"deep": DEEP' in src)
check("기록: 클로드 자리가 둘일 때 이름을 둘 다 적는다",
      'CLAUDE_MODELS[a] for a in POOL' in src,
      "이름 하나만 적으면 어느 모델이 낸 결론인지 못 읽는다")
check("기록: 코덱스 자리가 둘일 때도 이름을 둘 다 적는다",
      'agent_codex_model(a) or "?" for a in POOL if is_codex(a)' in src,
      "이름 하나만 적으면 어느 모델이 낸 결론인지 못 읽는다")

reset()
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
