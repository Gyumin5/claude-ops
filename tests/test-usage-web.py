#!/usr/bin/env python3
"""claude-usage-web 이 지키는 것들.

여기서 고정하는 것 넷이다. ⑴ 바깥으로 열면서 열쇠글이 없으면 시작하지 않는다 — "지금
재기" 가 그 계정 한도를 쓰는 동작이라, 주소를 아는 누구나 우리 한도를 태울 수 있다.
⑵ 열쇠글이 걸려 있으면 틀린 열쇠글로는 수치도 못 본다. ⑶ 다시 재는 간격에 바닥이 있어,
여러 사람이 같이 보고 있어도 재는 비용이 사람 수만큼 늘지 않는다. ⑷ 쪽 자체가 한글이
깨지지 않고 폰에서 읽히는 모양이다.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "bin" / "claude-usage-web"

fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  — {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def post(url):
    req = urllib.request.Request(url, method="POST", data=b"")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def main():
    check("웹 도우미가 있다", WEB.exists(), str(WEB))
    if not WEB.exists():
        return 1
    m = load(WEB, "usage_web")

    # ── 날짜 적기 ─────────────────────────────────────────────────────────
    now = time.time()
    later = now + 86400 * 3
    import datetime as _dt
    day = m.WEEKDAY[_dt.datetime.fromtimestamp(later).weekday()]
    check("오늘이 아닌 날짜엔 요일을 붙인다", f"({day})" in m.fmt_when(later, now),
          m.fmt_when(later, now))
    check("오늘이면 오늘이라고 적는다", m.fmt_when(now + 600, now).startswith("오늘"),
          m.fmt_when(now + 600, now))

    # ── 바깥으로 열 때는 열쇠글이 있어야 한다 ─────────────────────────────
    p = subprocess.run([sys.executable, str(WEB), "--host", "0.0.0.0", "--port", "0"],
                       capture_output=True, text=True, timeout=30)
    check("열쇠글 없이 바깥으로 열지 않는다",
          p.returncode == 2 and "token" in p.stderr, p.stderr.strip()[:120])

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "accounts"
        home = root / "acct-a"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "statusline_cache.json").write_text(json.dumps({
            "_cached_at": now,
            "rate_limits": {"five_hour": {"used_percentage": 28, "resets_at": now + 3600},
                            "seven_day": {"used_percentage": 4, "resets_at": later}},
        }), encoding="utf-8")

        # 재는 쪽은 진짜 세션을 띄우지 않는다 — 몇 번 불렸는지만 센다. 서버가 파이썬으로
        # 부르므로 가짜도 파이썬이어야 한다.
        counter = Path(td) / "count"
        fake_probe = Path(td) / "fake_probe.py"
        fake_probe.write_text(
            "import pathlib\n"
            f"p = pathlib.Path({str(counter)!r})\n"
            "p.write_text(p.read_text() + 'x' if p.exists() else 'x')\n"
            "print('acct-a: 받았다 — 1.0초')\n", encoding="utf-8")

        port = 8799
        # 올리는 계정은 이 목록이 정한다 — 뿌리에 다른 홈이 생겨도 안 나간다.
        store = m.Store(str(root), ["acct-a", "acct-b"], min_gap=60.0, timeout=10.0)
        m.PROBE = fake_probe          # 진짜 세션 대신 가짜를 부르게 바꿔 끼운다
        token = "열쇠-abc"
        from http.server import ThreadingHTTPServer
        httpd = ThreadingHTTPServer(("127.0.0.1", port), m.make_handler(store, token))
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        base = f"http://127.0.0.1:{port}"
        from urllib.parse import quote
        try:
            code, _ = get(base + "/")
            check("열쇠글 없이 들어오면 막는다", code == 403, str(code))
            code, _ = get(base + "/?t=" + quote("다른열쇠"))
            check("틀린 열쇠글도 막는다", code == 403, str(code))
            code, _ = get(base + "/?t=" + quote(token[:-1]))
            check("길이가 다른 열쇠글도 막는다", code == 403, str(code))

            q = "?t=" + quote(token)
            code, body = get(base + "/" + q)
            check("맞는 열쇠글이면 쪽을 준다", code == 200, str(code))
            check("한글이 안 깨지게 charset 이 맨 앞에 선다",
                  body.index("<meta charset=\"utf-8\">") < 1024,
                  body.index("<meta charset=\"utf-8\">"))
            check("폰 너비를 따라간다", "width=device-width" in body)
            check("이 기계에 있는 한글 글꼴을 쓴다", "Noto Sans CJK KR" in body)
            check("수치가 쪽에 그대로 실린다", "28%" in body and "4%" in body)
            check("오늘이 아닌 풀림 시각엔 요일이 붙는다", f"({day})" in body,
                  [l for l in body.splitlines() if f"({day})" in l][:1])

            # 한도를 다 쓴 계정 줄에 영어 원문이 그대로 뜨면 안 된다(11-06 운영자 지시).
            spent = root / "acct-b"
            (spent / ".claude").mkdir(parents=True)
            (spent / ".claude" / "statusline_cache.json").write_text(
                json.dumps({"_cached_at": now}), encoding="utf-8")
            (spent / ".claude" / "account-probe.json").write_text(json.dumps(
                {"at": now, "state": "한도 소진", "kind": "7일 한도", "until": later,
                 "note": "hit your weekly limit · resets Nov 9, 6pm"},
                ensure_ascii=False), encoding="utf-8")
            html = m.page(store)
            check("다 쓴 계정 줄도 우리 말로 적는다",
                  "7일 한도를 다 썼다" in html and "resets" not in html,
                  [l for l in html.splitlines() if "다 썼다" in l][:1])
            check("그 줄에 언제 풀리는지 적는다",
                  f"{m.fmt_when(later, now)} 풀림" in html,
                  [l for l in html.splitlines() if "풀림" in l][-1:])

            # 풀릴 시각이 지난 쪽지는 끝난 사실이다. 그대로 두면 며칠씩 "지금 다 썼다"
            # 고 말한다(11-08 홈 gmoh — 전날 22:00 에 풀렸는데 다음 날 오후까지 소진).
            over = now - 3600
            (spent / ".claude" / "account-probe.json").write_text(json.dumps(
                {"at": now - 86400, "state": "한도 소진", "kind": "7일 한도",
                 "until": over, "note": "hit your weekly limit"},
                ensure_ascii=False), encoding="utf-8")
            html = m.page(store)
            check("풀린 뒤에는 다 썼다고 안 적는다", "다 썼다" not in html,
                  [l for l in html.splitlines() if "다 썼다" in l][:1])
            check("대신 언제 풀렸는지를 적는다",
                  f"{m.fmt_when(over, now)} 에 풀렸고" in html,
                  [l for l in html.splitlines() if "풀렸고" in l][:1])

            # 칸이 안 온 창은 수치 자리를 비운다. 풀릴 시각이 지난 창도 모아 보기가
            # 빼고 보내므로 여기 안 온다 — 빈 칸을 0퍼센트나 "?" 로 적으면 여유가
            # 있는 계정으로 읽힌다.
            one = m.cell({"used_percentage": 28, "resets_at": later}, now)
            check("온 창은 수치와 풀림 시각을 같이 적는다",
                  "28%" in one and "풀림" in one, one)
            none_cell = m.cell(None, now)
            check("안 온 창은 퍼센트를 지어내지 않는다",
                  "%" not in none_cell and "칸 없음" in none_cell, none_cell)

            code, body = get(base + "/usage.txt" + q)
            check("터미널에서 읽는 통로도 같은 수치를 준다",
                  code == 200 and "28%" in body and "4%" in body, body.strip()[:120])
            check("그 통로에는 꾸밈이 안 섞인다", "<" not in body, body.strip()[:80])

            code, body = get(base + "/usage.json" + q)
            check("기계가 읽을 형태도 같은 값을 준다",
                  code == 200 and json.loads(body)["rows"][0]["five_hour"]
                  ["used_percentage"] == 28, body[:120])

            code, body = post(base + "/measure" + q)
            check("누르면 실제로 잰다",
                  code == 200 and json.loads(body)["ok"] is True
                  and counter.read_text().count("x") == 1, body[:120])

            code, body = post(base + "/measure" + q)
            d = json.loads(body)
            check("바닥 안에 또 누르면 다시 재지 않는다",
                  d["ok"] is False and counter.read_text().count("x") == 1,
                  d.get("message", "")[:80])
            check("언제 다시 누르면 되는지 말해 준다", "초" in d.get("message", ""),
                  d.get("message", "")[:80])

            code, _ = post(base + "/measure")
            check("열쇠글 없이는 재지도 못한다",
                  code == 403 and counter.read_text().count("x") == 1, code)

            # 열쇠글을 건 자리는 여럿이 보는 자리다. 운영자의 회사·개인 계정 수치는
            # 거기 안 올린다(운영자 지시) — 보는 쪽은 컨트롤봇뿐이다. 뿌리에 그
            # 홈이 생기기만 해도 새던 자리라, 올리는 목록으로 거르고 목록 자체도 막는다.
            boss = root / "ogm3614"
            (boss / ".claude").mkdir(parents=True)
            (boss / ".claude" / "statusline_cache.json").write_text(json.dumps({
                "_cached_at": now,
                "rate_limits": {"seven_day": {"used_percentage": 93, "resets_at": later}},
            }), encoding="utf-8")
            code, body = get(base + "/usage.json" + q)
            labels = [r.get("label") for r in json.loads(body).get("rows", [])]
            check("목록 밖 계정은 뿌리에 있어도 안 내보낸다", "ogm3614" not in labels, labels)
            code, body = get(base + "/" + q)
            check("쪽에도 그 수치가 안 뜬다", "93%" not in body and "ogm3614" not in body)
            code, body = get(base + "/usage.txt" + q)
            check("터미널 통로에도 안 샌다", "93%" not in body and "ogm3614" not in body,
                  body.strip()[:100])
        finally:
            httpd.shutdown()

    # 무엇이 공용인가는 설정이 정하므로 검사도 자기 설정을 지어서 넘긴다. 이 기계의
    # 진짜 설정을 쓰면 설정이 없거나 다른 기계에서 이 검사가 통째로 갈린다.
    with tempfile.TemporaryDirectory() as td:
        conf = Path(td) / "night-accounts"
        conf.write_text("candidate acct-a\ncandidate acct-b\nday 개인계정\n", encoding="utf-8")
        env = dict(os.environ, CLAUDE_NIGHT_CONF=str(conf))

        # 거르는 쪽만 두면 누가 목록에 끼워 넣는 순간 조용히 샌다. 목록 자체를 막는다.
        p = subprocess.run([sys.executable, str(WEB), "--host", "0.0.0.0", "--port", "0",
                            "--token", "열쇠-abc", "--names", "acct-a", "개인계정"],
                           capture_output=True, text=True, timeout=30, env=env)
        check("열쇠글을 건 자리에 공용 아닌 계정을 올리면 시작하지 않는다",
              p.returncode == 2 and "개인계정" in p.stderr, p.stderr.strip()[:140])
        p = subprocess.run([sys.executable, str(WEB), "--host", "0.0.0.0", "--port", "0",
                            "--token", "열쇠-abc", "--names", "acct-a", "acct-b", "--help"],
                           capture_output=True, text=True, timeout=30, env=env)
        check("공용 계정만이면 그 게이트에 안 걸린다", p.returncode == 0, p.stderr.strip()[:140])

        # 이름을 안 적으면 설정이 정한다 — 코드에 박힌 기본값이 없다.
        p = subprocess.run([sys.executable, str(WEB), "--host", "0.0.0.0", "--port", "0",
                            "--token", "열쇠-abc", "--help"],
                           capture_output=True, text=True, timeout=30, env=env)
        check("이름을 안 적으면 설정의 공용 계정을 쓴다", p.returncode == 0,
              p.stderr.strip()[:140])

        empty = Path(td) / "비어있다"
        empty.write_text("day 개인계정\n", encoding="utf-8")
        p = subprocess.run([sys.executable, str(WEB), "--host", "127.0.0.1", "--port", "0"],
                           capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, CLAUDE_NIGHT_CONF=str(empty)))
        check("설정에 공용 계정이 없으면 시작하지 않는다",
              p.returncode == 2 and "candidate" in p.stderr, p.stderr.strip()[:140])

    return 1 if fails else 0


if __name__ == "__main__":
    rc = main()
    print(f"\n{'실패: ' + ', '.join(fails) if fails else '모두 통과'}")
    sys.exit(rc)
