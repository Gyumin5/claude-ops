#!/usr/bin/env python3
"""home-claude-health 상태기계 테스트.

왜 있나: 2026-08-07 17:56, CLI 자기업데이트 교체 창에 걸린 probe 실패가 "로그인 만료,
재로그인 필요" 로 사람에게 나갔다. 로그인은 멀쩡했다. rc≠0 을 전부 인증 실패로 라벨하고
출력은 버리던 구현 때문이다.

그래서 고정하는 것: 인증 확정은 좁은 문구로만, 그 외 실패는 연속 2회차에 "미분류" 로,
성공 출력은 분류하지 않는다, 복구 알림은 실제로 경보한 뒤에만.
"""
import os
import shutil
import subprocess
import sys
import tempfile

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "home-claude-health")


class Env:
    def __init__(self, tmp):
        self.tmp = tmp
        self.state_dir = os.path.join(tmp, "state")
        self.notify_log = os.path.join(tmp, "notify.log")
        self.fake_claude = os.path.join(tmp, "fake-claude")
        self.fake_notify = os.path.join(tmp, "fake-notify")
        os.makedirs(os.path.join(tmp, "home", ".cache"), exist_ok=True)
        with open(self.fake_notify, "w") as f:
            # $2=세션 이름, $3=본문. 수신처도 따로 남긴다 — 본문만 보면 경보가
            # 엉뚱한 세션으로 가도 테스트가 통과한다.
            f.write('#!/bin/bash\nprintf "%s\\n" "$3" >> "$NOTIFY_LOG"\n'
                    'printf "%s\\n" "$2" >> "$NOTIFY_LOG.session"\n')
        os.chmod(self.fake_notify, 0o755)

    def set_probe(self, *rounds):
        """rounds: (rc, output) 순서대로. 호출 횟수를 카운터 파일로 센다."""
        cnt = os.path.join(self.tmp, "probe.count")
        if os.path.exists(cnt):
            os.remove(cnt)
        body = ['#!/bin/bash', 'n=$(cat "%s" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "%s"' % (cnt, cnt)]
        body.append('case "$n" in')
        for i, (rc, out) in enumerate(rounds, 1):
            body.append('  %d) printf "%%s\\n" %s; exit %d;;' % (i, _q(out), rc))
        last_rc, last_out = rounds[-1]
        body.append('  *) printf "%%s\\n" %s; exit %d;;' % (_q(last_out), last_rc))
        body.append('esac')
        with open(self.fake_claude, "w") as f:
            f.write("\n".join(body) + "\n")
        os.chmod(self.fake_claude, 0o755)

    def run(self):
        env = dict(os.environ)
        env.update({
            "HOME": os.path.join(self.tmp, "home"),
            "HOME_CLAUDE_HEALTH_STATE_DIR": self.state_dir,
            "HOME_CLAUDE_HEALTH_BACKOFF": "0",
            "HOME_CLAUDE_HEALTH_CLAUDE": self.fake_claude,
            "HOME_CLAUDE_HEALTH_NOTIFY": self.fake_notify,
            "NOTIFY_LOG": self.notify_log,
        })
        return subprocess.run(["bash", SCRIPT], env=env, capture_output=True, text=True)

    def notifications(self):
        if not os.path.exists(self.notify_log):
            return []
        with open(self.notify_log) as f:
            return [l.strip() for l in f if l.strip()]

    def notify_sessions(self):
        f = self.notify_log + ".session"
        if not os.path.exists(f):
            return []
        with open(f) as fh:
            return [l.strip() for l in fh if l.strip()]

    def state(self):
        p = os.path.join(self.state_dir, "state")
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return dict(l.strip().split("=", 1) for l in f if "=" in l)

    def probe_log(self):
        p = os.path.join(self.state_dir, "probe.log")
        return open(p).read() if os.path.exists(p) else ""


def _q(s):
    return "'" + s.replace("'", "'\\''") + "'"


OK_NOISE = "Permission deny rule (.claude/settings.json): Write(**/.env*) is not matched"
UPDATE_FAIL = "/home/user/.local/bin/claude: cannot execute binary file"
AUTH_FAIL = "Invalid API key · Please run /login"
BARE_401 = "API error: 401 unauthorized (oauth)"

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("  PASS " if cond else "  FAIL ") + name + (("  — " + str(detail)) if detail and not cond else ""))


def case(fn):
    tmp = tempfile.mkdtemp(prefix="hch.")
    try:
        fn(Env(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("성공 경로")

    def t1(e):
        e.set_probe((0, OK_NOISE))
        e.run()
        check("rc=0 이면 경고가 섞여 있어도 무경보", e.notifications() == [], e.notifications())
        check("rc=0 이면 상태파일 없음", e.state() is None)
    case(t1)

    print("업데이트 교체 창 (이번 오탐)")

    def t2(e):
        e.set_probe((1, UPDATE_FAIL), (1, UPDATE_FAIL))
        e.run()
        check("1회차 즉시실패 2회는 무경보", e.notifications() == [], e.notifications())
        st = e.state()
        check("count=1 로 기록만", st and st.get("count") == "1", st)
        check("로그인으로 라벨하지 않는다", st and st.get("class") == "exec", st)
    case(t2)

    def t3(e):
        e.set_probe((1, UPDATE_FAIL))
        e.run()
        e.run()   # 두 번째 회차
        n = e.notifications()
        check("연속 2회차면 경보 1건", len(n) == 1, n)
        # 문구가 "로그인 만료인지는 확인 안 됐다" 이므로 단어 포함 여부로 보면 안 된다.
        # 판정 기준은 "재로그인하라고 시키는가" 다.
        check("문구는 미분류 — 재로그인을 지시하지 않는다",
              n and "재로그인" not in n[0] and "확인 안 됐다" in n[0], n)
        e.run()   # 세 번째 회차
        check("이미 알렸으면 재알림 없음", len(e.notifications()) == 1, e.notifications())
    case(t3)

    def t4(e):
        e.set_probe((1, UPDATE_FAIL))
        e.run(); e.run()
        e.set_probe((0, OK_NOISE))
        e.run()
        n = e.notifications()
        check("경보 후 복구되면 해소 알림", len(n) == 2 and "복귀" in n[1], n)
        check("복구 후 상태파일 제거", e.state() is None)
    case(t4)

    print("인증 확정")

    def t5(e):
        e.set_probe((1, AUTH_FAIL), (1, AUTH_FAIL))
        e.run()
        n = e.notifications()
        check("같은 회차 2회 인증 실패면 즉시 경보", len(n) == 1, n)
        check("문구에 재로그인 필요", n and "재로그인" in n[0], n)
        st = e.state()
        check("class=auth 로 기록", st and st.get("class") == "auth", st)
    case(t5)

    def t6(e):
        e.set_probe((1, BARE_401), (1, BARE_401))
        e.run()
        check("401·oauth 단독은 인증 확정이 아니다 — 1회차 무경보", e.notifications() == [],
              e.notifications())
        st = e.state()
        check("미분류로 떨어진다", st and st.get("class") == "other", st)
    case(t6)

    def t7(e):
        e.set_probe((1, AUTH_FAIL), (1, UPDATE_FAIL))
        e.run()
        check("두 번째가 인증이 아니면 즉시 경보 안 함", e.notifications() == [], e.notifications())
    case(t7)

    print("회차 내 재확인·타임아웃")

    def t8(e):
        e.set_probe((1, UPDATE_FAIL), (0, OK_NOISE))
        e.run()
        check("실패 뒤 재확인이 성공하면 무경보", e.notifications() == [], e.notifications())
        check("카운터도 안 남는다", e.state() is None, e.state())
    case(t8)

    def t9(e):
        e.set_probe((124, ""))
        e.run()
        st = e.state()
        check("rc=124 는 timeout 분류", st and st.get("class") == "timeout", st)
        check("timeout 1회차는 무경보", e.notifications() == [], e.notifications())
    case(t9)

    def t10(e):
        e.set_probe((1, UPDATE_FAIL))
        e.run()
        e.set_probe((0, OK_NOISE))
        e.run()
        check("1회 실패 후 성공이면 경보도 해소알림도 없음", e.notifications() == [], e.notifications())
        e.set_probe((1, UPDATE_FAIL))
        e.run()
        check("카운터가 리셋돼 다시 1회차부터", e.state().get("count") == "1", e.state())
    case(t10)

    print("옛 상태파일 이관·마스킹")

    def t11(e):
        legacy = os.path.join(e.tmp, "home", ".cache", "home-claude-health.state")
        with open(legacy, "w") as f:
            f.write("down\n")
        e.set_probe((0, OK_NOISE))
        e.run()
        n = e.notifications()
        check("옛 down 상태에서 성공하면 해소 알림 1건", len(n) == 1 and "복귀" in n[0], n)
        check("옛 상태파일 정리", not os.path.exists(legacy))
    case(t11)

    def t12(e):
        secret = "sk-ant-oat01-" + "A" * 60
        e.set_probe((1, "auth failed with token " + secret))
        e.run(); e.run()
        blob = " ".join(e.notifications()) + e.probe_log()
        check("토큰성 문자열은 알림·로그에 원문 노출 안 됨", secret not in blob, blob[:120])
    case(t12)

    print("머신 가드")

    def t13(e):
        cfg = os.path.join(e.tmp, "home", ".config")
        os.makedirs(cfg, exist_ok=True)
        with open(os.path.join(cfg, "claude-machine-id"), "w") as f:
            f.write("peer\n")
        e.set_probe((1, AUTH_FAIL), (1, AUTH_FAIL))
        e.run(); e.run()
        check("home 이 아니면 probe 도 알림도 없다", e.notifications() == [] and e.state() is None,
              str((e.notifications(), e.state())))
    case(t13)

    def t14(e):
        # 마커가 없으면 fail-open — 돌긴 돈다. 다만 남의 이름으로 보고하지 않는지 본다.
        e.set_probe((1, AUTH_FAIL), (1, AUTH_FAIL))
        e.run()
        n = e.notifications()
        import socket
        check("마커 없으면 실행은 된다(fail-open)", len(n) == 1, str(n))
        check("라벨이 home 이 아니라 실제 호스트명",
              n and n[0].startswith("[%s health]" % socket.gethostname().split(".")[0]), str(n))
    case(t14)

    print("수신처")

    def t15(e):
        # 기본 수신처는 peer 프로젝트 세션이 아니라 peer-dotfiles 다. 여기가 틀리면
        # 경보가 그걸 처리할 맥락이 없는 세션에 떨어지고 사람이 손으로 옮겨야 한다.
        e.set_probe((1, AUTH_FAIL), (1, AUTH_FAIL))
        e.run()
        s = e.notify_sessions()
        check("경보는 peer-dotfiles 로 간다", s == ["peer-dotfiles"], str(s))
    case(t15)

    ok = all(results)
    print("\n%d/%d PASS" % (sum(results), len(results)))
    print("ALL PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
