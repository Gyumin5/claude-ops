#!/usr/bin/env python3
"""정지된 세션에는 inbound 적체 경보를 내지 않는다.

왜 있나: 2026-09-15 옆 기계에서 운영자가 일부러 정지시켜 둔 네 세션(session-a·
session-g·session-h·session-f)이 같은 분에 '151분째 안 빠짐' 으로 한꺼번에
울렸고, 쿨다운이 한 시간이라 매시간 네 건씩 반복됐다.

원인은 감지 실패가 아니라 구조적 오탐이다. 유닛 목록이 --all 이라 dead 유닛까지
들어오는데, 정지된 세션은 큐를 비울 주체가 없으니 pending 이 영원히 안 빠진다 —
30분 임계를 반드시 넘는다. child 쪽 검사는 유닛 상태를 보고 판정을 접는 관문이
이미 있었고 적체 쪽만 그게 빠져 있었다.

그래서 고정하는 것 둘: 정지 유닛이면 적체 경보를 안 내고 streak 파일도 지운다,
활성 유닛의 기존 적체 판정은 그대로다. streak 을 지우는 것까지 보는 이유는,
남겨두면 세션이 다시 살아난 순간 정지해 있던 기간이 그대로 합산돼 첫 회차에
바로 울리기 때문이다.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "claude-telegram-healthcheck"

results = []


def check(name, cond, detail: object = ""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name +
          (("\n      " + str(detail)) if detail and not cond else ""))


def load(home):
    """HOME 을 갈아끼운 뒤 새로 읽는다 — 상태 경로가 import 시점에 굳는다."""
    os.environ["HOME"] = home
    loader = importlib.machinery.SourceFileLoader("tghc", str(SCRIPT))
    spec = importlib.util.spec_from_loader("tghc", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


DEAD = {"ActiveState": "inactive", "SubState": "dead", "MainPID": "0"}
NOPID = {"ActiveState": "active", "SubState": "running", "MainPID": "0"}
# 활성이지만 cgroup 을 못 읽는 상태. check_child 가 판정을 보류하고(cg_unknown)
# 적체 검사는 그대로 진행하는 갈래라 "기존 동작" 의 대표로 쓴다.
ALIVE = {"ActiveState": "active", "SubState": "running", "MainPID": "4242",
         "ControlGroup": "", "ActiveEnterTimestampMonotonic": "1"}


def run_once(mod, units, pending=1):
    """main() 을 한 번 돌리고 (보낸 알림, journalctl 로 나가는 요약줄) 을 돌려준다."""
    sent = []
    mod.list_claude_services = lambda: list(units)
    mod.svc_props = lambda unit: dict(units[unit])
    mod.load_control_token = lambda: "control-token"
    mod.svc_telegram_state_dir = lambda unit: str(STATE_ENV)
    mod.pending_count = lambda token: pending
    mod.telegram_post = lambda token, chat, text: (sent.append((chat, text)), True)[1]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.main()
    return sent, buf.getvalue().strip()


tmp = tempfile.mkdtemp(prefix="tghc-inactive.")
real_home = os.environ.get("HOME")
try:
    home = Path(tmp) / "home"
    (home / ".claude" / "state").mkdir(parents=True)
    STATE_ENV = Path(tmp) / "botstate"
    STATE_ENV.mkdir()
    (STATE_ENV / ".env").write_text("TELEGRAM_BOT_TOKEN=x\n", encoding="utf-8")
    mod = load(str(home))

    dead_unit = "claude-session-a.service"
    live_unit = "claude-dotfiles.service"

    # ── 정지 유닛 ──────────────────────────────────────────────────────
    # 임계를 한참 넘긴 streak 을 미리 깔아둔다. 옆 기계에서 실제로 151분이었다.
    mod.STATE_DIR.mkdir(parents=True, exist_ok=True)
    stale = mod.STATE_DIR / f"{dead_unit}.nonzero_since"
    stale.write_text(str(time.time() - 151 * 60), encoding="utf-8")

    sent, line = run_once(mod, {dead_unit: DEAD})
    check("정지된 세션에는 적체 경보를 안 낸다", sent == [], sent)
    check("정지된 세션의 streak 파일을 지운다", not stale.exists())
    check("쿨다운 표식도 안 남긴다 — 다시 살아나면 첫 회차부터 정상 판정이다",
          not (mod.STATE_DIR / f"{dead_unit}.backlog.alerted").is_file())
    check("건너뛴 사실이 결과 줄에 남는다 — 침묵이 아니다",
          "skip_backlog|inactive" in line, line)

    # 활성인데 주 프로세스가 없는 경우도 같다 — 큐를 비울 주체가 없다.
    stale.write_text(str(time.time() - 151 * 60), encoding="utf-8")
    sent, line = run_once(mod, {dead_unit: NOPID})
    check("주 프로세스가 없는 유닛도 같게 다룬다",
          sent == [] and not stale.exists() and "skip_backlog|nopid" in line, line)

    # ── 활성 유닛 — 기존 판정이 그대로여야 한다 ────────────────────────
    live_streak = mod.STATE_DIR / f"{live_unit}.nonzero_since"
    live_streak.write_text(str(time.time() - 151 * 60), encoding="utf-8")
    sent, line = run_once(mod, {live_unit: ALIVE})
    check("활성 유닛의 적체는 그대로 알린다", len(sent) == 1, sent)
    check("문구는 적체 경보 그대로다",
          bool(sent) and "inbound 적체 의심" in sent[0][1] and "pending=1" in sent[0][1],
          sent)
    check("활성 유닛의 streak 은 안 지운다", live_streak.is_file())

    # 큐가 비면 활성 유닛도 안 울린다 — 관문이 이 갈래를 안 건드렸는지 본다.
    (mod.STATE_DIR / f"{live_unit}.backlog.alerted").unlink(missing_ok=True)
    live_streak.write_text(str(time.time() - 151 * 60), encoding="utf-8")
    sent, line = run_once(mod, {live_unit: ALIVE}, pending=0)
    check("활성 유닛도 큐가 비면 안 울린다", sent == [] and not live_streak.exists(), sent)

    # ── 섞였을 때 ──────────────────────────────────────────────────────
    for u in (dead_unit, live_unit):
        (mod.STATE_DIR / f"{u}.backlog.alerted").unlink(missing_ok=True)
        (mod.STATE_DIR / f"{u}.nonzero_since").write_text(
            str(time.time() - 151 * 60), encoding="utf-8")
    sent, line = run_once(mod, {dead_unit: DEAD, live_unit: ALIVE})
    check("정지와 활성이 섞이면 활성 것만 울린다",
          len(sent) == 1 and live_unit.replace("claude-", "").replace(".service", "")
          in sent[0][1], sent)
finally:
    if real_home is not None:
        os.environ["HOME"] = real_home
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
