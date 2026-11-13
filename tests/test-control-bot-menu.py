#!/usr/bin/env python3
"""컨트롤봇 자판이 지키는 모양.

운영자가 "두 개 있다가 한 개 있다가" 를 고치라고 한 자리다(2026-11-06 운영자 지시).
줄마다 개수가 달라지면 같은 버튼이 매번 다른 자리에 오고, 그래서 엉뚱한 것을 누른다.
그래서 셋을 고정한다. ⑴ 메인 메뉴는 모든 줄이 둘이다. ⑵ 세션별 동작처럼 확인 없이
바로 도는 위험한 것은 한 줄에 하나다 — 끄기 옆에 켜기가 붙으면 한 칸 미끄러진 것만으로
돌던 세션이 꺼진다. ⑶ 뒤로 가는 줄은 맨 마지막에 혼자 선다.
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
    code = (
        "import importlib.machinery, importlib.util, json, sys\n"
        f"loader = importlib.machinery.SourceFileLoader('bot', {str(BOT)!r})\n"
        "spec = importlib.util.spec_from_loader('bot', loader)\n"
        "bot = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(bot)\n"
        f"print('<<<' + json.dumps({snippet}) + '>>>')\n"
    )
    env = dict(os.environ)
    env["HOME"] = str(home)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=env, timeout=120)
    m = re.search(r"<<<(.*)>>>", p.stdout, re.S)
    if not m:
        return None, (p.stderr or p.stdout).strip()[:300]
    return json.loads(m.group(1)), ""


def run_snippet(home, body):
    """봇을 띄운 뒤 넘긴 코드를 돌리고 그 코드가 찍은 JSON 을 돌려준다.

    화면 글은 함수가 돌려주는 값이 아니라 텔레그램으로 나가는 인자라, 보내는 자리를
    가짜로 바꿔 받아 본다.
    """
    code = (
        "import importlib.machinery, importlib.util, json, sys\n"
        f"loader = importlib.machinery.SourceFileLoader('bot', {str(BOT)!r})\n"
        "spec = importlib.util.spec_from_loader('bot', loader)\n"
        "bot = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(bot)\n"
        "sent = []\n"
        "bot.api = lambda method, **kw: sent.append(dict(kw, _m=method))\n"
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


def is_back(row):
    return len(row) == 1 and "Back" in row[0].get("text", "")


def main():
    check("컨트롤봇이 있다", BOT.exists(), str(BOT))
    if not BOT.exists():
        return 1

    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / ".claude" / "control-bot").mkdir(parents=True)
        (home / ".claude" / "control-bot" / ".env").write_text(
            "CONTROL_BOT_TOKEN=test-token\nALLOWED_USER_ID=1\n", encoding="utf-8")

        rows, err = call(home, "bot.main_menu_markup()['inline_keyboard']")
        check("메인 메뉴를 만든다", rows is not None, err)
        if rows is None:
            return 1

        widths = [len(r) for r in rows]
        check("메인 메뉴는 줄마다 둘이다", set(widths) == {2}, str(widths))
        datas = [b.get("callback_data") for r in rows for b in r]
        check("같은 곳으로 가는 버튼이 겹치지 않는다",
              len(datas) == len(set(datas)), str(datas))
        check("빈 이름이 없다", all(b.get("text", "").strip() for r in rows for b in r))
        check("메인에는 뒤로 가기가 없다", not any(is_back(r) for r in rows))

        act, err = call(home, "bot.session_action_markup('demo')['inline_keyboard']")
        check("세션 동작 자판을 만든다", act is not None, err)
        if act is not None:
            body = [r for r in act if not is_back(r)]
            check("세션 동작은 한 줄에 하나다", all(len(r) == 1 for r in body),
                  str([len(r) for r in body]))
            check("뒤로 가기는 맨 마지막 줄에 혼자 선다", is_back(act[-1]),
                  str(act[-1]))

        # 계정 자판 — 계정 이름 버튼은 둘씩, 실제로 계정을 바꾸는 자동 버튼과
        # 새로 로그인은 한 줄에 하나다(자판 원칙 ⑵). 자동 버튼이 없어지면 운영자가
        # 여유 있는 계정을 손으로 골라야 하고, 그러려면 넷의 한도를 먼저 재야 한다.
        for n in ("acct-a", "acct-b", "acct-c"):
            (home / "claude-accounts" / n).mkdir(parents=True)
        acct, err = call(home, "bot.account_menu_markup('acct-a')['inline_keyboard']")
        check("계정 자판을 만든다", acct is not None, err)
        if acct is not None:
            flat = [b.get("callback_data") for r in acct for b in r]
            check("자동으로 고르는 버튼이 있다", "acct:auto" in flat, str(flat))
            auto_row = [r for r in acct if any(b.get("callback_data") == "acct:auto"
                                               for b in r)]
            check("자동 버튼은 한 줄에 혼자 선다",
                  bool(auto_row) and len(auto_row[0]) == 1, str(auto_row))
            names = [r for r in acct
                     if all((b.get("callback_data") or "").startswith("acct:pick:")
                            for b in r)]
            check("계정 이름 버튼은 둘씩 선다",
                  bool(names) and len(names[0]) == 2, str([len(r) for r in names]))
            check("뒤로 가기는 맨 마지막 줄에 혼자 선다", is_back(acct[-1]), str(acct[-1]))

        # 자동 버튼은 고르는 규칙을 스스로 쓰지 않고 밤 전환 코드를 부른다. 두 벌이
        # 되면 한쪽만 고쳐져서 버튼과 밤 전환이 다른 계정을 고른다.
        src = BOT.read_text(encoding="utf-8")
        check("자동은 밤 전환 코드를 부른다", "NIGHT_SWITCH_BIN" in src
              and "run_night_switch(\"--apply\"" in src)
        check("자동도 한 번 더 묻는다", 'confirm_markup("acct:autogo"' in src)

        conf, err = call(home, "bot.confirm_markup('do:x', '시험')['inline_keyboard']")
        check("확인 자판은 한 줄에 예·아니오 둘이다",
              conf is not None and len(conf) == 1 and len(conf[0]) == 2,
              str(conf) if conf else err)

        # 지금 쓰는 계정은 화면마다 보인다(운영자 지시). 계정 메뉴에 들어가야만
        # 보이면 버튼을 누르기 전에 어느 계정 한도를 깎는지 모른다.
        got, err = call(home, "bot.with_account('본문')")
        check("글 끝에 계정 줄을 붙인다",
              got is not None and got.startswith("본문") and "👤 계정:" in got,
              got or err)
        got2, err = call(home, "bot.with_account(bot.with_account('본문'))")
        check("두 번 붙여도 한 줄만 남는다",
              got2 is not None and got2.count("👤 계정:") == 1, got2 or err)

        sent, err = run_snippet(home, "bot.show_main(1)\nprint('<<<'+json.dumps(sent)+'>>>')\n")
        check("메인 화면에 계정이 적힌다",
              sent and "👤 계정:" in sent[0].get("text", ""),
              (sent[0].get("text") if sent else err))

        sent, err = run_snippet(
            home,
            "bot.send_result_and_menu(1, '막대 [###]', mono=True)\n"
            "print('<<<'+json.dumps(sent)+'>>>')\n")
        text = sent[0].get("text", "") if sent else ""
        check("막대 화면에도 계정이 적힌다", "👤 계정:" in text, text or err)
        check("그 줄은 고정폭 블록 밖에 있다",
              "</pre>" in text and text.index("</pre>") < text.index("👤 계정:"), text)

        sent, err = run_snippet(
            home,
            "bot.send_result_and_menu(1, '평문 결과')\n"
            "print('<<<'+json.dumps(sent)+'>>>')\n")
        text = sent[0].get("text", "") if sent else ""
        check("평문 결과에도 계정이 적힌다",
              "평문 결과" in text and "👤 계정:" in text, text or err)

    return 1 if fails else 0


if __name__ == "__main__":
    rc = main()
    print(f"\n{'실패: ' + ', '.join(fails) if fails else '모두 통과'}")
    sys.exit(rc)
