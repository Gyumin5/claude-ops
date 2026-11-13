#!/usr/bin/env python3
"""claude-usage-daily-report 가 지키는 것들.

고정하는 것 넷이다. ⑴ 서로 다른 계정의 수치를 한 선 위에 놓지 않는다. ⑵ 창이
초기화된 자리도 가른다 — 같은 계정이라도 창이 갈리면 0 부터 다시 쌓인다.
⑶ 계정은 번호가 아니라 자리 이름으로 적는다. ⑷ 묶음마다 처음·끝·피크가 그
묶음 안의 값이다.

왜 이걸 고정하나: 2026-11-07 둘째 기계 보고가 "7일 시작 76퍼센트 → 종료 24퍼센트,
피크 78퍼센트, 최저 0퍼센트" 였다. 그날 계정이 갈렸고 창도 한 번 초기화됐는데
174개 표본을 한 줄로 합쳐서, 쓰지도 않은 사용량이 줄어든 것처럼 읽혔다.
"""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REPORT = REPO / "bin" / "claude-usage-daily-report"
KST = timezone(timedelta(hours=9))

fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  — {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def main():
    check("보고 도구가 있다", REPORT.exists(), str(REPORT))
    if not REPORT.exists():
        return 1

    with tempfile.TemporaryDirectory() as td:
        # 계정 자리 둘. 번호를 이름으로 되짚는 길이 여기를 본다.
        root = Path(td) / "accounts"
        uuid_a = "aaaaaaaa-0000-0000-0000-000000000001"
        uuid_b = "bbbbbbbb-0000-0000-0000-000000000002"
        for name, uid in (("acct-a", uuid_a), ("acct-b", uuid_b)):
            (root / name).mkdir(parents=True)
            (root / name / ".claude.json").write_text(
                json.dumps({"oauthAccount": {"accountUuid": uid}}), encoding="utf-8")
        os.environ["CLAUDE_ACCOUNTS_ROOT"] = str(root)

        m = load(REPORT, "usage_daily")

        day = datetime(2026, 11, 7, tzinfo=KST)

        def at(h, mi=0):
            return (day + timedelta(hours=h, minutes=mi)).timestamp()

        win_old = (day + timedelta(hours=4)).timestamp()      # 그날 끝난 창
        win_new = (day + timedelta(days=7, hours=4)).timestamp()

        # 둘째 기계 그날의 모양: 계정 하나가 76~78 까지 쓰고, 창이 갈린 뒤 0 부터 다시.
        recs = [
            {"ts": at(0, 14), "acct": uuid_a, "sd_pct": 76, "sd_reset": win_old},
            {"ts": at(2, 41), "acct": uuid_a, "sd_pct": 78, "sd_reset": win_old},
            {"ts": at(4, 4), "acct": uuid_a, "sd_pct": 0, "sd_reset": win_new},
            {"ts": at(23, 44), "acct": uuid_a, "sd_pct": 24, "sd_reset": win_new},
        ]
        su = m.summarize(recs)
        check("창이 갈리면 줄도 갈린다", su and len(su["sd"]) == 2,
              [k for k, _ in (su or {}).get("sd", [])])
        msg_groups = {m.window_label(k[1]): rows for k, rows in su["sd"]}
        old_rows = msg_groups[m.window_label(win_old)]
        check("끝난 창의 피크는 그 창 안의 값이다",
              max(r["sd_pct"] for r in old_rows) == 78,
              [r["sd_pct"] for r in old_rows])
        new_rows = msg_groups[m.window_label(win_new)]
        check("새 창은 0 에서 시작한다고 적는다",
              new_rows[0]["sd_pct"] == 0 and new_rows[-1]["sd_pct"] == 24,
              [r["sd_pct"] for r in new_rows])

        line = m.window_line((uuid_a, win_old), old_rows, "sd_pct")
        check("줄에 자리 이름이 뜬다 — 계정 번호가 아니라", line.startswith("  acct-a · "),
              line[:40])
        check("줄 안의 처음·끝·피크가 그 묶음 값이다",
              "76% (00:14)" in line and "78% (02:41)" in line and "피크 78%" in line,
              line)

        # 계정이 갈린 자리. 같은 창 끝 시각을 공유해도 섞이면 안 된다.
        same_win = (day + timedelta(days=7)).timestamp()
        recs2 = [
            {"ts": at(1), "acct": uuid_a, "sd_pct": 60, "sd_reset": same_win},
            {"ts": at(2), "acct": uuid_b, "sd_pct": 3, "sd_reset": same_win},
            {"ts": at(3), "acct": uuid_a, "sd_pct": 62, "sd_reset": same_win},
        ]
        su2 = m.summarize(recs2)
        check("창 끝 시각이 같아도 계정이 다르면 안 섞는다", len(su2["sd"]) == 2,
              len(su2["sd"]))
        labels = {m.acct_label(k[0]) for k, _ in su2["sd"]}
        check("두 자리 이름이 다 뜬다", labels == {"acct-a", "acct-b"}, labels)

        # 5시간 한도도 같은 규칙으로 가른다.
        recs3 = [
            {"ts": at(1), "acct": uuid_a, "fh_pct": 40, "fh_reset": at(5)},
            {"ts": at(6), "acct": uuid_a, "fh_pct": 5, "fh_reset": at(10)},
        ]
        su3 = m.summarize(recs3)
        check("5시간 한도도 창마다 가른다", len(su3["fh"]) == 2, len(su3["fh"]))

        # 번호를 못 찾으면 번호 앞자리로 적되 사람이 읽을 말을 붙인다.
        check("모르는 번호는 앞 여덟 자로 적는다",
              m.acct_label("zzzzzzzz-9999-0000-0000-000000000009") == "계정 zzzzzzzz",
              m.acct_label("zzzzzzzz-9999-0000-0000-000000000009"))
        check("번호가 없으면 모름이라고 적는다", m.acct_label(None) == "계정 모름",
              m.acct_label(None))

        # 옛 기록에는 계정 칸이 없다. 그래도 창으로는 가른다.
        recs4 = [
            {"ts": at(1), "sd_pct": 10, "sd_reset": win_old},
            {"ts": at(5), "sd_pct": 1, "sd_reset": win_new},
        ]
        su4 = m.summarize(recs4)
        check("계정 칸이 없는 옛 기록도 창으로 가른다", len(su4["sd"]) == 2, len(su4["sd"]))

        check("표본이 없으면 None", m.summarize([]) is None)
        check("수치가 하나도 없으면 None",
              m.summarize([{"ts": at(1), "acct": uuid_a}]) is None)

    return 1 if fails else 0


if __name__ == "__main__":
    rc = main()
    print(f"\n{'실패: ' + ', '.join(fails) if fails else '모두 통과'}")
    sys.exit(rc)
