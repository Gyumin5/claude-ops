#!/usr/bin/env python3
"""bg-longjob-guard 계약 검사.

지키는 것:
  1. ai-debate 직접 실행은 foreground 든 background 든 막는다(detach 전용).
  2. ai-debate-detach 는 foreground 로 통과하고 run_in_background 로는 막힌다.
  3. 등록된 벤치는 run_in_background 일 때만 막힌다.
  4. 인자에 문자열로 들어간 ai-debate(grep/echo)는 안 막는다.
  5. kill switch 와 파싱 실패는 fail-open 이다.

차단은 rc=2 + stderr 로 나온다. 통과는 rc=0.
"""
import json
import os
import subprocess
import sys
import tempfile

# 저장소 뿌리를 이 파일 위치에서 찾는다. 예전엔 홈 아래 고정 경로였는데,
# 그러면 다른 자리에 받은 사람은 그대로 못 돌린다.
REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

# 결함주입용으로 사본을 검사할 수 있게 열어둔다(검사가 실제로 회귀를 잡는지 확인할 때).
HOOK = os.environ.get("BG_GUARD_HOOK") or os.path.join(
    os.path.dirname(os.path.realpath(__file__)), "..", "claude", "hooks", "bg-longjob-guard.sh")
FAILED = []


def run(cmd, bg=False, tool="Bash", env=None, manifest=None):
    payload = {"tool_name": tool, "tool_input": {"command": cmd, "run_in_background": bg}}
    e = dict(os.environ)
    e.pop("CLAUDE_BG_GUARD_OFF", None)
    if manifest:
        e["CLAUDE_BG_GUARD_MANIFEST"] = manifest
    if env:
        e.update(env)
    r = subprocess.run(["bash", HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, env=e)
    return r.returncode, (r.stderr or "")


def check(name, got, want, detail=""):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s (rc=%s, 기대 %s) %s" % (name, got, want, detail))
        FAILED.append(name)


def main():
    off_flag = os.path.expanduser("~/.claude/state/bg-longjob-guard.off")
    if os.path.exists(off_flag):
        print("건너뛰지 않는다: kill switch 파일이 있으면 모든 검사가 통과로 보인다.")
        print("  %s 를 치우고 다시 돌려라." % off_flag)
        return 1

    print("ai-debate 직접 실행")
    check("foreground 차단", run("ai-debate '질문'")[0], 2)
    check("background 차단", run("ai-debate '질문'", bg=True)[0], 2)
    check("파이프 뒤에서도 차단", run("cat brief.md | ai-debate '질문'")[0], 2)
    check("절대경로 차단", run(os.path.join(REPO, "bin/ai-debate '질문'"))[0], 2)
    check("timeout 래퍼 차단", run("timeout 590 ai-debate '질문'")[0], 2)
    _, err = run("ai-debate '질문'")
    check("대안 명령을 알려준다", "ai-debate-detach" in err, True, err[:80])

    print("ai-debate-detach")
    check("foreground 통과", run("cat b.md | ai-debate-detach '질문'")[0], 0)
    check("background 차단", run("ai-debate-detach '질문'", bg=True)[0], 2)

    print("오탐 방지")
    check("grep 인자", run("grep -n ai-debate bin/x", bg=True)[0], 0)
    check("echo 인자", run("echo ai-debate")[0], 0)
    check("주석만", run("# ai-debate 얘기")[0], 0)
    check("무관한 명령 foreground", run("ls -la")[0], 0)
    check("무관한 명령 background", run("sleep 5", bg=True)[0], 0)
    # heredoc 본문은 데이터다 — 커밋 메시지·문서에 ai-debate 로 시작하는 줄이 있어도 명령이 아니다.
    check("heredoc 본문", run(
        "git commit -F - <<'MSG'\n제목\n\nai-debate run-2026 에서 정했다\nMSG")[0], 0)
    check("heredoc 뒤 명령은 살아있다", run(
        "cat <<'EOF' | ai-debate '질문'\n본문\nEOF")[0], 2)

    print("등록 벤치")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("# 주석\nrun_bench.py\n")
        man = f.name
    try:
        check("background 차단", run("python3 run_bench.py", bg=True, manifest=man)[0], 2)
        check("foreground 통과", run("python3 run_bench.py", manifest=man)[0], 0)
    finally:
        os.unlink(man)

    print("fail-open")
    check("kill switch", run("ai-debate '질문'", env={"CLAUDE_BG_GUARD_OFF": "1"})[0], 0)
    check("Bash 아닌 도구", run("ai-debate '질문'", tool="Read")[0], 0)
    check("빈 명령", run("   ")[0], 0)

    if FAILED:
        print("\n실패 %d건: %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("\n전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
