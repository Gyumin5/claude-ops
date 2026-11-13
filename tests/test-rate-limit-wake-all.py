#!/usr/bin/env python3
"""계정을 갈아끼운 순간 큐가 빈 세션까지 깨우는지 본다.

깨우기 신호(trigger:queue-flush)는 큐가 비어 있으면 prompt-guard 가 조용히 버린다.
그래서 2026-10-21 20:41 교체에서 밀린 메시지가 셋 있던 velocity 만 깨어나고 나머지
세션은 그대로 멈춰 있었다. 운영자 지시로 그 자리를 바꾼다 — 교체
시점에 살아 있는 세션 유닛마다 합성 레코드 한 줄을 넣고, 기존 큐 경로로 내보낸다.

고정하는 것 넷.
  1 큐가 빈 세션에 깨우기 한 줄이 들어간다. 그 한 줄은 flush 훅이 읽는 형식 그대로다.
  2 이미 밀린 큐가 있는 세션에는 안 넣는다 — 사람이 보낸 메시지 뒤에 안내문이 끼면
    처리 순서가 흐려진다.
  3 세션이 아닌 유닛(통제 봇·타이머)에는 안 넣는다.
  4 깨우기 한 줄만 든 큐는 사람에게 알리지 않는다. 안 그러면 교체 한 번에 같은 알림이
    세션 수만큼 간다.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RECOVERY = REPO / "bin" / "rate-limit-recovery"

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


FAKE_SYSTEMCTL = r"""#!/bin/bash
# 세션 유닛 둘(claude-aaa, claude-bbb) 과 세션이 아닌 유닛 하나(claude-control-bot).
# 작업 디렉토리는 FAKE_WD_ROOT 아래 세션 이름 폴더로 준다 — 소유자 검증이 통과해야
# 사람 알림 경로까지 실제로 지나간다.
unit=""
for a in "$@"; do
  case "$a" in claude-*.service) unit="$a" ;; esac
done
proj="${unit#claude-}"; proj="${proj%.service}"
case "$*" in
  *"list-unit-files"*)
      echo "claude-aaa.service enabled enabled"
      echo "claude-bbb.service enabled enabled"
      echo "claude-control-bot.service enabled enabled"
      ;;
  *"cat claude-control-bot.service"*)
      echo "ExecStart=/home/x/.local/bin/claude-control-bot" ;;
  *"cat claude-"*)
      echo "ExecStart=/home/x/.local/bin/claude-service-exec $proj" ;;
  *"show"*"LoadState"*) echo "loaded" ;;
  *"show"*"WorkingDirectory"*)
      if [ -d "$FAKE_WD_ROOT/$proj" ]; then
          echo "WorkingDirectory=$FAKE_WD_ROOT/$proj"
      else
          echo "WorkingDirectory="
      fi
      ;;
  *) : ;;
esac
exit 0
"""

FAKE_CURL = """#!/bin/bash
echo "$*" >> "$FAKE_CURL_LOG"
exit 0
"""


def sandbox(td, queues=None):
    """가짜 HOME 을 세우고 (home, state, env) 를 준다. 계정 교체 직전 상태로 둔다."""
    home = Path(td) / "home"
    state = home / ".claude" / "state"
    qdir = state / "rate-limit-queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"accountUuid": "uuid-A", "emailAddress": "x@y"}}),
        encoding="utf-8")
    (home / ".claude" / "statusline_cache.json").write_text(
        json.dumps({"_cached_at": time.time(), "_account": "uuid-A",
                    "rate_limits": {"five_hour": {"used_percentage": 95,
                                                  "resets_at": time.time() + 3600}}}),
        encoding="utf-8")
    # 계정 식별자는 그대로지만 로그인 적용 표시가 새로 찍혔다 = 릴레이 로그인 경로.
    (state / "rate-limit-account-seen").write_text("uuid-A\n", encoding="utf-8")
    (state / "rate-limit-login-applied").write_text("1790000000\n", encoding="utf-8")
    for proj, records in (queues or {}).items():
        (qdir / f"{proj}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8")

    # 세션별 작업 디렉토리 + 전용 .env — 있어야 소유자 검증이 ok 로 떨어진다.
    wd_root = Path(td) / "wd"
    for proj in ("aaa", "bbb"):
        envd = wd_root / proj / ".claude" / "telegram"
        envd.mkdir(parents=True, exist_ok=True)
        (envd / ".env").write_text(
            "TELEGRAM_BOT_" + "TOKEN=fake-" + proj + "\n"
            "TELEGRAM_BOT_USERNAME=fake_" + proj + "_bot\n", encoding="utf-8")

    binp = Path(td) / "bin"
    binp.mkdir(exist_ok=True)
    for name, body in (("systemctl", FAKE_SYSTEMCTL), ("curl", FAKE_CURL)):
        p = binp / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    curl_log = Path(td) / "curl.log"

    env = dict(os.environ, HOME=str(home),
               PATH=f"{binp}:{os.environ['PATH']}",
               FAKE_WD_ROOT=str(wd_root),
               FAKE_CURL_LOG=str(curl_log),
               # 방 번호는 추적 밖 설정에서 읽는다. 시험 HOME 에는 그 파일이 없으니
               # 지어낸 값을 환경변수로 넘긴다 — 없으면 알림 자체가 건너뛰어진다.
               CLAUDE_OPERATOR_CHAT_ID="1234509876",
               # 깨우기를 흩는 무작위 대기는 끈다 — 이 검사가 보는 것은 누구에게
               # 나갔나지 몇 초 뒤에 나갔나가 아니다. 흩는 동작 자체는
               # tests/test-flush-jitter.py 가 본다.
               CLAUDE_FLUSH_JITTER_MAX="0")
    env.pop("CLAUDE_BG_GUARD_OFF", None)
    return home, qdir, env, curl_log


def run(env):
    return subprocess.run(["bash", str(RECOVERY)], env=env,
                          capture_output=True, text=True, timeout=120)


def live(qdir, proj):
    f = qdir / f"{proj}.jsonl"
    if not f.exists():
        return []
    return [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ── 1. 큐가 빈 세션에 깨우기 한 줄이 들어간다 ───────────────────────────────
with tempfile.TemporaryDirectory(prefix="wake.") as td:
    home, qdir, env, curl_log = sandbox(td)
    run(env)

    aaa = live(qdir, "aaa")
    bbb = live(qdir, "bbb")
    check("빈 큐 세션 aaa 에 깨우기 한 줄이 들어간다", len(aaa) == 1, str(aaa))
    check("빈 큐 세션 bbb 에도 들어간다", len(bbb) == 1, str(bbb))
    check("세션이 아닌 유닛에는 안 넣는다",
          not (qdir / "control-bot.jsonl").exists())

    if aaa:
        r = aaa[0]
        need = {"id", "ts", "ts_unix", "project", "cause", "state", "retry_count", "prompt"}
        check("flush 훅이 읽는 칸을 전부 갖춘다", need <= set(r), str(sorted(r)))
        check("깨우기라고 표시한다", r.get("cause") == "account-switch-wake", str(r.get("cause")))
        check("프로젝트 이름이 유닛 이름에서 바로 온다", r.get("project") == "aaa", str(r.get("project")))
        check("시각이 지금이다", abs(int(r.get("ts_unix", 0)) - int(time.time())) < 120,
              str(r.get("ts_unix")))
        check("본문이 비어 있지 않다", len(r.get("prompt", "")) > 10, r.get("prompt", ""))

    log = (qdir / "recovery.log")
    txt = log.read_text(encoding="utf-8") if log.exists() else ""
    check("몇 개를 깨웠는지 기록에 남긴다", "action=wake-all woke=2" in txt, txt)

# ── 2. 이미 밀린 큐가 있으면 안 넣는다 ──────────────────────────────────────
with tempfile.TemporaryDirectory(prefix="wake.") as td:
    now = int(time.time())
    real = {"id": "x-1", "ts": "2026-10-21T20:32:14+09:00", "ts_unix": now,
            "project": "aaa", "cause": "5h_or_7d", "state": "pending",
            "retry_count": 0, "prompt": "사람이 보낸 메시지"}
    home, qdir, env, curl_log = sandbox(td, queues={"aaa": [real]})
    run(env)

    aaa = live(qdir, "aaa")
    check("밀린 큐가 있는 세션은 그대로 둔다",
          len(aaa) == 1 and aaa[0]["prompt"] == "사람이 보낸 메시지", str(aaa))
    check("그 옆 빈 세션은 깨운다", len(live(qdir, "bbb")) == 1)
    txt = (qdir / "recovery.log").read_text(encoding="utf-8")
    check("건너뛴 수도 기록에 남긴다", "woke=1 skipped=1" in txt, txt)

# ── 3. 만료된 레코드만 남은 큐는 빈 것으로 본다 ─────────────────────────────
with tempfile.TemporaryDirectory(prefix="wake.") as td:
    old = {"id": "x-old", "ts": "2026-10-20T01:00:00+09:00",
           "ts_unix": int(time.time()) - 30 * 3600, "project": "aaa",
           "cause": "5h_or_7d", "state": "pending", "retry_count": 0, "prompt": "옛 메시지"}
    home, qdir, env, curl_log = sandbox(td, queues={"aaa": [old]})
    run(env)
    aaa = live(qdir, "aaa")
    check("만료분만 있던 큐에도 깨우기가 들어간다",
          any(r.get("cause") == "account-switch-wake" for r in aaa), str(aaa))

# ── 4. 깨우기만 든 큐는 사람에게 안 알린다 ──────────────────────────────────
with tempfile.TemporaryDirectory(prefix="wake.") as td:
    home, qdir, env, curl_log = sandbox(td)
    run(env)
    txt = (qdir / "recovery.log").read_text(encoding="utf-8")
    check("소유자 검증을 통과한 상태로 알림 자리까지 갔다",
          "triggered=" in txt, txt)
    sent = curl_log.read_text(encoding="utf-8") if curl_log.exists() else ""
    check("교체 한 번에 세션 수만큼 알림이 가지 않는다",
          "sendMessage" not in sent, sent[:400])

# ── 5. 사람이 보낸 메시지가 든 큐는 그대로 알린다 (과잉 억제 방지) ──────────
with tempfile.TemporaryDirectory(prefix="wake.") as td:
    now = int(time.time())
    real = {"id": "x-2", "ts": "2026-10-21T20:32:14+09:00", "ts_unix": now,
            "project": "aaa", "cause": "5h_or_7d", "state": "pending",
            "retry_count": 0, "prompt": "사람이 보낸 메시지"}
    home, qdir, env, curl_log = sandbox(td, queues={"aaa": [real]})
    run(env)
    sent = curl_log.read_text(encoding="utf-8") if curl_log.exists() else ""
    check("밀린 메시지가 있던 세션에는 전처럼 알린다",
          "sendMessage" in sent, sent[:400])
    check("알린 것은 그 한 세션뿐이다", sent.count("sendMessage") == 1, sent[:400])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
