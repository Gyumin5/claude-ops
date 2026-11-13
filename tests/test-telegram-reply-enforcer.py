#!/usr/bin/env python3
"""텔레그램 답장 강제 훅(telegram-reply-enforcer.sh).

고정하는 것 하나와, 같이 지키는 것 넷.

고정: 답장으로 치는 것은 reply 호출뿐이다. 훅이 edit_message 와 react 까지 충족으로
세고 있어서, 이모지만 달거나 앞선 메시지를 고치기만 하고 턴을 닫아도 통과했다.
둘 다 운영자 기기에 알림을 안 울린다 — 규칙이 reply 1회 이상을 요구하는 이유가 그것이고,
화면에서는 세션이 침묵한 것과 구분되지 않는다.

같이 지키는 것: 자동 넛지는 발신자 태그로만 가린다(문구 인용으로 새지 않는다),
압축 요약 안의 인용은 새 메시지가 아니다, 5xx 로 끝난 턴은 막지 않는다(재시도 고리),
채널 메시지가 없는 턴은 건드리지 않는다.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = str(Path(__file__).resolve().parent.parent / "claude" / "hooks"
           / "telegram-reply-enforcer.sh")

CHANNEL = ('<channel source="plugin:telegram:telegram" chat_id="1" '
           'message_id="2" user="운영자">지금 상태 알려줘</channel>')
NUDGE = ('[[xsrc:from=home/session-nudge@dotfiles]]\n'
         '<channel source="plugin:telegram:telegram" chat_id="1" '
         'message_id="3" user="봇">계속 진행해</channel>')

results = []


def check(name, cond, detail=None):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail!r}" if not cond and detail else ""))


def user(text):
    return {"type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def tool(name):
    return {"type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "name": name, "input": {}}]}}


def says(text):
    return {"type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}]}}


def run(rows, home, automated=False, stop_hook_active=False, no_transcript=False):
    tpath = Path(home) / "t.jsonl"
    tpath.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
                     + "\n", encoding="utf-8")
    event = {"transcript_path": "" if no_transcript else str(tpath)}
    if stop_hook_active:
        event["stop_hook_active"] = True
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["CLAUDE_AUTOMATED"] = "1" if automated else "0"
    p = subprocess.run([HOOK], input=json.dumps(event), text=True,
                       capture_output=True, env=env, timeout=60)
    return p.returncode, p.stderr


REPLY = "mcp__plugin_telegram_telegram__reply"
EDIT = "mcp__plugin_telegram_telegram__edit_message"
REACT = "mcp__plugin_telegram_telegram__react"

with tempfile.TemporaryDirectory() as td:
    home = Path(td)

    print("=== 답장으로 치는 것은 reply 뿐 ===")
    rc, err = run([user(CHANNEL), tool(REPLY)], home)
    check("reply 를 불렀으면 통과한다", rc == 0, (rc, err[:160]))

    rc, err = run([user(CHANNEL), tool(EDIT)], home)
    check("앞 메시지를 고치기만 하면 막는다", rc == 2, (rc, err[:160]))

    rc, err = run([user(CHANNEL), tool(REACT)], home)
    check("이모지만 달면 막는다", rc == 2, (rc, err[:160]))

    rc, err = run([user(CHANNEL), tool(REACT), tool(EDIT), tool(REPLY)], home)
    check("반응·수정에 reply 가 더해지면 통과한다", rc == 0, (rc, err[:160]))

    rc, err = run([user(CHANNEL), says("다 했다")], home)
    check("글만 쓰고 끝내면 막는다", rc == 2, (rc, err[:160]))
    check("막을 때 무엇을 하라고 알려준다", "reply" in err, err[:200])

    print("\n=== 자동 넛지는 발신자 태그로만 가린다 ===")
    rc, err = run([user(NUDGE), says("이어서 작업 중")], home)
    check("넛지 회차는 답장이 없어도 통과한다", rc == 0, (rc, err[:160]))

    quoted = ('운영자: 아까 넛지에 "지금 뭘 하고 있는지 텔레그램으로 한 줄 답하고 "\n'
              '"작업을 이어가라" 라고 돼 있던데\n') + CHANNEL
    rc, err = run([user(quoted), says("네")], home)
    check("사람이 넛지 문구를 인용해도 강제가 걸린다", rc == 2, (rc, err[:160]))

    rc, err = run([user(NUDGE), user(CHANNEL), says("답")], home)
    check("넛지 뒤에 사람 메시지가 오면 강제가 걸린다", rc == 2, (rc, err[:160]))

    print("\n=== 막지 않는 자리 ===")
    rc, err = run([user("평범한 터미널 요청"), says("했다")], home)
    check("채널 메시지가 없으면 건드리지 않는다", rc == 0, (rc, err[:160]))

    compact = {"type": "user", "isCompactSummary": True,
               "message": {"role": "user",
                           "content": [{"type": "text", "text": CHANNEL}]}}
    rc, err = run([compact, says("요약을 읽었다")], home)
    check("압축 요약 안의 인용은 새 메시지가 아니다", rc == 0, (rc, err[:160]))

    rc, err = run([user(CHANNEL), says("API Error: 529 Overloaded")], home)
    check("5xx 로 끝난 턴은 막지 않는다", rc == 0, (rc, err[:160]))

    rc, err = run([user(CHANNEL), says("끝")], home, stop_hook_active=True)
    check("이미 한 번 막은 턴은 또 막지 않는다", rc == 0, (rc, err[:160]))

    rc, err = run([user(CHANNEL), says("끝")], home, automated=True)
    check("자동 실행은 강제 대상이 아니다", rc == 0, (rc, err[:160]))

    rc, err = run([user(CHANNEL), says("끝")], home, no_transcript=True)
    check("기록이 없으면 막지 않는다", rc == 0, (rc, err[:160]))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
