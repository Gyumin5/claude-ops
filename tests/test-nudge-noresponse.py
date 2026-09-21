"""claude-session-nudge 무응답 감지 상태기계 검증.

실제 전송·텔레그램 호출 없이 tick 한 회차씩 돌려 상태 전이를 본다.
  1) 응답하면 계속 보낸다(무응답 카운터 0)
  2) 무응답 1회는 다시 보낸다(성급히 멈추지 않는다)
  3) 무응답 2회면 멈추고 알린다
  4) 멈춘 뒤에는 안 보낸다(큐를 더 쌓지 않는다)
  5) 세션이 다시 응답하면 자동 재개된다
  6) 한도·도구실행·유닛정지는 무응답으로 세지 않는다
  7) 전송 실패는 무응답으로 세지 않는다(말을 건 적이 없다)
  8) 도구가 오래 열려 있으면(권한 승인 대기 등) 조용히 건너뛰지 않고 알린다
  9) 시간을 새로 등록해도(--start 재호출) 무응답 추적·고정 알림 id 를 잃지 않는다
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# 저장소 뿌리를 이 파일 위치에서 찾는다. 예전엔 홈 아래 고정 경로였는데,
# 그러면 다른 자리에 받은 사람은 그대로 못 돌린다.
REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

NUDGE = os.path.join(REPO, "bin/claude-session-nudge")
SESSION = "probe-test"
tmp = Path(tempfile.mkdtemp(prefix="tnudge."))
fails = []

probe_out = tmp / "probe.json"
send_rc = tmp / "send_rc"
send_log = tmp / "send.log"

probe_sh = tmp / "probe.sh"
probe_sh.write_text('#!/bin/bash\ncat "%s"\n' % probe_out)
send_sh = tmp / "send.sh"
send_sh.write_text('#!/bin/bash\necho "$@" >> "%s"\nexit "$(cat %s)"\n'
                   % (send_log, send_rc))
for p in (probe_sh, send_sh):
    p.chmod(p.stat().st_mode | stat.S_IEXEC)

state_dir = tmp / "state"
sfile = state_dir / "session-nudge" / (SESSION + ".json")
sfile.parent.mkdir(parents=True)
sfile.write_text(json.dumps({
    "session": SESSION, "interval_min": 30, "end_ts": 4102444800,
    "message": "테스트", "enabled": True}, ensure_ascii=False))

ENV = dict(os.environ,
           CLAUDE_NUDGE_STATE_DIR=str(state_dir),
           CLAUDE_NUDGE_SEND=str(send_sh),
           CLAUDE_NUDGE_PROBE=str(probe_sh),
           # 없는 경로 → tg() 가 네트워크를 건드리지 않는다.
           CLAUDE_NUDGE_TG_ENV=str(tmp / "no-such.env"))


def set_probe(last_assistant, *, active=True, quota=10.0, pending=False,
              pending_age_min=0, transcript="/x.jsonl"):
    """pending_age_min = 그 도구가 열린 지 몇 분 됐나(현재 시각 기준)."""
    probe_out.write_text(json.dumps({
        "session": SESSION, "unit_active": active, "quota_pct": quota,
        "transcript": transcript, "last_assistant": last_assistant,
        "pending_tool": pending,
        "pending_tool_since": int(time.time()) - pending_age_min * 60 if pending else 0,
        "pending_tool_name": "Bash" if pending else "", "reason": ""}))


def tick(label, expect_in, expect_send):
    before = send_log.read_text().count("\n") if send_log.is_file() else 0
    r = subprocess.run([NUDGE, "--tick", SESSION], capture_output=True,
                       text=True, env=ENV)
    line = (r.stdout or "").strip()
    after = send_log.read_text().count("\n") if send_log.is_file() else 0
    sent = after > before
    ok = expect_in in line and sent == expect_send
    print("%-28s %-46s 전송=%s  %s"
          % (label, line[:46], sent, "OK" if ok else "FAIL"))
    if not ok:
        fails.append("%s: line=%r sent=%s (기대 %r/%s)"
                     % (label, line, sent, expect_in, expect_send))
    return line


send_rc.write_text("0")

set_probe(1000)
tick("1a 첫 회차", "전송", True)
set_probe(2000)                       # 응답했다
tick("1b 응답 후", "전송", True)

set_probe(2000)                       # 그대로 = 무응답
tick("2  무응답 1회", "무응답 1/2", True)

set_probe(2000)
tick("3  무응답 2회", "넛지 중단", False)

st = json.loads(sfile.read_text())
print("   상태: suspended=%s no_response=%s" % (st.get("suspended"), st.get("no_response")))
if not st.get("suspended"):
    fails.append("상한 도달인데 suspended 가 아니다")

set_probe(2000)
tick("4  중단 후", "대기", False)

set_probe(3000)                       # 사람이 승인 → 세션이 응답
tick("5  복구", "복구", False)
st = json.loads(sfile.read_text())
print("   상태: suspended=%s no_response=%s" % (st.get("suspended"), st.get("no_response")))
if st.get("suspended"):
    fails.append("응답이 돌아왔는데 suspended 가 안 풀렸다")
set_probe(4000)
tick("5b 재개 후", "전송", True)

set_probe(4000, quota=95.0)
tick("6a 한도 중", "quota-blocked", False)
set_probe(4000, pending=True, pending_age_min=2)
tick("6b 도구 실행 중(2분)", "tool-running", False)
set_probe(4000, active=False)
tick("6c 유닛 정지", "unit-not-active", False)
st = json.loads(sfile.read_text())
if int(st.get("no_response") or 0) != 0:
    fails.append("예외 상황이 무응답으로 계수됐다: %s" % st.get("no_response"))

# 7) 전송이 실패하면 그 회차는 말을 건 적이 없다 — 다음 회차에 무응답으로 세면 안 된다.
send_rc.write_text("1")
set_probe(4000)
# 직전(5b) 전송은 성공했으므로 여기서 1회 계수되는 건 맞다. 다만 이번 전송은 실패했고
# 그 사실이 문구에 남아야 한다.
tick("7a 전송 실패", "재전송 실패 rc=1", True)
set_probe(4000)
tick("7b 실패 다음 회차", "전송 실패 rc=1", True)
send_rc.write_text("0")
set_probe(4000)
tick("7c 전송 복구", "재전송(직전 전송 실패라 무응답 계수 안 함)", True)
st = json.loads(sfile.read_text())
print("   상태: no_response=%s (실패한 두 회차는 안 세서 1 에 머문다)" % st.get("no_response"))
if int(st.get("no_response") or 0) != 1:
    fails.append("전송 실패 회차를 무응답으로 셌다: %s (기대 1)" % st.get("no_response"))

# 8) 도구가 오래 열려 있으면 조용히 건너뛰지 않는다. 권한 승인 대기가 여기 걸린다 —
#    요청 자체는 transcript 에 안 남으므로 "오래 열린 도구"가 우리가 볼 수 있는 전부다.
set_probe(4000, pending=True, pending_age_min=90)
tick("8a 도구 90분 열림", "넛지 중단", False)
st = json.loads(sfile.read_text())
print("   상태: suspended=%s" % st.get("suspended"))
if not st.get("suspended"):
    fails.append("도구가 90분 열렸는데 suspended 가 아니다")

set_probe(4000, pending=True, pending_age_min=120)
tick("8b 계속 열림", "대기", False)

set_probe(5000, pending=True, pending_age_min=1)   # 승인됨 → 다음 도구가 막 돌기 시작
tick("8c 승인 후 복구", "복구", False)
st = json.loads(sfile.read_text())
print("   상태: suspended=%s" % st.get("suspended"))
if st.get("suspended"):
    fails.append("도구가 진행됐는데 고정이 안 풀렸다")

# --- 9) 컨트롤봇에서 켜진 세션에 시간을 새로 등록하면 --start 가 다시 불린다.
# 그때 상태파일을 통째 새 dict 로 덮으면 alert_msg_id 를 잃고, 그러면 고정된 텔레그램
# 알림을 아무도 못 푼다(clear_alert 가 그 id 로만 푼다). systemctl 은 가짜로 세운다 —
# 여기서 검증하는 건 상태파일 쓰기지 유닛 기동이 아니다.
fake_bin = tmp / "bin"
fake_bin.mkdir()
fake_systemctl = fake_bin / "systemctl"
fake_systemctl.write_text("#!/bin/bash\nexit 0\n")
fake_systemctl.chmod(0o755)
START_ENV = dict(ENV, PATH="%s:%s" % (fake_bin, os.environ["PATH"]))

st = json.loads(sfile.read_text())
st.update({"suspended": True, "alert_msg_id": 424242, "no_response": 2,
           "last_assistant": 5000, "end_ts": int(time.time()) + 60})
sfile.write_text(json.dumps(st, ensure_ascii=False))
r = subprocess.run([NUDGE, "--start", "--sessions", SESSION, "--hours", "6"],
                   capture_output=True, text=True, env=START_ENV)
st = json.loads(sfile.read_text())
extended = st.get("end_ts", 0) > int(time.time()) + 5 * 3600
ok = (r.returncode == 0 and extended and st.get("alert_msg_id") == 424242
      and st.get("suspended") is True and st.get("no_response") == 2
      and st.get("last_assistant") == 5000 and st.get("enabled") is True)
print("%-28s rc=%s 종료시각갱신=%s 보존=%s  %s"
      % ("9a 재등록 보존", r.returncode, extended,
         (st.get("suspended"), st.get("alert_msg_id"), st.get("no_response")),
         "OK" if ok else "FAIL"))
if not ok:
    fails.append("재등록이 추적 필드를 지웠거나 시각을 안 늘렸다: rc=%s %r"
                 % (r.returncode, st))

# 처음 켜는 경우엔 그 필드가 없어야 한다 — 없는 상태를 물려받은 것처럼 만들지 않는다.
sfile.unlink()
subprocess.run([NUDGE, "--start", "--sessions", SESSION, "--hours", "6"],
               capture_output=True, text=True, env=START_ENV)
st = json.loads(sfile.read_text())
ok = "suspended" not in st and "alert_msg_id" not in st and st.get("enabled") is True
print("%-28s 키=%s  %s" % ("9b 새 등록", sorted(st.keys()), "OK" if ok else "FAIL"))
if not ok:
    fails.append("새 등록인데 추적 필드가 생겼다: %r" % st)

print()
if fails:
    for f in fails:
        print("FAIL:", f)
    sys.exit(1)
print("PASS — 응답 시 계속 전송, 1회는 재시도, 2회면 중단·알림, 중단 중 무전송, "
      "응답 복귀 시 자동 재개, 한도·도구실행·유닛정지·전송실패는 미계수, "
      "오래 열린 도구는 조용히 건너뛰지 않고 알림·자동 해소, "
      "시간 재등록은 무응답 추적·고정 알림 id 를 보존")
