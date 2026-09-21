#!/usr/bin/env python3
"""claude-progress-updater-all 대상 선정 테스트.

왜 있나: 2026-08-11 까지 이 스크립트는 세션이름→디렉토리 매핑을 손으로 들고 있었고,
목록에 없는 이름은 조용히 continue 했다. peer·session-a·session-* 4개·session_c
일곱 세션이 도입 이후 내내 갱신 대상이 아니었는데 로그조차 안 남아 아무도 몰랐다.
progress.md 가 19~92시간 낡아 있었다(history #57).

그래서 고정하는 것: 대상은 systemd 에서 읽는다(손목록 금지), 세션이 아닌 유닛은 빠진다,
빠질 때 조용하지 않다.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin",
                      "claude-progress-updater-all")

results = []


def check(name, cond, detail: object = ""):
    results.append(bool(cond))
    print(("  PASS " if cond else "  FAIL ") + name +
          (("  — " + str(detail)) if detail and not cond else ""))


def sh(path, body):
    with open(path, "w") as f:
        f.write("#!/bin/bash\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# (유닛, 세션이름 있음, transient, cwd 하위경로 | None = WorkingDirectory 없음)
UNITS = [
    ("claude-dotfiles.service", True, False, "dotfiles"),
    ("claude-peer.service", True, False, "peer"),
    ("claude-session-a.service", True, False, "session-a"),
    ("claude-session-b.service", True, False, "proj-b"),
    ("claude-session_c.service", True, False, "session_c_work"),
    ("claude-watchdog.service", False, False, "etc"),          # 세션 아님
    ("claude-control-bot.service", False, False, "etc"),       # 세션 아님
    ("claude-job-sweep-1786.service", True, True, "jobdir"),   # 일회성 잡
    ("claude-session-nudge@session-d.service", True, False, "x"),  # 템플릿 인스턴스
    ("claude-noprog.service", True, False, "noprog"),          # progress.md 없음
]


def build(tmp):
    home = os.path.join(tmp, "home")
    fake = os.path.join(tmp, "fakebin")
    os.makedirs(os.path.join(home, ".claude", "logs"), exist_ok=True)
    os.makedirs(fake, exist_ok=True)

    for entry in UNITS:
        sub = entry[3]
        d = os.path.join(home, sub)
        os.makedirs(d, exist_ok=True)
        if sub != "noprog":
            with open(os.path.join(d, "progress.md"), "w") as f:
                f.write("# progress.md\n")

    listing = "\n".join("%s loaded active running x" % u for u, _, _, _ in UNITS)
    cases = []
    for u, has_name, transient, sub in UNITS:
        body = ["WorkingDirectory=%s" % os.path.join(home, sub),
                "Transient=%s" % ("yes" if transient else "no")]
        body.append("Environment=%s" %
                    ("CLAUDE_SESSION_NAME=%s FOO=1" % u if has_name else "FOO=1"))
        cases.append('%s) cat <<E\n%s\nE\n;;' % (u, "\n".join(body)))

    sh(os.path.join(fake, "systemctl"), """
for a in "$@"; do case "$a" in list-units) MODE=list;; show) MODE=show;; esac; done
if [ "${MODE:-}" = list ]; then cat <<'L'
%s
L
exit 0
fi
for a in "$@"; do case "$a" in claude-*) U="$a";; esac; done
case "$U" in
%s
esac
""" % (listing, "\n".join(cases)))

    updater = os.path.join(tmp, "updater")
    sh(updater, 'printf "%s\\n" "$1" >> "$RECORD"\n')

    env = dict(os.environ)
    env.update({"HOME": home, "PATH": fake + os.pathsep + env["PATH"],
                "PROGRESS_UPDATER_BIN": updater,
                "RECORD": os.path.join(tmp, "record")})
    return env, home


def main():
    tmp = tempfile.mkdtemp(prefix="pua.")
    try:
        env, home = build(tmp)
        r = subprocess.run(["bash", os.path.realpath(SCRIPT)], env=env,
                           capture_output=True, text=True, timeout=60)
        rec = open(env["RECORD"]).read() if os.path.exists(env["RECORD"]) else ""
        log = open(os.path.join(home, ".claude", "logs", "progress-updater.log")).read() \
            if os.path.exists(os.path.join(home, ".claude", "logs", "progress-updater.log")) else ""
        hit = [os.path.basename(x) for x in rec.split()]

        print("대상 선정")
        check("rc=0", r.returncode == 0, r.stderr[-200:])
        for sub in ("dotfiles", "peer", "session-a", "proj-b", "session_c_work"):
            check("%s 세션이 대상이다" % sub, sub in hit, hit)

        print("제외")
        check("세션이름 없는 상시 유닛은 제외(watchdog·control-bot)",
              "etc" not in hit, hit)
        check("systemd-run 일회성 잡은 제외", "jobdir" not in hit, hit)
        check("템플릿 인스턴스는 제외", "x" not in hit, hit)
        check("progress.md 없으면 갱신 안 한다", "noprog" not in hit, hit)
        check("빠질 때 조용하지 않다", "progress.md 없음" in log, log[-200:])

        print("손목록 부재")
        src = open(os.path.realpath(SCRIPT)).read()
        check("세션이름→경로 하드코딩이 없다",
              "project_cwd" not in src and "/home/user/session-e" not in src)
        check("대상을 WorkingDirectory 에서 읽는다", "WorkingDirectory=" in src)

        print("빈 목록")
        env2, home2 = build(tempfile.mkdtemp(dir=tmp))
        sh(os.path.join(os.path.dirname(env2["PATH"].split(os.pathsep)[0]),
                        "fakebin", "systemctl"), 'exit 0\n')
        subprocess.run(["bash", os.path.realpath(SCRIPT)], env=env2,
                       capture_output=True, text=True, timeout=60)
        log2 = open(os.path.join(home2, ".claude", "logs",
                                 "progress-updater.log")).read()
        check("대상이 0개면 그 사실을 남긴다", "대상 세션 0개" in log2, log2[-200:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ok = all(results)
    print("\n%d/%d PASS" % (sum(results), len(results)))
    print("ALL PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
