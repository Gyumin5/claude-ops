"""claude-session-nudge 주입·무응답 상태기계 검증.

실제 전송·텔레그램 호출 없이 tick 한 회차씩 돌려 상태 전이를 본다. 시간이 지난 것은
상태파일의 시각 필드를 뒤로 돌려 흉내낸다(데몬이 진짜로 그 시간을 자는 걸 검사가
기다릴 수는 없다). 탐침 값은 가짜 스크립트가 낸다.

  1) 일하는 중이어도 회차마다 보낸다(유휴 조건이 없다)
  2) 보내면서 응답 대기 창을 연다
  3) 창이 열려 있는 동안에도 보내되 창은 새로 열지 않는다(무응답 시계가 계속 간다)
  4) 유효 대기가 임계를 넘고 응답이 없으면 무응답 1회 + 재전송
  5) 무응답이 상한(NO_RESP_MAX)에 닿으면 멈추고 알린다, 그 뒤로는 안 보낸다
  6) 세션이 다시 응답하면 자동 재개되고 계수가 0 으로 돌아간다
  7) 한도 구간은 대기 시계에서 빠진다(차단된 시간을 무응답으로 세지 않는다)
  8) 전송 실패는 창을 안 연다 — 말을 건 적이 없으므로 무응답으로 안 센다
  9) 도구가 짧게 열렸으면 건너뛰고, 오래 열렸으면 알린다
 10) 유닛 정지·기록 없음은 건너뛴다(유휴로 읽지 않는다)
 11) 시간을 새로 등록해도(--start 재호출) 추적 필드와 진행 중인 대기를 잃지 않는다
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

NUDGE = str(Path(__file__).resolve().parent.parent / "bin" / "claude-session-nudge")
SRC = Path(NUDGE).read_text()
SESSION = "probe-test"
tmp = Path(tempfile.mkdtemp(prefix="tnudge."))
fails = []


def const(name):
    import re
    m = re.search(r"^%s=(\d+)" % name, SRC, re.M)
    assert m, "%s 상수를 못 찾았다" % name
    return int(m.group(1))


# 탐침에 넣을 유휴 분. 주기 주입이라 이 값은 주입 여부를 가르지 않고 기록 칸에만 간다.
# 무응답 판정 시계와 섞이지 않게 응답 대기 임계보다 넉넉히 크게 둔다.
IDLE_MIN = 40
WAIT_MIN = const("RESPONSE_WAIT_MIN")
MAX = const("NO_RESP_MAX")

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

ENV = dict(os.environ,
           CLAUDE_NUDGE_STATE_DIR=str(state_dir),
           CLAUDE_NUDGE_SEND=str(send_sh),
           CLAUDE_NUDGE_PROBE=str(probe_sh),
           # 없는 경로 → tg() 가 네트워크를 건드리지 않는다.
           CLAUDE_NUDGE_TG_ENV=str(tmp / "no-such.env"),
           # 검사가 실제 운영 로그에 줄을 쌓지 않게 한다. 회차 기록도 같이 돌린다 —
           # 안 돌리면 가짜 회차가 운영 기록에 섞여 점검기가 거짓 이상을 집는다.
           CLAUDE_NUDGE_LOG=str(tmp / "nudge.log"),
           CLAUDE_NUDGE_TICKLOG=str(tmp / "ticks.log"))


def now():
    return int(time.time())


# 레코드 시각은 고정된 기준점에서 만든다. 호출할 때마다 현재 시각으로 만들면 같은
# "새 레코드 없음" 상태가 회차마다 1초씩 새 레코드로 보여서 검사가 거짓 통과한다.
T0 = now()


def reset_state(started_min_ago=300):
    """막 등록한 상태파일. 등록 시각은 충분히 과거로 둔다."""
    sfile.write_text(json.dumps({
        "session": SESSION, "interval_min": 10, "end_ts": 4102444800,
        "message": "테스트", "enabled": True,
        "started_ts": now() - started_min_ago * 60}, ensure_ascii=False))


def state():
    return json.loads(sfile.read_text())


def rewind(field, minutes):
    """그 시각 필드를 과거로 밀어 '시간이 흘렀다'를 만든다."""
    d = state()
    d[field] = int(d.get(field) or now()) - minutes * 60
    sfile.write_text(json.dumps(d, ensure_ascii=False))


def set_probe(last_min_ago, *, active=True, quota=10.0, quota7=2.0,
              obs_status="fresh", queue=0, flag=0, account="acct-1",
              pending=False, pending_age_min=0, transcript="/x.jsonl"):
    """last_min_ago = 마지막 assistant 레코드가 T0 기준 몇 분 전인가. None 이면 기록 없음."""
    probe_out.write_text(json.dumps({
        "session": SESSION, "unit_active": active, "quota_pct": quota,
        "queue_bytes": queue, "blocked_flag": flag, "account": account,
        "quota_obs": {"status": obs_status,
                      "five_hour": {"status": "fresh", "used": quota},
                      "seven_day": {"status": "fresh", "used": quota7}},
        "transcript": transcript,
        "last_assistant": 0 if last_min_ago is None else T0 - last_min_ago * 60,
        "pending_tool": pending,
        "pending_tool_since": T0 - pending_age_min * 60 if pending else 0,
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

# --- 1~6) 주입 → 대기 → 무응답 → 중단 → 복구 --------------------------------
reset_state()
set_probe(1)
tick("1  일하는 중에도 주입", "회차 주입", True)

reset_state()
set_probe(IDLE_MIN + 10)
tick("2  회차 주입", "회차 주입", True)
st = state()
if not st.get("awaiting"):
    fails.append("전송했는데 응답 대기 창이 안 열렸다: %r" % st)

# 레코드가 안 늘었다 = 같은 값을 그대로 다시 낸다. 창이 열려 있어도 회차는 나간다.
opened_at = int(state().get("await_since") or 0)
set_probe(IDLE_MIN + 10)
tick("3  창 열린 동안 재주입", "대기 중 재주입", True)
if int(state().get("await_since") or 0) != opened_at:
    fails.append("재주입이 응답 대기 창을 새로 열었다 — 무응답 계수가 상한에 못 닿는다")

rewind("await_since", WAIT_MIN + 1)
set_probe(IDLE_MIN + 10)
tick("4  무응답 1회", "무응답 1/%d" % MAX, True)

# 상한 직전까지는 같은 모양이 반복된다. 횟수를 상수에서 받아 돌린다 — 숫자를 박으면
# NO_RESP_MAX 를 바꿀 때 멀쩡한 동작이 실패로 뜬다.
for n in range(2, MAX):
    rewind("await_since", WAIT_MIN + 1)
    set_probe(IDLE_MIN + 10)
    tick("4+ 무응답 %d회" % n, "무응답 %d/%d" % (n, MAX), True)

rewind("await_since", WAIT_MIN + 1)
set_probe(IDLE_MIN + 10)
tick("5a 무응답 %d회" % MAX, "넛지 중단", False)
st = state()
print("   상태: suspended=%s no_response=%s" % (st.get("suspended"), st.get("no_response")))
if not st.get("suspended"):
    fails.append("상한 도달인데 suspended 가 아니다")
if st.get("awaiting"):
    fails.append("중단했는데 응답 대기 창이 열린 채다: %r" % st)

set_probe(IDLE_MIN + 10)
tick("5b 중단 후", "대기", False)

set_probe(0)                       # 사람이 승인 → 세션이 지금 응답했다
tick("6a 복구", "복구", False)
st = state()
print("   상태: suspended=%s no_response=%s" % (st.get("suspended"), st.get("no_response")))
if st.get("suspended") or int(st.get("no_response") or 0) != 0:
    fails.append("응답이 돌아왔는데 중단·계수가 안 풀렸다: %r" % st)
set_probe(0)
tick("6b 재개 직후", "회차 주입", True)

# --- 7) 한도 구간은 대기 시계에서 뺀다 --------------------------------------
reset_state()
set_probe(IDLE_MIN + 15)
tick("7a 주입", "회차 주입", True)
EXCL_MIN = 20                       # 한도로 막혀 있던 시간
rewind("await_since", WAIT_MIN + EXCL_MIN - 5)
set_probe(IDLE_MIN + 15, quota=95.0)
tick("7b 한도 진입", "quota-blocked", False)
if not state().get("excluded_since"):
    fails.append("한도 구간인데 대기 시계를 안 멈췄다: %r" % state())
rewind("excluded_since", EXCL_MIN)  # 한도로 그만큼을 보냈다
set_probe(IDLE_MIN + 15)
# 그중 한도 구간을 빼면 유효 대기가 임계보다 5분 모자라므로 아직 안 센다(보내긴 한다).
tick("7c 한도 해제", "대기 중 재주입", True)
st = state()
print("   상태: 제외누적=%s초 무응답=%s" % (st.get("await_excluded"), st.get("no_response")))
if int(st.get("no_response") or 0) != 0:
    fails.append("한도 구간을 무응답으로 셌다: %s" % st.get("no_response"))
if not (18 * 60 <= int(st.get("await_excluded") or 0) <= 22 * 60):
    fails.append("제외 구간 누적이 20분이 아니다: %s초" % st.get("await_excluded"))
rewind("await_since", 10)           # 유효 대기가 임계를 넘는다 → 이제 판정
set_probe(IDLE_MIN + 15)
tick("7d 유효 대기 초과", "무응답 1/%d" % MAX, True)

# --- 8) 전송 실패는 창을 안 연다 ---------------------------------------------
send_rc.write_text("1")
reset_state()
set_probe(IDLE_MIN + 10)
tick("8a 전송 실패", "전송 실패 rc=1", True)
st = state()
if st.get("awaiting") or int(st.get("no_response") or 0) != 0:
    fails.append("실패한 전송이 대기 창을 열었거나 계수를 올렸다: %r" % st)
set_probe(IDLE_MIN + 20)
tick("8b 다음 회차", "전송 실패 rc=1", True)
if int(state().get("no_response") or 0) != 0:
    fails.append("전송 실패 회차를 무응답으로 셌다: %s" % state().get("no_response"))
send_rc.write_text("0")
set_probe(IDLE_MIN + 30)
tick("8c 전송 복구", "회차 주입", True)

# --- 9) 도구가 열려 있다 -----------------------------------------------------
reset_state()
set_probe(IDLE_MIN + 10, pending=True, pending_age_min=2)
tick("9a 도구 2분", "tool-running", False)
set_probe(120, pending=True, pending_age_min=90)
tick("9b 도구 90분", "넛지 중단", False)
st = state()
print("   상태: suspended=%s" % st.get("suspended"))
if not st.get("suspended"):
    fails.append("도구가 90분 열렸는데 suspended 가 아니다")
set_probe(120, pending=True, pending_age_min=120)
tick("9c 계속 열림", "대기", False)
set_probe(0, pending=True, pending_age_min=1)   # 승인됨 → 다음 도구가 막 돌기 시작
tick("9d 승인 후 복구", "복구", False)
if state().get("suspended"):
    fails.append("도구가 진행됐는데 고정이 안 풀렸다")

# --- 10) 관측이 안 되는 회차 --------------------------------------------------
reset_state()
set_probe(IDLE_MIN + 10, active=False)
tick("10a 유닛 정지", "unit-not-active", False)
set_probe(None, transcript="")
tick("10b 기록 없음", "no-transcript", False)
if int(state().get("no_response") or 0) != 0:
    fails.append("관측 불가 회차를 무응답으로 셌다: %s" % state().get("no_response"))

# --- 11) 재등록이 추적 필드와 진행 중인 대기를 보존한다 -----------------------
# 컨트롤봇에서 시간을 늘리면 --start 가 다시 불린다. 그때 상태파일을 통째 새 dict 로
# 덮으면 alert_msg_id 를 잃어 고정된 텔레그램 알림을 아무도 못 푼다(clear_alert 가 그
# id 로만 푼다). 열려 있던 응답 대기 창도 같이 잃으면 추적이 처음부터 다시 시작한다.
fake_bin = tmp / "bin"
fake_bin.mkdir()
fake_systemctl = fake_bin / "systemctl"
fake_systemctl.write_text("#!/bin/bash\nexit 0\n")
fake_systemctl.chmod(0o755)
START_ENV = dict(ENV, PATH="%s:%s" % (fake_bin, os.environ["PATH"]))

st = state()
await_since = now() - 600
st.update({"suspended": True, "alert_msg_id": 424242, "no_response": 1,
           "last_assistant": 5000, "awaiting": True, "await_since": await_since,
           "await_base": 4000, "await_excluded": 120,
           "end_ts": now() + 60})
sfile.write_text(json.dumps(st, ensure_ascii=False))
r = subprocess.run([NUDGE, "--start", "--sessions", SESSION, "--hours", "6"],
                   capture_output=True, text=True, env=START_ENV)
st = state()
extended = st.get("end_ts", 0) > now() + 5 * 3600
ok = (r.returncode == 0 and extended and st.get("alert_msg_id") == 424242
      and st.get("suspended") is True and st.get("no_response") == 1
      and st.get("last_assistant") == 5000 and st.get("enabled") is True
      and st.get("awaiting") is True and st.get("await_since") == await_since
      and st.get("await_excluded") == 120)
print("%-28s rc=%s 종료시각갱신=%s 대기보존=%s  %s"
      % ("11a 재등록 보존", r.returncode, extended,
         (st.get("awaiting"), st.get("await_excluded")), "OK" if ok else "FAIL"))
if not ok:
    fails.append("재등록이 추적 필드나 진행 중인 대기를 지웠다: rc=%s %r"
                 % (r.returncode, st))

# 처음 켜는 경우엔 그 필드가 없어야 한다 — 없는 상태를 물려받은 것처럼 만들지 않는다.
sfile.unlink()
subprocess.run([NUDGE, "--start", "--sessions", SESSION, "--hours", "6"],
               capture_output=True, text=True, env=START_ENV)
st = state()
ok = ("suspended" not in st and "alert_msg_id" not in st and "awaiting" not in st
      and st.get("enabled") is True and st.get("started_ts", 0) >= now() - 60)
print("%-28s 키=%s  %s" % ("11b 새 등록", sorted(st.keys()), "OK" if ok else "FAIL"))
if not ok:
    fails.append("새 등록인데 추적 필드가 생겼거나 등록 시각이 없다: %r" % st)

# 새로 등록한 직후의 첫 회차. 기록이 비어 있으면 유휴를 등록 시각부터 센다 —
# "무한 유휴" 로 읽지 않는다. 주기 주입이므로 보내긴 보낸다.
set_probe(None)
line = tick("11c 등록 직후 첫 회차", "회차 주입", True)
if "마지막 응답 0분 전" not in line:
    fails.append("등록 직후인데 유휴를 등록 시각 기준으로 안 셌다: %r" % line)

# --- 12) 한 번 본 뒤의 관측 실패는 유휴가 아니다 -------------------------------
# 기록 파일은 있는데 그 안에서 응답 레코드를 못 찾는 경우(읽기 실패·꼬리 범위 밖)를
# 유휴로 읽으면 등록 시각부터 통째로 빈 것으로 세어 회차마다 주입이 꽂힌다.
# 마지막으로 믿을 수 있었던 관측은 0 으로 덮이면 안 된다.
reset_state()
set_probe(IDLE_MIN - 5)
tick("12a 응답 관측", "회차 주입", True)
seen = int(state().get("last_assistant") or 0)
set_probe(None)
tick("12b 관측 실패", "no-assistant-record", False)
kept = int(state().get("last_assistant") or 0)
if kept != seen or not seen:
    fails.append("관측 실패 회차가 마지막 관측을 덮었다: %s → %s" % (seen, kept))
if int(state().get("no_response") or 0) != 0:
    fails.append("관측 실패 회차를 무응답으로 셌다: %s" % state().get("no_response"))

# --- 13) 주입 메시지는 지연삭제를 달고 나간다 --------------------------------
# 넛지 문구는 480자가 넘어서, 안 지우면 회차마다 쌓여 그 봇 대화가 넛지로 덮인다.
# 세션은 이미 받은 뒤에 지워지므로 수신에는 영향이 없다. 값이 0 이거나 인자가 아예
# 빠지면 유저봇이 안 지운다 — 그래서 인자 자체를 검사한다.
import re as _re
m = _re.search(r'^NUDGE_DELETE_AFTER_SEC="\$\{CLAUDE_NUDGE_DELETE_AFTER_SEC:-(\d+)\}"',
               SRC, _re.M)
if not m:
    fails.append("NUDGE_DELETE_AFTER_SEC 기본값을 못 찾았다")
else:
    reset_state()
    set_probe(IDLE_MIN + 10)
    tick("13  지연삭제 인자", "회차 주입", True)
    last = send_log.read_text().strip().splitlines()[-1]
    want = "--delete-after %s" % m.group(1)
    ok = want in last
    print("%-28s %-46s %s" % ("13b 전송 인자", last[:46], "OK" if ok else "FAIL"))
    if not ok:
        fails.append("주입 전송에 %r 가 없다: %r" % (want, last))
    # 간격보다 짧아야 화면에 한 번에 한 건만 남는다. 0 이면 안 지운다.
    delay = int(m.group(1))
    if not 0 < delay < const("DEFAULT_INTERVAL") * 60:
        fails.append("삭제 지연(%ds)이 0 이거나 회차 간격 이상이다 — 화면에 두 건이 "
                     "겹치거나 아예 안 지워진다" % delay)

print()
if fails:
    for f in fails:
        print("FAIL:", f)
    sys.exit(1)
print("PASS — 회차마다 보내고 창이 열린 동안에도 보내되 창을 새로 열지 않으며, "
      "유효 대기 초과 시 재전송·상한 도달 시 중단·응답 시 자동 재개, 한도 구간은 대기에서 "
      "빠지고, 전송 실패·도구 실행·유닛 정지·기록 없음·관측 실패는 무응답으로 안 세며, "
      "재등록이 추적 필드와 진행 중인 대기를 보존한다")
