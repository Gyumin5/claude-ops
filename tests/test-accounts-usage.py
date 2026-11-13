#!/usr/bin/env python3
"""계정별 사용량 모아 보기(bin/claude-accounts-usage)의 읽기 계약.

이 명령이 지켜야 하는 것은 셋이다.

  ⑴ 홈마다 `.claude/statusline_cache.json` 하나만 읽고 아무것도 쓰지 않는다.
  ⑵ 수치가 없는 홈을 0퍼센트로 읽지 않는다 — 없으면 없다고 적는다.
  ⑶ 리셋 시각이 이미 지난 값을 현재 사용량으로 내지 않는다.

⑵·⑶ 이 핵심이다. 계정별 홈은 세션이 안 돌면 파일이 아예 없고, 돌다 멈추면 낡은 값이
그대로 남는다. 둘 중 어느 쪽도 "0퍼센트 여유 있음" 으로 보이면 운영자가 그 계정으로
일을 보냈다가 바로 막힌다.

실호출이 없다. 가짜 홈을 만들어 파일만 놓고 읽는다.
"""
import datetime
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CMD = REPO / "bin" / "claude-accounts-usage"
results = []


def check(name, cond, detail: object = ""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def load():
    loader = importlib.machinery.SourceFileLoader("accounts_usage", str(CMD))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def make_home(root, name, cache=None):
    home = Path(root) / name
    (home / ".claude").mkdir(parents=True)
    if cache is not None:
        (home / ".claude" / "statusline_cache.json").write_text(
            json.dumps(cache), encoding="utf-8")
    return home


check("명령 파일이 있다", CMD.exists(), str(CMD))
if not CMD.exists():
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(1)

m = load()
now = time.time()

with tempfile.TemporaryDirectory() as td:
    root = Path(td)

    ok_home = make_home(root, "acct-a", {
        "_cached_at": now - 120,
        "rate_limits": {
            "five_hour": {"used_percentage": 28, "resets_at": now + 3600},
            "seven_day": {"used_percentage": 4, "resets_at": now + 86400 * 3},
        },
    })
    empty_home = make_home(root, "acct-b")
    stale_home = make_home(root, "acct-c", {
        "_cached_at": now - 86400,
        "rate_limits": {
            "five_hour": {"used_percentage": 100, "resets_at": now - 600},
            "seven_day": {"used_percentage": 71, "resets_at": now + 86400},
        },
    })
    nolimit_home = make_home(root, "acct-d", {"_cached_at": now, "model": "opus"})
    (root / "sw4").mkdir()
    (root / "sw4" / ".claude").mkdir()
    (root / "sw4" / ".claude" / "statusline_cache.json").write_text("{깨짐", encoding="utf-8")

    # ── 홈 하나를 읽는 계약 ────────────────────────────────────────────────
    r = m.read_home("acct-a", ok_home)
    check("수치가 있는 홈은 ok", r["state"] == "ok", r["state"])
    check("다섯 시간 창의 사용률을 그대로 읽는다",
          r["five_hour"]["used_percentage"] == 28, r.get("five_hour"))
    check("7일 창의 리셋 시각을 그대로 읽는다",
          r["seven_day"]["resets_at"] == now + 86400 * 3, r.get("seven_day"))

    r = m.read_home("acct-b", empty_home)
    check("로그인도 캐시도 없는 홈은 0퍼센트가 아니라 '로그인 안 함'",
          r["state"] == "no-login" and "five_hour" not in r, r)
    # 로그인은 했는데 수치가 안 온 홈은 다시 재면 되고, 로그인을 안 한 홈은 사람이
    # 로그인해야 한다. 둘을 같은 말로 적으면 할 일이 뭔지 안 선다.
    (empty_home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    r = m.read_home("acct-b", empty_home)
    check("로그인은 됐는데 캐시가 없으면 '수치 없음'", r["state"] == "no-cache", r["state"])

    # 첫 회차부터 한도에 걸린 홈은 수치 파일이 아예 안 생긴다 — 탐침이 그 계정을 안
    # 띄우고 건너뛰기 때문이다. 그걸 "세션이 아직 안 돌았다" 로 적으면 사람이 헛되이
    # 다시 재고, 다시 재도 같은 줄이 나온다(11-07 둘째 기계 gmoh).
    (empty_home / ".claude" / "account-probe.json").write_text(
        json.dumps({"at": now, "state": "한도 소진", "kind": "7일 한도",
                    "until": now + 3600, "note": "hit your weekly limit"},
                   ensure_ascii=False), encoding="utf-8")
    r = m.read_home("acct-b", empty_home)
    check("캐시가 없어도 탐침이 '한도 소진' 이라 적어 뒀으면 그걸 적는다",
          r["state"] == "spent" and r.get("kind") == "7일 한도", r["state"])
    line = m.render([r], now).splitlines()[1]
    check("그 줄도 우리 말로 풀림 시각까지 적는다",
          "7일 한도를 다 썼다" in line and "풀림" in line, line)
    (empty_home / ".claude" / "account-probe.json").unlink()
    r = m.read_home("acct-b", empty_home)
    check("탐침 표식이 없으면 다시 '수치 없음' 이다", r["state"] == "no-cache", r["state"])
    (empty_home / ".claude" / ".credentials.json").unlink()
    check("빈 로그인 파일은 로그인으로 안 본다",
          not m.has_creds(stale_home), str(stale_home))

    # 7일가 거의 다 찬 계정은 다섯 시간 창이 안 열려서 그 칸이 아예 안 온다.
    # 2026-11-06 ogm3614 가 7일 99퍼센트에 다섯 시간 칸 없음으로 왔다.
    only7_home = make_home(root, "sw5", {
        "_cached_at": now,
        "rate_limits": {"seven_day": {"used_percentage": 99, "resets_at": now + 86400}},
    })
    r = m.read_home("sw5", only7_home)
    check("7일 칸만 와도 ok 다", r["state"] == "ok" and "five_hour" not in r, r)
    line = m.render([r], now).splitlines()[1]
    check("없는 다섯 시간 칸은 줄에 안 적는다",
          "5시간" not in line and "7일 99%" in line, line)

    r = m.read_home("acct-d", nolimit_home)
    check("한도 칸이 없는 캐시도 '수치 없음'", r["state"] == "no-limits", r["state"])

    # 한도를 다 쓴 계정은 수치 파일에 한도 칸이 아예 안 온다. 까닭을 안 가르면 말이 없는
    # 계정과 똑같이 보여서, 고칠 것이 없는데 고치려 든다(11-06 acct-c).
    (nolimit_home / ".claude" / "account-probe.json").write_text(
        json.dumps({"at": now, "state": "한도 소진",
                    "note": "hit your weekly limit · resets Nov 9, 6pm"},
                   ensure_ascii=False), encoding="utf-8")
    r = m.read_home("acct-d", nolimit_home)
    check("다 쓴 계정은 '말이 없다' 와 가른다", r["state"] == "spent", r["state"])
    line = m.render([r], now)
    check("뽑아 둔 시각이 없으면 원문이라도 남긴다",
          "한도를 다 썼다" in line and "Nov 9" in line, line.splitlines()[-1][:120])

    # 탐침이 종류와 풀릴 시각을 뽑아 뒀으면 영어 원문 대신 우리 말로 적는다.
    # 운영자가 읽는 줄에 영어가 섞이면 안 된다(11-06 운영자 지시).
    later = now + 86400 * 3
    (nolimit_home / ".claude" / "account-probe.json").write_text(
        json.dumps({"at": now, "state": "한도 소진", "kind": "7일 한도",
                    "until": later,
                    "note": "hit your weekly limit · resets Nov 9, 6pm"},
                   ensure_ascii=False), encoding="utf-8")
    r = m.read_home("acct-d", nolimit_home)
    check("뽑아 둔 종류와 시각을 그대로 들고 온다",
          r.get("kind") == "7일 한도" and r.get("until") == later, r.get("until"))
    line = m.render([r], now).splitlines()[1]
    check("그 줄은 우리 말로만 적힌다",
          "7일 한도를 다 썼다" in line and "풀림" in line and "resets" not in line, line)
    check("그 줄에 풀리는 날짜와 요일이 있다",
          m.fmt_clock(later, now) in line, line)

    # 풀릴 시각이 지난 쪽지는 끝난 사실이다. 탐침은 그 계정을 안 띄우는 동안 쪽지를
    # 안 고치므로 풀린 뒤에도 그 자리에 남아 며칠씩 "지금 다 썼다" 고 말한다
    # (11-08 홈 gmoh — 11-07 22:00 에 풀렸는데 다음 날 오후까지 소진으로 떴다).
    over = now - 3600
    (nolimit_home / ".claude" / "account-probe.json").write_text(
        json.dumps({"at": now - 86400, "state": "한도 소진", "kind": "7일 한도",
                    "until": over, "note": "hit your weekly limit"},
                   ensure_ascii=False), encoding="utf-8")
    r = m.read_home("acct-d", nolimit_home, now=now)
    check("풀릴 시각이 지났으면 '다 썼다' 로 안 적는다",
          r["state"] == "no-limits" and "until" not in r, r["state"])
    check("언제 풀렸는지는 남긴다",
          r.get("spent_until") == over and r.get("kind") == "7일 한도", r.get("spent_until"))
    line = m.render([r], now).splitlines()[1]
    check("그 줄은 풀린 시각과 그 뒤로 안 쟀다는 것을 적는다",
          "다 썼다" not in line and "풀렸고" in line and m.fmt_clock(over, now) in line, line)

    # 수치 파일이 아예 없는 홈도 같다 — 까닭을 통째로 버리면 왜 수치가 없는지가
    # 사라져서 사람이 로그인부터 의심한다.
    (empty_home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    (empty_home / ".claude" / "account-probe.json").write_text(
        json.dumps({"at": now - 86400, "state": "한도 소진", "kind": "7일 한도",
                    "until": over, "note": "hit your weekly limit"},
                   ensure_ascii=False), encoding="utf-8")
    r = m.read_home("acct-b", empty_home, now=now)
    check("수치 파일이 없는 홈도 지난 쪽지는 안 믿는다",
          r["state"] == "no-cache" and r.get("spent_until") == over, r["state"])
    line = m.render([r], now).splitlines()[1]
    check("그 줄도 '세션이 아직 안 돌았다' 로 안 적는다",
          "안 돌았다" not in line and "풀렸고" in line, line)
    (empty_home / ".claude" / "account-probe.json").unlink()
    (empty_home / ".claude" / ".credentials.json").unlink()

    r = m.read_home("sw4", root / "sw4")
    check("깨진 캐시는 멈추지 않고 사유를 남긴다", r["state"] == "broken", r.get("detail"))

    # ── 지난 창은 수치에서 뺀다 ───────────────────────────────────────────
    # 풀릴 시각이 지난 창은 이미 0 으로 초기화됐고, 우리가 든 값은 초기화 전에 잰
    # 값이다. 그대로 적으면 지금 그만큼 쓴 계정으로 읽힌다(2026-11-08 ogm3614 이
    # 아홉 시간 전에 끝난 창의 99퍼센트를 달고 있었다).
    r = m.read_home("acct-c", stale_home)
    check("지난 다섯 시간 창은 수치에서 뺀다",
          r["state"] == "ok" and "five_hour" not in r, r)
    check("아직 유효한 7일 창은 그대로 남는다",
          r["seven_day"]["used_percentage"] == 71, r.get("seven_day"))
    line = m.render([r], now).splitlines()[1]
    check("줄에도 지난 창은 안 적는다", "5시간" not in line and "7일 71%" in line, line)

    both_gone = m.read_home("sw6", make_home(root, "sw6", {
        "_cached_at": now - 86400,
        "rate_limits": {
            "five_hour": {"used_percentage": 92, "resets_at": now - 7200},
            "seven_day": {"used_percentage": 99, "resets_at": now - 3600},
        },
    }))
    check("창이 다 지났으면 수치 없음으로 적는다",
          both_gone["state"] == "no-limits", both_gone["state"])
    check("무엇이 언제 풀렸는지는 남긴다 — 마지막으로 풀린 창",
          both_gone.get("kind") == "7일 한도"
          and both_gone.get("spent_until") == now - 3600, both_gone)
    line = m.render([both_gone], now).splitlines()[1]
    check("그 줄은 퍼센트를 안 적고 왜 없는지를 적는다",
          "%" not in line and "풀렸고 그 뒤로 안 쟀다" in line, line)

    remote_row = {"label": "peer:ogm3614", "state": "ok", "cached_at": now - 90000,
                  "seven_day": {"used_percentage": 99, "resets_at": now - 3600}}
    m.drop_expired_windows(remote_row, now)
    check("저 기계가 보낸 지난 창도 받는 쪽에서 뺀다",
          remote_row["state"] == "no-limits" and "seven_day" not in remote_row,
          remote_row)

    # 날짜만 있으면 그게 이번 주인지 다음 주인지가 안 선다. 요일을 같이 적는다.
    import datetime as _dt
    later = now + 86400 * 3
    day = m.WEEKDAY[_dt.datetime.fromtimestamp(later).weekday()]
    other = m.fmt_window({"used_percentage": 5, "resets_at": later}, now)
    check("오늘이 아닌 날짜엔 요일을 붙인다", f"({day})" in other, other)
    today_only = m.fmt_window({"used_percentage": 5, "resets_at": now + 3600}, now)
    check("오늘이면 시각만 적는다", "(" in today_only and "월" not in today_only,
          today_only)

    future = m.fmt_window({"used_percentage": 28, "resets_at": now + 3600}, now)
    check("아직 유효한 창은 리셋 시각을 보여 준다",
          "초기화" in future and "28%" in future, future)
    check("칸 자체가 없으면 퍼센트를 지어내지 않는다",
          "%" not in m.fmt_window(None, now), m.fmt_window(None, now))

    # ── 모으기 ────────────────────────────────────────────────────────────
    rows = m.collect(root, include_current=False)
    labels = [x["label"] for x in rows]
    check("뿌리 밑 홈을 전부, 이름순으로 모은다",
          labels == ["acct-a", "acct-b", "acct-c", "acct-d", "sw4", "sw5", "sw6"], labels)
    rows_cur = m.collect(root, include_current=True)
    check("지금 쓰는 홈이 맨 앞에 온다", rows_cur[0]["label"] == "현재", rows_cur[0]["label"])
    check("뿌리가 없어도 멈추지 않는다",
          m.collect(root / "없는뿌리", include_current=False) == [])

    # ── 사람이 읽는 줄 ────────────────────────────────────────────────────
    text = m.render(rows, now)
    check("낡은 홈 줄에는 지난 창이 안 보인다",
          "5시간" not in text.split("\n")[3] and "7일 71%" in text.split("\n")[3],
          text.split("\n")[3])
    check("빈 홈 줄에 퍼센트 기호가 없다",
          "%" not in text.split("\n")[2], text.split("\n")[2])
    check("모든 홈이 한 줄씩 나온다", len(text.split("\n")) == 1 + len(rows), len(text.split("\n")))

    # ── 끝에서 끝까지 ─────────────────────────────────────────────────────
    p = subprocess.run([sys.executable, str(CMD), "--root", str(root), "--no-current"],
                       capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    check("사람이 읽는 형태로 돈다", p.returncode == 0, p.stderr[:200])
    check("출력에 홈 이름이 다 있다",
          all(n in p.stdout for n in ("acct-a", "acct-b", "acct-c", "acct-d", "sw4")), p.stdout[:200])

    p = subprocess.run([sys.executable, str(CMD), "--root", str(root), "--no-current", "--json"],
                       capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    check("기계가 읽는 형태로도 돈다", p.returncode == 0, p.stderr[:200])
    try:
        got = json.loads(p.stdout)
    except ValueError as exc:
        got = {}
        check("기계 출력이 올바른 JSON", False, str(exc))
    else:
        check("기계 출력이 올바른 JSON", True)
    check("기계 출력에 홈이 일곱 줄", len(got.get("rows", [])) == 7, len(got.get("rows", [])))

    # ── 쓰지 않는다 ───────────────────────────────────────────────────────
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    m.collect(root, include_current=False)
    subprocess.run([sys.executable, str(CMD), "--root", str(root), "--no-current"],
                   capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    check("읽기만 하고 파일을 만들거나 지우지 않는다", before == after,
          f"{len(before)} → {len(after)}")

# ── 계정 홈을 까는 계약 ───────────────────────────────────────────────────
# 빠지면 조용히 망하는 자리가 여기다. 그 홈의 .claude/settings.json 에 상태줄 설정이
# 없으면 세션이 상태줄을 안 부르고 캐시가 영원히 안 생긴다 — 화면에서는 "세션이 아직
# 안 돌았다" 와 구분이 안 되므로, 깔 때 같이 들어가는지를 고정한다.
INIT = REPO / "bin" / "claude-account-home-init"
check("홈 까는 명령이 있다", INIT.exists(), str(INIT))

with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "accounts"
    p = subprocess.run([sys.executable, str(INIT), "--root", str(root), "acct-a", "acct-b"],
                       capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    check("홈 둘을 깐다", p.returncode == 0, p.stderr[:200])

    cdir = root / "acct-a" / ".claude"
    link = cdir / "statusline.py"
    check("상태줄을 그 홈에 연결한다",
          link.is_symlink() and Path(os.readlink(link)) == (REPO / "claude" / "statusline.py"),
          str(link))
    try:
        st = json.loads((cdir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        st = {}
        check("상태줄 설정을 적는다", False, str(exc))
    else:
        check("상태줄 설정을 적는다",
              st.get("statusLine", {}).get("command") == "python3 ~/.claude/statusline.py", st)

    # 두 번 돌려도 같은 상태 — 사람이 다시 돌릴 수 있어야 한다.
    sig = sorted((str(x.relative_to(root)), x.is_symlink() or x.stat().st_size)
                 for x in root.rglob("*"))
    p = subprocess.run([sys.executable, str(INIT), "--root", str(root), "acct-a", "acct-b"],
                       capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    sig2 = sorted((str(x.relative_to(root)), x.is_symlink() or x.stat().st_size)
                  for x in root.rglob("*"))
    check("두 번 돌려도 그대로다", p.returncode == 0 and sig == sig2 and "이미" in p.stdout,
          p.stdout.splitlines()[0] if p.stdout else p.stderr[:120])

    # 기존 설정이 있으면 다른 칸을 안 지운다.
    (cdir / "settings.json").write_text(json.dumps({"env": {"KEEP": "1"}}), encoding="utf-8")
    subprocess.run([sys.executable, str(INIT), "--root", str(root), "acct-a"],
                   capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    st = json.loads((cdir / "settings.json").read_text(encoding="utf-8"))
    check("이미 있던 설정 칸은 남긴다",
          st.get("env", {}).get("KEEP") == "1" and "statusLine" in st, st)

    # 공용 기계에 깔리므로 남의 계정이 그 홈을 읽으면 안 된다 — 자격 파일이 거기 들어온다.
    check("계정 홈을 주인만 들어가게 잠근다",
          ((root / "acct-a").stat().st_mode & 0o077) == 0
          and (cdir.stat().st_mode & 0o077) == 0,
          oct((root / "acct-a").stat().st_mode))

    p = subprocess.run([sys.executable, str(INIT), "--root", str(root), "../탈출"],
                       capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    check("이름에 경로를 쓰면 거절한다", p.returncode == 2, p.returncode)

    # 깐 직후에는 로그인도 수치도 없다 — 그 상태가 모아 보기에서 '로그인 안 함' 이다.
    rows = m.collect(root, include_current=False)
    check("깐 직후에는 로그인 안 함으로 보인다",
          [r["state"] for r in rows] == ["no-login", "no-login"], [r["state"] for r in rows])

# ── 로그인 주소를 긁는 계약 ───────────────────────────────────────────────
# 로그인 화면은 주소를 그냥 찍기도 하고 터미널 링크(OSC 8)로 감싸기도 한다. 감싼 쪽을
# 놓치면 "주소를 못 받았다" 로 보이는데, 실제로는 로그인 프로세스가 멀쩡히 기다리고 있다.
# 그 둘을 화면에서 구분할 수 없으므로 여기서 고정한다.
LOGIN = REPO / "bin" / "claude-account-login"
check("로그인 도우미가 있다", LOGIN.exists(), str(LOGIN))

if LOGIN.exists():
    loader = importlib.machinery.SourceFileLoader("account_login", str(LOGIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    lg = importlib.util.module_from_spec(spec)
    loader.exec_module(lg)

    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "log.txt"

        log.write_text("\x1b[32m브라우저에서 여세요:\x1b[0m\n"
                       "https://claude.ai/oauth/authorize?code=true&state=abc\n")
        check("평범하게 찍힌 주소를 읽는다",
              lg.find_url(log) == "https://claude.ai/oauth/authorize?code=true&state=abc",
              lg.find_url(log))

        log.write_text("\x1b]8;;https://claude.ai/oauth/authorize?code=true&state=xyz\x07"
                       "여기를 누르세요\x1b]8;;\x07\n")
        check("터미널 링크로 감싼 주소도 읽는다",
              lg.find_url(log) == "https://claude.ai/oauth/authorize?code=true&state=xyz",
              lg.find_url(log))

        log.write_text("로그인 화면을 준비하는 중...\n")
        check("주소가 없으면 지어내지 않는다", lg.find_url(log) is None, lg.find_url(log))

        check("화면 꾸밈만 걷어낸다", lg.strip_ansi("\x1b[1m값\x1b[0m 28%") == "값 28%",
              lg.strip_ansi("\x1b[1m값\x1b[0m 28%"))

        # 홈이 없으면 로그인을 띄우지 않는다 — 엉뚱한 자리에 .claude 를 만들면
        # 그 홈이 '세션이 안 돌았다' 로 조용히 섞여 들어간다.
        p = subprocess.run([sys.executable, str(LOGIN), "start", "acct-a",
                            "--root", str(Path(td) / "없는뿌리")],
                           capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL)
        check("계정 홈이 없으면 로그인을 안 띄운다", p.returncode == 1, p.returncode)

        # 실행파일은 바깥에서 찾아 절대경로로 넘긴다. 유닛 안의 PATH 는 접속 셸의 것이
        # 아니라서, 이름만 넘기면 그 자리에서 못 찾고 죽는다(11-06 사고 — 서버의
        # ~/.local/bin 이 비대화형 접속 PATH 에도 없다).
        check("적어 준 실행파일 경로를 그대로 쓴다",
              lg.resolve_claude("/고른/자리/claude") == "/고른/자리/claude",
              lg.resolve_claude("/고른/자리/claude"))
        check("아무것도 안 적으면 이름으로 부른다",
              lg.login_cmd("")[0] == "claude", lg.login_cmd(""))
        check("정한 실행파일은 명령 맨 앞에 선다",
              lg.login_cmd("a@b", "/고른/자리/claude")[0] == "/고른/자리/claude",
              lg.login_cmd("a@b", "/고른/자리/claude"))

        # 끝에서 끝까지 — 가짜 로그인 화면으로 주소를 받고, 코드를 넣고, 접는다.
        # 코드는 명령줄이 아니라 입력으로 간다(공용 기계에서는 명령줄이 남에게 보인다).
        accounts = Path(td) / "accounts"
        subprocess.run([sys.executable, str(INIT), "--root", str(accounts), "acct-a"],
                       capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        fakebin = Path(td) / "bin"
        fakebin.mkdir()
        (fakebin / "claude").write_text(
            "#!/bin/sh\n"
            "printf 'open this:\\n'\n"
            "printf 'https://claude.ai/oauth/authorize?code=true&state=fake\\n'\n"
            "printf 'Paste code here > '\n"
            "read got\n"
            "printf 'got[%s]\\n' \"$got\"\n", encoding="utf-8")
        (fakebin / "claude").chmod(0o755)

        # PATH 에는 가짜 실행파일을 일부러 안 넣는다 — 유닛 안이 꼭 그 모양이고,
        # 그 자리에서도 넘겨받은 경로로 로그인 화면이 떠야 한다.
        env = dict(os.environ, PATH="/usr/bin:/bin")
        waiter = subprocess.Popen(
            [sys.executable, str(LOGIN), "--inner", "start", "acct-a", "--root", str(accounts),
             "--claude-bin", str(fakebin / "claude")],
            env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wlog = accounts / "acct-a" / "login" / "log.txt"
        url = None
        for _ in range(100):
            url = lg.find_url(wlog) if wlog.exists() else None
            if url:
                break
            time.sleep(0.1)
        check("기다리는 쪽이 주소를 로그에 남긴다",
              url == "https://claude.ai/oauth/authorize?code=true&state=fake", url)

        p = subprocess.run([sys.executable, str(LOGIN), "code", "acct-a", "-",
                            "--root", str(accounts)],
                           input="ABC-123\n", capture_output=True, text=True, timeout=60)
        check("입력으로 받은 코드가 로그인 화면까지 간다",
              "got[ABC-123]" in lg.strip_ansi(wlog.read_text(errors="replace")),
              lg.strip_ansi(wlog.read_text(errors="replace"))[-120:])
        try:
            waiter.wait(timeout=30)
        except subprocess.TimeoutExpired:
            waiter.kill()
            check("코드를 넣으면 기다리는 쪽이 끝난다", False, "안 끝났다")
        else:
            check("코드를 넣으면 기다리는 쪽이 끝난다", True)

        p = subprocess.run([sys.executable, str(LOGIN), "code", "acct-a", "--root", str(td)],
                           capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL)
        check("코드를 안 적으면 거절한다", p.returncode == 2, p.returncode)

# ── 경로 계약 ─────────────────────────────────────────────────────────────
# 수치를 적는 쪽(claude/statusline.py)이 홈을 따라가야 계정별로 파일이 갈린다.
# 그 경로가 고정 경로로 바뀌면 모든 계정이 한 파일을 덮어쓰고 이 명령은 같은 줄을
# 다섯 번 내게 된다 — 틀렸다는 신호가 출력에 안 나타나므로 여기서 고정한다.
sl = (REPO / "claude" / "statusline.py").read_text(encoding="utf-8")
check("상태줄의 캐시 경로가 홈을 따라간다",
      'CACHE_FILE = os.path.expanduser("~/.claude/statusline_cache.json")' in sl,
      "statusline.py 의 CACHE_FILE")

# ── 홈을 깔 때 첫 화면을 미리 지나가 둔다 ─────────────────────────────────
# 서버에는 "이 폴더를 믿느냐" 에 답할 사람이 없고 그 물음의 기본값은 끝내기다. 안 적어
# 두면 세션이 거기서 끝나 상태줄이 한 번도 안 불리고, 화면에는 "수치 없음" 으로만 보인다.
with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "accounts"
    (root / "acct-a" / ".claude").mkdir(parents=True)
    keep = root / "acct-a" / ".claude.json"
    keep.write_text(json.dumps({"지키는칸": "그대로"}), encoding="utf-8")
    subprocess.run([sys.executable, str(INIT), "--root", str(root), "acct-a"],
                   capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    cfg = json.loads(keep.read_text(encoding="utf-8"))
    check("첫 실행 화면을 지나간 것으로 적는다", cfg.get("hasCompletedOnboarding") is True, cfg)
    check("그 홈 폴더를 믿는 것으로 적는다",
          cfg.get("projects", {}).get(str(root / "acct-a"), {}).get("hasTrustDialogAccepted") is True,
          cfg.get("projects"))
    check("이미 있던 칸은 안 건드린다", cfg.get("지키는칸") == "그대로", cfg)
    check("그 파일은 주인만 읽는다", (keep.stat().st_mode & 0o077) == 0,
          oct(keep.stat().st_mode))

# ── 세션을 띄워 수치를 받아 오는 계약 ─────────────────────────────────────
PROBE = REPO / "bin" / "claude-account-probe"
check("세션 띄우기 도우미가 있다", PROBE.exists(), str(PROBE))

if PROBE.exists():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "accounts"
        subprocess.run([sys.executable, str(INIT), "--root", str(root), "acct-a", "acct-b"],
                       capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)

        # 로그인 안 한 홈은 띄우지 않는다 — 띄워 봐야 로그인 화면에서 멈추고,
        # 그 멈춤이 "수치가 안 생겼다" 와 화면에서 같아 보인다.
        p = subprocess.run([sys.executable, str(PROBE), "--root", str(root), "acct-b",
                            "--claude-bin", "/bin/true", "--timeout", "5"],
                           capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
        check("로그인 안 한 홈은 안 띄운다",
              p.returncode == 1 and "아직 로그인" in p.stdout, p.stdout.strip()[:120])

        # 끝에서 끝까지 — 가짜 세션이 수치 파일을 적으면 받았다고 보고 접는다.
        (root / "acct-a" / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
        # 가짜 세션은 진짜와 같은 순서로 군다 — 띄우면 수치 파일부터 생기고 한도 칸은
        # 비어 있다가, 한 번 물어본 뒤에야 찬다(11-06 서버 실측과 같은 모양).
        fake = Path(td) / "fakeclaude"
        fake.write_text(
            "#!/bin/sh\n"
            "printf '%s' '{\"model\":{\"id\":\"x\"}}' "
            "> \"$HOME/.claude/statusline_cache.json\"\n"
            "read line\n"
            "printf '%s' '{\"rate_limits\":{\"five_hour\":{\"used_percentage\":7,"
            "\"resets_at\":1},\"seven_day\":{\"used_percentage\":3,\"resets_at\":2}},"
            "\"물은것\":\"'\"$line\"'\"}' > \"$HOME/.claude/statusline_cache.json\"\n"
            "sleep 120\n", encoding="utf-8")
        fake.chmod(0o755)
        t0 = time.time()
        p = subprocess.run([sys.executable, str(PROBE), "--root", str(root), "acct-a",
                            "--claude-bin", str(fake), "--timeout", "60",
                            "--ask", "짧게", "--ask-after", "2"],
                           capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL)
        took = time.time() - t0
        check("한 번 물어본 뒤 한도 칸을 받아 온다",
              p.returncode == 0 and "받았다" in p.stdout and "5시간 7%" in p.stdout,
              p.stdout.strip()[:160])
        check("물음이 세션 안까지 들어간다",
              json.loads((root / "acct-a" / ".claude" / "statusline_cache.json")
                         .read_text(encoding="utf-8")).get("물은것") == "짧게",
              (root / "acct-a" / ".claude" / "statusline_cache.json").read_text()[:120])
        check("수치를 받으면 끝까지 안 기다리고 접는다", took < 30, round(took, 1))

        # 한도 칸이 끝내 안 오면 받았다고 하지 않는다 — 그 구분이 흐려지면 모아 보기가
        # 빈 줄을 "정상" 으로 읽는다. 이 홈에는 바로 앞 회차가 적어 둔 값이 그대로 남아
        # 있으므로, 전 회차 값을 이번에 받은 것으로 읽는지도 같이 걸린다.
        mute = Path(td) / "fakemute"
        mute.write_text(
            "#!/bin/sh\n"
            "printf '%s' '{\"model\":{\"id\":\"x\"}}' "
            "> \"$HOME/.claude/statusline_cache.json\"\n"
            "sleep 120\n", encoding="utf-8")
        mute.chmod(0o755)
        p = subprocess.run([sys.executable, str(PROBE), "--root", str(root), "acct-a",
                            "--claude-bin", str(mute), "--timeout", "6", "--ask-after", "1"],
                           capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
        check("한도 칸이 안 오면 받았다고 안 한다",
              p.returncode == 1 and "한도 칸이 안 왔다" in p.stdout, p.stdout.strip()[:160])

        # 한도를 다 쓴 계정은 물음에 답하지 않고 그 사실을 화면에 적는다. 그걸 못 읽으면
        # 말이 없는 계정과 똑같이 보여서, 고칠 것이 없는데 고치려 든다(11-06 acct-c).
        spent = Path(td) / "fakespent"
        # 풀릴 시각은 돌리는 날에 맞춰 만든다. 날짜를 글자로 박으면 그 시각이 지나는
        # 순간부터 "아직 안 풀렸다" 를 요구하는 칸 둘이 깨지고, 한 달이 더 지나면
        # 파서가 이듬해로 넘겨 저절로 되살아난다 — 그 사이 한 달만 빨갛다(11-09 18시).
        when = datetime.datetime.now() + datetime.timedelta(days=3)
        reset_text = "{} {}, {}{}".format(
            when.strftime("%b"), when.day, (when.hour % 12) or 12,
            "am" if when.hour < 12 else "pm")
        spent.write_text(
            "#!/bin/sh\n"
            "printf '%s' '{\"model\":{\"id\":\"x\"}}' "
            "> \"$HOME/.claude/statusline_cache.json\"\n"
            "read line\n"
            # 진짜 화면처럼 캐리지리턴으로 줄을 끊고 그 뒤에 다른 글자를 더 찍는다.
            "printf '\\033[33m  ⎿  You'\"'\"'ve hit your weekly limit · resets "
            + reset_text +
            "\\033[0m\\r✻ Brewed for 0s · done\\r'\n"
            "sleep 120\n", encoding="utf-8")
        spent.chmod(0o755)
        t0 = time.time()
        p = subprocess.run([sys.executable, str(PROBE), "--root", str(root), "acct-a",
                            "--claude-bin", str(spent), "--timeout", "40", "--ask-after", "1"],
                           capture_output=True, text=True, timeout=90,
                           stdin=subprocess.DEVNULL)
        took = time.time() - t0
        check("한도를 다 쓴 계정은 그렇게 말한다",
              p.returncode == 1 and "한도 소진" in p.stdout and "7일 한도" in p.stdout,
              p.stdout.strip()[:160])
        check("그 줄에 영어 원문을 그대로 쓰지 않는다",
              "resets" not in p.stdout and "풀림" in p.stdout, p.stdout.strip()[:160])
        note = json.loads((root / "acct-a" / ".claude" / "account-probe.json")
                          .read_text(encoding="utf-8"))
        check("원문은 기록에 그대로 남는다",
              note.get("note", "").endswith(reset_text), note.get("note"))
        check("그 문구 뒤의 화면 글자는 안 딸려 온다",
              "Brewed" not in note.get("note", ""), note.get("note"))
        check("언제 풀리는지를 뽑아 둔다",
              note.get("kind") == "7일 한도" and note.get("until", 0) > time.time(),
              {k: note.get(k) for k in ("kind", "until")})
        check("한도 소진이면 끝까지 안 기다린다", took < 20, round(took, 1))

        # 아직 안 풀린 계정을 또 띄우면 같은 줄을 다시 받고 기다린 시간만 버린다.
        t0 = time.time()
        p = subprocess.run([sys.executable, str(PROBE), "--root", str(root), "acct-a",
                            "--claude-bin", str(spent), "--timeout", "40", "--ask-after", "1"],
                           capture_output=True, text=True, timeout=90,
                           stdin=subprocess.DEVNULL)
        check("안 풀린 계정은 아예 안 띄운다",
              p.returncode == 1 and "건너뜀" in p.stdout and time.time() - t0 < 5,
              p.stdout.strip()[:120])

        # 풀릴 시각이 지났으면 다시 띄운다 — 안 그러면 영원히 안 재진다.
        note["until"] = time.time() - 60
        (root / "acct-a" / ".claude" / "account-probe.json").write_text(
            json.dumps(note, ensure_ascii=False), encoding="utf-8")
        p = subprocess.run([sys.executable, str(PROBE), "--root", str(root), "acct-a",
                            "--claude-bin", str(spent), "--timeout", "40", "--ask-after", "1"],
                           capture_output=True, text=True, timeout=90,
                           stdin=subprocess.DEVNULL)
        check("풀릴 시각이 지나면 다시 띄운다", "건너뜀" not in p.stdout,
              p.stdout.strip()[:120])

        # 한도는 모델 무게로 세므로 수치만 받을 때는 가장 가벼운 것으로 띄운다.
        argfile = Path(td) / "argv.txt"
        spy = Path(td) / "fakespy"
        spy.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" > {argfile}\n"
            "printf '%s' '{\"rate_limits\":{\"five_hour\":{\"used_percentage\":1,"
            "\"resets_at\":1}}}' > \"$HOME/.claude/statusline_cache.json\"\n"
            "sleep 30\n", encoding="utf-8")
        spy.chmod(0o755)
        subprocess.run([sys.executable, str(PROBE), "--root", str(root), "acct-b",
                        "--claude-bin", str(spy), "--timeout", "20", "--ask-after", "1"],
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        (root / "acct-b" / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
        subprocess.run([sys.executable, str(PROBE), "--root", str(root), "acct-b",
                        "--claude-bin", str(spy), "--timeout", "20", "--ask-after", "1"],
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        argv = argfile.read_text(encoding="utf-8") if argfile.exists() else ""
        check("가장 가벼운 모델로 띄운다", "--model claude-haiku-5-5" in argv, argv.strip())
        subprocess.run([sys.executable, str(PROBE), "--root", str(root), "acct-b",
                        "--claude-bin", str(spy), "--timeout", "20", "--ask-after", "1",
                        "--model", ""],
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        check("빈 값이면 모델을 안 정한다",
              "--model" not in argfile.read_text(encoding="utf-8"),
              argfile.read_text(encoding="utf-8").strip())

        # ── 전 회차 수치를 이번 값으로 내놓지 않는다 ─────────────────────
        # 진짜 상태줄에는 래칫이 걸려 있다 — 한도 칸이 안 온 렌더는 앞 값을 그대로
        # 물려받고 파일 시각만 새것이 된다. 그러면 시각으로 신선도를 보는 자리가
        # 못 가르고, 띄울 때마다 첫 회차의 수치가 영원히 돌아온다(11-06 서버 실측:
        # acct-d 를 세 번 띄워 매번 1.6~1.8초에 5시간 14퍼센트, 같은 시각 홈 세션은 56).
        ratchet = Path(td) / "fakeratchet"
        ratchet.write_text(
            "#!/bin/sh\n"
            f"{sys.executable} -c \"\n"
            "import json,os\n"
            "p=os.environ['HOME']+'/.claude/statusline_cache.json'\n"
            "try: d=json.load(open(p))\n"
            "except Exception: d={}\n"
            "d['model']={'id':'x'}\n"
            "json.dump(d, open(p,'w'))\n"
            "\"\n"
            "sleep 120\n", encoding="utf-8")
        ratchet.chmod(0o755)
        cache2 = root / "acct-b" / ".claude" / "statusline_cache.json"
        cache2.write_text(json.dumps({
            "model": {"id": "x"},
            "rate_limits": {"five_hour": {"used_percentage": 14, "resets_at": 1},
                            "seven_day": {"used_percentage": 16, "resets_at": 2}},
        }, ensure_ascii=False), encoding="utf-8")
        p = subprocess.run([sys.executable, str(PROBE), "--root", str(root), "acct-b",
                            "--claude-bin", str(ratchet), "--timeout", "8",
                            "--ask-after", "1"],
                           capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
        check("전 회차 수치를 이번에 받은 것으로 내놓지 않는다",
              p.returncode == 1 and "14%" not in p.stdout, p.stdout.strip()[:160])

    # ── 띄우기 전에 한도 칸을 비운다 ─────────────────────────────────────
    loader = importlib.machinery.SourceFileLoader("account_probe", str(PROBE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    pr0 = importlib.util.module_from_spec(spec)
    loader.exec_module(pr0)
    with tempfile.TemporaryDirectory() as td2:
        c = Path(td2) / "statusline_cache.json"
        c.write_text(json.dumps({"model": {"id": "x"}, "_account": "a",
                                 "rate_limits": {"five_hour": {"used_percentage": 9}}},
                                ensure_ascii=False), encoding="utf-8")
        pr0.clear_limits(c)
        left = json.loads(c.read_text(encoding="utf-8"))
        check("비우면 한도 칸만 없어진다",
              "rate_limits" not in left and left.get("_account") == "a", left)
        pr0.clear_limits(Path(td2) / "없는파일.json")
        check("파일이 없어도 안 죽는다", True)
        broken = Path(td2) / "broken.json"
        broken.write_text("{깨진", encoding="utf-8")
        pr0.clear_limits(broken)
        check("깨진 파일은 건드리지 않는다", broken.read_text(encoding="utf-8") == "{깨진")

        # 7일가 거의 다 찬 계정은 다섯 시간 창이 안 열려서 그 칸이 안 온다. 다섯 시간을
        # 필수로 보면 수치가 멀쩡히 온 회차가 시간초과로 적힌다(11-06 ogm3614, 7일 99퍼센트).
        w = Path(td2) / "only7.json"
        w.write_text(json.dumps(
            {"rate_limits": {"seven_day": {"used_percentage": 99, "resets_at": 1}}}),
            encoding="utf-8")
        check("7일 칸만 와도 받은 것으로 본다",
              pr0.limits_of(w) is not None, pr0.limits_of(w))
        w.write_text(json.dumps({"rate_limits": {"seven_day": {}}}), encoding="utf-8")
        check("사용률 없는 빈 칸은 받은 것이 아니다", pr0.limits_of(w) is None, pr0.limits_of(w))

    # ── 여러 홈을 같이 띄운다 ────────────────────────────────────────────
    # 하나씩 차례로 띄우면 계정 수만큼 시간이 곱해진다(11-06 서버 실측: 계정당 9.1~9.3초,
    # 넷을 물으면 27.5초). 여기서는 3초를 끄는 가짜 둘을 두고, 같이 띄운 회차가 차례로
    # 띄운 회차보다 확실히 짧은지 본다.
    with tempfile.TemporaryDirectory() as td3:
        root = Path(td3) / "accounts"
        slow = Path(td3) / "slowclaude"
        slow.write_text(
            "#!/bin/sh\n"
            "sleep 3\n"
            "printf '%s' '{\"rate_limits\":{\"five_hour\":{\"used_percentage\":5,"
            "\"resets_at\":1},\"seven_day\":{\"used_percentage\":6,\"resets_at\":2}}}'"
            " > \"$HOME/.claude/statusline_cache.json\"\n"
            "sleep 60\n", encoding="utf-8")
        slow.chmod(0o755)
        for nm in ("acct-b", "acct-c"):
            (root / nm / ".claude").mkdir(parents=True)
            (root / nm / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")

        def run_probe(jobs):
            t = time.time()
            p = subprocess.run([sys.executable, str(PROBE), "--root", str(root), "acct-b", "acct-c",
                                "--claude-bin", str(slow), "--timeout", "25",
                                "--ask-after", "20", "--jobs", str(jobs)],
                               capture_output=True, text=True, timeout=120,
                               stdin=subprocess.DEVNULL)
            return time.time() - t, p

        seq, pseq = run_probe(1)
        par, ppar = run_probe(0)
        check("같이 띄워도 두 홈 다 받는다",
              ppar.returncode == 0 and ppar.stdout.count("받았다") == 2,
              ppar.stdout.strip()[:200])
        check("적은 차례대로 내놓는다",
              ppar.stdout.index("acct-b") < ppar.stdout.index("acct-c"),
              ppar.stdout.strip()[:200])
        check("같이 띄우면 차례로 띄우는 것보다 짧다",
              par < seq - 1.5,
              "같이 {:.1f}초 · 차례로 {:.1f}초".format(par, seq))
        check("차례로 띄우면 하나씩 더해진다", seq > 5.5, "{:.1f}초".format(seq))
        check("하나만 적으면 그대로 돈다",
              pseq.returncode == 0 and pseq.stdout.count("받았다") == 2,
              pseq.stdout.strip()[:200])

    # ── 다른 자리에서 쓰는 동안은 안 띄운다 ──────────────────────────────
    # 자격을 다른 기계로 가져가 쓰는 동안 두 자리가 같은 토큰 묶음을 각자 갱신하면
    # 늦게 쓴 쪽이 이미 바뀐 옛 토큰을 내민다. 표시 파일 하나로 그 홈만 멈춘다.
    with tempfile.TemporaryDirectory() as td4:
        root = Path(td4) / "accounts"
        (root / "acct-b" / ".claude").mkdir(parents=True)
        (root / "acct-b" / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
        hold = root / "acct-b" / "probe-hold"
        hold.write_text("", encoding="utf-8")
        t = time.time()
        p = subprocess.run([sys.executable, str(PROBE), "--root", str(root), "acct-b",
                            "--claude-bin", "/bin/sleep", "--timeout", "30"],
                           capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
        check("멈춤 표시가 있으면 안 띄운다",
              p.returncode == 0 and "멈춤" in p.stdout and time.time() - t < 5,
              p.stdout.strip()[:120])
        hold.unlink()
        p = subprocess.run([sys.executable, str(PROBE), "--root", str(root), "acct-b",
                            "--claude-bin", "/bin/true", "--timeout", "3",
                            "--ask-after", "2"],
                           capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
        check("표시를 지우면 다시 띄운다", "멈춤" not in p.stdout, p.stdout.strip()[:120])

    # ── 화면 한 줄에서 풀릴 시각 뽑기 ─────────────────────────────────────
    loader = importlib.machinery.SourceFileLoader("account_probe", str(PROBE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    pr = importlib.util.module_from_spec(spec)
    loader.exec_module(pr)

    import datetime as _d
    base = _d.datetime(2026, 11, 6, 10, 0).timestamp()
    kind, until = pr.parse_reset("hit your weekly limit · resets Nov 9, 6pm (Asia/Seoul)", base)
    check("7일 한도를 알아본다", kind == "7일 한도", kind)
    check("날짜가 붙은 시각을 뽑는다",
          until == _d.datetime(2026, 11, 9, 18, 0).timestamp(),
          _d.datetime.fromtimestamp(until).isoformat() if until else None)

    kind, until = pr.parse_reset("You've hit your 5-hour limit · resets 3:30pm", base)
    check("다섯 시간 한도를 알아본다", kind == "다섯 시간 한도", kind)
    check("시각만 적힌 줄은 오늘로 읽는다",
          until == _d.datetime(2026, 11, 6, 15, 30).timestamp(),
          _d.datetime.fromtimestamp(until).isoformat() if until else None)

    kind, until = pr.parse_reset("hit your 5-hour limit · resets 9am", base)
    check("이미 지난 시각은 다음 날로 읽는다",
          until == _d.datetime(2026, 11, 7, 9, 0).timestamp(),
          _d.datetime.fromtimestamp(until).isoformat() if until else None)

    _, until = pr.parse_reset("hit your weekly limit · resets Jan 2, 6pm",
                              _d.datetime(2026, 12, 30, 10, 0).timestamp())
    check("해를 넘긴 날짜도 앞날로 읽는다",
          until == _d.datetime(2027, 1, 2, 18, 0).timestamp(),
          _d.datetime.fromtimestamp(until).isoformat() if until else None)

    # 기다리는 시간은 실측에 맞춘다 — 서버에서 수치가 오기까지 2.0~2.6초, 한도를 다 쓴
    # 계정이 그 줄을 내놓기까지 8.1초였다(11-06). 넉넉히 두면 답 없는 계정 하나가 다시
    # 재기 전체를 그만큼 늘린다.
    p = subprocess.run([sys.executable, str(PROBE), "--help"],
                       capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    check("한 홈을 기다리는 시간이 실측에 맞춰져 있다", "기본 30" in p.stdout,
          [l for l in p.stdout.splitlines() if "기다리는" in l][:1])
    pw = subprocess.run([sys.executable, str(REPO / "bin" / "claude-usage-web"), "--help"],
                        capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    check("쪽에서 누를 때도 같은 값으로 기다린다", "기본 30" in pw.stdout,
          [l for l in pw.stdout.splitlines() if "기다리는" in l][:1])

    kind, until = pr.parse_reset("hit your weekly limit · 알 수 없는 꼴", base)
    check("못 알아보면 지어내지 않는다", kind == "7일 한도" and until is None, until)
    check("빈 줄이면 아무것도 안 돌려준다", pr.parse_reset("") == ("", None))

# ── 저 기계 수치 합치기 ───────────────────────────────────────────────
# 실호출 없이 본다. 지켜야 하는 것은 셋이다. ⑴ 저쪽 줄에 호스트 접두가 붙어 이쪽
# 줄과 안 섞인다 ⑵ 저쪽이 안 되면 그 줄만 실패로 적고 이쪽 줄은 그대로 난다
# ⑶ 저쪽 출력이 깨져도 수치를 지어내지 않는다.
# 본보기 ssh 대상은 글자를 이어 붙여 만든다 — 통째로 적으면 내용 검사(R76)가 이
# 파일을 메일 주소로 읽는다.
_user, _at = "someone", "@"
check("ssh 대상에서 짧은 이름을 뽑는다",
      m.host_tag(_user + _at + "home") == "home" and m.host_tag("peer") == "peer"
      and m.host_tag(_user + _at + "box" + ".example" + ".com") == "box",
      [m.host_tag(_user + _at + "home"), m.host_tag("peer")])


class _Ran:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _with_run(fake, fn):
    real = m.subprocess.run
    m.subprocess.run = fake
    try:
        return fn()
    finally:
        m.subprocess.run = real


ok_json = json.dumps({"now": 1.0, "rows": [
    {"label": "현재", "home": "/home/x", "state": "ok",
     "five_hour": {"used_percentage": 7, "resets_at": now + 600}},
    {"label": "acct-a", "home": "/home/x/a", "state": "no-cache"}]})
rows = _with_run(lambda *a, **k: _Ran(0, ok_json), lambda: m.collect_remote("gmoh@home"))
check("저쪽 줄에 호스트 접두가 붙는다",
      [r["label"] for r in rows] == ["home:현재", "home:acct-a"],
      [r["label"] for r in rows])
check("저쪽 줄에 호스트 칸이 남는다", all(r.get("host") == "home" for r in rows))
check("저쪽 수치를 그대로 옮긴다",
      rows[0]["five_hour"]["used_percentage"] == 7 and rows[1]["state"] == "no-cache")

bad = _with_run(lambda *a, **k: _Ran(255, "", "ssh: connect: refused"),
                lambda: m.collect_remote("peer"))
check("접속이 안 되면 그 줄 하나만 실패로 적는다",
      len(bad) == 1 and bad[0]["state"] == "error" and bad[0]["label"] == "peer",
      bad)
check("실패 줄에 까닭이 남는다", "rc=255" in bad[0]["detail"], bad[0]["detail"])

torn = _with_run(lambda *a, **k: _Ran(0, "{\"rows\": "),
                 lambda: m.collect_remote("peer"))
check("저쪽 출력이 깨지면 수치를 지어내지 않는다",
      len(torn) == 1 and torn[0]["state"] == "error" and "five_hour" not in torn[0],
      torn)


def _slow(*a, **k):
    raise m.subprocess.TimeoutExpired(cmd="ssh", timeout=m.REMOTE_TIMEOUT)


late = _with_run(_slow, lambda: m.collect_remote("peer"))
check("답이 늦으면 영원히 기다리지 않는다",
      len(late) == 1 and late[0]["state"] == "error" and "초" in late[0]["detail"],
      late[0]["detail"])

h = subprocess.run([sys.executable, str(CMD), "--help"], capture_output=True,
                   text=True, timeout=30, stdin=subprocess.DEVNULL)
check("저 기계를 부르는 길이 도움말에 있다", "--host" in h.stdout)
check("저쪽에서 돌릴 명령이 한 자리에 적혀 있다",
      m.REMOTE_CMD.endswith("--json") and "claude-accounts-usage" in m.REMOTE_CMD,
      m.REMOTE_CMD)

# ── 지금 쓰는 계정의 줄은 자리 사본이 아니라 지금 도는 홈에서 읽는다 ────────────
# 자리 사본은 갈아 끼울 때만 갱신된다. 그 계정으로 하루를 돌면 사본이 하루치 뒤처진
# 값을 사실처럼 보여 준다(2026-11-07 실측: 지금 계정이 7일 42% 인데 자리 사본은 13%).
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    root2 = base / "accounts"
    root2.mkdir()
    live = make_home(base, "live", {
        "_cached_at": now - 10,
        "rate_limits": {"seven_day": {"used_percentage": 42, "resets_at": now + 86400}},
    })
    seat = make_home(root2, "acct-b", {
        "_cached_at": now - 86400 * 0.75,
        "rate_limits": {"seven_day": {"used_percentage": 13, "resets_at": now + 86400}},
    })
    other = make_home(root2, "acct-a", {
        "_cached_at": now - 60,
        "rate_limits": {"seven_day": {"used_percentage": 65, "resets_at": now + 86400}},
    })

    def put_uuid(home, uid):
        (Path(home) / ".claude.json").write_text(
            json.dumps({"oauthAccount": {"accountUuid": uid}}), encoding="utf-8")

    put_uuid(live, "uuid-acct-b")
    put_uuid(seat, "uuid-acct-b")
    put_uuid(other, "uuid-acct-a")

    saved_home = os.environ.get("HOME")
    os.environ["HOME"] = str(live)
    try:
        rows = {r["label"]: r for r in m.collect(root2, include_current=False)}
        check("지금 쓰는 계정의 줄이 지금 값을 든다",
              rows["acct-b"]["seven_day"]["used_percentage"] == 42,
              rows["acct-b"].get("seven_day"))
        check("그 줄에 지금 계정이라는 표시가 붙는다", rows["acct-b"].get("live") is True)
        check("다른 계정 줄은 자기 자리 사본 그대로다",
              rows["acct-a"]["seven_day"]["used_percentage"] == 65
              and not rows["acct-a"].get("live"), rows["acct-a"].get("seven_day"))
        text = m.render(m.collect(root2, include_current=False), now)
        check("화면 줄에도 그 표시가 보인다", "(지금 이 계정)" in text, text)

        # 계정 번호가 없는 기계(사용량 서버처럼 대화형 세션이 없는 자리)는 바꿔치기가
        # 안 걸려야 한다 — 걸리면 그 기계가 자기 수치를 남의 계정 줄에 적는다.
        (Path(live) / ".claude.json").unlink()
        rows = {r["label"]: r for r in m.collect(root2, include_current=False)}
        check("계정 번호가 없으면 자리 사본을 그대로 읽는다",
              rows["acct-b"]["seven_day"]["used_percentage"] == 13
              and not rows["acct-b"].get("live"), rows["acct-b"].get("seven_day"))

        # 이름이 같아도 계정 번호가 다르면 남의 자리다.
        put_uuid(live, "uuid-다른계정")
        rows = {r["label"]: r for r in m.collect(root2, include_current=False)}
        check("이름이 같아도 계정 번호가 다르면 안 바꾼다",
              rows["acct-b"]["seven_day"]["used_percentage"] == 13
              and not rows["acct-b"].get("live"), rows["acct-b"].get("seven_day"))

        # 지금 도는 홈에 아직 수치가 없으면 자리 사본을 버리지 않는다. 같은 계정이라
        # 오래된 수치라도 아무것도 안 보여 주는 것보다 낫다 — 바꿔치기가 수치를 없애는
        # 쪽으로 돌면 이번에 고치려는 것과 같은 오해를 반대 방향으로 만든다.
        put_uuid(live, "uuid-acct-b")
        (Path(live) / ".claude" / "statusline_cache.json").unlink()
        rows = {r["label"]: r for r in m.collect(root2, include_current=False)}
        check("지금 홈에 수치가 없으면 자리 사본 값을 남긴다",
              rows["acct-b"].get("state") == "ok"
              and rows["acct-b"]["seven_day"]["used_percentage"] == 13,
              rows["acct-b"].get("state"))
        check("그래도 지금 계정이라는 표시는 붙는다", rows["acct-b"].get("live") is True)
    finally:
        if saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved_home


# ── 어느 자리로 도는지는 갈아 끼운 쪽이 적은 표식이 정한다 ────────────────────
# 설정의 계정 번호는 세션이 다음에 프로필을 받아올 때까지 옛 계정을 들고 있다. 그
# 값으로 판정하면 공용 계정 수치가 개인 계정 줄에 "지금 이 계정" 으로 붙는다
# (2026-11-09 둘째 기계 실측: 표식 shared3, 번호 ogm3614, 화면은 shared3 수치를 ogm3614 줄에).
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    root3 = base / "accounts"
    root3.mkdir()
    live = make_home(base, "live", {
        "_cached_at": now - 10,
        "rate_limits": {"seven_day": {"used_percentage": 24, "resets_at": now + 86400}},
    })
    seat_a = make_home(root3, "acct-a", {
        "_cached_at": now - 3600,
        "rate_limits": {"seven_day": {"used_percentage": 57, "resets_at": now + 86400}},
    })
    seat_b = make_home(root3, "acct-b", {
        "_cached_at": now - 3600,
        "rate_limits": {"seven_day": {"used_percentage": 9, "resets_at": now + 86400}},
    })

    def put_uuid(home, uid):
        (Path(home) / ".claude.json").write_text(
            json.dumps({"oauthAccount": {"accountUuid": uid}}), encoding="utf-8")

    def put_marker(home, name):
        p = Path(home) / ".claude" / "state"
        p.mkdir(parents=True, exist_ok=True)
        (p / "account-current").write_text(name + "\n", encoding="utf-8")

    # 번호는 아직 acct-b 를 가리키는데 표식은 acct-a 로 갈아 끼운 상태.
    put_uuid(live, "uuid-acct-b")
    put_uuid(seat_a, "uuid-acct-a")
    put_uuid(seat_b, "uuid-acct-b")
    put_marker(live, "acct-a")

    saved_home = os.environ.get("HOME")
    os.environ["HOME"] = str(live)
    try:
        rows = {r["label"]: r for r in m.collect(root3, include_current=False)}
        check("표식이 가리키는 자리가 지금 계정이다",
              rows["acct-a"].get("live") is True
              and rows["acct-a"]["seven_day"]["used_percentage"] == 24,
              rows["acct-a"].get("seven_day"))
        check("번호가 뒤처져 있어도 그 줄에는 표시가 안 붙는다",
              not rows["acct-b"].get("live")
              and rows["acct-b"]["seven_day"]["used_percentage"] == 9,
              rows["acct-b"].get("seven_day"))

        # 표식이 목록에 없는 자리를 가리키면 어느 줄도 지금 계정이 아니다 — 번호로
        # 내려가면 바로 이 사고(남의 줄에 지금 수치)가 돌아온다.
        put_marker(live, "acct-없음")
        rows = {r["label"]: r for r in m.collect(root3, include_current=False)}
        check("표식이 모르는 자리를 가리키면 아무 줄도 안 바꾼다",
              not any(r.get("live") for r in rows.values())
              and rows["acct-b"]["seven_day"]["used_percentage"] == 9,
              str([(k, v.get("live")) for k, v in rows.items()]))

        # 손으로 로그인만 한 기계에는 표식이 없다. 그때만 번호로 내려간다.
        (Path(live) / ".claude" / "state" / "account-current").unlink()
        rows = {r["label"]: r for r in m.collect(root3, include_current=False)}
        check("표식이 없으면 계정 번호로 판정한다",
              rows["acct-b"].get("live") is True
              and rows["acct-b"]["seven_day"]["used_percentage"] == 24,
              rows["acct-b"].get("seven_day"))
    finally:
        if saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved_home


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
