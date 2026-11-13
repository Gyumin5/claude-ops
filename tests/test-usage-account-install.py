#!/usr/bin/env python3
"""claude-usage-account-install 이 지키는 것들.

여기서 고정하는 것 넷이다. ⑴ 열쇠가 닿는 자리가 전용 계정 하나로 옮겨지고,
소유자 계정에서는 그 줄이 빠진다 — 안 빼면 같은 열쇠로 두 계정에 붙어서 옮긴
의미가 없다. ⑵ 소유자의 다른 접속 줄은 하나도 안 건드린다. ⑶ 이미 나눠 준
열쇠를 그대로 쓴다(공개키를 옮겨 적을 뿐이다). ⑷ 옮긴 뒤에도 명령이 못 박혀
있고 나머지는 다 꺼져 있다.
"""

import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "claude-usage-account-install"

fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  — {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def run(root, owner="owner", user="claude-usage"):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--target-root", str(root), "--no-system",
         "--owner", owner, "--user", user],
        capture_output=True, text=True, timeout=60)


OTHER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOTHERKEY nobody@elsewhere"


def seed(root, owner="owner"):
    oh = root / "home" / owner
    (oh / ".config" / "claude-usage" / "keys").mkdir(parents=True)
    (oh / ".ssh").mkdir(parents=True)
    (oh / ".config" / "claude-usage" / "token").write_text("secret-token-value-123\n",
                                                           encoding="utf-8")
    pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIUSAGEKEY claude-usage"
    (oh / ".config" / "claude-usage" / "keys" / "usage.pub").write_text(
        pub + "\n", encoding="utf-8")
    (oh / ".ssh" / "authorized_keys").write_text(
        OTHER + "\n"
        + f'command="/home/{owner}/.local/bin/claude-usage",restrict {pub}\n',
        encoding="utf-8")
    return oh, pub


def main():
    check("설치 도우미가 있다", SCRIPT.exists(), str(SCRIPT))
    if not SCRIPT.exists():
        return 1

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        oh, pub = seed(root)

        p = run(root)
        check("한 번 돌면 끝난다", p.returncode == 0, (p.stderr or p.stdout).strip()[:200])
        if p.returncode != 0:
            return 1

        home = root / "home" / "claude-usage"
        ak = home / ".ssh" / "authorized_keys"
        lines = [l for l in ak.read_text(encoding="utf-8").splitlines() if l.strip()]
        check("전용 계정에 열쇠 하나가 걸린다", len(lines) == 1, str(len(lines)))
        for line in lines:
            m = re.match(r'command="([^"]+)",([^ ]+) ssh-ed25519 ', line)
            check("명령이 못 박히고 열쇠 모양도 맞다", bool(m), line[:80])
            if not m:
                continue
            check("못 박힌 명령이 전용 계정 안의 것이다",
                  m.group(1) == str(home / "bin" / "usage"), m.group(1))
            check("전부 끄고 시작한다", m.group(2).split(",")[0] == "restrict", m.group(2))
        check("이미 나눠 준 열쇠를 그대로 쓴다",
              pub.split()[1] in ak.read_text(encoding="utf-8"))

        owner_ak = (oh / ".ssh" / "authorized_keys").read_text(encoding="utf-8")
        check("소유자 계정에서는 그 열쇠로 못 붙는다",
              "claude-usage" not in owner_ak, owner_ak.strip()[-80:])
        check("소유자의 다른 접속 줄은 그대로 둔다", OTHER in owner_ak)
        baks = list((oh / ".ssh").glob("authorized_keys.bak-*"))
        check("손대기 전 사본을 남긴다", len(baks) >= 1, str(len(baks)))

        tok = home / ".config" / "claude-usage" / "token"
        check("전용 계정이 읽을 열쇠글이 생긴다",
              tok.read_text(encoding="utf-8").strip() == "secret-token-value-123")
        check("그 열쇠글은 주인만 읽는다",
              stat.S_IMODE(tok.stat().st_mode) == 0o600,
              oct(stat.S_IMODE(tok.stat().st_mode)))
        body = (home / "bin" / "usage").read_text(encoding="utf-8")
        check("누르면 언제나 다시 잰다", "/measure" in body, body.strip()[-80:])
        check("재고 나서 그 결과를 보여 준다", "/usage.txt" in body, body.strip()[-80:])
        check("쪽은 같은 기계 안에서만 부른다",
              "localhost:8787" in body and "://" not in body)
        check("쓰는 법에 전용 계정 이름이 나온다",
              "claude-usage@" in p.stdout, p.stdout.strip()[-90:])

        # ── 두 번째 ─────────────────────────────────────────────────────────
        p2 = run(root)
        check("두 번째도 끝난다", p2.returncode == 0, (p2.stderr or p2.stdout).strip()[:200])
        lines2 = [l for l in ak.read_text(encoding="utf-8").splitlines() if l.strip()]
        check("접속 설정 줄이 안 늘어난다", len(lines2) == 1, str(len(lines2)))
        check("두 번째에는 뺄 줄이 없다", "뺀 줄 0개" in p2.stdout, p2.stdout.strip()[:90])

    # ── 열쇠글이 없으면 시작하지 않는다 ───────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "home" / "owner").mkdir(parents=True)
        p = run(root)
        check("열쇠글이 없으면 거절한다",
              p.returncode == 1 and "열쇠글이 없다" in p.stderr, p.stderr.strip()[:90])

    return 1 if fails else 0


if __name__ == "__main__":
    rc = main()
    print(f"\n{'실패: ' + ', '.join(fails) if fails else '모두 통과'}")
    sys.exit(rc)
