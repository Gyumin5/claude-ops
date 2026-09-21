#!/usr/bin/env python3
"""기록 검증 실패를 다음 세션에 전달하는 계약 검사.

progress.md 는 세션이 쓰고 다음 세션이 사실로 물려받는다. 자동 갱신기가 근거를 못 대는
제안을 버려도 그 사실은 로그에만 남아 아무도 안 봤다(실측: 근거 없는 패치 거절이 이틀에
두 건, 그리고 한 기계는 남의 세션 제약을 한 달 넘게 자기 것으로 들고 일했다).

3주 실험으로 여는 최소판이라 고정하는 것이 다섯이다.

  1. 거절의 종류와 횟수만 남긴다. 제안 본문·정정 명령은 저장하지 않는다.
  2. 같은 종류는 합치고, 오래된 것은 떨어지고, 종류 수에 상한이 있다.
  3. 호스트·프로젝트가 다르거나 깨졌거나 만료된 자료는 경고로 쓰지 않는다.
  4. 사실 판정을 하지 않는다 — 미판정이라고 쓰고 원자료 경로를 같이 준다.
     내용 지문이 바뀌었으면 해결됐다고 하지 않고 반영 여부 미확인으로 쓴다.
  5. 주입 자리를 새로 늘리지 않는다. 쓴 만큼 본문 예산에서 빼고, 다른 블록은 안 건드린다.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIAG = REPO / "bin" / "claude-progress-diag"
HOOK = REPO / "claude" / "hooks" / "session-start-context.sh"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


def diag(home, *args):
    """고치기 전 판본에는 이 도구가 없다 — 크래시 대신 실패로 떨어지게 받는다."""
    if not DIAG.exists():
        return 127, "", "no such tool"
    p = subprocess.run([sys.executable, str(DIAG)] + list(args),
                       env=dict(os.environ, HOME=str(home)),
                       capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def state_file(home, project):
    key = hashlib.sha256(project.encode("utf-8")).hexdigest()[:16]
    return Path(home) / ".claude/state/progress-updater/diagnostics" / f"{key}.json"


def load_state(sf):
    """고치기 전 판본에는 이 파일 자체가 없다 — 크래시 대신 실패로 떨어지게 받는다."""
    try:
        return json.loads(sf.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(sf, doc):
    """고치기 전 판본에는 상태 폴더 자체가 없다 — 크래시 대신 실패로 떨어지게 쓴다."""
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def make_project(root, body="# progress.md\nupdated: 2026-09-17 00:00 KST\n"):
    proj = Path(root) / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    prog = proj / "progress.md"
    prog.write_text(body, encoding="utf-8")
    return str(proj), str(prog), hashlib.sha256(prog.read_bytes()).hexdigest()[:16]


# ── 기록 ────────────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / "home"
    home.mkdir()
    proj, prog, fp = make_project(tmp)

    diag(home, "record", "--project", proj, "--mode", "propose",
         "--transcript", "t1", "--fingerprint", fp, "--kind", "근거가 입력에 없다")
    sf = state_file(home, proj)
    check("거절을 기록한다", sf.exists(), str(sf))
    doc = load_state(sf)
    ev = (doc.get("events") or [{}])[0]
    check("종류와 횟수를 남긴다",
          ev.get("kind") == "근거가 입력에 없다" and ev.get("count") == 1, str(doc))
    check("프로젝트와 지문을 같이 남긴다",
          doc.get("project") == proj and doc.get("fingerprint") == fp, str(doc))
    check("남기는 칸이 정해져 있다 — 제안 본문이 들어갈 자리가 없다",
          set(ev) <= {"kind", "count", "first", "last", "last_ts", "mode", "transcript"},
          str(ev))

    diag(home, "record", "--project", proj, "--mode", "propose",
         "--transcript", "t2", "--fingerprint", fp, "--kind", "근거가 입력에 없다")
    doc = load_state(sf)
    evs = doc.get("events") or []
    check("같은 종류는 한 줄로 합친다",
          len(evs) == 1 and evs[0].get("count") == 2, str(evs))

    diag(home, "record", "--project", proj, "--mode", "apply",
         "--transcript", "t3", "--fingerprint", fp,
         "--kind", "미래 날짜를 일어난 일로 적었다", "--kind", "바꿀 대상이 너무 짧다")
    doc = load_state(sf)
    evs = doc.get("events") or []
    check("다른 종류는 따로 선다", len(evs) == 3, str([e["kind"] for e in evs]))

    before = load_state(sf)
    diag(home, "record", "--project", proj, "--mode", "propose", "--fingerprint", fp)
    check("거절이 없으면 아무것도 안 쓴다", load_state(sf) == before)

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / "home"
    home.mkdir()
    proj, prog, fp = make_project(tmp)
    sf = state_file(home, proj)
    sf.parent.mkdir(parents=True, exist_ok=True)
    import socket
    old = time.time() - 40 * 86400
    sf.write_text(json.dumps({
        "schema": 1, "host": socket.gethostname(), "project": proj, "fingerprint": fp,
        "events": [{"kind": "옛 종류", "count": 9, "last": "옛날", "last_ts": old}]},
        ensure_ascii=False), encoding="utf-8")
    diag(home, "record", "--project", proj, "--mode", "propose", "--fingerprint", fp,
         "--kind", "근거가 입력에 없다")
    doc = load_state(sf)
    evs = doc.get("events") or []
    check("만료된 종류는 떨어진다",
          [e.get("kind") for e in evs] == ["근거가 입력에 없다"], str(evs))

    for i in range(20):
        diag(home, "record", "--project", proj, "--mode", "propose", "--fingerprint", fp,
             "--kind", f"종류{i}")
    doc = load_state(sf)
    evs = doc.get("events") or []
    check("종류 수에 상한이 있다", 0 < len(evs) <= 12, str(len(evs)))

# ── 전달 ────────────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / "home"
    home.mkdir()
    proj, prog, fp = make_project(tmp)

    rc, out, _ = diag(home, "render", "--project", proj, "--progress", prog)
    check("기록이 없으면 아무 말도 안 한다", rc == 0 and out == "", repr(out[:120]))

    diag(home, "record", "--project", proj, "--mode", "propose", "--fingerprint", fp,
         "--kind", "근거가 입력에 없다")
    rc, out, _ = diag(home, "render", "--project", proj, "--progress", prog)
    check("종류와 횟수를 전한다", "근거가 입력에 없다" in out and "1회" in out, out[:200])
    check("사실 판정이 아니라고 못박는다", "미판정" in out, out[:200])
    check("원자료 경로를 같이 준다", "progress-updater.log" in out, out[:300])
    check("지문이 같으면 같은 내용이라고 쓴다", "같은 내용" in out, out[:300])

    Path(prog).write_text("# progress.md\n바뀐 내용\n", encoding="utf-8")
    rc, out, _ = diag(home, "render", "--project", proj, "--progress", prog)
    check("지문이 바뀌면 해결됐다고 하지 않는다", "미확인" in out, out[:300])

    sf = state_file(home, proj)
    doc = load_state(sf)
    doc["host"] = "다른기계"
    save_state(sf, doc)
    rc, out, _ = diag(home, "render", "--project", proj, "--progress", prog)
    check("다른 호스트 자료는 경고로 안 쓴다", rc == 0 and out == "", out[:120])

    doc["host"] = __import__("socket").gethostname()
    doc["project"] = "/다른/트리"
    save_state(sf, doc)
    rc, out, _ = diag(home, "render", "--project", proj, "--progress", prog)
    check("다른 프로젝트 자료는 경고로 안 쓴다", rc == 0 and out == "", out[:120])

    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text("{망가진 줄", encoding="utf-8")
    rc, out, _ = diag(home, "render", "--project", proj, "--progress", prog)
    check("깨진 자료에 안 걸려 넘어진다", rc == 0 and out == "", out[:120])

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / "home"
    home.mkdir()
    proj, prog, fp = make_project(tmp)
    for i in range(12):
        diag(home, "record", "--project", proj, "--mode", "propose", "--fingerprint", fp,
             "--kind", f"아주 긴 거절 종류 이름 {i} " + "가" * 60)
    rc, out, _ = diag(home, "render", "--project", proj, "--progress", prog, "--cap", "1024")
    check("상한을 안 넘는다", len(out.encode("utf-8")) <= 1024,
          str(len(out.encode("utf-8"))))
    check("잘렸으면 잘렸다고 쓴다", "잘림" in out, out[-120:])

# ── 세션 시작 주입 ──────────────────────────────────────────────────────
def run_hook(home, cwd, path_extra):
    p = subprocess.run(["bash", str(HOOK)], input=json.dumps({"cwd": str(cwd)}),
                       env=dict(os.environ, HOME=str(home),
                                PATH=f"{path_extra}:{os.environ['PATH']}"),
                       capture_output=True, text=True)
    try:
        ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    except Exception:
        ctx = ""
    return p, ctx


with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / "home"
    (home / ".claude").mkdir(parents=True)
    long_body = "# progress.md\nupdated: 2026-09-17 00:00 KST\n" + ("가나다라마바사\n" * 2000)
    proj, prog, fp = make_project(tmp, long_body)

    p, ctx = run_hook(home, proj, str(REPO / "bin"))
    check("진단이 없으면 훅이 그대로 돈다", p.returncode == 0 and "progress.md" in ctx,
          p.stderr[:200])
    # 예산은 바이트로 매긴다. 글자 수로 보면 경로 같은 영문이 섞일 때 늘어난 것처럼 보인다.
    plain_len = len(ctx.encode("utf-8"))

    diag(home, "record", "--project", proj, "--mode", "propose", "--fingerprint", fp,
         "--kind", "근거가 입력에 없다")
    p, ctx = run_hook(home, proj, str(REPO / "bin"))
    check("훅 출력이 유효한 JSON 이다", p.returncode == 0 and ctx != "", p.stdout[:200])
    check("진단이 progress.md 보다 먼저 온다",
          0 <= ctx.find("기록 검증 실패") < ctx.find("## progress.md"),
          f"{ctx.find('기록 검증 실패')} / {ctx.find('## progress.md')}")
    # 글자 경계에서 잘린 조각을 버리는 만큼(최대 한 글자 4바이트) 앞뒤가 어긋난다.
    check("주입 총량이 늘지 않는다 — 쓴 만큼 본문에서 뺐다",
          len(ctx.encode("utf-8")) <= plain_len + 4,
          f"{plain_len} -> {len(ctx.encode('utf-8'))} 바이트")
    check("본문이 사라지지는 않는다", "updated: 2026-09-17" in ctx, ctx[:200])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
