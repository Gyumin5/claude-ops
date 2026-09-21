#!/usr/bin/env python3
"""사본 대상과 열어주는 디렉토리를 브리프가 지목한 경로로 한정하는 계약 검사.

2026-09-15 실회차(run-20260915T052322Z)에서 원본 경로가 사본 밖으로 새는 길 셋이
실제로 발동했다. 셋 다 여기서 고정한다.

하나. 프롬프트 본문에 적힌 절대경로를 전부 사본으로 떴다. 참여자가 답변 산문에 적은
경로가 다음 호출에서 실제로 열렸다 — 제미나이 비판자가 사본 경로에서 접두를 떼어
저장소 경로를 적었고, 그것 때문에 중재자 사본이 저장소를 통째로 떠서 상한에 걸렸다.

둘. 상한으로 빠진 파일 고지가 경로 치환이 끝난 뒤에 붙으면서 원본 절대경로를 다시
프롬프트에 넣었다. 고지는 사본 경로로 적어야 한다 — 받는 쪽에도 그게 맞다.

셋. run_planner 는 call_agent 를 안 거쳐 사본도 치환도 없이 원문을 넘기고 작업 위치도
안 정했다. --auto-roles 회차에만 열리던 구멍이다.
"""
import os
import importlib.machinery
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


mod = load("ai_debate_briefpath", REPO / "bin" / "ai-debate")
real_run = subprocess.run


# ── 1. 브리프 경로 목록 ────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp).resolve()
    brief_dir = root / "target"
    other_dir = root / "elsewhere"
    (brief_dir / "sub").mkdir(parents=True)
    other_dir.mkdir()
    (brief_dir / "a.txt").write_text("가\n", encoding="utf-8")
    (brief_dir / "sub" / "b.txt").write_text("나\n", encoding="utf-8")
    (other_dir / "secret.txt").write_text("남의 것\n", encoding="utf-8")

    picked = mod.set_brief_paths(f"{brief_dir} 를 검토해 달라")
    check("브리프에 적힌 경로만 목록에 든다", picked == [str(brief_dir)], str(picked))
    check("브리프 경로 자신이 포함된다", mod.from_brief(brief_dir))
    check("브리프 경로 아래 파일이 포함된다", mod.from_brief(brief_dir / "sub" / "b.txt"))
    check("형제 디렉토리는 제외된다", not mod.from_brief(other_dir / "secret.txt"))

    # ── 2. 브리프 밖 경로는 사본을 안 뜬다 ─────────────────────────────
    dest = root / "snap"
    prompt = (f"검토 대상은 {brief_dir} 다.\n"
              f"[앞 라운드] 참여자가 이렇게 적었다: {other_dir} 도 같이 보면 좋겠다.")
    staged, made = mod.stage_readonly(prompt, dest)
    check("브리프 경로는 사본을 뜬다", made and (dest / str(brief_dir).lstrip("/") / "a.txt").is_file())
    check("브리프 밖 경로는 사본을 안 뜬다",
          not (dest / str(other_dir).lstrip("/")).exists())
    check("브리프 밖 경로는 프롬프트에서 지워진다",
          "브리프에 없는 경로" in staged and str(other_dir) not in staged,
          staged[-160:])
    # 사본 경로는 원본 경로를 통째로 품는다(사본은 원본 트리를 미러링한다). 그래서
    # "원본 경로가 안 남았다" 는 사본 경로를 걷어낸 나머지에서 봐야 한다.
    def bare(text, snap_root):
        """사본 경로를 통째로 지운 나머지. 여기 원본 경로가 남으면 진짜 누출이다."""
        return text.replace(str(snap_root / str(brief_dir).lstrip("/")), "<사본>")

    check("브리프 경로는 사본 경로로 바뀐다",
          str(dest) in staged and str(brief_dir) not in bare(staged, dest), staged[:200])

    # ── 3. 빠진 파일 고지는 사본 경로로 적는다 ─────────────────────────
    keep = mod.SNAP_MAX_FILES
    mod.SNAP_MAX_FILES = 1
    try:
        dest2 = root / "snap2"
        mod.set_brief_paths(f"{brief_dir}")
        out2, _ = mod.stage_readonly(f"{brief_dir} 를 봐라", dest2)
    finally:
        mod.SNAP_MAX_FILES = keep
    check("상한에 걸리면 불완전 고지를 붙인다", "[사본 불완전]" in out2, out2[-200:])
    check("고지에 원본 절대경로를 적지 않는다",
          str(brief_dir) not in bare(out2, dest2), out2[-200:])
    check("고지는 사본 경로로 적는다", str(dest2) in out2, out2[-200:])


# ── 4. 열어주는 디렉토리는 이 호출의 사본 뿌리 하나다 ──────────────────
class FakeProc:
    returncode = 0
    stdout = '{"position": "좋다", "should_proceed": true, "roles": ["debater"]}'
    stderr = ""


calls = []


def fake_run(cmd, **kw):
    calls.append({"cmd": list(cmd), "cwd": kw.get("cwd", "_없음"), "input": kw.get("input", "")})
    return FakeProc()


mod.subprocess.run = fake_run
mod.sync_iso_credentials = lambda: (True, "")
try:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run-20260915T000000Z"
        run_dir.mkdir(parents=True)
        # 이 기계의 실제 홈이어야 한다 — 가려내기는 홈 경로를 보고 판단한다.
        leaked = os.path.expanduser("~/dotfiles")
        mod.set_brief_paths("경로 없는 브리프")

        for agent in ("gemini", "claude-fable"):
            label = f"r2.critic.{agent}"
            snap = run_dir / "snapshot" / label
            snap.mkdir(parents=True)
            calls.clear()
            # 앞 라운드 참여자가 원본 경로를 산문에 적어 넣은 상황을 그대로 재현한다.
            mod._call_agent_once(agent, "critic", f"[앞 라운드] {leaked} 를 보면", run_dir, label)
            cmd = calls[-1]["cmd"]
            opened = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--add-dir"]
            check(f"{agent}: 사본 뿌리 하나만 연다", opened == [str(snap)], str(opened))
            check(f"{agent}: 참여자가 적은 원본 경로를 열지 않는다",
                  leaked not in opened, str(opened))

        # ── 5. 역할 플래너 ─────────────────────────────────────────────
        calls.clear()
        picked = mod.run_planner(f"{leaked}/bin/ai-debate 를 검토하자", 3, run_dir)
        pc = calls[-1]
        check("플래너는 역할을 고른다", picked == ["debater"], str(picked))
        check("플래너에 원본 절대경로를 넘기지 않는다", leaked not in pc["input"],
              pc["input"][-160:])
        check("플래너는 이 런 전용 빈 디렉토리에서 돈다",
              pc["cwd"] == str(run_dir / "planner-cwd"), str(pc["cwd"]))
        check("플래너에도 --cd 를 같은 값으로 준다",
              "--cd" in pc["cmd"] and pc["cmd"][pc["cmd"].index("--cd") + 1] == pc["cwd"],
              str(pc["cmd"]))
finally:
    mod.subprocess.run = real_run

# ── 5. 감싼 경로와 못 쓰는 경로 ────────────────────────────────────────
# 옆 기계 run-20260917T020033Z: 브리프가 검토 대상을 역따옴표로 감쌌더니 닫는 역따옴표가
# 경로에 붙어 존재하지 않는 경로가 됐고, 사본이 안 만들어진 채 참여자 셋 전부가 "지정된
# 경로를 열 수 없었다" 고 밝혔는데 회차는 합의로 정상 종료했다. 조용히 실패하는 게
# 결함이라, 감싸개를 벗기는 것과 하나도 못 쓸 때 멈추는 것 둘 다 고정한다.
# 고치기 전 판본에는 아래 두 이름이 없다 — 크래시 대신 실패로 떨어지게 기본값으로 받는다.
_classify = getattr(mod, "classify_brief_paths", None)
_abort_msg = getattr(mod, "brief_path_abort_message", None)


def split(task):
    """(쓸 수 있는 경로, 못 쓰는 경로) — 고치기 전 판본에는 함수가 없으니 빈 값으로 떨어뜨린다."""
    return _classify(task) if _classify else ([], [])

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp).resolve()
    real_dir = root / "target"
    real_dir.mkdir()
    (real_dir / "a.txt").write_text("가\n", encoding="utf-8")
    gone = root / "없는자리"

    picked = mod.set_brief_paths(f"검토 대상: `{real_dir}` 를 봐라")
    check("역따옴표로 감싼 경로도 목록에 든다", picked == [str(real_dir)], str(picked))
    check("정규식이 역따옴표를 경로에 안 먹는다",
          mod._ABS_PATH_RE.findall(f"`{real_dir}`") == [str(real_dir)],
          str(mod._ABS_PATH_RE.findall(f"`{real_dir}`")))

    ok, bad = split(f"{real_dir} 를 봐라")
    check("전부 쓸 수 있으면 못 쓰는 목록이 비어 있다",
          ok == [str(real_dir)] and bad == [], f"{ok} / {bad}")

    ok, bad = split(f"검토 대상: {gone} 를 봐라")
    check("없는 경로는 사유와 함께 잡힌다",
          bad == [(str(gone), "그 경로가 없다")], str(bad))
    check("그때 쓸 수 있는 경로가 0 이다 — 이게 중단 조건이다", bad and not ok, str(ok))
    # 허용 목록은 실재를 안 본다. 그래서 그것만으로는 이 상태를 못 가른다 — 여기가
    # 조용히 지나가던 자리다.
    check("허용 목록만으로는 이 상태를 못 가른다",
          mod.set_brief_paths(f"검토 대상: {gone} 를 봐라") == [str(gone)],
          str(mod.BRIEF_PATHS))

    ok, bad = split(f"{real_dir} 와 {gone} 를 봐라")
    check("하나라도 쓸 수 있으면 중단 조건이 아니다",
          ok == [str(real_dir)] and bad == [(str(gone), "그 경로가 없다")],
          f"{ok} / {bad}")
    ok_deny, bad_deny = split(f"{root}/.ssh/id_rsa 를 봐라")
    check("사본 금지 자리는 다른 사유로 잡힌다",
          bad_deny == [(f"{root}/.ssh/id_rsa", "사본으로 뜰 수 없는 자리다")] and not ok_deny,
          f"{ok_deny} / {bad_deny}")
    _, bad = split(f"검토 대상: {gone} 를 봐라")

    msg = _abort_msg(bad) if (_abort_msg and bad) else ""
    check("중단 사유문이 못 연 경로를 그대로 적는다", str(gone) in msg, msg)
    check("중단 사유문이 회차를 시작하지 않는다고 밝힌다",
          "회차를 시작하지 않는다" in msg, msg)
    check("중단 사유문이 감싸개를 원인으로 짚는다", "역따옴표" in msg, msg)


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
