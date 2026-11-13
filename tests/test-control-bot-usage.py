#!/usr/bin/env python3
"""컨트롤봇의 계정 사용량 버튼이 지키는 것들.

여기서 고정하는 것 셋이다. ⑴ 가는 길은 저장소 밖 설정 한 줄이 정본이다 — 그 줄에
서버 주소가 들어가므로 코드에 박으면 공용 저장소로 새고, 호스트마다 가는 길도
다르다. ⑵ 설정이 없거나 저쪽이 안 되는 회차에도 봇이 안 죽고 까닭을 말한다 —
컨트롤봇은 사고 때 쓰는 통로라 버튼 하나가 루프를 깨면 안 된다. ⑶ 메뉴에 그
버튼이 실제로 있다.
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


def call(home, snippet):
    return call_with(home, snippet, "")


def call_with(home, snippet, prelude):
    """임시 홈으로 봇 모듈을 띄워 한 조각을 돌리고 그 값을 받는다."""
    code = (
        # 봇 파일에 확장자가 없어 보통 방법으로는 못 읽는다 — 읽는 쪽을 직접 지정한다.
        "import importlib.machinery, importlib.util, json, sys\n"
        f"loader = importlib.machinery.SourceFileLoader('bot', {str(BOT)!r})\n"
        "spec = importlib.util.spec_from_loader('bot', loader)\n"
        "bot = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(bot)\n"
        + prelude
        + f"print('<<<' + json.dumps({snippet}) + '>>>')\n"
    )
    env = dict(os.environ)
    env["HOME"] = str(home)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=env, timeout=120)
    m = re.search(r"<<<(.*)>>>", p.stdout, re.S)
    if not m:
        return None, (p.stderr or p.stdout).strip()[:300]
    return json.loads(m.group(1)), ""


def main():
    check("컨트롤봇이 있다", BOT.exists(), str(BOT))
    if not BOT.exists():
        return 1

    src = BOT.read_text(encoding="utf-8")

    # ⑶ 메뉴 버튼 — 소스를 열지 않고 실제로 만들어진 자판을 본다.
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / ".claude" / "control-bot").mkdir(parents=True)
        (home / ".claude" / "control-bot" / ".env").write_text(
            "CONTROL_BOT_TOKEN=test-token\nALLOWED_USER_ID=1\n", encoding="utf-8")
        cfgdir = home / ".config" / "claude-usage"
        cfgdir.mkdir(parents=True)
        remote = cfgdir / "remote"

        rows, err = call(home, "bot.main_menu_markup()['inline_keyboard']")
        check("봇 모듈이 떠서 자판을 만든다", rows is not None, err)
        if rows is None:
            return 1
        datas = [b.get("callback_data") for row in rows for b in row]
        check("메뉴에 계정 사용량 버튼이 있다", "usage" in datas, str(datas))
        texts = [b.get("text", "") for row in rows for b in row
                 if b.get("callback_data") == "usage"]
        check("버튼 이름이 우리 말이다", any("사용량" in t for t in texts), str(texts))

        # ⑵-가 설정이 없을 때
        out, err = call(home, "bot.read_usage()")
        check("설정이 없으면 안 죽고 어디에 적을지 말한다",
              out is not None and str(remote) in (out or ""), (out or err)[:120])

        # ⑵-나 설정이 비었을 때
        remote.write_text("\n", encoding="utf-8")
        out, err = call(home, "bot.read_usage()")
        check("설정이 비어 있어도 안 죽는다",
              out is not None and "비어" in (out or ""), (out or err)[:120])

        # 성공 회차 — 저쪽 대신 그 자리에서 글자를 뱉는 명령을 건다.
        remote.write_text("printf '계정별 사용량\\nacct-a 5시간 1%%\\n'\n", encoding="utf-8")
        out, err = call(home, "bot.read_usage()")
        check("저쪽이 준 글자를 그대로 보여 준다",
              out is not None and "acct-a" in (out or ""), (out or err)[:120])

        # 실패 회차 — 조용히 빈 값으로 넘어가면 운영자가 재 봤다고 착각한다.
        remote.write_text("echo '붙지 못했다' >&2; exit 7\n", encoding="utf-8")
        out, err = call(home, "bot.read_usage()")
        check("실패하면 까닭과 끝번호를 같이 말한다",
              out is not None and "rc=7" in (out or "") and "붙지 못했다" in (out or ""),
              (out or err)[:120])

        # 오래 걸리는 회차 — 기다리다 영원히 안 돌아오면 봇 루프가 묶인다.
        remote.write_text("sleep 30\n", encoding="utf-8")
        out, err = call(home, "bot.read_usage(timeout=2)")
        check("오래 걸리면 끊고 그렇다고 말한다",
              out is not None and "2초" in (out or ""), (out or err)[:120])

        # ⑸ 두 자리를 같이 긁는다(운영자 지시). 차례로 부르면 이쪽 재기가 끝나야
        #    저쪽 접속이 시작해서, 둘 다 느린 회차에 걸리는 시간이 합이 된다.
        remote.write_text("printf '저쪽\\n'\n", encoding="utf-8")
        pre = ("import time\n"
               "_r, _l = bot.read_usage_remote, bot.read_usage_local\n"
               "bot.read_usage_remote = lambda t=90: (time.sleep(1.2), '저쪽')[-1]\n"
               "bot.read_usage_local = lambda t=120: (time.sleep(1.2), '이쪽')[-1]\n"
               "_t0 = time.time()\n"
               "_out = bot.read_usage()\n"
               "_took = time.time() - _t0\n")
        got, err = call_with(home, "[_out, _took]", pre)
        check("두 자리를 같이 긁는다 — 걸리는 시간이 합이 아니라 느린 쪽 하나다",
              got is not None and got[1] < 2.0, str(got)[:160] + (err or "")[:120])
        check("두 자리 결과를 한 글에 같이 담는다",
              got is not None and "저쪽" in got[0] and "이쪽" in got[0], str(got)[:160])

        # 한쪽이 터져도 다른 쪽은 보여 준다 — 사고 때 쓰는 통로라 반쪽이라도 나와야 한다.
        pre_bad = ("_r = bot.read_usage_remote\n"
                   "def _boom(t=90):\n"
                   "    raise RuntimeError('저쪽 터짐')\n"
                   "bot.read_usage_remote = _boom\n"
                   "bot.read_usage_local = lambda t=120: '이쪽 수치'\n"
                   "_out = bot.read_usage()\n")
        got, err = call_with(home, "_out", pre_bad)
        check("한쪽이 터져도 다른 쪽 수치는 나온다",
              got is not None and "이쪽 수치" in got and "저쪽 터짐" in got,
              str(got)[:160] + (err or "")[:120])

    # ⑷ 칸을 맞춰 쓴 글은 고정폭으로 보낸다. 텔레그램 기본 비례 폰트에서는 막대가
    #    줄마다 어긋나고, 백틱만 적고 parse_mode 를 안 넘기면 백틱이 글자로 보인다.
    sent = []
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / ".claude" / "control-bot").mkdir(parents=True)
        (home / ".claude" / "control-bot" / ".env").write_text(
            "CONTROL_BOT_TOKEN=test-token\nALLOWED_USER_ID=1\n", encoding="utf-8")
        snippet = ("(lambda b: (setattr(b, 'api', lambda m, **k: rec.append((m, k))),"
                   " b.send_result_and_menu(1, 'acct-a\\n 5시간 <1%>', mono=True),"
                   " b.send_result_and_menu(1, '그냥 글'),"
                   " rec)[-1])(bot)")
        code_pre = "rec = []\n"
        sent, err = call_with(home, snippet, code_pre)
    check("고정폭으로 보내면 블록으로 감싸고 그 모드를 같이 넘긴다",
          sent and sent[0][1].get("parse_mode") == "HTML"
          and sent[0][1]["text"].startswith("<pre>"), str(sent[:1])[:200])
    check("블록 안의 꺾쇠는 글자로 바꿔 넣는다",
          sent and "&lt;1%&gt;" in sent[0][1]["text"], str(sent[:1])[:200])
    # 평문은 감싸지 않는다. 다만 지금 쓰는 계정 한 줄은 어느 화면에도 붙는다
    # (운영자 지시) — 그 줄은 블록 밖이라 평문 쪽 모드도 그대로다.
    check("평문은 고정폭으로 감싸지 않는다",
          len(sent) > 1 and "parse_mode" not in sent[1][1]
          and sent[1][1]["text"].startswith("그냥 글"), str(sent[1:2])[:200])
    check("평문에도 계정 줄이 붙는다",
          len(sent) > 1 and "👤 계정:" in sent[1][1]["text"], str(sent[1:2])[:200])
    check("고정폭 글의 계정 줄은 블록 밖에 있다",
          sent and "</pre>" in sent[0][1]["text"]
          and sent[0][1]["text"].index("</pre>") < sent[0][1]["text"].index("👤 계정:"),
          str(sent[:1])[:200])
    # 세션 목록은 칸을 맞춰 쓴 글이라 고정폭이 필요하다. 사용량은 저쪽이 막대 없는
    # 글을 보내므로 맞출 칸이 없고, 감싸면 오히려 줄이 어긋나 보인다(운영자 지시).
    check("세션 목록은 고정폭으로 보낸다", src.count("mono=True") == 2, src.count("mono=True"))
    check("사용량은 고정폭으로 감싸지 않는다",
          "read_usage(), mono=True" not in src and src.count("read_usage())") == 2,
          src.count("read_usage())"))

    # ⑴ 주소가 코드에 안 박힌다
    pub = [a for a in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", src)
           if not a.startswith("127.") and a != "0.0.0.0"]
    check("봇 코드에 바깥 주소가 안 박혀 있다", not pub, str(pub[:3]))
    check("가는 길은 설정 파일에서 읽는다",
          "USAGE_REMOTE" in src and "claude-usage" in src)

    return 1 if fails else 0


if __name__ == "__main__":
    rc = main()
    print(f"\n{'실패: ' + ', '.join(fails) if fails else '모두 통과'}")
    sys.exit(rc)
