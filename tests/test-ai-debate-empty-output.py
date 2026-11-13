#!/usr/bin/env python3
"""출력 0바이트 실패가 파싱 실패로 뭉뚱그려지지 않는지 고정한다.

agy 는 도구가 거부되면 종료코드 0 에 stdout 0바이트로 끝난다. 2026-09-29 홈에서
실제로 그랬다 — lean-ctx 를 지운 뒤 남은 ~/.gemini/GEMINI.md 가 "읽기는 ctx_read 로만"
이라고 시켜서, 제미나이가 셸 head 를 부르고 헤드리스가 거부하자 답이 통째로 사라졌다.

그걸 "JSON parse failed" 한 줄로 적으면 두 가지를 잃는다. 회차 요약에 원인이 안 실리고,
실패한 참여자는 다음 라운드에서 빠지므로 회차가 의견 하나를 조용히 잃는다. 사유가
표준출력이 아니라 표준오류에만 있는 경우가 대부분이라 그 꼬리도 같이 남긴다.

고정하는 것 넷.

  1. 출력이 있는데 JSON 이 아니면 파싱 실패로 적는다(기존 동작 유지).
  2. 출력이 0바이트면 파싱 실패라고 적지 않는다.
  3. 0바이트일 때 표준오류 꼬리를 문장에 싣는다.
  4. 두 호출 지점(클로드 갈래·일반 갈래)이 같은 함수를 쓴다 — 한쪽만 고치면
     그 갈래의 실패가 계속 뭉뚱그려진다.
"""
import importlib.machinery
import importlib.util
import sys
import types
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


mod = load("ai_debate_empty", REPO / "bin" / "ai-debate")

check("사유 분류 함수가 있다", hasattr(mod, "parse_fail_error"))
if not hasattr(mod, "parse_fail_error"):
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(1)


def fake(out, err=""):
    return types.SimpleNamespace(stdout=out, stderr=err)


# --- 1. 출력이 있는 경우는 기존대로 -------------------------------------------
got = mod.parse_fail_error("gemini", fake("설명만 잔뜩 적고 JSON 은 없다"))
check("출력이 있으면 파싱 실패로 적는다", got == "gemini JSON parse failed", got)

# --- 2~3. 0바이트 --------------------------------------------------------------
got = mod.parse_fail_error("gemini", fake(""))
check("0바이트를 파싱 실패라고 적지 않는다", "parse failed" not in got.lower(), got)
check("0바이트임을 문장에 적는다", "0바이트" in got, got)
check("의심 원인을 같이 적는다", "도구 거부" in got, got)

got = mod.parse_fail_error("gemini", fake("   \n  ", "Tool 'run_shell_command' is not allowed"))
check("공백뿐인 출력도 0바이트로 본다", "0바이트" in got, got)
check("표준오류 꼬리를 싣는다", "run_shell_command" in got, got)

got = mod.parse_fail_error("gemini", fake("", ""))
check("표준오류가 없으면 꼬리를 안 붙인다", "[stderr]" not in got, got)

long_err = "x" * 500 + "REAL-CAUSE-HERE"
got = mod.parse_fail_error("gemini", fake("", long_err))
check("긴 표준오류는 꼬리만 남긴다", len(got) < 320, f"길이 {len(got)}")
check("꼬리는 끝쪽을 남긴다 — 사유는 대개 마지막 줄이다",
      "REAL-CAUSE-HERE" in got, got)

got = mod.parse_fail_error("gemini", fake("", "줄1\n줄2\n줄3"))
check("여러 줄 표준오류를 한 줄로 만든다", "\n" not in got, repr(got))

# --- 4. 두 호출 지점이 같은 함수를 쓴다 ----------------------------------------
src = (REPO / "bin" / "ai-debate").read_text(encoding="utf-8")
# 호출 모양은 안 고정한다 — 앞에 더 좁은 분류(gemini_model_error 등)를 두는 변형이
# 정당하다. 고정하는 것은 두 갈래가 다 이 함수를 거친다는 것뿐이다.
calls = (src.count("parse_fail_error(agent, proc)")
         - src.count("def parse_fail_error(agent, proc)"))
check("실패를 적는 지점 둘이 같은 함수를 쓴다", calls == 2, f"{calls}곳")
check("뭉뚱그리던 옛 문장이 호출 지점에 안 남아 있다",
      '"_error": f"{agent} JSON parse failed"' not in src,
      "한 갈래라도 남으면 그 갈래의 0바이트가 계속 파싱 실패로 적힌다")

# 되울림 방지 — 실패 기록에 원문 앞부분도 계속 남아야 한다(빈 문자열이어도 칸은 있다).
check("실패 기록에 원문 칸이 남아 있다", '"_raw": proc.stdout[:300]' in src)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
