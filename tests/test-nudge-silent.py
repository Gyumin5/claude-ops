#!/usr/bin/env python3
"""주기 주입을 무음으로 보내는 계약 검사.

운영자 지시(2026-10-24): 넛지가 소리 나게 가서 10분마다 푸시 알림이
하나씩 쌓인다. 메시지 자체는 60초 뒤 지워지지만 한 번 울린 알림은 안 사라져서,
운영자 화면에는 지워진 주입이 알림으로 계속 남아 보인다.

고정하는 것:
  1. --silent 를 주면 telethon 에 silent=True 로 넘어간다.
  2. 기본은 소리 나게 간다(silent=False) — 세션끼리 주고받는 통보·완료 보고는
     운영자가 알아야 하는 글이라 무음을 전체에 걸지 않는다.
  3. 릴레이(ssh) 경로도 --silent 를 그대로 넘긴다 — 둘째 기계 넛지가 홈 유저봇을
     빌려 쓰므로, 안 넘기면 그쪽만 소리가 난다.
  4. 넛지 회차가 실제로 --silent 를 붙여 부른다.
  5. 본문·지연삭제는 무음 여부와 무관하게 그대로다(무음은 알림만 끈다).
"""
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"\n      {detail}" if not cond and detail else ""))


# ---------------------------------------------------------------- 발송 쪽
sent_calls = []


class _Sent:
    id = 4242


class _FakeClient:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def is_user_authorized(self):
        return True

    def send_message(self, entity, text, **kw):
        sent_calls.append({"entity": entity, "text": text, **kw})
        return _Sent()


def _install_fake_telethon():
    tele = types.ModuleType("telethon")
    sync = types.ModuleType("telethon.sync")
    sync.TelegramClient = _FakeClient
    tele.sync = sync
    sys.modules["telethon"] = tele
    sys.modules["telethon.sync"] = sync


def load(tmp):
    path = REPO / "bin" / "claude-userbot-send"
    loader = importlib.machinery.SourceFileLoader("userbot_send_silent", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    # 실제 자격증명·세션 파일을 건드리지 않는다. API_ID/API_HASH 는 합성값이고
    # 가짜 telethon 이 받으므로 아무 데도 안 쓰인다.
    env = tmp / "userbot.env"
    env.write_text("API_" + "ID=1\nAPI_" + "HASH=" + "0" * 32 + "\n")
    mod.ENV_PATH = str(env)
    mod.SESSION_PATH = str(tmp / "userbot")
    (tmp / "userbot.session").write_text("")
    mod.LOCK_PATH = str(tmp / "userbot.lock")
    mod.TARGETS_PATH = str(tmp / "targets.json")
    mod.AMBIGUOUS_PATH = str(tmp / "ambiguous.json")
    return mod


def run_send(mod, argv):
    sent_calls.clear()
    old = sys.argv
    sys.argv = ["claude-userbot-send"] + argv
    try:
        mod.main()
    finally:
        sys.argv = old
    return sent_calls[-1] if sent_calls else None


tmp = Path(tempfile.mkdtemp(prefix="tsilent."))
_install_fake_telethon()
mod = load(tmp)
Path(mod.TARGETS_PATH).write_text(json.dumps({"home/probe-test": "@probe_bot"}))

call = run_send(mod, ["--no-origin", "--silent", "@probe_bot", "무음 시험"])
check("1. --silent 는 telethon 에 silent=True 로 간다", call and call.get("silent") is True, str(call))

call = run_send(mod, ["--no-origin", "@probe_bot", "보통 시험"])
check("2. 기본은 소리 나게 간다", call and call.get("silent") is False, str(call))

check("5. 무음은 본문을 바꾸지 않는다",
      call and call["text"] == "보통 시험" and call["parse_mode"] is None, str(call))

# ---------------------------------------------------------------- 릴레이 쪽
relayed = []


class _Proc:
    returncode = 0
    stdout = ""
    stderr = ""


def _fake_run(cmd, **kw):
    relayed.append(cmd)
    return _Proc()


mod.TARGETS_PATH = str(tmp / "no-such-targets.json")   # 로컬 유저봇 없는 머신 흉내
Path(tmp / "relay-alias.json").write_text(json.dumps({"home/probe-test": "probe-test"}))
mod.RELAY_ALIAS_PATH = str(tmp / "relay-alias.json")
mod._relay_host = lambda: "gmoh@example"
mod.subprocess = types.SimpleNamespace(run=_fake_run, Popen=lambda *a, **k: None,
                                       DEVNULL=subprocess.DEVNULL)
run_send(mod, ["--no-origin", "--silent", "--session", "home/probe-test", "릴레이 시험"])
joined = " ".join(relayed[-1]) if relayed else ""
check("3. 릴레이 경로도 --silent 를 넘긴다", "--silent" in joined, joined)

# ---------------------------------------------------------------- 넛지 쪽
NUDGE = str(REPO / "bin" / "claude-session-nudge")
nt = Path(tempfile.mkdtemp(prefix="tsilent-nudge."))
send_log = nt / "send.log"
send_sh = nt / "send.sh"
send_sh.write_text('#!/bin/bash\necho "$@" >> "%s"\nexit 0\n' % send_log)
probe_out = nt / "probe.json"
probe_sh = nt / "probe.sh"
probe_sh.write_text('#!/bin/bash\ncat "%s"\n' % probe_out)
for p in (send_sh, probe_sh):
    p.chmod(p.stat().st_mode | stat.S_IEXEC)

SESSION = "probe-test"
state_dir = nt / "state"
sfile = state_dir / "session-nudge" / (SESSION + ".json")
sfile.parent.mkdir(parents=True)
now = int(time.time())
sfile.write_text(json.dumps({"session": SESSION, "interval_min": 10,
                             "end_ts": 4102444800, "message": "테스트",
                             "enabled": True, "started_ts": now - 18000},
                            ensure_ascii=False))
probe_out.write_text(json.dumps({"session": SESSION, "unit_active": True,
                                 "quota_pct": 10.0, "transcript": "/x.jsonl",
                                 "last_assistant": now - 2400, "pending_tool": False,
                                 "pending_tool_since": 0, "pending_tool_name": "",
                                 "reason": ""}))
subprocess.run([NUDGE, "--tick", SESSION], capture_output=True, text=True,
               env=dict(os.environ,
                        CLAUDE_NUDGE_STATE_DIR=str(state_dir),
                        CLAUDE_NUDGE_SEND=str(send_sh),
                        CLAUDE_NUDGE_PROBE=str(probe_sh),
                        CLAUDE_NUDGE_TG_ENV=str(nt / "no-such.env"),
                        CLAUDE_NUDGE_LOG=str(nt / "nudge.log"),
                        CLAUDE_NUDGE_TICKLOG=str(nt / "ticks.log")))
line = send_log.read_text().strip() if send_log.is_file() else ""
check("4. 넛지 회차가 --silent 를 붙여 부른다", "--silent" in line, line[:160])
check("5b. 넛지의 지연삭제는 그대로다", "--delete-after" in line, line[:160])

print(f"\n{sum(results)}/{len(results)} PASS")
raise SystemExit(0 if all(results) else 1)
