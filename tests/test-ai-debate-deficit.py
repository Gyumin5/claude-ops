#!/usr/bin/env python3
"""토론 결손 표시 계약 검사.

고정하는 것: 참가자가 빠진 채 나온 결론이 정상 결론처럼 읽히면 안 된다. 3의견인 줄
알고 읽는 판정문이 사실 2의견이었다는 사고가 실제로 있었다(2026-08-18 bias, codex 0건).
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

with tempfile.TemporaryDirectory() as tmp:
    # 9) 정리자 단독 실행(--arbiter-only)도 결손을 표시한다.
    #    이 경로는 --deep 플래그 없이 불리므로 풀을 meta.json 에서 되살리지 않으면
    #    깊은 회차의 늘어난 자리가 빠진 것을 못 본다.
    run_dir = Path(tmp) / "run-20261015T000000Z"
    run_dir.mkdir()
    (run_dir / "task.txt").write_text("질문", encoding="utf-8")
    (run_dir / "meta.json").write_text(json.dumps({
        "pool": ["codex", "gemini", "claude-opus", "claude-fable"], "deep": True,
    }), encoding="utf-8")
    # 제미나이 자리가 통째로 빠진 라운드 하나.
    r1 = [out("codex", 1), out("claude-opus", 1), out("claude-fable", 1),
          out("gemini", 1, err="gemini 모델 미인식 — agy models 목록·구글 계정 등급 확인")]
    (run_dir / "r1.outputs.json").write_text(json.dumps(r1, ensure_ascii=False),
                                             encoding="utf-8")
    shim = Path(tmp) / "shim2"
    shim.mkdir()
    arb = {"final_decision": "그대로 간다", "rationale": "근거", "action_steps": ["하나"],
           "verification_plan": "검사", "dissent": "없음"}
    for name in ("codex-ask", "agy", "claude", "codex"):
        f = shim / name
        f.write_text("#!/bin/bash\ncat <<'JSON'\n%s\nJSON\n" % json.dumps(arb, ensure_ascii=False),
                     encoding="utf-8")
        f.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(shim) + os.pathsep + env.get("PATH", "")
    r = subprocess.run([sys.executable, str(REPO / "bin" / "ai-debate"),
                        "--arbiter-only", str(run_dir)],
                       capture_output=True, text=True, env=env, timeout=180)
    final = {}
    fp = run_dir / "final_result.json"
    if fp.exists():
        final = json.loads(fp.read_text(encoding="utf-8"))
    check("단독 정리도 결손을 화면에 적는다",
          "gemini" in (r.stdout + r.stderr), (r.stderr or "")[-300:])
    check("단독 정리의 결손이 final_result.json 에도 같이 남는다",
          isinstance(final.get("deficit"), str) and "gemini" in final.get("deficit", ""),
          str(final.get("deficit"))[:200])
    _d = final.get("deficit", "")
    _missing = _d.split("빠진 참가자 ", 1)[-1].split(" —", 1)[0]
    check("깊은 회차의 늘어난 자리를 풀로 되살린다",
          "예정 4명" in _d and _missing == "gemini",
          "되살리지 못하면 예정 인원이 3명으로 줄어 그 자리의 결손이 안 보인다: %s"
          % str(_d)[:200])

# 10) 결손이 있으면 캐시에 안 남긴다. 캐시 키에 참가자도 모델도 안 들어가서,
#     자리 하나가 빈 결론이 24시간 동안 멀쩡한 회차의 답인 척 돌아온다.
SRC = (REPO / "bin" / "ai-debate").read_text(encoding="utf-8")
check("결손 회차는 캐시 저장 조건에서 빠진다",
      "not _fb_model and not _deficit" in SRC,
      "저장 조건에 결손이 안 들어가면 빈 자리 결론이 캐시로 되돌아온다")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
