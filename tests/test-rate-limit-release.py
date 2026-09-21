#!/usr/bin/env python3
"""한도 차단·해제 경로 계약 검사 — 진짜 한도를 기다리지 않고 합성 HOME 으로 잰다.

재현하는 사고(2026-08-28 옆 기계): 캐시가 6시간 전에 얼었고 그 안의 7일 창은 리셋이
아직 미래라 99% 가 그대로 살아 있었다. 옛 계산은 그걸 근거로 계속 차단했고, 막히니
턴이 없고, 턴이 없으니 캐시가 안 갱신되는 순환에 6시간 갇혔다.

지키는 것:
  1. 도구 차단 훅은 낡은 캐시로 차단하지 않는다(순환의 입구를 막는다).
  2. 복구 스크립트는 낡은 캐시를 해제 근거로도 쓰지 않는다 — 큐가 있으면 실측한다.
  3. 실측이 성공해야 해제다. 실패하면 큐를 그대로 두고 백오프한다.
  4. 큐가 없으면 실측하지 않는다(공짜가 아니다).
  5. 신선한 관측이 임계를 넘으면 그대로 차단이다.

가짜 claude 를 $HOME/.local/bin 에 놓아 실측 성공·실패를 주입한다. 네트워크·실제
계정을 쓰지 않는다.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

# 저장소 뿌리를 이 파일 위치에서 찾는다. 예전엔 홈 아래 고정 경로였는데,
# 그러면 다른 자리에 받은 사람은 그대로 못 돌린다.
REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
# 도는 스크립트를 제자리에서 고치지 않으려고 새 파일로 먼저 검사할 수 있게 열어둔다.
RECOVERY = os.environ.get("RECOVERY_SCRIPT") or os.path.join(
    REPO, "bin", "rate-limit-recovery")
GUARD = os.path.join(REPO, "claude", "hooks", "rate-limit-guard.sh")
PROMPT_GUARD = os.path.join(REPO, "claude", "hooks", "rate-limit-prompt-guard.sh")
FAILED = []


def check(name, got, want, detail=""):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s (%r, 기대 %r) %s" % (name, got, want, detail))
        FAILED.append(name)


class Home:
    """합성 HOME. 캐시·큐·가짜 claude 를 원하는 상태로 놓는다."""

    def __init__(self, cache_age, fh, sd, queue=False, probe_rc=None):
        self.dir = tempfile.mkdtemp(prefix="ratelimit-test-")
        now = time.time()
        state = os.path.join(self.dir, ".claude", "state")
        os.makedirs(state)
        with open(os.path.join(self.dir, ".claude", "statusline_cache.json"), "w") as f:
            json.dump({"_cached_at": now - cache_age,
                       "rate_limits": {"five_hour": fh, "seven_day": sd}}, f)
        qdir = os.path.join(state, "rate-limit-queue")
        os.makedirs(qdir)
        if queue:
            with open(os.path.join(qdir, "testproj.jsonl"), "w") as f:
                f.write('{"id": "m1", "text": "밀린 메시지"}\n')
        if probe_rc is not None:
            bindir = os.path.join(self.dir, ".local", "bin")
            os.makedirs(bindir)
            fake = os.path.join(bindir, "claude")
            with open(fake, "w") as f:
                f.write("#!/bin/bash\n"
                        + ("echo pong\nexit 0\n" if probe_rc == 0
                           else "echo 'rate limit' >&2\nexit %d\n" % probe_rc))
            os.chmod(fake, 0o755)

    def env(self):
        e = dict(os.environ)
        e["HOME"] = self.dir
        # 세션 이름을 비워두면 안 된다. 가드는 이름이 없을 때 마지막 수단으로
        # /proc/self/cgroup 을 읽는데, 이 검사가 진짜 세션 안에서 도니 거기서
        # 그 세션 이름이 나온다. 그러면 합성 HOME 을 지나쳐 systemd 의
        # WorkingDirectory 로 진짜 봇 설정을 찾아가 운영 채팅에 알림을 쏜다
        # (2026-09-09 실사고: 검사 한 번에 알림 3건이 운영자에게 나갔다).
        # 실재하지 않는 이름을 박아 그 경로를 끊는다. 어떤 검사도 이름 유도
        # 자체를 판정하지 않으므로 의도는 그대로다. 보조 조치로 부모가 들고
        # 있을 수 있는 토큰도 뺀다.
        e["CLAUDE_SESSION_NAME"] = "ratelimit-test"
        e.pop("TELEGRAM_BOT_TOKEN", None)
        e.pop("TELEGRAM_CHAT_ID", None)
        return e

    def log(self):
        p = os.path.join(self.dir, ".claude", "state", "rate-limit-queue", "recovery.log")
        try:
            with open(p) as f:
                return f.read()
        except OSError:
            return ""

    def has(self, *parts):
        return os.path.exists(os.path.join(self.dir, ".claude", "state", *parts))

    def run_recovery(self):
        return subprocess.run(["bash", RECOVERY], env=self.env(),
                              capture_output=True, text=True, timeout=60).returncode

    def run_guard(self, tool="Bash"):
        payload = json.dumps({"tool_name": tool, "cwd": self.dir})
        return subprocess.run(["bash", GUARD], input=payload, env=self.env(),
                              capture_output=True, text=True, timeout=60).returncode

    def run_guard_outside_unit(self, tool="Bash"):
        """유닛 밖 임시 세션(수신 경로 없음)으로 가드를 돌린다.

        가드는 CLAUDE_SESSION_NAME 이 비면 /proc/self/cgroup 에서 claude-*.service 를
        찾는데, 이 검사 자체가 진짜 세션 안에서 도니 거기서 그 세션 이름이 나온다.
        그래서 이름만 비우면 유닛 밖 판정이 안 된다 — 일회성 scope 로 감싸 cgroup 에서
        claude-*.service 가 사라지게 한 뒤 돌린다."""
        e = self.env()
        e["CLAUDE_SESSION_NAME"] = ""
        payload = json.dumps({"tool_name": tool, "cwd": self.dir})
        return subprocess.run(
            ["systemd-run", "--user", "--scope", "--quiet", "bash", GUARD],
            input=payload, env=e, capture_output=True, text=True, timeout=60).returncode

    def markers(self):
        """큐에 쌓인 재개 검토 신호 줄 수(보관본 제외)."""
        qdir = os.path.join(self.dir, ".claude", "state", "rate-limit-queue")
        n = 0
        for name in os.listdir(qdir):
            if not name.endswith(".jsonl") or name.endswith(".flushed.jsonl"):
                continue
            with open(os.path.join(qdir, name)) as f:
                for line in f:
                    try:
                        if json.loads(line).get("cause") == "rate_limit_blocked":
                            n += 1
                    except ValueError:
                        pass
        return n

    def run_prompt_guard(self, prompt="안녕", automated=False):
        e = self.env()
        e["CLAUDE_AUTOMATED"] = "1" if automated else "0"
        payload = json.dumps({"prompt": prompt, "cwd": self.dir})
        return subprocess.run(["bash", PROMPT_GUARD], input=payload, env=e,
                              capture_output=True, text=True, timeout=60).returncode

    def queued_bytes(self):
        """큐에 쌓인 총 바이트. 훅이 쓰는 프로젝트 이름은 세션 키 규칙으로 정해지므로
        이름을 맞히지 않고 디렉토리 전체를 센다(보관본 .flushed 는 제외)."""
        qdir = os.path.join(self.dir, ".claude", "state", "rate-limit-queue")
        total = 0
        for name in os.listdir(qdir):
            if name.endswith(".jsonl") and not name.endswith(".flushed.jsonl"):
                total += os.path.getsize(os.path.join(qdir, name))
        return total

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def frozen(**kw):
    """6시간 전에 언 캐시. 5시간 창 리셋은 지났고 7일 창 리셋은 미래(사고 그대로)."""
    now = time.time()
    return Home(6 * 3600,
                {"used_percentage": 99, "resets_at": now - 3600},
                {"used_percentage": 99, "resets_at": now + 2 * 86400}, **kw)


def fresh(pct5, pct7=2, **kw):
    now = time.time()
    return Home(60,
                {"used_percentage": pct5, "resets_at": now + 600},
                {"used_percentage": pct7, "resets_at": now + 86400}, **kw)


def main():
    print("도구 차단 훅")
    h = frozen()
    try:
        check("얼어붙은 캐시로는 차단하지 않는다", h.run_guard(), 0)
    finally:
        h.close()
    h = fresh(95)
    try:
        check("신선한 5시간 초과는 차단", h.run_guard(), 2)
    finally:
        h.close()
    h = fresh(10, pct7=99)
    try:
        check("신선한 7일 초과도 차단", h.run_guard(), 2)
    finally:
        h.close()
    h = fresh(10)
    try:
        check("임계 아래는 통과", h.run_guard(), 0)
    finally:
        h.close()

    print("차단 시 재개 검토 신호 (깨울 근거를 큐에 한 칸 남기는가)")
    # 재현하는 사고(2026-09-20 session-b): 첫 차단 도구가 Bash 라 마커가 안 남았고,
    # 복구 타이머는 큐가 빈 세션을 건너뛰므로 한도가 풀려도 아무도 깨우지 않았다.
    h = fresh(95)
    try:
        h.run_guard(tool="Bash")
        check("Bash 가 막혀도 신호를 남긴다", h.markers(), 1)
    finally:
        h.close()
    h = fresh(95)
    try:
        h.run_guard(tool="Read")
        check("Read 가 막혀도 신호를 남긴다", h.markers(), 1)
    finally:
        h.close()
    h = fresh(95)
    try:
        h.run_guard(tool="ScheduleWakeup")
        check("재무장 도구도 그대로 남긴다", h.markers(), 1)
    finally:
        h.close()
    h = fresh(95)
    try:
        for tool in ("Bash", "Read", "Edit", "ScheduleWakeup"):
            h.run_guard(tool=tool)
        check("한 차단 구간에서 몇 번 막혀도 한 칸", h.markers(), 1)
    finally:
        h.close()
    h = fresh(10)
    try:
        h.run_guard(tool="Bash")
        check("차단이 아니면 남기지 않는다", h.markers(), 0)
    finally:
        h.close()
    h = fresh(95)
    try:
        h.run_guard(tool="mcp__plugin_telegram_telegram__reply")
        check("허용 도구는 남기지 않는다", h.markers(), 0)
    finally:
        h.close()
    h = fresh(95)
    try:
        # 수신 경로가 없는 세션에 남기면 복구 타이머가 깨울 수 없는 큐를 매번 헛돈다.
        check("유닛 밖 임시 세션도 차단은 한다", h.run_guard_outside_unit(), 2)
        check("유닛 밖 임시 세션에는 남기지 않는다", h.markers(), 0)
    finally:
        h.close()

    print("프롬프트 훅 (들어오는 메시지를 큐에 넣을지)")
    h = frozen()
    try:
        check("얼어붙은 캐시로는 큐잉하지 않는다", h.run_prompt_guard(), 0)
    finally:
        h.close()
    h = fresh(95)
    try:
        check("신선한 차단이면 큐잉하고 막는다", h.run_prompt_guard(), 2)
        check("큐에 실제로 쌓인다", h.queued_bytes() > 0, True)
    finally:
        h.close()
    h = fresh(95)
    try:
        # 해제 실측 프로브가 자기 자신을 큐에 넣어버리면 안 된다.
        check("자동 호출은 큐잉 대상이 아니다", h.run_prompt_guard(automated=True), 0)
    finally:
        h.close()

    print("복구 — 모름 + 큐 있음")
    h = frozen(queue=True, probe_rc=0)
    try:
        h.run_recovery()
        check("실측한다", "probe" in h.log(), True, h.log()[:120])
        check("성공하면 해제로 본다", "probe 성공" in h.log(), True, h.log()[:120])
        check("성공 뒤 백오프 상태가 남지 않는다", h.has("rate-limit-probe-next"), False)
    finally:
        h.close()

    h = frozen(queue=True, probe_rc=1)
    try:
        h.run_recovery()
        check("실패하면 실패로 기록", "probe 실패" in h.log(), True, h.log()[:120])
        check("백오프 상태를 남긴다", h.has("rate-limit-probe-next"), True)
        qpath = os.path.join(h.dir, ".claude", "state", "rate-limit-queue", "testproj.jsonl")
        check("큐를 건드리지 않는다", os.path.getsize(qpath) > 0, True)
        # 두 번째 tick 은 백오프 중이라 실측하지 않는다.
        before = h.log().count("probe")
        h.run_recovery()
        check("백오프 중에는 다시 찌르지 않는다", h.log().count("probe"), before)
    finally:
        h.close()

    print("복구 — 실측하지 않아야 하는 경우")
    h = frozen(queue=False, probe_rc=0)
    try:
        h.run_recovery()
        check("큐가 없으면 실측 안 함", "probe" in h.log(), False, h.log()[:120])
    finally:
        h.close()

    h = fresh(10, queue=True, probe_rc=0)
    try:
        h.run_recovery()
        check("신선하면 실측 안 함", "probe" in h.log(), False, h.log()[:120])
    finally:
        h.close()

    h = fresh(95, queue=True, probe_rc=0)
    try:
        h.run_recovery()
        check("신선한 차단이면 실측 안 함", "probe" in h.log(), False, h.log()[:120])
        check("차단 플래그를 세운다", h.has("rate-limit-blocked.flag"), True)
    finally:
        h.close()

    print("규칙이 한 벌인가")
    hits = []
    for rel in ("bin/rate-limit-recovery", "claude/hooks/rate-limit-guard.sh",
                "claude/hooks/rate-limit-prompt-guard.sh"):
        with open(os.path.join(REPO, rel)) as f:
            body = f.read()
        if "def effective(" in body:
            hits.append(rel)
    check("세 소비자에 계산 사본이 없다", hits, [])

    if FAILED:
        print("\n실패 %d건: %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("\n전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
