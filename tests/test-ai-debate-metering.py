#!/usr/bin/env python3
"""호출당 사용량 계측 계약 검사.

값을 정하는 것은 프롬프트 크기가 아니라 한 호출 안의 모델 요청 수다 — 요청마다 그때까지의
대화가 다시 실려서 같은 내용도 나눠 읽으면 나눈 횟수만큼 다시 산다(홈 실측: 셸 0회 호출은
요청 중앙 1회·입력 25,406, 셸 3회 이상은 10회·488,050, 최대 568만). 그런데 코덱스 CLI 는
사용량을 안 돌려준다. 롤아웃 기록에서 읽어 호출 단위로 남긴다.

고정하는 것 넷.

  1. 롤아웃에서 요청 수·입력·캐시 재사용·출력·셸 실행 수를 읽는다.
  2. 어느 호출의 기록인지는 프롬프트 앞머리로 가린다. 같은 라운드에 나란히 도는 다른
     코덱스 호출과 안 섞인다 — 시각으로만 고르면 섞인다.
  3. 계측 로그 한 줄에 그 다섯이 같이 들어간다. 못 읽었으면 빈 값이지 0 이 아니다.
  4. 재기만 한다. 요청이 아무리 많아도 그 호출을 실패로 돌리지 않는다 — 임계로 끊는 것은
     값을 본 뒤에 정한다(옆 기계 3회차 판정도 관측 먼저다).
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
from contextlib import redirect_stdout
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


mod = load("ai_debate_metering", REPO / "bin" / "ai-debate")

# 고치기 전 판본에는 이 이름이 없다 — 크래시 대신 실패로 떨어지게 기본값으로 받는다.
_usage = getattr(mod, "codex_call_usage", None)


def usage(prompt, since, paths):
    return _usage(prompt, since, paths) if _usage else None


def rollout(path, prompt, requests, shell, per=(1000, 400, 50)):
    """롤아웃 한 벌을 흉내 낸다. 코덱스가 실제로 남기는 줄 모양 그대로."""
    lines = [{"type": "session_meta", "payload": {"id": "x"}},
             {"type": "response_item",
              "payload": {"type": "message", "role": "user",
                          "content": [{"type": "input_text", "text": prompt}]}}]
    for _ in range(shell):
        lines.append({"type": "response_item",
                      "payload": {"type": "custom_tool_call", "name": "exec"}})
        # 출력 기록은 셸 실행이 아니다. 둘을 같이 세면 실행 수가 두 배로 보인다.
        lines.append({"type": "response_item",
                      "payload": {"type": "custom_tool_call_output", "output": "x"}})
    for _ in range(requests):
        lines.append({"type": "token_usage_record",
                      "payload": {"usage": {"input_tokens": per[0],
                                            "cached_input_tokens": per[1],
                                            "output_tokens": per[2],
                                            "total_tokens": per[0] + per[2]}}})
    Path(path).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines),
                          encoding="utf-8")
    return path


PROMPT_A = "역할: debater\n\n당신은 토론자. 무엇을 할지 정해라.\n" + "가" * 500
PROMPT_B = "역할: critic\n\n당신은 비판자. 약한 가정을 찾아라.\n" + "가" * 500

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    a = rollout(root / "a.jsonl", PROMPT_A, requests=3, shell=2)
    b = rollout(root / "b.jsonl", PROMPT_B, requests=11, shell=13)
    since = 0

    got = usage(PROMPT_A, since, [str(a), str(b)])
    check("요청 수를 센다", got and got["requests"] == 3, str(got))
    check("셸 실행 수를 센다 — 출력 기록은 안 센다", got and got["shell"] == 2, str(got))
    check("입력·캐시 재사용·출력을 더한다",
          got and (got["input"], got["cached_input"], got["output"]) == (3000, 1200, 150),
          str(got))

    other = usage(PROMPT_B, since, [str(a), str(b)])
    check("나란히 도는 다른 호출과 안 섞인다",
          other and other["requests"] == 11 and other["shell"] == 13, str(other))

    check("앞머리가 안 맞으면 못 찾은 것이다",
          usage("역할: arbiter\n다른 글" + "나" * 500, since, [str(a), str(b)]) is None)
    check("빈 프롬프트로는 아무것도 고르지 않는다", usage("", since, [str(a), str(b)]) is None)

    # 호출보다 먼저 끝난 기록은 이 호출의 것이 아니다.
    future = time.time_ns() + 10 ** 12
    check("호출 시작 전 기록은 건너뛴다", usage(PROMPT_A, future, [str(a), str(b)]) is None)

    # 망가진 줄이 섞여도 계측이 회차를 죽이지 않는다.
    broken = root / "broken.jsonl"
    broken.write_text('{"type": "token_usage_record" 깨진 줄\n', encoding="utf-8")
    check("망가진 기록에 안 걸려 넘어진다", usage(PROMPT_A, since, [str(broken)]) is None)
    check("없는 파일도 조용히 지나간다", usage(PROMPT_A, since, [str(root / "없다.jsonl")]) is None)

# ── 계측 로그 한 줄 ────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    log = Path(tmp) / "calls.jsonl"
    run_dir = Path(tmp) / "run-20260917T000000Z"
    run_dir.mkdir()
    keep = mod.LOG_PATH
    mod.LOG_PATH = str(log)
    try:
        mod._log_call({"_agent": "codex", "_role": "debater", "_duration": 12.5,
                       "position": "의견",
                       "_usage": {"requests": 9, "input": 500000, "cached_input": 400000,
                                  "output": 3000, "shell": 7}}, run_dir, 1)
        mod._log_call({"_agent": "gemini", "_role": "critic", "_duration": 3.0,
                       "should_proceed": True}, run_dir, 1)
        # provider 칸이 빈 옛 기록. 키는 있고 값이 null 이라 get 의 기본값이 안 나온다.
        mod._log_call({"_duration": 0.0}, run_dir, 0)
    finally:
        mod.LOG_PATH = keep
    rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    codex = rows[0] if rows else {}
    check("요청 수가 로그에 든다", codex.get("model_requests") == 9, str(codex))
    check("셸 실행 수가 로그에 든다", codex.get("shell_calls") == 7, str(codex))
    check("입력·캐시·출력·합계가 로그에 든다",
          (codex.get("input_tokens"), codex.get("cached_input_tokens"),
           codex.get("output_tokens"), codex.get("total_tokens")) == (500000, 400000, 3000, 503000),
          str(codex))
    check("요청이 많아도 실패로 돌리지 않는다 — 재기만 한다",
          codex.get("success") is True, str(codex))
    gem = rows[1] if len(rows) > 1 else {}
    check("기록이 없는 provider 는 빈 값이지 0 이 아니다",
          gem.get("model_requests") is None and gem.get("total_tokens") is None, str(gem))

    # ── 집계 ───────────────────────────────────────────────────────────
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = mod.do_log_summary(str(log))
    except Exception as e:  # 집계가 죽으면 검사 전체를 같이 죽이지 않고 실패로 적는다
        rc, text = None, f"{buf.getvalue()}\n예외: {type(e).__name__}: {e}"
    else:
        text = buf.getvalue()
    check("집계가 돈다", rc == 0, text[:200])
    # 죽으면 rc 가 아니라 예외라 위 줄까지 못 온다. 그래서 따로 적어 둔다.
    check("provider 칸이 빈 옛 기록에 집계가 안 죽는다", "?" in text, text)
    check("집계에 요청 수 표가 붙는다", "req_med" in text and "req_max" in text, text)
    check("기록 있는 provider 만 그 표에 든다",
          "req_med" in text and text.count("gemini") == 1, text)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
