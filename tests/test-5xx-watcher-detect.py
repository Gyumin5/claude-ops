"""5xx 감지 판정 검증 — 대조군에 '5xx 뒤 도구 호출만' 케이스를 포함한다.

이번 결함이 통과한 이유가 바로 그 형태를 대조군이 안 덮어서였다.
process() 를 실제로 돌리되 재주입은 절대 못 하게 do_resend/notify 를 막고,
상태 변화(active/attempts/gave_up)와 재주입 호출 여부로 판정을 본다.
"""
import os
import json
import sys
import tempfile
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

# 저장소 뿌리를 이 파일 위치에서 찾는다. 예전엔 홈 아래 고정 경로였는데,
# 그러면 다른 자리에 받은 사람은 그대로 못 돌린다.
REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

w = SourceFileLoader("w", os.path.join(REPO, "bin/claude-api-5xx-watcher")).load_module()

ERR500 = "API Error: 500 Internal server error. This is a server-side issue."
resends = []
w.do_resend = lambda username: (resends.append(username), (True, "stub"))[1]
w.notify = lambda tok, text: None
w.load_bot_token = lambda cwd: "tok"
w.bot_username = lambda tok: "bot"


def rec(kind, ts, text=None, tool=False):
    # entrypoint 는 대화형 세션 transcript 임을 나타낸다. 없으면 latest_jsonl 이
    # 폴백 경로(경고 후 사용)로 빠져 이 테스트가 검증하려는 경로와 달라진다.
    d = {"type": kind, "timestamp": ts, "entrypoint": "cli"}
    if kind == "assistant":
        content = []
        if text is not None:
            content.append({"type": "text", "text": text})
        if tool:
            content.append({"type": "tool_use", "name": "Bash", "input": {}})
        d["message"] = {"content": content}
    else:
        d["message"] = {"role": "user", "content": text or ""}
    return d


def iso(offset_sec):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z",
                         time.gmtime(time.time() + offset_sec))


def run_case(name, records, expect_active, expect_resend):
    tmp = Path(tempfile.mkdtemp(prefix="t5xxd."))
    proj_dir = tmp / "projects" / "-fake-cwd"
    proj_dir.mkdir(parents=True)
    with (proj_dir / "s.jsonl").open("w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    w.PROJECTS_DIR = tmp / "projects"
    w.STATE_DIR = tmp / "state"
    w._DIR_CACHE.clear()
    # process() 는 unit_props 로 (cwd, transient) 를 받는다. 여기를 스텁한다.
    w.unit_props = lambda proj: ("/fake/cwd", False)
    resends.clear()
    w.process("t")
    st = w.load_state("t")
    ok = (bool(st.get("active")) == expect_active) and (bool(resends) == expect_resend)
    print("%-46s active=%-5s resend=%-5s → %s"
          % (name, bool(st.get("active")), bool(resends), "OK" if ok else "FAIL"))
    return ok

# 1) 5xx 로 끝나고 그 뒤 아무것도 없음(신선) → 감지 O. 첫 틱은 백오프 대기라 재주입 X.
c1 = run_case("5xx 직후, 뒤에 아무것도 없음",
              [rec("user", iso(-120), "해줘"),
               rec("assistant", iso(-60), ERR500)],
              expect_active=True, expect_resend=False)

# 2) ★이번 결함: 5xx 뒤에 도구 전용 assistant 레코드가 이어짐 → 진행 중이므로 감지 X.
c2 = run_case("5xx 뒤 도구 호출만 이어짐(진행 중)",
              [rec("user", iso(-600), "해줘"),
               rec("assistant", iso(-500), ERR500),
               rec("assistant", iso(-100), None, tool=True),
               rec("user", iso(-90), "tool result"),
               rec("assistant", iso(-30), None, tool=True)],
              expect_active=False, expect_resend=False)

# 3) 5xx 뒤 정상 텍스트 응답 → 복구.
c3 = run_case("5xx 뒤 정상 텍스트 응답(복구)",
              [rec("assistant", iso(-300), ERR500),
               rec("assistant", iso(-30), "다 했다")],
              expect_active=False, expect_resend=False)

# 4) 5xx 가 STALE_SEC 초과 + 뒤에 아무것도 없음 → 포기 기록만, 재주입 금지.
c4 = run_case("오래된 5xx(>10분), 뒤에 아무것도 없음",
              [rec("assistant", iso(-3600), ERR500)],
              expect_active=True, expect_resend=False)

# 5) 감지만 되고 끝나면 의미가 없다 — 백오프가 지나면 실제로 재주입되는가.
#    (1~4 는 "안 쏜다"만 증명한다. 정상 경로가 살아있는지도 봐야 한다.)
tmp5 = Path(tempfile.mkdtemp(prefix="t5xxd5."))
pd5 = tmp5 / "projects" / "-fake-cwd"
pd5.mkdir(parents=True)
with (pd5 / "s.jsonl").open("w") as fh:
    fh.write(json.dumps(rec("user", iso(-200), "해줘")) + "\n")
    fh.write(json.dumps(rec("assistant", iso(-120), ERR500)) + "\n")
w.PROJECTS_DIR = tmp5 / "projects"
w.STATE_DIR = tmp5 / "state"
w._DIR_CACHE.clear()
resends.clear()
w.process("t")                                    # 1틱: incident 등록, 대기
first = w.load_state("t")
seeded = dict(first)
seeded["first_seen_ts"] = time.time() - (w.BACKOFF[0] + 5)   # 백오프 경과시킴
w.save_state("t", seeded)
w.process("t")                                    # 2틱: 재주입 기대
st5 = w.load_state("t")
c5 = bool(resends) and st5.get("attempts") == 1
print("%-46s active=%-5s resend=%-5s attempts=%s → %s"
      % ("백오프 경과 후 실제 재주입", bool(st5.get("active")), bool(resends),
         st5.get("attempts"), "OK" if c5 else "FAIL"))

print()
if all([c1, c2, c3, c4, c5]):
    print("PASS — 도구 연속 구간 진행중 인식, 오탐 재주입 0, 정상 재주입 경로 생존")
else:
    print("FAIL")
    sys.exit(1)
