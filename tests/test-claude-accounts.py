#!/usr/bin/env python3
"""남에게 주는 계정 묶음 — 파일 하나를 그냥 실행하면 메뉴로 끝까지 도는가.

운영자 지시 "다른 사람은 컨트롤봇을 안쓰고 파일 하나로 … 계정 등록이나 계정 전환을
시도할꺼야", 8573 "아규먼트로 하지 말고 대화형으로 만들어줘". 받는 쪽에는 이 저장소도,
형제 명령도, 상태줄 파일도 없고, 외울 옵션도 없다. 그래서 검사는 저장소 밖 빈 디렉토리에
그 파일 하나만 복사해 두고, 사람이 치듯 번호만 먹여 돌린다 — 어딘가를 조용히 참조하거나
메뉴가 막히면 그 자리에서 깨진다.

고정하는 것.
⑴ 저장소 밖에 파일 하나만 두고도 메뉴가 뜨고 0 으로 나간다.
⑵ 번호로 계정을 고른다 — 이름을 외워 치지 않는다. 지금 쓰는 계정은 고를 목록에 없다.
⑶ 로그인은 떼어 놓은 쪽에서 돌고, 주소가 화면에 나오고, 코드를 넣으면 끝난다.
⑷ 코드를 보냈다는 것만으로 성공이라고 하지 않는다 — 자격 파일이 생겨야 성공이다.
⑸ 전환은 떠나는 계정의 지금 자격을 그 홈에 되돌리고, 백업을 먼저 만들고 바꾼다.
⑹ 새 이름이 경로 조각이거나 이미 있으면 되묻고, 메뉴를 안 빠져나간다.
⑺ 전환이 한도 수치를 새 계정 것으로 바꿔 단다(계정 표시까지 같이).
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "bin" / "claude-accounts"

fails = []


def check(name, cond, detail=""):
    print(("PASS  " + name) if cond
          else ("FAIL  " + name + (f"  — {str(detail)[:400]}" if detail else "")))
    if not cond:
        fails.append(name)


def cred(token):
    return json.dumps({"claudeAiOauth": {"accessToken": token,
                                         "refreshToken": "r-" + token}})


class Box:
    """저장소 밖 빈 자리. 도구 파일 하나만 들어간다.

    PATH 를 가짜 자리 하나로 좁힌다 — 그래야 로그인 떼어 놓기가 systemd 로 안 가고,
    검사가 이 기계에 유닛을 남기지 않는다.
    """

    def __init__(self, td):
        self.base = Path(td)
        self.home = self.base / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.tool = self.base / "claude-accounts"
        shutil.copy2(TOOL, self.tool)
        os.chmod(self.tool, 0o755)
        self.bin = self.base / "fakebin"
        self.bin.mkdir()

    def run(self, keys, timeout=120):
        """메뉴에 줄 단위로 먹인다. 사람이 번호를 치는 것과 같다."""
        return subprocess.run(
            [sys.executable, str(self.tool)],
            input="".join(k + "\n" for k in keys),
            capture_output=True, text=True, timeout=timeout,
            env=dict(os.environ, HOME=str(self.home), PATH=str(self.bin)),
            cwd=str(self.base))

    def reap(self):
        """떼어 놓은 로그인이 남아 있으면 거둔다. 검사가 뒤에 뭘 남기지 않는다."""
        root = self.home / "claude-accounts"
        if not root.is_dir():
            return
        for d in root.iterdir():
            pid_file = d / "login" / "pid"
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                continue
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    # --- 자리 만들기 도우미 (로그인 없이 그 상태를 만든다) ---
    def seed(self, name, token):
        d = self.home / "claude-accounts" / name / ".claude"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".credentials.json").write_text(cred(token), encoding="utf-8")

    def live(self):
        p = self.home / ".claude" / ".credentials.json"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def set_live(self, token):
        (self.home / ".claude" / ".credentials.json").write_text(
            cred(token), encoding="utf-8")

    def marker(self):
        p = self.home / ".claude" / "state" / "account-current"
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""

    def home_cred(self, name):
        p = (self.home / "claude-accounts" / name / ".claude" / ".credentials.json")
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def fake_claude(self, body):
        p = self.bin / "claude"
        p.write_text(body, encoding="utf-8")
        os.chmod(p, 0o755)

    def run_args(self, args, timeout=120):
        """메뉴를 거치지 않는 길. 기계가 부르는 자리를 그대로 흉내낸다."""
        return subprocess.run(
            [sys.executable, str(self.tool)] + args,
            input="", capture_output=True, text=True, timeout=timeout,
            env=dict(os.environ, HOME=str(self.home), PATH=str(self.bin)),
            cwd=str(self.base))

    def set_cache(self, name, five, week):
        """그 계정 자리(이름이 없으면 지금 쓰는 홈)의 한도 칸을 만들어 둔다."""
        base = self.home if name is None else (self.home / "claude-accounts" / name)
        p = base / ".claude" / "statusline_cache.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"rate_limits": {
            "five_hour": {"used_percentage": five, "resets_at": 1800000000},
            "seven_day": {"used_percentage": week, "resets_at": 1800600000}}}),
            encoding="utf-8")


FAKE_OK = r"""#!/bin/bash
# 진짜 로그인 흉내: 주소를 찍고 코드를 한 줄 기다렸다가 자격 파일을 쓴다.
echo "Browser didn't open? Use the url below:"
echo "https://claude.ai/oauth/authorize?code=true&client_id=deadbeef&state=xyz"
read -r code
if [ -n "$code" ]; then
  mkdir -p "$HOME/.claude"
  printf '{"claudeAiOauth":{"accessToken":"T-%s","refreshToken":"r"}}' "$code" \
    > "$HOME/.claude/.credentials.json"
  echo "Login successful"
fi
"""

FAKE_BAD = r"""#!/bin/bash
# 코드를 받고도 실패하는 경우 — 자격 파일을 안 쓴다.
echo "open https://claude.ai/oauth/authorize?code=true&client_id=dead"
read -r code
echo "Invalid code"
"""


check("도구 파일이 있다", TOOL.exists(), str(TOOL))
if not TOOL.exists():
    sys.exit(1)

# --- 1. 저장소 밖에서 메뉴가 혼자 선다 ---------------------------------------
with tempfile.TemporaryDirectory() as td:
    b = Box(td)
    p = b.run(["0"])
    out = p.stdout
    check("저장소 밖에서 메뉴가 뜬다",
          p.returncode == 0 and "계정 갈아 끼우기" in out and "새 계정 등록" in out,
          f"rc={p.returncode} {out[:200]}{p.stderr[:200]}")
    check("지금 계정을 머리에 보여 준다", "지금 계정:" in out, out[:200])
    check("0 으로 나간다", p.returncode == 0, f"rc={p.returncode}")

    p = b.run(["9", "0"])
    check("모르는 번호는 되묻고 메뉴를 안 빠져나간다",
          p.returncode == 0 and "0 에서 5" in p.stdout, p.stdout[-300:])

    p = b.run([])          # 입력이 끊기면 나간다
    check("입력이 끊기면 조용히 나간다", p.returncode == 0,
          f"rc={p.returncode} {p.stderr[:200]}")

# --- 2. 빈 상태의 안내 --------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    b = Box(td)
    p = b.run(["1", "3", "5", "0"])
    check("갈아 끼울 것이 없으면 그렇게 말한다", "갈아 끼울 계정이 없다" in p.stdout,
          p.stdout[-400:])
    check("자리가 없으면 목록이 그렇게 말한다", "만들어 둔 계정 자리가 없다" in p.stdout,
          p.stdout[-400:])
    check("기다리는 로그인이 없으면 그렇게 말한다",
          "기다리는 로그인이 없다" in p.stdout, p.stdout[-400:])

# --- 3. 번호로 골라 갈아 끼운다 ----------------------------------------------
with tempfile.TemporaryDirectory() as td:
    b = Box(td)
    b.seed("acct-a", "T-acct-a")
    b.seed("acct-b", "T-acct-b")
    b.set_live("T-first")

    p = b.run(["1", "1", "0"])        # 갈아 끼우기 → 첫 번째(acct-a) → 나가기
    check("번호로 골라 갈아 끼운다", "acct-a 로 갈아 끼웠다" in p.stdout,
          p.stdout[-400:] + p.stderr[-200:])
    check("live 자격이 그 계정 것으로 바뀐다", "T-acct-a" in b.live(), b.live()[:120])
    check("이름표를 적는다", b.marker() == "acct-a", b.marker())

    bak = b.home / ".claude" / "state" / "accounts" / "last-live.json"
    check("바꾸기 전 자격을 백업한다",
          bak.exists() and "T-first" in bak.read_text(encoding="utf-8"), str(bak))
    first = b.home / ".claude" / "state" / "accounts" / "first-unmanaged.json"
    check("이 도구 밖에서 쓰던 첫 자격은 따로 한 벌 남긴다",
          first.exists() and "T-first" in first.read_text(encoding="utf-8"), str(first))

    p = b.run(["1", "0", "0"])
    # 고르는 목록 블록만 본다 — 그 뒤에 메뉴가 다시 찍히고 거기엔 "지금 계정: acct-a" 이 있다.
    block = p.stdout.split("어느 계정으로")[-1].split("0) 돌아가기")[0]
    check("지금 쓰는 계정은 고를 목록에 없다",
          "acct-b" in block and "acct-a" not in block, block[:300])

    p = b.run(["3", "0"])
    check("목록이 지금 쓰는 자리를 별표로 가리킨다", "* acct-a" in p.stdout,
          p.stdout[-400:])

    # 쓰는 동안 토큰이 저절로 갱신된 상황
    b.set_live("T-acct-a-renewed")
    p = b.run(["1", "1", "0"])        # 남은 것은 acct-b 하나
    check("두 번째 전환도 돈다", "T-acct-b" in b.live(), b.live()[:120])
    check("떠나는 계정의 갱신분을 그 홈에 되돌린다",
          "T-acct-a-renewed" in b.home_cred("acct-a"), b.home_cred("acct-a")[:120])
    check("첫 자격 보관본은 덮이지 않는다",
          "T-first" in first.read_text(encoding="utf-8"),
          first.read_text(encoding="utf-8")[:120])

# --- 4. 한도 수치 인계 --------------------------------------------------------
RL = {"five_hour": {"used_percentage": 3, "resets_at": 1790500000}}
with tempfile.TemporaryDirectory() as td:
    b = Box(td)
    b.seed("acct-a", "T-acct-a")
    b.set_live("T-x")
    (b.home / ".claude" / "statusline_cache.json").write_text(
        json.dumps({"_account": "old", "model": "m",
                    "rate_limits": {"five_hour": {"used_percentage": 99}}}),
        encoding="utf-8")
    (b.home / "claude-accounts" / "acct-a" / ".claude"
     / "statusline_cache.json").write_text(
        json.dumps({"_account": "new", "rate_limits": RL}), encoding="utf-8")

    b.run(["1", "1", "0"])
    got = json.loads((b.home / ".claude" / "statusline_cache.json")
                     .read_text(encoding="utf-8"))
    check("전환이 한도 수치를 새 계정 것으로 바꿔 단다", got.get("rate_limits") == RL,
          str(got.get("rate_limits")))
    check("계정 표시를 같이 옮긴다", got.get("_account") == "new", str(got.get("_account")))
    check("다른 칸은 손대지 않는다", got.get("model") == "m", str(got))

with tempfile.TemporaryDirectory() as td:
    # 그 홈에 수치가 없으면 비운다 — 남의 수치를 새 계정 여유처럼 보여 주지 않는다.
    b = Box(td)
    b.seed("acct-a", "T-acct-a")
    b.set_live("T-x")
    (b.home / ".claude" / "statusline_cache.json").write_text(
        json.dumps({"_account": "old", "rate_limits": {"five_hour": {}}}),
        encoding="utf-8")
    b.run(["1", "1", "0"])
    got = json.loads((b.home / ".claude" / "statusline_cache.json")
                     .read_text(encoding="utf-8"))
    check("그 홈에 수치가 없으면 비운다", "rate_limits" not in got, str(got))

# --- 5. 새 이름 묻기 ----------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    b = Box(td)
    b.seed("acct-a", "T-acct-a")
    p = b.run(["2", "../etc", "a/b", ".hidden", "acct-a", "", "0"])
    check("경로 조각 이름을 거절한다",
          p.stdout.count("경로나 점으로 시작하는 이름은 안 된다") == 3, p.stdout[-500:])
    check("이미 있는 이름을 거절한다", "acct-a 은 이미 있다" in p.stdout, p.stdout[-400:])
    check("빈 줄이면 메뉴로 돌아간다 — 거절이 메뉴를 안 깨뜨린다",
          p.returncode == 0 and p.stdout.count("클로드 계정 바꾸기") >= 2,
          f"rc={p.returncode}")

# --- 6. 로그인 ----------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    b = Box(td)
    b.fake_claude(FAKE_OK)
    p = b.run(["2", "sw9", "ABC123", "0"], timeout=180)
    b.reap()
    check("로그인 주소를 화면에 보여 준다", "claude.ai/oauth" in p.stdout,
          p.stdout[-500:] + p.stderr[-200:])
    check("코드를 넣으면 로그인이 끝난다", "sw9 로그인 완료" in p.stdout,
          p.stdout[-500:] + p.stderr[-300:])
    check("그 계정 홈에 자격이 생긴다", "T-ABC123" in b.home_cred("sw9"),
          b.home_cred("sw9")[:120])

with tempfile.TemporaryDirectory() as td:
    b = Box(td)
    b.fake_claude(FAKE_BAD)
    b.set_live("T-live")
    p = b.run(["2", "sw8", "WRONG", "0"], timeout=180)
    b.reap()
    check("코드를 보냈다는 것만으로 성공이라고 하지 않는다",
          "로그인 완료" not in p.stdout and "아직 로그인이 안 끝났다" in p.stderr,
          p.stdout[-300:] + p.stderr[-300:])
    check("실패하면 그 홈에 자격이 안 생긴다", b.home_cred("sw8") == "")
    check("로그인이 지금 쓰는 자격을 안 건드린다", "T-live" in b.live(), b.live()[:120])

# --- 7. 첫 실행 물음을 미리 지나간 것으로 적는다 -------------------------------
with tempfile.TemporaryDirectory() as td:
    b = Box(td)
    b.fake_claude(FAKE_OK)
    b.run(["2", "sw6", "ZZZ", "0"], timeout=180)
    b.reap()
    jf = b.home / "claude-accounts" / "sw6" / ".claude.json"
    cfg = json.loads(jf.read_text(encoding="utf-8")) if jf.exists() else {}
    acc_home = str(b.home / "claude-accounts" / "sw6")
    check("온보딩을 끝난 것으로 적는다", cfg.get("hasCompletedOnboarding") is True, str(cfg))
    check("그 홈을 믿는 것으로 적는다",
          cfg.get("projects", {}).get(acc_home, {}).get("hasTrustDialogAccepted") is True,
          str(cfg.get("projects")))
    check("홈 권한은 주인만", (Path(acc_home).stat().st_mode & 0o077) == 0,
          oct(Path(acc_home).stat().st_mode))
    check("설정 파일 권한은 주인만", (jf.stat().st_mode & 0o077) == 0,
          oct(jf.stat().st_mode))
    check("로그인 기록 자리도 주인만",
          ((Path(acc_home) / "login").stat().st_mode & 0o077) == 0,
          oct((Path(acc_home) / "login").stat().st_mode))

# --- 8. 끊긴 로그인에 코드만 다시 넣는다 (메뉴 5번) ---------------------------------------
with tempfile.TemporaryDirectory() as td:
    b = Box(td)
    b.fake_claude(FAKE_OK)
    # 코드를 안 넣고 빠져나온다 — 기다리는 로그인이 남는다.
    b.run(["2", "sw5", "", "0"], timeout=180)
    p = b.run(["5", "1", "LATER", "0"], timeout=180)
    b.reap()
    check("기다리던 로그인을 번호로 골라 코드를 넣는다",
          "sw5 로그인 완료" in p.stdout, p.stdout[-500:] + p.stderr[-300:])
    check("늦게 넣은 코드로도 자격이 생긴다", "T-LATER" in b.home_cred("sw5"),
          b.home_cred("sw5")[:120])

# --- 9. 끝난 로그인은 "기다리는 중" 으로 안 센다 ------------------------------
# 판정이 파일 존재였을 때는 한 번이라도 띄웠던 자리가 전부 올라왔다 — 끝난 것까지.
# 받는 쪽은 그 목록을 보고 "여기에 코드를 넣으면 되는구나" 로 읽는데, 넣을 데가 없다.
with tempfile.TemporaryDirectory() as td:
    b = Box(td)
    b.fake_claude(FAKE_OK)
    b.run(["2", "sw4", "DONE1", "0"], timeout=180)     # 끝까지 마친 로그인
    b.reap()
    p = b.run(["5", "0"])
    check("끝난 로그인은 코드 넣기 목록에 안 올라온다",
          "기다리는 로그인이 없다" in p.stdout, p.stdout[-400:])
    check("끝난 로그인은 메뉴 머리에도 안 뜬다",
          "코드를 기다리는 로그인" not in p.stdout, p.stdout[-400:])

with tempfile.TemporaryDirectory() as td:
    b = Box(td)
    b.fake_claude(FAKE_OK)
    b.run(["2", "acct-d", "", "0"], timeout=180)          # 코드를 안 넣고 빠져나온다
    p = b.run(["0"])
    b.reap()
    check("기다리는 로그인은 메뉴 머리에 이름으로 뜬다",
          "코드를 기다리는 로그인: acct-d" in p.stdout, p.stdout[-400:])

# --- 10. 사용량 재기 ----------------------------------------------------------
# 셸 내장만 쓴다 — 찾는 경로를 가짜 자리 하나로 좁혀 놨으므로 sleep·mkdir·cat 이 없다.
FAKE_USAGE = r"""#!/bin/bash
# 세션 흉내: 한도 칸을 수치 파일에 적고 가만히 있는다. 도구가 그걸 보고 접는다.
read -t 1 -r _settle
printf '%s' '{"_account":"x","rate_limits":{"five_hour":{"used_percentage":42,"resets_at":1800000000},"seven_day":{"used_percentage":77,"resets_at":1800600000}}}' > "$HOME/.claude/statusline_cache.json"
read -r _hold
"""

with tempfile.TemporaryDirectory() as td:
    b = Box(td)
    p = b.run(["4", "0"])
    check("잴 계정이 없으면 그렇게 말한다", "잴 계정이 없다" in p.stdout, p.stdout[-300:])

with tempfile.TemporaryDirectory() as td:
    b = Box(td)
    b.fake_claude(FAKE_USAGE)
    b.seed("acct-a", "T-acct-a")
    b.seed("acct-b", "T-acct-b")
    b.set_live("T-acct-a")
    (b.home / ".claude" / "state").mkdir(parents=True, exist_ok=True)
    (b.home / ".claude" / "state" / "account-current").write_text("acct-a",
                                                                  encoding="utf-8")
    p = b.run(["4", "0"], timeout=180)
    check("등록한 계정을 재서 수치를 보여 준다",
          "42%" in p.stdout and "77%" in p.stdout, p.stdout[-600:] + p.stderr[-200:])
    check("두 창을 각각 막대로 보여 준다",
          "5시간 [" in p.stdout and "7일 [" in p.stdout, p.stdout[-600:])
    check("지금 쓰는 계정은 안 띄우고 돌고 있는 값을 읽는다",
          "지금 쓰는 중 — 수치가 아직 없다" in p.stdout, p.stdout[-600:])
    check("재고 나서도 메뉴로 돌아온다",
          p.returncode == 0 and p.stdout.count("클로드 계정 바꾸기") >= 2,
          f"rc={p.returncode}")

# --- 기계가 읽는 측정(--usage-json) ------------------------------------------
# 밤에 어느 계정으로 갈지는 재 봐야 안다 — 캐시 수치는 며칠 전 값일 수 있다. 재는
# 자리는 이 파일 하나이고, 고르는 규칙은 부르는 쪽에 둔다. 사람에게 보일 안내문이
# 섞이면 부르는 쪽이 통째로 못 읽으므로 출력은 JSON 한 덩어리여야 한다.
with tempfile.TemporaryDirectory() as td:
    b = Box(td)
    b.fake_claude(FAKE_USAGE)
    b.seed("acct-a", "T-acct-a")
    b.seed("acct-b", "T-acct-b")
    b.set_live("T-acct-a")
    # 순서가 뜻을 가진다. 갈아 끼운 표식보다 나중에 적힌 수치만 지금 계정 것으로 읽으므로
    # (read_limits 의 since), 실제 순서대로 표식을 먼저 두고 수치를 그 뒤에 적는다.
    # 거꾸로 두면 기계마다 다르게 돈다 — 홈 /tmp 는 두 파일의 시각이 같게 찍혀 우연히
    # 통과하고, 둘째 기계 /tmp 는 나노초까지 찍혀 매번 실패한다(2026-11-09 실측).
    (b.home / ".claude" / "state").mkdir(parents=True, exist_ok=True)
    (b.home / ".claude" / "state" / "account-current").write_text("acct-a",
                                                                  encoding="utf-8")
    b.set_cache(None, 11, 22)          # 지금 쓰는 계정 값은 돌고 있는 홈에서 읽는다
    p = b.run_args(["--usage-json"], timeout=180)
    doc = None
    try:
        doc = json.loads(p.stdout)
    except ValueError:
        pass
    check("출력이 JSON 한 덩어리다 — 안내문이 안 섞인다",
          doc is not None and p.returncode == 0,
          f"rc={p.returncode} out={p.stdout[-400:]} err={p.stderr[-200:]}")
    if doc:
        rows = {r["name"]: r for r in doc["accounts"]}
        check("등록한 계정이 다 들어간다", set(rows) == {"acct-a", "acct-b"}, list(rows))
        check("띄워서 잰 계정의 두 창이 다 들어간다",
              rows.get("acct-b", {}).get("five_hour") == 42
              and rows.get("acct-b", {}).get("seven_day") == 77, rows.get("acct-b"))
        check("풀리는 시각도 같이 낸다",
              rows.get("acct-b", {}).get("seven_day_reset") == 1800600000,
              rows.get("acct-b"))
        check("지금 쓰는 계정은 안 띄우고 돌고 있는 값을 읽는다",
              rows.get("acct-a", {}).get("current") is True
              and rows.get("acct-a", {}).get("seven_day") == 22, rows.get("acct-a"))
        check("언제 쟀는지를 같이 낸다", isinstance(doc.get("measured_at"), int)
              and doc.get("current") == "acct-a", doc.get("measured_at"))

# --- 새 계정 자리는 상태줄까지 걸어 둔다 -------------------------------------
# 안 걸면 로그인은 되는데 사용량 칸이 영원히 "수치 없음" 으로 남는다. 둘째 기계 여섯 홈이
# 그 상태였고, 컨트롤봇의 계정 사용량이 그래서 비어 보였다(11-07).
import importlib.machinery          # noqa: E402
import importlib.util               # noqa: E402

_loader = importlib.machinery.SourceFileLoader("claude_accounts_tool", str(TOOL))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
mod = importlib.util.module_from_spec(_spec)
_loader.exec_module(mod)

with tempfile.TemporaryDirectory() as td:
    mod.ROOT = Path(td) / "homes"
    home = mod.init_home("sw9")
    link = home / ".claude" / "statusline.py"
    check("새 자리에 상태줄을 걸어 둔다", link.is_symlink() and link.resolve().is_file(),
          str(link))
    try:
        cfg = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        cfg = {"err": str(exc)}
    check("상태줄 설정도 같이 적는다",
          cfg.get("statusLine", {}).get("command", "").endswith("statusline.py"), cfg)

    # 두 번 돌려도 같은 자리를 다시 쓰지 않는다 — 이미 걸린 것은 그대로 둔다.
    before = link.resolve()
    mod.init_home("sw9")
    check("두 번째에도 같은 곳을 가리킨다", link.resolve() == before, str(link.resolve()))

    # 묶음만 받은 기계에는 상태줄 본체가 없다. 그때는 조용히 건너뛰고 홈은 그대로 선다.
    src_backup = mod.statusline_src
    mod.statusline_src = lambda: None
    try:
        home2 = mod.init_home("sw8")
    finally:
        mod.statusline_src = src_backup
    check("상태줄 본체가 없으면 건너뛰고도 홈은 선다",
          (home2 / ".claude").is_dir()
          and not (home2 / ".claude" / "statusline.py").exists(), str(home2))

# --- 표식이 없는 기계에서도 지금 계정을 알아본다 -----------------------------
# 표식은 갈아 끼운 쪽이 적는다. 손으로 로그인만 한 기계에는 아예 없어서 화면이 "모름"
# 만 띄웠다(11-07 둘째 기계). 계정 번호는 설정에 있고 토큰 갱신을 안 타므로 그 자리를 메운다.
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    mod.HOME = base / "live"
    mod.ROOT = base / "homes"
    mod.MARKER = mod.HOME / ".claude" / "state" / "account-current"

    def put_uuid(d, uid):
        d.mkdir(parents=True, exist_ok=True)
        (d / ".claude.json").write_text(
            json.dumps({"oauthAccount": {"accountUuid": uid}}), encoding="utf-8")

    put_uuid(mod.HOME, "uuid-acct-c")
    put_uuid(mod.ROOT / "acct-b", "uuid-acct-b")
    put_uuid(mod.ROOT / "acct-c", "uuid-acct-c")

    check("표식이 없어도 계정 번호로 이름을 찾는다",
          mod.current_name() == "acct-c", mod.current_name())

    # 표식이 있으면 그쪽이 먼저다 — 갈아 끼운 직후에는 설정이 아직 옛 계정을 들고 있다.
    mod.MARKER.parent.mkdir(parents=True, exist_ok=True)
    mod.MARKER.write_text("acct-b", encoding="utf-8")
    check("표식이 있으면 표식을 따른다", mod.current_name() == "acct-b", mod.current_name())

    # 맞는 자리가 없으면 지어내지 않는다.
    mod.MARKER.unlink()
    put_uuid(mod.HOME, "uuid-없는계정")
    check("맞는 자리가 없으면 빈 값이다", mod.current_name() == "", mod.current_name())

print(f"\n{'실패: ' + ', '.join(fails) if fails else '모두 통과'}")
sys.exit(1 if fails else 0)
