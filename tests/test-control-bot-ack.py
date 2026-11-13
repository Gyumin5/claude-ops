#!/usr/bin/env python3
"""컨트롤봇은 버튼이 눌린 것을 먼저 알린다.

운영자 지시: 버튼을 눌렀을 때 반응이 없으면 눌리고 기다리는 중인지 안 눌린 것인지
구분이 안 된다 — 확실하게 응답부터 하라.

고정하는 것 넷이다.
⑴ 받는 쪽(getUpdates 루프)이 누른 즉시 답한다 — 처리 결과를 기다리지 않는다.
⑵ 답은 빈 응답이 아니라 무엇을 하는지 적힌 한 줄이다. 빈 응답은 버튼의 시계만
   끄고 화면에 아무 말도 안 남겨서, 안 눌린 것과 구분이 안 된다.
⑶ 처리는 일꾼 스레드가 맡는다 — 몇 분 걸리는 동작이 도는 중에 누른 것도 바로
   답이 가고, 앞에 몇 건이 밀렸는지 같이 적힌다.
⑷ 한 번 누르면 답도 한 번이다 — 처리하는 쪽이 또 답하면 텔레그램이 거절한다.
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


def run_snippet(home, body):
    """봇을 띄운 뒤 넘긴 코드를 돌리고 그 코드가 찍은 JSON 을 돌려준다.

    텔레그램으로 나가는 호출을 가짜로 바꿔 무엇이 언제 나갔는지 받아 본다.
    보내기 호출은 message_id 를 돌려줘야 지우는 쪽까지 재진다.
    """
    code = (
        "import importlib.machinery, importlib.util, json, sys, threading, time\n"
        f"loader = importlib.machinery.SourceFileLoader('bot', {str(BOT)!r})\n"
        "spec = importlib.util.spec_from_loader('bot', loader)\n"
        "bot = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(bot)\n"
        "sent = []\n"
        "_lock = threading.Lock()\n"
        "def _api(method, **kw):\n"
        "    with _lock:\n"
        "        sent.append(dict(kw, _m=method))\n"
        "    return {'ok': True, 'result': {'message_id': 777}}\n"
        "bot.api = _api\n"
        + body
    )
    env = dict(os.environ)
    env["HOME"] = str(home)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=env, timeout=120)
    m = re.search(r"<<<(.*)>>>", p.stdout, re.S)
    if not m:
        return None, (p.stderr or p.stdout).strip()[:400]
    return json.loads(m.group(1)), ""


def main():
    check("컨트롤봇이 있다", BOT.exists(), str(BOT))
    if not BOT.exists():
        return 1

    src = BOT.read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / ".claude" / "control-bot").mkdir(parents=True)
        (home / ".claude" / "control-bot" / ".env").write_text(
            "CONTROL_BOT_TOKEN=test-token\nALLOWED_USER_ID=1\n", encoding="utf-8")

        # ⑵ 눌린 즉시 가는 답에 무엇을 하는지가 적힌다.
        got, err = run_snippet(
            home,
            "cb = {'id': 'q1', 'from': {'id': 1}, 'data': 'usage',\n"
            "      'message': {'chat': {'id': 5}, 'message_id': 9}}\n"
            "ok = bot.ack_press(cb, 0)\n"
            "print('<<<'+json.dumps({'ok': ok, 'sent': sent})+'>>>')\n")
        if got is None:
            check("누른 즉시 답한다", False, err)
        else:
            s = got["sent"]
            check("누를 자격이 있으면 통과시킨다", got["ok"] is True, str(got["ok"]))
            check("답은 answerCallbackQuery 한 번이다",
                  len(s) == 1 and s[0]["_m"] == "answerCallbackQuery", str(s))
            text = s[0].get("text", "") if s else ""
            check("그 답에 글이 실린다", bool(text.strip()), repr(text))
            check("무엇을 하는지 적힌다", "사용량" in text, repr(text))

        # 자격이 없는 쪽은 막되 답은 준다.
        got, err = run_snippet(
            home,
            "cb = {'id': 'q2', 'from': {'id': 99}, 'data': 'usage',\n"
            "      'message': {'chat': {'id': 5}, 'message_id': 9}}\n"
            "ok = bot.ack_press(cb, 0)\n"
            "print('<<<'+json.dumps({'ok': ok, 'sent': sent})+'>>>')\n")
        if got is None:
            check("남이 누르면 막는다", False, err)
        else:
            check("남이 누르면 막는다", got["ok"] is False, str(got["ok"]))
            check("막을 때도 답은 보낸다",
                  bool(got["sent"]) and got["sent"][0]["_m"] == "answerCallbackQuery",
                  str(got["sent"]))

        # ⑶ 앞에 밀린 것이 있으면 그 수를 같이 적는다.
        got, err = run_snippet(
            home,
            "cb = {'id': 'q3', 'from': {'id': 1}, 'data': 'list',\n"
            "      'message': {'chat': {'id': 5}, 'message_id': 9}}\n"
            "bot.ack_press(cb, 2)\n"
            "print('<<<'+json.dumps(sent)+'>>>')\n")
        text = got[0].get("text", "") if got else ""
        check("앞에 밀린 건수를 적는다", "2" in text, repr(text) or err)

        # ⑷ 처리하는 쪽은 다시 답하지 않는다.
        got, err = run_snippet(
            home,
            "bot.show_main = lambda *a, **k: None\n"
            "cb = {'id': 'q4', 'from': {'id': 1}, 'data': 'menu:main',\n"
            "      'message': {'chat': {'id': 5}, 'message_id': 9}}\n"
            "bot.handle_callback(cb)\n"
            "print('<<<'+json.dumps(sent)+'>>>')\n")
        if got is None:
            check("처리하는 쪽은 다시 답하지 않는다", False, err)
        else:
            check("처리하는 쪽은 다시 답하지 않는다",
                  not any(x["_m"] == "answerCallbackQuery" for x in got), str(got))

        # 버튼 이름표가 긴 앞가지부터 걸린다.
        got, err = run_snippet(
            home,
            "names = {k: bot.action_label(k) for k in\n"
            " ['acct:auto', 'acct:autogo', 'restart:a', 'confirm:restart:a',\n"
            "  'nudge:off:a', 'nudge:offc:a', 'zzz']}\n"
            "print('<<<'+json.dumps(names)+'>>>')\n")
        if got is None:
            check("버튼마다 이름이 있다", False, err)
        else:
            check("확인 단계와 실행 단계를 가른다",
                  got["confirm:restart:a"] != got["restart:a"], json.dumps(got, ensure_ascii=False))
            check("끄기 확인과 끄기를 가른다",
                  got["nudge:offc:a"] != got["nudge:off:a"], json.dumps(got, ensure_ascii=False))
            check("모르는 버튼에도 이름이 있다", bool(got["zzz"].strip()), got["zzz"])

        # 바로 끝나는 동작에는 "하는 중" 줄을 안 띄운다.
        got, err = run_snippet(
            home,
            "bot.Progress.DELAY = 0.4\n"
            "p = bot.Progress(5, '빠른 것')\n"
            "p.start()\n"
            "p.stop()\n"
            "time.sleep(0.8)\n"
            "print('<<<'+json.dumps(sent)+'>>>')\n")
        if got is None:
            check("빠른 동작에는 줄을 안 띄운다", False, err)
        else:
            check("빠른 동작에는 줄을 안 띄운다", got == [], str(got))

        # 느린 동작에는 띄우고, 끝나면 그 줄을 지운다.
        got, err = run_snippet(
            home,
            "bot.Progress.DELAY = 0.3\n"
            "p = bot.Progress(5, '느린 것')\n"
            "p.start()\n"
            "time.sleep(0.8)\n"
            "p.stop()\n"
            "time.sleep(0.3)\n"
            "print('<<<'+json.dumps(sent)+'>>>')\n")
        if got is None:
            check("느린 동작에는 줄을 띄운다", False, err)
        else:
            methods = [x["_m"] for x in got]
            check("느린 동작에는 줄을 띄운다", methods[:1] == ["sendMessage"], str(methods))
            check("그 줄에 이름이 적힌다",
                  bool(got) and "느린 것" in got[0].get("text", ""), str(got[:1]))
            check("끝나면 그 줄을 지운다", "deleteMessage" in methods, str(methods))

        # 로그인 코드는 되비치지 않는다.
        got, err = run_snippet(
            home,
            "labels = {'code': bot._message_label({'text': 'sk-secret-code'}),\n"
            "          'cmd': bot._message_label({'text': '/list'})}\n"
            "print('<<<'+json.dumps(labels)+'>>>')\n")
        if got is None:
            check("코드 입력을 되비치지 않는다", False, err)
        else:
            check("코드 입력을 되비치지 않는다", "secret" not in got["code"], got["code"])
            check("명령은 그 이름으로 적는다", got["cmd"] == "/list", got["cmd"])

        # ⑸ 누른 사실과 끝낸 사실이 로그에 남는다.
        # 2026-11-11 에 계정이 13:26·13:31 두 번 갈렸는데 그게 사람이 누른 것인지
        # 저절로 된 것인지 가릴 자리가 없었다. 이 로그는 오류만 적고 누름은 한 줄도
        # 안 적고 있어서, 누름이 없는 것을 "사람이 아무것도 안 했다" 로 잘못 읽었다.
        got, err = run_snippet(
            home,
            "lines = []\n"
            "bot.log = lambda m: lines.append(m)\n"
            "bot.ack_press({'id': 'q1', 'from': {'id': 1}, 'data': 'acct:go:gmoh'}, 0)\n"
            "bot.ack_press({'id': 'q2', 'from': {'id': 999}, 'data': 'acct:auto'}, 0)\n"
            "print('<<<'+json.dumps(lines)+'>>>')\n")
        if got is None:
            check("누름을 받은 자리에서 남긴다", False, err)
        else:
            joined = " ".join(got)
            check("누름을 받은 자리에서 남긴다",
                  "[누름]" in joined and "acct:go:gmoh" in joined, str(got))
            check("자격 없는 누름은 안 남긴다", "acct:auto" not in joined, str(got))

        # 끝낸 자리에도 한 줄. 둘을 맞대야 "결과가 안 왔다" 와 "늦게 왔다" 가 갈린다.
        for label, stub in (("끝내면 걸린 시간을 남긴다", "lambda cb: None"),
                            ("터져도 끝 줄은 남는다",
                             "lambda cb: (_ for _ in ()).throw(RuntimeError('x'))")):
            got, err = run_snippet(
                home,
                "import queue\n"
                "lines = []\n"
                "bot.log = lambda m: lines.append(m)\n"
                f"bot.handle_callback = {stub}\n"
                "bot.send_result_and_menu = lambda *a, **k: None\n"
                "q = queue.Queue()\n"
                "q.put(('cb', {'from': {'id': 1}, 'data': 'status',\n"
                "               'message': {'chat': {'id': 5}, 'message_id': 9}}))\n"
                "q.put(None)\n"
                "bot.worker(q)\n"
                "print('<<<'+json.dumps(lines)+'>>>')\n")
            if got is None:
                check(label, False, err)
            else:
                done = [x for x in got if x.startswith("[처리끝]")]
                check(label, len(done) == 1 and "status" in done[0]
                      and done[0].endswith("초"), str(got))

    # ⑴⑶ 받는 쪽과 처리하는 쪽이 나뉘어 있다.
    check("받는 쪽이 누른 즉시 답한다", "if ack_press(cb, waiting):" in src)
    check("처리는 일꾼에게 넘긴다", 'q.put(("cb", cb))' in src and "queue.Queue()" in src)
    check("일꾼은 따로 돈다", "threading.Thread(target=worker" in src)
    check("일꾼이 터져도 화면에 남긴다", "⚠️ 처리 중 오류" in src)

    return 1 if fails else 0


if __name__ == "__main__":
    rc = main()
    print(f"\n{'실패: ' + ', '.join(fails) if fails else '모두 통과'}")
    sys.exit(rc)
