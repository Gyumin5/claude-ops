#!/usr/bin/env python3
"""claude-session-healthcheck 무진행 알림 테스트.

왜 있나: 2026-08-11 옆 기계 dotfiles 세션이 2시간 40분 멈췄는데 아무도 몰랐다. 조사해
보니 healthcheck 가 11:40 부터 12:56 까지 6회 정확히 잡아서 ~/.claude/state/auto-restart/
에 shadow 스냅샷을 남겼고, 그게 전부였다 — 사람에게 가는 경로가 없었다. 감지가 아니라
출력이 없던 것이다(history #56).

그래서 고정하는 것: 판정이 통과하면 사람에게 간다, 며칠 방치된 세션은 알리지 않는다,
같은 세션을 쿨다운 안에 두 번 알리지 않는다, 문구가 자기설명형이다.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
import time

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin",
                      "claude-session-healthcheck")

results = []


def check(name, cond, detail: object = ""):
    results.append(bool(cond))
    print(("  PASS " if cond else "  FAIL ") + name +
          (("  — " + str(detail)) if detail and not cond else ""))


def load(home):
    """HOME 을 갈아끼운 뒤 모듈을 새로 읽는다(경로 상수가 import 시점에 굳는다)."""
    os.environ["HOME"] = home
    loader = importlib.machinery.SourceFileLoader("hc", os.path.realpath(SCRIPT))
    spec = importlib.util.spec_from_loader("hc", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def payload(age_sec, pending=3, ev="queue-operation"):
    return {"assistant_age_sec": int(age_sec), "pending_after_assistant": pending,
            "last_event_type": ev}


def main():
    tmp = tempfile.mkdtemp(prefix="hang-alert.")
    real_home = os.environ.get("HOME")
    try:
        home = os.path.join(tmp, "home")
        os.makedirs(os.path.join(home, ".claude", "state"), exist_ok=True)
        hc = load(home)

        print("발송 판정")
        # 08-11 옆 기계 실제 스냅샷 값(11:40:15 KST, assistant_age 4823s, 큐 3건).
        check("실제 사고 값이면 알린다", hc.hang_alert_due("claude-dotfiles.service", 4823))
        check("같은 세션은 쿨다운 안에 다시 안 알린다",
              not hc.hang_alert_due("claude-dotfiles.service", 5723))
        check("다른 세션은 따로 센다", hc.hang_alert_due("claude-peer.service", 4823))

        print("상한")
        check("24시간 넘으면 방치된 세션으로 보고 안 알린다",
              not hc.hang_alert_due("claude-session-e.service", 51951 * 60))
        check("상한에 걸린 세션은 도장도 안 찍는다",
              not (hc.STATE_DIR / "hang-alert-claude-session-e.service.flag").exists())
        check("상한 바로 아래는 알린다",
              hc.hang_alert_due("claude-session-a.service", hc.HANG_ALERT_MAX_AGE_SEC - 60))

        print("쿨다운 만료")
        flag = hc.STATE_DIR / "hang-alert-claude-dotfiles.service.flag"
        old = time.time() - hc.HANG_ALERT_COOLDOWN_SEC - 60
        os.utime(flag, (old, old))
        check("쿨다운이 지나면 다시 알린다", hc.hang_alert_due("claude-dotfiles.service", 4823))

        print("문구")
        text = hc.hang_alert_text("peer", "dotfiles", payload(4823))
        check("관측·뜻·조치·볼 것 구조다",
              all(k in text for k in ("관측:", "뜻:", "조치:", "볼 것:")), text[:200])
        check("무응답 분을 적는다", "80분" in text, text[:120])
        check("큐 건수를 적는다", "3건" in text, text[:200])
        check("자동 개입 안 한다고 명시한다", "자동으로는 아무것도 안 한다" in text)
        check("굵게 표기를 쓰지 않는다", "**" not in text)
        check("슬래시로 시작하는 단어가 없다(텔레그램이 커맨드로 렌더링한다)",
              not any(w.startswith("/") for w in text.split()),
              [w for w in text.split() if w.startswith("/")])

        print("임계 상수")
        check("상한 24시간", hc.HANG_ALERT_MAX_AGE_SEC == 24 * 3600)
        check("쿨다운 6시간", hc.HANG_ALERT_COOLDOWN_SEC == 6 * 3600)
        check("자동 restart 는 여전히 shadow 기본",
              not (hc.SHADOW_FLAG).exists())
    finally:
        if real_home:
            os.environ["HOME"] = real_home
        shutil.rmtree(tmp, ignore_errors=True)

    ok = all(results)
    print("\n%d/%d PASS" % (sum(results), len(results)))
    print("ALL PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
