#!/usr/bin/env python3
"""개인 계정 커넥터 읽기 명령의 계약.

지키는 것 넷.

  1. 개인 계정 이름을 이 코드에 안 적는다 — 밤 전환 설정의 day 줄에서 읽는다.
     적어 두면 기계마다 갈리고, 갈린 쪽을 다음 세션이 사실로 읽는다.
  2. 실행하는 홈이 그 개인 계정 자리다. 부르는 쪽 설정을 가리키는 환경값 셋은
     지운다 — 안 지우면 지정한 홈이 아니라 지금 계정으로 읽는다. 그게 바로
     남의 사서함이 열린 경로다.
  3. 도구 이름을 반드시 박는다. 비면 읽기가 조용히 빈손으로 끝나므로 거절한다.
  4. 로그인 파일이 없으면 시끄럽게 죽는다. 조용히 빈손으로 돌아오지 않는다.
"""
import importlib.machinery
import importlib.util
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / "bin" / "claude-m365-read"
results = []


def check(name, cond, detail=None):
    results.append(bool(cond))
    print("%s  %s" % ("PASS" if cond else "FAIL", name)
          + (("\n      " + str(detail)) if not cond and detail else ""))


def load():
    loader = importlib.machinery.SourceFileLoader("m365read", str(TARGET))
    spec = importlib.util.spec_from_loader("m365read", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class FakeRun:
    """claude 를 실제로 안 부르고 넘어온 인자만 받아 둔다."""

    def __init__(self, rc=0, out="오규민"):
        self.rc, self.out, self.seen = rc, out, None

    def __call__(self, cmd, **kw):
        self.seen = (cmd, kw)
        return subprocess.CompletedProcess(cmd, self.rc, self.out, "")


def run_main(mod, argv, fake):
    old_argv, old_run = sys.argv, mod.subprocess.run
    sys.argv = ["claude-m365-read"] + argv
    mod.subprocess.run = fake
    try:
        return mod.main()
    except SystemExit as e:
        return e.code
    finally:
        sys.argv, mod.subprocess.run = old_argv, old_run


def main():
    m = load()

    src = TARGET.read_text(encoding="utf-8")
    check("1 개인 계정 이름을 코드에 안 적는다",
          "read_conf" in src and "claude-account-night-switch" in src)
    check("1-b 밤 전환 실행 파일이 그 함수를 실제로 내준다",
          hasattr(m, "personal_account") and m.SWITCH.exists(), str(m.SWITCH))

    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        acct = td / "someone"
        (acct / ".claude").mkdir(parents=True)
        (acct / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
        m.ROOT = td
        m.personal_account = lambda: "someone"

        f = FakeRun()
        rc = run_main(m, ["--tools", "get_me,outlook_email_search", "무엇을 읽어라"], f)
        cmd, kw = f.seen
        check("2 실행 홈이 그 개인 계정 자리다",
              kw["env"]["HOME"] == str(acct) and kw["cwd"] == str(acct),
              kw["env"].get("HOME"))
        check("2-b 부르는 쪽을 가리키는 환경값 셋을 지운다",
              not any(k in kw["env"] for k in
                      ("CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "CLAUDE_ACCOUNTS_ROOT")))
        allowed = cmd[cmd.index("--allowedTools") + 1]
        check("3 도구 이름을 앞가지와 함께 박는다",
              allowed == (m.PREFIX + "get_me," + m.PREFIX + "outlook_email_search"),
              allowed)
        check("3-b 읽기만 하는 도구가 기본값이다",
              all(("send" not in t and "create" not in t and "update" not in t)
                  for t in m.DEFAULT_TOOLS), m.DEFAULT_TOOLS)
        check("정상 회차는 0 으로 끝난다", rc == 0, rc)

        check("3-c 도구를 비우면 거절한다",
              run_main(m, ["--tools", " ", "무엇을"], FakeRun()) == 2)

        f2 = FakeRun(out="   ")
        check("4 빈손으로 끝나면 시끄럽게 죽는다",
              run_main(m, ["--tools", "get_me", "무엇을"], f2) == 2)
        f3 = FakeRun(rc=1)
        check("4-b 실행이 실패하면 시끄럽게 죽는다",
              run_main(m, ["--tools", "get_me", "무엇을"], f3) == 2)

        m.ROOT = td / "없는자리"
        check("4-c 로그인 파일이 없으면 시끄럽게 죽는다",
              run_main(m, ["--tools", "get_me", "무엇을"], FakeRun()) == 2)

    print("PASS %d  FAIL %d" % (sum(results), len(results) - sum(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
