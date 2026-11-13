#!/usr/bin/env python3
"""settings.json 에 등록한 훅 명령이 실제로 닿는 파일인지 고정한다.

훅 등록이 깨지는 방식은 조용하다. 하네스는 명령을 셸에 넘기므로 경로가 틀려도
그 자리에서 아무 일도 안 일어나고 오류도 안 난다 — 가드가 등록만 살아 있고
실제로는 꺼진 상태가 된다(R37 이 적은 "등록됐는데 파일이 없는" 자리).

여기서 보는 것은 셋이다.
하나. 명령의 실행 파일 자리를 셸과 같은 방식으로 풀면($HOME·${HOME}·~) 그 파일이 있다.
둘. 그 파일이 실행 가능하다. 등록은 맞는데 실행 권한이 없으면 같은 침묵이 난다.
셋. 홈을 글자로 박지 않았다. 박으면 계정 이름이 다른 기계에서 그 훅만 조용히 죽는다.

실제로 셸이 그 글자를 풀어 주는지는 이 검사가 아니라 하네스가 증명한다 — 2026-11-10
10:3x 에 $HOME 으로 등록한 bg-longjob-guard 가 실제 도구 호출을 막았고, 거절 문구에
풀기 전 글자가 그대로 찍혔다.
"""
import json
import os
import shlex
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SETTINGS = REPO / "claude" / "settings.json"
HOME = Path.home()

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


def expand(word):
    """셸이 푸는 것과 같은 범위에서만 푼다 — 홈을 가리키는 세 모양."""
    if word.startswith("~/"):
        word = str(HOME) + word[1:]
    return (word.replace("${HOME}", str(HOME))
                .replace("$HOME", str(HOME)))


def hook_commands(data):
    for event, matchers in (data.get("hooks") or {}).items():
        for matcher in matchers or []:
            for hook in (matcher.get("hooks") or []):
                if hook.get("type") != "command":
                    continue
                cmd = hook.get("command") or ""
                if cmd:
                    yield event, cmd


data = json.loads(SETTINGS.read_text(encoding="utf-8"))
cmds = list(hook_commands(data))
check("훅 명령을 하나 이상 읽었다", cmds, str(SETTINGS))

for event, cmd in cmds:
    try:
        first = shlex.split(cmd)[0]
    except ValueError:
        check(f"{event}: 명령을 토큰으로 가를 수 있다", False, cmd)
        continue
    path = Path(expand(first))
    check(f"{event}: {first} 가 실재한다", path.is_file(), str(path))
    if path.is_file():
        check(f"{event}: {first} 가 실행 가능하다",
              os.access(path, os.X_OK), str(path))
    # 홈을 글자로 박으면 계정 이름이 다른 기계에서 그 훅만 죽는다.
    check(f"{event}: {first} 에 홈을 글자로 안 박았다",
          not first.startswith(str(HOME)), first)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
