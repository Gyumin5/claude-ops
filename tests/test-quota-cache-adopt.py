#!/usr/bin/env python3
"""계정을 갈아 끼울 때 한도 수치를 그 계정 것으로 바꿔 다는 경로.

전환은 자격 파일만 바꾸는데 한도 수치는 호스트가 파일 하나를 같이 쓴다. 바꾼 직후 그
파일에는 떠나는 계정의 숫자가 남아 있어서, 비우지 않으면 새 계정의 여유를 옛 계정의
숫자로 보여 준다. 비우면 화면이 한동안 빈다 — 그 자리를 그 계정이 마지막으로 보인
수치로 채우는 것이 이 명령이다(운영자 지시).

고정하는 것 여섯.
⑴ --to 가 그 홈의 수치를 live 에 올린다.
⑵ 계정 표시를 같이 옮긴다 — 안 옮기면 상태줄 래칫이 두 계정 수치를 합친다.
⑶ 그 홈에 수치가 없으면 live 수치 칸을 비운다(남의 수치를 보여 주는 것보다 낫다).
⑷ --from 이 떠나는 계정의 지금 수치를 그 홈에 보관한다.
⑸ live 의 다른 칸은 손대지 않는다.
⑹ 전환 명령이 이름을 알 때 이 경로를 쓰고 모를 때만 비우는 쪽으로 간다.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADOPT = REPO / "bin" / "claude-quota-cache-adopt"
SWITCH = REPO / "bin" / "claude-accounts"

fails = []


def check(name, cond, detail=""):
    print(("PASS  " + name) if cond
          else ("FAIL  " + name + (f"  — {str(detail)[:300]}" if detail else "")))
    if not cond:
        fails.append(name)


def run(home, *args):
    p = subprocess.run([sys.executable, str(ADOPT), *args],
                       capture_output=True, text=True,
                       env=dict(os.environ, HOME=str(home),
                                CLAUDE_ACCOUNTS_ROOT=str(home / "claude-accounts")),
                       timeout=60)
    return p


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def make(base, live=None, homes=None):
    """가짜 HOME. live 캐시와 계정 홈 캐시를 미리 둔다."""
    home = base / "home"
    if live is not None:
        write(home / ".claude" / "statusline_cache.json", live)
    for name, data in (homes or {}).items():
        if data is not None:
            write(home / "claude-accounts" / name / ".claude"
                  / "statusline_cache.json", data)
        else:
            (home / "claude-accounts" / name / ".claude").mkdir(parents=True)
    return home


def live_of(home):
    return json.loads((home / ".claude" / "statusline_cache.json")
                      .read_text(encoding="utf-8"))


RL_A = {"five_hour": {"used_percentage": 90, "resets_at": 1790000000},
        "seven_day": {"used_percentage": 99, "resets_at": 1791000000}}
RL_B = {"five_hour": {"used_percentage": 3, "resets_at": 1790500000},
        "seven_day": {"used_percentage": 8, "resets_at": 1791500000}}

check("명령이 있다", ADOPT.exists(), str(ADOPT))
if not ADOPT.exists():
    print("\n실패: 명령 없음")
    sys.exit(1)

# --- 1. 그 홈 수치를 올린다 --------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    home = make(Path(td),
                live={"_account": "old-id", "rate_limits": RL_A, "model": "x"},
                homes={"acct-a": {"_account": "new-id", "rate_limits": RL_B}})
    p = run(home, "--to", "acct-a")
    got = live_of(home)
    check("홈 수치를 live 에 올린다", got.get("rate_limits") == RL_B,
          f"{got.get('rate_limits')} rc={p.returncode} {p.stderr[:200]}")
    check("계정 표시를 같이 옮긴다", got.get("_account") == "new-id", got.get("_account"))
    check("다른 칸은 손대지 않는다", got.get("model") == "x", got)
    check("종료코드 0", p.returncode == 0, str(p.returncode))

# --- 2. 홈에 수치가 없으면 비운다 --------------------------------------------
for label, homes in (("홈 캐시가 없으면 비운다", {"acct-a": None}),
                     ("홈에 한도 칸이 없으면 비운다",
                      {"acct-a": {"_account": "new-id", "rate_limits": None}})):
    with tempfile.TemporaryDirectory() as td:
        home = make(Path(td),
                    live={"_account": "old-id", "rate_limits": RL_A, "model": "x"},
                    homes=homes)
        p = run(home, "--to", "acct-a")
        got = live_of(home)
        check(label, "rate_limits" not in got, f"{got} {p.stdout[:200]}")

# --- 3. 떠나는 계정 수치를 그 홈에 보관한다 -----------------------------------
with tempfile.TemporaryDirectory() as td:
    home = make(Path(td),
                live={"_account": "old-id", "rate_limits": RL_A},
                homes={"acct-b": {"_account": "old-id", "rate_limits": {"five_hour":
                       {"used_percentage": 1, "resets_at": 1}}},
                       "acct-a": {"_account": "new-id", "rate_limits": RL_B}})
    p = run(home, "--from", "acct-b", "--to", "acct-a")
    stashed = json.loads((home / "claude-accounts" / "acct-b" / ".claude"
                          / "statusline_cache.json").read_text(encoding="utf-8"))
    check("떠나는 계정 수치를 그 홈에 보관한다", stashed.get("rate_limits") == RL_A,
          f"{stashed.get('rate_limits')} {p.stdout[:200]}")
    check("보관 뒤에도 새 수치를 올린다", live_of(home).get("rate_limits") == RL_B,
          live_of(home).get("rate_limits"))

with tempfile.TemporaryDirectory() as td:
    # 떠나는 홈에 캐시 파일 자체가 없어도 보관은 된다 — 수치만 담은 새 파일을 만든다.
    home = make(Path(td), live={"_account": "old-id", "rate_limits": RL_A},
                homes={"acct-b": None, "acct-a": {"rate_limits": RL_B}})
    run(home, "--from", "acct-b", "--to", "acct-a")
    f = home / "claude-accounts" / "acct-b" / ".claude" / "statusline_cache.json"
    check("홈 캐시가 없어도 보관한다",
          f.exists() and json.loads(f.read_text(encoding="utf-8"))
          .get("rate_limits") == RL_A, str(f))

# --- 4. 이름 검증 ------------------------------------------------------------
for bad in ("../etc", "a/b", ".hidden"):
    with tempfile.TemporaryDirectory() as td:
        home = make(Path(td), live={"rate_limits": RL_A})
        p = run(home, "--to", bad)
        check(f"이름을 거절한다: {bad}", p.returncode == 2 and
              live_of(home).get("rate_limits") == RL_A,
              f"rc={p.returncode} {p.stdout[:150]}")

# --- 5. 같은 이름이면 보관을 건너뛴다 ----------------------------------------
with tempfile.TemporaryDirectory() as td:
    # --from 과 --to 가 같으면 자기 수치를 자기에게 보관했다가 다시 읽는 꼴이라
    # 아무 일도 안 하는 것이 맞다. 특히 live 가 빈 상태에서 홈 값을 지우면 안 된다.
    home = make(Path(td), live={"_account": "a"},
                homes={"acct-a": {"_account": "a", "rate_limits": RL_B}})
    run(home, "--from", "acct-a", "--to", "acct-a")
    kept = json.loads((home / "claude-accounts" / "acct-a" / ".claude"
                       / "statusline_cache.json").read_text(encoding="utf-8"))
    check("같은 이름이면 그 홈 수치를 안 지운다", kept.get("rate_limits") == RL_B,
          kept.get("rate_limits"))

# --- 6. 깨진 live 는 안 덮는다 ------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    home = make(Path(td), homes={"acct-a": {"rate_limits": RL_B}})
    broken = home / ".claude" / "statusline_cache.json"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("{not json", encoding="utf-8")
    p = run(home, "--to", "acct-a")
    check("깨진 live 캐시는 손대지 않는다",
          p.returncode != 0 and broken.read_text(encoding="utf-8") == "{not json",
          f"rc={p.returncode} {broken.read_text(encoding='utf-8')[:80]}")

# --- 7. 전환 명령이 이 경로를 쓴다 --------------------------------------------
src = SWITCH.read_text(encoding="utf-8")
# 전환 도구는 저장소 없이 파일 하나로 서야 해서(운영자 지시) 이양을 자기 안에서
# 끝낸다. 형제 명령을 부르면 파일만 받아 간 쪽에서 조용히 안 돈다.
check("전환이 이름을 알면 수치를 바꿔 단다", "def adopt_quota" in src, str(SWITCH))
check("이름을 모르면 비우는 쪽으로 간다", "def clear_quota" in src, str(SWITCH))
check("이양에 형제 명령을 부르지 않는다",
      "claude-quota-cache-adopt" not in src.replace(
          "형제 명령(claude-quota-cache-adopt)", ""), str(SWITCH))
check("갈아 끼우는 길 둘이 각각 그 자리를 부른다",
      "adopt_quota(name, prev, gen)" in src and "clear_quota()" in src, str(SWITCH))
# 자격이 바뀌는 길 둘 다 교체 세대를 올려야 한다. 안 올리면 교체 전 응답을 든 세션이
# 자기가 이미 그린 세대 그대로라 "확인됨" 으로 통과해 옛 수치를 공유 칸에 올린다.
check("이름을 아는 전환이 세대를 올린다",
      "gen = bump_generation()" in src, str(SWITCH))
_clear_at = src.find("\n        clear_quota()")
check("이름을 모르는 전환도 세대를 올린다",
      _clear_at > 0 and "bump_generation()" in src[max(0, _clear_at - 300):_clear_at],
      str(SWITCH))

print(f"\n{'실패: ' + ', '.join(fails) if fails else '모두 통과'}")
sys.exit(1 if fails else 0)
