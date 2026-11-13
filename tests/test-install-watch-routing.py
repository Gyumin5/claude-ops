#!/usr/bin/env python3
"""install-watch 알림은 사람이 아니라 그 호스트의 저장소 세션으로 간다.

왜 있나: 배포를 안 돌린 것은 사람이 처리할 일이 아니라 커밋한 세션이 안 끝낸
일이다(전역 규칙 R39). 그런데 알림이 제어봇을 거쳐 사람 화면에 떠서, 사람이
그 내용을 다시 세션에 옮겨 적어야 했다(2026-10-13 운영자 지적).

고정하는 것 셋: 알림이 유저봇으로 그 호스트의 저장소 세션에 꽂힌다, 세션 이름을
마커의 machine_id 에서 뽑는다(home 이면 dotfiles, 아니면 <id>-dotfiles),
주입이 실패하면 조용히 삼키지 않고 사유를 붙여 사람에게 간다.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "claude-session-healthcheck"

results = []


def check(name, cond, detail: object = ""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name +
          (("\n      " + str(detail)) if detail and not cond else ""))


def _plant_chat_id(home):
    """방 번호는 추적 밖 설정에서 읽는다. 가짜 HOME 에 그 파일을 세워 실제 읽는 길을
    그대로 지나가게 한다 — 없으면 모듈이 import 자리에서 거절하고 죽는다.
    진짜 방 번호는 안 쓴다(지어낸 값이면 통로 판정에 충분하다)."""
    d = os.path.join(home, ".config", "dotfiles-private")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "telegram.json"), "w", encoding="utf-8") as f:
        f.write('{"operator_chat_id": "1234509876"}\n')


def load(home):
    """HOME 을 갈아끼운 뒤 새로 읽는다 — 상태 경로가 import 시점에 굳는다."""
    os.environ["HOME"] = home
    _plant_chat_id(home)
    loader = importlib.machinery.SourceFileLoader("cshc", str(SCRIPT))
    spec = importlib.util.spec_from_loader("cshc", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Done:
    def __init__(self, rc):
        self.returncode = rc
        self.stdout = ""
        self.stderr = ""


tmp = tempfile.mkdtemp(prefix="install-watch-routing.")
real_home = os.environ.get("HOME")
real_run = subprocess.run
try:
    home = Path(tmp) / "home"
    state = home / ".claude" / "state"
    state.mkdir(parents=True)
    (state / "install-last-ok").write_text(
        json.dumps({"head": "aaaaaaa", "machine_id": "home"}), encoding="utf-8")
    mod = load(str(home))

    check("home 호스트의 대상 세션은 home/dotfiles 다",
          mod.install_session_name() == "home/dotfiles", mod.install_session_name())

    (state / "install-last-ok").write_text(
        json.dumps({"head": "aaaaaaa", "machine_id": "peer"}), encoding="utf-8")
    check("다른 호스트는 <machine_id>/dotfiles 다",
          mod.install_session_name() == "peer/dotfiles", mod.install_session_name())

    (state / "install-last-ok").write_text(json.dumps({"head": "aaaaaaa"}),
                                           encoding="utf-8")
    check("machine_id 가 없으면 대상을 지어내지 않는다",
          mod.install_session_name() == "", mod.install_session_name())

    # ── 주입 성공: 사람에게는 안 간다 ──
    (state / "install-last-ok").write_text(
        json.dumps({"head": "aaaaaaa", "machine_id": "home"}), encoding="utf-8")
    sent, injected = [], []
    mod.telegram_alert = lambda token, text: (sent.append(text), True)[1]

    def fake_run_ok(cmd, **kw):
        injected.append(cmd)
        return Done(0)

    subprocess.run = fake_run_ok
    ok = mod.install_alert("토큰", "[install-watch] 시험 — 밀렸다")
    check("주입에 성공하면 참을 돌려준다", ok is True, ok)
    check("유저봇으로 그 호스트 세션에 꽂는다",
          injected and injected[0][:3] == ["claude-userbot-send", "--session", "home/dotfiles"],
          injected)
    check("주입이 되면 사람에게는 안 간다", sent == [], sent)

    # ── 주입 실패: 조용히 삼키지 않는다 ──
    def fake_run_fail(cmd, **kw):
        injected.append(cmd)
        return Done(3)

    subprocess.run = fake_run_fail
    mod.install_alert("토큰", "[install-watch] 시험 — 밀렸다")
    check("주입이 실패하면 사람에게 간다", len(sent) == 1, sent)
    check("실패 사유를 알림에 붙인다",
          sent and "rc=3" in sent[0] and "dotfiles" in sent[0], sent)
finally:
    subprocess.run = real_run
    if real_home is not None:
        os.environ["HOME"] = real_home
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
