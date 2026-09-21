#!/usr/bin/env python3
"""update_active_memory 테스트 — 착수 전 조회에 필요한 것이 실리는지.

고정하는 것: 이 파일은 SessionStart 에 주입되는 유일한 결정 요약이다.
"다시 하면 시간낭비"로 기록된 실험이 여기 안 실리면 기록이 있어도 조회가 끊긴다.
그리고 절단은 조용하면 안 된다 — 몇 줄이 사라졌는지 파일 안에서 말해야 한다.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

GEN = str(Path(__file__).resolve().parent.parent / "claude" / "hooks" / "update_active_memory.sh")
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"\n      {detail}" if not cond and detail else ""))


last_rc = None


def gen(tmp, *, history="", progress="# progress.md\nstatus: active\ntask: t\n",
        extra=None):
    """extra = {파일명: 본문} — history/ 밑의 분리 로그·월별 archive 픽스처."""
    global last_rc
    d = Path(tmp)
    (d / "history.md").write_text(history, encoding="utf-8")
    (d / "progress.md").write_text(progress, encoding="utf-8")
    if extra:
        (d / "history").mkdir(exist_ok=True)
        for name, body in extra.items():
            (d / "history" / name).write_text(body, encoding="utf-8")
    p = subprocess.run([GEN, str(d)], capture_output=True, text=True, timeout=60)
    last_rc = p.returncode
    a = d / "history" / "active.md"
    return a.read_text(encoding="utf-8") if a.exists() else ""


def hist(n_decisions=5, n_neg=0, prepend=False):
    """history.md 픽스처. prepend=True 면 최신이 맨 위(session-j 형태)."""
    idx = range(n_decisions, 0, -1) if prepend else range(1, n_decisions + 1)
    out = []
    for i in idx:
        out.append(f"## [2026-01-{i:02d}] #{i} 결정 제목 {i}")
        out.append("- 결정: 무언가")
        # 기각 기재는 항상 번호가 작은 쪽(= 오래된 쪽)부터 n_neg 개
        if i <= n_neg:
            out.append(f"- 실험/실패: 실패사례{i} 구현·검증·기각. 재시도 시간낭비.")
    return "\n".join(out) + "\n"


with tempfile.TemporaryDirectory() as tmp:
    out = gen(tmp, history=hist(5, 3))
    check("기각 목록이 실린다", "이미 기각됨" in out and "실패사례1" in out, out)
    check("총 건수를 밝힌다", "총 3건" in out, out)
    check("착수 전 조회를 지시한다", "착수 전에 반드시 본다" in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # 기각 목록은 절단에 먹히면 안 되므로 파일 맨 앞이어야 한다
    out = gen(tmp, history=hist(5, 3))
    i_neg = out.find("이미 기각됨")
    i_dec = out.find("최근 결정")
    check("기각 목록이 최근 결정보다 앞", 0 < i_neg < i_dec, f"neg={i_neg} dec={i_dec}")

with tempfile.TemporaryDirectory() as tmp:
    # 결정이 많아도 최근 3개가 아니라 규약대로 10~20개 범위를 싣는다
    out = gen(tmp, history=hist(40, 0))
    shown = out.count("] #")
    check("최근 결정 12건을 싣는다", shown == 12, f"실제 {shown}건\n{out}")
    check("전체 건수를 밝힌다", "총 40건" in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # 실험/실패 기재가 아예 없으면 그 절을 만들지 않는다(빈 껍데기 금지)
    out = gen(tmp, history=hist(5, 0))
    check("기재 없으면 절 자체를 안 만든다", "이미 기각됨" not in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # 상한 초과 시 몇 줄 버렸는지 말한다. "상세는 원본 참조"만으로는 무엇이
    # 안 보이는지 알 수 없고, 그 침묵이 이 파일이 방치된 이유였다.
    long_neg = "\n".join(f"- 실험/실패: {'가'*300} {i}" for i in range(10))
    out = gen(tmp, history=hist(200, 0) + long_neg + "\n")
    if "절단됨" in out:
        check("절단 시 버린 줄 수를 말한다", "줄 절단됨" in out, out[-300:])
    else:
        check("절단 시 버린 줄 수를 말한다", len(out.splitlines()) <= 120,
              f"절단도 상한 준수도 아님: {len(out.splitlines())}줄")

with tempfile.TemporaryDirectory() as tmp:
    # 정렬 방향 실측. 규약은 append 지만 prepend(최신이 맨 위)로 쓰는 프로젝트가
    # 있다(session-j). 방향을 가정하고 tail 을 쓰면 가장 오래된 것을 집어서 최신 기각이
    # 조용히 빠진다 — 증상이 안 보이는 종류의 실패라 고정해 둔다.
    out = gen(tmp, history=hist(20, 20, prepend=True))
    check("prepend 에서 최신 기각을 집는다", "실패사례20" in out and "실패사례1 " not in out,
          out)
    shown = [l for l in out.splitlines() if "실패사례" in l]
    check("prepend 에서 10건만", len(shown) == 10, f"{len(shown)}건")
    check("prepend 에서 최신 결정을 집는다", "#20 결정" in out and "#1 결정" not in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # append 회귀 — 기존 동작이 그대로여야 한다
    out = gen(tmp, history=hist(20, 20, prepend=False))
    check("append 에서 최신 기각을 집는다", "실패사례20" in out and "실패사례1 " not in out, out)
    check("append 에서 최신 결정을 집는다", "#20 결정" in out and "#1 결정" not in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # 방향을 못 정하면 규약 기본(append=tail)으로 떨어진다. 오탐으로 head 를 쓰면
    # 정상 프로젝트가 옛 항목을 보게 되므로 판정 불가는 보수적으로 간다.
    out = gen(tmp, history="## 날짜 없는 헤더\n- 실험/실패: 하나뿐\n")
    check("판정 불가면 기본 동작", "하나뿐" in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # 분리 로그. 결정 로그를 history/<이름>.md 로 쪼개는 프로젝트가 있고(proj-b 는
    # history/lidar.md 에 기각 103건), 루트만 읽으면 통째로 시야 밖이었다.
    out = gen(tmp, history=hist(3, 2),
              extra={"lidar.md": "## [2026-05-01] #1 결정\n- 실험/실패: 분리로그기각 A\n"})
    check("분리 로그의 기각도 실린다", "분리로그기각 A" in out, out)
    check("출처를 파일명으로 구분한다", "lidar.md" in out and "history.md" in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # 월별 archive 는 제외한다(규약상 lazy load). 다만 뺐다는 사실을 적어야 한다 —
    # 조용히 빼면 "있는 줄 알았는데 없던" 항목이 다시 생긴다.
    out = gen(tmp, history=hist(3, 2),
              extra={"2026-07.md": "- 실험/실패: 아카이브기각 Z\n",
                     "2026-08-early.md": "- 실험/실패: 아카이브기각 Y\n"})
    check("월별 archive 는 제외", "아카이브기각" not in out, out)
    check("제외 사실을 명시", "월별 archive 는 제외" in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # 회귀: 기각이 0건이면 산술 오류로 파일 전체가 죽었었다(grep -c 가 0 을 출력하며
    # exit 1 이라 `|| echo 0` 이 값을 "0\n0" 으로 만들었다).
    out = gen(tmp, history=hist(40, 0))
    check("기각 0건이어도 나머지 절이 살아있다", "최근 결정" in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # progress.md 요약은 넣지 않는다. SessionStart 훅이 progress.md 를 통째로 따로
    # 주입하므로 중복이고, 6000바이트 예산만 먹는다 — dotfiles 에서 예산 초과로
    # 잘려나간 19줄이 전부 이 절이었다.
    out = gen(tmp, history=hist(10, 2),
              progress="# progress.md\nstatus: active\ntask: PROGRESS_SENTINEL\n")
    check("progress 요약을 넣지 않는다",
          "PROGRESS_SENTINEL" not in out and "진행 상태" not in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # 같은 입력이면 다시 쓰지 않는다. 헤더의 updated: 시각 때문에 매 턴 파일이 달라져
    # 저장소가 계속 dirty 해졌다(추적 트리에서 checkout·merge 를 막는다).
    d = Path(tmp)
    gen(tmp, history=hist(10, 2))
    a = d / "history" / "active.md"
    first_mt, first = a.stat().st_mtime_ns, a.read_text(encoding="utf-8")
    gen(tmp, history=hist(10, 2))
    check("같은 입력이면 재작성 안 함",
          a.stat().st_mtime_ns == first_mt and a.read_text(encoding="utf-8") == first,
          f"mtime {first_mt} -> {a.stat().st_mtime_ns}")
    # 입력이 바뀌면 당연히 다시 쓴다
    gen(tmp, history=hist(10, 3))
    check("입력이 바뀌면 다시 쓴다", a.read_text(encoding="utf-8") != first)

with tempfile.TemporaryDirectory() as tmp:
    # 수기본 사본에 실패하면 덮지 않는다. 덮고 나서 "사본 실패" 한 줄만 남기면
    # 이 가드가 막으려던 손실을 그대로 낸다.
    d = Path(tmp)
    (d / "history").mkdir()
    manual = "# 손으로 관리하는 요약\n- 유효한 결정\n"
    (d / "history" / "active.md").write_text(manual, encoding="utf-8")
    (d / "history").chmod(0o555)          # 사본 생성 불가
    try:
        out = gen(tmp, history=hist(10, 2))
        kept = (d / "history" / "active.md").read_text(encoding="utf-8")
    finally:
        (d / "history").chmod(0o755)
    check("사본 실패 시 수기본을 덮지 않는다", kept == manual, kept[:200])

with tempfile.TemporaryDirectory() as tmp:
    # 실을 게 없으면 파일을 안 만든다. 머리말뿐인 파일을 써버리면 주입기가
    # history.md 꼬리로 떨어지던 폴백을 잃는다.
    d = Path(tmp)
    out = gen(tmp, history="일지인데 규약 헤더가 없다\n다시 한 줄\n")
    check("실을 내용 없으면 파일을 안 만든다",
          not (d / "history" / "active.md").exists(), out[:200])
    check("안 쓴 이유를 rc 3 으로 알린다", last_rc == 3, f"rc={last_rc}")

with tempfile.TemporaryDirectory() as tmp:
    # "일부러 안 썼다"를 종료코드로 구분한다. Stop 훅은 mtime 이 움직였는지로 성공을
    # 판정하는데, 멱등 스킵은 mtime 을 안 움직이므로 그것만 두면 매 턴 실패로 보고
    # 영원히 재시도한다 — 실제로 홈 트리 두 곳에서 그렇게 나왔다.
    gen(tmp, history=hist(10, 2))
    check("처음 썼으면 rc 0", last_rc == 0, f"rc={last_rc}")
    log = Path.home() / ".claude" / "logs" / "stop-memory.log"
    gen(tmp, history=hist(10, 2))
    check("내용 같아 안 썼으면 rc 3", last_rc == 3, f"rc={last_rc}")
    # rc 3 은 사유가 로그에 남아야 한다. 안 남으면 Stop 훅 스탬프가 가리키는
    # "위 REFUSE/SKIP/NOCHANGE 줄" 이 없어서 읽는 사람이 원인을 못 찾는다.
    # (로그를 바이트 오프셋으로 잘라 읽지 않는다 — 한글이 섞여 있어 문자 인덱스와
    #  어긋난다. 이 tmp 경로가 이번 런에서만 쓰이므로 전체에서 찾으면 된다.)
    lines = log.read_text(encoding="utf-8", errors="ignore").splitlines() if log.exists() else []
    hit = [l for l in lines if "NOCHANGE" in l and tmp in l]
    check("안 쓴 사유를 로그에 남긴다", len(hit) >= 1, f"{lines[-3:]}")

with tempfile.TemporaryDirectory() as tmp:
    # 수기 관리본을 말없이 덮지 않는다. allowlist 에 트리를 추가하는 순간 이 스크립트가
    # 처음 돌면서 사람이 써온 active.md 를 통째로 갈아치운다(proj-b 363줄·
    # session_c_work 592줄). history/ 를 gitignore 한 저장소면 복구 경로가 없다.
    d = Path(tmp)
    (d / "history").mkdir()
    manual = "# 손으로 관리하는 요약\n- 아직 유효한 결정 하나\n"
    (d / "history" / "active.md").write_text(manual, encoding="utf-8")
    out = gen(tmp, history=hist(5, 2))
    baks = list((d / "history").glob("active.md.manual-*"))
    check("수기 관리본을 사본으로 남긴다", len(baks) == 1, f"{[b.name for b in baks]}")
    if baks:
        check("사본 내용이 원본 그대로", baks[0].read_text(encoding="utf-8") == manual)
    check("생성본이 사본 경로를 밝힌다",
          baks and baks[0].name in out and "수기 관리본" in out, out[:400])
    check("자동 생성으로 바뀌었음을 말한다", "다음 턴에 지워진다" in out, out[:400])

with tempfile.TemporaryDirectory() as tmp:
    # 자기 생성물은 사본 대상이 아니다 — 매 턴 사본이 쌓이면 그게 쓰레기다
    d = Path(tmp)
    gen(tmp, history=hist(5, 2))
    gen(tmp, history=hist(5, 2))
    baks = list((d / "history").glob("active.md.manual-*"))
    check("자동 생성본은 사본 안 만든다", len(baks) == 0, f"{[b.name for b in baks]}")

with tempfile.TemporaryDirectory() as tmp:
    # 주입 예산(6000바이트)을 넘기지 않는다. 줄 수만 지키면 이게 안 지켜진다 —
    # 한글은 문자당 3바이트라 규약 줄 수 안에서도 예산을 넘고, 넘긴 만큼은
    # session-start-context.sh 가 말없이 잘라서 어느 세션에도 안 보인다.
    long_neg = "\n".join(f"- 실험/실패: {'가'*200} 사례{i}" for i in range(40))
    out = gen(tmp, history=hist(30, 0) + long_neg + "\n")
    nbytes = len(out.encode("utf-8"))
    check("주입 예산 안에 들어간다", nbytes <= 6000, f"{nbytes}바이트\n{out[:200]}")
    check("바이트 절단을 밝힌다", "주입 예산" in out and "줄 절단됨" in out, out[-300:])
    try:
        out.encode("utf-8").decode("utf-8")
        ok = True
    except UnicodeError:
        ok = False
    check("바이트 절단이 문자를 안 쪼갠다", ok and "가가가" in out, out[-200:])
    check("절단 후에도 기각 절이 남는다", "이미 기각됨" in out, out[:300])

with tempfile.TemporaryDirectory() as tmp:
    # 예산 안이면 절단 문구를 만들지 않는다(오탐 방지)
    out = gen(tmp, history=hist(5, 2))
    check("예산 안이면 절단 문구 없음", "주입 예산" not in out, out)

with tempfile.TemporaryDirectory() as tmp:
    # 고정 서문. 프로젝트마다 매 세션 맨 위에 보여야 하는 상시 제약이 있는데(peer 의
    # sudo 금지·공용 계정 등) 생성기는 history.md 에서 뽑은 것만 실을 수 있어 그게
    # 재생산되지 않았다. 그래서 peer 이 자동생성을 못 켰다.
    out = gen(tmp, history=hist(5, 2),
              extra={"active-preamble.md": "## 상시 제약\n- SUDO_SENTINEL 금지\n"})
    check("서문을 그대로 싣는다", "SUDO_SENTINEL" in out, out[:300])
    check("서문이 기각 목록보다 앞", 0 <= out.find("SUDO_SENTINEL") < out.find("이미 기각됨"),
          f"{out.find('SUDO_SENTINEL')} vs {out.find('이미 기각됨')}")
    check("서문을 결정 로그로 착각하지 않는다", "출처 1개" in out, out[:400])

with tempfile.TemporaryDirectory() as tmp:
    # 서문이 예산을 다 먹으면 기각 목록이 밀려난다. 상한을 두고 잘랐다고 말한다.
    big = "## 서문\n" + "\n".join(f"- 제약 {i}" for i in range(80)) + "\n"
    out = gen(tmp, history=hist(5, 2), extra={"active-preamble.md": big})
    check("긴 서문은 자르고 밝힌다", "앞 40줄만 실었다" in out, out[:600])
    check("서문을 잘라도 기각 목록은 남는다", "이미 기각됨" in out, out[:600])

with tempfile.TemporaryDirectory() as tmp:
    # 한글이 깨지지 않는다. 바이트 절단으로 문자를 반토막 내면 SessionStart
    # 주입이 통째로 날아갔던 이력이 있다(03c0042).
    out = gen(tmp, history=hist(5, 3))
    try:
        out.encode("utf-8").decode("utf-8")
        ok = "실패사례" in out
    except UnicodeError:
        ok = False
    check("한글이 온전하다", ok, out[:300])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
