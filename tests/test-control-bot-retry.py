#!/usr/bin/env python3
"""컨트롤봇의 재시도는 "요청이 나갔나" 로 가른다.

운영자 증상(2026-11-11): "버튼 누르면 뭘 눌렀는지는 뜨는데 막상
반응이 없거나 엄청 늦다". 누름 응답은 롱폴 스레드가 보내서 늘 가고, 결과는 일꾼
스레드의 커넥션으로 나가는데 그 커넥션이 버튼과 버튼 사이에 논다.

토론 run-20261111T035103Z 라운드 1 에서 참가자 넷이 같은 자리를 짚었다. 고정하는
것 넷이다.

⑴ 오래 논 커넥션은 요청을 쓰기 전에 버린다 — 살아 있는지 묻는 방법이 요청을
   보내 보는 것뿐이라, 묻지 말고 새로 연다. 중복 위험이 0 이다.
⑵ 연결 단계에서 죽은 것(이름 풀기·접속 거부·암호화 악수)은 요청이 한 바이트도
   안 나간 것이라, 글 보내기 같은 비멱등 호출도 한 번 다시 건다.
⑶ 요청을 쓴 뒤에 죽은 것은 글 보내기를 다시 걸지 않는다 — 서버가 이미 처리했는지
   사후 예외 이름으로 못 가린다(2026-07-03 run-20260703T114659Z).
⑷ 같은 자리에 덮어쓰는 호출(고쳐쓰기·지우기·누름 응답)은 두 번 가도 둘째가
   400 으로 끝나므로 ⑶ 의 대상이 아니다.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BOT = REPO / "bin" / "claude-control-bot"

fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  — {detail}" if detail else ""))
    if not cond:
        fails.append(name)


HARNESS = """
import http.client, importlib.machinery, importlib.util, json, sys, time

events = []

class FakeResp:
    status = 200
    def read(self):
        return b'{"ok": true, "result": {}}'

class FakeConn:
    # plan 의 한 칸을 한 번의 request 에 쓴다. 'ok' 면 정상 응답, 'boom' 이면
    # 요청을 쓴 뒤 끊긴 것(RemoteDisconnected), 'noconnect' 면 연결 단계에서 죽은 것.
    plan = []
    def __init__(self, host, timeout=None):
        self.sock = None
        events.append(('new', host))
    def connect(self):
        step = self.plan[0] if self.plan else 'ok'
        if step == 'noconnect':
            self.plan.pop(0)
            events.append(('connect-fail', None))
            raise OSError('[Errno -3] Temporary failure in name resolution')
        events.append(('connect', None))
        self.sock = object()
    def request(self, method, path, body=None, headers=None):
        step = self.plan.pop(0) if self.plan else 'ok'
        events.append(('request', step))
        if step == 'boom':
            self.sock = None
            raise http.client.RemoteDisconnected('Remote end closed connection')
    def getresponse(self):
        return FakeResp()
    def close(self):
        self.sock = None

http.client.HTTPSConnection = FakeConn

loader = importlib.machinery.SourceFileLoader('bot', %(bot)r)
spec = importlib.util.spec_from_loader('bot', loader)
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
"""


def run(home, body, plan):
    code = (HARNESS % {"bot": str(BOT)}) + f"FakeConn.plan = {plan!r}\n" + body
    env = dict(os.environ)
    env["HOME"] = str(home)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=env, timeout=120)
    m = re.search(r"<<<(.*)>>>", p.stdout, re.S)
    if not m:
        return None, (p.stderr or p.stdout).strip()[:500]
    return json.loads(m.group(1)), ""


def main():
    check("컨트롤봇이 있다", BOT.exists(), str(BOT))
    if not BOT.exists():
        return 1

    src = BOT.read_text(encoding="utf-8")
    check("오래 논 커넥션을 버리는 기준값이 있다", "_KEEPALIVE_IDLE_TTL" in src)
    check("덮어쓰는 호출 묶음이 따로 있다", "_IDEMPOTENT_WRITE_METHODS" in src)
    check("글 보내기는 그 묶음에 없다",
          "sendMessage" not in src.split("_IDEMPOTENT_WRITE_METHODS")[1].split("}")[0]
          if "_IDEMPOTENT_WRITE_METHODS" in src else False)

    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / ".claude" / "control-bot").mkdir(parents=True)
        (home / ".claude" / "control-bot" / ".env").write_text(
            "CONTROL_BOT_TOKEN=test-token\nALLOWED_USER_ID=1\n", encoding="utf-8")

        tail = "print('<<<'+json.dumps({'r': r, 'events': events})+'>>>')\n"

        # ⑵ 연결 단계 실패는 글 보내기도 한 번 다시 건다.
        got, err = run(home, "r = bot.api('sendMessage', chat_id=1, text='x')\n" + tail,
                       ["noconnect", "ok"])
        if got is None:
            check("연결 단계에서 죽으면 글 보내기도 다시 건다", False, err)
        else:
            reqs = [e for e in got["events"] if e[0] == "request"]
            check("연결 단계에서 죽으면 글 보내기도 다시 건다",
                  got["r"].get("ok") is True and len(reqs) == 1, str(got["events"]))
            check("그때 요청은 한 번만 나간다", len(reqs) == 1, str(reqs))

        # ⑵ 두 번 연속 연결 실패면 포기한다 — 연결 재시도는 한 번뿐이다.
        got, err = run(home, "r = bot.api('sendMessage', chat_id=1, text='x')\n" + tail,
                       ["noconnect", "noconnect", "ok"])
        if got is None:
            check("연결 재시도는 한 번뿐이다", False, err)
        else:
            check("연결 재시도는 한 번뿐이다", got["r"].get("ok") is False, str(got["r"]))

        # ⑶ 요청을 쓴 뒤 끊긴 글 보내기는 다시 안 건다.
        got, err = run(home, "r = bot.api('sendMessage', chat_id=1, text='x')\n" + tail,
                       ["boom", "ok"])
        if got is None:
            check("요청을 쓴 뒤 끊기면 글 보내기는 다시 안 건다", False, err)
        else:
            reqs = [e for e in got["events"] if e[0] == "request"]
            check("요청을 쓴 뒤 끊기면 글 보내기는 다시 안 건다",
                  got["r"].get("ok") is False and len(reqs) == 1, str(got["events"]))

        # ⑷ 같은 자리에 덮어쓰는 호출은 그때도 다시 건다.
        got, err = run(home, "r = bot.api('editMessageText', chat_id=1, message_id=2, text='x')\n" + tail,
                       ["boom", "ok"])
        if got is None:
            check("고쳐쓰기는 요청을 쓴 뒤 끊겨도 다시 건다", False, err)
        else:
            reqs = [e for e in got["events"] if e[0] == "request"]
            check("고쳐쓰기는 요청을 쓴 뒤 끊겨도 다시 건다",
                  got["r"].get("ok") is True and len(reqs) == 2, str(got["events"]))

        # ⑴ 오래 논 커넥션은 요청을 쓰기 전에 버린다.
        got, err = run(
            home,
            "r = bot.api('sendMessage', chat_id=1, text='a')\n"
            "bot._tls.conn_used_at = time.time() - (bot._KEEPALIVE_IDLE_TTL + 5)\n"
            "r = bot.api('sendMessage', chat_id=1, text='b')\n" + tail,
            ["ok", "ok"])
        if got is None:
            check("오래 논 커넥션은 쓰기 전에 버린다", False, err)
        else:
            news = [e for e in got["events"] if e[0] == "new"]
            check("오래 논 커넥션은 쓰기 전에 버린다", len(news) == 2, str(got["events"]))

        # 같은 흐름에서 방금 쓴 커넥션은 그대로 재사용한다 — 매번 새로 열면 느려진다.
        got, err = run(
            home,
            "r = bot.api('sendMessage', chat_id=1, text='a')\n"
            "r = bot.api('sendMessage', chat_id=1, text='b')\n" + tail,
            ["ok", "ok"])
        if got is None:
            check("방금 쓴 커넥션은 재사용한다", False, err)
        else:
            news = [e for e in got["events"] if e[0] == "new"]
            check("방금 쓴 커넥션은 재사용한다", len(news) == 1, str(got["events"]))

    print()
    if fails:
        print(f"{len(fails)}건 실패: " + ", ".join(fails))
        return 1
    print("모두 통과")
    return 0


sys.exit(main())
