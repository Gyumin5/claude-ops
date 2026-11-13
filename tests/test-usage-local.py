#!/usr/bin/env python3
"""컨트롤봇이 이 기계 안에서 바로 읽는 계정 사용량 경로.

운영자의 회사·개인 계정 수치는 여럿이 보는 자리(열쇠글을 건 사용량 서버)에 안 올린다 —
그 자리는 회사 공용 계정만 받는다(2026-11-06 운영자 지시, bin/claude-usage-web 의 시작
게이트). 그래서 운영자 계정은 저쪽을 안 거치고 이 기계 안에서 바로 읽는다(운영자 지시).

보는 것 넷이다.
⑴ 어느 홈을 그렇게 읽을지는 저장소 밖 설정에서 읽고, 경로가 섞인 이름은 버린다.
⑵ 설정이 없으면 그 절이 통째로 빠진다 — 없는 것이 고장이 아니다.
⑶ 수치가 새것이면 세션을 다시 안 띄운다. 보기만 해도 한도가 깎이면 안 된다.
⑷ 낡았으면 그 홈만 다시 띄운다.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BOT = REPO / "bin" / "claude-control-bot"

fails = []


def check(name, cond, detail=""):
    print(("PASS  " + name) if cond
          else ("FAIL  " + name + (f"  — {str(detail)[:300]}" if detail else "")))
    if not cond:
        fails.append(name)


def call_bot(home, snippet, extra_path=None):
    """가짜 HOME 으로 봇을 불러 표현식 하나를 평가한다."""
    code = (
        "import importlib.machinery, importlib.util, json, sys\n"
        f"loader = importlib.machinery.SourceFileLoader('bot', {str(BOT)!r})\n"
        "spec = importlib.util.spec_from_loader('bot', loader)\n"
        "bot = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(bot)\n"
        f"print('<<<' + json.dumps({snippet}) + '>>>')\n"
    )
    env = dict(os.environ, HOME=str(home))
    if extra_path:
        env["PATH"] = f"{extra_path}:{env.get('PATH', '')}"
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=env, timeout=120)
    out = p.stdout
    i, j = out.find("<<<"), out.rfind(">>>")
    if i < 0 or j < 0:
        return None, (p.stderr or out).strip()[:300]
    return json.loads(out[i + 3:j]), ""


def make_home(base, names_body=None):
    home = base / "home"
    (home / ".config" / "claude-usage").mkdir(parents=True)
    # 봇은 뜰 때 자기 열쇠글 파일을 읽는다. 가짜 값을 둬서 진짜 봇 설정을 안 건드린다.
    (home / ".claude" / "control-bot").mkdir(parents=True)
    (home / ".claude" / "control-bot" / ".env").write_text(
        "CONTROL_BOT_TOKEN=test-token\nALLOWED_USER_ID=1\n", encoding="utf-8")
    if names_body is not None:
        (home / ".config" / "claude-usage" / "local-names").write_text(
            names_body, encoding="utf-8")
    return home


def bot_copy(d):
    """가짜 명령 옆에 둔 봇 사본. 봇은 자기 옆 파일을 부르므로 사본이 있어야 한다."""
    (d / "claude-control-bot").write_bytes(BOT.read_bytes())
    (d / "claude-control-bot").chmod(0o755)
    # 봇이 뜨면서 같은 디렉토리의 login_relay 를 불러들인다.
    (d / "login_relay.py").write_bytes((REPO / "bin" / "login_relay.py").read_bytes())
    return d / "claude-control-bot"


check("컨트롤봇이 있다", BOT.exists(), str(BOT))
if not BOT.exists():
    print("\n0/0 passed")
    sys.exit(1)

# --- 1. 설정에서 이름을 읽는다 ----------------------------------------------
with tempfile.TemporaryDirectory() as td:
    home = make_home(Path(td),
                     "# 주석\n\n  alpha  \nbeta\n../etc\n/abs\n.hidden\n")
    names, err = call_bot(home, "bot.local_names()")
    check("설정에서 이름을 읽고 주석·빈 줄을 버린다", names == ["alpha", "beta"],
          f"{names!r} {err}")

with tempfile.TemporaryDirectory() as td:
    home = make_home(Path(td))          # 설정 파일 없음
    names, err = call_bot(home, "bot.local_names()")
    check("설정이 없으면 빈 목록이다", names == [], f"{names!r} {err}")
    body, err = call_bot(home, "bot.read_usage_local()")
    check("설정이 없으면 그 절이 통째로 빠진다", body == "", f"{body!r} {err}")

# --- 2. 새 수치면 다시 안 띄운다 --------------------------------------------
# 진짜 명령 둘을 가짜로 바꿔 끼운다. 읽는 쪽은 미리 정한 수치를 돌려주고, 띄우는 쪽은
# 불릴 때마다 자취를 남긴다 — 불렸는지 아닌지가 이 검사의 전부다.
def fake_bins(base, cached_ago):
    """가짜 claude-accounts-usage·claude-account-probe 를 만든 디렉토리."""
    d = base / "fakebin"
    d.mkdir()
    trace = base / "probe-trace"
    rows = {"now": time.time(),
            "rows": [{"label": "alpha", "cached_at": time.time() - cached_ago},
                     {"label": "beta", "cached_at": time.time() - cached_ago}]}
    (d / "claude-accounts-usage").write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        f"ROWS = {rows!r}\n"
        "if '--json' in sys.argv:\n"
        "    print(json.dumps(ROWS))\n"
        "else:\n"
        "    print('계정별 사용량 (머리줄)')\n"
        "    print('alpha  5시간 1% (23:30 초기화)  7일 2% (12-31(수) 23:30 초기화)  방금 값')\n"
        "    print('beta   수치 없음 — 로그인은 됐는데 그 홈에서 세션이 아직 안 돌았다')\n",
        encoding="utf-8")
    (d / "claude-account-probe").write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"open({str(trace)!r}, 'a').write(' '.join(sys.argv[1:]) + chr(10))\n",
        encoding="utf-8")
    # 그리는 자리는 가짜로 안 바꾼다 — 이 기계 계정 줄도 저 기계 줄과 같은 서식으로
    # 나오는지가 이 검사의 한 칸이고, 그걸 보려면 진짜 그리는 도구가 있어야 한다.
    shutil.copy2(REPO / "bin" / "claude-usage-gauge", d / "claude-usage-gauge")
    for f in d.iterdir():
        f.chmod(0o755)
    return d, trace


for label, ago, want_probe in (("새 수치면 다시 안 띄운다", 60, False),
                               ("낡았으면 그 홈만 다시 띄운다", 99999, True)):
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        home = make_home(base, "alpha\nbeta\n")
        d, trace = fake_bins(base, ago)
        bot = bot_copy(d)
        code = (
            "import importlib.machinery, importlib.util, json\n"
            f"loader = importlib.machinery.SourceFileLoader('bot', {str(bot)!r})\n"
            "spec = importlib.util.spec_from_loader('bot', loader)\n"
            "bot = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(bot)\n"
            "print('<<<' + json.dumps(bot.read_usage_local()) + '>>>')\n"
        )
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=dict(os.environ, HOME=str(home)), timeout=120)
        out = p.stdout
        i, j = out.find("<<<"), out.rfind(">>>")
        body = json.loads(out[i + 3:j]) if i >= 0 and j >= 0 else None
        called = trace.exists()
        check(label, called == want_probe,
              f"띄움={called} 바람={want_probe} {(p.stderr or '')[:200]}")
        if label.startswith("새"):
            check("글줄에 계정 줄이 담긴다",
                  body is not None and "alpha" in body and "beta" in body,
                  f"{body!r} {(p.stderr or '')[:200]}")
            check("머리줄은 우리 것으로 바꿔 단다",
                  body is not None and body.startswith("이 기계 계정"), f"{body!r}")
            # 저 기계 줄과 같은 서식으로 선다 — 이름 한 줄, 그 아래 수치 줄(운영자 8754).
            lines = (body or "").splitlines()
            check("이 기계 계정 줄도 저 기계와 같은 서식이다",
                  "alpha" in lines and lines[lines.index("alpha") + 1].startswith("5시간 1% ·"),
                  f"{lines!r}")
            check("수치 없는 계정도 이름 줄과 까닭 줄로 선다",
                  "beta" in lines and lines[lines.index("beta") + 1].startswith("수치 없음"),
                  f"{lines!r}")

# --- 3. 저쪽이 안 돼도 이쪽은 보인다 ----------------------------------------
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    home = make_home(base, "alpha\n")
    d, _ = fake_bins(base, 60)
    bot = bot_copy(d)
    code = (
        "import importlib.machinery, importlib.util, json\n"
        f"loader = importlib.machinery.SourceFileLoader('bot', {str(bot)!r})\n"
        "spec = importlib.util.spec_from_loader('bot', loader)\n"
        "bot = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(bot)\n"
        "print('<<<' + json.dumps(bot.read_usage()) + '>>>')\n"
    )
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=dict(os.environ, HOME=str(home)), timeout=120)
    out = p.stdout
    i, j = out.find("<<<"), out.rfind(">>>")
    body = json.loads(out[i + 3:j]) if i >= 0 and j >= 0 else None
    # 저쪽 접속 설정이 아예 없는 자리다 — 그래도 이쪽 수치는 같이 나와야 한다.
    check("저쪽 길이 없어도 이 기계 수치는 나온다",
          body is not None and "이 기계 계정" in body and "alpha" in body,
          f"{body!r} {(p.stderr or '')[:200]}")

# --- 4. 여럿이 보는 자리에는 안 올라간다 -------------------------------------
# 같은 이름을 열쇠글 통로에 올리려 하면 그쪽이 시작을 거절해야 한다. 그 게이트가
# 살아 있는지를 여기서도 본다 — 두 경로를 가르는 것이 이 설계의 전부라서다.
# 글자를 찾지 말고 실제로 돌려 본다. 전에는 원본에서 판정식 문자열을 grep 했는데,
# 그 판정을 이름 앞가지에서 설정 읽기로 바꾸는 날 이 칸이 통째로 거짓 실패가 됐다.
# 재는 것은 그 코드가 어떻게 쓰였나가 아니라 공용 아닌 이름을 거절하느냐다.
web = REPO / "bin" / "claude-usage-web"
with tempfile.TemporaryDirectory() as td:
    conf = Path(td) / "night-accounts"
    conf.write_text("candidate 공용하나\nday 운영자계정\n", encoding="utf-8")
    p = subprocess.run([sys.executable, str(web), "--host", "0.0.0.0", "--port", "0",
                        "--token", "열쇠-abc", "--names", "공용하나", "운영자계정"],
                       capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, CLAUDE_NIGHT_CONF=str(conf)))
    check("열쇠글 통로는 공용 아닌 이름을 거절한다",
          p.returncode == 2 and "운영자계정" in p.stderr, p.stderr.strip()[:140])

print(f"\n{'실패: ' + ', '.join(fails) if fails else '모두 통과'}")
sys.exit(1 if fails else 0)
