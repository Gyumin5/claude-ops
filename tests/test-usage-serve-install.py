#!/usr/bin/env python3
"""claude-usage-serve-install 이 지키는 것들.

여기서 고정하는 것 셋이다. ⑴ 접속 열쇠에 명령이 못 박히고 나머지는 다 꺼진다 —
그 열쇠를 쥔 사람이 셸을 열거나 포트를 넘기면 그 기계 전체가 열린다. ⑵ 두 번
돌려도 열쇠·열쇠글이 새로 안 생기고 접속 설정 줄도 안 늘어난다 — 설치를 다시
돌리는 일은 흔하고, 그때 이미 나눠 준 열쇠가 바뀌면 받은 사람이 통째로 막힌다.
⑶ 손대기 전에 접속 설정 사본을 남긴다.
"""
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "claude-usage-serve-install"

fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  — {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def run(home, repo):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["CLAUDE_USAGE_NO_SYSTEMD"] = "1"   # 진짜 유닛은 안 건드린다
    return subprocess.run([str(SCRIPT), "--repo", str(repo)],
                          capture_output=True, text=True, env=env, timeout=120)


def main():
    check("설치 도우미가 있다", SCRIPT.exists(), str(SCRIPT))
    if not SCRIPT.exists():
        return 1

    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "home"
        repo = Path(td) / "snap"
        (repo / "bin").mkdir(parents=True)
        web = repo / "bin" / "claude-usage-web"
        web.write_text("#!/bin/sh\n", encoding="utf-8")
        web.chmod(0o755)
        home.mkdir()

        p = run(home, repo)
        check("한 번 돌면 끝난다", p.returncode == 0, (p.stderr or p.stdout).strip()[:160])
        if p.returncode != 0:
            return 1

        ak = home / ".ssh" / "authorized_keys"
        lines = [l for l in ak.read_text(encoding="utf-8").splitlines() if l.strip()]
        check("열쇠 하나가 등록된다", len(lines) == 1, str(len(lines)))

        for line in lines:
            m = re.match(r'command="([^"]+)",([^ ]+) ssh-ed25519 ', line)
            check("명령이 못 박히고 열쇠 모양도 맞다", bool(m), line[:90])
            if not m:
                continue
            cmd, opts = m.group(1), m.group(2)
            check("못 박힌 명령이 이 설치가 만든 것이다",
                  cmd == str(home / ".local" / "bin" / "claude-usage"), cmd)
            check("전부 끄고 시작한다", opts.split(",")[0] == "restrict", opts)
            for off in ("no-pty", "no-port-forwarding", "no-agent-forwarding"):
                check(f"{off} 가 걸린다", off in opts, opts)

        tok = home / ".config" / "claude-usage" / "token"
        check("열쇠글이 생긴다", tok.exists() and len(tok.read_text().strip()) >= 20)
        check("열쇠글은 주인만 읽는다",
              stat.S_IMODE(tok.stat().st_mode) == 0o600,
              oct(stat.S_IMODE(tok.stat().st_mode)))
        keydir = home / ".config" / "claude-usage" / "keys"
        check("사설 열쇠는 주인만 읽는다",
              stat.S_IMODE((keydir / "usage").stat().st_mode) == 0o600)

        unit = (home / ".config" / "systemd" / "user" / "claude-usage-web.service"
                ).read_text(encoding="utf-8")
        check("쪽은 이 기계 안에서만 듣는다",
              "--host 127.0.0.1" in unit and "0.0.0.0" not in unit)
        check("쪽은 열쇠글을 걸고 선다", "--token" in unit)
        check("쪽은 죽으면 다시 선다", "Restart=always" in unit)

        body = (home / ".local" / "bin" / "claude-usage").read_text(encoding="utf-8")
        check("누르면 언제나 다시 잰다", "/measure" in body, body.strip()[-90:])
        check("재고 나서 그 결과를 보여 준다", "/usage.txt" in body, body.strip()[-90:])

        # ── 두 번째 ─────────────────────────────────────────────────────────
        before = (tok.read_text(), (keydir / "usage.pub").read_text())
        p2 = run(home, repo)
        check("두 번째도 끝난다", p2.returncode == 0, (p2.stderr or p2.stdout).strip()[:160])
        after = (tok.read_text(), (keydir / "usage.pub").read_text())
        check("이미 나눠 준 열쇠와 열쇠글은 그대로 둔다", before == after)
        lines2 = [l for l in ak.read_text(encoding="utf-8").splitlines() if l.strip()]
        check("접속 설정 줄이 안 늘어난다", len(lines2) == 1, str(len(lines2)))
        baks = list((home / ".ssh").glob("authorized_keys.bak-*"))
        check("손대기 전 사본을 남긴다", len(baks) >= 1, str(len(baks)))

    return 1 if fails else 0


if __name__ == "__main__":
    rc = main()
    print(f"\n{'실패: ' + ', '.join(fails) if fails else '모두 통과'}")
    sys.exit(rc)
