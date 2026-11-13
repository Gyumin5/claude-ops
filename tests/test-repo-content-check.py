#!/usr/bin/env python3
"""추적 파일 내용 검사(bin/repo-content-check)가 실제로 잡는지 고정한다.

2026-11-04 에 운영자가 저장소에 개인 정보가 들어가 있다고 짚었고(운영자 지시), 2026-11-06 에
"항상 밀 때 그런 걸 다 검사하는 규칙" 을 넣으라고 했다(8389). 경로 검사
(tests/test-repo-scope.py)는 어느 경로를 추적하느냐만 보므로 허용된 경로 안에 적힌 값은
못 본다 — 실제로 스킬 본문에 사업자등록번호가, 다섯 자리에 개인·회사 메일이 박혀 있었다.

여기서 세는 것은 둘이다. ⑴ 본보기 값을 주면 걸리고 안 걸려야 하는 것은 안 걸린다
⑵ 지금 추적 중인 파일 전부가 깨끗하다.

본보기 번호는 문자를 이어 붙여 만든다 — 이 파일 자체에 그대로 적으면 검사가 자기를 잡는다.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / "bin" / "repo-content-check"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


def run(*args):
    p = subprocess.run([sys.executable, str(CHECK), *args],
                       cwd=str(REPO), capture_output=True, text=True, timeout=180)
    return p.returncode, (p.stdout + p.stderr)


def scan_text(body):
    """임시 파일에 적어 그 경로만 검사한다. (rc, 출력)"""
    with tempfile.TemporaryDirectory(dir=str(REPO)) as td:
        f = Path(td) / "sample.txt"
        f.write_text(body, encoding="utf-8")
        rel = os.path.relpath(f, REPO)
        return run(rel)


check("검사 파일이 있다", CHECK.exists(), str(CHECK))
check("실행 권한이 있다", os.access(CHECK, os.X_OK))
if not CHECK.exists():
    print("\n0/0 passed")
    sys.exit(1)

# --- 1. 잡아야 하는 것 -------------------------------------------------------
BIZ = "580-81-" + "03287"
RRN = "900101-" + "1234567"
CARD = "1234-5678-" + "9012-3456"
# 허용 목록 밖 도메인이면 잡혀야 한다. 글자는 아무 것이나 되므로 본보기로 둔다 —
# 실제 조직 이름을 적을 이유가 없다.
MAIL_WORK = "someone" + "@" + "some-company.co"
MAIL_PERSONAL = "someone" + "@" + "gmail.com"

for label, body in [
    ("사업자등록번호", f"사업자 {BIZ} 로 발행"),
    ("주민등록번호", f"주민 {RRN} 확인"),
    ("카드번호", f"카드 {CARD} 승인"),
    ("허용 밖 메일", f"보낸이 {MAIL_WORK} 입니다"),
    ("개인 메일", f"연락처 {MAIL_PERSONAL}"),
]:
    rc, out = scan_text(body)
    check(f"잡는다: {label}", rc == 1, f"rc={rc} out={out.strip()[:200]}")

# --- 2. 잡으면 안 되는 것 ----------------------------------------------------
for label, body in [
    ("날짜", "2026-11-06 09:08 KST 에 돌았다"),
    ("고객센터 번호", "고객센터 1800-2200 의 2번"),
    ("ssh 원격 주소", "git clone git@github.com:Gyumin5/dotfiles.git"),
    ("커밋 귀속 줄", "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"),
    ("공개 커밋 메일", "Gyumin5@users.noreply.github.com"),
    ("systemd 템플릿 인스턴스", "claude-session-nudge@beta.service 는 세션이 아니다"),
    ("cgroup 경로", "/user.slice/user-1000.slice/user@1000.service/claude-dotfiles.service"),
    ("본보기 도메인", "someone@example.com 으로 보낸다"),
    # RFC 2606 예약 최상위 도메인. 등록될 수 없어 사람 주소가 못 된다 —
    # 검사 코드가 임시 저장소에 심는 가짜 작성자 주소가 이 꼴이다.
    ("예약 최상위 도메인", "git config user.email t@example.invalid"),
]:
    rc, out = scan_text(body)
    check(f"안 잡는다: {label}", rc == 0, f"rc={rc} out={out.strip()[:200]}")

# --- 3. 지금 추적 중인 파일 전부 ---------------------------------------------
rc, out = run("--all")
check("추적 파일 전부가 깨끗하다", rc == 0, out.strip()[:1500])

# --- 3-b. 커밋되는 것은 인덱스다 ---------------------------------------------
# 스테이징분을 볼 때 작업 트리를 읽으면, 민감한 내용을 스테이징한 다음 작업 파일만
# 정리하는 것으로 검사를 통과하고 커밋에는 그 내용이 그대로 들어간다. 진짜 저장소를
# 건드리지 않으려고 임시 저장소에 검사 사본을 두고 거기서 돌린다.
with tempfile.TemporaryDirectory() as td:
    sand = Path(td) / "repo"
    (sand / "bin").mkdir(parents=True)
    (sand / "bin" / "repo-content-check").write_bytes(CHECK.read_bytes())
    (sand / "bin" / "repo-content-check").chmod(0o755)

    def git(*a):
        return subprocess.run(["git", "-C", str(sand), *a],
                              capture_output=True, text=True, timeout=60)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")

    def sand_run():
        p = subprocess.run([sys.executable, str(sand / "bin" / "repo-content-check")],
                           cwd=str(sand), capture_output=True, text=True, timeout=120)
        return p.returncode, (p.stdout + p.stderr)

    target = sand / "doc.txt"
    target.write_text(f"사업자 {BIZ} 로 발행\n", encoding="utf-8")
    git("add", "doc.txt")
    rc, out = sand_run()
    check("스테이징된 내용을 잡는다", rc == 1, f"rc={rc} out={out.strip()[:200]}")

    # 스테이징은 그대로 두고 작업 파일만 지운다 — 커밋되는 것은 여전히 더러운 쪽이다.
    target.write_text("깨끗하다\n", encoding="utf-8")
    rc, out = sand_run()
    check("작업 파일만 정리해도 그대로 잡는다", rc == 1, f"rc={rc} out={out.strip()[:200]}")

    # 반대로 인덱스가 깨끗하면 작업 트리가 더러워도 그 커밋은 통과해야 한다.
    git("add", "doc.txt")
    target.write_text(f"사업자 {BIZ} 로 발행\n", encoding="utf-8")
    rc, out = sand_run()
    check("인덱스가 깨끗하면 그 커밋은 통과한다", rc == 0, f"rc={rc} out={out.strip()[:200]}")

# 훅 배선과 미는 자리 본체는 이 저장소가 쓰는 경계 검사라 공개판에 안 나간다.
# 여기서는 검사 자체의 동작만 고정한다 — 없는 파일을 찾는 칸을 남겨 두면 그
# 검사는 공개판에서 영영 빨갛고, 빨간 이유가 결함이 아니라 설계가 된다.

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
