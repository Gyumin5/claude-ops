#!/usr/bin/env python3
"""ai-debate 의 사본·열람 허용 경계가 이 계정의 실제 홈으로 판정되는가.

이 경계는 토론 참가 모델에게 어느 디렉토리를 복사해 열어줄지를 정한다. 느슨해지면
브리프에 적힌 경로 하나로 허용 밖 트리가 통째로 사본에 들어간다.

보는 것 셋.
1. 기준이 환경변수가 아니라 이 계정의 실제 홈인가. HOME 은 부르는 쪽이 바꿀 수 있고,
   그 값으로 판정하면 HOME 을 바꿔 호출하는 것만으로 경계가 옮겨간다.
2. 비교가 문자열 앞가지가 아니라 경로 구성요소인가. 앞가지 비교는 같은 글자로 시작하는
   옆 계정 홈(/home/<이름>x)을 통과시킨다.
3. 홈 자신과 /home 은 여전히 거부되는가. 홈 전체를 사본으로 뜨는 브리프는 비밀 노출과
   상한 둘 다에 걸린다.
"""
import importlib.machinery
import importlib.util
import os
import pwd
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
results = []


def check(name, cond, detail: object = ""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name +
          (("\n      " + str(detail)) if detail and not cond else ""))


loader = importlib.machinery.SourceFileLoader("ai_debate_home", str(REPO / "bin" / "ai-debate"))
spec = importlib.util.spec_from_loader("ai_debate_home", loader)
assert spec is not None
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)

real_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()

check("경계 기준이 이 계정의 실제 홈이다",
      getattr(mod, "ACCOUNT_HOME", None) == real_home,
      getattr(mod, "ACCOUNT_HOME", "(없음)"))

# 1. 환경변수로 옮겨지지 않는다.
saved = os.environ.get("HOME")
try:
    os.environ["HOME"] = "/tmp/gaja-home"
    check("HOME 을 바꿔도 경계가 안 옮겨간다",
          mod.ACCOUNT_HOME == real_home, mod.ACCOUNT_HOME)
finally:
    if saved is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = saved

# 2. 같은 글자로 시작하는 옆 계정은 통과하지 못한다.
sibling = Path(str(real_home) + "x")
check("같은 글자로 시작하는 옆 계정 홈은 거부한다",
      not mod._snap_ok(sibling), sibling)
check("그 아래 디렉토리도 거부한다",
      not mod._snap_ok(sibling / "work"), sibling / "work")

# 3. 홈 자신과 /home 은 거부, 홈 아래는 허용.
check("홈 자신은 거부한다", not mod._snap_ok(real_home))
check("/home 은 거부한다", not mod._snap_ok(Path("/home")))
check("홈 아래 디렉토리는 허용한다", mod._snap_ok(real_home / "dotfiles"))
check("홈 아래 깊은 디렉토리도 허용한다",
      mod._snap_ok(real_home / "dotfiles" / "bin"))

# 4. /tmp 갈래는 그대로다. 뿌리 자신은 홈과 같은 이유로 거부하고 그 아래만 허용한다.
check("/tmp 자신은 거부한다", not mod._snap_ok(Path("/tmp")))
check("/tmp 아래는 허용한다", mod._snap_ok(Path("/tmp/brief-1")))
check("/tmp 로 시작하는 다른 이름은 거부한다", not mod._snap_ok(Path("/tmpx")))

# 5. 아예 다른 뿌리는 거부.
check("허용 트리 밖은 거부한다", not mod._snap_ok(Path("/etc")))
check("다른 사용자의 홈은 거부한다", not mod._snap_ok(Path("/home/someone-else")))

# 7. 보고서 보관처만 좁게 연다.
#
# 2026-11-04 에 보고서를 저장소 밖으로 내리면서 reports 가 심링크가 됐고, 푼 자리가
# 홈 바로 밑 .local 아래라 부정목록에 걸렸다. 그래서 보고서를 필수 자료로 건 브리프가
# 그 자료를 통째로 잃었다. 여는 것은 그 한 디렉토리뿐이고, 그 안에서도 자격증명 이름은
# 그대로 막혀야 한다.
archive = mod.reports_archive_root()
if archive is None and not (REPO / "reports").exists():
    # 보고서 별칭이 없는 체크아웃(공개판 등)에는 잴 대상이 없다.
    print("SKIP  보고서 보관처 — 이 체크아웃에 reports 가 없다")
elif archive is None:
    check("보고서 보관처를 찾는다", False, "reports 별칭이 심링크가 아니다")
else:
    check("보고서 보관처를 별칭에서 푼다", archive.is_dir(), archive)
    check("보관처 자신은 사본 대상이다", mod._snap_ok(archive), archive)
    check("보관처 안의 보고서는 사본 대상이다",
          mod._snap_ok(archive / "2026-01-01-x.html"))
    check("보관처 안이라도 자격증명 이름은 막는다",
          not mod._snap_ok(archive / ".ssh" / "id_rsa"))
    check("보관처 안이라도 열쇠 확장자는 막는다",
          not mod._snap_ok(archive / "a.pem"))
    # 면제는 보관처로 가는 경로 하나뿐이다. 같은 .local 아래 옆자리는 그대로 막힌다.
    check("보관처 옆자리는 그대로 막는다",
          not mod._snap_ok(archive.parent / "other"))
    check("홈 바로 밑 .local 자체는 그대로 막는다",
          not mod._snap_ok(real_home / ".local"))
    check("홈 바로 밑 .config 는 그대로 막는다",
          not mod._snap_ok(real_home / ".config" / "x"))
    # brief_dirs 와 사본 판정이 같은 경계를 써야 한다 — 한쪽만 열리면 사본은 떠도
    # 참여자에게 열어주는 목록에 안 들어가거나 그 반대가 된다.
    _brief = "검토 대상: %s" % (REPO / "reports")
    check("brief_dirs 도 같은 경계를 쓴다",
          str(archive) in [str(Path(d).resolve()) for d in mod.brief_dirs(_brief)],
          mod.brief_dirs(_brief))

# 6. 사본의 사본 방지는 그대로.
check("산출물 디렉토리는 거부한다", not mod._snap_ok(Path(str(mod.RUN_BASE))))
check("산출물 디렉토리 아래도 거부한다",
      not mod._snap_ok(Path(str(mod.RUN_BASE)) / "run-1"))

ok = sum(results)
print(f"\n{ok}/{len(results)} passed")
sys.exit(0 if ok == len(results) else 1)
