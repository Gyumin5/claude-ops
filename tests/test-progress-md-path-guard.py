#!/usr/bin/env python3
"""progress-md-path-guard 테스트.

고정하는 것 셋.

1. 이 훅은 막지 않는다 — 항상 종료코드 0.
2. 판정 기준은 세션 루트(CLAUDE_PROJECT_DIR)지 현재 작업디렉토리가 아니다.
   하네스가 작업 도중 작업디렉토리를 옮기기 때문이고, 2026-09-23~10-12 로그 27건이
   전부 그 때문에 난 오탐이었다. 루트를 기준으로 삼으면 그 27건이 침묵하고,
   작업디렉토리가 하위로 내려간 뒤 거기 쓰는 진짜 오배치가 잡힌다.
3. 경고한 회차만 관측 기록을 남긴다 — 그 기록이 차단 승격을 판단할 유일한 자료다.

루트 변수가 없는 환경은 근사 판정으로 내려가고, 그 회차 기록에 root=미확인 으로
남긴다. 근사라는 사실이 기록과 문구에서 사라지면 나중에 그 줄을 정확한 판정으로
오해한다.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = str(Path(__file__).resolve().parent.parent / "claude" / "hooks"
           / "progress-md-path-guard.sh")
results = []


def check(name, cond, detail=None):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail!r}" if not cond and detail else ""))


def run_hook(log, *, tool="Write", file_path="", cwd="", root=None):
    env = dict(os.environ)
    env["PROGRESS_MD_PATH_GUARD_LOG"] = str(log)
    env.pop("CLAUDE_PROJECT_DIR", None)
    if root is not None:
        env["CLAUDE_PROJECT_DIR"] = str(root)
    payload = json.dumps({"tool_name": tool,
                          "tool_input": {"file_path": file_path},
                          "cwd": cwd})
    p = subprocess.run(["bash", HOOK], input=payload, capture_output=True,
                       text=True, env=env, timeout=60)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def log_lines(log):
    try:
        return [l for l in Path(log).read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return []


# ── 루트 기준 판정 (CLAUDE_PROJECT_DIR 있음) ────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    base = Path(os.path.realpath(tmp))
    root = base / "proj"
    sub = root / "state" / "job"
    sub.mkdir(parents=True)
    sibling = base / "proj-backup"
    sibling.mkdir()
    log = base / "guard.log"

    rc, out = run_hook(log, file_path=str(root / "progress.md"),
                       cwd=str(root), root=root)
    check("루트에 쓰면 종료코드 0", rc == 0, out)
    check("루트에 쓰면 무경고", "경고" not in out, out)
    check("루트에 쓰면 기록 없음", log_lines(log) == [], log_lines(log))

    # 이게 27건 오탐의 모양이다 — 작업디렉토리만 하위로 내려가 있고 쓰는 곳은 루트다.
    rc, out = run_hook(log, file_path=str(root / "progress.md"),
                       cwd=str(sub), root=root)
    check("작업디렉토리가 하위여도 루트에 쓰면 무경고", "경고" not in out, out)
    check("그 회차는 기록도 안 남는다", log_lines(log) == [], log_lines(log))

    rc, out = run_hook(log, file_path=str(sub / "progress.md"),
                       cwd=str(root), root=root)
    check("하위에 쓰면 경고한다", "경고" in out, out)
    check("하위에 써도 막지 않는다", rc == 0, out)

    # 옛 조건(대상과 작업디렉토리 비교)이 놓치던 자리다.
    rc, out = run_hook(log, file_path=str(sub / "progress.md"),
                       cwd=str(sub), root=root)
    check("작업디렉토리가 그 하위여도 하위 쓰기는 경고한다", "경고" in out, out)

    rc, out = run_hook(log, file_path=str(sibling / "progress.md"),
                       cwd=str(root), root=root)
    check("형제 폴더는 경고한다", "경고" in out, out)

    rc, out = run_hook(log, file_path=str(base / "progress.md"),
                       cwd=str(root), root=root)
    check("루트보다 위도 경고한다", "경고" in out, out)

    lines = log_lines(log)
    check("경고한 회차만 쌓인다", len(lines) == 4, lines)
    check("기록에 루트가 남는다", all(f"root={root}" in l for l in lines), lines)
    check("기록에 작업디렉토리가 남는다", "cwd=" in lines[0], lines[0])
    check("기록에 쓰려던 경로가 남는다", f"target={sub}" in lines[0], lines[0])
    check("기록에 도구 이름이 남는다", "tool=Write" in lines[0], lines[0])
    check("문구가 작업디렉토리를 루트라고 말하지 않는다",
          "세션 루트(" not in out or str(root) in out, out)

# ── 공백이 든 경로 ─────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    base = Path(os.path.realpath(tmp))
    root = base / "my proj"
    sub = root / "state dir"
    sub.mkdir(parents=True)
    log = base / "guard.log"

    rc, out = run_hook(log, file_path=str(root / "progress.md"),
                       cwd=str(sub), root=root)
    check("공백 경로도 루트 쓰기는 무경고", rc == 0 and "경고" not in out, out)
    rc, out = run_hook(log, file_path=str(sub / "progress.md"),
                       cwd=str(root), root=root)
    check("공백 경로도 하위 쓰기는 경고", "경고" in out, out)
    lines = log_lines(log)
    check("공백 경로가 기록에 온전히 남는다",
          len(lines) == 1 and f"target={sub}" in lines[0], lines)

# ── 루트 변수가 없을 때(근사 판정) ─────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    base = Path(os.path.realpath(tmp))
    root = base / "proj"
    sub = root / "state"
    sub.mkdir(parents=True)
    trap = base / "proj-backup"
    trap.mkdir()
    log = base / "guard.log"

    rc, out = run_hook(log, file_path=str(root / "progress.md"), cwd=str(sub))
    check("근사: 대상이 작업디렉토리의 상위면 무경고", "경고" not in out, out)
    rc, out = run_hook(log, file_path=str(sub / "progress.md"), cwd=str(sub))
    check("근사: 대상이 작업디렉토리 자신이면 무경고", "경고" not in out, out)
    rc, out = run_hook(log, file_path=str(sub / "progress.md"), cwd=str(root))
    check("근사: 대상이 하위면 경고", "경고" in out, out)
    check("근사라는 것을 문구가 밝힌다", "근사" in out, out)
    rc, out = run_hook(log, file_path=str(root / "progress.md"), cwd=str(trap))
    check("근사: 이름 앞부분만 같은 형제는 경고한다", "경고" in out, out)
    lines = log_lines(log)
    check("근사 회차는 root=미확인 으로 남는다",
          len(lines) == 2 and all("root=미확인" in l for l in lines), lines)
    rc, out = run_hook(log, file_path=str(sub / "progress.md"), cwd="")
    check("근사인데 작업디렉토리도 없으면 판정하지 않는다",
          rc == 0 and len(log_lines(log)) == 2, out)

# ── 대상이 아닌 호출 ───────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    base = Path(os.path.realpath(tmp))
    root = base / "proj"
    (root / "sub").mkdir(parents=True)
    log = base / "guard.log"
    rc, out = run_hook(log, tool="Read", file_path=str(root / "sub" / "progress.md"),
                       cwd=str(root), root=root)
    check("Read 는 대상 아님", rc == 0 and "경고" not in out and log_lines(log) == [], out)
    rc, out = run_hook(log, file_path=str(root / "sub" / "notes.md"),
                       cwd=str(root), root=root)
    check("다른 파일 이름은 대상 아님",
          rc == 0 and "경고" not in out and log_lines(log) == [], out)
    rc, out = run_hook(log, file_path="", cwd=str(root), root=root)
    check("경로가 비면 판정하지 않는다", rc == 0 and log_lines(log) == [], out)

# ── 기록 상한과 기록 실패 ──────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    base = Path(os.path.realpath(tmp))
    root = base / "proj"
    (root / "sub").mkdir(parents=True)
    log = base / "guard.log"
    log.write_text("".join(f"old {i}\n" for i in range(300)), encoding="utf-8")
    run_hook(log, file_path=str(root / "sub" / "progress.md"),
             cwd=str(root), root=root)
    lines = log_lines(log)
    check("기록은 200줄로 잘린다", len(lines) == 200, len(lines))
    check("잘려도 방금 줄은 남는다", lines and "target=" in lines[-1], lines[-1:])

with tempfile.TemporaryDirectory() as tmp:
    base = Path(os.path.realpath(tmp))
    root = base / "proj"
    (root / "sub").mkdir(parents=True)
    blocked = base / "nodir"
    blocked.write_text("파일이라 하위 경로를 못 만든다", encoding="utf-8")
    rc, out = run_hook(blocked / "x" / "guard.log",
                       file_path=str(root / "sub" / "progress.md"),
                       cwd=str(root), root=root)
    check("기록 실패해도 종료코드 0", rc == 0, out)
    check("기록 실패해도 경고는 나간다", "경고" in out, out)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
