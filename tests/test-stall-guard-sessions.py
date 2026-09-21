"""stall-guard 세션 열거 검증 — 조용히 빠지는 세션이 없는가.

1) CLAUDE_SESSION_NAME 이 없는 세션 유닛은 목록에서 빠지되 로그를 남기는가
   (peer session-a 가 도입 이후 내내 감시 밖이었고 아무도 몰랐다 — 2026-08-03)
2) 같은 사유가 5분마다 반복되지 않는가(로그 폭주 방지)
3) 일회성 유닛(claude-job-*, Transient=yes)과 비세션 유닛은 조용히 빠지는가
4) NON_SESSION 이 실제 세션 이름과 겹치지 않는가(겹치면 영구 사각지대)
"""
import os
import json
import sys
import tempfile
from importlib.machinery import SourceFileLoader
from pathlib import Path

# 저장소 뿌리를 이 파일 위치에서 찾는다. 예전엔 홈 아래 고정 경로였는데,
# 그러면 다른 자리에 받은 사람은 그대로 못 돌린다.
REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

g = SourceFileLoader("g", os.path.join(REPO, "bin/claude-stall-guard")).load_module()
fails = []
skipped = []   # 안 돈 검사는 PASS 안에 숨기지 않는다 — 그게 우리가 잡고 있는 침묵 패턴이다

tmp = Path(tempfile.mkdtemp(prefix="tsg."))
g.STATE_DIR = str(tmp)
g.LOG_FILE = str(tmp / "stall-guard.log")
g._ONCE_FILE = str(tmp / "once.json")
g.PROJECTS = str(tmp / "projects")
(tmp / "projects" / "-home-user-session-a").mkdir(parents=True)
(tmp / "projects" / "-home-user-ok").mkdir(parents=True)

UNITS = "\n".join([
    "claude-session-a.service        loaded active running x",   # 이름 없음
    "claude-ok.service             loaded active running x",   # 정상
    "claude-job-bench-1.service    loaded active running x",   # 일회성
    "claude-control-bot.service    loaded active running x",   # 비세션
])
SHOW = {
    "claude-session-a.service": "WorkingDirectory=/home/user/session-a\nActiveState=active\n"
                              "Transient=no\nEnvironment=PATH=/usr/bin\n",
    "claude-ok.service": "WorkingDirectory=/home/user/ok\nActiveState=active\n"
                         "Transient=no\nEnvironment=PATH=/usr/bin CLAUDE_SESSION_NAME=ok\n",
    "claude-job-bench-1.service": "WorkingDirectory=/home/user/session-a\nActiveState=active\n"
                                  "Transient=yes\nEnvironment=PATH=/usr/bin\n",
    "claude-control-bot.service": "WorkingDirectory=/home/user\nActiveState=active\n"
                                  "Transient=no\nEnvironment=PATH=/usr/bin\n",
}


class Res:
    def __init__(self, out):
        self.stdout = out
        self.returncode = 0


def fake_run(cmd, **kw):
    if "list-units" in cmd:
        return Res(UNITS)
    if "show" in cmd:
        return Res(SHOW.get(cmd[3], ""))
    return Res("")


g.subprocess.run = fake_run

names = [n for n, _ in g.live_sessions()]
print("1) 목록: %s" % names)
if names != ["ok"]:
    fails.append("목록이 예상과 다르다: %s" % names)

logtxt = Path(g.LOG_FILE).read_text() if Path(g.LOG_FILE).is_file() else ""
print("2) 로그: %r" % logtxt.strip()[:90])
if "no-session-name" not in logtxt or "session-a" not in logtxt:
    fails.append("이름 없는 세션이 조용히 빠졌다(로그 없음)")
if "job-bench" in logtxt or "control-bot" in logtxt:
    fails.append("일회성·비세션 유닛까지 로그를 남긴다(폭주)")

before = logtxt
g.live_sessions()
after = Path(g.LOG_FILE).read_text()
print("3) 재실행 후 로그 증가: %d바이트" % (len(after) - len(before)))
if after != before:
    fails.append("같은 사유가 반복 기록된다(rate-limit 미동작)")

targets = Path.home() / ".claude" / "userbot" / "targets.json"
if targets.is_file():
    clash = sorted(set(json.loads(targets.read_text()).keys()) & g.NON_SESSION)
    print("4) NON_SESSION ∩ 세션이름: %s" % (clash or "없음"))
    if clash:
        fails.append("NON_SESSION 이 실제 세션을 가린다: %s" % ", ".join(clash))
else:
    print("4) SKIP — targets.json 없음(이 호스트엔 없다). NON_SESSION 대조 안 함")
    skipped.append("NON_SESSION ∩ targets.json")

print()
if fails:
    for f in fails:
        print("FAIL:", f)
    sys.exit(1)
tail = "  (건너뜀: %s)" % ", ".join(skipped) if skipped else ""
print("PASS — 이름 없는 세션 노출, 일회성·비세션 조용히 제외, 로그 rate-limit 동작%s"
      % ("" if skipped else ", NON_SESSION 충돌 없음") + tail)
