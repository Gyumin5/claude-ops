#!/usr/bin/env python3
"""home-infra-watch 테스트.

고정하는 것: 감시가 오탐(정상 배포 시차·부팅 직후·짧은 실행중)에 안 울고,
미탐(하한 미달·타이머 정지·서비스 실패)에는 정확히 한 번 운다.
로그 mtime 같은 중간 프록시가 아니라 발화 시각과 종료 결과로 판정한다.
"""
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

WATCH = str(Path(__file__).resolve().parent.parent / "bin" / "home-infra-watch")
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"\n      {detail}" if not cond and detail else ""))


def fake_systemctl(tmp, enabled="enabled", active="active", last_trigger="",
                   svc_state="inactive", result="success", status="0", started=""):
    """systemctl 대역. 인자 모양으로 분기해 미리 정한 값을 뱉는다."""
    p = Path(tmp) / "fake-systemctl"
    p.write_text(f"""#!/bin/bash
# $1 은 --user, 나머지가 실제 인자
shift
case "$1" in
  is-enabled) echo "{enabled}";;
  is-active)  echo "{active}";;
  show)
    case "$2" in
      todo-rearm.timer)   echo "LastTriggerUSec={last_trigger}";;
      todo-rearm.service) echo "Result={result}"
                          echo "ExecMainStatus={status}"
                          echo "ActiveState={svc_state}"
                          echo "ExecMainStartTimestamp={started}";;
    esac;;
esac
exit 0
""", encoding="utf-8")
    p.chmod(0o755)
    return f"{p} --user"


def usage_json(now, *, pct=10.0, age_s=600, resets=None, total=1234):
    """codex-usage --json 대역이 뱉을 본문. 관측 시각은 now 기준 상대값이다."""
    import datetime
    kst = datetime.timezone(datetime.timedelta(hours=9))
    observed = datetime.datetime.fromtimestamp(now - age_s, kst).isoformat()
    return json.dumps({
        "hours": 24,
        "hourly": [{"hour_kst": observed, "합계": total}],
        "rate_limit": {
            "observed_kst": observed,
            "primary": {"used_percent": pct, "window_minutes": 10080,
                        "resets_at": resets if resets is not None else now + 3600},
        },
    }, ensure_ascii=False)


def make_repo_pair(tmp):
    """origin 하나와 그것을 복제한 홈 작업본 하나. 진짜 git 이라 조상 판정이 실물이다."""
    root = Path(tmp) / "repos"
    origin, home = root / "origin.git", root / "home"
    root.mkdir(parents=True, exist_ok=True)
    g = dict(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
             GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    env = {**os.environ, **g}
    def sh(cmd, cwd):
        subprocess.run(cmd, shell=True, cwd=str(cwd), env=env,
                       capture_output=True, text=True, check=True, timeout=60)
    # `git init -b` 는 2.28 부터라 이 기계 판본에서 죽는다. 기본 가지 이름에 기대지
    # 않고 양쪽 HEAD 를 직접 master 로 못박는다.
    sh(f"git init -q --bare {shlex.quote(str(origin))}", root)
    sh(f"git symbolic-ref HEAD refs/heads/master", origin)
    sh(f"git clone -q {shlex.quote(str(origin))} {shlex.quote(str(home))}", root)
    sh("git symbolic-ref HEAD refs/heads/master", home)
    sh("git commit -q --allow-empty -m c0 && git push -q origin master", home)
    return origin, home


def repo_sha(repo, rev="HEAD"):
    p = subprocess.run(f"git -C {shlex.quote(str(repo))} rev-parse {rev}", shell=True,
                       capture_output=True, text=True, timeout=30)
    return p.stdout.strip()


def repo_commit(repo, msg, push=False):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    cmd = f"git commit -q --allow-empty -m {shlex.quote(msg)}"
    if push:
        cmd += " && git push -q origin master"
    subprocess.run(cmd, shell=True, cwd=str(repo), env=env,
                   capture_output=True, text=True, check=True, timeout=60)
    return repo_sha(repo)


def run_watch(tmp, *, local_ver="codex-cli 0.147.0", local_rc=0,
              remote_ver="codex-cli 0.147.0", remote_rc=0, now=1_000_000, uptime=99999,
              systemctl=None, usage=None, usage_rc=0, usage_pct=None,
              repo=None, repo_remote_sha=None, repo_remote_rc=0, repo_persist_s=None,
              **sysargs):
    # 사용량 대역은 항상 건다. 안 걸면 테스트가 진짜 rollout 과 ssh 를 읽는다.
    if usage is None:
        usage = usage_json(now, pct=usage_pct if usage_pct is not None else 10.0)
    env = dict(os.environ)
    # 감시는 home 전용이라 다른 머신에서는 아무것도 안 하고 나간다. 진짜 HOME 을
    # 그대로 두면 둘째 기계에서 이 검사가 통째로 무의미해지는데, 더 나쁜 건 음성
    # 검사들이 그래도 통과해서 통과처럼 보인다는 것이다. 표식을 담은 가짜 HOME 을
    # 줘서 어느 기계에서 돌려도 같은 판정이 나오게 한다.
    fake_home = Path(tmp) / "fakehome"
    (fake_home / ".config").mkdir(parents=True, exist_ok=True)
    (fake_home / ".config" / "claude-machine-id").write_text("home\n", encoding="utf-8")
    env["HOME"] = str(fake_home)
    env.update({
        # 사람이 읽을 이름은 여기서 고정한다. 기본값은 기계 마커와 상대 주소라
        # 돌리는 기계마다 달라지는데, 문구 검사가 그것에 매달리면 그 검사는
        # 어느 기계에서 도느냐로 붙었다 떨어졌다 한다.
        "HOME_INFRA_WATCH_LABEL": "이쪽",
        "HOME_INFRA_WATCH_PEER_LABEL": "저쪽",
        "HOME_INFRA_WATCH_USAGE_LOCAL":
            f"bash -c {shlex.quote('printf %s ' + shlex.quote(usage) + f'; exit {usage_rc}')} --",
        "HOME_INFRA_WATCH_USAGE_REMOTE": "bash -c 'exit 1' --",
        "HOME_INFRA_WATCH_STATE_DIR": str(Path(tmp) / "state"),
        "HOME_INFRA_WATCH_DRY_RUN": "1",
        "HOME_INFRA_WATCH_NOW_EPOCH": str(now),
        "HOME_INFRA_WATCH_UPTIME_S": str(uptime),
        "HOME_INFRA_WATCH_WHICH_LOCAL":
            f"bash -c 'echo \"bin=/x real=/y ver={local_ver}\"; exit {local_rc}'",
        "HOME_INFRA_WATCH_WHICH_REMOTE":
            f"bash -c 'echo \"bin=/x real=/y ver={remote_ver}\"; exit {remote_rc}'",
        "HOME_INFRA_WATCH_SYSTEMCTL": systemctl or fake_systemctl(tmp, **sysargs),
    })
    # 저장소 검사 대역은 항상 건다. 안 걸면 이 검사가 진짜 저장소에 fetch 를 걸고
    # 진짜 ssh 를 연다 — 네트워크 없는 곳에서 결과가 갈린다.
    env["HOME_INFRA_WATCH_REPO"] = str(repo) if repo else str(Path(tmp) / "no-such-repo")
    env["HOME_INFRA_WATCH_REPO_REMOTE_HEAD"] = (
        f"bash -c {shlex.quote('printf %s ' + shlex.quote(repo_remote_sha or '') + f'; exit {repo_remote_rc}')} --")
    if repo_persist_s is not None:
        env["HOME_INFRA_WATCH_REPO_PERSIST_S"] = str(repo_persist_s)
    p = subprocess.run([sys.executable, WATCH], capture_output=True, text=True, env=env, timeout=60)
    return p.stdout + p.stderr


with tempfile.TemporaryDirectory() as tmp:
    # A) codex 하한 미달 — 1회차는 조용, 2회차에 1건, 3회차는 재알림 없음
    out1 = run_watch(tmp, local_ver="codex-cli 0.136.0")
    out2 = run_watch(tmp, local_ver="codex-cli 0.136.0")
    out3 = run_watch(tmp, local_ver="codex-cli 0.136.0")
    check("A 하한미달 1회차는 조용", "codex 버전 이상" not in out1, out1)
    check("A 하한미달 2회차에 경보", "codex 버전 이상" in out2 and "0.136.0" in out2, out2)
    check("A 하한미달 3회차 재알림 없음", "codex 버전 이상" not in out3, out3)
    out4 = run_watch(tmp)      # 정상 복귀
    check("A 복귀 시 해소 알림", "정상 복귀" in out4, out4)

with tempfile.TemporaryDirectory() as tmp:
    # A) --which 자체가 실패해도 이상으로 센다 (버전을 모르는 것도 모르는 것이다)
    run_watch(tmp, local_rc=1, local_ver="")
    out = run_watch(tmp, local_rc=1, local_ver="")
    check("A which 실패도 경보", "codex 버전 이상" in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # A) 호스트간 버전차 — 갓 생긴 차이는 안 울고, 24시간 지속돼야 운다
    o1 = run_watch(tmp, local_ver="codex-cli 0.147.0", remote_ver="codex-cli 0.148.0", now=1000)
    o2 = run_watch(tmp, local_ver="codex-cli 0.147.0", remote_ver="codex-cli 0.148.0", now=1000 + 3600)
    o3 = run_watch(tmp, local_ver="codex-cli 0.147.0", remote_ver="codex-cli 0.148.0",
                   now=1000 + 25 * 3600)
    check("A 단기 버전차는 무경보", "24시간" not in o1 and "24시간" not in o2, o1 + o2)
    check("A 24시간 지속 버전차는 경보", "24시간" in o3, o3)
    o4 = run_watch(tmp, local_ver="codex-cli 0.147.0", remote_ver="codex-cli 0.148.0",
                   now=1000 + 26 * 3600)
    check("A 지속 버전차 재알림 없음", "24시간" not in o4, o4)

with tempfile.TemporaryDirectory() as tmp:
    # A) ssh 실패는 드리프트가 아니다
    run_watch(tmp, remote_rc=255, remote_ver="")
    out = run_watch(tmp, remote_rc=255, remote_ver="")
    check("A ssh 실패는 드리프트 무경보", "버전차" not in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # B) 타이머 정상(방금 발화) — 무경보
    out = run_watch(tmp, uptime=10000, last_trigger="@999800", now=1000000)
    check("B 정상 타이머 무경보", "todo-rearm 복구 경로 이상" not in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # B) 부팅 직후 미발화는 유예
    out = run_watch(tmp, uptime=300, last_trigger="")
    out2 = run_watch(tmp, uptime=600, last_trigger="")
    check("B 부팅 유예 안에서는 무경보", "todo-rearm" not in out + out2, out + out2)

with tempfile.TemporaryDirectory() as tmp:
    # B) 부팅 유예를 넘겨도 미발화면 경보
    run_watch(tmp, uptime=3000, last_trigger="")
    out = run_watch(tmp, uptime=3600, last_trigger="")
    check("B 유예 후 미발화 경보", "한 번도 발화하지 않았다" in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # B) 40분 조용하면 경보 (주기 10분)
    run_watch(tmp, uptime=10000, last_trigger="@997600", now=1000000)
    out = run_watch(tmp, uptime=10000, last_trigger="@997600", now=1000000)
    check("B 발화 정체 경보", "마지막 발화가" in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # B) 타이머가 죽어 있으면 경보
    run_watch(tmp, active="inactive", uptime=10000, last_trigger="@999900", now=1000000)
    out = run_watch(tmp, active="inactive", uptime=10000, last_trigger="@999900", now=1000000)
    check("B 타이머 inactive 경보", "active 가 아니다" in out, out)
    out2 = run_watch(tmp, uptime=10000, last_trigger="@999900", now=1000000)
    check("B 타이머 복귀 시 해소", "정상 복귀" in out2, out2)

with tempfile.TemporaryDirectory() as tmp:
    # B) 서비스가 실패로 끝났으면 경보 (rc 만 보고 넘어가지 않는다)
    run_watch(tmp, uptime=10000, last_trigger="@999900", now=1000000,
              result="timeout", status="1")
    out = run_watch(tmp, uptime=10000, last_trigger="@999900", now=1000000,
                    result="timeout", status="1")
    check("B 서비스 실패 경보", "마지막 결과가 timeout" in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # B) 지금 실행중이고 상한 안이면 정상 진행으로 본다
    out = run_watch(tmp, uptime=10000, last_trigger="@999950", now=1000000,
                    svc_state="active", started="@999950")
    out2 = run_watch(tmp, uptime=10000, last_trigger="@999950", now=1000000,
                     svc_state="active", started="@999950")
    check("B 짧은 실행중은 무경보", "todo-rearm 복구 경로 이상" not in out + out2, out + out2)

with tempfile.TemporaryDirectory() as tmp:
    # B) 상한을 훌쩍 넘겨 안 끝나면 경보
    run_watch(tmp, uptime=10000, last_trigger="@999900", now=1000000,
              svc_state="active", started="@999000")
    out = run_watch(tmp, uptime=10000, last_trigger="@999900", now=1000000,
                    svc_state="active", started="@999000")
    check("B 서비스 장기 실행 경보", "안 끝난다" in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # 진짜 `codex-ask --which` 출력 형식이 파싱되는지. 여기서 실패하면 감시가 영원히
    # "버전을 못 읽음" 으로만 울어서, 하한 미달 경보가 통과해도 아무 의미가 없다.
    o1 = run_watch(tmp, local_ver="codex-cli 0.147.0")
    o2 = run_watch(tmp, local_ver="codex-cli 0.147.0")
    check("실제 --which 형식(이름 공백 숫자)이 파싱된다", "codex 버전 이상" not in o1 + o2, o1 + o2)

with tempfile.TemporaryDirectory() as tmp:
    # 심박: 무경보 회차도 관측값과 함께 한 줄 남아야 한다. 안 남으면 "조용한 2주"가
    # 정상인지 검사 중단인지 구분할 방법이 없다.
    run_watch(tmp, uptime=10000, last_trigger="@999800", now=1000000)
    log = (Path(tmp) / "state" / "watch.log")
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    check("무경보 회차도 심박 1줄", len(text.splitlines()) == 1 and "ok " in text,
          text or "(로그 없음)")
    check("심박에 관측값이 실린다", "0.147.0" in text and "todo_rearm 정상" in text, text)
    # 경보 회차는 ok 가 아니라 ALERT 로 남는다
    run_watch(tmp, local_ver="codex-cli 0.136.0")
    run_watch(tmp, local_ver="codex-cli 0.136.0")
    text2 = log.read_text(encoding="utf-8")
    check("경보 회차는 ALERT 로 구분된다", "ALERT " in text2, text2)

with tempfile.TemporaryDirectory() as tmp:
    # C) 한도 사용률. 임계 미만은 조용하고, 넘으면 그 창에 딱 한 번 운다.
    NOW = 1_000_000
    out = run_watch(tmp, now=NOW, usage_pct=79.0)
    check("C 임계 미만은 무경보", "계정 한도" not in out, out)
    # 임계를 넘겨도 안 운다 — 한도 알림은 껐다(운영자 지시).
    out1 = run_watch(tmp, now=NOW, usage_pct=81.0)
    check("C 임계를 넘겨도 무경보", "계정 한도" not in out1, out1)
    out3 = run_watch(tmp, now=NOW,
                     usage=usage_json(NOW, pct=88.0, resets=NOW + 999_999))
    check("C 창이 바뀌어도 무경보", "계정 한도" not in out3, out3)

with tempfile.TemporaryDirectory() as tmp:
    NOW = 1_000_000
    # 오래된 관측으로는 안 운다. 사용률은 codex 가 돌 때만 갱신되므로, 하루 전 값으로
    # 우는 것은 지금 상태를 말하는 게 아니다.
    out = run_watch(tmp, now=NOW, usage=usage_json(NOW, pct=95.0, age_s=13 * 3600))
    check("C 오래된 관측은 무경보", "계정 한도" not in out, out)
    # 양쪽 다 사용량을 못 읽으면 조용히 넘어간다(감시 자체의 고장은 심박이 드러낸다).
    out = run_watch(tmp, now=NOW, usage_rc=1, usage="")
    check("C 관측 못 하면 무경보", "계정 한도" not in out, out)
    log = (Path(tmp) / "state" / "watch.log").read_text(encoding="utf-8")
    check("C 심박에 사용률이 실린다", "codex_usage" in log, log)

with tempfile.TemporaryDirectory() as tmp:
    # D) 둘 다 origin 끝점이면 조용하고, 심박에는 그래도 한 줄 남는다.
    NOW = 1_000_000
    origin, home = make_repo_pair(tmp)
    tip = repo_sha(home)
    out = run_watch(tmp, now=NOW, repo=home, repo_remote_sha=tip)
    check("D 양쪽 동기면 무경보", "저장소가" not in out, out)
    log = (Path(tmp) / "state" / "watch.log").read_text(encoding="utf-8")
    check("D 심박에 저장소 상태가 실린다", "repo_sync 동기" in log, log)

with tempfile.TemporaryDirectory() as tmp:
    # D) 둘째 기계가 안 올린 커밋 — 홈의 저장소에 그 객체가 아예 없다.
    NOW = 1_000_000
    origin, home = make_repo_pair(tmp)
    ghost = "0" * 39 + "1"
    o1 = run_watch(tmp, now=NOW, repo=home, repo_remote_sha=ghost, repo_persist_s=6 * 3600)
    o2 = run_watch(tmp, now=NOW + 3600, repo=home, repo_remote_sha=ghost, repo_persist_s=6 * 3600)
    check("D 갓 생긴 어긋남은 무경보", "저장소가" not in o1 and "저장소가" not in o2, o1 + o2)
    o3 = run_watch(tmp, now=NOW + 7 * 3600, repo=home, repo_remote_sha=ghost,
                   repo_persist_s=6 * 3600)
    check("D 지속되면 경보 1건", "저장소가" in o3 and "저쪽 미푸시" in o3, o3)
    o4 = run_watch(tmp, now=NOW + 8 * 3600, repo=home, repo_remote_sha=ghost,
                   repo_persist_s=6 * 3600)
    check("D 같은 어긋남 재알림 없음", "저장소가" not in o4, o4)
    o5 = run_watch(tmp, now=NOW + 9 * 3600, repo=home, repo_remote_sha=repo_sha(home),
                   repo_persist_s=6 * 3600)
    check("D 해소되면 해소 알림", "어긋남 해소" in o5, o5)

with tempfile.TemporaryDirectory() as tmp:
    # D) 홈이 못 받은 커밋 — origin 이 앞서 있고 홈 HEAD 가 그 조상이다. 개수도 센다.
    NOW = 1_000_000
    origin, home = make_repo_pair(tmp)
    behind = repo_sha(home)
    other = Path(tmp) / "repos" / "other"
    subprocess.run(f"git clone -q {shlex.quote(str(origin))} {shlex.quote(str(other))}",
                   shell=True, capture_output=True, text=True, check=True, timeout=60)
    for i in range(2):
        repo_commit(other, f"c{i + 1}", push=True)
    ahead_tip = repo_sha(other)
    out = run_watch(tmp, now=NOW + 7 * 3600, repo=home, repo_remote_sha=ahead_tip,
                    repo_persist_s=0)
    check("D 이쪽 미풀을 개수까지 센다", "이쪽 미풀 2커밋" in out, out)
    check("D 미풀일 때 상대 기계는 동기로 본다", "저쪽 동기" in out, out)
    # 홈이 안 올린 커밋은 미푸시로 갈린다 — 같은 "어긋남" 이라도 조치가 반대다.
    repo_commit(home, "local-only")
    out2 = run_watch(tmp, now=NOW + 8 * 3600, repo=home, repo_remote_sha=ahead_tip,
                    repo_persist_s=0)
    check("D 이쪽 미푸시는 미푸시로 갈린다", "이쪽 갈라짐" in out2 or "이쪽 미푸시" in out2, out2)

with tempfile.TemporaryDirectory() as tmp:
    # D) ssh 가 안 되는 것은 어긋남이 아니다. 그래도 홈 자신의 미푸시는 본다.
    NOW = 1_000_000
    origin, home = make_repo_pair(tmp)
    out = run_watch(tmp, now=NOW + 7 * 3600, repo=home, repo_remote_sha="",
                    repo_remote_rc=255, repo_persist_s=0)
    check("D ssh 실패만으로는 무경보", "저장소가" not in out, out)
    repo_commit(home, "home-local-only")
    out2 = run_watch(tmp, now=NOW + 8 * 3600, repo=home, repo_remote_sha="",
                     repo_remote_rc=255, repo_persist_s=0)
    check("D ssh 실패여도 이쪽 미푸시는 잡는다", "이쪽 미푸시 1커밋" in out2, out2)

with tempfile.TemporaryDirectory() as tmp:
    # D) 저장소가 없거나 fetch 가 안 되면 판정하지 않는다(감시 자신의 고장은 심박이 드러낸다).
    out = run_watch(tmp, now=1_000_000)
    check("D fetch 실패는 무경보", "저장소가" not in out, out)
    log = (Path(tmp) / "state" / "watch.log").read_text(encoding="utf-8")
    check("D fetch 실패가 심박에 남는다", "repo_sync fetch실패" in log, log)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
