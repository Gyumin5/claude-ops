#!/usr/bin/env python3
"""토론 결손 표시 계약 검사.

고정하는 것: 참가자가 빠진 채 나온 결론이 정상 결론처럼 읽히면 안 된다. 3의견인 줄
알고 읽는 판정문이 사실 2의견이었다는 사고가 실제로 있었다(2026-08-18 session-p, codex 0건).
숫자는 이미 찍히고 있었고 사람이 안 읽었을 뿐이라, 결손은 맨 앞 한 줄로 세운다.

  1. 결손이 없으면 빈 문자열이다. 정상 실행에 노이즈를 얹지 않는다.
  2. 빠진 참가자는 이름·원인·복구 절차와 함께 나온다.
  3. 라운드 중 탈락을 누적 인원으로 숨기지 않는다.
  4. 결론 첫 줄·세션 주입 요약 첫 줄·metering 이 같은 문자열을 쓴다.
  5. 길이 제한에 잘려도 결손 줄은 남는다.
  6. 유효응답은 종료 상태와 응답 구조로만 가른다. 본문의 오류 낱말로 실패시키지 않는다.
  7. 유효 참가자가 2명 미만인 라운드는 정족수 미달이다. 마지막 라운드만 보지 않는다.
  8. 정리자 자격이 없으면 착수 전에 멈추고, 그 중단은 유료 호출을 0회 쓴다.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
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
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"\n      {detail}" if not cond and detail else ""))


def out(agent, rnd, err=None):
    """정상 콜 하나. 알맹이가 있어야 유효한 의견으로 센다."""
    o = {"_agent": agent, "_role": "debater", "_round": rnd,
         "position": "%s 의 %d라운드 의견" % (agent, rnd)}
    if err:
        o.pop("position")
        o["_error"] = err
    return o

debate = load("ai_debate_mod", REPO / "bin" / "ai-debate")
detach = load("ai_debate_detach_mod", REPO / "bin" / "ai-debate-detach")
POOL = debate.POOL

with tempfile.TemporaryDirectory() as tmp:
    rd = Path(tmp)

    # 1) 정상 — 풀 전원이 매 라운드 의견을 냈다
    full = [out(a, r) for r in (1, 2) for a in POOL]
    check("결손 없으면 빈 문자열", debate.deficit_header(rd, full) == "",
          repr(debate.deficit_header(rd, full)))

    # 2) 한 명이 통째로 빠졌다. 원인을 raw 에서 읽어 복구 절차까지 붙인다.
    (rd / "r1.debater.gemini.raw.txt").write_text(
        "Error: Could not load the default credentials. authentication required\n",
        encoding="utf-8")
    partial = [out(a, 1) for a in POOL if a != "gemini"] + [out("gemini", 1, "실패")]
    h = debate.deficit_header(rd, partial)
    check("빠진 참가자를 이름으로 밝힌다", "gemini" in h and "빠진 참가자" in h, h)
    check("예정 인원과 실제 인원을 같이 적는다",
          f"예정 {len(POOL)}명" in h and "2명" in h, h)
    check("복구 절차를 붙인다", "복구" in h and "재인증" in h, h)
    check("모델은 기록값이라고 밝힌다", "기록값" in h, h)

    # 2-b) 유효응답 판정 — 빈 응답은 의견이 아니고, 본문의 오류 낱말은 실패가 아니다
    check("오류로 끝난 콜은 무효", debate.output_is_valid(out("codex", 1, "실패")) is False)
    check("알맹이 없는 응답은 무효",
          debate.output_is_valid({"_agent": "codex", "_round": 1,
                                  "position": "", "concerns": []}) is False)
    check("빈 문자열이라도 다른 칸이 차 있으면 유효",
          debate.output_is_valid({"_agent": "codex", "_round": 1,
                                  "position": "", "should_proceed": True}) is True)
    check("본문에 오류 낱말이 있어도 실패로 안 센다",
          debate.output_is_valid({"_agent": "codex", "_round": 1,
                                  "position": "authentication required 가 원인이다"}) is True)
    empty_round = [out(a, 1) for a in POOL if a != "codex"] + [
        {"_agent": "codex", "_role": "debater", "_round": 1, "position": "   "}]
    h3 = debate.deficit_header(rd, empty_round)
    check("빈 응답을 낸 참가자는 결손으로 센다", h3 and "codex" in h3 and "R1=2" in h3, h3)

    # 3) 라운드 중 탈락 — 누적으로는 셋 다 의견을 냈지만 2라운드는 둘뿐이다.
    dropped = [out(a, 1) for a in POOL] + [out(a, 2) for a in POOL if a != "codex"] \
        + [out("codex", 2, "한도")]
    h2 = debate.deficit_header(rd, dropped)
    check("라운드 중 탈락을 누적 인원으로 숨기지 않는다",
          h2 and "R1=3" in h2 and "R2=2" in h2, h2)

    # 4) 세 자리가 같은 문자열인가 — 결론 첫 줄·주입 요약 첫 줄·metering
    (rd / "final_result.json").write_text(
        json.dumps({"deficit_header": h}, ensure_ascii=False), encoding="utf-8")
    stdout = f"rounds=1/2 consensus=True\nagents: codex=1ok/0fail\nartifacts={rd}\n"
    cap = detach.capsule_from(stdout, "질문", 12)
    check("주입 요약의 첫 줄이 결손 줄이다", cap.splitlines()[0] == h,
          cap.splitlines()[0][:200])

    # 5) 길이 제한에 잘려도 결손은 남는다
    long_out = stdout + "\n".join("군더더기 %d" % i for i in range(4000))
    cap2 = detach.capsule_from(long_out, "질문", 12)
    check("잘려도 결손 줄이 살아남는다",
          len(cap2) <= detach.CAPSULE_MAX and cap2.splitlines()[0] == h,
          "%d자 / %s" % (len(cap2), cap2.splitlines()[0][:120]))

    # 6) 결손이 없으면 주입 요약도 예전 그대로다
    (rd / "final_result.json").write_text(json.dumps({"deficit_header": ""}),
                                          encoding="utf-8")
    cap3 = detach.capsule_from(stdout, "질문", 12)
    check("결손 없으면 요약 첫 줄은 그대로", cap3.startswith("[토론 결과]"),
          cap3.splitlines()[0][:120])

    # 7) 정족수 — 유효 인원이 2명 미만인 라운드를 골라낸다
    solo = [out("codex", 1)] + [out(a, 1, "실패") for a in POOL if a != "codex"] \
        + [out(a, 2) for a in POOL]
    per = debate.valid_by_round(solo)
    thin = {r: s for r, s in per.items() if len(s) < 2}
    check("유효 1명 라운드를 정족수 미달로 본다", list(thin) == [1], str(per))
    full_rounds = debate.valid_by_round([out(a, r) for r in (1, 2) for a in POOL])
    check("전원 라운드는 정족수 미달이 아니다",
          all(len(s) >= 2 for s in full_rounds.values()), str(full_rounds))

with tempfile.TemporaryDirectory() as tmp:
    # 8) 착수 전 점검 — 정리자 자격이 없으면 유료 호출 0회로 멈춘다
    shim = Path(tmp) / "shim"
    shim.mkdir()
    log = Path(tmp) / "called.log"
    for name in ("codex-ask", "agy", "claude", "codex"):
        f = shim / name
        f.write_text('#!/bin/bash\necho "%s $*" >> "%s"\nexit 0\n' % (name, log),
                     encoding="utf-8")
        f.chmod(0o755)
    env = dict(os.environ)
    env["AI_DEBATE_CODEX_AUTH"] = str(Path(tmp) / "없는파일.json")
    env["PATH"] = str(shim) + os.pathsep + env.get("PATH", "")
    r = subprocess.run([sys.executable, str(REPO / "bin" / "ai-debate"), "질문"],
                       capture_output=True, text=True, env=env, timeout=120)
    check("정리자 자격이 없으면 착수 전 중단", r.returncode == 6,
          "rc=%s %s" % (r.returncode, (r.stderr or "")[-200:]))
    check("중단 사유와 복구 절차를 남긴다",
          "정리자" in r.stderr and "복구" in r.stderr, r.stderr[-300:])
    check("착수 전 중단은 유료 호출을 0회 쓴다", not log.exists(),
          log.read_text(encoding="utf-8")[:200] if log.exists() else "")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
