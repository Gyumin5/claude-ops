"""claude-nudge-audit 검증 — 넛지 회차 기록을 읽어 이상만 집어내는지.

실제 알림은 안 보낸다. 알림 경로를 가짜 스크립트로 바꿔 호출 내용을 파일로 받는다.
회차 기록은 넛지가 쓰는 것과 같은 칸 모양으로 직접 만든다.

  1) 정상 기록만 있으면 아무것도 안 알린다
  2) 주입 실패가 한 번이면 안 잡는다(다음 회차에 저절로 다시 보낸다)
  3) 주입 실패가 두 번이면 잡는다 — 주입 경로가 끊긴 것이다
  4) 못 보고 건너뛴 회차(관측 실패·기록 없음·유닛 정지·탐침 실패)가 연속 상한에 닿으면
     잡고, 그 미만이거나 사이에 정상 회차가 끼면 안 잡는다
  5) 회차가 간격의 2.5배 넘게 끊기면 잡는다(단 회차 기록이 아예 없는 호스트는 안 센다)
  6) 등록이 꺼졌거나 종료시각이 지났으면 감시 대상이 아니다
  7) 같은 이상은 한 번만 알린다. 사라지면 해소를 한 번 알린다
  8) 알림이 실패하면 "알렸다" 로 적지 않고 다음 회차에 다시 알린다
  9) 넛지가 멈춰 있는데 그걸 알린 메시지가 없으면 잡는다(알림 실패 뒤 고착)
 10) 등록을 막 손댄 구간은 유닛 상태를 안 센다(시작·중지는 원자적이지 않다)
 11) 끝난 등록의 옛 회차는 창 안에 있어도 안 센다
 12) 회차 기록이 아예 없는 상태는 유예가 지나면 잡는다(기록기 고장이 영원히 조용해지지 않게)
 13) 못 읽는 상태파일·못 읽는 회차 기록은 관측 불능으로 잡는다
 14) 유닛 조회가 안 되면(답이 빔) 정지로 단정하지 않는다
"""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AUDIT = str(REPO / "bin" / "claude-nudge-audit")
SRC = Path(AUDIT).read_text()
tmp = Path(tempfile.mkdtemp(prefix="taudit."))
fails = []


def const(name, cast=float):
    m = re.search(r"^%s = ([0-9.]+)" % name, SRC, re.M)
    assert m, "%s 상수를 못 찾았다" % name
    return cast(m.group(1))


GAP_FACTOR = const("GAP_FACTOR")
NO_RECORD_RUN = const("NO_RECORD_RUN", int)
WINDOW_MIN = const("WINDOW_MIN", int)

ticklog = tmp / "ticks.log"
state_dir = tmp / "state"
nudge_dir = state_dir / "session-nudge"
nudge_dir.mkdir(parents=True)
audit_state = tmp / "audit-state"
notify_log = tmp / "notify.log"
machine_file = tmp / "machine-id"
machine_file.write_text("home\n")
# 공개판 점검기는 수신 세션 표를 설정 파일에서 읽는다. 비공개판은 이 값을 안 본다.
sessions_file = tmp / "audit-sessions.json"
sessions_file.write_text('{"home": "home/dotfiles"}\n')

notify_sh = tmp / "notify.sh"
notify_sh.write_text('#!/bin/bash\necho "$@" >> "%s"\n' % notify_log)
notify_sh.chmod(notify_sh.stat().st_mode | stat.S_IEXEC)

# 유닛 조회를 실제 systemd 에 묻지 않는다. 검사 중에 진짜 유닛이 있고 없고가 결과를
# 바꾸면 그 검사는 기계마다 다른 답을 낸다.
unit_state = tmp / "unit-state"
unit_state.write_text("active\n")
systemctl_sh = tmp / "systemctl.sh"
systemctl_sh.write_text('#!/bin/bash\ncat "%s"\n' % unit_state)
systemctl_sh.chmod(systemctl_sh.stat().st_mode | stat.S_IEXEC)

SESSION = "audit-test-session"


def register(session=SESSION, interval=10, enabled=True, started_ago_min=600,
             ends_in_min=600, registered_ago_sec=7200, **extra):
    """등록 하나를 만든다. registered_ago_sec 가 작으면 '방금 손댄 등록' 이라
    유닛 상태를 안 센다 — 그 유예는 10번에서 따로 본다."""
    now = int(time.time())
    d = {"session": session, "interval_min": interval, "enabled": enabled,
         "started_ts": now - started_ago_min * 60,
         "registered_ts": now - registered_ago_sec,
         "end_ts": now + ends_in_min * 60}
    d.update(extra)
    (nudge_dir / (session + ".json")).write_text(json.dumps(d, ensure_ascii=False))


def clear_registrations():
    for f in nudge_dir.glob("*.json"):
        f.unlink()


def tick(tag, ago_min, session=SESSION, idle="", ivl=10, rc=""):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S KST",
                          time.localtime(time.time() - ago_min * 60))
    with open(ticklog, "a") as fh:
        fh.write("\t".join([stamp, "session=%s" % session, "tag=%s" % tag,
                            "idle=%s" % idle, "ivl=%d" % ivl, "nores=0",
                            "await=0", "susp=0", "tool=", "rc=%s" % rc]) + "\n")


def run(dry=True):
    env = dict(os.environ)
    env.update({
        "CLAUDE_NUDGE_TICKLOG": str(ticklog),
        "CLAUDE_NUDGE_STATE_DIR": str(state_dir),
        "CLAUDE_NUDGE_AUDIT_STATE_DIR": str(audit_state),
        "CLAUDE_NUDGE_AUDIT_NOTIFY": str(notify_sh),
        "CLAUDE_MACHINE_ID_FILE": str(machine_file),
        "CLAUDE_NUDGE_AUDIT_SESSIONS": str(sessions_file),
        # 텔레그램 대체 경로가 검사에서 실제로 나가지 않게 없는 파일을 준다.
        "CLAUDE_NUDGE_TG_ENV": str(tmp / "no-such.env"),
        # 유닛 재조회 대기를 줄인다. 검사가 실제로 15초씩 자고 있을 이유가 없다.
        "CLAUDE_NUDGE_AUDIT_RECHECK_SEC": "0",
        "CLAUDE_NUDGE_AUDIT_SYSTEMCTL": str(systemctl_sh),
    })
    cmd = [sys.executable, AUDIT] + (["--dry-run"] if dry else [])
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
    if r.returncode != 0:
        fails.append("audit rc=%d %s" % (r.returncode, r.stderr.strip()[:200]))
    return r.stdout


def expect(name, out, needle, want=True):
    got = needle in out
    if got != want:
        fails.append("%s: '%s' %s — 출력:\n%s"
                     % (name, needle, "가 없다" if want else "가 잘못 잡혔다", out.strip()))
    print("  %-34s %s" % (name, "OK" if got == want else "FAIL"))


def reset_log():
    ticklog.write_text("")


# --- 1) 정상 기록만 있으면 조용하다 -------------------------------------------
# 등록은 살아 있어야 그 회차가 판정 대상이 된다. 유닛은 정상(active)으로 답하게 둔다 —
# 유닛 검사는 5번에서 따로 본다.
clear_registrations()
register()
reset_log()
for i in range(6):
    tick("working", ago_min=i * 10 + 1, idle=3)
tick("send", ago_min=35, idle=25)
tick("answered", ago_min=25)
out = run()
expect("1 정상 기록은 이상 없음", out, "이상 없음")

# --- 2·3) 주입 실패는 두 번부터 --------------------------------------------
reset_log()
tick("send-fail", ago_min=5, idle=25, rc=7)
out = run()
expect("2 한 번 실패는 안 잡음", out, "이어서 실패했다", want=False)
tick("send-fail", ago_min=2, idle=27, rc=7)
out = run()
expect("3 두 번 실패는 잡음", out, "이어서 실패했다")

# --- 4) 못 보고 건너뛴 회차가 연속 -------------------------------------------
reset_log()
for i in range(NO_RECORD_RUN - 1):
    tick("no-assistant-record", ago_min=(NO_RECORD_RUN - i) * 10)
out = run()
expect("4a 상한 미만은 안 잡음", out, "못 보고 건너뛴 회차가", want=False)
tick("no-assistant-record", ago_min=1)
out = run()
expect("4b 상한 도달은 잡음", out, "못 보고 건너뛴 회차가 연속 %d번" % NO_RECORD_RUN)

# 사유가 섞여도 같은 결과다 — 못 봤다는 점이 같다.
reset_log()
for tag in ("probe-fail", "no-transcript", "unit-not-active")[:NO_RECORD_RUN]:
    tick(tag, ago_min=NO_RECORD_RUN * 5)
out = run()
expect("4c 사유가 섞여도 잡음", out, "못 보고 건너뛴 회차가 연속")

# 사이에 정상 회차가 끼면 연속이 끊긴다.
reset_log()
for i in range(NO_RECORD_RUN):
    tick("no-assistant-record", ago_min=(NO_RECORD_RUN + 2 - i) * 5)
tick("working", ago_min=1, idle=2)
out = run()
expect("4d 정상 회차가 끼면 끊긴다", out, "못 보고 건너뛴 회차가", want=False)

# 도는 중(tool-running)과 한도 구간은 못 봤다가 아니다 — 안 센다.
reset_log()
for i in range(NO_RECORD_RUN + 2):
    tick("tool-running", ago_min=(NO_RECORD_RUN + 3 - i) * 5)
out = run()
expect("4e 도구 실행 중은 안 셈", out, "못 보고 건너뛴 회차가", want=False)

# --- 5) 회차 누락과 유닛 정지 -------------------------------------------------
# 회차가 허용 공백을 넘게 끊겼다. 유닛은 정상이라고 답하므로 공백으로 잡혀야 한다.
clear_registrations()
register(interval=10)
reset_log()
tick("working", ago_min=int(10 * GAP_FACTOR) + 5, idle=2)
out = run()
expect("5a 회차 공백 잡음", out, "마지막 넛지 회차가")

# 유닛이 꺼져 있으면 그게 더 센 신호라 그쪽으로 잡는다.
unit_state.write_text("inactive\n")
out = run()
expect("5b 유닛 정지 잡음", out, "넛지 유닛이 안 돈다")

# 조회가 안 되면(답이 빔) 정지로 단정하지 않는다.
unit_state.write_text("")
out = run()
expect("5c 조회 불가는 정지 아님", out, "넛지 유닛이 안 돈다", want=False)
unit_state.write_text("active\n")

# 기록이 아직 하나도 없고 등록도 막 한 상태는 공백이 아니다.
clear_registrations()
register(started_ago_min=1, registered_ago_sec=30)
reset_log()
out = run()
expect("5d 갓 등록은 공백 아님", out, "마지막 넛지 회차가", want=False)

# --- 6) 꺼졌거나 끝난 등록은 대상이 아니다 ------------------------------------
clear_registrations()
register(enabled=False)
reset_log()
out = run()
expect("6a 꺼진 등록은 감시 안 함", out, "이상 없음")
clear_registrations()
register(ends_in_min=-60)
out = run()
expect("6b 끝난 등록은 감시 안 함", out, "이상 없음")

# --- 7) 중복 알림 억제와 해소 알림 --------------------------------------------
clear_registrations()
register()
reset_log()
tick("send-fail", ago_min=4, rc=7)
tick("send-fail", ago_min=3, rc=7)
run(dry=False)
first = notify_log.read_text() if notify_log.exists() else ""
run(dry=False)
second = notify_log.read_text() if notify_log.exists() else ""
if first.count("이어서 실패했다") != 1:
    fails.append("7a 첫 알림이 1건이 아니다: %r" % first)
print("  %-34s %s" % ("7a 이상 1건 알림", "OK" if first.count("이어서 실패했다") == 1 else "FAIL"))
if second != first:
    fails.append("7b 같은 이상을 또 알렸다: %r" % second[len(first):])
print("  %-34s %s" % ("7b 같은 이상은 재알림 없음", "OK" if second == first else "FAIL"))

reset_log()
tick("working", ago_min=1, idle=2)
run(dry=False)
third = notify_log.read_text()
ok = "해소" in third[len(second):]
if not ok:
    fails.append("7c 해소 알림이 없다: %r" % third[len(second):])
print("  %-34s %s" % ("7c 해소되면 한 번 알림", "OK" if ok else "FAIL"))

# dry-run 은 상태를 안 바꾸고 알림도 안 한다.
before = (audit_state / "state.json").read_text()
reset_log()
tick("send-fail", ago_min=2, rc=9)
run(dry=True)
after = (audit_state / "state.json").read_text()
if before != after:
    fails.append("7d dry-run 이 상태를 바꿨다")
print("  %-34s %s" % ("7d dry-run 은 상태 안 건드림", "OK" if before == after else "FAIL"))

# --- 8) 알림이 실패하면 "알렸다" 로 적지 않는다 -------------------------------
# 적어 버리면 그 이상은 영영 안 나간다 — 조용한 실패를 잡으려는 장치가 조용해진다.
(audit_state / "state.json").write_text(json.dumps({"sig": "", "notified_ts": 0}))
clear_registrations()
register()
reset_log()
tick("send-fail", ago_min=4, rc=7)
tick("send-fail", ago_min=3, rc=7)
notify_sh_backup = notify_sh.read_text()
notify_sh.write_text('#!/bin/bash\nexit 1\n')       # 세션 알림도 텔레그램도 실패
run(dry=False)
st_after_fail = json.loads((audit_state / "state.json").read_text())
ok8a = st_after_fail.get("sig") == ""
if not ok8a:
    fails.append("8a 알림 실패인데 표식을 남겼다: %s" % st_after_fail.get("sig"))
print("  %-34s %s" % ("8a 알림 실패는 표식 안 남김", "OK" if ok8a else "FAIL"))

notify_sh.write_text(notify_sh_backup)               # 다음 회차엔 알림이 된다
before_len = len(notify_log.read_text()) if notify_log.exists() else 0
run(dry=False)
retried = notify_log.read_text()[before_len:] if notify_log.exists() else ""
ok8b = "이어서 실패했다" in retried
if not ok8b:
    fails.append("8b 다음 회차에 다시 알리지 않았다")
print("  %-34s %s" % ("8b 다음 회차에 다시 알림", "OK" if ok8b else "FAIL"))

# --- 9) 멈췄는데 알린 메시지가 없다 -------------------------------------------
clear_registrations()
register(suspended=True, alert_msg_id=None)
reset_log()
tick("suspended-hold", ago_min=2)
out = run()
expect("9a 알림 없이 멈춘 상태 잡음", out, "알린 메시지가 없다")
clear_registrations()
register(suspended=True, alert_msg_id=12345)
out = run()
expect("9b 알림이 있으면 안 잡음", out, "알린 메시지가 없다", want=False)

# --- 10) 등록을 막 손댄 구간은 유닛을 안 센다 ---------------------------------
clear_registrations()
register(registered_ago_sec=0)      # 방금 등록 — 유닛이 아직 안 올라왔을 수 있다
reset_log()
unit_state.write_text("inactive\n")
out = run()
expect("10 막 등록한 구간은 유예", out, "넛지 유닛이 안 돈다", want=False)
unit_state.write_text("active\n")

# --- 11) 끝난 등록의 옛 회차는 안 센다 ----------------------------------------
clear_registrations()
register(ends_in_min=-60)           # 종료시각이 지난 등록
reset_log()
tick("send-fail", ago_min=4, rc=7)
tick("send-fail", ago_min=3, rc=7)
out = run()
expect("11 끝난 등록의 회차는 무시", out, "이어서 실패했다", want=False)

# --- 12) 회차 기록이 아예 없는 상태는 유예가 지나면 잡는다 --------------------
# 기록기가 고장나면 이 파일이 그대로 비어 있고, 그러면 공백·연속 판정이 전부 볼 게
# 없어 조용해진다. 배포 직후만 봐주고 그 뒤로는 이상으로 본다.
clear_registrations()
register(started_ago_min=10, registered_ago_sec=600)
reset_log()
out = run()
expect("12a 유예 안에서는 안 잡음", out, "한 줄도 없다", want=False)
clear_registrations()
register(started_ago_min=600, registered_ago_sec=36000)
out = run()
expect("12b 유예 지나면 잡음", out, "한 줄도 없다")

# --- 13) 못 읽는 상태파일·회차 기록은 관측 불능으로 잡는다 --------------------
clear_registrations()
register()
(nudge_dir / "audit-test-broken.json").write_text("{깨진 json")
reset_log()
tick("working", ago_min=2, idle=2)
out = run()
expect("13a 깨진 상태파일 잡음", out, "상태파일을 못 읽는다")
(nudge_dir / "audit-test-broken.json").unlink()

ticklog.unlink()
ticklog.mkdir()                     # 파일 자리에 디렉토리 — 열면 OSError
out = run()
expect("13b 못 읽는 회차 기록 잡음", out, "회차 기록을 못 읽는다")
ticklog.rmdir()
reset_log()

# --- 14) 성공 전송 뒤에는 옛 실패를 현재 고장으로 보지 않는다 -----------------
clear_registrations()
register()
reset_log()
tick("send-fail", ago_min=9, rc=7)
tick("send-fail", ago_min=8, rc=7)
tick("send", ago_min=4, idle=22)
tick("working", ago_min=1, idle=2)
out = run()
expect("14 성공 뒤 옛 실패는 안 잡음", out, "이어서 실패했다", want=False)

if fails:
    print("\nFAIL %d건" % len(fails))
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("PASS claude-nudge-audit — 이상(유닛 정지·회차 공백·주입 실패 2회·못 본 회차 연속)만 "
      "집어내고, 같은 이상은 한 번만 알리며, 알림이 실패하면 다음 회차에 다시 알린다.")
