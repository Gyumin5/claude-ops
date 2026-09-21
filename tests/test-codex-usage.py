#!/usr/bin/env python3
"""코덱스 사용량 집계 계약 검사.

고정하는 것: 오래 열려 있는 대화가 최근 구간 집계에서 빠지면 안 된다.
codex 는 대화를 시작한 날짜 폴더에 기록을 계속 덧붙인다. 옛 판은 창 시작 하루
전부터 오늘까지의 날짜 폴더만 훑어서, 13일째 살아 있는 대화 하나가 통째로
빠졌다(2026-09-15 옆 기계 실측: 7일치 1억 2,513만 vs 실제 3억 6,522만).

  1. 후보는 날짜 폴더가 아니라 최종수정 시각으로 고른다 — 옛 폴더의 살아 있는 대화도 든다.
  2. 최종수정이 창 밖인 파일은 안 든다.
  3. 파일이 들어와도 창 밖 기록은 줄 단위로 걸러진다.
  4. 작업 디렉토리로 세션을 가르고 합계가 보존된다.
  5. 사람이 읽는 출력에 그 대화의 토큰과 세션 이름이 실제로 나온다.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"\n      {detail}" if not cond and detail else ""))


def load(name, path, home):
    """모듈 전역 SESSIONS 가 import 시점의 HOME 을 읽으므로 먼저 바꿔 둔다."""
    old = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        loader = importlib.machinery.SourceFileLoader(name, str(path))
        spec = importlib.util.spec_from_loader(name, loader)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        loader.exec_module(mod)
        return mod
    finally:
        if old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old


def usage(total):
    return {"input_tokens": total // 2, "cached_input_tokens": total // 4,
            "output_tokens": total // 4, "reasoning_output_tokens": total // 8,
            "total_tokens": total}


def rollout(path, cwd, records, mtime):
    """records: [(KST datetime, total_tokens 또는 used_percent 튜플)]"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "session_meta", "payload": {"cwd": cwd}},
                        ensure_ascii=False)]
    for when, kind, val in records:
        ts = when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if kind == "tokens":
            lines.append(json.dumps(
                {"timestamp": ts, "type": "token_usage_record",
                 "payload": {"usage": usage(val)}}, ensure_ascii=False))
        else:
            lines.append(json.dumps(
                {"timestamp": ts, "type": "event_msg",
                 "payload": {"rate_limits": {"primary": {
                     "used_percent": val, "window_minutes": 300,
                     "resets_at": int(when.timestamp()) + 3600}}}},
                ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))


with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    now = datetime.now(KST)
    sess = home / ".codex" / "sessions"

    # 13일 전에 시작해 지금도 살아 있는 대화 — 폴더는 옛 날짜, 기록은 최근
    old_start = now - timedelta(days=13)
    live = sess / f"{old_start:%Y/%m/%d}" / "rollout-live.jsonl"
    rollout(live, "/home/user/session-b",
            [(now - timedelta(days=12), "tokens", 111),          # 창 밖 기록
             (now - timedelta(hours=3), "tokens", 900_000),      # 창 안
             (now - timedelta(hours=2), "limit", 91.5)],
            mtime=(now - timedelta(hours=2)).timestamp())

    # 오늘 폴더의 평범한 대화
    today = sess / f"{now:%Y/%m/%d}" / "rollout-today.jsonl"
    rollout(today, "/home/user/dotfiles",
            [(now - timedelta(hours=1), "tokens", 100_000),
             (now - timedelta(hours=1), "limit", 92.0)],
            mtime=(now - timedelta(hours=1)).timestamp())

    # 오래 전에 끝난 대화 — 창 밖
    dead_day = now - timedelta(days=30)
    dead = sess / f"{dead_day:%Y/%m/%d}" / "rollout-dead.jsonl"
    rollout(dead, "/home/user/session-a",
            [(dead_day, "tokens", 777_000)], mtime=dead_day.timestamp())

    mod = load("codex_usage_mod", REPO / "bin" / "codex-usage", home)
    start = now - timedelta(hours=24)
    cands = mod.candidate_files(start)
    names = sorted(Path(p).name for p in cands)

    check("옛 폴더의 살아 있는 대화를 후보로 든다", "rollout-live.jsonl" in names, str(names))
    check("오늘 폴더의 대화도 든다", "rollout-today.jsonl" in names, str(names))
    check("최종수정이 창 밖이면 안 든다", "rollout-dead.jsonl" not in names, str(names))

    turns, limits = mod.parse(cands, start)
    totals = sorted(u.get("total_tokens", 0) for _, _, u in turns)
    check("창 안 기록만 센다", totals == [100_000, 900_000], str(totals))
    check("한도 관측도 읽는다", len(limits) == 2, str(len(limits)))

    rows = mod.by_session(turns)
    check("작업 디렉토리로 세션을 가른다",
          set(rows) == {"/home/user/session-b", "/home/user/dotfiles"}, str(set(rows)))
    check("세션별 합계가 전체 합계와 같다",
          sum(v["합계"] for v in rows.values()) == 1_000_000,
          str({k: v["합계"] for k, v in rows.items()}))
    check("가장 많이 쓴 세션을 집어낼 수 있다",
          max(rows.items(), key=lambda kv: kv[1]["합계"])[0] == "/home/user/session-b",
          str(rows))

    # 작업 이름: 잎 폴더로 고르면 한 작업이 여러 줄로 흩어진다(옆 기계 관측).
    os.environ["HOME"] = str(home)
    debate = f"{home}/claude-work/ai-debate/run-20260915T010203Z/snapshot"
    for path, want in [
        (f"{home}/work/test/session-d", "work/test/session-d"),
        (f"{debate}/arbiter", "claude-work/ai-debate"),
        (f"{debate}/r2.debater.codex", "claude-work/ai-debate"),
        (f"{home}/session_c_work", "session_c_work"),
        (str(home), "홈 디렉토리"),
        ("/srv/other/thing", "/srv/other/thing"),
    ]:
        got = mod.work_name(path)
        check(f"작업 이름 {path[-30:]} → {want}", got == want, f"실제 {got}")
    check("한 토론의 잎 폴더들이 한 이름으로 묶인다",
          len({mod.work_name(f"{debate}/arbiter"),
               mod.work_name(f"{debate}/r1.debater.codex"),
               mod.work_name(f"{debate}/r2.critic.gemini")}) == 1)

    env = dict(os.environ)
    env["HOME"] = str(home)
    r = subprocess.run([sys.executable, str(REPO / "bin" / "codex-usage"),
                        "--hours", "24", "--tokens"],
                       capture_output=True, text=True, env=env, timeout=120)
    check("사람이 읽는 출력이 정상 종료", r.returncode == 0, r.stderr[-300:])
    check("살아 있는 대화의 토큰이 합계에 든다", "1,000,000" in r.stdout,
          r.stdout[-400:])
    check("세션별 표에 그 작업 이름이 나온다",
          "session-b" in r.stdout and "세션별" in r.stdout, r.stdout[-400:])

    rj = subprocess.run([sys.executable, str(REPO / "bin" / "codex-usage"),
                         "--hours", "24", "--json"],
                        capture_output=True, text=True, env=env, timeout=120)
    check("기계용 출력이 정상 종료", rj.returncode == 0, rj.stderr[-300:])
    try:
        d = json.loads(rj.stdout)
    except ValueError as e:
        d = {}
        check("기계용 출력이 JSON 이다", False, str(e))
    else:
        check("기계용 출력이 JSON 이다", True)
    check("감시가 읽는 rate_limit 칸이 그대로 있다",
          isinstance(d.get("rate_limit"), dict) and "primary" in d["rate_limit"],
          str(d.get("rate_limit"))[:200])
    check("기계용 출력에도 세션별 칸이 있다",
          d.get("by_session") and d["by_session"][0]["cwd"] == "/home/user/session-b",
          str(d.get("by_session"))[:200])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
