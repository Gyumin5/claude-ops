"""claude-session-nudge 기본 문구와 등록분 갱신 방식 검증.

  1) 기본 문구의 자원 조항이 확인 가능한 조건이다 — 무엇으로 재고(uptime),
     어느 선이 낮은 것이고(코어 수 절반), 그때 무엇을 해야 회차를 끝낼 수 있는지
     (잡 하나 이상)가 문장 안에 있다. 권고형으로 되돌아가면 여기서 깨진다.
  2) 문구는 짧게 유지한다(운영자가 777자를 길다고 했다 — 상한 500자).
  3) 기본값으로 등록하면 message_from_default 가 참, --message 로 주면 거짓.
  4) --refresh-defaults 는 기본값으로 등록된 문구만 지금 기본값으로 바꾼다.
  5) --refresh-defaults 는 맞춤 문구를 덮지 않는다(간격은 맞춘다).
  6) 그 표시가 없는 옛 파일은 기본값으로 보고 갱신한다.
  7) --message 없이 시간만 다시 등록해도 맞춤 문구가 안 지워진다(기본값 등록분은 갱신).
  8) 갱신이 무응답 추적 필드를 보존한다.
  9) 맞춤 문구를 건너뛰었으면 끝에 경고가 한 줄 뜬다.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NUDGE = str(REPO / "bin" / "claude-session-nudge")
SRC = Path(NUDGE).read_text()
tmp = Path(tempfile.mkdtemp(prefix="tnudgemsg."))
fails = []


def default_msg():
    m = re.search(r'^DEFAULT_MSG="(.*)"$', SRC, re.M)
    assert m, "DEFAULT_MSG 상수를 못 찾았다"
    return m.group(1)


DEF = default_msg()

# 간격은 스크립트 상수에서 읽는다 — 숫자를 검사에 박아 두면 기본값을 바꿀 때마다
# 멀쩡한 동작이 실패로 뜬다(2026-10-14 60 → 30 에서 실제로 두 건 터졌다).
_m = re.search(r'^DEFAULT_INTERVAL=(\d+)$', SRC, re.M)
assert _m, "DEFAULT_INTERVAL 상수를 못 찾았다"
IVL = int(_m.group(1))

# --- 1) 자원 조항이 확인 가능한 조건인가 -----------------------------------
need = {
    "코어 수 측정": "nproc",
    "부하 측정": "uptime",
    "메모리 측정": "available",
    "낮다는 기준": "절반",
    "회차 종료 조건": "회차를 닫지 마라",
    "병렬 지시": "병렬",
    "회피 차단(사유 기록)": "태스크 목록에 적고",
    "정상 대기 예외": "승인 대기",
    "스왑 제외": "스왑",
}
missing = [name for name, token in need.items() if token not in DEF]
print("%-28s 빠진 요소=%s  %s" % ("1 조건 구성요소", missing or "없음",
                                  "OK" if not missing else "FAIL"))
if missing:
    fails.append("자원 조항에서 빠진 요소: %s" % ", ".join(missing))

# --- 1b) 답장은 안 해도 된다고 문구가 말한다 ---------------------------------
# 2026-10-16 운영자 지시. 주입은 15분 뒤 지워지지만 답장은 안
# 지워져서, 회차마다의 한 줄이 남는 쪽에만 쌓인다. 문구가 이 말을 안 들고 있으면 이미
# 등록된 세션은 옛 문구를 그대로 받아서 회차마다 답장을 쌓는다.
need_reply = {"주기 고지": "10분", "답장 면제": "답장은 안 해도"}
missing_r = [name for name, token in need_reply.items() if token not in DEF]
ok = not missing_r and "한 줄 답" not in DEF
print("%-28s 빠진 요소=%s  %s" % ("1b 답장 면제 고지", missing_r or "없음",
                                  "OK" if ok else "FAIL"))
if not ok:
    fails.append("문구가 답장 면제를 안 알린다: 빠짐=%s, 옛 요구문구=%s"
                 % (missing_r, "한 줄 답" in DEF))

# --- 2) 길이 상한 ----------------------------------------------------------
ok = len(DEF) <= 500
print("%-28s %d자 (상한 500)  %s" % ("2 문구 길이", len(DEF), "OK" if ok else "FAIL"))
if not ok:
    fails.append("기본 문구가 500자를 넘었다: %d자" % len(DEF))

# --- 공통: 상태 디렉토리를 갈아끼워 실제 스크립트를 돌린다 ------------------
state_dir = tmp / "state"
nudge_dir = state_dir / "session-nudge"
nudge_dir.mkdir(parents=True)
ENV = dict(os.environ,
           CLAUDE_NUDGE_STATE_DIR=str(state_dir),
           CLAUDE_NUDGE_TG_ENV=str(tmp / "no-such.env"),
           CLAUDE_NUDGE_LOG=str(tmp / "nudge.log"),
           CLAUDE_NUDGE_TICKLOG=str(tmp / "ticks.log"),
           # systemctl 호출이 이 테스트에서 실제 유닛을 건드리지 않게 한다.
           PATH=str(tmp / "bin") + ":" + os.environ["PATH"])
(tmp / "bin").mkdir()
fake_systemctl = tmp / "bin" / "systemctl"
fake_systemctl.write_text("#!/bin/bash\nexit 0\n")
fake_systemctl.chmod(0o755)


def run(*args):
    return subprocess.run([NUDGE, *args], capture_output=True, text=True, env=ENV)


def state(session):
    return json.loads((nudge_dir / (session + ".json")).read_text())


# --- 3) 등록 시 출처 표시 --------------------------------------------------
run("--start", "--sessions", "plain", "--hours", "6")
run("--start", "--sessions", "custom", "--hours", "6", "--message", "내 맞춤 문구")
a, b = state("plain"), state("custom")
ok = a.get("message_from_default") is True and b.get("message_from_default") is False
print("%-28s 기본=%s 맞춤=%s  %s" % ("3 출처 표시",
                                     a.get("message_from_default"),
                                     b.get("message_from_default"),
                                     "OK" if ok else "FAIL"))
if not ok:
    fails.append("message_from_default 가 등록 방식을 반영하지 않는다: %r / %r"
                 % (a.get("message_from_default"), b.get("message_from_default")))

# --- 4·5) 갱신이 기본값만 바꾸고 맞춤값은 건너뛴다 --------------------------
for s in ("plain", "custom"):
    d = state(s)
    d["message"] = "옛 문구 " + s
    d["interval_min"] = 30
    (nudge_dir / (s + ".json")).write_text(json.dumps(d, ensure_ascii=False))

# 추적 필드가 갱신을 넘어 살아남는지도 같이 본다 — 잃으면 고정된 알림을 못 푼다.
d = state("plain")
d["alert_msg_id"] = 7
d["no_response"] = 1
(nudge_dir / "plain.json").write_text(json.dumps(d, ensure_ascii=False))

r = run("--refresh-defaults")
a, b = state("plain"), state("custom")
ok = a["message"] == DEF and a["interval_min"] == IVL
print("%-28s 문구일치=%s 간격=%s  %s" % ("4 기본값 등록분 갱신",
                                         a["message"] == DEF, a["interval_min"],
                                         "OK" if ok else "FAIL"))
if not ok:
    fails.append("기본값으로 등록된 문구가 안 바뀌었다: rc=%s out=%r"
                 % (r.returncode, r.stdout[-200:]))

ok = b["message"] == "옛 문구 custom" and b["interval_min"] == IVL
print("%-28s 문구보존=%s 간격=%s  %s" % ("5 맞춤 문구 보존",
                                         b["message"] == "옛 문구 custom",
                                         b["interval_min"], "OK" if ok else "FAIL"))
if not ok:
    fails.append("맞춤 문구를 덮었거나 간격을 안 맞췄다: %r" % b)

# --- 6) 표시가 없는 옛 파일은 기본값으로 본다 -------------------------------
legacy = {"session": "legacy", "interval_min": 60, "end_ts": 4102444800,
          "message": "표시가 없던 시절의 옛 기본 문구", "enabled": True}
(nudge_dir / "legacy.json").write_text(json.dumps(legacy, ensure_ascii=False))
run("--refresh-defaults")
c = state("legacy")
ok = c["message"] == DEF and c.get("message_from_default") is True
print("%-28s 문구일치=%s 표시=%s  %s" % ("6 옛 파일 갱신", c["message"] == DEF,
                                         c.get("message_from_default"),
                                         "OK" if ok else "FAIL"))
if not ok:
    fails.append("표시 없는 옛 파일이 안 갱신됐다: %r" % c)

# --- 7) --message 없는 재등록이 맞춤 문구를 지우지 않는다 -------------------
# 컨트롤봇의 시간 연장 경로가 이 모양이다. refresh 만 맞춤값을 지키고 재등록이
# 지우면 결국 맞춤 문구는 사라진다.
run("--start", "--sessions", "custom", "--hours", "9")
b = state("custom")
ok = b["message"] == "옛 문구 custom" and b.get("message_from_default") is False
print("%-28s 문구보존=%s 표시=%s  %s" % ("7 재등록 맞춤 보존", b["message"] == "옛 문구 custom",
                                         b.get("message_from_default"),
                                         "OK" if ok else "FAIL"))
if not ok:
    fails.append("--message 없는 재등록이 맞춤 문구를 덮었다: %r" % b)

# 기본값으로 등록된 세션은 재등록 때 지금 기본값을 받아야 한다.
d = state("plain")
d["message"] = "옛 기본 문구"
(nudge_dir / "plain.json").write_text(json.dumps(d, ensure_ascii=False))
run("--start", "--sessions", "plain", "--hours", "9")
a = state("plain")
ok = a["message"] == DEF and a.get("message_from_default") is True
print("%-28s 문구일치=%s  %s" % ("7b 재등록 기본값 갱신", a["message"] == DEF,
                                 "OK" if ok else "FAIL"))
if not ok:
    fails.append("기본값 등록분 재등록이 지금 기본값을 안 받았다: %r" % a["message"][:40])

# --- 8) 갱신이 추적 필드를 보존한다 ----------------------------------------
ok = a.get("alert_msg_id") == 7 and a.get("no_response") == 1
print("%-28s 알림id=%s 무응답=%s  %s" % ("8 추적 필드 보존", a.get("alert_msg_id"),
                                         a.get("no_response"), "OK" if ok else "FAIL"))
if not ok:
    fails.append("갱신·재등록이 무응답 추적 필드를 지웠다: %r" % a)

# --- 9) 맞춤 문구를 건너뛰면 경고가 뜬다 ------------------------------------
r = run("--refresh-defaults")
ok = "맞춤 문구라 새 기본 조항이 안 들어간 세션" in r.stderr and "custom" in r.stderr
print("%-28s 경고=%s  %s" % ("9 건너뜀 경고", ok, "OK" if ok else "FAIL"))
if not ok:
    fails.append("맞춤 문구를 건너뛰고도 경고가 없다: rc=%s err=%r" % (r.returncode, r.stderr[-200:]))

print()
if fails:
    for f in fails:
        print("FAIL:", f)
    sys.exit(1)
print("PASS — 자원 조항이 측정수단·임계·종료조건·회피 차단·예외를 갖추고, 문구 길이 "
      "상한을 지키며, 등록 출처를 표시하고, 갱신과 재등록 둘 다 맞춤 문구를 보존하고 "
      "기본값 등록분만 바꾸며, 추적 필드를 잃지 않고, 건너뛴 세션은 경고로 올라간다")
