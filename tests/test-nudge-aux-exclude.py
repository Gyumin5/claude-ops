"""claude-session-nudge 한도 판정 세 층 — 큐·관측·차단 표식 보조.

계기는 2026-10-30 실측이다. 홈의 bias 세션이 04:20~09:10 한도로 막혀 있는 동안
넛지가 네 회차를 "한도 아님" 으로 읽고 무응답 1·2·3 을 세어 멈춰 세웠다(태그가
전부 send/send-awaiting/resend, quota-blocked 는 0건). 차단된 세션은 턴이 없어
사용률 캐시가 얼고, 아무도 말을 안 걸면 큐도 비어서, 가장 확실히 막힌 순간이
가장 깨끗해 보인다.

여기서 재는 것:
  1) 큐에 바이트가 남아 있으면 차단으로 보고 회차를 건너뛴다
  2) 계정이 맞고 두 창이 다 신선할 때 임계를 넘으면 차단, 아래면 평소대로 센다
  3) 창 하나가 신선하지 않으면 그 관측은 못 쓴다 — 바깥 status 가 fresh 여도 그렇다
  4) 관측을 못 쓰고 차단 표식이 유예 안이면 주입은 보내되 무응답은 안 센다
  5) 보조 제외가 이어지는 동안 유효 대기가 임계에 안 닿는다(여러 회차)
  6) 표식이 지워지거나 관측이 해제를 확정하면 보조 사건이 닫히고 다시 센다
  7) 표식이 상한보다 오래 남아 있으면 유예를 안 준다 — 안 지워진 표식이 감시를
     영원히 재우는 길을 끊는다
  8) 차단이 이어지며 표식이 다시 touch 돼도 사건 기한이 안 밀린다
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
NUDGE = str(REPO / "bin" / "claude-session-nudge")
SRC = Path(NUDGE).read_text()
SESSION = "aux-test"
tmp = Path(tempfile.mkdtemp(prefix="tnudgeaux."))
fails = []


def const(name):
    m = re.search(r"^%s=(\d+)" % name, SRC, re.M)
    assert m, "%s 상수를 못 찾았다" % name
    return int(m.group(1))


WAIT_MIN = const("RESPONSE_WAIT_MIN")
MAX = const("NO_RESP_MAX")
m = re.search(r"^AUX_MAX = (\d+) \* 3600", SRC, re.M)
assert m, "AUX_MAX 상수를 못 찾았다"
AUX_MAX_H = int(m.group(1))

probe_out = tmp / "probe.json"
send_log = tmp / "send.log"
probe_sh = tmp / "probe.sh"
probe_sh.write_text('#!/bin/bash\ncat "%s"\n' % probe_out)
send_sh = tmp / "send.sh"
send_sh.write_text('#!/bin/bash\necho "$@" >> "%s"\nexit 0\n' % send_log)
for p in (probe_sh, send_sh):
    p.chmod(p.stat().st_mode | stat.S_IEXEC)

state_dir = tmp / "state"
sfile = state_dir / "session-nudge" / (SESSION + ".json")
sfile.parent.mkdir(parents=True)

ENV = dict(os.environ,
           CLAUDE_NUDGE_STATE_DIR=str(state_dir),
           CLAUDE_NUDGE_SEND=str(send_sh),
           CLAUDE_NUDGE_PROBE=str(probe_sh),
           CLAUDE_NUDGE_TG_ENV=str(tmp / "no-such.env"),
           CLAUDE_NUDGE_LOG=str(tmp / "nudge.log"),
           CLAUDE_NUDGE_TICKLOG=str(tmp / "ticks.log"))

IDLE_MIN = 40


def now():
    return int(time.time())


T0 = now()


def reset_state():
    sfile.write_text(json.dumps({
        "session": SESSION, "interval_min": 10, "end_ts": 4102444800,
        "message": "테스트", "enabled": True,
        "started_ts": now() - 300 * 60}, ensure_ascii=False))


def state():
    return json.loads(sfile.read_text())


def rewind(field, minutes):
    d = state()
    d[field] = int(d.get(field) or now()) - minutes * 60
    sfile.write_text(json.dumps(d, ensure_ascii=False))


def advance(minutes):
    """회차 사이에 그만큼 시간이 흘렀다고 만든다.

    시각 필드를 한 칸만 뒤로 밀면 안 된다 — 멈춰 둔 구간의 시작까지 같이 밀어야
    "그 동안 막혀 있었다" 가 되고, 안 그러면 대기만 늘고 제외는 안 늘어 검사가
    실제와 다른 상황을 잰다.
    """
    d = state()
    for f in ("await_since", "excluded_since"):
        if d.get(f):
            d[f] = int(d[f]) - minutes * 60
    sfile.write_text(json.dumps(d, ensure_ascii=False))


def set_probe(last_min_ago, *, obs="fresh", w5="fresh", w7="fresh",
              used5=10.0, used7=2.0, queue=0, flag_age_min=None,
              account="acct-1"):
    """flag_age_min = 차단 표식이 몇 분 전에 찍혔나. None 이면 표식 없음."""
    probe_out.write_text(json.dumps({
        "session": SESSION, "unit_active": True, "quota_pct": used5,
        "queue_bytes": queue,
        "blocked_flag": 0 if flag_age_min is None else T0 - flag_age_min * 60,
        "account": account,
        "quota_obs": {"status": obs,
                      "five_hour": {"status": w5, "used": used5},
                      "seven_day": {"status": w7, "used": used7}},
        "transcript": "/x.jsonl",
        "last_assistant": T0 - last_min_ago * 60,
        "pending_tool": False, "pending_tool_since": 0,
        "pending_tool_name": "", "reason": ""}))


def tick(label, expect_in, expect_send):
    before = send_log.read_text().count("\n") if send_log.is_file() else 0
    r = subprocess.run([NUDGE, "--tick", SESSION], capture_output=True,
                       text=True, env=ENV)
    line = (r.stdout or "").strip()
    after = send_log.read_text().count("\n") if send_log.is_file() else 0
    sent = after > before
    ok = expect_in in line and sent == expect_send
    print("%-30s %-44s 전송=%s  %s"
          % (label, line[:44], sent, "OK" if ok else "FAIL"))
    if not ok:
        fails.append("%s: line=%r sent=%s (기대 %r/%s)"
                     % (label, line, sent, expect_in, expect_send))
    return line


# --- 1) 큐에 남은 바이트 = 가장 확실한 차단 --------------------------------
reset_state()
set_probe(IDLE_MIN, queue=512)
tick("1  큐에 바이트", "quota-blocked queue=512B", False)

# --- 2) 계정 검증된 두 창 관측이 정본 ---------------------------------------
reset_state()
set_probe(IDLE_MIN, used5=95.0)
tick("2a 5시간 창 초과", "quota-blocked 5h=95.0%", False)
reset_state()
set_probe(IDLE_MIN, used5=10.0, used7=99.5)
tick("2b 7일 창 초과", "quota-blocked", False)
reset_state()
set_probe(IDLE_MIN, used5=10.0, used7=2.0)
tick("2c 둘 다 아래", "회차 주입", True)

# --- 3) 창 하나가 신선하지 않으면 그 관측은 못 쓴다 --------------------------
# 바깥 status 가 fresh 여도 안쪽 창이 못 쓰는 값이면 모르는 것이다. 표식이 없으니
# 보조도 안 걸려서 평소대로 간다 — 여기서 재는 것은 "차단으로 단정하지 않는다" 다.
reset_state()
set_probe(IDLE_MIN, w7="expired", used5=95.0)
tick("3a 7일 창 만료", "회차 주입", True)
reset_state()
set_probe(IDLE_MIN, obs="other-account", used5=95.0)
tick("3b 계정 불일치", "회차 주입", True)

# --- 4) 관측 불명 + 표식 유예 안 → 보내되 안 센다 ---------------------------
reset_state()
set_probe(IDLE_MIN, obs="stale", flag_age_min=5)
tick("4a 주입(보조 제외)", "무응답 집계 보류", True)
st = state()
if not st.get("awaiting"):
    fails.append("보조 제외 회차가 응답 대기 창을 안 열었다: %r" % st)
if not (st.get("aux_event") or {}).get("until"):
    fails.append("보조 사건이 안 열렸다: %r" % st)

# --- 5) 회차가 이어져도, 창이 열린 뒤 판정 시간이 지나도 안 센다 --------------
# 10분 간격 회차를 다섯 번 돌린다(합 50분 > 판정 20분). 차단 구간이 대기에서 빠지므로
# 유효 대기가 임계에 안 닿는다 — 2026-10-30 bias 가 바로 여기서 셋을 채우고 멈췄다.
for i in range(1, 6):
    advance(10)
    set_probe(IDLE_MIN, obs="stale", flag_age_min=5 + i * 10)
    tick("5  이어지는 회차 %d" % i, "무응답 집계 보류", True)
if int(state().get("no_response") or 0) != 0:
    fails.append("보조 제외가 이어지는데 무응답이 올라갔다: %s"
                 % state().get("no_response"))

# --- 6) 해제되면 사건이 닫히고 다시 센다 --------------------------------------
# 관측이 돌아와 두 창 다 임계 아래 → 해제 확정. 멈춰 둔 구간이 닫히고 남은 유효
# 대기부터 다시 잰다. 그 길이가 임계를 넘으면 그때는 센다.
set_probe(IDLE_MIN, obs="fresh", flag_age_min=60)
tick("6a 관측 해제 확정", "대기 중 재주입", True)
st = state()
if st.get("aux_event"):
    fails.append("해제가 확정됐는데 보조 사건이 안 닫혔다: %r" % st.get("aux_event"))
advance(WAIT_MIN + 1)
set_probe(IDLE_MIN, obs="fresh")
tick("6b 해제 뒤엔 센다", "무응답 1/%d" % MAX, True)

# 표식이 지워져도 사건이 닫힌다.
reset_state()
set_probe(IDLE_MIN, obs="stale", flag_age_min=5)
tick("6c 보조 제외 재개", "무응답 집계 보류", True)
advance(10)
set_probe(IDLE_MIN, obs="stale", flag_age_min=None)
tick("6d 표식 사라짐", "대기 중 재주입", True)
if state().get("aux_event"):
    fails.append("표식이 없는데 보조 사건이 남았다: %r" % state().get("aux_event"))

# --- 7) 상한보다 오래 남은 표식엔 유예가 없다 ---------------------------------
reset_state()
set_probe(IDLE_MIN, obs="stale", flag_age_min=(AUX_MAX_H + 1) * 60)
tick("7a 오래된 표식", "회차 주입", True)
rewind("await_since", WAIT_MIN + 1)
set_probe(IDLE_MIN, obs="stale", flag_age_min=(AUX_MAX_H + 1) * 60)
tick("7b 그대로 센다", "무응답 1/%d" % MAX, True)

# --- 8) 표식이 다시 touch 돼도 사건 기한이 안 밀린다 --------------------------
# 복구 경로는 차단이 이어지는 동안 표식을 다시 찍는다. 그 시각을 사건 키로 삼으면
# 회차마다 사건이 새로 열려 상한이 이름뿐인 값이 된다.
reset_state()
set_probe(IDLE_MIN, obs="stale", flag_age_min=5)
tick("8a 사건 열림", "무응답 집계 보류", True)
until1 = int((state().get("aux_event") or {}).get("until") or 0)
for i in range(3):
    advance(10)
    set_probe(IDLE_MIN, obs="stale", flag_age_min=0)   # 방금 다시 touch 됐다
    tick("8b 다시 touch %d" % (i + 1), "무응답 집계 보류", True)
until2 = int((state().get("aux_event") or {}).get("until") or 0)
ok = until1 and until1 == until2
print("%-30s %s → %s  %s" % ("8c 기한 고정", until1, until2, "OK" if ok else "FAIL"))
if not ok:
    fails.append("표식을 다시 찍었더니 보조 사건 기한이 밀렸다: %s → %s"
                 % (until1, until2))

print()
if fails:
    for f in fails:
        print("FAIL:", f)
    sys.exit(1)
print("PASS — 큐·두 창 관측·차단 표식 세 층으로 가르고, 관측을 못 쓸 때만 표식을 "
      "보조로 써서 무응답 집계만 멈추되 주입은 보내며, 해제·표식 삭제·상한 초과에서 "
      "사건이 닫히고 다시 찍어도 기한이 안 밀린다")
