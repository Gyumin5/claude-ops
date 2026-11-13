#!/usr/bin/env python3
"""bin/claude-coord-report 의 세 갈래를 실제 텔레그램 없이 고정한다.

고정하는 계약 (R70):
  1. 모은 결과가 비면 안 보내고 rc=0 — 빈 회차는 조용해야 한다.
  2. 전송이 200 이 아니면 rc=1 이고 기준(mark-sent)을 안 고친다 — 다음 회차가 다시 보낸다.
  3. 200 이면 그때만 기준을 고친다.
  4. 봇 토큰이 없으면 보내지 않고 rc=1.

가짜 저장소(CLAUDE_COORD_REPO)와 PATH 앞단의 가짜 curl 로 돈다.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "claude-coord-report")

FAKE_COORD = """#!/usr/bin/env python3
import os, sys
mode = sys.argv[1] if len(sys.argv) > 1 else ""
if mode == "mark-sent":
    open(os.environ["MARK_FILE"], "a").write("mark\\n")
    print("기준 고정: 00000000")
    sys.exit(0)
sys.stdout.write(os.environ.get("FAKE_OUT", ""))
sys.exit(int(os.environ.get("FAKE_RC", "0")))
"""

FAKE_CURL = """#!/bin/bash
echo -n "${FAKE_HTTP:-200}"
"""


class CoordReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        os.makedirs(os.path.join(root, "repo", "bin"))
        os.makedirs(os.path.join(root, "shim"))
        self.coord = os.path.join(root, "repo", "bin", "coord.py")
        with open(self.coord, "w") as f:
            f.write(FAKE_COORD)
        os.chmod(self.coord, 0o755)
        curl = os.path.join(root, "shim", "curl")
        with open(curl, "w") as f:
            f.write(FAKE_CURL)
        os.chmod(curl, 0o755)
        self.mark = os.path.join(root, "mark.txt")
        self.repo = os.path.join(root, "repo")
        self.shim = os.path.join(root, "shim")

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, mode="delta", **env):
        e = dict(os.environ)
        e.update({
            "CLAUDE_COORD_REPO": self.repo,
            "MARK_FILE": self.mark,
            "PATH": self.shim + os.pathsep + e.get("PATH", ""),
            "TELEGRAM_BOT_TOKEN": "fake-token",
        })
        e.update({k: str(v) for k, v in env.items()})
        return subprocess.run([SCRIPT, mode], capture_output=True, text=True, env=e)

    def marked(self):
        return os.path.exists(self.mark)

    def test_empty_is_silent(self):
        r = self.run_script(FAKE_OUT="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("보낼 변화 없음", r.stdout)
        self.assertFalse(self.marked(), "빈 회차인데 기준을 고쳤다")

    def test_whitespace_only_is_silent(self):
        r = self.run_script(FAKE_OUT="\n   \n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("보낼 변화 없음", r.stdout)
        self.assertFalse(self.marked())

    def test_sends_and_marks_on_200(self):
        r = self.run_script(FAKE_OUT="정할 것 1개\n1. a — b\n", FAKE_HTTP="200")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("보냄", r.stdout)
        self.assertTrue(self.marked(), "200 인데 기준을 안 고쳤다")

    def test_keeps_baseline_when_send_fails(self):
        r = self.run_script(FAKE_OUT="정할 것 1개\n1. a — b\n", FAKE_HTTP="429")
        self.assertEqual(r.returncode, 1)
        self.assertIn("전송 실패", r.stderr)
        self.assertFalse(self.marked(), "전송 실패인데 기준을 고쳤다 — 그 변화가 영영 안 간다")

    def test_collector_failure_stops_before_send(self):
        r = self.run_script(FAKE_OUT="터짐", FAKE_RC=3)
        self.assertEqual(r.returncode, 3)
        self.assertIn("모으기 실패", r.stderr)
        self.assertFalse(self.marked())

    def test_no_token_does_not_send(self):
        e = {"TELEGRAM_BOT_TOKEN": "", "BOT_TOKEN": "", "FAKE_OUT": "정할 것 1개\n"}
        r = self.run_script(**e)
        self.assertEqual(r.returncode, 1)
        self.assertIn("봇 토큰 없음", r.stderr)
        self.assertFalse(self.marked())

    def test_bad_mode_rejected(self):
        r = self.run_script("hourly")
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage", r.stderr)


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(CoordReportTest))
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    print(f"{total - failed}/{total} PASS")
    sys.exit(1 if failed else 0)
