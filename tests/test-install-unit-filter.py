#!/usr/bin/env python3
"""install.sh 의 유닛 필터가 설정대로 가르는지 고정한다.

어느 유닛이 어느 기계 전용인지를 코드에 기계 이름으로 박으면, 그 이름을 쓰지 않는
기계에서는 아무것도 못 가른다. 그래서 그 판정을 `~/.config/claude-unit-hosts` 에서
읽는다 — 한 줄에 "<유닛 앞가지> <기계 이름>".

여기서 세는 것은 넷이다.
⑴ 설정이 없으면 전부 심는다(기계가 하나면 걸러낼 것이 없다)
⑵ 적힌 기계에서는 심고, 다른 기계에서는 안 심는다
⑶ 목록에 없는 유닛은 어느 기계에서든 심는다
⑷ 빈 줄과 주석은 판정에 안 쓴다 — 안 거르면 그 줄이 유닛 앞가지로 읽혀서, 이름이 빈
   앞가지에 모든 유닛이 걸린다

함수만 떼어 돌린다. install.sh 를 통째로 돌리면 이 기계의 홈에 실제로 링크를 심는다.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "install.sh"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


check("install.sh 가 있다", SRC.exists(), str(SRC))
if not SRC.exists():
    print("\n0/1 passed")
    sys.exit(1)

body = SRC.read_text(encoding="utf-8")
m = re.search(r"^unit_allowed_here\(\) \{.*?^\}", body, re.S | re.M)
check("unit_allowed_here 함수가 있다", m is not None)
if not m:
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(1)
func = m.group(0)


def ask(unit, machine, hosts_lines):
    """hosts_lines 가 None 이면 설정 파일 자체를 안 만든다. (rc, 출력)"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        hosts = Path(td) / "claude-unit-hosts"
        if hosts_lines is not None:
            hosts.write_text(hosts_lines, encoding="utf-8")
        script = (
            "set -u\n"
            f'UNIT_HOSTS="{hosts}"\n'
            f'MACHINE_ID="{machine}"\n'
            f"{func}\n"
            f'unit_allowed_here "{unit}" && echo ALLOW || echo DENY\n'
        )
        p = subprocess.run(["bash", "-c", script], capture_output=True,
                           text=True, timeout=30)
        return p.stdout.strip(), p.stderr.strip()


CONF = (
    "# 어느 유닛을 어느 기계에만 심을지\n"
    "\n"
    "claude-daily-report- alpha\n"
    "home-infra-watch beta\n"
)

out, err = ask("claude-daily-report-dispatch.service", "beta", None)
check("설정이 없으면 심는다", out == "ALLOW", f"out={out} err={err}")

out, err = ask("claude-daily-report-dispatch.service", "alpha", CONF)
check("적힌 기계에서는 심는다", out == "ALLOW", f"out={out} err={err}")

out, err = ask("claude-daily-report-dispatch.timer", "beta", CONF)
check("다른 기계에서는 안 심는다", out == "DENY", f"out={out} err={err}")

out, err = ask("home-infra-watch.service", "beta", CONF)
check("두 번째 줄도 읽는다", out == "ALLOW", f"out={out} err={err}")

out, err = ask("claude-watchdog.service", "alpha", CONF)
check("목록에 없는 유닛은 심는다", out == "ALLOW", f"out={out} err={err}")

out, err = ask("claude-watchdog.service", "beta", CONF)
check("목록에 없는 유닛은 어느 기계에서든 심는다", out == "ALLOW", f"out={out} err={err}")

# 주석·빈 줄을 안 거르면 빈 앞가지가 모든 유닛에 걸려서, 설정에 적지도 않은 유닛이
# 통째로 안 심긴다. 그 상태는 아무 오류도 안 내므로 사람이 모른다.
out, err = ask("claude-watchdog.service", "alpha",
               "\n\n# 주석만 있는 설정\n")
check("빈 줄과 주석만 있으면 전부 심는다", out == "ALLOW", f"out={out} err={err}")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
