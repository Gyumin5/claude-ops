#!/usr/bin/env python3
"""끝난 로그인이 지우는 것은 자기가 만든 것뿐인가.

계기는 둘째 기계 2026-11-06 재현이다. 같은 계정으로 로그인을 다시 띄우면 앞서 떠 있던 낡은
유닛이 끝나면서 정리 단계에서 login/pid 와 login/in.fifo 를 지웠다. 그 자리는 새 로그인도
쓰는 자리라, 새 로그인에 승인 코드를 넣을 통로가 통째로 사라진다. 살아 있는 프로세스의
/proc/<pid>/fd 로 우회해야 했다.

주인 표식은 pid 파일 하나다. 파일 inode 로는 못 가른다 — 지우고 같은 자리에 다시 만들면
같은 번호가 그대로 다시 쓰이는 것을 이 검사를 쓰다가 실제로 봤다. 그래서 inode 비교를
안 쓴다.

고정하는 것.
⑴ pid 가 내 것이면 둘 다 지운다 — 끝난 자리가 "기다리는 중" 으로 보이지 않아야 한다.
⑵ 뒤에 온 로그인이 pid 를 덮어썼으면 pid 도 파이프도 안 지운다.
⑶ pid 파일이 없으면 아무것도 안 지운다 — 새 로그인이 아직 자기 pid 를 안 적었을 수 있다.
⑷ 파이프가 이미 없어도 예외로 죽지 않는다.
"""
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "bin" / "claude-accounts"

fails = []


def check(name, cond, detail=""):
    print(("PASS  " + name) if cond
          else ("FAIL  " + name + (f"  — {str(detail)[:300]}" if detail else "")))
    if not cond:
        fails.append(name)


def load_tool():
    # 확장자가 없는 실행 파일이라 로더를 직접 준다 — 안 주면 spec 이 None 으로 온다.
    loader = importlib.machinery.SourceFileLoader("claude_accounts_tool", str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def make_pair(d, pid_text):
    pidfile, fifo = d / "pid", d / "in.fifo"
    pidfile.write_text(pid_text, encoding="utf-8")
    os.mkfifo(fifo, 0o600)
    return pidfile, fifo


mod = load_tool()
clean = getattr(mod, "cleanup_stale_login", None)
check("정리 함수가 있다", callable(clean))
if not callable(clean):
    print("\n실패: 정리 함수 없음")
    sys.exit(1)

with tempfile.TemporaryDirectory() as td:
    # ⑴ 내 것이면 둘 다 지운다
    d = Path(td) / "mine"
    d.mkdir()
    pidfile, fifo = make_pair(d, "4242")
    clean(pidfile, fifo, 4242)
    check("내가 적은 pid 는 지운다", not pidfile.exists())
    check("내 pid 와 짝인 파이프는 지운다", not fifo.exists())

    # ⑵ 뒤에 온 로그인이 pid 를 덮어쓴 경우 — 이번 결함의 재현 칸이다
    d = Path(td) / "taken"
    d.mkdir()
    pidfile, fifo = make_pair(d, "4242")
    pidfile.write_text("9999", encoding="utf-8")      # 새 로그인이 자기 pid 를 적었다
    clean(pidfile, fifo, 4242)
    check("뒤에 온 로그인의 pid 는 안 지운다",
          pidfile.exists() and pidfile.read_text(encoding="utf-8").strip() == "9999")
    check("뒤에 온 로그인의 파이프는 안 지운다", fifo.exists())

    # 파이프를 새로 만든 경우에도 pid 가 남의 것이면 그대로 둔다
    d = Path(td) / "taken-fresh"
    d.mkdir()
    pidfile, fifo = make_pair(d, "4242")
    pidfile.write_text("9999", encoding="utf-8")
    fifo.unlink()
    os.mkfifo(fifo, 0o600)
    clean(pidfile, fifo, 4242)
    check("새로 만든 파이프도 pid 가 남의 것이면 그대로 둔다", fifo.exists())

    # ⑶ pid 파일이 없으면 아무것도 안 지운다
    d = Path(td) / "nopid"
    d.mkdir()
    pidfile, fifo = make_pair(d, "4242")
    pidfile.unlink()
    clean(pidfile, fifo, 4242)
    check("pid 파일이 없으면 파이프를 안 지운다", fifo.exists())

    # ⑷ 파이프가 이미 없어도 죽지 않는다
    d = Path(td) / "gone"
    d.mkdir()
    pidfile, fifo = make_pair(d, "4242")
    fifo.unlink()
    try:
        clean(pidfile, fifo, 4242)
        check("파이프가 이미 없어도 죽지 않는다", not pidfile.exists())
    except Exception as exc:                                   # noqa: BLE001
        check("파이프가 이미 없어도 죽지 않는다", False, str(exc))

print(f"\n{'실패: ' + ', '.join(fails) if fails else '모두 통과'}")
sys.exit(1 if fails else 0)
