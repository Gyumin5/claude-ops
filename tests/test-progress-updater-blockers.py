#!/usr/bin/env python3
"""claude-progress-updater 가 ## Blockers 구역을 못 고치는지 본다.

왜 있나: 갱신기 프롬프트에 할 일 목록 규칙이 하나도 없었다. 그래서 15분(실측 30분)마다
도는 자동화가 그 구역을 자유롭게 고쳤고, 백업 사본 재연에서 활성 항목이 완료 구역으로
옮겨진 것보다 흔적 없이 사라진 것이 훨씬 많았다(R51 은 "종결은 삭제가 아니라 이동" 이다).
사라진 것 안에 운영자가 정해야 하는 항목([coord:...])이 들어 있었다.

그래서 고정하는 것: 그 구역은 세션만 쓴다. 갱신기는 더하지도 고치지도 지우지도 못하고,
완료로 옮기거나 완료 표시를 붙이지도 못한다. 거절한 제안은 버리지 않고 대기함에 남기며,
그 기록에 실패하면 본문을 안 쓰고 입력 위치도 안 올린다.

판정은 가짜 claude 를 PATH 앞에 두고 실제 배포본을 그대로 돌려서 한다.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "pu_harness", os.path.join(HERE, "test-progress-updater.py"))
_h = importlib.util.module_from_spec(_spec)
_h.__name__ = "pu_harness"          # main() 이 자동 실행되지 않게 한다
_spec.loader.exec_module(_h)

REAL_LINE = _h.REAL_LINE
SCRIPT = _h.SCRIPT

BASE = """# progress.md
updated: 2026-01-01 00:00 KST
status: active
task: 게이트 검사기 작업 중.

## Current State
- 게이트 검사기 1차 구현 완료.

## Blockers
1. (대기·운영자 판단) 저장 장치 107기가를 지울지 — [coord:df-d11]. 운영자 답을 기다린다.
2. (가능) 넛지 한도 판정 3층 보조 경로 관측. 어려운 경우가 와야 aux=1 을 본다.
3. (대기·사건) 알림 이원화 실효 — 되묻는 알림 0 이면 종결.

### 완료(최근)
- [11-04] 저장소 경계를 세우고 이력을 다시 썼다.

## Do Not Repeat
- rc 만 보고 원인을 라벨하지 말 것.
"""

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("  PASS " if cond else "  FAIL ") + name +
          (("  — " + str(detail)) if detail and not cond else ""))


def blockers_of(text):
    """본문에서 보호 구역만 떼어낸다 (## Blockers ~ 다음 ## 직전)."""
    lines = text.split("\n")
    try:
        a = next(n for n, l in enumerate(lines) if l.strip() == "## Blockers")
    except StopIteration:
        return None
    b = len(lines)
    for n in range(a + 1, len(lines)):
        if lines[n].startswith("## "):
            b = n
            break
    return "\n".join(lines[a:b])


def pending_of(home, proj):
    p = os.path.join(home, ".claude", "state", "progress-updater", "pending",
                     os.path.basename(proj) + ".jsonl")
    if not os.path.exists(p):
        return []
    out = []
    with open(p) as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    return out


def go(patches, prog=BASE, **kw):
    """한 회차 돌리고 (반환코드, 본문, 로그, 대기함, 프로젝트경로, HOME) 을 준다."""
    tmp = tempfile.mkdtemp(prefix="pub.")
    try:
        proj, env = _h.make_env(tmp, {"patches": patches}, prog=prog,
                                sabotage=kw.get("sabotage", False))
        if kw.get("env_extra"):
            env.update(kw["env_extra"])
        if kw.get("pre"):
            kw["pre"](env["HOME"], proj)
        p = os.path.join(proj, "progress.md")
        old = os.path.getmtime(p) - 9999
        os.utime(p, (old, old))
        r = subprocess.run(["bash", SCRIPT, proj], env=env,
                           capture_output=True, text=True)
        after = open(p).read()
        logf = os.path.join(env["HOME"], ".claude", "logs", "progress-updater.log")
        log = open(logf).read() if os.path.exists(logf) else ""
        return r, after, log, pending_of(env["HOME"], proj), proj, env["HOME"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ev():
    return REAL_LINE


BEFORE = blockers_of(BASE)


def protected(name, patches, prog=BASE, expect_pending=True):
    """그 패치가 보호 구역을 한 글자도 못 바꾸는지 본다."""
    r, after, log, pend, _proj, _home = go(patches, prog=prog)
    same = blockers_of(after) == blockers_of(prog)
    check(name, same, (blockers_of(after) or "")[:160])
    if expect_pending:
        check(name + " — 제안이 대기함에 남는다", len(pend) >= 1, log[-200:])
    return r, after, log, pend


def main():
    print("보호 구역을 바꾸려는 패치")

    # 1 항목 삭제
    protected("항목을 지우는 교체를 막는다", [{
        "op": "replace",
        "old": "3. (대기·사건) 알림 이원화 실효 — 되묻는 알림 0 이면 종결.\n",
        "new": "", "evidence": ev()}])

    # 2 본문 변경
    protected("항목 본문을 고치는 교체를 막는다", [{
        "op": "replace",
        "old": "2. (가능) 넛지 한도 판정 3층 보조 경로 관측.",
        "new": "2. (가능) 넛지 한도 판정 3층 보조 경로 관측 — 오늘 2회 돌았다.",
        "evidence": ev()}])

    # 3 제자리 완료 표시
    protected("제자리에 완료 표시를 붙이는 것을 막는다", [{
        "op": "replace",
        "old": "3. (대기·사건) 알림 이원화 실효",
        "new": "3. ✅(완료) 알림 이원화 실효",
        "evidence": ev()}])

    # 4 완료 구역으로 이동
    protected("완료 구역으로 옮기는 복합 교체를 막는다", [{
        "op": "replace",
        "old": ("3. (대기·사건) 알림 이원화 실효 — 되묻는 알림 0 이면 종결.\n\n"
                "### 완료(최근)\n"),
        "new": ("\n### 완료(최근)\n"
                "- [11-05] 알림 이원화 실효 확인.\n"),
        "evidence": ev()}])

    # 5 coord 식별자 교체
    protected("coord 식별자를 바꾸는 교체를 막는다", [{
        "op": "replace", "old": "[coord:df-d11]", "new": "[coord:df-d99]",
        "evidence": ev()}])

    # 6 재개 조건 제거
    protected("재개 조건을 떼는 교체를 막는다", [{
        "op": "replace",
        "old": "— [coord:df-d11]. 운영자 답을 기다린다.",
        "new": "— [coord:df-d11].",
        "evidence": ev()}])

    # 7 완료 구역 줄 변경
    protected("완료 구역의 줄을 고치는 것도 막는다", [{
        "op": "replace",
        "old": "- [11-04] 저장소 경계를 세우고 이력을 다시 썼다.",
        "new": "- [11-04] 저장소 경계를 세웠다. 이력 재작성 포함.",
        "evidence": ev()}])

    # 8 구역 밖에서 시작해 안으로 걸치는 교체
    protected("경계를 걸친 교체를 막는다", [{
        "op": "replace",
        "old": ("- 게이트 검사기 1차 구현 완료.\n\n## Blockers\n"
                "1. (대기·운영자 판단) 저장 장치 107기가를 지울지"),
        "new": ("- 게이트 검사기 1차 구현 완료.\n\n## Blockers\n"
                "1. (가능) 저장 장치 107기가를 지운다"),
        "evidence": ev()}])

    # 9 ## Blockers 에 직접 append
    protected("할 일 목록에 직접 더하는 것을 막는다", [{
        "op": "append", "section": "## Blockers",
        "new": "4. (가능) 새로 들어온 요청.", "evidence": ev()}])

    # 10 ### 완료(최근) 에 append
    protected("완료 구역에 더하는 것을 막는다", [{
        "op": "append", "section": "### 완료(최근)",
        "new": "- [11-05] 끝냈다.", "evidence": ev()}])

    # 11 머리말 삽입으로 구역 경계를 흔드는 패치
    protected("구역 안에 새 머리말을 끼우는 것을 막는다", [{
        "op": "replace",
        "old": "2. (가능) 넛지 한도 판정 3층 보조 경로 관측.",
        "new": "## 새 구역\n2. (가능) 넛지 한도 판정 3층 보조 경로 관측.",
        "evidence": ev()}])

    # 12 번호를 다시 매기는 패치
    protected("번호를 다시 매기는 것을 막는다", [{
        "op": "replace",
        "old": "2. (가능) 넛지 한도 판정",
        "new": "5. (가능) 넛지 한도 판정",
        "evidence": ev()}])

    # 13 번호 없는 항목 추가
    protected("번호 없는 줄을 끼우는 것도 막는다", [{
        "op": "replace",
        "old": "### 완료(최근)",
        "new": "- 번호 없는 새 항목\n\n### 완료(최근)",
        "evidence": ev()}])

    # 14 중복 번호
    protected("중복 번호를 만드는 것을 막는다", [{
        "op": "replace",
        "old": "3. (대기·사건) 알림 이원화 실효",
        "new": "2. (대기·사건) 알림 이원화 실효",
        "evidence": ev()}])

    # 15 ## Blockers 자체가 없는 파일에 새로 만드는 패치
    noblk = BASE.replace("## Blockers", "## 지난 일")
    r, after, log, pend, _p, _hm = go([{
        "op": "append", "section": "## Current State",
        "new": "\n## Blockers\n1. (가능) 새 요청.", "evidence": ev()}], prog=noblk)
    check("없던 할 일 목록을 새로 만드는 것도 막는다",
          "## Blockers" not in after, after[-200:])
    check("그 제안도 대기함에 남는다", len(pend) >= 1, log[-200:])

    print("보호 구역 밖")

    # 16 정상 패치는 그대로 적용된다
    r, after, log, pend, _p, _hm = go([{
        "op": "replace",
        "old": "- 게이트 검사기 1차 구현 완료.",
        "new": "- 게이트 검사기 1차 구현 완료. 사후조건 검사 추가.",
        "evidence": ev()}])
    check("구역 밖 교체는 적용된다", "사후조건 검사 추가" in after, log[-200:])
    check("그때 보호 구역은 그대로다", blockers_of(after) == BEFORE)
    check("정상 패치만 있으면 대기함에 안 쌓인다", pend == [], pend[:1])

    # 17 섞여 들어와도 정상 패치는 살고 보호 패치만 걸린다
    r, after, log, pend, _p, _hm = go([
        {"op": "replace", "old": "[coord:df-d11]", "new": "[coord:df-d99]",
         "evidence": ev()},
        {"op": "append", "section": "## Current State",
         "new": "- 새 사실 한 줄.", "evidence": ev()},
    ])
    check("복합 패치에서 정상 쪽은 살아남는다", "새 사실 한 줄" in after, log[-200:])
    check("복합 패치에서 보호 쪽은 걸린다", blockers_of(after) == BEFORE)
    check("걸린 쪽만 대기함에 남는다", len(pend) == 1, [x.get("index") for x in pend])

    # 18 상한 순서 — 보호 제안이 정상 패치를 8개 밖으로 밀지 않는다
    many = [{"op": "replace", "old": "[coord:df-d11]", "new": "[coord:df-d%d]" % i,
             "evidence": ev()} for i in range(8)]
    many.append({"op": "append", "section": "## Current State",
                 "new": "- 상한 뒤에 온 정상 패치.", "evidence": ev()})
    r, after, log, pend, _p, _hm = go(many)
    check("보호 제안이 상한을 먹지 않는다 — 뒤의 정상 패치가 적용된다",
          "상한 뒤에 온 정상 패치" in after, log[-300:])
    check("그때도 보호 구역은 그대로다", blockers_of(after) == BEFORE)

    print("대기함 기록")

    # 19 기록 내용
    r, after, log, pend, _p, _hm = go([{
        "op": "replace", "old": "[coord:df-d11]", "new": "[coord:df-d99]",
        "evidence": ev()}])
    rec = pend[0] if pend else {}
    for key in ("ts", "session", "input_range", "index", "reason", "patch",
                "prog_sha_before", "mode", "status"):
        check("대기함에 %s 칸이 있다" % key, key in rec, list(rec.keys()))
    check("패치 원문을 그대로 남긴다",
          rec.get("patch", {}).get("new") == "[coord:df-d99]", rec.get("patch"))

    # 20 기록에 실패하면 본문을 안 쓰고 입력 위치도 안 올린다
    def lock_pending(home, proj):
        d = os.path.join(home, ".claude", "state", "progress-updater", "pending")
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o500)

    r, after, log, pend, proj, home = go([
        {"op": "replace", "old": "[coord:df-d11]", "new": "[coord:df-d99]",
         "evidence": ev()},
        {"op": "append", "section": "## Current State",
         "new": "- 기록 실패 회차의 정상 패치.", "evidence": ev()},
    ], pre=lock_pending)
    check("대기함을 못 쓰면 본문을 안 쓴다",
          "기록 실패 회차의 정상 패치" not in after, log[-300:])
    check("그때 로그에 사유가 남는다", "PENDING_WRITE_FAIL" in log, log[-300:])

    # 21 동시 수정(TOCTOU) 에서도 보호 구역은 그대로
    r, after, log, pend, _p, _hm = go([{
        "op": "replace", "old": "[coord:df-d11]", "new": "[coord:df-d99]",
        "evidence": ev()}], sabotage=True)
    check("생성 중 세션이 고치면 덮지 않는다", blockers_of(after) == BEFORE, log[-200:])

    print("보존 사본 재연 (읽기 전용)")
    replay()

    bad = results.count(False)
    print("\nPASS %d  FAIL %d" % (results.count(True), bad))
    return 1 if bad else 0


def replay():
    """보존해 둔 실제 사본으로 합성 공격을 돌려 본다.

    과거 패치 원문이 안 남아 있어서 "그때 무엇이 적용됐나" 는 복원할 수 없다. 대신
    실제 진행 파일 모양을 입력으로 삼아, 보호 구역을 건드리는 합성 패치가 하나라도
    통과하는지만 본다. 이건 방어 검증이지 과거 복원이 아니다.
    """
    root = os.path.join(os.path.expanduser("~"), ".claude", "state",
                        "progress-updater", "backups-preserved-20261105")
    if not os.path.isdir(root):
        print("  SKIP 보존 사본이 없다 (%s)" % root)
        return
    import random
    snaps = []
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if os.path.isdir(p):
            snaps += [os.path.join(p, f) for f in sorted(os.listdir(p))
                      if f.endswith(".md")]
    if os.environ.get("PU_REPLAY_ALL") == "1":
        snaps.sort()          # 전수 — 한 번 재는 용도 (수 분)
    else:
        random.Random(20261105).shuffle(snaps)
        snaps = snaps[:40]    # 기본 테스트 명령은 빨라야 한다
    checked = leaked = skipped = 0
    for s in snaps:
        try:
            prog = open(s, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        before = blockers_of(prog)
        if before is None or "updated:" not in prog:
            skipped += 1
            continue
        # 그 파일의 실제 첫 항목을 집어 지우는 패치를 만든다.
        target = None
        for ln in before.split("\n"):
            if ln.strip() and ln[0].isdigit() and ". " in ln and len(ln) > 30:
                target = ln
                break
        if target is None:
            skipped += 1
            continue
        _r, after, _log, _pend, _p, _hm = go(
            [{"op": "replace", "old": target, "new": "", "evidence": REAL_LINE}],
            prog=prog)
        checked += 1
        if blockers_of(after) != before:
            leaked += 1
            print("  LEAK %s" % s)
    check("보존 사본 %d개 합성 공격 중 통과 0건 (건너뜀 %d)" % (checked, skipped),
          leaked == 0 and checked >= 10, "leaked=%d checked=%d" % (leaked, checked))


if __name__ == "__main__":
    sys.exit(main())
