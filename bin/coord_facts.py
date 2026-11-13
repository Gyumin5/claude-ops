#!/usr/bin/env python3
"""조율 모음의 사실 쪽: 세션 파일에 적힌 말과 대조할 실제 상태를 모은다.

세션이 적은 값은 여기서 만든 값과 맞을 때만 "확인됨" 이 된다.
"""
import json
import os
import re
import subprocess


def _run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


def jobs():
    """돌고 있는 detach 잡: {유닛이름: {"session": 발신 세션, "cmd": 명령}}."""
    rc, out = _run(["systemctl", "--user", "list-units", "--all", "--plain",
                    "--no-legend", "claude-job-*"])
    res = {}
    if rc != 0:
        return None
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4 or parts[3] != "running":
            continue
        unit = parts[0]
        # 유닛 이름은 세션명과 슬러그가 하이픈으로 섞여 못 가른다 — 착수 때 각인한 환경값을 본다.
        _, env = _run(["systemctl", "--user", "show", "-p", "Environment", "--value", unit])
        m = re.search(r"CLAUDE_DETACH_ORIGIN_SESSION=(\S+)", env)
        _, ts = _run(["systemctl", "--user", "show", "-p", "ActiveEnterTimestamp", "--value", unit])
        res[unit] = {"session": m.group(1) if m else None, "since": ts.strip(),
                     "cmd": parts[4] if len(parts) > 4 else ""}
    return res


NOT_SESSIONS = {"control-bot"}


def sessions():
    """지금 떠 있는 세션: {세션명: 작업 디렉토리}. 손으로 든 목록 대신 유닛에서 읽는다.

    claude-<이름>.service 중 잡(claude-job-*)·템플릿 인스턴스(@)·봇은 세션이 아니다.
    """
    rc, out = _run(["systemctl", "--user", "list-units", "--plain", "--no-legend",
                    "--state=running", "claude-*.service"])
    if rc != 0:
        return None
    res = {}
    for line in out.splitlines():
        m = re.match(r"claude-([^@\s]+)\.service$", line.split()[0] if line.split() else "")
        if not m or m.group(1).startswith("job-") or m.group(1) in NOT_SESSIONS:
            continue
        _, wd = _run(["systemctl", "--user", "show", "-p", "WorkingDirectory", "--value", line.split()[0]])
        res[m.group(1)] = wd.strip()
    return res


def pr(repo, number):
    """PR 하나의 상태. 못 읽으면 None."""
    rc, out = _run(["gh", "pr", "view", str(number), "-R", repo,
                    "--json", "number,state,isDraft,title,reviewRequests"])
    if rc != 0:
        return None
    d = json.loads(out)
    d["reviewers"] = [r.get("login") or r.get("name") for r in d.pop("reviewRequests", [])]
    return d


def host():
    """이 머신 부하: 코어 수, 1분 부하, 여유 메모리(GiB)."""
    load1 = os.getloadavg()[0]
    avail = None
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                avail = int(line.split()[1]) / 1024 / 1024
    return {"cores": os.cpu_count(), "load1": round(load1, 1),
            "mem_avail_gib": round(avail, 1) if avail is not None else None}


if __name__ == "__main__":
    # 손으로 돌려보는 자리다. 볼 PR 은 인자로 받는다 — 특정 저장소·번호를 코드에
    # 박아 두면 그 하나가 닫히는 날부터 이 자리는 아무것도 안 재면서 계속 돈다.
    #   python3 coord_facts.py <소유자>/<저장소> <번호>
    import sys
    out = {"jobs": jobs(), "host": host()}
    if len(sys.argv) == 3:
        out["pr"] = pr(sys.argv[1], int(sys.argv[2]))
    print(json.dumps(out, ensure_ascii=False, indent=1))
