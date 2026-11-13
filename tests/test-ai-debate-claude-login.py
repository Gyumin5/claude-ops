#!/usr/bin/env python3
"""토론이 부르는 자식 claude 의 로그인 자리를 고정한다.

지키는 것 둘이다. ⑴ 자식은 이 기계의 로그인 파일 하나를 그대로 쓴다 — 격리 홈에 사본을
두면 그 사본이 따로 갱신을 돌려 다른 세션들의 토큰을 통째로 무효화한다(2026-06-10·06-12
실사고). ⑵ 만료 구간을 피하는 자격 게이트를 다시 두지 않는다 — 사본이 없으면 누가
갱신하든 모두가 같은 새 토큰을 읽으므로 경쟁할 자리가 없고, 게이트는 멀쩡한 회차를
최대 30분 세우는 일만 했다(2026-11-06 운영자 판정).

부모 세션 설정·훅·MCP 를 끄는 플래그는 남는다. 그건 로그인이 아니라 작업 환경을
가르는 자리다.
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = (REPO / "bin" / "ai-debate").read_text(encoding="utf-8")

fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  — {detail}" if detail else ""))
    if not cond:
        fails.append(name)


# ⑴ 자격 사본·격리 홈이 없다.
_iso = [l for l in SRC.splitlines() if ".claude-iso" in l and "SNAP_DENY" not in l
        and not l.lstrip().startswith("#")
        and '".claude", ".claude-iso"' not in l]
check("격리 홈을 안 쓴다", not _iso,
      "격리 홈에 자격 사본을 두면 그 사본의 갱신이 다른 세션 토큰을 무효화한다: "
      + str(_iso[:2]))
check("자격 파일을 직접 읽지 않는다",
      ".credentials.json" not in SRC and "claudeAiOauth" not in SRC)
check("자식은 이 기계의 홈을 그대로 쓴다", '"HOME": HOME_DIR' in SRC)

# ⑵ 자격 게이트가 없다.
for gone in ("check_creds_gate", "await_creds_gate", "CREDS_SAFETY_MARGIN_S",
             "CREDS_WAIT_MAX_S", "CLI_PREREFRESH_S", "MAIN_CREDS",
             "allow_partial_pool", "allow-partial-pool"):
    check(f"자격 게이트 자취가 없다: {gone}", gone not in SRC)

# 남아야 하는 것 — 작업 환경을 가르는 플래그.
check("부모 설정·훅을 끈다", '"--safe-mode"' in SRC)
check("부모 MCP 를 안 물려받는다", '"--strict-mcp-config"' in SRC)
check("브리프가 가리킨 폴더만 연다", '"--add-dir"' in SRC)

# 문법과 이름. 모듈을 읽어 컴파일까지 해 본다 — 지운 이름을 어딘가 남겨 두면
# 그 갈래가 실제로 돌 때만 NameError 로 터져서 토론 한 회차가 통째로 사라진다.
try:
    compile(SRC, "ai-debate", "exec")
    check("파일이 컴파일된다", True)
except SyntaxError as e:
    check("파일이 컴파일된다", False, str(e))

# 지운 이름을 코드가 아직 부르는지 — 주석·문자열 밖에서만 본다.
bare = re.sub(r"#.*", "", SRC)
for gone in ("math.", "_read_creds(", "_usable_token(", "_wait_deadline("):
    check(f"지운 이름을 안 부른다: {gone}", gone not in bare)

print()
print("실패 {}건".format(len(fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
