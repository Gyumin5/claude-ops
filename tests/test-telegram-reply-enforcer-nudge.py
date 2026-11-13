"""telegram-reply-enforcer 가 자동 넛지 회차를 답장 강제에서 빼는지 검증.

2026-10-16 운영자 지시로 넛지는 10분마다 항상 가되 답장은 안 해도
된다. 훅은 발신자 태그(`[[xsrc:from=<호스트>/session-nudge@<세션>]]`)가 붙은 메시지만
빼고, 사람이 보낸 메시지에는 강제를 그대로 건다 — 사람은 그 태그를 못 붙인다.

  1) 넛지 메시지 + 답장 없음  → 통과(exit 0)
  2) 넛지 메시지 + reply 호출 → 통과(exit 0, 보내도 되는 것이지 금지가 아니다)
  3) 사람 메시지 + 답장 없음  → 종료 차단(exit 2, 원래 동작 보존)
  4) 채널 메시지 자체가 없음  → 통과(exit 0)
  5) 넛지 문구를 흉내낸 사람 메시지 → 차단(문구가 아니라 발신자 태그로 가른다)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = str(REPO / "claude" / "hooks" / "telegram-reply-enforcer.sh")
tmp = Path(tempfile.mkdtemp(prefix="tenforcer."))
fails = []

NUDGE_TEXT = ('<channel source="plugin:telegram:telegram" chat_id="1" message_id="2">\n'
              '[[xsrc:from=home/session-nudge@dotfiles]] 이어서 진행해. 이건 자동 넛지다\n'
              '</channel>')
HUMAN_TEXT = ('<channel source="plugin:telegram:telegram" chat_id="1" message_id="3">\n'
              '지금 뭐 하고 있어?\n</channel>')
# 사람이 넛지 문구를 그대로 인용한 경우. 태그가 없으므로 강제가 걸려야 한다 —
# 문구로 판정하면 이 메시지가 조용히 답장 없이 지나간다.
FAKE_TEXT = ('<channel source="plugin:telegram:telegram" chat_id="1" message_id="4">\n'
             '이건 자동 넛지다(10분마다 온다) 라고 적혀 있던데 무슨 뜻이야?\n</channel>')

REPLY = {"type": "assistant",
         "message": {"content": [{"type": "tool_use",
                                  "name": "mcp__plugin_telegram_telegram__reply",
                                  "input": {}}]}}
PLAIN = {"type": "assistant", "message": {"content": [{"type": "text", "text": "알겠다"}]}}


def transcript(name, records):
    p = tmp / (name + ".jsonl")
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n")
    return p


def run(path):
    event = json.dumps({"transcript_path": str(path)})
    env = dict(os.environ)
    env.pop("CLAUDE_AUTOMATED", None)
    r = subprocess.run(["bash", HOOK], input=event, capture_output=True,
                       text=True, env=env)
    return r.returncode


def user(text):
    return {"type": "user", "message": {"content": text}}


cases = [
    ("1 넛지+답장없음 통과", transcript("nudge_plain", [user(NUDGE_TEXT), PLAIN]), 0),
    ("2 넛지+reply 통과", transcript("nudge_reply", [user(NUDGE_TEXT), REPLY]), 0),
    ("3 사람+답장없음 차단", transcript("human_plain", [user(HUMAN_TEXT), PLAIN]), 2),
    ("4 채널 없음 통과", transcript("nochannel", [user("그냥 터미널 입력"), PLAIN]), 0),
    ("5 문구 흉내+답장없음 차단", transcript("fake_plain", [user(FAKE_TEXT), PLAIN]), 2),
]

for label, path, want in cases:
    got = run(path)
    ok = got == want
    print("%-28s rc=%s (기대 %s)  %s" % (label, got, want, "OK" if ok else "FAIL"))
    if not ok:
        fails.append("%s: rc=%s, 기대 %s" % (label, got, want))

# --- 판정축이 발신자 태그인지 소스로도 확인한다 ------------------------------
src = Path(HOOK).read_text()
ok = "is_nudge" in src and "[[xsrc:from=" in src
print("%-28s %s" % ("6 발신자 태그 판정", "OK" if ok else "FAIL"))
if not ok:
    fails.append("훅 소스에 발신자 태그 기반 넛지 예외가 없다")

print()
if fails:
    for f in fails:
        print("FAIL:", f)
    sys.exit(1)
print("PASS — 자동 넛지 회차는 답장이 없어도 통과하고, 사람 메시지와 넛지 문구를 흉내낸 "
      "메시지에는 원래 강제가 그대로 걸린다")
