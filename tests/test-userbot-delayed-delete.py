#!/usr/bin/env python3
"""지연삭제 예약이 호출자 유닛이 끝난 뒤에도 사는지 본다.

대상은 bin/claude-userbot-send 의 spawn_supervised·schedule_delayed_delete 다.

고친 결함: 예약 일꾼을 subprocess.Popen(start_new_session=True) 로만 띄우면, 호출자가
Type=oneshot systemd 유닛일 때 그 유닛이 끝나는 순간 같이 죽는다. start_new_session 은
프로세스 세션만 떼어낼 뿐 cgroup 은 그대로고, systemd 기본값 KillMode=control-group 이
남은 프로세스를 전부 정리하기 때문이다. 그래서 큐 깨우기 메시지가 2026-07-09 이후
한 건도 안 지워졌다(albert 11·dotfiles 8·resume 12건 잔류).

텔레그램은 쓰지 않는다. 실행기와 Popen 을 대역으로 갈아 끼워 인자와 갈래만 본다.
마지막 두 검사만 실제 systemd 사용자 유닛을 쓰고, 사용자 버스가 없으면 건너뛴다 —
건너뛴 회차는 운영 경로를 확인한 증거로 치지 않는다.
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEND = REPO / "bin" / "claude-userbot-send"

PASS = FAIL = SKIP = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def skip(name, why):
    global SKIP
    SKIP += 1
    print(f"  SKIP  {name}  ({why})")


def load_module():
    spec = importlib.util.spec_from_loader(
        "userbot_send", importlib.machinery.SourceFileLoader("userbot_send", str(SEND)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Recorder:
    """subprocess.run 대역. 호출 인자를 모아 두고 정해진 결과를 돌려준다."""

    def __init__(self, rc=0, raises=None, stderr=""):
        self.calls = []
        self.rc = rc
        self.raises = raises
        self.stderr = stderr

    def __call__(self, cmd, **kw):
        self.calls.append((list(cmd), kw))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(cmd, self.rc, stdout="", stderr=self.stderr)


class PopenRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((list(cmd), kw))
        return None


m = load_module()
print("=== 지연삭제 예약 ===")

# 1. 정상 인계: systemd-run 이 rc=0 이면 대체 실행이 없어야 한다.
run, pop = Recorder(rc=0), PopenRecorder()
how, guaranteed = m.spawn_supervised(["/bin/true"], _runner=run, _popen=pop)
check("정상 인계는 독립 유닛으로 간다", (how, guaranteed) == ("systemd-run", True),
      f"{how} {guaranteed}")
check("정상 인계 때 떼어낸 자식을 안 띄운다", pop.calls == [], f"{len(pop.calls)}건")

cmd = run.calls[0][0]
kw = run.calls[0][1]
check("실행기 인자에 --user --collect --quiet 가 있다",
      all(f in cmd for f in ("--user", "--collect", "--quiet")), str(cmd[:6]))
check("유닛 이름을 명시한다", any(c.startswith("--unit=") for c in cmd), str(cmd[:6]))
check("HOME 을 명시적으로 넘긴다", any(c.startswith("--setenv=HOME=") for c in cmd),
      str(cmd[:8]))
check("운영 경로에 --wait·--pipe·--scope 를 안 붙인다",
      not any(c in ("--wait", "--pipe", "--scope") for c in cmd), str(cmd))
check("실행기 표준입력을 닫는다", kw.get("stdin") == subprocess.DEVNULL, str(kw.get("stdin")))
check("인계 확인은 제한시간 안에서만 기다린다",
      isinstance(kw.get("timeout"), (int, float)) and kw["timeout"] <= 10,
      str(kw.get("timeout")))
check("넘긴 명령이 그대로 보존된다", cmd[-1] == "/bin/true", str(cmd[-1]))

# 2. 유닛 이름은 호출마다 달라야 한다 — 겹치면 두 번째 인계가 거절된다.
run2 = Recorder(rc=0)
m.spawn_supervised(["/bin/true"], _runner=run2, _popen=PopenRecorder())
time.sleep(0.002)
run3 = Recorder(rc=0)
m.spawn_supervised(["/bin/true"], _runner=run3, _popen=PopenRecorder())
u2 = [c for c in run2.calls[0][0] if c.startswith("--unit=")][0]
u3 = [c for c in run3.calls[0][0] if c.startswith("--unit=")][0]
check("유닛 이름이 호출마다 다르다", u2 != u3, f"{u2} {u3}")

# 3. 실행기가 없으면 떼어낸 자식으로 떨어지고, 보장은 거짓이다.
run, pop = Recorder(raises=FileNotFoundError()), PopenRecorder()
how, guaranteed = m.spawn_supervised(["/bin/true"], _runner=run, _popen=pop)
check("실행기가 없으면 대체 실행한다", (how, guaranteed) == ("popen", False),
      f"{how} {guaranteed}")
check("대체 실행은 보장으로 치지 않는다", guaranteed is False)
check("대체 실행도 명령을 그대로 넘긴다", pop.calls and pop.calls[0][0] == ["/bin/true"],
      str(pop.calls))
check("대체 실행이 잠금 기술자를 안 물려준다",
      pop.calls and pop.calls[0][1].get("close_fds") is True, str(pop.calls[0][1]))

# 4. 실행기가 실패 코드를 내면 대체 실행한다.
run, pop = Recorder(rc=1, stderr="boom"), PopenRecorder()
how, guaranteed = m.spawn_supervised(["/bin/true"], _runner=run, _popen=pop)
check("실행기 실패면 대체 실행한다", (how, guaranteed) == ("popen", False), f"{how}")

# 5. 시간 초과는 인계 불명이다 — 유닛이 올라갔으면 대체 실행하지 않는다.
run = Recorder(raises=subprocess.TimeoutExpired(cmd="systemd-run", timeout=5))
pop = PopenRecorder()
how, guaranteed = m.spawn_supervised(
    ["/bin/true"], _runner=run, _popen=pop,
    _query=lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="loaded\n", stderr=""))
check("시간 초과라도 유닛이 올라갔으면 인계로 본다", (how, guaranteed) == ("systemd-run", True),
      f"{how} {guaranteed}")
check("그때 두 번 띄우지 않는다", pop.calls == [], f"{len(pop.calls)}건")

run = Recorder(raises=subprocess.TimeoutExpired(cmd="systemd-run", timeout=5))
pop = PopenRecorder()
how, guaranteed = m.spawn_supervised(
    ["/bin/true"], _runner=run, _popen=pop,
    _query=lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="not-found\n", stderr=""))
check("유닛이 없으면 그때는 대체 실행한다", (how, guaranteed) == ("popen", False), f"{how}")

# 6. 대체 실행마저 실패해도 예외를 밖으로 내보내지 않는다 — 발송은 이미 성공했다.
def boom(*a, **k):
    raise OSError("no fork")


how, guaranteed = m.spawn_supervised(["/bin/true"], _runner=Recorder(rc=1), _popen=boom)
check("예약이 통째로 실패해도 예외를 안 올린다", (how, guaranteed) == ("실패", False), f"{how}")

# 7. schedule_delayed_delete 가 워커 인자를 제대로 만든다.
run, pop = Recorder(rc=0), PopenRecorder()
m.schedule_delayed_delete(600, "@somebot", 4242, _runner=run, _popen=pop)
cmd = run.calls[0][0]
tail = cmd[cmd.index("--"):]
check("워커를 절대경로 인터프리터로 부른다", os.path.isabs(tail[1]), str(tail[1]))
check("워커 스크립트 경로가 절대경로다", os.path.isabs(tail[2]), str(tail[2]))
check("워커 인자가 지연·대상·메시지 아이디 순이다",
      tail[3:] == ["--_delayed-delete", "600", "@somebot", "4242"], str(tail[3:]))

# 8. 사용자 버스 환경을 호출 환경에 기대지 않는다.
saved = {k: os.environ.get(k) for k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")}
try:
    os.environ.pop("XDG_RUNTIME_DIR", None)
    os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
    env = m._user_bus_env()
    want = f"/run/user/{os.getuid()}"
    if os.path.isdir(want) and os.stat(want).st_uid == os.getuid():
        check("버스 변수가 없으면 자기 소유 경로로 채운다",
              env.get("XDG_RUNTIME_DIR") == want, str(env.get("XDG_RUNTIME_DIR")))
    else:
        skip("버스 변수가 없으면 자기 소유 경로로 채운다", "이 기계에 사용자 런타임 경로가 없다")
finally:
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v

# 9·10. 실제 유닛 — oneshot 부모가 끝난 뒤에도 자식이 사는가. 대조군은 옛 경로다.
def user_bus_ok():
    try:
        p = subprocess.run(["systemctl", "--user", "is-system-running"],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return False
    return p.returncode == 0 or "running" in p.stdout or "degraded" in p.stdout


print("=== 실제 유닛 (oneshot 부모 종료 뒤 생존) ===")
if not user_bus_ok():
    skip("독립 유닛 자식이 부모 oneshot 종료 뒤에도 산다", "사용자 systemd 버스 없음")
    skip("옛 떼어낸 자식은 부모 oneshot 종료와 함께 죽는다", "사용자 systemd 버스 없음")
else:
    with tempfile.TemporaryDirectory() as td:
        new_mark = os.path.join(td, "new")
        old_mark = os.path.join(td, "old")
        runner = os.path.join(td, "runner.py")
        with open(runner, "w") as f:
            f.write(
                "import importlib.machinery, importlib.util, subprocess, sys\n"
                "spec = importlib.util.spec_from_loader('ub',\n"
                "    importlib.machinery.SourceFileLoader('ub', sys.argv[1]))\n"
                "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
                "argv = ['/bin/bash', '-c', 'sleep 3; echo alive > ' + sys.argv[2]]\n"
                "if sys.argv[3] == 'new':\n"
                "    mod.spawn_supervised(argv, unit_hint='claude-userbot-del-test')\n"
                "else:\n"
                "    subprocess.Popen(argv, start_new_session=True, close_fds=True,\n"
                "        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
                "        stderr=subprocess.DEVNULL)\n")
        for tag, mark in (("new", new_mark), ("old", old_mark)):
            subprocess.run(
                ["systemd-run", "--user", "--collect", "--quiet",
                 f"--unit=claude-userbot-del-parent-{tag}-{os.getpid()}",
                 "--", sys.executable, runner, str(SEND), mark, tag],
                capture_output=True, text=True, timeout=20)
        time.sleep(7)
        check("독립 유닛 자식이 부모 oneshot 종료 뒤에도 산다", os.path.exists(new_mark),
              "표식 없음 — 고친 경로가 안 산다")
        check("옛 떼어낸 자식은 부모 oneshot 종료와 함께 죽는다", not os.path.exists(old_mark),
              "대조군이 살았다 — 이 검사가 변별력을 잃었다")

print(f"\nPASS {PASS}  FAIL {FAIL}  SKIP {SKIP}")
sys.exit(1 if FAIL else 0)
