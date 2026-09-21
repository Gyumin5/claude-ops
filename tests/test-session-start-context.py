#!/usr/bin/env python3
"""session-start-context 훅 테스트 — 결정 조회 층 점검 부분.

고정하는 것: 착수 시점에 과거 결론이 안 보이는 상태가 조용히 지나가지 않는다.
active.md 가 없거나·잘리거나·본체보다 크면 주입 안에서 그 사실을 말한다.
정상일 때는 경고를 만들지 않는다(주입된 내용 자체가 관측이라 별도 심박 불필요).
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = str(Path(__file__).resolve().parent.parent / "claude" / "hooks" / "session-start-context.sh")
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"\n      {detail}" if not cond and detail else ""))


def run(cwd):
    p = subprocess.run([HOOK], input=json.dumps({"cwd": str(cwd)}),
                       capture_output=True, text=True, timeout=60)
    if not p.stdout.strip():
        return ""
    try:
        return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    except Exception as e:
        return f"(파싱 실패: {e})\n{p.stdout}\n{p.stderr}"


def project(tmp, *, progress="# progress.md\nstatus: active\n",
            history=None, active=None):
    """프로젝트 트리 하나를 만든다. active/history 는 줄 수 또는 본문."""
    d = Path(tmp)
    if progress is not None:
        (d / "progress.md").write_text(progress, encoding="utf-8")
    if history is not None:
        (d / "history.md").write_text(history, encoding="utf-8")
    if active is not None:
        (d / "history").mkdir(exist_ok=True)
        (d / "history" / "active.md").write_text(active, encoding="utf-8")
    return d


ALERT = "[자동 알림] 결정 조회 층 점검"

with tempfile.TemporaryDirectory() as tmp:
    # 정상: compact 한 active.md 가 있으면 경고 없음
    d = project(tmp, history="## [2026-01-01] #1 결정\n" * 100,
                active="## 현재 유효한 결정\n- 하나\n- 둘\n")
    out = run(d)
    check("정상 compact view 는 무경고", ALERT not in out, out[:400])
    check("정상일 때 active.md 가 주입된다", "현재 유효한 결정" in out, out[:400])

with tempfile.TemporaryDirectory() as tmp:
    # active.md 부재 — session-a 가 몇 달간 있던 상태. 지금까지 조용했다.
    d = project(tmp, history="## [2026-01-01] #1 결정\n- 실험/실패: 재시도 시간낭비\n" * 300)
    out = run(d)
    check("active.md 부재 시 경고", ALERT in out and "history/active.md 가 없다" in out, out[-600:])
    check("부재 경고가 본체 줄 수를 댄다", "600줄" in out, out[-600:])

with tempfile.TemporaryDirectory() as tmp:
    # 6000바이트 초과 — 뒷부분이 말없이 잘려나가던 자리
    big = "## 결정\n" + ("- 아주 긴 결정 줄이다.\n" * 400)
    d = project(tmp, history="## [2026-01-01] #1\n" * 5000, active=big)
    out = run(d)
    check("상한 초과 시 잘림을 말한다", ALERT in out and "주입이 잘렸다" in out, out[-600:])
    check("잘림 경고가 실제 바이트를 댄다", str(len(big.encode())) + "바이트" in out, out[-600:])

with tempfile.TemporaryDirectory() as tmp:
    # 회귀: 바이트 절단이 한글 문자 중간에 떨어져도 주입이 통째로 사라지지 않는다.
    # 예전 구현은 여기서 decode 가 터져 progress.md·handoff 까지 전부 빈 문자열이
    # 됐고, 훅은 exit 0 이라 아무도 몰랐다. 잘리는 건 그 문자 하나뿐이어야 한다.
    big = "## 결정\n" + ("- 아주 긴 결정 줄이다.\n" * 400)
    cut = big.encode()[:6000]
    try:
        cut.decode("utf-8")
        boundary_clean = True
    except UnicodeDecodeError:
        boundary_clean = False
    d = project(tmp, progress="# progress.md\nSENTINEL_PROGRESS\n",
                history="## [2026-01-01] #1\n" * 50, active=big)
    out = run(d)
    check("절단 지점이 문자 중간이다(전제 확인)", not boundary_clean,
          "6000바이트 경계가 우연히 문자 경계라 회귀를 못 재현한다")
    check("문자 중간 절단에도 주입이 살아남는다", "SENTINEL_PROGRESS" in out, out[:300])

with tempfile.TemporaryDirectory() as tmp:
    # 본문에 따옴표 세 개·역슬래시가 있어도 주입이 죽지 않는다(같은 계열의 두 번째 경로)
    d = project(tmp, progress='# progress.md\nSENTINEL_QUOTE\n"""\\ 이런 줄\n',
                history="## [2026-01-01] #1\n" * 50, active="- 짧다\n")
    out = run(d)
    check("따옴표·역슬래시가 있어도 주입 유지", "SENTINEL_QUOTE" in out, out[:300])

with tempfile.TemporaryDirectory() as tmp:
    # 줄 수만 초과(바이트는 상한 안) — session_c_work 592줄 계열
    d = project(tmp, history="## [2026-01-01] #1\n" * 500, active="- 짧다\n" * 200)
    out = run(d)
    check("줄 수 초과를 잡는다", ALERT in out and "200줄이다" in out, out[-600:])

with tempfile.TemporaryDirectory() as tmp:
    # 요약본이 본체보다 큼 — proj-b 계열(363줄 vs 184줄)
    d = project(tmp, history="## 짧은 본체\n", active="- 요약이 더 길다\n" * 20)
    out = run(d)
    check("요약본 역전을 잡는다", ALERT in out and "요약본이 원본보다 크다" in out, out[-600:])

with tempfile.TemporaryDirectory() as tmp:
    # history.md 자체가 없는 새 프로젝트는 경고 대상이 아니다(오탐 방지)
    d = project(tmp)
    out = run(d)
    check("history.md 없는 프로젝트는 무경고", ALERT not in out, out[:400])

with tempfile.TemporaryDirectory() as tmp:
    # 경고는 무엇을 하라는 것까지 말한다. 관측만 남기면 또 몇 달 방치된다.
    d = project(tmp, history="## [2026-01-01] #1\n" * 300)
    out = run(d)
    check("경고가 조치를 지시한다", "재시도 시 시간낭비" in out and "compact view" in out, out[-600:])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
