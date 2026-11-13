#!/usr/bin/env python3
"""압축 직후 AI 재호출 가드(block-ai-after-compact.sh).

고정하는 것 다섯.

0. 보는 표식은 자기 세션 것 하나다. 표식이 기계에 하나뿐이면 한 세션의 압축이
   다른 세션의 토론 제출을 막는다. 세션을 모르는 호출은 전역 표식으로 내려가지
   않고 통과하며 그 사실만 로그에 남긴다.

1. 지금 쓰는 진입 이름을 안다. 가드가 만들어질 때의 이름(gemini-ask·codex-ask·
   ai-collab)만 알고 있어서, 실제 진입인 ai-debate-detach 가 통째로 빠져 있었다.
2. 이름이 경로나 인자로만 나오면 막지 않는다. 토론 결과를 읽는 명령
   (`cat .../ai-debate/run-x/arbiter.json`)까지 막으면 압축 직후 판단 자체가 멈춘다.
   판정은 "실행 명령 자리에 섰나" 로만 한다.
3. 사용자 최신 메시지는 훅 입력의 transcript_path 가 가리킨 기록에서만 읽는다.
   작업디렉토리로 프로젝트 키를 만들어 최신 기록을 고르면 남의 세션 기록을 읽는다.
4. 명시 요청 허용에 '토론해' 류가 든다. 운영자가 쓰는 말이 그쪽인데 옛 목록에는
   'ai 토론' 만 있어서 "토론해서 해" 가 자발 재호출로 막혔다.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOK = str(Path(__file__).resolve().parent.parent / "claude" / "hooks"
           / "block-ai-after-compact.sh")

results = []


def check(name, cond, detail=None):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail!r}" if not cond and detail else ""))


def transcript(path, text):
    """마지막 사용자 메시지가 text 인 기록 파일을 만든다."""
    rows = [
        {"type": "user", "message": {"role": "user", "content": "이전 요청"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "답"}},
        {"type": "user", "message": {"role": "user",
                                     "content": [{"type": "text", "text": text}]}},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
                    + "\n", encoding="utf-8")


SID = "sess-11111111-aaaa"


def run(cmd, home, tpath=None, cwd=None, flag_age=0, make_flag=True,
        sid: "str | None" = SID, flag_sid=None):
    """flag_sid 는 표식을 만들어 둘 세션. 안 주면 호출하는 세션과 같다."""
    flags = Path(home) / ".claude" / "state" / "compact-flags"
    flags.mkdir(parents=True, exist_ok=True)
    flag = flags / "{}.block".format(flag_sid or sid or "없음")
    if flag_sid and sid:
        # 표식 주인을 따로 지정했으면 부르는 세션 것은 없는 상태로 만든다.
        own = flags / "{}.block".format(sid)
        if own.exists():
            own.unlink()
    if make_flag:
        flag.write_text("", encoding="utf-8")
        if flag_age:
            t = time.time() - flag_age
            os.utime(flag, (t, t))
    elif flag.exists():
        flag.unlink()

    payload = {"tool_name": "Bash", "tool_input": {"command": cmd},
               "cwd": cwd or str(home)}
    if sid is not None:
        payload["session_id"] = sid
    if tpath:
        payload["transcript_path"] = str(tpath)

    env = dict(os.environ)
    env["HOME"] = str(home)
    p = subprocess.run([HOOK], input=json.dumps(payload), text=True,
                       capture_output=True, env=env, timeout=60)
    return p.returncode, p.stderr, flag.exists()


with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    home = base / "home"
    (home / ".claude" / "state").mkdir(parents=True)

    plain = base / "plain.jsonl"
    transcript(plain, "저장소 정리해 줘")
    asked = base / "asked.jsonl"
    transcript(asked, "이건 토론해서 해")
    asked_en = base / "asked-en.jsonl"
    transcript(asked_en, "codex 한테도 물어봐")
    other = base / "other.jsonl"
    transcript(other, "남의 세션에서 토론해 달라고 한 요청")

    print("=== 플래그가 없을 때 ===")
    rc, err, _ = run("ai-debate-detach '질문'", home, plain, make_flag=False)
    check("압축 플래그가 없으면 통과한다", rc == 0, (rc, err[:120]))

    print("\n=== 실행 명령 자리의 진입은 막는다 ===")
    for label, cmd in [
        ("ai-debate-detach 직접", "ai-debate-detach '질문'"),
        ("ai-debate 직접", "ai-debate '질문'"),
        ("파이프 뒤", "cat brief.md | ai-debate-detach '질문'"),
        ("공백 없는 파이프", "cat brief.md|ai-debate-detach '질문'"),
        ("래퍼를 거쳐", "setsid nohup ai-debate-detach '질문'"),
        ("셸 -c 안쪽", "bash -c \"ai-debate-detach '질문'\""),
        ("변수 접두", "FOO=1 ai-debate-detach '질문'"),
        ("절대경로", "/home/u/dotfiles/bin/ai-debate-detach '질문'"),
        ("옛 이름 gemini-ask", "gemini-ask '질문'"),
        ("옛 이름 codex-ask", "codex-ask '질문'"),
        ("명령 치환 안쪽", "x=$(ai-debate-detach '질문'); echo done"),
        ("세미콜론과 괄호가 붙어 있어도",
         "(cd /tmp); ai-debate-detach '질문'"),
    ]:
        rc, err, kept = run(cmd, home, plain)
        check(f"차단: {label}", rc == 2, (rc, err[:160]))
    check("차단해도 플래그는 남는다", kept, kept)
    check("차단 안내가 통과 방법을 알려준다", "토론해" in err, err[:200])

    print("\n=== 이름이 인자·경로로만 나오면 막지 않는다 ===")
    for label, cmd in [
        ("토론 결과 읽기",
         "cat /home/u/claude-work/ai-debate/run-1/arbiter.normalized.json"),
        ("결과 디렉토리 나열", "ls -t state/ai-debate | head -5"),
        ("이름이 든 글자 출력", "echo 'ai-collab 결과를 본다'"),
        ("결과 파일 검색", "grep -n final_decision run-1/ai-debate.json"),
        ("경로 안의 이름", "wc -l /home/u/dotfiles/bin/ai-debate"),
        # 따옴표 안의 세로줄까지 글자로 갈라 버리면 그 조각이 명령 자리로 승격된다.
        # 실제로 막혔던 모양 그대로다.
        ("따옴표 안에 세로줄이 든 검색 패턴",
         'grep -n "run_dir\\|state/ai-debate" bin/ai-debate | head -8'),
        ("따옴표 안의 세로줄과 이름", "echo 'a|b/ai-debate-detach'"),
        ("작은따옴표 안의 별표 패턴",
         "journalctl --user -u 'claude-job-*ai-debate*' --no-pager"),
    ]:
        rc, err, _ = run(cmd, home, plain)
        check(f"통과: {label}", rc == 0, (rc, err[:160]))

    rc, err, _ = run("ls -al", home, plain)
    check("통과: 관계없는 명령", rc == 0, (rc, err[:120]))

    print("\n=== 사용자가 명시로 요청하면 통과 ===")
    rc, err, kept = run("ai-debate-detach '질문'", home, asked)
    check("'토론해서 해' 면 통과한다", rc == 0, (rc, err[:160]))
    check("통과하면서 플래그를 해제한다", not kept, kept)

    rc, err, _ = run("ai-debate-detach '질문'", home, asked_en)
    check("'codex' 가 들어도 통과한다", rc == 0, (rc, err[:160]))

    print("\n=== 기록은 transcript_path 가 가리킨 것만 본다 ===")
    # 작업디렉토리는 다른 프로젝트를 가리키고, 허용 키워드는 지정된 기록에만 있다.
    rc, err, _ = run("ai-debate-detach '질문'", home, asked,
                     cwd="/home/u/전혀-다른-프로젝트")
    check("작업디렉토리가 달라도 지정된 기록을 읽는다", rc == 0, (rc, err[:160]))
    rc, err, _ = run("ai-debate-detach '질문'", home, plain,
                     cwd=str(other.parent))
    check("옆에 허용 키워드가 든 기록이 있어도 끌려가지 않는다", rc == 2,
          (rc, err[:160]))
    rc, err, _ = run("ai-debate-detach '질문'", home, None)
    check("기록 경로가 없으면 막는다", rc == 2, (rc, err[:160]))

    print("\n=== 표식은 세션별로 갈린다 ===")
    rc, err, kept = run("ai-debate-detach '질문'", home, plain,
                        flag_sid="sess-22222222-bbbb")
    check("남의 세션이 압축해도 이 세션은 통과한다", rc == 0, (rc, err[:160]))
    check("남의 세션 표식은 그대로 남는다", kept, kept)

    rc, err, _ = run("ai-debate-detach '질문'", home, plain, sid=None,
                     make_flag=False)
    check("세션 식별자가 없으면 막지 않는다", rc == 0, (rc, err[:160]))
    rc, err, _ = run("ai-debate-detach '질문'", home, plain, sid="../../etc/x",
                     make_flag=False)
    check("식별자에 경로 구분자가 섞이면 막지 않는다", rc == 0, (rc, err[:160]))

    nosid = home / ".claude" / "logs" / "compact-flag-nosid.log"
    check("식별자를 못 가른 회차는 로그로 드러난다",
          nosid.exists() and "block-ai-after-compact" in nosid.read_text(),
          nosid.read_text()[-200:] if nosid.exists() else "로그 없음")

    print("\n=== 창이 지나면 스스로 풀린다 ===")
    rc, err, kept = run("ai-debate-detach '질문'", home, plain, flag_age=400)
    check("5분이 지나면 통과한다", rc == 0, (rc, err[:160]))
    check("지난 플래그는 지운다", not kept, kept)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
