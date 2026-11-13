#!/usr/bin/env python3
"""버튼으로 계정을 고르는 길이 지키는 것.

가짜 자격으로만 돈다 — HOME 을 임시 디렉토리로 돌려서 진짜 토큰은 한 바이트도 안 걸린다.

보는 것 셋이다.
⑴ 지금 어느 계정으로 도는지를 이름으로 적어 두고 그 이름을 돌려준다. 자격 파일끼리
   비교해서는 못 가린다 — 쓰는 동안 토큰이 저절로 갱신돼 홈에 있는 사본과 글자가 달라진다.
⑵ 떠나는 계정의 자격을 그 홈에 되돌려 둔다. 안 돌려놓으면 그 홈 사본이 낡아서 다음에
   그 계정으로 돌아올 때 이미 밀려난 갱신표를 들고 간다.
⑶ 컨트롤봇 자판이 계정 목록을 그리고 지금 쓰는 것에 표를 단다.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SWITCH = REPO / "bin" / "claude-accounts"
BOT = REPO / "bin" / "claude-control-bot"

fails = []


def check(name, cond, detail=""):
    # 통과한 줄에는 까닭을 안 붙인다 — 붙이면 쪽 전문이 매 줄 따라 나와서 실패한 줄을 못 찾는다.
    print(("PASS  " + name) if cond
          else ("FAIL  " + name + (f"  — {str(detail)[:200]}" if detail else "")))
    if not cond:
        fails.append(name)


def switch(home, *args):
    env = dict(os.environ, HOME=str(home))
    p = subprocess.run([str(SWITCH), *args], capture_output=True, text=True,
                       timeout=60, env=env)
    return p.returncode, (p.stdout + p.stderr)


def cred_body(token, plan="team"):
    return json.dumps({"claudeAiOauth": {
        "accessToken": token, "refreshToken": "r-" + token,
        "expiresAt": 1799999999999, "subscriptionType": plan}})


def token_of(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def make_home(base):
    """계정 홈 셋을 가진 가짜 HOME. sw9 는 로그인 안 한 홈이다."""
    home = base / "home"
    (home / ".claude").mkdir(parents=True)
    for name, plan in (("acct-a", "team"), ("acct-b", "team"), ("boss", "max")):
        d = home / "claude-accounts" / name / ".claude"
        d.mkdir(parents=True)
        (d / ".credentials.json").write_text(cred_body("tok-" + name, plan),
                                             encoding="utf-8")
    (home / "claude-accounts" / "sw9" / ".claude").mkdir(parents=True)
    return home


def call_bot(home, snippet):
    code = (
        "import importlib.machinery, importlib.util, json, sys\n"
        f"loader = importlib.machinery.SourceFileLoader('bot', {str(BOT)!r})\n"
        "spec = importlib.util.spec_from_loader('bot', loader)\n"
        "bot = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(bot)\n"
        f"print('<<<' + json.dumps({snippet}) + '>>>')\n"
    )
    env = dict(os.environ, HOME=str(home))
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=env, timeout=120)
    m = re.search(r"<<<(.*)>>>", p.stdout, re.S)
    if not m:
        return None, (p.stderr or p.stdout).strip()[:300]
    return json.loads(m.group(1)), ""


check("교체 명령이 있다", SWITCH.exists(), str(SWITCH))
check("컨트롤봇이 있다", BOT.exists(), str(BOT))
if not (SWITCH.exists() and BOT.exists()):
    print("\n0/0 passed")
    sys.exit(1)

# --- 1. 이름을 적어 두고 돌려준다 -------------------------------------------
with tempfile.TemporaryDirectory() as td:
    home = make_home(Path(td))
    live = home / ".claude" / ".credentials.json"

    rc, out = switch(home, "--which")
    check("고르기 전에는 모른다고 답한다", rc == 0 and out.strip() == "모름",
          f"rc={rc} out={out.strip()[:120]}")

    rc, out = switch(home, "--use", "acct-a")
    check("계정을 고르면 그 홈 자격이 live 로 간다",
          rc == 0 and token_of(live) == "tok-acct-a", f"rc={rc} out={out.strip()[:200]}")
    rc, out = switch(home, "--which")
    check("고른 뒤에는 그 이름을 돌려준다", out.strip() == "acct-a", out.strip()[:120])
    check("live 자격 권한은 600 이다",
          oct(live.stat().st_mode & 0o777)[2:] == "600",
          oct(live.stat().st_mode & 0o777)[2:])

    rc, out = switch(home, "--list")
    check("목록이 지금 쓰는 것에 별표를 단다",
          rc == 0 and re.search(r"^\* acct-a\b", out, re.M) is not None, out.strip()[:200])
    check("목록이 로그인 안 한 홈을 그렇게 적는다",
          "sw9" in out and "로그인 안 함" in out, out.strip()[:200])

# --- 2. 떠나는 계정의 갱신분을 그 홈에 되돌린다 ------------------------------
with tempfile.TemporaryDirectory() as td:
    home = make_home(Path(td))
    live = home / ".claude" / ".credentials.json"
    acct_a_home = home / "claude-accounts" / "acct-a" / ".claude" / ".credentials.json"

    switch(home, "--use", "acct-a")
    # 쓰는 동안 토큰이 갱신된 것을 흉내 낸다 — live 만 새 글자가 되고 홈 사본은 그대로다.
    live.write_text(cred_body("tok-acct-a-갱신됨"), encoding="utf-8")
    rc, out = switch(home, "--use", "acct-b")
    check("다른 계정으로 가면 live 가 그 계정 자격으로 바뀐다",
          rc == 0 and token_of(live) == "tok-acct-b", f"rc={rc} {out.strip()[:200]}")
    check("떠난 계정의 갱신분이 그 홈에 남는다",
          token_of(acct_a_home) == "tok-acct-a-갱신됨", token_of(acct_a_home))
    rc, out = switch(home, "--which")
    check("이름도 같이 넘어간다", out.strip() == "acct-b", out.strip()[:120])

    # 다시 acct-a 으로 돌아오면 갱신된 쪽을 들고 간다 — 낡은 사본이 아니다.
    switch(home, "--use", "acct-a")
    check("돌아올 때 낡은 사본이 아니라 갱신분을 쓴다",
          token_of(live) == "tok-acct-a-갱신됨", token_of(live))

# --- 3. 거절해야 하는 것 -----------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    home = make_home(Path(td))
    live = home / ".claude" / ".credentials.json"
    switch(home, "--use", "acct-a")

    for label, name in (("없는 계정", "nope"), ("로그인 안 한 홈", "sw9"),
                        ("경로가 섞인 이름", "../etc")):
        rc, out = switch(home, "--use", name)
        check(f"거절한다: {label}", rc != 0, f"rc={rc} {out.strip()[:120]}")
    check("거절해도 지금 자격은 그대로다", token_of(live) == "tok-acct-a", token_of(live))
    rc, out = switch(home, "--which")
    check("거절해도 이름은 그대로다", out.strip() == "acct-a", out.strip()[:120])

# --- 4. 이름 모르는 로그인은 표식을 지운다 -----------------------------------
# 릴레이 로그인은 어느 계정인지를 안 돌려준다. 옛 이름을 그냥 두면 다음 전환이 엉뚱한
# 홈에 그 자격을 되돌려 써서 그 계정을 망가뜨린다.
with tempfile.TemporaryDirectory() as td:
    home = make_home(Path(td))
    switch(home, "--use", "acct-a")
    fresh = Path(td) / "fresh.json"
    fresh.write_text(cred_body("tok-누군지모름"), encoding="utf-8")
    rc, out = switch(home, "--apply-login", str(fresh))
    check("이름 없는 로그인도 적용은 된다",
          rc == 0 and token_of(home / ".claude" / ".credentials.json") == "tok-누군지모름",
          f"rc={rc} {out.strip()[:200]}")
    rc, out = switch(home, "--which")
    check("그때는 이름을 모른다고 되돌린다", out.strip() == "모름", out.strip()[:120])

# --- 5. 컨트롤봇 자판 --------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    home = make_home(Path(td))
    (home / ".claude" / "control-bot").mkdir(parents=True)
    (home / ".claude" / "control-bot" / ".env").write_text(
        "CONTROL_BOT_TOKEN=test-token\nALLOWED_USER_ID=1\n", encoding="utf-8")
    switch(home, "--use", "acct-b")

    rows, err = call_bot(home, "bot.main_menu_markup()['inline_keyboard']")
    check("메인 메뉴에 계정 버튼이 있다",
          rows is not None and any(b.get("callback_data") == "acct:menu"
                                   for r in rows for b in r),
          err or str(rows))
    check("메인 메뉴는 그대로 줄마다 둘이다",
          rows is not None and {len(r) for r in rows} == {2},
          str([len(r) for r in rows]) if rows else err)

    cur, err = call_bot(home, "bot.account_current()")
    check("봇이 지금 계정을 읽는다", cur == "acct-b", f"{cur!r} {err}")

    text, err = call_bot(home, "bot.account_menu_text(bot.account_current())")
    check("계정 쪽에 홈 넷이 다 보인다",
          text is not None and all(n in text for n in ("acct-a", "acct-b", "boss", "sw9")),
          err or (text or "")[:200])
    check("지금 쓰는 것에 표가 붙는다", text is not None and "● acct-b" in text,
          (text or "")[:200])
    check("로그인 안 한 홈은 그렇게 적는다",
          text is not None and "로그인 안 함" in text, (text or "")[:200])
    check("요금제를 같이 적는다", text is not None and "max" in text, (text or "")[:200])

    kb, err = call_bot(home, "bot.account_menu_markup(bot.account_current())['inline_keyboard']")
    check("계정 자판을 만든다", kb is not None, err)
    if kb:
        datas = [b.get("callback_data") for r in kb for b in r]
        check("계정마다 버튼이 하나씩 있다",
              all(f"acct:pick:{n}" in datas for n in ("acct-a", "acct-b", "boss", "sw9")),
              str(datas))
        check("새로 로그인도 여기서 간다", "relogin-confirm" in datas, str(datas))
        check("뒤로 가기는 맨 마지막 줄에 혼자 선다",
              len(kb[-1]) == 1 and "Back" in kb[-1][0].get("text", ""), str(kb[-1]))

print(f"\n{'실패: ' + ', '.join(fails) if fails else '모두 통과'}")
sys.exit(1 if fails else 0)
