"""감시기 3종이 같은 세션에 대해 같은 transcript 를 고르는가.

규칙이 세 벌로 복제돼 있던 게 2026-08-03 사고의 원인이었다 — 5xx watcher 에서
고친 결함이 stall-guard 에 그대로 남아 두 번 고쳐야 했다. 이제 규칙은
lib/claude_session_lib.py 한 곳이고, 이 테스트는 셋이 다시 갈라지는 걸 막는다.

1) 합성 디렉토리: 일회성(sdk-cli)이 더 최신이어도 세션(cli)을 골라야 한다
2) 합성 디렉토리: 대화형이 하나도 없으면 폴백하며 반드시 알린다
3) 실제 활성 세션: 5xx watcher / stall-guard / probe 가 같은 파일을 가리킨다
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

# 저장소 뿌리를 이 파일 위치에서 찾는다. 예전엔 홈 아래 고정 경로였는데,
# 그러면 다른 자리에 받은 사람은 그대로 못 돌린다.
REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

sys.path.insert(0, os.path.join(REPO, "lib"))
import claude_session_lib as csl                      # noqa: E402

W = SourceFileLoader("w", os.path.join(REPO, "bin/claude-api-5xx-watcher")).load_module()
G = SourceFileLoader("g", os.path.join(REPO, "bin/claude-stall-guard")).load_module()
P = SourceFileLoader("p", os.path.join(REPO, "bin/claude-session-probe")).load_module()

fails = []
skipped = []

# ---- 1) 일회성이 더 최신이어도 세션을 고른다 ------------------------------
tmp = Path(tempfile.mkdtemp(prefix="tsel."))
pdir = tmp / "-home-user-mix"
pdir.mkdir(parents=True)


def write(name, entrypoint, mtime):
    f = pdir / name
    f.write_text(json.dumps({"type": "user", "entrypoint": entrypoint,
                             "cwd": "/home/user/mix"}) + "\n")
    os.utime(f, (mtime, mtime))
    return f


now = time.time()
write("session.jsonl", "cli", now - 100)
write("updater.jsonl", "sdk-cli", now)          # 더 최신이지만 남의 파일
got = csl.session_transcript(str(pdir))
print("1) 최신 sdk-cli 배제: %s" % os.path.basename(got or "None"))
if os.path.basename(got or "") != "session.jsonl":
    fails.append("일회성 transcript 를 골랐다: %s" % got)

# ---- 2) 대화형이 없으면 폴백 + 반드시 알린다 ------------------------------
pdir2 = tmp / "-home-user-old"
pdir2.mkdir()
f = pdir2 / "legacy.jsonl"
f.write_text(json.dumps({"type": "user", "cwd": "/home/user/old"}) + "\n")
seen = []
got2 = csl.session_transcript(str(pdir2), on_fallback=seen.append)
print("2) entrypoint 없는 옛 파일: 폴백=%s 알림=%s"
      % (os.path.basename(got2 or "None"), bool(seen)))
if os.path.basename(got2 or "") != "legacy.jsonl":
    fails.append("옛 transcript 를 배제해 사각지대를 만들었다")
if not seen:
    fails.append("폴백했는데 조용하다 — 규칙이 바뀐 걸 알 수 없다")

# 대화형도 옛 파일도 없으면 None. sidechain 도 세션 transcript 가 아니다.
pdir3 = tmp / "-home-user-sdkonly"
pdir3.mkdir()
(pdir3 / "u.jsonl").write_text(json.dumps({"type": "user", "entrypoint": "sdk-cli"}) + "\n")
(pdir3 / "s.jsonl").write_text(json.dumps({"type": "user", "isSidechain": True,
                                           "entrypoint": "cli"}) + "\n")
if csl.session_transcript(str(pdir3)) is not None:
    fails.append("sdk-cli·sidechain 뿐인데 뭔가를 골랐다")

# 깨진 입력에 죽지 않는다: 객체가 아닌 JSON 라인, *.jsonl 이름의 디렉토리
pdir4 = tmp / "-home-user-junk"
pdir4.mkdir()
(pdir4 / "dir.jsonl").mkdir()
(pdir4 / "bad.jsonl").write_text('["cwd", 1]\n"just a string"\nnot json at all\n'
                                 + json.dumps({"cwd": "/home/user/junk",
                                               "entrypoint": "cli"}) + "\n")
try:
    got4 = csl.session_transcript(str(pdir4))
    cwd4 = csl.recorded_cwd(str(pdir4))
    print("4) 깨진 입력: 선택=%s cwd=%s" % (os.path.basename(got4 or "None"), cwd4))
    if os.path.basename(got4 or "") != "bad.jsonl" or cwd4 != "/home/user/junk":
        fails.append("깨진 라인 때문에 뒤의 정상 레코드를 놓쳤다: %s / %s" % (got4, cwd4))
except Exception as e:
    fails.append("깨진 입력에 예외로 죽었다: %r" % (e,))

# ---- 3) 실제 활성 세션에서 3종이 일치하는가 -------------------------------
out = subprocess.run(["systemctl", "--user", "list-units", "claude-*.service",
                      "--state=active", "--no-legend", "--plain"],
                     capture_output=True, text=True).stdout
names = []
for ln in out.splitlines():
    u = ln.split()[0] if ln.split() else ""
    if not u.startswith("claude-") or not u.endswith(".service"):
        continue
    n = u[len("claude-"):-len(".service")]
    if n.startswith("job-") or n.startswith("session-nudge@") or n in G.NON_SESSION:
        continue
    names.append(n)

if not names:
    print("3) SKIP — 활성 세션 없음")
    skipped.append("실세션 3종 대조")
for n in names:
    wd = subprocess.run(["systemctl", "--user", "show", "claude-%s.service" % n,
                         "-p", "WorkingDirectory", "--value"],
                        capture_output=True, text=True).stdout.strip().lstrip("!")
    if not wd:
        skipped.append("%s(WorkingDirectory 없음)" % n)
        continue
    a = W.latest_jsonl(wd)
    pd = G.project_dir(wd)
    b = G.newest_transcript(pd) if pd else None
    c = json.loads(subprocess.run([str(Path(os.path.join(REPO, "bin/claude-session-probe"))), n],
                                  capture_output=True, text=True).stdout or "{}").get("transcript")
    same = (str(a) if a else None) == (str(b) if b else None) == (str(c) if c else None)
    print("3) %-12s %-40s 일치=%s" % (n, os.path.basename(str(a)), same))
    if not same:
        fails.append("%s: watcher=%s guard=%s probe=%s" % (n, a, b, c))

print()
if fails:
    for f_ in fails:
        print("FAIL:", f_)
    sys.exit(1)
tail = "  (건너뜀: %s)" % ", ".join(skipped) if skipped else ""
print("PASS — 일회성 배제, 옛 파일 폴백+알림, 감시기 3종 선택 일치%s" % tail)
