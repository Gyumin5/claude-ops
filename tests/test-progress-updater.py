#!/usr/bin/env python3
"""claude-progress-updater 패치 검증 테스트.

왜 있나: 2026-08-10, 옆 기계에서 이 자동화가 progress.md 에 실재하지 않는 사건을 미래
날짜로 5회 기입했다("밤새 tick 14회", "08-11 07:00 guardian 라운드 실행"). 옛 구조는
헤드리스 claude 에게 progress.md 전문을 다시 쓰게 하고 그 결과로 파일을 통째 덮었고,
가드 4종(길이·헤더·Jaccard)이 전부 형태만 봐서 유창한 창작을 그대로 통과시켰다.

그래서 고정하는 것: 모델은 패치와 근거만 낸다, 근거가 입력에 글자 그대로 없으면 버린다,
미래 날짜를 일어난 일로 적으면 버린다(예정은 통과), updated 는 프로그램이 쓴다,
세션이 방금 고친 파일은 건드리지 않는다, 생성 중 파일이 바뀌면 덮지 않는다,
8KB 넘는 파일의 tail 이 살아남는다.

가짜 claude 를 PATH 앞에 두고 실제 배포본을 그대로 돌린다.
"""
import datetime
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin",
                      "claude-progress-updater")

BASE_PROG = """# progress.md
updated: 2026-01-01 00:00 KST
status: active
task: 게이트 검사기 작업 중.

## Current State
- 게이트 검사기 1차 구현 완료.
- 넛지 사고는 세션 권한 대기가 원인이었다.

## Blockers
1. (관측) 재무장 오탐률 4주 관측.
2. (사람 작업) usr/local/bin claude 깨짐.

## Do Not Repeat
- rc 만 보고 원인을 라벨하지 말 것.
"""

# transcript 에 실제로 들어가는 문장. evidence 는 여기서 글자 그대로 나와야 한다.
REAL_LINE = "게이트 검사기에 사후조건 검사를 붙였고 테스트 12개가 전부 통과했다"

results = []


def check(name, cond, detail: object = ""):
    results.append(bool(cond))
    print(("  PASS " if cond else "  FAIL ") + name +
          (("  — " + str(detail)) if detail and not cond else ""))


def make_env(tmp, patch_obj, prog=BASE_PROG, sabotage=False):
    """가짜 프로젝트 + 가짜 claude 를 만든다. (cwd, env) 반환.

    매 호출마다 새 하위 디렉토리를 판다. 같은 HOME 을 재사용하면 앞 케이스가 남긴
    offset state 때문에 다음 케이스가 "no new entries" 로 조용히 통과해버린다.
    """
    tmp = tempfile.mkdtemp(dir=tmp)
    home = os.path.join(tmp, "home")
    proj = os.path.join(tmp, "proj")
    os.makedirs(proj, exist_ok=True)
    os.makedirs(os.path.join(home, ".local", "bin"), exist_ok=True)
    with open(os.path.join(proj, "progress.md"), "w") as f:
        f.write(prog)

    key = proj.replace("/", "-").replace("_", "-")
    pdir = os.path.join(home, ".claude", "projects", key)
    os.makedirs(pdir, exist_ok=True)
    with open(os.path.join(pdir, "s.jsonl"), "w") as f:
        for i in range(12):
            f.write(json.dumps({
                "type": "assistant", "timestamp": "2026-08-10T20:00:0%d" % (i % 10),
                "message": {"content": [{"type": "text",
                                         "text": REAL_LINE + " (반복 %d)" % i}]},
            }) + "\n")

    bindir = os.path.join(tmp, "fakebin")
    os.makedirs(bindir, exist_ok=True)
    payload = json.dumps({"result": json.dumps(patch_obj, ensure_ascii=False),
                          "usage": {}, "total_cost_usd": 0})
    with open(os.path.join(bindir, "payload.json"), "w") as f:
        f.write(payload)
    # sabotage: claude 호출 도중 세션이 progress.md 를 고친 상황을 흉내낸다(TOCTOU).
    sab = ('printf "x\\n" >> %s\n' % json.dumps(os.path.join(proj, "progress.md"))) if sabotage else ""
    fake = "#!/bin/bash\n%scat %s\n" % (sab, json.dumps(os.path.join(bindir, "payload.json")))
    fp = os.path.join(bindir, "claude")
    with open(fp, "w") as f:
        f.write(fake)
    os.chmod(fp, os.stat(fp).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    env = dict(os.environ)
    env["HOME"] = home
    env["PATH"] = bindir + ":" + env.get("PATH", "")
    env["UPDATER_AUTO_MERGE"] = "1"
    return proj, env


def run(tmp, patch_obj, prog=BASE_PROG, sabotage=False, env_extra=None, age=None):
    proj, env = make_env(tmp, patch_obj, prog=prog, sabotage=sabotage)
    if env_extra:
        env.update(env_extra)
    p = os.path.join(proj, "progress.md")
    if age is not None:
        old = time.time() - age
        os.utime(p, (old, old))
    r = subprocess.run(["bash", SCRIPT, proj], env=env, capture_output=True, text=True)
    with open(p) as f:
        after = f.read()
    logf = os.path.join(env["HOME"], ".claude", "logs", "progress-updater.log")
    logtxt = open(logf).read() if os.path.exists(logf) else ""
    return r, after, logtxt, proj


def ev(text=REAL_LINE):
    return text


def main():
    today = datetime.date.today()
    tomorrow = (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    print("근거 대조")
    tmp = tempfile.mkdtemp(prefix="pu1.")
    try:
        r, after, log, _ = run(tmp, {"patches": [{
            "op": "replace",
            "old": "- 게이트 검사기 1차 구현 완료.",
            "new": "- 게이트 검사기 1차 구현 완료. 사후조건 검사 추가, 테스트 12개 통과.",
            "evidence": ev()}]}, age=9999)
        check("근거가 입력에 있으면 적용된다", "사후조건 검사 추가" in after, log[-300:])
        check("updated 를 실제 시각으로 다시 쓴다",
              today.strftime("%Y-%m-%d") in after.splitlines()[1], after.splitlines()[1])
        check("모델이 준 옛 updated 값은 남지 않는다", "2026-01-01" not in after)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    tmp = tempfile.mkdtemp(prefix="pu2.")
    try:
        r, after, log, _ = run(tmp, {"patches": [{
            "op": "append", "section": "## Current State",
            "new": "- 밤새 tick 14회 실행됨. 07:00 guardian 라운드 완료.",
            "evidence": "밤새 tick 14회를 돌렸고 guardian 라운드가 끝났다"}]}, age=9999)
        check("근거가 입력에 없으면 버린다 — 이번 사고 재현 케이스",
              "밤새 tick" not in after, after[-200:])
        check("버린 이유를 로그에 남긴다", "근거가 입력에 없다" in log, log[-300:])
        check("아무것도 적용 안 되면 파일이 그대로다", after == BASE_PROG)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    tmp = tempfile.mkdtemp(prefix="pu3.")
    try:
        r, after, log, _ = run(tmp, {"patches": [{
            "op": "replace", "old": "- 게이트 검사기 1차 구현 완료.",
            "new": "- 게이트 검사기 완료.", "evidence": "짧다"}]}, age=9999)
        check("근거가 너무 짧으면 버린다", after == BASE_PROG, log[-200:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("미래 날짜")
    tmp = tempfile.mkdtemp(prefix="pu4.")
    try:
        r, after, log, _ = run(tmp, {"patches": [{
            "op": "append", "section": "## Current State",
            "new": "- %s 07:00 guardian 라운드 실행 완료." % tomorrow,
            "evidence": ev()}]}, age=9999)
        check("미래 날짜를 일어난 일로 적으면 버린다", tomorrow not in after, after[-200:])
        check("미래날짜 거부를 로그에 남긴다", "미래 날짜" in log, log[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    tmp = tempfile.mkdtemp(prefix="pu5.")
    try:
        r, after, log, _ = run(tmp, {"patches": [{
            "op": "append", "section": "## Blockers",
            "new": "3. (관측) %s 만료 예정 — 그 전에 재무장 확인." % tomorrow,
            "evidence": ev()}]}, age=9999)
        check("미래 날짜라도 예정 문맥이면 통과한다", tomorrow in after, after[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("old 일치")
    tmp = tempfile.mkdtemp(prefix="pu6.")
    try:
        r, after, log, _ = run(tmp, {"patches": [{
            "op": "replace", "old": "존재하지 않는 문장이다 이건", "new": "x",
            "evidence": ev()}]}, age=9999)
        check("old 가 없으면 버린다", after == BASE_PROG, log[-200:])

        dup = BASE_PROG + "\n- 게이트 검사기 1차 구현 완료.\n"
        r, after, log, _ = run(tmp, {"patches": [{
            "op": "replace", "old": "- 게이트 검사기 1차 구현 완료.", "new": "x",
            "evidence": ev()}]}, prog=dup, age=9999)
        check("old 가 2번 나오면 버린다(엉뚱한 자리 수정 방지)", "2번 일치" in log, log[-200:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("변경 없음")
    tmp = tempfile.mkdtemp(prefix="pu7.")
    try:
        r, after, log, _ = run(tmp, {"patches": []}, age=9999)
        check("빈 패치는 정상 종료하고 파일을 안 건드린다", after == BASE_PROG)
        check("NO_CHANGE 로 기록한다", "NO_CHANGE" in log, log[-200:])
        check("rc=0", r.returncode == 0, str(r.returncode))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("세션 편집 보호")
    tmp = tempfile.mkdtemp(prefix="pu8.")
    try:
        r, after, log, _ = run(tmp, {"patches": [{
            "op": "replace", "old": "- 게이트 검사기 1차 구현 완료.",
            "new": "- 자동화가 덮어쓴 문장.", "evidence": ev()}]}, age=60)
        check("세션이 방금 고친 파일은 건드리지 않는다", after == BASE_PROG, after[-200:])
        check("보호창 사유를 로그에 남긴다", "세션이 직접 수정" in log, log[-200:])

        r, after, log, _ = run(tmp, {"patches": [{
            "op": "replace", "old": "- 게이트 검사기 1차 구현 완료.",
            "new": "- 창을 좁히면 통과한다.", "evidence": ev()}]},
            age=60, env_extra={"RECENT_EDIT_GUARD_SEC": "10"})
        check("보호창 밖이면 적용된다", "창을 좁히면 통과" in after, after[-200:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 스킵이 근거를 버리면 안 된다. 옛 코드는 스킵하면서 offset 을 현재 줄수로 밀어버려서
    # 그 주기에 오간 대화가 영구히 근거에서 빠졌다. 옆 기계 peer 2026-08-27 사고의 실제
    # 기전이 이것이다 — 12주기 중 11회가 스킵됐고 그 스킵들이 정정 근거를 통째로 버렸다.
    tmp = tempfile.mkdtemp(prefix="pu8b.")
    try:
        patch = {"patches": [{"op": "replace", "old": "- 게이트 검사기 1차 구현 완료.",
                              "new": "- 스킵 뒤에도 근거가 살아서 적용된다.",
                              "evidence": ev()}]}
        proj, env = make_env(tmp, patch)
        p = os.path.join(proj, "progress.md")
        old = time.time() - 60
        os.utime(p, (old, old))
        r1 = subprocess.run(["bash", SCRIPT, proj], env=env, capture_output=True, text=True)
        logf = os.path.join(env["HOME"], ".claude", "logs", "progress-updater.log")
        log1 = open(logf).read() if os.path.exists(logf) else ""
        check("스킵 주기는 아무것도 안 바꾼다",
              r1.returncode == 0 and open(p).read() == BASE_PROG and "이번 주기 스킵" in log1,
              log1[-200:])
        # 두 번째 주기: 보호창만 좁힌다. 커서가 살아 있으면 같은 대화로 패치가 적용된다.
        env2 = dict(env); env2["RECENT_EDIT_GUARD_SEC"] = "10"
        os.utime(p, (old, old))
        subprocess.run(["bash", SCRIPT, proj], env=env2, capture_output=True, text=True)
        after2 = open(p).read()
        log2 = open(logf).read() if os.path.exists(logf) else ""
        check("스킵된 주기의 대화가 다음 주기에 살아 있다(커서 미전진)",
              "스킵 뒤에도 근거가 살아서 적용된다" in after2,
              ("no new entries" in log2 and "→ 커서가 전진해 근거가 버려졌다") or log2[-260:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("TOCTOU")
    tmp = tempfile.mkdtemp(prefix="pu9.")
    try:
        r, after, log, _ = run(tmp, {"patches": [{
            "op": "replace", "old": "- 게이트 검사기 1차 구현 완료.",
            "new": "- 덮어쓰면 안 되는 문장.", "evidence": ev()}]}, age=9999, sabotage=True)
        check("생성 중 세션이 파일을 고치면 덮지 않는다",
              "덮어쓰면 안 되는" not in after, after[-200:])
        check("TOCTOU 를 로그에 남긴다", "ABORT_TOCTOU" in log, log[-200:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("tail 보존")
    tmp = tempfile.mkdtemp(prefix="pu10.")
    try:
        big = BASE_PROG + "\n## 부록\n" + ("- 채우기 줄입니다.\n" * 700) + "- TAIL_MARKER 끝줄.\n"
        check("픽스처가 8KB 를 넘는다", len(big.encode()) > 8000, str(len(big.encode())))
        r, after, log, _ = run(tmp, {"patches": [{
            "op": "replace", "old": "- 게이트 검사기 1차 구현 완료.",
            "new": "- 게이트 검사기 완료, 테스트 12개 통과.", "evidence": ev()}]},
            prog=big, age=9999)
        check("8KB 뒤의 tail 이 그대로 남는다", "TAIL_MARKER" in after, after[-200:])
        check("머리 쪽 패치는 적용된다", "테스트 12개 통과" in after, after[:400])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("auto-merge 꺼짐")
    tmp = tempfile.mkdtemp(prefix="pu11.")
    try:
        r, after, log, proj = run(tmp, {"patches": [{
            "op": "replace", "old": "- 게이트 검사기 1차 구현 완료.",
            "new": "- 제안본에만 들어갈 문장.", "evidence": ev()}]},
            age=9999, env_extra={"UPDATER_AUTO_MERGE": "0"})
        check("progress.md 는 안 건드린다", after == BASE_PROG)
        # 제안본은 프로젝트 밖(도구 state 디렉토리)에 쓴다. 프로젝트 루트에 쓰면
        # state/ 를 gitignore 안 한 저장소마다 untracked 잔재가 매 회차 쌓인다.
        home = os.path.join(os.path.dirname(proj), "home")
        prop = os.path.join(home, ".claude", "state", "progress-updater",
                            "proposed", os.path.basename(proj) + ".md")
        check("제안본을 도구 state 디렉토리에 남긴다",
              os.path.exists(prop) and "제안본에만" in open(prop).read(), log[-300:])
        check("프로젝트 루트에는 안 남긴다",
              not os.path.exists(os.path.join(proj, "proposed-progress.md")),
              str(sorted(os.listdir(proj))))
        check("PROPOSED 줄에 경로를 찍는다", prop in log, log[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("깨진 출력")
    tmp = tempfile.mkdtemp(prefix="pu12.")
    try:
        r, after, log, _ = run(tmp, "갱신 완료했습니다.", age=9999)
        check("JSON 이 아니면 파일을 안 건드린다", after == BASE_PROG)
        check("PARSE_FAIL 로 기록한다", "PARSE_FAIL" in log, log[-200:])
        check("rc=0 (타이머를 죽이지 않는다)", r.returncode == 0, str(r.returncode))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ok = all(results)
    print("\n%d/%d PASS" % (sum(results), len(results)))
    print("ALL PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
