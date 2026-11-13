#!/usr/bin/env python3
"""설치 점검 명령이 지키는 계약.

이 명령은 고치지 않고 보기만 한다. 그래서 고정하는 것이 넷이다.

  1. 못 세운 자리를 실패로 적고 종료코드 1 로 끝난다 — 조용히 통과하지 않는다.
  2. 다 세운 자리에는 설치 쪽 실패가 하나도 없다 — 멀쩡한 설치에 거짓 경보를 안 낸다.
  3. 비어 있어도 세션은 도는 것(통제 봇·유저봇 통로·linger·bun)은 경고지 실패가 아니다.
     통제 봇과 통로는 기본 구성이지만, 비었다고 실패로 막으면 첫 세션 확인부터 막힌다.
  4. 아무것도 안 고친다 — 돌린 뒤 집 디렉토리의 파일 목록이 그대로다.

기계마다 다른 자리(systemctl 유무, 명령이 깔렸는지)는 판정에 안 쓴다. 쓰면 이 검사가
그 기계의 설치 상태를 재게 되고, 그건 이 검사가 볼 것이 아니다.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCTOR = REPO / "bin" / "claude-ops-doctor"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


def fake_repo(root):
    """점검 명령만 든 작은 저장소를 만든다 — 진짜 저장소를 안 건드린다."""
    repo = Path(root) / "repo"
    (repo / "bin").mkdir(parents=True)
    (repo / "machines").mkdir()
    shutil.copy(str(DOCTOR), str(repo / "bin" / "claude-ops-doctor"))
    return repo


def run(repo, home):
    p = subprocess.run([sys.executable, str(repo / "bin" / "claude-ops-doctor")],
                       env=dict(os.environ, HOME=str(home),
                                PATH=f"{home}/.local/bin:{os.environ.get('PATH','')}"),
                       capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


def session(home, repo, name, with_token=True, mode=0o600):
    wd = home / name
    (wd / ".claude" / "telegram").mkdir(parents=True)
    unit = home / ".config" / "systemd" / "user" / f"claude-{name}.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(f"[Service]\nWorkingDirectory={wd}\n", encoding="utf-8")
    if with_token:
        env = wd / ".claude" / "telegram" / ".env"
        env.write_text("TELEGRAM_BOT_TOKEN=x\n", encoding="utf-8")
        env.chmod(mode)
    (home / ".config" / "claude-machine-id").write_text("box\n", encoding="utf-8")
    (repo / "machines" / "box.list").write_text(name + "\n", encoding="utf-8")
    return wd


def tree(home):
    return sorted(str(p.relative_to(home)) for p in home.rglob("*"))


def later_fails(out):
    """바탕 절(claude·bun·systemctl·PATH) 뒤의 실패 줄만 센다.

    바탕 절은 이 검사가 볼 것이 아니다 — 그 기계에 무엇이 깔렸는지라서, 그걸 판정에
    넣으면 이 검사가 설치 상태에 따라 켜졌다 꺼졌다 한다.
    """
    body = out.split("== 심기 ==", 1)
    if len(body) < 2:
        return ["(심기 절이 아예 없다)"]
    # 판정 줄은 칸을 맞춰 적는다("실패" + 빈칸 둘). 맨 끝 셈 줄("실패 0 · 경고 1")은
    # 빈칸 하나라 여기 안 걸린다 — 그 줄을 세면 멀쩡한 설치가 실패로 읽힌다.
    return [ln for ln in body[1].splitlines() if re.match(r"^실패\s{2,}\S", ln)]


check("점검 명령이 있다", DOCTOR.exists(), str(DOCTOR))
if not DOCTOR.exists():
    print("\n0/1 passed")
    sys.exit(1)

# ── 안 세운 자리 ────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    repo = fake_repo(tmp)

    rc, out = run(repo, home)
    check("기계 이름표가 없으면 실패로 끝난다", rc == 1 and "기계 이름표가 없다" in out,
          out[-300:])
    check("어디를 고치면 되는지 같이 적는다", "claude-machine-id" in out, out[-300:])
    check("설정이 안 깔린 것도 실패로 센다", "settings.json" in out, out[-400:])

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".config").mkdir(parents=True)
    repo = fake_repo(tmp)
    (home / ".config" / "claude-machine-id").write_text("box\n", encoding="utf-8")

    rc, out = run(repo, home)
    check("켤 목록이 없으면 실패로 끝난다", rc == 1 and "켤 목록이 없다" in out, out[-300:])

# ── 다 세운 자리 ────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    repo = fake_repo(tmp)
    (repo / "claude").mkdir()
    (repo / "claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    os.symlink(str(repo / "claude" / "settings.json"), str(home / ".claude" / "settings.json"))
    session(home, repo, "alpha")
    (home / ".claude" / "control-bot").mkdir(parents=True)
    (home / ".claude" / "control-bot" / ".env").write_text("x\n", encoding="utf-8")
    (home / ".claude" / "userbot").mkdir(parents=True)
    (home / ".claude" / "userbot" / "targets.json").write_text("{}\n", encoding="utf-8")

    before = tree(home)
    rc, out = run(repo, home)
    check("다 세운 설치에는 설치 쪽 실패가 없다", later_fails(out) == [],
          str(later_fails(out)))
    check("세션 이름을 찍는다", "[alpha]" in out, out[-500:])
    check("설정이 저장소를 가리키는 것을 본다", "설정이 이 저장소를 가리킨다" in out,
          out[-500:])
    check("아무것도 안 고친다", tree(home) == before,
          str(set(tree(home)) ^ set(before)))

# ── 없어도 도는 것은 경고다 ─────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    repo = fake_repo(tmp)
    (repo / "claude").mkdir()
    (repo / "claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    os.symlink(str(repo / "claude" / "settings.json"), str(home / ".claude" / "settings.json"))
    session(home, repo, "alpha", with_token=False)

    rc, out = run(repo, home)
    check("토큰이 없으면 경고지 실패가 아니다",
          later_fails(out) == [] and "봇 토큰 파일이 없다" in out, out[-500:])
    check("통제 봇·통로가 없으면 경고로 적는다",
          "통제 봇 설정이 없다" in out and "통로" in out, out[-400:])

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    repo = fake_repo(tmp)
    (repo / "claude").mkdir()
    (repo / "claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    os.symlink(str(repo / "claude" / "settings.json"), str(home / ".claude" / "settings.json"))
    session(home, repo, "alpha", mode=0o644)

    rc, out = run(repo, home)
    check("남이 읽을 수 있는 토큰 파일을 짚는다",
          "남이 읽을 수 있다" in out and later_fails(out) == [], out[-500:])

# ── 유닛이 안 깔린 세션 ─────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    (home / ".config").mkdir(parents=True, exist_ok=True)
    repo = fake_repo(tmp)
    (repo / "claude").mkdir()
    (repo / "claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    os.symlink(str(repo / "claude" / "settings.json"), str(home / ".claude" / "settings.json"))
    (home / ".config" / "claude-machine-id").write_text("box\n", encoding="utf-8")
    (repo / "machines" / "box.list").write_text("alpha\n# 주석\n\n", encoding="utf-8")

    rc, out = run(repo, home)
    check("유닛이 안 깔렸으면 실패로 끝난다",
          rc == 1 and "유닛이 안 깔렸다" in out, out[-400:])
    check("주석과 빈 줄은 세션으로 안 센다", "켤 세션 1개" in out, out[-600:])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
