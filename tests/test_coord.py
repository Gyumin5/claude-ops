#!/usr/bin/env python3
"""coord.py 시험 — 실제 gh·systemctl 대신 가짜 관측을 넣는다."""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import coord  # noqa: E402

T = dt.datetime(2026, 10, 19, 14, 30, tzinfo=dt.timezone(dt.timedelta(hours=9)))


class FakeFacts:
    def __init__(self, jobs=None, prs=None, host=None, sessions=None):
        self._jobs = jobs if jobs is not None else {}
        self._prs = prs or {}
        self._host = host or {"cores": 16, "load1": 9.0, "mem_avail_gib": 47.0}
        self._sessions = sessions if sessions is not None else {"a": "/x", "b": "/y", "bad": "/z"}

    def sessions(self):
        return self._sessions

    def jobs(self):
        return self._jobs

    def pr(self, repo, number):
        return self._prs.get((repo, number))

    def host(self):
        return self._host


def status(session, decisions=(), work=()):
    return {"schema": 1, "session": session, "host": "peer", "decisions": list(decisions), "work": list(work)}


def work(id_, units=(), eta=None, state="진행", checked="2026-10-19T14:00+09:00", resources=(), prs=()):
    return {"id": id_, "version": 1, "title": id_, "purpose": "p", "priority": "1", "state": state,
            "resources": list(resources), "eta": {"text": "t", "at": eta},
            "refs": {"pr": list(prs), "units": list(units)}, "checked_at": checked}


def decision(id_, prs=(), checked="2026-10-19T14:00+09:00"):
    return {"id": id_, "version": 1, "title": id_, "options": ["가", "나"], "recommendation": "가",
            "blocks": "b", "refs": {"pr": list(prs)}, "checked_at": checked}


class CoordTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        # 맡는 세션 목록은 실제 운영값이라, 시험에서 쓰는 이름들로 바꿔 끼운다.
        self._scope = coord.SCOPE
        coord.SCOPE = {"a", "b", "bad", "alpha", "beta", "new", "gone", "dotfiles", "mine"}
        # 닫은 목록은 실제 운영 상태파일이라 시험에서 격리한다.
        self._closed = coord.CLOSED
        coord.CLOSED = os.path.join(self.d, "closed.json")

    def tearDown(self):
        coord.SCOPE = self._scope
        coord.CLOSED = self._closed
        shutil.rmtree(self.d)

    def live(self, session, decisions):
        """떠 있는 세션 하나를 흉내낸다 — 자기 작업 디렉토리 밑의 status.json."""
        wd = os.path.join(self.d, session)
        os.makedirs(os.path.join(wd, "state", "coord"), exist_ok=True)
        p = os.path.join(wd, "state", "coord", "status.json")
        with open(p, "w") as f:
            json.dump(status(session, decisions), f, ensure_ascii=False)
        return FakeFacts(sessions={session: wd}), p

    def put(self, name, obj):
        with open(os.path.join(self.d, name + ".json"), "w") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False))

    def marks(self, rep):
        return [m for s in rep["sessions"] for k in ("decisions", "work") for x in s[k] for m in x["marks"]]

    def test_same_input_twice_gives_no_delta(self):
        self.put("a", status("a", [decision("a-d1")], [work("a-w1", eta="2026-10-19T13:00+09:00")]))
        fx = FakeFacts()
        base = coord.fingerprints(coord.build(self.d, fx, T))
        self.assertEqual(coord.render_delta(coord.build(self.d, fx, T), base), "")

    def test_delta_sends_the_current_list_without_resolved_items(self):
        # 운영자 7298: 풀린 것은 안 적는다. 바뀌었으면 지금 봐야 할 것만 최종 목록으로 보낸다.
        self.put("a", status("a", [decision("a-d1")]))
        fx = FakeFacts()
        base = coord.fingerprints(coord.build(self.d, fx, T))
        self.put("a", status("a", [decision("a-d2")]))
        out = coord.render_delta(coord.build(self.d, fx, T), base)
        self.assertIn("a-d2", out)
        self.assertNotIn("a-d1", out)
        self.assertNotIn("풀림", out)
        self.assertNotIn("새로", out)

    def test_delta_ignores_non_decision_changes(self):
        self.put("a", status("a", [decision("a-d1")], [work("a-w1", eta="2026-10-19T13:00+09:00")]))
        base = {k: v for k, v in coord.fingerprints(coord.build(self.d, FakeFacts(), T)).items()
                if k.startswith("dec:")}
        self.assertEqual(coord.render_delta(coord.build(self.d, FakeFacts(), T), base), "")

    def test_brief_lists_only_decision_titles(self):
        self.put("a", status("a", [decision("a-d1"), decision("a-d2")], [work("a-w1")]))
        out = coord.render_brief(coord.build(self.d, FakeFacts(), T))
        self.assertEqual(out.splitlines()[1:3], ["1. [a-d1 v1] a — a-d1", "2. [a-d2 v1] a — a-d2"])
        self.assertNotIn("a-w1", out)

    def test_origin_mtime_comes_from_the_file_not_the_session(self):
        # 원문 시각은 취합하는 쪽이 stat 해서 적는다. 세션이 자기 파일 안에 적으면 그 세션이
        # 멈춘 순간의 값에서 굳어, 멈춘 파일과 방금 갱신한 파일이 화면에서 같아 보인다.
        # 2026-11-10 delta 통보 전까지 이 칸을 아무도 안 채워 모든 줄이 '원문 ' 뒤가 비어 있었다.
        self.put("a", status("a", [decision("a-d1")]))
        os.utime(os.path.join(self.d, "a.json"), (1_760_000_000, 1_760_000_000))
        rep = coord.build(self.d, FakeFacts(), T)
        want = dt.datetime.fromtimestamp(1_760_000_000).replace(microsecond=0).isoformat()
        self.assertEqual(rep["sessions"][0]["origin"]["mtime"], want)
        self.assertIn(f'원문 {want[5:16].replace("T", " ")}', coord.render_full(rep))

    def test_origin_mtime_ignores_what_the_session_wrote(self):
        # 세션이 직접 적어 둔 값이 있어도 취합 쪽 stat 가 이긴다 — 안 그러면 멈춘 세션의
        # 굳은 값이 그대로 화면에 남는다.
        s = status("a", [decision("a-d1")])
        s["origin"] = {"mtime": "2020-01-01T00:00:00"}
        self.put("a", s)
        rep = coord.build(self.d, FakeFacts(), T)
        self.assertNotIn("2020-01-01", rep["sessions"][0]["origin"]["mtime"])

    def test_broken_json_isolated(self):
        self.put("bad", "{not json")
        self.put("a", status("a", [decision("a-d1")]))
        rep = coord.build(self.d, FakeFacts(), T)
        self.assertEqual([e["session"] for e in rep["errors"]], ["bad"])
        self.assertEqual([s["session"] for s in rep["sessions"]], ["a"])

    def test_bad_timestamp_drops_only_that_item(self):
        """시각 형식이 틀린 항목 하나가 그 세션의 다른 정할 것까지 지우면 안 된다.
        2026-10-26 에 bias·velocity 가 이 모양이라 열 건이 통째로 사라지고
        운영자 화면에는 '지금 정할 것 없음' 으로 보였다."""
        self.put("bad", status("bad", [decision("bad-d1", checked="2026-10-20 11:58 KST"),
                                       decision("bad-d2")]))
        self.put("a", status("a", [decision("a-d1")]))
        rep = coord.build(self.d, FakeFacts(), T)
        self.assertEqual([e["session"] for e in rep["errors"]], ["bad"])
        self.assertIn("checked_at", rep["errors"][0]["error"])
        self.assertEqual(sorted(s["session"] for s in rep["sessions"]), ["a", "bad"])
        ids = [d["item"]["id"] for s in rep["sessions"] for d in s["decisions"]]
        self.assertIn("bad-d2", ids)
        self.assertNotIn("bad-d1", ids)

    def test_brief_says_read_failed_instead_of_nothing(self):
        """읽기 실패를 '정할 것 없음' 으로 내보내지 않는다 — 조용한 거짓 성공(R16)."""
        self.put("bad", "{not json")
        out = coord.render_brief(coord.build(self.d, FakeFacts(), T))
        self.assertIn("읽기 실패", out)
        self.assertIn("bad", out)

    def test_brief_keeps_read_failure_line_with_items(self):
        """정할 것이 있어도 못 읽은 세션이 있으면 같이 알린다."""
        self.put("bad", "{not json")
        self.put("a", status("a", [decision("a-d1")]))
        out = coord.render_brief(coord.build(self.d, FakeFacts(), T))
        self.assertIn("읽기 실패", out)
        self.assertIn("a-d1", out)

    def test_missing_field_is_error(self):
        self.put("a", {"schema": 1, "session": "a", "decisions": []})
        rep = coord.build(self.d, FakeFacts(), T)
        self.assertIn("work", rep["errors"][0]["error"])

    def test_stale_item_marked_unverified_not_closed(self):
        self.put("a", status("a", [], [work("a-w1", checked="2026-10-19T06:00+09:00")]))
        rep = coord.build(self.d, FakeFacts(), T)
        self.assertIn("미확인", [m[0] for m in self.marks(rep)])
        self.assertEqual(len(rep["sessions"][0]["work"]), 1)  # 자동 종료 안 함

    def test_overdue_eta(self):
        self.put("a", status("a", [], [work("a-w1", eta="2026-10-19T13:00+09:00")]))
        rep = coord.build(self.d, FakeFacts(), T)
        self.assertTrue(any("지났는데" in m[1] for m in self.marks(rep)))

    def test_unlisted_job_and_listed_job(self):
        jobs = {"claude-job-a-bench-1.service": {"session": "a", "since": "x"},
                "claude-job-a-other-2.service": {"session": "a", "since": "y"}}
        self.put("a", status("a", [], [work("a-w1", units=["claude-job-a-bench"])]))
        rep = coord.build(self.d, FakeFacts(jobs=jobs), T)
        self.assertEqual([u[1] for u in rep["unlisted"]], ["claude-job-a-other-2.service"])
        self.assertIn(("확인됨", "잡 claude-job-a-bench 도는 중", None), self.marks(rep))

    def test_job_gone(self):
        self.put("a", status("a", [], [work("a-w1", units=["claude-job-a-bench"])]))
        rep = coord.build(self.d, FakeFacts(jobs={}), T)
        self.assertTrue(any(m[0] == "불일치" and "관측 없음" in m[1] for m in self.marks(rep)))

    def test_job_query_failure_is_unverified(self):
        self.put("a", status("a", [], [work("a-w1", units=["claude-job-a-bench"])]))
        fx = FakeFacts()
        fx._jobs = None
        rep = coord.build(self.d, fx, T)
        self.assertTrue(any(m[0] == "미확인" for m in self.marks(rep)))

    def test_pr_mismatch_and_failure(self):
        prs = [{"repo": "o/r", "number": 1, "claim": {"isDraft": True}},
               {"repo": "o/r", "number": 2, "claim": {"state": "OPEN"}}]
        self.put("a", status("a", [decision("a-d1", prs=prs)]))
        rep = coord.build(self.d, FakeFacts(prs={("o/r", 1): {"isDraft": False, "reviewers": []}}), T)
        kinds = {m[1].split(" ")[0] + ":" + m[0] for m in self.marks(rep)}
        self.assertIn("o/r#1:불일치", kinds)
        self.assertIn("o/r#2:미확인", kinds)

    def test_remote_host_contention_across_sessions(self):
        r1 = {"host": "train-server", "device": "mem", "amount": "약 115", "unit": "GB", "mode": "shared", "status": "in_use", "of": "125 GB"}
        r2 = {"host": "train-server", "device": "docker-sim", "amount": "시험", "unit": "", "mode": "shared", "status": "requested"}
        self.put("a", status("a", [], [work("a-w1", resources=[r1])]))
        self.put("b", status("b", [], [work("b-w1", state="대기", resources=[r2])]))
        rep = coord.build(self.d, FakeFacts(), T)
        self.assertTrue(any("학습 서버" in c[1] for c in rep["contention"]))

    def test_coverage_live_without_file_and_file_without_live(self):
        self.put("a", status("a"))
        self.put("gone", status("gone"))
        rep = coord.build(self.d, FakeFacts(sessions={"a": "/x", "new": "/n", "dotfiles": "/d"}), T)
        texts = [c[1] for c in rep["coverage"]]
        self.assertIn("new: 떠 있는데 현황 파일 없음", texts)
        self.assertIn("dotfiles: 취합자 자기 현황 아직 안 넣음", texts)
        self.assertTrue(any(t.startswith("gone: 세션이 안 떠 있음") for t in texts))

    def test_mark_sent_only_after_success_keeps_baseline(self):
        # 전송이 실패하면 mark-sent 를 안 부른다 — 기준이 그대로라 다음 회차가 같은 변화를 다시 보낸다.
        self.put("a", status("a", [decision("a-d1")]))
        fx = FakeFacts()
        base = {}
        first = coord.render_delta(coord.build(self.d, fx, T), base)
        again = coord.render_delta(coord.build(self.d, fx, T), base)
        self.assertEqual(first, again)
        self.assertTrue(first)

    def test_live_paths_read_each_session_state_coord(self):
        # 정식 경로는 세션마다의 <작업 디렉토리>/state/coord/status.json 이다.
        wd = os.path.join(self.d, "alpha")
        os.makedirs(os.path.join(wd, "state", "coord"))
        with open(os.path.join(wd, "state", "coord", "status.json"), "w") as f:
            json.dump(status("alpha", [decision("al-d1")]), f, ensure_ascii=False)
        fx = FakeFacts(sessions={"alpha": wd, "없는세션": os.path.join(self.d, "nope")})
        self.assertEqual(coord.status_paths(fx),
                         [("alpha", os.path.join(wd, "state", "coord", "status.json"))])
        rep = coord.build(None, fx, T)
        self.assertEqual([s["session"] for s in rep["sessions"]], ["alpha"])
        self.assertIn("al-d1", coord.render_brief(rep))

    def test_broken_live_file_is_named_by_session_not_filename(self):
        # 실제 파일 이름은 전부 status.json 이라, 오류 줄의 이름은 세션명에서 와야 한다.
        wd = os.path.join(self.d, "beta")
        os.makedirs(os.path.join(wd, "state", "coord"))
        with open(os.path.join(wd, "state", "coord", "status.json"), "w") as f:
            f.write("{깨짐")
        rep = coord.build(None, FakeFacts(sessions={"beta": wd}), T)
        self.assertEqual([e["session"] for e in rep["errors"]], ["beta"])

    def test_full_renders_when_optional_fields_missing(self):
        # 세션이 칸을 다 안 채워도 전체 보고가 죽으면 안 된다 — 물었을 때 답할 길이 그것뿐이다.
        w = {"id": "a-w1", "version": 1, "title": "t", "refs": {}, "checked_at": "2026-10-19T14:00+09:00"}
        d = {"id": "a-d1", "version": 1, "title": "정할 것", "refs": {}, "checked_at": "2026-10-19T14:00+09:00"}
        self.put("a", status("a", [d], [w]))
        out = coord.render_full(coord.build(self.d, FakeFacts(), T))
        self.assertIn("정할 것", out)
        self.assertIn("a-w1", out)

    def test_sessions_outside_scope_are_skipped(self):
        # 맡지 않는 세션은 모으지도, 빠졌다고 적지도 않는다 — 그쪽은 자기가 보고한다.
        coord.SCOPE = {"mine"}
        wd = os.path.join(self.d, "mine")
        os.makedirs(os.path.join(wd, "state", "coord"))
        with open(os.path.join(wd, "state", "coord", "status.json"), "w") as f:
            json.dump(status("mine", [decision("m-d1")]), f, ensure_ascii=False)
        other = os.path.join(self.d, "other")
        os.makedirs(os.path.join(other, "state", "coord"))
        with open(os.path.join(other, "state", "coord", "status.json"), "w") as f:
            json.dump(status("other", [decision("o-d1")]), f, ensure_ascii=False)
        fx = FakeFacts(sessions={"mine": wd, "other": other})
        rep = coord.build(None, fx, T)
        self.assertEqual([s["session"] for s in rep["sessions"]], ["mine"])
        self.assertFalse([c for c in rep["coverage"] if "other" in c[1]])

    def test_close_hides_item_and_keeps_session_file(self):
        # 취합자가 닫으면 목록에서 빠진다. 남의 파일은 한 글자도 안 고친다 — 단일 writer 유지.
        coord.SCOPE = {"mine"}
        fx, p = self.live("mine", [decision("m-d1")])
        with open(p) as f:
            before = f.read()
        rc, msg = coord.close_item("mine", "m-d1", "운영자가 닫으라고 했다", fx)
        self.assertEqual(rc, 0, msg)
        with open(p) as f:
            self.assertEqual(f.read(), before, "남의 status.json 을 고쳤다")
        rep = coord.build(None, fx, T)
        self.assertIn("정할 것 없음", coord.render_brief(rep))
        self.assertIn("[닫음] mine m-d1", coord.render_full(rep))

    def test_closed_item_reappears_when_session_bumps_version(self):
        # 같은 id 라도 세션이 version 을 올리면 새 물음이다 — 닫은 기록으로 덮지 않는다.
        coord.SCOPE = {"mine"}
        fx, p = self.live("mine", [decision("m-d1")])
        self.assertEqual(coord.close_item("mine", "m-d1", "닫음", fx)[0], 0)
        d2 = decision("m-d1")
        d2["version"] = 2
        d2["title"] = "다시 묻는다"
        with open(p, "w") as f:
            json.dump(status("mine", [d2]), f, ensure_ascii=False)
        self.assertIn("다시 묻는다", coord.render_brief(coord.build(None, fx, T)))

    def test_close_unknown_item_writes_nothing(self):
        # 오타로 빈 칸을 적어두면 나중에 진짜 항목이 조용히 사라진다.
        coord.SCOPE = {"mine"}
        fx, _ = self.live("mine", [decision("m-d1")])
        rc, msg = coord.close_item("mine", "m-d9", "", fx)
        self.assertEqual(rc, 1)
        self.assertIn("못 찾았다", msg)
        self.assertFalse(os.path.exists(coord.CLOSED))

    def test_brief_carries_item_id_and_version(self):
        # 순번은 그 회차의 자리다. 앞 항목이 닫히면 같은 "1번" 이 다른 물음을 가리키는데,
        # 운영자는 번호로 답하고 그 답이 다음 회차에 읽힐 수 있다. 표식으로 대상을 고정한다.
        coord.SCOPE = {"mine"}
        first, second = decision("m-d1"), decision("m-d2")
        second["version"] = 3
        fx, _ = self.live("mine", [first, second])
        before = coord.render_brief(coord.build(None, fx, T))
        self.assertIn("1. [m-d1 v1] mine", before)
        self.assertIn("2. [m-d2 v3] mine", before)
        self.assertEqual(coord.close_item("mine", "m-d1", "닫음", fx)[0], 0)
        after = coord.render_brief(coord.build(None, fx, T))
        # 1번이 가리키는 물음이 바뀌었다 — 표식이 없으면 이 회차와 앞 회차를 구분할 수 없다.
        self.assertIn("1. [m-d2 v3] mine", after)

    def test_item_without_version_still_fingerprints(self):
        # version 은 REQUIRED 가 아니다. 그 칸 없는 세션 파일 하나가 mark-sent 를 죽이면
        # 발송 기준이 안 고정되고, 다음 회차의 delta 가 바뀐 것만 고르지 못한다.
        # 2026-11-10 에 실제로 홈 status.json 이 그 모양이라 brief 가 보낸 직후 죽었다.
        coord.SCOPE = {"mine"}
        bare = decision("m-d1")
        del bare["version"]
        fx, _ = self.live("mine", [bare])
        rep = coord.build(None, fx, T)
        fp = coord.fingerprints(rep)
        self.assertIn("dec:m-d1:1", fp, "version 없는 항목이 지문에서 빠졌다")
        self.assertIn("1. [m-d1 v1] mine", coord.render_brief(rep))

    def test_stale_mark_survives_missing_version(self):
        # 확인이 끊긴 항목의 표식도 같은 칸을 읽는다 — 한쪽만 고치면 오래된 항목에서 다시 죽는다.
        coord.SCOPE = {"mine"}
        bare = decision("m-d1", checked="2026-09-01T14:00+09:00")
        del bare["version"]
        fx, _ = self.live("mine", [bare])
        fp = coord.fingerprints(coord.build(None, fx, T))
        self.assertIn("stale:m-d1:1", fp)

    def test_close_refuses_when_answered_version_is_stale(self):
        # 답을 받은 뒤 세션이 물음을 고쳐 판본을 올렸으면 그 답은 새 물음의 답이 아니다.
        # 그대로 닫으면 새 물음이 한 번도 안 보인 채 사라진다.
        coord.SCOPE = {"mine"}
        fx, p = self.live("mine", [decision("m-d1")])
        bumped = decision("m-d1")
        bumped["version"] = 2
        bumped["title"] = "물음이 바뀌었다"
        with open(p, "w") as f:
            json.dump(status("mine", [bumped]), f, ensure_ascii=False)
        rc, msg = coord.close_item("mine", "m-d1", "닫음", fx, answered_version=1)
        self.assertEqual(rc, 1)
        self.assertIn("v2", msg)
        self.assertFalse(os.path.exists(coord.CLOSED), "거절했는데 닫은 목록을 썼다")
        self.assertIn("물음이 바뀌었다", coord.render_brief(coord.build(None, fx, T)))
        # 지금 판본으로 답하면 닫힌다.
        self.assertEqual(coord.close_item("mine", "m-d1", "닫음", fx, answered_version=2)[0], 0)

    def test_closing_the_last_item_sends_nothing(self):
        # 마지막 항목을 닫으면 볼 것이 없다 — "다 풀렸다" 를 알리지 않고 그냥 조용하다.
        coord.SCOPE = {"mine"}
        fx, _ = self.live("mine", [decision("m-d1")])
        base = coord.fingerprints(coord.build(None, fx, T))
        self.assertEqual(coord.close_item("mine", "m-d1", "닫음", fx)[0], 0)
        rep = coord.build(None, fx, T)
        self.assertEqual(coord.render_delta(rep, base), "")

    def test_remaining_items_are_resent_when_one_closes(self):
        # 둘 중 하나를 닫으면 남은 하나만 최종 목록으로 다시 간다.
        coord.SCOPE = {"mine"}
        fx, _ = self.live("mine", [decision("m-d1"), decision("m-d2")])
        base = coord.fingerprints(coord.build(None, fx, T))
        self.assertEqual(coord.close_item("mine", "m-d1", "닫음", fx)[0], 0)
        out = coord.render_delta(coord.build(None, fx, T), base)
        self.assertIn("m-d2", out)
        self.assertNotIn("m-d1", out)

    def test_local_host_comes_from_machine_id_file(self):
        # 유저봇 host 파일은 릴레이 대상이라 자기 신원이 아니다 — 기계 신원 파일을 본다.
        p = os.path.expanduser("~/.config/claude-machine-id")
        if not os.path.exists(p):
            self.skipTest("이 기계에는 신원 파일이 없다")
        with open(p) as f:
            self.assertEqual(coord.local_host(), f.read().strip())


if __name__ == "__main__":
    unittest.main(verbosity=1)
