#!/usr/bin/env python3
"""코덱스 한도가 끝났을 때 예비 모델로 넘어가는지 보는 검사.

코덱스 계정 한도는 모델별이 아니라 칸별이다. 평소 모델(gpt-6.1-sol)과 일반 모델들은
같은 칸을 써서 한 번 소진되면 다 같이 막히고, gpt-reserve 는 다른 칸이라 그 구간에도
답한다(2026-09-21 15:07 KST 실측). 토론 도구는 정리자가 코덱스라 이 구간에 결론 없이
닫히므로, 코덱스로 가는 유일한 통로인 codex-ask 가 한 번 다시 부르게 해 뒀다.

다섯이 동시에 참이어야 이 장치가 제 일을 한다.

  1 한도 거절을 알아본다. 종료코드로는 못 가른다 — 한도 거절도 0 으로 끝난다.
    그래서 문장을 본다.
  2 예비 모델 이름을 실제로 호출에 넘긴다. 넘기지 않으면 설정 파일의 평소 모델로
    다시 불러서 같은 자리에서 또 거절당한다.
  3 정상 응답에는 손대지 않는다. 멀쩡한 회차가 예비 모델로 새면 그 회차의 모델이
    기록과 달라진다. 특히 codex exec 는 우리가 보낸 프롬프트를 되울리므로, 한도를
    논하는 브리프를 출력 전체로 훑으면 성공한 회차마다 오탐이 난다.
  4 예비 모델까지 막히면 전용 종료코드로 알린다. 조용히 0 으로 끝내면 토론 도구가
    그것을 JSON 파싱 실패로 읽고 재시도해서, 한 번으로 못박은 재시도가 넷이 된다.
  5 예비 모델이 답한 회차라는 사실이 사람이 읽는 자리에 뜨고, 그 결론이 캐시로
    남아 평소 모델의 결론인 척 돌아오지 않는다.

실제 코덱스를 부르지 않는다. 같은 이름의 가짜 실행 파일을 앞세워 codex-ask 가
무엇을 넘기는지 받아 적게 한다 — 이 검사가 보려는 것은 답의 내용이 아니라 인자다.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ASK = REPO / "bin" / "codex-ask"
results = []

# 실제 코덱스가 한도 소진에서 내는 문장. 아포스트로피는 유니코드 오른쪽 따옴표다.
LIMIT_LINE = ("ERROR: You’ve hit your usage limit. Visit "
              "https://chatgpt.com/codex/settings/usage to purchase more credits "
              "or try again at Sep 24th, 2026 10:33 AM.")


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


FAKE = r"""#!/bin/bash
# 가짜 코덱스. 받은 인자를 적어 두고, 정해진 모드대로 답한다.
case "$1" in
  --version) echo "codex-cli 0.0.0-fake"; exit 0 ;;
esac
printf '%s\n' "$*" >> "$FAKE_CODEX_DIR/argv"
n_file="$FAKE_CODEX_DIR/calls"
n=$(( $(cat "$n_file" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$n_file"
sent=$(cat)
if [ "$FAKE_CODEX_MODE" = "limit-all" ] \
   || { [ "$n" = 1 ] && [ "$FAKE_CODEX_MODE" = "limit-first" ]; }; then
    printf '%s\n' "$FAKE_CODEX_LIMIT_LINE"
    exit 0
fi
# codex exec 는 우리가 보낸 프롬프트를 먼저 그대로 되울린다.
case "$FAKE_CODEX_MODE" in
  echo|echo-only) printf 'user\n%s\n\n' "$sent" ;;
esac
[ "$FAKE_CODEX_MODE" = "echo-only" ] && exit 0
printf 'codex\nOK-CALL-%s\ntokens used\n1\n' "$n"
exit 0
"""


def run_ask(mode, env_extra=None, prompt="테스트"):
    """가짜 코덱스를 앞세워 codex-ask 를 한 번 돌리고 (종료코드, 표준출력, 표준오류, 인자기록)."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        bindir = td / "bin"
        bindir.mkdir()
        fake = bindir / "codex"
        fake.write_text(FAKE, encoding="utf-8")
        fake.chmod(0o755)

        env = dict(os.environ)
        env["PATH"] = f"{bindir}:{env.get('PATH', '')}"
        env["FAKE_CODEX_DIR"] = str(td)
        env["FAKE_CODEX_MODE"] = mode
        env["FAKE_CODEX_LIMIT_LINE"] = LIMIT_LINE
        env["AI_OUT_RAW"] = "1"          # 출력 필터를 거치지 않고 원문을 본다
        env["CODEX_ASK_TIMEOUT"] = "30"
        env.update(env_extra or {})

        # 표준입력을 반드시 비운 채로 부른다. codex-ask 는 표준입력이 터미널이 아니면
        # 그걸 프롬프트 앞에 붙일 내용으로 보고 끝까지 읽는다(bin/codex-ask 의 "Read stdin
        # if piped"). 여기서 지정하지 않으면 이 검사를 띄운 쪽의 표준입력을 그대로 물려받고,
        # 그게 안 닫히는 파이프이면 가짜 코덱스가 즉시 답해도 codex-ask 가 영원히 기다린다.
        # 터미널에서 손으로 돌리면 통과하고 파이프 아래서 돌리면 깨지는, 실행 방식에 따라
        # 갈리는 실패가 된다(11-05 16:3x 전체 실행 한 회차가 이 모양으로 120초에 끊겼다).
        proc = subprocess.run(
            [str(ASK), "--new", prompt],
            capture_output=True, text=True, timeout=120, env=env, cwd=str(td),
            stdin=subprocess.DEVNULL,
        )
        argv = (td / "argv").read_text(encoding="utf-8") if (td / "argv").exists() else ""
        return proc.returncode, proc.stdout, proc.stderr, argv


def exec_calls(argv):
    return [ln for ln in argv.splitlines() if ln.startswith("exec")]


# ── 1. 한도 거절이면 예비 모델로 한 번 다시 부른다 ──────────────────────
rc, out, err, argv = run_ask("limit-first")
calls = exec_calls(argv)
check("한도 거절이면 다시 부른다 (exec 호출 2회)", len(calls) == 2, f"argv=\n{argv}")
check("다시 부를 때 예비 모델 이름을 넘긴다",
      len(calls) == 2 and "-m gpt-reserve" in calls[1],
      calls[1] if len(calls) == 2 else "(2번째 호출 없음)")
check("첫 호출에는 모델 이름을 안 넘긴다 (설정 파일이 단일 출처)",
      bool(calls) and "-m " not in calls[0],
      calls[0] if calls else "(호출 없음)")
check("두 번째 답이 호출자에게 나간다", "OK-CALL-2" in out, out.strip()[:200])
check("바꿨다는 사실을 조용히 넘기지 않는다",
      "예비 모델" in err and "gpt-reserve" in err, err.strip()[:200])
check("넘어간 회차는 정상 종료코드로 끝난다", rc == 0, f"rc={rc}")

# ── 2. 정상 응답은 건드리지 않는다 ─────────────────────────────────────
rc, out, err, argv = run_ask("ok")
calls = exec_calls(argv)
check("정상 회차는 한 번만 부른다", len(calls) == 1, f"argv=\n{argv}")
check("정상 회차에는 예비 모델을 안 넘긴다",
      bool(calls) and "-m " not in calls[0], calls[0] if calls else "(호출 없음)")
check("정상 회차에는 전환 알림이 안 뜬다", "예비 모델" not in err, err.strip()[:200])

# ── 3. 되울린 프롬프트에 걸려 성공한 회차를 버리지 않는다 ───────────────
# 한도를 논하는 브리프를 보내면 출력에 그 문장이 두 번 실린다 — 우리가 보낸 것과
# (한도일 때만) 코덱스가 낸 것. 성공한 회차는 답 블록이 있으므로 첫 관문에서 걸러진다.
brief = f"아래 문장이 뜨면 한도다.\n{LIMIT_LINE}\n이 판정이 맞나?"
rc, out, err, argv = run_ask("echo", prompt=brief)
calls = exec_calls(argv)
check("한도 문장을 인용한 브리프라도 성공한 회차는 한 번만 부른다",
      len(calls) == 1, f"argv=\n{argv}")
check("인용한 브리프의 답이 그대로 나간다", "OK-CALL-1" in out, out.strip()[:200])

# 답 블록이 아예 없는데 프롬프트만 되울린 경우도 한도가 아니다.
rc, out, err, argv = run_ask("echo-only", prompt=brief)
calls = exec_calls(argv)
check("답 블록이 없어도 되울린 프롬프트만으로는 한도로 보지 않는다",
      len(calls) == 1, f"argv=\n{argv}")

# ── 4. 예비 모델까지 막히면 전용 종료코드로 알린다 ─────────────────────
rc, out, err, argv = run_ask("limit-all")
calls = exec_calls(argv)
check("예비 모델도 막히면 세 번째로 안 부른다", len(calls) == 2, f"argv=\n{argv}")
check("예비 모델도 막히면 종료코드 75 로 끝난다", rc == 75, f"rc={rc}")
check("예비 모델도 막혔다는 사실이 표준오류에 남는다",
      "도 한도 소진" in err, err.strip()[:300])

# ── 5. 끌 수 있고 이름을 바꿀 수 있다 ──────────────────────────────────
rc, out, err, argv = run_ask("limit-first", {"CODEX_ASK_FALLBACK_MODEL": ""})
calls = exec_calls(argv)
check("빈 값을 넘기면 다시 안 부른다", len(calls) == 1, f"argv=\n{argv}")
check("끈 회차에는 한도 문장이 그대로 호출자에게 간다",
      "usage limit" in out, out.strip()[:200])

rc, out, err, argv = run_ask("limit-first", {"CODEX_ASK_FALLBACK_MODEL": "gpt-5.6-luna"})
calls = exec_calls(argv)
check("지정한 이름으로 다시 부른다",
      len(calls) == 2 and "-m gpt-5.6-luna" in calls[1],
      calls[1] if len(calls) == 2 else f"argv=\n{argv}")


# ── 6. 토론 도구 쪽 ────────────────────────────────────────────────────
import importlib.machinery
import importlib.util

loader = importlib.machinery.SourceFileLoader("ai_debate_fb", str(REPO / "bin" / "ai-debate"))
spec = importlib.util.spec_from_loader("ai_debate_fb", loader)
assert spec is not None
debate = importlib.util.module_from_spec(spec)
sys.modules["ai_debate_fb"] = debate
loader.exec_module(debate)

FB_LINE = "[codex-ask] 한도 소진 — 예비 모델 gpt-reserve 로 1회 재시도"

with tempfile.TemporaryDirectory() as td:
    rd = Path(td)
    (rd / "r1.gemini.debater.raw.txt").write_text("평범한 답\n", encoding="utf-8")
    check("전환이 없으면 아무 이름도 안 적는다",
          debate.codex_fallback_model(rd) is None)

    # 참가자가 브리프에 그 줄을 인용해도 기록이 흔들리면 안 된다. 전환 줄은 codex-ask 가
    # 표준오류로 내므로, 원문 파일의 "[stderr]" 뒤쪽에서만 찾는다.
    (rd / "r1.codex.debater.raw.txt").write_text(
        f"user\n{FB_LINE}\n\ncodex\n이 줄은 브리프 인용이다\ntokens used\n", encoding="utf-8")
    check("표준출력 쪽에 실린 같은 글자는 전환으로 세지 않는다",
          debate.codex_fallback_model(rd) is None,
          repr(debate.codex_fallback_model(rd)))

    (rd / "arbiter.raw.txt").write_text(f"…\n[stderr]\n{FB_LINE}\n", encoding="utf-8")
    check("전환이 있으면 그 이름을 적는다",
          debate.codex_fallback_model(rd) == "gpt-reserve",
          repr(debate.codex_fallback_model(rd)))
    check("어느 호출이 예비였는지도 적는다",
          debate.codex_fallback_info(rd)[1] == ["arbiter"],
          repr(debate.codex_fallback_info(rd)[1]))

    # 결손이 하나도 없어도 사람이 읽는 첫 줄에 뜬다. 계측 파일에만 적으면 낮은 모델이
    # 낸 결론을 평소 모델의 판정으로 읽게 된다.
    full = [{"_agent": a, "_role": "debater", "_round": 1, "position": "의견"}
            for a in debate.POOL]
    check("결손이 없어도 결론 첫 줄이 예비 모델을 말한다",
          debate.deficit_header(rd, full).startswith("!! 코덱스 한도 소진으로 예비 모델 gpt-reserve"),
          repr(debate.deficit_header(rd, full))[:300])
    check("정리자가 예비였다는 것을 따로 말한다",
          "정리자 포함" in debate.deficit_header(rd, full),
          repr(debate.deficit_header(rd, full))[:300])

with tempfile.TemporaryDirectory() as td:
    rd = Path(td)
    full = [{"_agent": a, "_role": "debater", "_round": 1, "position": "의견"}
            for a in debate.POOL]
    check("전환이 없는 정상 회차는 첫 줄이 비어 있다",
          debate.deficit_header(rd, full) == "",
          repr(debate.deficit_header(rd, full))[:200])

# 한도 종료코드는 재시도 제외 낱말을 달고 나와야 한다. 안 그러면 부를 곳이 없는 상태로
# 한 번 더 불러서 한 콜이 코덱스 호출 넷이 된다.
check("codex 한도 종료코드는 재시도 제외로 분류된다",
      any(s in debate.exit_error("codex", debate.CODEX_ASK_RC_LIMIT)
          for s in debate._RETRY_SKIP),
      debate.exit_error("codex", debate.CODEX_ASK_RC_LIMIT))
check("그 밖의 종료코드는 그대로 재시도 대상이다",
      not any(s in debate.exit_error("codex", 1) for s in debate._RETRY_SKIP),
      debate.exit_error("codex", 1))
check("같은 종료코드라도 다른 참가자는 한도로 읽지 않는다",
      debate.exit_error("gemini", debate.CODEX_ASK_RC_LIMIT) == "gemini exit 75",
      debate.exit_error("gemini", debate.CODEX_ASK_RC_LIMIT))

# ── 실행 방식에 안 휘둘리는지 ────────────────────────────────────────────
# 이 검사가 codex-ask 를 부를 때 표준입력을 비우지 않으면, 터미널에서는 통과하고
# 안 닫히는 파이프 아래서는 120초 시간초과로 깨진다(11-05 16:3x 실제 사건).
# 기능으로 확인하려면 그 멈춤을 실제로 재현해야 해서 검사 자체가 느려진다 —
# 그래서 호출 자리에 그 지정이 남아 있는지로 고정한다.
_self = Path(__file__).read_text(encoding="utf-8")
check("codex-ask 를 부를 때 표준입력을 비운다",
      "stdin=subprocess.DEVNULL" in _self,
      "물려받은 표준입력이 안 닫히는 파이프면 codex-ask 가 그걸 끝까지 읽느라 안 끝난다")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
