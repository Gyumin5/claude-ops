#!/usr/bin/env python3
"""주간 브리핑 한 회차의 계약 검사.

고정하는 것:
  1. 완성된 토론 입력은 50,000바이트를 넘지 않고, 넘칠 때는 낮은 순위부터 부록으로
     내리되 그 사실을 남긴다.
  2. 후보가 있는 관심 분야마다 최소 한 자리가 먼저 확보된다. 별 수·내려받기 수는
     통과 조건이 아니다 — 인기가 0이어도 주제 관련성이 높으면 올라간다.
  3. 같은 작업·같은 사건만 하나로 묶는다. 제목이 비슷하다고 묶지 않는다.
     이미 소개한 논문이라도 새 코드 공개는 다른 사건이라 다시 올라간다.
  4. 출처 하나가 죽어도 나머지로 돈다. 성공·정상 0건·실패·부분수집을 구분해 적는다.
  5. 전송은 HTTP 성공만으로 성공이라고 적지 않는다. 응답의 ok 가 거짓이면 실패다.
     보고서는 전송 전에 로컬에 남고, 첨부만 실패하면 본문으로 알린다.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
results = []


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


col = load("aiwb_collect", REPO / "bin" / "ai-weekly-brief-collect.py")
ev = load("aiwb_evaluate", REPO / "bin" / "ai-weekly-brief-evaluate.py")
au = load("aiwb_audit", REPO / "bin" / "ai-weekly-brief-audit.py")


def cand(**kw):
    base = {"source": "시험", "title": "제목", "url": "https://example.com/x",
            "work_id": "x", "event": col.EVENT_PAPER, "when": col.iso(col.NOW),
            "topic": "주행추정과 지도작성", "topic_hits": 3, "summary": "요약",
            "license": "미확인", "popularity": 0, "cross": 1}
    base.update(kw)
    return base


# ── 1. 분야 자리 확보와 인기 지표 ──
pool = [cand(work_id="pop%d" % i, title="인기만 높은 글 %d" % i,
             topic="클로드와 에이전트 사용성", topic_hits=3, popularity=9000)
        for i in range(20)]
pool.append(cand(work_id="quiet", title="별 없는 주행추정 논문",
                 topic="센서가 무너지는 상황", topic_hits=3, popularity=0))
picked, left = col.choose(list(pool), budget=30000)
topics = {c["topic"] for c in picked}
check("후보가 있는 분야마다 자리를 먼저 확보한다",
      "센서가 무너지는 상황" in topics, str(topics))
check("인기 0이어도 통과 조건에서 걸리지 않는다",
      any(c["work_id"] == "quiet" for c in picked))

# ── 2. 크기 상한과 부록 ──
big = [cand(work_id="b%d" % i, title="긴 제목 " * 10,
            summary="아주 긴 요약 " * 60,
            topic=list(col.TOPICS)[i % 5], topic_hits=2) for i in range(100)]
picked2, left2 = col.choose(list(big), budget=30000)
size2 = len(json.dumps(picked2, ensure_ascii=False).encode("utf-8"))
check("선정분은 수집 예산 안에 든다", size2 <= 30000, "%d바이트" % size2)
check("빠진 후보는 사유와 함께 부록으로 남는다",
      left2 and all(c.get("drop_reason") for c in left2),
      "부록 %d건" % len(left2))

audit = {"host": "시험기", "items": [{"kind": "plugin", "name": "p%d" % i,
                                      "origin": "o", "version": "1.0"}
                                     for i in range(60)]}
text, note = ev.fit(list(big), audit, ["2026-10-05 이번 주 흐름: 아무것"])
total = len(text.encode("utf-8"))
check("완성된 토론 입력은 5만 바이트를 넘지 않는다", total <= ev.TOTAL_LIMIT,
      "%d바이트" % total)
check("줄였으면 그 사실을 입력과 결과에 같이 남긴다",
      (not note) or (note in text), note)
check("연구와 설치 점검이 각각의 절로 들어간다",
      "[연구 후보" in text and "[설치물" in text)

# 토론 도구는 브리프의 절대경로를 사본으로 뜨려 하고 홈 아래에서는 그 시도가 회차를
# 통째로 멈춘다. 첫 회차가 실제로 그렇게 평가 없이 끝났다.
path_audit = {"host": "시험기", "items": [
    {"kind": "skill", "name": "plan", "origin": "/home/u/.claude/skills/plan"}]}
ptext, _ = ev.fit([cand(url="https://github.com/a/b")], path_audit, [])
check("설치 경로를 절대경로 그대로 토론에 넘기지 않는다",
      not __import__("re").search(r"(?<![\w:~])/home/\S+", ptext),
      ptext[-200:])
check("주소는 그대로 살린다", "https://github.com/a/b" in ptext)

# ── 3. 사건 단위 묶기와 억제 ──
same = [cand(work_id="arxiv:2601.00001", source="논문 보관소"),
        cand(work_id="arxiv:2601.00001", source="해커뉴스(보조)",
             url="https://news.ycombinator.com/item?id=1")]
merged = col.merge(same)
check("같은 작업·같은 사건은 한 묶음이 된다", len(merged) == 1, str(len(merged)))
check("여러 출처에 같이 뜬 것은 교차 출현으로 센다", merged[0]["cross"] == 2)

similar = [cand(work_id="arxiv:2601.00001", title="Robust LiDAR Odometry"),
           cand(work_id="arxiv:2601.00002", title="Robust LiDAR Odometry")]
check("제목이 같아도 식별자가 다르면 따로 둔다", len(col.merge(similar)) == 2)

paper_then_code = [cand(work_id="arxiv:2601.00001", event=col.EVENT_PAPER),
                   cand(work_id="arxiv:2601.00001", event=col.EVENT_CODE)]
check("같은 작업이라도 논문과 코드 공개는 다른 사건이다",
      len(col.merge(paper_then_code)) == 2)

with tempfile.TemporaryDirectory() as tmp:
    hist = Path(tmp) / "seen.jsonl"
    hist.write_text(json.dumps({"work_id": "arxiv:2601.00001",
                                "event": col.EVENT_PAPER,
                                "date": col.NOW.strftime("%Y-%m-%d")},
                               ensure_ascii=False) + "\n", encoding="utf-8")
    kept, dropped = col.suppress(paper_then_code, col.load_history(str(hist)))
    check("최근 소개한 사건은 억제된다",
          len(dropped) == 1 and dropped[0]["event"] == col.EVENT_PAPER)
    check("같은 작업의 새 사건은 다시 올라간다",
          len(kept) == 1 and kept[0]["event"] == col.EVENT_CODE)

# ── 4. 출처 실패 갈래 ──
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "c.jsonl"
    st = Path(tmp) / "s.json"
    col.SOURCES = [
        ("성공", lambda d: [cand(work_id="ok")]),
        ("정상0", lambda d: []),
        ("부분", lambda d: ([cand(work_id="part")], ["질의2 실패"])),
        ("실패", lambda d: (_ for _ in ()).throw(RuntimeError("응답 없음"))),
    ]
    sys.argv = ["x", "--out", str(out), "--status", str(st), "--days", "7"]
    col.main()
    s = json.loads(st.read_text(encoding="utf-8"))
    states = {r["source"]: r["state"] for r in s["sources"]}
    check("출처 하나가 죽어도 나머지로 돈다", s["selected"] >= 2, json.dumps(s))
    check("성공·정상 0건·실패·부분수집을 구분해 적는다",
          states == {"성공": "성공", "정상0": "정상 0건",
                     "부분": "부분수집", "실패": "실패"}, str(states))
    check("실패한 출처는 사유를 남긴다",
          any(r["source"] == "실패" and r["note"] for r in s["sources"]))

# ── 5. 조립 스크립트: 전송 판정과 보고서 ──
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / "home"
    binp = home / "bin"
    shim = Path(tmp) / "shim"
    for d in (binp, shim, home / "dotfiles" / ".claude" / "telegram"):
        d.mkdir(parents=True)
    (home / "dotfiles" / ".claude" / "telegram" / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=시험토큰\n", encoding="utf-8")
    shutil.copy(REPO / "bin" / "ai-weekly-brief", binp / "ai-weekly-brief")
    # 설치된 모양 그대로 세운다 — bin 옆에 lib 가 있고, 방 번호는 추적 밖 설정에서
    # 읽는다. 실제 방 번호는 안 쓴다(지어낸 값이면 통로 판정에 충분하다).
    (home / "lib").mkdir()
    shutil.copy(REPO / "lib" / "operator_chat.sh", home / "lib" / "operator_chat.sh")
    (home / ".config" / "dotfiles-private").mkdir(parents=True)
    (home / ".config" / "dotfiles-private" / "telegram.json").write_text(
        '{"operator_chat_id": "1234509876"}\n', encoding="utf-8")
    # 모듈 자리에 선 가짜들 — 조립 스크립트의 책임만 본다.
    (binp / "ai-weekly-brief-collect.py").write_text(
        "import sys\n"
        "a=dict(zip(sys.argv[1::2],sys.argv[2::2]))\n"
        "open(a['--out'],'w').write('')\n"
        "open(a['--status'],'w').write('{\"sources\":[{\"source\":\"시험\","
        "\"state\":\"실패\",\"count\":0,\"note\":\"응답 없음\"}],\"raw\":0,"
        "\"merged\":0,\"suppressed\":0,\"selected\":0}')\n"
        "open(a['--appendix'],'w').write('')\n"
        "print('없음')\n", encoding="utf-8")
    (binp / "ai-weekly-brief-audit.py").write_text(
        "import sys\n"
        "a=dict(zip(sys.argv[1::2],sys.argv[2::2]))\n"
        "open(a['--out'],'w').write('{\"host\":\"시험기\",\"items\":[],"
        "\"problems\":[]}')\n"
        "print('0건')\n", encoding="utf-8")
    (binp / "ai-weekly-brief-evaluate.py").write_text(
        "import sys\n"
        "a=dict(zip(sys.argv[1::2],sys.argv[2::2]))\n"
        "open(a['--out'],'w').write('{\"state\":\"완료\",\"decision\":"
        "\"추천 없음 — 이번 주 출처가 모두 응답하지 않았다.\",\"input_bytes\":10,"
        "\"candidates\":0,\"installs\":0}')\n"
        "print('ok')\n", encoding="utf-8")
    calls = Path(tmp) / "curl.log"
    (shim / "curl").write_text(
        '#!/bin/bash\n'
        'echo "$*" >> "%s"\n'
        'if [[ "$*" == *sendDocument* ]]; then\n'
        '  echo \'{"ok":false,"description":"file too big"}\'\n'
        'else\n'
        '  echo \'{"ok":true,"result":{"message_id":1}}\'\n'
        'fi\n' % calls, encoding="utf-8")
    (shim / "curl").chmod(0o755)
    inject = Path(tmp) / "inject.log"
    (shim / "claude-userbot-send").write_text(
        '#!/bin/bash\nprintf "%%s\\n" "$@" > "%s"\n' % inject, encoding="utf-8")
    (shim / "claude-userbot-send").chmod(0o755)
    env = dict(os.environ)
    env.pop("CLAUDE_OPERATOR_CHAT_ID", None)   # 설정 파일 통로를 재는 자리다
    env["HOME"] = str(home)
    env["PATH"] = str(shim) + os.pathsep + env.get("PATH", "")
    r = subprocess.run(["bash", str(binp / "ai-weekly-brief")],
                       capture_output=True, text=True, env=env, timeout=180)
    report = home / ".local" / "share" / "ai-briefing" / "weekly-brief"
    files = sorted(report.glob("*.md")) if report.is_dir() else []
    check("전송 전에 보고서를 로컬에 남긴다", bool(files),
          r.stderr[-300:] if not files else "")
    body = files[0].read_text(encoding="utf-8") if files else ""
    check("출처가 다 죽어도 상태 보고를 만든다", "실패" in body and "출처별" in body,
          body[:200])
    status = (home / ".local" / "state" / "ai-weekly-brief" / "status.jsonl")
    lines = [json.loads(x) for x in status.read_text(encoding="utf-8").splitlines()
             if x.strip()] if status.exists() else []
    phases = {d["phase"]: d["success"] for d in lines}
    check("응답 ok 가 거짓인 전송을 성공으로 적지 않는다",
          phases.get("telegram_file") is False, json.dumps(phases, ensure_ascii=False))
    check("본문 전송 성공은 따로 적는다", phases.get("telegram_body") is True,
          json.dumps(phases, ensure_ascii=False))
    log = calls.read_text(encoding="utf-8") if calls.exists() else ""
    check("첨부만 실패하면 본문으로 알린다", "첨부 전송 실패" in log, log[-300:])
    # 채팅에는 목록을 통째로 싣지 않는다. 목록만 오면 무엇을 할지는 여전히 사람이
    # 골라야 해서 전달이 안 된 것과 같다(운영자 지시).
    check("채팅 본문은 목록이 아니라 끝났다는 알림이다",
          "주간 브리핑 회차가 끝났다" in log and "전체 목록은 첨부" in log
          and "[설치 점검]" not in log, log[-300:])
    # 추천 줄은 '#1' 처럼 번호가 붙는다. 문서 제목·절 제목까지 세면 수가 부풀려진다.
    check("추천 건수에 제목 줄을 같이 세지 않는다", "추천 0건" in log, log[-300:])
    check("토큰을 보고서에 적지 않는다", "시험토큰" not in body)
    sent = inject.read_text(encoding="utf-8") if inject.exists() else ""
    check("회차가 끝나면 판단할 세션에 주입한다",
          "--session" in sent and "dotfiles" in sent, sent[:200])
    check("주입은 경로와 요약만 보낸다 — 보고서 본문을 옮기지 않는다",
          str(files[0]) in sent and "출처별" not in sent and len(sent) < 2000,
          "%d자" % len(sent))
    check("주입 실패도 회차 상태에 남는다", "inject" in phases,
          json.dumps(phases, ensure_ascii=False))

# ── 5-b. 계정 동기화 묶음은 스킬 하나로 세지 않는다 ──
with tempfile.TemporaryDirectory() as tmp:
    sync = Path(tmp) / "synced"
    for snap in ("aaa_111", "bbb_222"):
        for sk in ("pdf", "docx"):
            (sync / snap / sk).mkdir(parents=True)
    item = au.synced_skills(str(sync))[0]
    check("계정 동기화 묶음은 한 줄로 묶는다", len(au.synced_skills(str(sync))) == 1,
          str(item))
    check("묶음 수와 담긴 스킬 이름을 적는다",
          item["묶음"] == 2 and item["스킬"] == "docx,pdf", str(item))
    check("계정 동기화는 저장소 관리 대상이 아니라고 적는다",
          "계정 동기화" in item["origin"], str(item))

# ── 6. 규칙 점검 근거 ──
with tempfile.TemporaryDirectory() as tmp:
    main_md = Path(tmp) / "CLAUDE.md"
    why_md = Path(tmp) / "RATIONALE.md"
    main_md.write_text("가 [R01]\n나 [R02]\n다 [R02]\n라 [R77]\n", encoding="utf-8")
    why_md.write_text("### R01\n본문\n### R02\n본문\n### R09 (폐기 — 지움)\n본문\n"
                      "### R40\n본문\n", encoding="utf-8")
    au.RULES_MAIN, au.RULES_WHY = str(main_md), str(why_md)
    item = au.rules()[0]
    check("근거 없는 번호를 찾는다", item.get("근거_없는_번호") == "R77", str(item))
    check("본문에서 두 번 쓴 번호를 찾는다",
          item.get("본문에서_두_번_쓴_번호") == "R02", str(item))
    check("본문에 없는 근거를 찾는다", item.get("본문에_없는_근거") == "R40", str(item))
    check("폐기 표시된 번호는 고아로 세지 않는다",
          "R09" not in item.get("본문에_없는_근거", ""), str(item))
    check("폐기된 번호를 따로 적는다 — 집계가 어긋나는 이유가 여기 있다",
          item.get("폐기된_번호") == "R09", str(item))

    # 이상이 없을 때도 세 칸이 '없음' 으로 남아야 한다. 키가 빠지면 평가 단계가
    # 대조 자체를 못 봤다고 읽고 판정을 보류한다(2026-10-13 회차 실측).
    main_md.write_text("가 [R01]\n나 [R02]\n", encoding="utf-8")
    why_md.write_text("### R01\n본문\n### R02\n본문\n", encoding="utf-8")
    clean = au.rules()[0]
    check("이상이 없으면 세 칸을 '없음' 으로 채운다",
          clean.get("근거_없는_번호") == "없음"
          and clean.get("본문에_없는_근거") == "없음"
          and clean.get("본문에서_두_번_쓴_번호") == "없음", str(clean))

# ── 5-c. 로컬 클론은 상류를 안 따라간다 — 뒤처진 양을 숫자로 낸다 ──
with tempfile.TemporaryDirectory() as tmp:
    import subprocess as sp
    up, work = os.path.join(tmp, "up.git"), os.path.join(tmp, "work")
    sp.run(["git", "init", "-q", "--bare", up], check=True)
    sp.run(["git", "clone", "-q", up, work], check=True)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    for i in range(3):
        open(os.path.join(work, "f%d" % i), "w").write("x")
        sp.run(["git", "-C", work, "add", "."], check=True)
        sp.run(["git", "-C", work, "commit", "-qm", "c%d" % i], check=True, env=env)
    sp.run(["git", "-C", work, "push", "-q", "origin", "HEAD"], check=True)
    sp.run(["git", "-C", work, "reset", "-q", "--hard", "HEAD~2"], check=True)
    st = au.git_state("bash -c cd %s && uv run server/main.py" % work)
    check("로컬 클론이 상류보다 몇 커밋 뒤인지 센다", st.get("상류보다_뒤") == "2커밋",
          str(st))
    check("로컬에 손댄 것이 있는지 같이 본다", st.get("로컬_수정") == "없음", str(st))
    check("클론이 아닌 명령에는 아무것도 붙이지 않는다",
          au.git_state("npx -y some-mcp-server") == {})

rule_item = {"kind": "rules", "name": "전역 규칙", "lines": 469,
             "근거_없는_번호": "R77"}
rtext, _ = ev.fit([cand(url="https://x.test/a")],
                  {"host": "시험기", "items": [rule_item]}, [])
check("규칙 점검 항목을 토론 입력에 그대로 싣는다",
      "근거_없는_번호=R77" in rtext and "[규칙 점검]" in rtext, rtext[-300:])

# ── 7. 허깅페이스 라이선스 ──
# 목록 API 가 주는 값을 그대로 쓴다. 무조건 "미확인" 으로 적으면 읽는 쪽이 그 쪽을
# 직접 열어야 한다(2026-10-18 실측).
check("카드 정보의 라이선스를 그대로 쓴다",
      col.hf_license({"cardData": {"license": "apache-2.0"}}) == "apache-2.0")
check("라이선스가 목록으로 와도 첫 값을 쓴다",
      col.hf_license({"cardData": {"license": ["mit", "apache-2.0"]}}) == "mit")
check("태그에만 있어도 찾아낸다",
      col.hf_license({"tags": ["robotics", "license:cc-by-4.0"]}) == "cc-by-4.0")
check("아무 데도 없으면 미확인이다",
      col.hf_license({"cardData": {}, "tags": ["slam"]}) == "미확인")
check("빈 값은 라이선스로 치지 않는다",
      col.hf_license({"cardData": {"license": "  "}, "tags": ["license:"]}) == "미확인")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
