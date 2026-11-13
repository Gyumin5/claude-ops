#!/usr/bin/env python3
"""운영자 방 번호를 읽는 자리가 하나인지, 그리고 없을 때 거절하는지 본다.

왜 재나: 방 번호는 운영자를 가리키는 값이라 저장소에 박아 두지 않는다(R76).
서른 군데에 흩어져 있던 것을 설정 파일 하나로 뺐는데, 다음에 새 발송 자리를
만들면서 다시 숫자를 적으면 그 순간 규칙이 깨지고 아무도 모른다.

왜 기본값이 없어야 하나: 기본값이 있으면 설정을 안 만든 기계가 조용히 남의
방으로 보낸다. 알림이 안 가는 것은 화면에 안 보이므로, 안 가는 이유를
시끄럽게 남기는 쪽을 고른다.

이 파일에는 실제 방 번호를 안 적는다 — 적으면 그 값이 저장소에 다시 들어온다.
재는 값은 읽는 자리에서 가져오거나 지어낸다.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "lib"
SH = LIB / "operator_chat.sh"
PY = LIB / "operator_chat.py"

fails = []


def check(name, ok, detail=""):
    print("  {}  {}{}".format("PASS" if ok else "FAIL", name,
                              "  — {}".format(detail) if detail else ""))
    if not ok:
        fails.append(name)


def sh(script, env=None, cwd=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          env=e, cwd=cwd or str(REPO))


print("=== 읽는 자리가 있다 ===")
check("셸 쪽 정본이 있다", SH.is_file(), str(SH.relative_to(REPO)))
check("파이썬 쪽 정본이 있다", PY.is_file(), str(PY.relative_to(REPO)))

print("=== 설정에서 읽는다 ===")
with tempfile.TemporaryDirectory() as td:
    home = Path(td) / "home"
    (home / ".config" / "dotfiles-private").mkdir(parents=True)
    made_up = "1234509876"
    (home / ".config" / "dotfiles-private" / "telegram.json").write_text(
        json.dumps({"operator_chat_id": made_up}), encoding="utf-8")

    r = sh('source "{}"; operator_chat_id'.format(SH),
           env={"HOME": str(home), "CLAUDE_OPERATOR_CHAT_ID": ""})
    check("셸: 설정 파일에서 읽는다", r.stdout.strip() == made_up and r.returncode == 0,
          r.stdout.strip() or r.stderr.strip()[:60])

    # 설정 경로는 모듈을 들일 때 HOME 으로 풀리므로, 바꾼 HOME 을 들고 새 프로세스를 띄운다.
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys;sys.path.insert(0,{!r});import operator_chat;"
         "print(operator_chat.chat_id())".format(str(LIB))],
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(home), "CLAUDE_OPERATOR_CHAT_ID": ""})
    check("파이썬: 설정 파일에서 읽는다", r.stdout.strip() == made_up and r.returncode == 0,
          r.stdout.strip() or r.stderr.strip()[-60:])

    print("=== 환경변수가 설정을 이긴다 ===")
    other = "5555500000"
    r = sh('source "{}"; operator_chat_id'.format(SH),
           env={"HOME": str(home), "CLAUDE_OPERATOR_CHAT_ID": other})
    check("셸: 환경변수 우선", r.stdout.strip() == other, r.stdout.strip())

    r = subprocess.run(
        [sys.executable, "-c",
         "import sys;sys.path.insert(0,{!r});import operator_chat;"
         "print(operator_chat.chat_id())".format(str(LIB))],
        capture_output=True, text=True,
        env={**os.environ, "CLAUDE_OPERATOR_CHAT_ID": other})
    check("파이썬: 환경변수 우선", r.stdout.strip() == other, r.stdout.strip())

    print("=== 없으면 거절한다 ===")
    empty = Path(td) / "empty"
    empty.mkdir()
    r = sh('source "{}"; operator_chat_id'.format(SH),
           env={"HOME": str(empty), "CLAUDE_OPERATOR_CHAT_ID": ""})
    check("셸: 설정이 없으면 rc≠0", r.returncode != 0, "rc={}".format(r.returncode))
    check("셸: 고치는 법을 stderr 로 남긴다",
          "telegram.json" in r.stderr, r.stderr.strip().splitlines()[:1])
    check("셸: 아무 값도 안 찍는다", r.stdout.strip() == "", repr(r.stdout[:40]))

    r = subprocess.run(
        [sys.executable, "-c",
         "import sys;sys.path.insert(0,{!r});import operator_chat;"
         "operator_chat.CONFIG={!r};"
         "import os;os.environ.pop('CLAUDE_OPERATOR_CHAT_ID',None);"
         "print(operator_chat.chat_id())".format(str(LIB), str(empty / "없다.json"))],
        capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "CLAUDE_OPERATOR_CHAT_ID"})
    check("파이썬: 설정이 없으면 예외", r.returncode != 0, "rc={}".format(r.returncode))
    check("파이썬: 고치는 법이 예외 글에 있다", "telegram.json" in r.stderr,
          r.stderr.strip().splitlines()[-1:])

print("=== 부르는 파일은 읽는 자리를 먼저 들인다 ===")
# 함수만 부르고 lib 를 안 들이면 돌릴 때 command not found 로 죽는다. 그 자리는
# 평소 경로에서 안 밟혀서 몇 달 뒤에 알림이 한 번 안 가는 모양으로만 드러난다.
tracked_exec = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z",
                               "bin", "claude", "shell", "lib"],
                              capture_output=True, text=True).stdout.split("\0")
missing = []
for rel in [t for t in tracked_exec if t]:
    p = REPO / rel
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        continue
    if not re.search(r"operator_chat_id\)|\bchat_id\(\)", text):
        continue
    # 정본 자신과, 사람·세션이 읽는 글(프롬프트·설명서)은 부르는 자리가 아니다.
    if rel.startswith("lib/") or p.suffix in (".txt", ".md", ".json"):
        continue
    if "lib/operator_chat.sh" in text or "from operator_chat import" in text:
        continue
    missing.append(rel)
check("부르는 파일마다 읽는 자리가 들어 있다", not missing, missing[:5])

print("=== 저장소에 번호가 안 남아 있다 ===")
tracked = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"],
                         capture_output=True, text=True).stdout.split("\0")
tracked = [t for t in tracked if t]

real = ""
try:
    sys.path.insert(0, str(LIB))
    import operator_chat  # noqa: E402
    real = operator_chat.chat_id()
except Exception:
    real = ""

if not real:
    check("실제 방 번호를 못 읽어 전수 대조는 건너뛴다", True, "설정 없음")
else:
    hits = []
    for rel in tracked:
        p = REPO / rel
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if real in text:
            hits.append(rel)
    check("추적 파일에 방 번호가 없다", not hits, hits[:5])

# 숫자를 직접 적은 새 발송 자리를 잡는다. 열 자리 이상 숫자가 chat_id 근처에 있으면
# 설정을 안 거친 것이다 — 값 자체를 모르고도 모양으로 잡힌다.
# 검사 파일은 뺀다 — 거기 적힌 번호는 지어낸 값이고, 진짜 번호는 바로 위 전수 대조가
# 검사 파일까지 포함해서 잡는다. 뿌리의 tests/ 만 빼면 공개본 사본(publish/files/tests/)이
# 남아서, 같은 지어낸 번호가 한쪽에서만 걸린다.
def _is_test(rel):
    return rel.startswith("tests/") or "/tests/" in rel


pat = re.compile(r"chat_id[\"'\s:=]{1,6}(\d{8,})")
raw = []
for rel in [t for t in tracked if not _is_test(t)]:
    p = REPO / rel
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        continue
    for i, line in enumerate(text.splitlines(), 1):
        if pat.search(line):
            raw.append("{}:{}".format(rel, i))
check("chat_id 자리에 숫자를 직접 적은 곳이 없다", not raw, raw[:5])

print()
print("실패 {}건".format(len(fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
