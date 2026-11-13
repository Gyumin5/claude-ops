#!/usr/bin/env python3
"""bin/claude-accounts 의 자격 갈아 끼우기 검사.

가짜 자격으로만 돈다 — HOME 을 임시 디렉토리로 돌려서 진짜 토큰은 한 바이트도 안 걸린다.

보는 것은 넷이다. 새 자격이 제대로 들어가나, 앞 자격을 되돌릴 수 있게 남기나,
모양이 아닌 것을 거절하면서 지금 자격을 지키나, 그리고 세션을 재시작하지 않나.
"""

import fcntl
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACCOUNTS = ROOT / "bin" / "claude-accounts"

fails = []


def check(name, cond, detail=None):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("  — " + str(detail) if detail else ""))
    if not cond:
        fails.append(name)


def apply_login(home, src):
    """교체 명령을 그 HOME 으로 한 번 돌린다. 돌아오는 것은 종료값이다."""
    env = dict(os.environ, HOME=str(home))
    p = subprocess.run([str(ACCOUNTS), "--apply-login", str(src)],
                       capture_output=True, text=True, timeout=60, env=env)
    return p.returncode


def token(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def mode(path):
    try:
        return oct(Path(path).stat().st_mode & 0o777)[2:]
    except OSError:
        return None


with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    home = base / "home"
    (home / ".claude").mkdir(parents=True)
    live = home / ".claude" / ".credentials.json"
    backup = home / ".claude" / "state" / "accounts" / "last-live.json"

    def fake(name):
        p = base / (name + ".json")
        p.write_text(json.dumps(
            {"claudeAiOauth": {"accessToken": "FAKE-" + name, "subscriptionType": "test"}}),
            encoding="utf-8")
        return p

    a, b = fake("aaa"), fake("bbb")

    print("=== 처음 적용 ===")
    check("살아 있는 자격이 없는 자리에도 적용된다", apply_login(home, a) == 0)
    check("live 가 새 자격이다", token(live) == "FAKE-aaa", token(live))
    check("남이 못 읽는 권한이다", mode(live) == "600", mode(live))

    print("=== 바꿔치기 ===")
    apply_login(home, b)
    check("live 가 갈렸다", token(live) == "FAKE-bbb", token(live))
    check("앞 자격을 사본으로 남긴다", token(backup) == "FAKE-aaa", token(backup))
    check("사본도 남이 못 읽는다", mode(backup) == "600", mode(backup))

    print("=== 되돌리기 ===")
    # 되돌릴 때는 백업 파일 자체가 새 자격으로 들어온다. 백업을 먼저 뜨면 그 파일이
    # 자기 자신을 덮어써서 되돌리기가 조용히 안 되고 되돌릴 길까지 사라진다.
    check("사본을 그대로 넘겨도 되돌아간다",
          apply_login(home, backup) == 0 and token(live) == "FAKE-aaa", token(live))
    check("되돌린 뒤에도 자격이 남아 있다", token(live) is not None, token(live))

    print("=== 거절해야 하는 것 ===")
    junk = base / "junk.json"
    junk.write_text("not json\n", encoding="utf-8")
    check("토큰 모양이 아니면 거절한다", apply_login(home, junk) != 0)
    check("거절해도 live 는 그대로다", token(live) == "FAKE-aaa", token(live))
    empty = base / "empty.json"
    empty.write_text(json.dumps({"claudeAiOauth": {"subscriptionType": "test"}}), encoding="utf-8")
    check("토큰 칸이 비면 거절한다", apply_login(home, empty) != 0)
    check("없는 파일은 거절한다", apply_login(home, base / "없는파일.json") != 0)

    # live 가 심링크면 추종해서 엉뚱한 경로를 덮을 수 있다 — 그 전에 멈춘다.
    real = base / "real.json"
    live.rename(real)
    live.symlink_to(real)
    check("live 가 심링크면 거절한다", apply_login(home, b) != 0)
    check("심링크가 가리키던 곳을 안 덮는다", token(real) == "FAKE-aaa", token(real))
    live.unlink()
    real.rename(live)

    print("=== 남기는 표시 ===")
    marker = home / ".claude" / "state" / "rate-limit-login-applied"
    check("적용 시각을 남긴다 (한도 수치를 들어내는 쪽이 이걸 본다)",
          marker.exists() and marker.stat().st_size > 0)

    print("=== 한도 캐시 ===")
    # 떠 있는 세션은 교체 직후부터 새 계정으로 요청을 보내는데(11-06 실측) 캐시의 계정
    # 이름표는 세션이 다시 뜰 때까지 옛 계정이다. 두 계정 수치가 한 계정 것으로 합쳐지면
    # 다 쓴 계정이 여유 있게도 그 반대로도 읽힌다. 복구기 타이머(10분)를 기다리지 않는다.
    cache = home / ".claude" / "statusline_cache.json"
    cache.write_text(json.dumps({
        "_account": "11111111-1111-1111-1111-111111111111",
        "rate_limits": {"five_hour": {"used_percentage": 7},
                        "seven_day": {"used_percentage": 99}},
        "model": "지키는 값",
    }, ensure_ascii=False), encoding="utf-8")
    check("교체가 한도 수치를 비운다", apply_login(home, b) == 0 and not (
        json.loads(cache.read_text(encoding="utf-8")).get("rate_limits")),
        cache.read_text(encoding="utf-8")[:200])
    check("수치 말고 다른 칸은 그대로 둔다",
          json.loads(cache.read_text(encoding="utf-8")).get("model") == "지키는 값")

print("=== 동시에 들어오면 한쪽을 막는다 ===")
# 자격 파일은 이 기계에 한 벌이라 두 입구(텔레그램 버튼·터미널 메뉴)가 같은 순간에
# 들어오면 섞인다. 잠금은 기다리지 않고 거절하는 쪽이다 — 부르는 쪽이 다음 회차에
# 다시 하면 되고, 기다리게 하면 봇이 그 자리에서 묶인다.
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    home = base / "home"
    (home / ".claude").mkdir(parents=True)
    live = home / ".claude" / ".credentials.json"

    def seed(name, tok):
        d = home / "claude-accounts" / name / ".claude"
        d.mkdir(parents=True)
        (d / ".credentials.json").write_text(json.dumps(
            {"claudeAiOauth": {"accessToken": tok, "subscriptionType": "test"}}),
            encoding="utf-8")

    seed("acct-a", "FAKE-acct-a")
    live.write_text(json.dumps(
        {"claudeAiOauth": {"accessToken": "FAKE-start", "subscriptionType": "test"}}),
        encoding="utf-8")

    def use(name):
        env = dict(os.environ, HOME=str(home))
        return subprocess.run([str(ACCOUNTS), "--use", name], capture_output=True,
                              text=True, timeout=60, env=env).returncode

    work = home / ".claude" / "state" / "accounts"
    work.mkdir(parents=True, exist_ok=True)
    with open(work / ".switch.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        rc_held = use("acct-a")
        held = token(live)
        fcntl.flock(fh, fcntl.LOCK_UN)
    check("잠금이 잡혀 있으면 거절한다", rc_held != 0, rc_held)
    check("거절한 쪽은 자격을 안 건드린다", held == "FAKE-start", held)
    check("잠금이 풀리면 돈다", use("acct-a") == 0 and token(live) == "FAKE-acct-a", token(live))

print("=== 갈아 끼우는 구현이 하나다 ===")
src = ACCOUNTS.read_text(encoding="utf-8")
# 같은 절차가 두 벌이면 잠금·백업 자리·표식이 갈려서 동시에 돌 때 자격이 섞인다
# (2026-11-06 에 실제로 그랬다 — bash 와 python 이 서로 다른 잠금 파일을 잡고 있었다).
check("폐기한 두 번째 구현이 없다", not (ROOT / "bin" / "claude-account-switch").exists())
check("봇이 이 구현을 부른다",
      "claude-accounts" in (ROOT / "bin" / "claude-control-bot").read_text(encoding="utf-8")
      and "claude-account-switch" not in
          (ROOT / "bin" / "claude-control-bot").read_text(encoding="utf-8"))

print("=== 잠금은 한 자리다 ===")
# 잠금 파일이 갈리면 flock 은 서로를 못 막는다 — 같은 파일을 잡아야 막힌다.
locks = sorted(set(x for x in src.split() if ".lock" in x))
check("잠금 파일 이름이 하나다", len(locks) <= 1, locks)

print("=== 세션 재시작 ===")
# 자격을 바꿔도 떠 있는 세션은 그대로 돌고(11-06 실측 fdfdc1e), 맥락을 다시 읽는 값이
# 교체 자체에는 안 든다. 재시작을 부르면 그 전제가 깨진다.
check("교체가 세션을 재시작하지 않는다", "restart" not in src)
# systemctl 을 쓰는 자리는 복구기를 한 번 부르는 것뿐이다. 유닛을 바꾸거나 멈추는 자리가
# 생기면 위 전제가 조용히 깨지므로 여기서 묶어 둔다.
sysc = [ln.strip() for ln in src.splitlines() if "systemctl" in ln and not ln.strip().startswith("#")]
check("systemctl 을 쓰는 자리는 복구기를 한 번 부르는 것뿐이다",
      len(sysc) == 1 and "--no-block" in sysc[0] and "start" in sysc[0], repr(sysc))

print("=== 갈아 끼운 직후 복구기를 부른다 ===")
# 복구기는 10분 타이머라 안 부르면 계정이 바뀐 것을 알아채는 데 최대 10분이 걸린다.
# 그 동안 한도에 막혀 큐가 쌓인 세션은 쓸 수 있는 계정을 두고도 조용하다. 밤에 저절로
# 갈아 끼우는 쪽에만 이 호출이 있어서, 2026-11-11 13:00 에 손으로 바꾼 회차가 타이머를
# 기다렸다(운영자 지적 9080).
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    home = base / "home"
    (home / ".claude").mkdir(parents=True)
    live = home / ".claude" / ".credentials.json"
    bins = base / "bin"
    bins.mkdir()
    calls = base / "systemctl-calls.txt"
    stub = bins / "systemctl"
    stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + str(calls) + "\n", encoding="utf-8")
    stub.chmod(0o755)

    def run_use(name):
        env = dict(os.environ, HOME=str(home),
                   PATH="{}:{}".format(bins, os.environ.get("PATH", "")))
        return subprocess.run([str(ACCOUNTS), "--use", name], capture_output=True,
                              text=True, timeout=60, env=env).returncode

    def run_apply(src_path):
        env = dict(os.environ, HOME=str(home),
                   PATH="{}:{}".format(bins, os.environ.get("PATH", "")))
        return subprocess.run([str(ACCOUNTS), "--apply-login", str(src_path)],
                              capture_output=True, text=True, timeout=60, env=env).returncode

    def kicks():
        if not calls.exists():
            return []
        return [ln for ln in calls.read_text(encoding="utf-8").splitlines()
                if "claude-rate-limit-recovery" in ln]

    d = home / "claude-accounts" / "acct-k" / ".claude"
    d.mkdir(parents=True)
    (d / ".credentials.json").write_text(json.dumps(
        {"claudeAiOauth": {"accessToken": "FAKE-k", "subscriptionType": "test"}}),
        encoding="utf-8")

    check("손으로 갈아 끼워도 복구기를 부른다", run_use("acct-k") == 0 and len(kicks()) == 1,
          repr(kicks()))
    check("막지 않고 부른다", all("--no-block" in k for k in kicks()), repr(kicks()))

    calls.unlink()
    good = base / "good.json"
    good.write_text(json.dumps(
        {"claudeAiOauth": {"accessToken": "FAKE-g", "subscriptionType": "test"}}),
        encoding="utf-8")
    check("방금 로그인한 자격을 올릴 때도 부른다",
          run_apply(good) == 0 and len(kicks()) == 1, repr(kicks()))

    calls.unlink()
    check("갈아 끼우지 못했으면 안 부른다 — 바뀐 것이 없다",
          run_apply(base / "없는파일.json") != 0 and kicks() == [], repr(kicks()))

print()
print("=== 교체를 한 줄로 남긴다 ===")
# 2026-11-11 13:31:42 에 계정이 바뀌어 여섯 세션이 깼는데, 그게 사람 손인지 자동인지를
# 되짚을 기록이 어디에도 없었다. 복구기의 자동 표식은 한 번 읽고 지우는 값이라 지나간
# 회차를 못 가린다. 교체하는 쪽이 직접 한 줄 남기면 그 물음이 파일 하나로 닫힌다.
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    home = base / "home"
    (home / ".claude").mkdir(parents=True)
    log = home / ".claude" / "logs" / "account-switch.log"

    def run_acct(*args):
        return subprocess.run([str(ACCOUNTS), *args], capture_output=True, text=True,
                              timeout=60, env=dict(os.environ, HOME=str(home))).returncode

    def lines():
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    for nm in ("acct-a", "acct-b"):
        d = home / "claude-accounts" / nm / ".claude"
        d.mkdir(parents=True)
        (d / ".credentials.json").write_text(json.dumps(
            {"claudeAiOauth": {"accessToken": "FAKE-" + nm, "subscriptionType": "test"}}),
            encoding="utf-8")

    check("첫 교체가 한 줄 남는다", run_acct("--use", "acct-a") == 0 and len(lines()) == 1,
          repr(lines()))
    check("그 줄에 어디로 갔는지가 있다", "acct-a" in (lines() or [""])[-1], repr(lines()))
    check("그 줄에 세대가 있다", "세대=" in (lines() or [""])[-1], repr(lines()))
    check("그 줄에 자동 표식 유무가 있다", "자동표식=" in (lines() or [""])[-1], repr(lines()))
    check("그 줄에 부른 쪽이 있다", "부른쪽=" in (lines() or [""])[-1], repr(lines()))

    check("다음 교체는 어디서 왔는지를 적는다",
          run_acct("--use", "acct-b") == 0 and len(lines()) == 2
          and "acct-a -> acct-b" in lines()[-1], repr(lines()))

    check("같은 계정을 다시 골라도 남는다 — 안 바뀐 것도 사실이다",
          run_acct("--use", "acct-b") == 0 and len(lines()) == 3
          and "그대로" in lines()[-1], repr(lines()))

    nlog = len(lines())
    check("갈아 끼우지 못하면 안 남는다",
          run_acct("--use", "없는계정") != 0 and len(lines()) == nlog, repr(lines()))

print()
