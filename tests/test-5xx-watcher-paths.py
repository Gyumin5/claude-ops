"""5xx watcher 경로 해석·침묵실패 제거 검증.

1) 계산 규칙이 실제 프로젝트 디렉토리 전부와 일치하는가
2) 계산이 빗나가는 인코딩에서도 역조회로 찾아내는가(+경고)
3) jsonl 을 못 찾으면 조용히 넘기지 않고 경고를 내는가
4) job-* / 템플릿 인스턴스가 세션 목록에서 빠지는가
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stderr
from importlib.machinery import SourceFileLoader
from pathlib import Path

# 저장소 뿌리를 이 파일 위치에서 찾는다. 예전엔 홈 아래 고정 경로였는데,
# 그러면 다른 자리에 받은 사람은 그대로 못 돌린다.
REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

w = SourceFileLoader("w", os.path.join(REPO, "bin/claude-api-5xx-watcher")).load_module()
real_projects = w.PROJECTS_DIR
fails = []
skipped = []   # 안 돈 검사는 PASS 안에 숨기지 않는다

# 1) 실제 디렉토리 전수 대조 — jsonl 이 스스로 기록한 cwd 가 정답이다.
checked = 0
for d in sorted(real_projects.iterdir()):
    if not d.is_dir():
        continue
    cwd = w._recorded_cwd(d)
    if not cwd:
        continue
    checked += 1
    got = w.project_dir(cwd)
    if got != d:
        fails.append("전수대조 불일치: cwd=%s 실제=%s 계산=%s" % (cwd, d.name, got))
print("1) 실제 디렉토리 대조: %d개 검사, 불일치 %d"
      % (checked, sum(1 for f in fails if "전수대조" in f)))

# 2) 계산이 빗나가는 인코딩 → 역조회로 복구되는가.
tmp = Path(tempfile.mkdtemp(prefix="t5xx."))
odd = tmp / "완전히~다른=이름"           # 어떤 치환 규칙으로도 안 나오는 이름
odd.mkdir()
target_cwd = "/home/user/some_project_dir"
(odd / "a.jsonl").write_text(json.dumps({"cwd": target_cwd, "type": "user"}) + "\n")
w.PROJECTS_DIR = tmp
w._DIR_CACHE.clear()
buf = io.StringIO()
with redirect_stderr(buf):
    got = w.project_dir(target_cwd)
warned = "역조회" in buf.getvalue()
print("2) 역조회 폴백: dir=%s, 경고=%s" % (got.name if got else None, warned))
if got != odd:
    fails.append("역조회 실패: %s" % got)
if not warned:
    fails.append("역조회했는데 경고가 없다")

# 3) 아예 없는 cwd → process() 가 조용히 넘어가지 않는가.
w._DIR_CACHE.clear()
w.STATE_DIR = tmp / "state"
w.unit_props = lambda proj: ("/nonexistent/path/xyz", False)
buf = io.StringIO()
with redirect_stderr(buf):
    w.process("ghost")
msg = buf.getvalue().strip()
print("3) 미발견 세션 경고: %r" % msg[:80])
if "감시되지 않는다" not in msg:
    fails.append("jsonl 미발견인데 경고가 없다(침묵 실패 그대로)")

# 4) 같은 경고가 1시간 안에 반복되지 않는가(저널 폭주 방지).
buf2 = io.StringIO()
with redirect_stderr(buf2):
    w.process("ghost")
print("4) 같은 경고 재발: %r" % buf2.getvalue().strip()[:40])
if buf2.getvalue().strip():
    fails.append("경고 rate-limit 이 안 걸린다")

w.PROJECTS_DIR = real_projects

# 5) SKIP 이 실제 세션 이름과 겹치면 그 세션은 영구 사각지대가 된다.
#    "resume" 가 도입부터 2026-08-01 까지 그 상태였다. 이름 목록으로 거르는 한
#    같은 사고가 또 나므로 대조를 검사로 남긴다.
targets = Path.home() / ".claude" / "userbot" / "targets.json"
if targets.is_file():
    sessions = set(json.loads(targets.read_text()).keys())
    clash = sorted(sessions & w.SKIP)
    print("5) SKIP ∩ 세션이름: %s" % (clash or "없음"))
    if clash:
        fails.append("SKIP 이 실제 세션을 가린다: %s" % ", ".join(clash))
else:
    print("5) SKIP — targets.json 없음(이 호스트엔 없다). SKIP 집합 대조 안 함")
    skipped.append("SKIP ∩ targets.json")

# 6) 같은 폴더의 `claude -p` 일회성 transcript 가 세션 것보다 최신이어도
#    세션 transcript 를 골라야 한다. 세션이 5xx 로 멈추면 세션 파일은 더 이상
#    자라지 않아 일회성 파일이 영구히 최신이 된다 — 감시가 필요한 바로 그 순간이다.
tmp6 = Path(tempfile.mkdtemp(prefix="t5xx.kind."))
proj6 = tmp6 / "-home-user-mix"
proj6.mkdir()
real6 = proj6 / "session.jsonl"
real6.write_text(json.dumps(
    {"type": "user", "entrypoint": "cli", "isSidechain": False,
     "cwd": "/home/user/mix"}) + "\n")
os.utime(real6, (1000, 1000))
for i, name in enumerate(("updater1.jsonl", "updater2.jsonl")):
    p = proj6 / name
    p.write_text(json.dumps(
        {"type": "user", "entrypoint": "sdk-cli", "isSidechain": False,
         "cwd": "/home/user/mix"}) + "\n")
    os.utime(p, (2000 + i, 2000 + i))      # 세션 파일보다 최신
w.PROJECTS_DIR = tmp6
w._DIR_CACHE.clear()
got6 = w.latest_jsonl("/home/user/mix")
print("6) 일회성 transcript 배제: %s" % (got6.name if got6 else None))
if got6 != real6:
    fails.append("최신 mtime 인 sdk-cli 파일을 골랐다: %s" % got6)

# 6b) 대화형 transcript 가 아예 없으면 None → 호출부가 '감시되지 않는다' 경고.
real6.unlink()
w._DIR_CACHE.clear()
got6b = w.latest_jsonl("/home/user/mix")
print("6b) 대화형 없음 → %s" % got6b)
if got6b is not None:
    fails.append("sdk-cli 뿐인데 조용히 그걸 골랐다: %s" % got6b)

w.PROJECTS_DIR = real_projects

print()
if fails:
    for f in fails:
        print("FAIL:", f)
    sys.exit(1)
tail = "  (건너뜀: %s)" % ", ".join(skipped) if skipped else ""
print("PASS — 전수대조 일치, 역조회 폴백 동작, 침묵실패 제거, 경고 rate-limit 동작, "
      "일회성 transcript 배제%s%s"
      % ("" if skipped else ", SKIP 충돌 없음", tail))
